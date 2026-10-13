"""Verify official signing, request boundaries, and isolated account storage offline."""

import base64
import json
import stat
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from music_tui.sources.netease_openapi import OfficialAPIError, OfficialClient


@pytest.fixture
def credentials(tmp_path):
    """Generate an independent RSA key and complete synthetic device profile."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    device_path = tmp_path / "device.json"
    device_path.write_text(
        json.dumps(
            dict(
                channel="test",
                deviceId="Device1",
                deviceType="test",
                appVer="1.0.0",
                os="test",
                osVer="1",
                brand="test",
                model="test",
                clientIp="127.0.0.1",
                netStatus="wifi",
            )
        )
    )
    return key, key_path, device_path


@pytest.fixture
def make_client(monkeypatch, tmp_path, credentials):
    """Replace the HTTP transport while retaining actual signing and client options."""
    original = httpx.AsyncClient

    def build(handler=None, app_id="application"):
        """Construct a client with test credentials and optionally a response handler."""

        def respond(request):
            """Reject network calls in storage-only tests."""
            pytest.fail("Unexpected HTTP request")

        def client(**kwargs):
            """Bind a mock transport without replacing the request implementation."""
            return original(**kwargs, transport=httpx.MockTransport(handler or respond))

        monkeypatch.setattr("music_tui.sources.netease_openapi.httpx.AsyncClient", client)
        return OfficialClient(tmp_path / "account", app_id, credentials[1], credentials[2])

    return build


@pytest.mark.parametrize(
    "explicit,expected", [(None, "saved-token"), ("", None), ("anonymous", "anonymous")]
)
async def test_signed_request_encodes_unicode_once(make_client, credentials, explicit, expected):
    """Verify the RSA signature from decoded wire values and exact Unicode business data."""
    business = {"keyword": "中文 + & = % /", "enabled": True}

    def handler(request):
        """Verify with the public key, independent of the client's private key operation."""
        params = {key: values[0] for key, values in parse_qs(request.url.query.decode()).items()}
        signature = base64.b64decode(params.pop("sign"))
        canonical = "&".join(f"{key}={params[key]}" for key in sorted(params))
        credentials[0].public_key().verify(
            signature, canonical.encode(), padding.PKCS1v15(), hashes.SHA256()
        )
        assert json.loads(params["bizContent"]) == business
        assert params.get("accessToken") == expected
        assert params["appId"] == "application"
        assert params["signType"] == "RSA_SHA256"
        assert request.url.host == "openapi.music.163.com"
        assert request.url.scheme == "https"
        return httpx.Response(200, json={"code": 200, "data": {"ok": True}})

    client = make_client(handler)
    client.token = "saved-token"
    try:
        assert await client.request("/test", business, token=explicit) == {"ok": True}
    finally:
        await client.close()


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"code": 1406}, "1406"),
        ({"code": 200, "subCode": 1408}, "1408"),
        ({"code": "secret-response"}, "未知"),
        ({"code": 200, "subCode": "secret-response"}, "未知"),
    ],
)
async def test_api_errors_keep_safe_codes(make_client, payload, code):
    """Expose machine-readable errors without copying arbitrary response content."""

    def handler(request):
        """Return a business error inside a successful HTTP response."""
        return httpx.Response(200, json={**payload, "message": "private-data"})

    client = make_client(handler)
    try:
        with pytest.raises(OfficialAPIError) as caught:
            await client.request("/test", {})
        assert caught.value.code == code
        assert "private-data" not in str(caught.value)
        assert "secret-response" not in str(caught.value)
    finally:
        await client.close()


@pytest.mark.parametrize("payload", [[], {"code": 200}, {"code": 200, "data": []}])
async def test_invalid_response_is_rejected(make_client, payload):
    """Reject unexpected success shapes before exposing data to source operations."""

    def handler(request):
        """Return a malformed API envelope."""
        return httpx.Response(200, json=payload)

    client = make_client(handler)
    try:
        with pytest.raises(ValueError):
            await client.request("/test", {})
    finally:
        await client.close()


@pytest.mark.parametrize(
    "payload, success",
    [
        ({"code": 200, "data": True}, True),
        ({"code": 200, "data": []}, True),
        ({"code": 200, "data": False}, False),
        ({"code": 200, "data": {}}, False),
        ({"code": 200, "data": ["unexpected"]}, False),
        ({"code": 200}, False),
        ({"code": 403, "data": True}, False),
        ({"code": 200, "subCode": 300, "data": True}, False),
    ],
)
async def test_playlist_add_accepts_only_documented_business_success(make_client, payload, success):
    """Accept fresh and duplicate appends only after top-level and subcode validation."""
    client = make_client(lambda request: httpx.Response(200, json=payload))
    try:
        endpoint = "/openapi/music/basic/playlist/song/batch/like"
        business = {"playlistId": "opaque-playlist", "songIdList": ["opaque-song"]}
        if success:
            assert await client.request(endpoint, business, token="test-token") == {}
        else:
            with pytest.raises(ValueError):
                await client.request(endpoint, business, token="test-token")
    finally:
        await client.close()


async def test_token_storage_permissions_and_restore(make_client, monkeypatch):
    """Persist an account atomically and restore only while its lifetime remains valid."""
    monkeypatch.setattr("music_tui.sources.netease_openapi.time.time", lambda: 1000)
    client = make_client()
    try:
        assert not await client.restore()
        client.save_token({"accessToken": "account-token", "expireTime": 60})
        assert stat.S_IMODE(client.credential_path.stat().st_mode) == 0o600
        assert not list(client.credential_path.parent.glob(".token-*"))
        client.token = ""
        assert await client.restore()
        assert client.token == "account-token"
        monkeypatch.setattr("music_tui.sources.netease_openapi.time.time", lambda: 1060)
        assert not await client.restore()
        assert client.token == ""
    finally:
        await client.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("appId", "other-app"),
        ("deviceId", "OtherDevice"),
        ("accessToken", "null"),
        ("accessToken", ""),
        ("accessToken", 123),
    ],
)
async def test_restore_rejects_other_identity_and_invalid_tokens(make_client, field, value):
    """Do not reuse a saved account across applications, devices, or invalid tokens."""
    client = make_client()
    try:
        client.save_token({"accessToken": "valid", "expiresTime": 3600})
        saved = json.loads(client.credential_path.read_text())
        saved[field] = value
        client.credential_path.write_text(json.dumps(saved))
        assert not await client.restore()
        assert client.token == ""
    finally:
        await client.close()


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"accessToken": "null", "expireTime": 60},
        {"accessToken": 123, "expireTime": 60},
        {"accessToken": "valid", "expireTime": 0},
        {"accessToken": "valid", "expireTime": -1},
    ],
)
async def test_invalid_login_does_not_create_credentials(make_client, data):
    """Reject incomplete login data without leaving a token file or logged-in state."""
    client = make_client()
    try:
        with pytest.raises(ValueError):
            client.save_token(data)
        assert not client.credential_path.exists()
        assert client.token == ""
    finally:
        await client.close()


@pytest.mark.parametrize("missing", ["app", "key", "device"])
def test_missing_configuration_explains_required_settings(tmp_path, credentials, missing):
    """Explain official configuration and the explicitly selected Enhanced alternative."""
    with pytest.raises(ValueError, match="NETEASE_APP_ID.*enhanced"):
        OfficialClient(
            tmp_path,
            "" if missing == "app" else "app",
            None if missing == "key" else credentials[1],
            None if missing == "device" else credentials[2],
        )
