"""Exercise NetEase login, library paging, and stream permissions without network access."""

import asyncio
import base64
import json
import stat

import httpx
import pytest

from music_tui.models import LoginState, Playlist, Quality, QualityUnavailableError, Track
from music_tui.sources.netease import NetEaseMusic


@pytest.fixture
def make_music(monkeypatch, tmp_path):
    """Create clients whose HTTP requests are handled by a test-supplied responder."""
    real_client = httpx.AsyncClient

    def build(handler):
        """Construct a source with a mock HTTP transport and isolated account storage."""

        def client(**kwargs):
            """Preserve source client settings while replacing the network transport."""
            return real_client(**kwargs, transport=httpx.MockTransport(handler))

        monkeypatch.setattr("music_tui.sources.netease.httpx.AsyncClient", client)
        return NetEaseMusic(tmp_path / "netease", api_url="http://api.test")

    return build


def song(song_id):
    """Return representative NetEase metadata including millisecond duration."""
    return {
        "id": song_id,
        "name": f"Song {song_id}",
        "ar": [{"name": "Artist A"}, {"name": "Artist B"}],
        "dt": 183456,
    }


def response(payload):
    """Return a successful HTTP response with an API-specific JSON payload."""
    return httpx.Response(200, json=payload)


async def test_restore_without_saved_account_does_not_request_network(make_music):
    """A fresh account stays logged out without contacting the API server."""

    def handler(request):
        """Fail if an account without a saved cookie sends a request."""
        pytest.fail(f"Unexpected request: {request.url.path}")

    music = make_music(handler)
    assert not await music.restore()
    assert not music.logged_in
    await music.close()


async def test_search_preserves_total_and_requests_correct_offset(make_music):
    """Search page two uses a 30-song offset and retains total and converted metadata."""

    def handler(request):
        """Return a partial second page while checking the user-visible search query."""
        assert request.url.path == "/cloudsearch"
        payload = json.loads(request.content)
        assert payload["keywords"] == "hello"
        assert int(payload["offset"]) == 30
        assert int(payload["limit"]) == 30
        return response({"code": 200, "result": {"songCount": 64, "songs": [song(31)]}})

    music = make_music(handler)
    result = await music.search("hello", page=2)
    assert result.total == 64 and result.has_more and result.page_size == 30
    assert result.tracks == [Track("31", "Song 31", "Artist A / Artist B", 183)]
    await music.close()


@pytest.mark.parametrize("valid", [True, False])
async def test_restore_verifies_saved_cookie(make_music, valid):
    """Saved cookies must resolve to an account before the source reports login."""

    def handler(request):
        """Return either a verified account or an expired anonymous session."""
        assert request.url.path == "/user/account"
        assert json.loads(request.content)["cookie"] == "MUSIC_U=dummy"
        return response({"code": 200, "profile": {"userId": 7} if valid else None})

    music = make_music(handler)
    music.credential_path.parent.mkdir()
    music.credential_path.write_text(json.dumps({"cookie": "MUSIC_U=dummy"}))
    if valid:
        assert await music.restore()
    else:
        with pytest.raises(ValueError, match="登录"):
            await music.restore()
    assert music.logged_in is valid
    await music.close()


async def test_qr_login_verifies_and_saves_cookie_before_done(make_music, monkeypatch):
    """QR polling maps scan states and stores only verified credentials privately."""
    png = b"\x89PNG\r\n\x1a\n"
    codes = iter([801, 802, 803])

    async def immediate_sleep(seconds):
        """Remove wall-clock polling delay while preserving the poll sequence."""

    def handler(request):
        """Serve a full QR workflow with dummy credentials in the response body."""
        payload = json.loads(request.content)
        match request.url.path:
            case "/login/qr/key":
                return response({"code": 200, "data": {"unikey": "dummy-key"}})
            case "/login/qr/create":
                assert payload["key"] == "dummy-key" and payload["qrimg"]
                return response(
                    {
                        "code": 200,
                        "data": {
                            "qrimg": "data:image/png;base64," + base64.b64encode(png).decode()
                        },
                    }
                )
            case "/login/qr/check":
                assert payload["key"] == "dummy-key"
                return response({"code": next(codes), "cookie": "MUSIC_U=dummy; __csrf=token"})
            case "/user/account":
                assert "MUSIC_U=dummy" in payload["cookie"]
                return response({"code": 200, "account": {"id": 7}})
            case _:
                pytest.fail(f"Unexpected endpoint: {request.url.path}")

    monkeypatch.setattr("music_tui.sources.netease.asyncio.sleep", immediate_sleep)
    music = make_music(handler)
    session = music.login_session()
    assert await session.get_qrcode() == png
    assert [state async for state in session] == [LoginState.CONFIRMED, LoginState.DONE]
    assert music.logged_in
    saved = json.loads(music.credential_path.read_text())
    assert saved == {"cookie": "MUSIC_U=dummy; __csrf=token"}
    assert stat.S_IMODE(music.credential_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(music.credential_path.parent.stat().st_mode) == 0o700
    assert len(list(music.credential_path.parent.iterdir())) == 1
    await music.close()


async def test_expired_qr_does_not_save_credentials(make_music):
    """Expired QR login ends without changing the account or writing secrets."""

    def handler(request):
        """Return expiration for a previously generated QR key."""
        return response({"code": 800})

    music = make_music(handler)
    session = music.login_session()
    session.key = "expired-key"
    assert [state async for state in session] == [LoginState.EXPIRED]
    assert not music.logged_in and not music.credential_path.exists()
    await music.close()


async def test_playlists_fetches_all_pages_with_reported_counts(make_music):
    """User playlists include later pages and preserve each track count."""
    offsets = []

    def handler(request):
        """Serve uneven pages to ensure the next offset counts actual returned entries."""
        payload = json.loads(request.content)
        assert request.url.path == "/user/playlist"
        assert payload["uid"] == 7 and payload["limit"] == 100
        offset = payload["offset"]
        offsets.append(offset)
        ids = [1, 2] if offset == 0 else [3]
        return response(
            {
                "code": 200,
                "more": offset == 0,
                "playlist": [
                    {
                        "id": song_id,
                        "name": f"List {song_id}",
                        "trackCount": song_id * 100,
                        "playCount": 0 if song_id == 1 else 1200,
                        "coverImgUrl": f"https://cover.test/{song_id}.jpg",
                    }
                    for song_id in ids
                ],
            }
        )

    music = make_music(handler)
    music.user_id = 7
    result = await music.playlists()
    assert offsets == [0, 2]
    assert [(item.id, item.song_count) for item in result] == [(1, 100), (2, 200), (3, 300)]
    assert [item.play_count for item in result] == [0, 1200, 1200]
    assert [item.cover_url for item in result] == [
        "https://cover.test/1.jpg",
        "https://cover.test/2.jpg",
        "https://cover.test/3.jpg",
    ]
    await music.close()


async def test_playlist_pages_use_complete_ids_and_restore_detail_order(make_music):
    """A playlist uses all track IDs even when embedded songs are truncated."""
    requested_ids = []

    def handler(request):
        """Shuffle detail responses so the source must restore the playlist's order."""
        if request.url.path == "/playlist/detail":
            return response(
                {
                    "code": 200,
                    "playlist": {
                        "trackIds": [{"id": i} for i in range(1, 104)],
                        "tracks": [song(1)],
                    },
                }
            )
        assert request.url.path == "/song/detail"
        ids = [int(item) for item in json.loads(request.content)["ids"].split(",")]
        requested_ids.append(ids)
        return response({"code": 200, "songs": [song(song_id) for song_id in reversed(ids)]})

    music = make_music(handler)
    playlist = Playlist(10, "Large playlist")
    first = await music.songs(playlist)
    second = await music.songs(playlist, page=2)
    assert [track.mid for track in first.tracks] == [str(i) for i in range(1, 101)]
    assert [track.mid for track in second.tracks] == ["101", "102", "103"]
    assert first.total == second.total == 103 and first.has_more and not second.has_more
    assert requested_ids == [list(range(1, 101)), [101, 102, 103]]
    await music.close()


async def test_missing_song_detail_is_visible_and_retry_returns_complete_page(make_music):
    """Incomplete detail data must fail visibly instead of silently losing playlist entries."""
    detail_calls = 0

    def handler(request):
        """Fail one detail response before returning the full playlist on retry."""
        nonlocal detail_calls
        if request.url.path == "/playlist/detail":
            return response({"code": 200, "playlist": {"trackIds": [{"id": 1}, {"id": 2}]}})
        detail_calls += 1
        return response(
            {"code": 200, "songs": [song(1)] if detail_calls == 1 else [song(1), song(2)]}
        )

    music = make_music(handler)
    playlist = Playlist(10, "Incomplete playlist")
    with pytest.raises(ValueError):
        await music.songs(playlist)
    result = await music.songs(playlist)
    assert [track.mid for track in result.tracks] == ["1", "2"]
    await music.close()


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"code": 403}, "结果码 403"),
        ({"url": None}, "未提供播放地址"),
        ({"freeTrialInfo": {"start": 0, "end": 30}}, "仅提供试听"),
        ({"level": "standard"}, "返回音质与请求不符"),
        ({"id": 999}, "未返回此歌曲的音源"),
    ],
)
async def test_stream_rejects_denied_preview_missing_and_downgraded_audio(
    make_music, changes, reason
):
    """The chosen quality never resolves to denied, preview, or lower-quality audio."""

    def handler(request):
        """Return one unusable stream variant while checking the requested level."""
        payload = json.loads(request.content)
        assert request.url.path == "/song/url/v1"
        assert payload["level"] == "lossless" and payload["unblock"] == "false"
        audio = {
            "id": 1,
            "code": 200,
            "url": "https://audio.test/song.flac",
            "level": "lossless",
            "freeTrialInfo": None,
        } | changes
        return response({"code": 200, "data": [audio]})

    music = make_music(handler)
    with pytest.raises(QualityUnavailableError, match=reason) as failure:
        await music.stream_url(Track("1", "Song", "Artist", 183), Quality.LOSSLESS)
    assert "会员" not in str(failure.value)
    assert "https://" not in str(failure.value)
    await music.close()


@pytest.mark.parametrize("code", [301, 500])
async def test_stream_api_authentication_or_server_failures_do_not_allow_lower_tiers(
    make_music, code
):
    """Leave top-level login and API errors distinct from per-song tier restrictions."""

    def handler(request):
        """Fail the entire stream lookup before any authorized audio result is returned."""
        return response({"code": code})

    music = make_music(handler)
    with pytest.raises(ValueError) as failure:
        await music.stream_url(Track("1", "Song", "Artist", 183), Quality.LOSSLESS)
    assert not isinstance(failure.value, QualityUnavailableError)
    await music.close()


async def test_available_qualities_only_include_exact_authorized_levels(make_music):
    """Quality discovery excludes unavailable tiers and server-side downgrade responses."""

    def handler(request):
        """Authorize lossless and standard while denying or downgrading other tiers."""
        level = json.loads(request.content)["level"]
        audio = {
            "id": 1,
            "code": 200,
            "url": "https://audio.test/song",
            "level": level,
            "freeTrialInfo": None,
        }
        if level == "exhigh":
            audio["level"] = "standard"
        elif level not in {"standard", "lossless"}:
            audio["code"] = 403
        return response({"code": 200, "data": [audio]})

    music = make_music(handler)
    track = Track("1", "Song", "Artist", 183)
    assert await music.available_qualities(track) == [Quality.LOSSLESS, Quality.STANDARD]
    assert await music.stream_url(track, Quality.LOSSLESS) == "https://audio.test/song"
    await music.close()
    assert music.client.is_closed


@pytest.mark.parametrize("cookie, account", [("__csrf=token", {"id": 7}), ("MUSIC_U=dummy", None)])
async def test_qr_login_does_not_persist_missing_or_unverified_credentials(
    make_music, cookie, account
):
    """A QR success code alone cannot create a persisted, logged-in account."""

    def handler(request):
        """Return success with either missing account cookies or an anonymous account."""
        if request.url.path == "/login/qr/key":
            return response({"code": 200, "data": {"unikey": "dummy-key"}})
        if request.url.path == "/login/qr/create":
            return response({"code": 200, "data": {"qrimg": "data:image/png;base64,iVBORw0KGgo="}})
        if request.url.path == "/login/qr/check":
            return response({"code": 803, "cookie": cookie})
        assert request.url.path == "/user/account"
        return response({"code": 200, "account": account})

    music = make_music(handler)
    session = music.login_session()
    await session.get_qrcode()
    with pytest.raises(ValueError):
        _ = [state async for state in session]
    assert not music.logged_in and not music.credential_path.exists()
    await music.close()


async def test_connection_error_explains_how_to_start_service_without_secret_details(make_music):
    """Offer complete startup guidance without reflecting request URLs or account cookies."""

    def handler(request):
        """Simulate a missing local listener with sensitive error text."""
        raise httpx.ConnectError("sensitive-token", request=request)

    music = make_music(handler)
    try:
        with pytest.raises(ValueError) as caught:
            await music.login_session().get_qrcode()
        message = str(caught.value)
        assert "uv run music-tui --netease-service" in message
        assert "按 R" in message
        assert "sensitive-token" not in message
    finally:
        await music.close()


async def test_qr_poll_reads_scan_changes_despite_service_url_cache(make_music):
    """Bypass Enhanced's URL-only cache so a waiting QR can advance to confirmation."""
    upstream = iter([801, 802, 803])
    cache = {}

    def handler(request):
        """Model the pinned service middleware, which ignores POST body timestamps."""
        cache_key = (str(request.url), request.headers.get("cookie", ""))
        if request.headers.get("x-apicache-force-fetch"):
            return response({"code": next(upstream)})
        if cache_key not in cache:
            cache[cache_key] = {"code": next(upstream)}
        return response(cache[cache_key])

    music = make_music(handler)
    try:
        statuses = [
            (await music._request("/login/qr/check", qr=True, key="dummy"))["code"]
            for _ in range(3)
        ]
        assert statuses == [801, 802, 803]
    finally:
        await music.close()


async def test_search_does_not_reuse_a_different_post_body(make_music, monkeypatch):
    """Search distinct keywords independently even when their URL and timestamp match."""
    cache = {}
    monkeypatch.setattr("music_tui.sources.netease.time.time", lambda: 1000)

    def handler(request):
        """Apply the same URL cache while returning query-specific search results."""
        keyword = json.loads(request.content)["keywords"]
        payload = {"code": 200, "result": {"songs": [song(keyword)], "songCount": 1}}
        if request.headers.get("x-apicache-force-fetch"):
            return response(payload)
        return response(cache.setdefault(str(request.url), payload))

    music = make_music(handler)
    try:
        assert (await music.search("first")).tracks[0].mid == "first"
        assert (await music.search("second")).tracks[0].mid == "second"
    finally:
        await music.close()


@pytest.mark.parametrize("headers", [False, True])
async def test_qr_login_reads_joined_set_cookie_credentials(make_music, headers):
    """Accept real service Set-Cookie attributes and empty separators before MUSIC_U."""
    issued = [
        "NMTID=dummy; Path=/; HttpOnly;",
        "MUSIC_U=fresh; Path=/; Expires=Wed, 01 Jan 2031 00:00:00 GMT; HttpOnly;",
        "__csrf=token; Path=/;",
    ]

    def handler(request):
        """Return Enhanced's joined JSON cookies and optionally its structured HTTP headers."""
        if request.url.path == "/login/qr/key":
            return response({"code": 200, "data": {"unikey": "dummy"}})
        if request.url.path == "/login/qr/create":
            return response({"code": 200, "data": {"qrimg": "data:image/png;base64,iVBORw0KGgo="}})
        if request.url.path == "/login/qr/check":
            return httpx.Response(
                200,
                json={"code": 803, "cookie": ";".join(issued)},
                headers=[("Set-Cookie", value) for value in issued] if headers else [],
            )
        assert request.url.path == "/user/account"
        sent = json.loads(request.content)["cookie"]
        assert "MUSIC_U=fresh" in sent
        assert "__csrf=token" in sent
        assert all(attribute not in sent for attribute in ("Path", "HttpOnly", "Expires"))
        return response({"code": 200, "account": {"id": 7}})

    music = make_music(handler)
    try:
        session = music.login_session()
        await session.get_qrcode()
        assert [state async for state in session] == [LoginState.DONE]
        saved = json.loads(music.credential_path.read_text())["cookie"]
        assert "MUSIC_U=fresh" in saved
        assert "Path" not in saved and "Expires" not in saved
    finally:
        await music.close()


async def test_qr_success_without_new_credentials_does_not_reuse_cookie_jar(make_music):
    """Reject an incomplete QR success even when the HTTP client holds an older account."""

    def handler(request):
        """Return no new account credentials for this login attempt."""
        return response({"code": 803, "cookie": "NMTID=dummy"})

    music = make_music(handler)
    music.client.cookies.set("MUSIC_U", "old-account")
    session = music.login_session()
    session.key = "dummy"
    try:
        with pytest.raises(ValueError, match="缺少账号凭据"):
            _ = [state async for state in session]
        assert not music.logged_in and not music.credential_path.exists()
    finally:
        await music.close()


@pytest.mark.parametrize("failure", ["missing-cookie", "account", "save", "expired"])
async def test_failed_relogin_preserves_active_account(make_music, monkeypatch, failure):
    """A failed QR attempt preserves active credentials, HTTP cookies and library cache."""

    def handler(request):
        """Issue new HTTP cookies even when the login cannot be committed."""
        if request.url.path == "/login/qr/check":
            return httpx.Response(
                200,
                json={
                    "code": 800 if failure == "expired" else 803,
                    "cookie": "__csrf=token" if failure == "missing-cookie" else "MUSIC_U=new",
                },
                headers={
                    "Set-Cookie": (
                        "__csrf=token; Path=/"
                        if failure == "missing-cookie"
                        else "MUSIC_U=new; Path=/"
                    )
                },
            )
        assert request.url.path == "/user/account"
        assert json.loads(request.content)["cookie"] == "MUSIC_U=new"
        assert music.cookie == "MUSIC_U=old" and music.user_id == 7
        assert music.client.cookies.get("MUSIC_U") == "old"
        return response({"code": 200, "account": None if failure == "account" else {"id": 8}})

    def fail_save(candidate):
        """Simulate a failed atomic credential write after successful verification."""
        raise OSError("credential write failed")

    music = make_music(handler)
    music.cookie = "MUSIC_U=old"
    music.user_id = 7
    music.client.cookies.set("MUSIC_U", "old", domain="api.test", path="/")
    music._playlist_ids[10] = [1, 2]
    music._save_cookie()
    previous = music.credential_path.read_bytes()
    if failure == "save":
        monkeypatch.setattr(NetEaseMusic, "_save_cookie", fail_save)
    session = music.login_session()
    session.key = "new-key"
    try:
        if failure == "expired":
            assert [state async for state in session] == [LoginState.EXPIRED]
        else:
            with pytest.raises(OSError if failure == "save" else ValueError):
                _ = [state async for state in session]
        assert music.cookie == "MUSIC_U=old" and music.user_id == 7
        assert list(music.client.cookies.items()) == [("MUSIC_U", "old")]
        assert music._playlist_ids == {10: [1, 2]}
        assert music.credential_path.read_bytes() == previous
    finally:
        await music.close()


async def test_relogin_commits_verified_account_and_replaces_old_http_cookies(make_music):
    """Successful replacement publishes the verified account and clears old library data."""

    def handler(request):
        """Verify the candidate without changing the old account before persistence."""
        if request.url.path == "/login/qr/check":
            assert "old" not in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={"code": 803},
                headers={"Set-Cookie": "MUSIC_U=new; Path=/"},
            )
        assert request.url.path == "/user/account"
        assert json.loads(request.content)["cookie"] == "MUSIC_U=new"
        assert music.cookie == "MUSIC_U=old" and music.user_id == 7
        return response({"code": 200, "account": {"id": 8}})

    music = make_music(handler)
    music.cookie = "MUSIC_U=old"
    music.user_id = 7
    music.client.cookies.set("MUSIC_U", "old", domain="api.test", path="/")
    music._playlist_ids[10] = [1, 2]
    session = music.login_session()
    session.key = "new-key"
    try:
        assert [state async for state in session] == [LoginState.DONE]
        assert music.cookie == "MUSIC_U=new" and music.user_id == 8
        assert list(music.client.cookies.items()) == [("MUSIC_U", "new")]
        assert music._playlist_ids == {}
        assert json.loads(music.credential_path.read_text()) == {"cookie": "MUSIC_U=new"}
    finally:
        await music.close()


async def test_cancelled_relogin_keeps_old_account_available(make_music):
    """Cancelling verification leaves concurrent library requests on the previous account."""
    verification_started = asyncio.Event()
    release_verification = asyncio.Event()

    async def handler(request):
        """Block verification while allowing an ordinary old-account library request."""
        if request.url.path == "/login/qr/check":
            return httpx.Response(
                200,
                json={"code": 803},
                headers={"Set-Cookie": "MUSIC_U=new; Path=/"},
            )
        if request.url.path == "/user/account":
            verification_started.set()
            await release_verification.wait()
            return response({"code": 200, "account": {"id": 8}})
        assert request.url.path == "/user/playlist"
        payload = json.loads(request.content)
        assert payload["cookie"] == "MUSIC_U=old" and payload["uid"] == 7
        assert request.headers["cookie"] == "MUSIC_U=old"
        return response({"code": 200, "playlist": []})

    music = make_music(handler)
    music.cookie = "MUSIC_U=old"
    music.user_id = 7
    music.client.cookies.set("MUSIC_U", "old", domain="api.test", path="/")
    music._save_cookie()
    session = music.login_session()
    session.key = "new-key"
    iterator = aiter(session)
    task = asyncio.create_task(anext(iterator))
    try:
        async with asyncio.timeout(2):
            await verification_started.wait()
            assert await music.playlists() == []
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert music.cookie == "MUSIC_U=old" and music.user_id == 7
        assert list(music.client.cookies.items()) == [("MUSIC_U", "old")]
        assert json.loads(music.credential_path.read_text()) == {"cookie": "MUSIC_U=old"}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await iterator.aclose()
        await music.close()
