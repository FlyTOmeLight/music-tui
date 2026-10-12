"""Verify account identity and explicit library writes without touching real accounts."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from music_tui.models import Playlist, Track
from music_tui.sources.netease import NetEaseMusic
from music_tui.sources.netease_official import OfficialNetEaseMusic
from music_tui.sources.qqmusic import QQMusic

TRACK = Track("123", "歌曲", "歌手", 180)
EDITABLE = Playlist(10, "自己的歌单", dirid=301, editable=True)


@pytest.fixture
def qq_music(monkeypatch, tmp_path):
    """Supply SDK operations whose effects are recorded only in memory."""
    client = SimpleNamespace(
        credential=SimpleNamespace(musicid=42, musickey="test", encrypt_uin="test-user"),
        song=SimpleNamespace(
            get_detail=AsyncMock(
                return_value=SimpleNamespace(track=SimpleNamespace(id=123, type=0))
            )
        ),
        songlist=SimpleNamespace(
            like_song=AsyncMock(return_value=True),
            unlike_song=AsyncMock(return_value=True),
            add_songs=AsyncMock(return_value=True),
        ),
        user=SimpleNamespace(
            get_fav_song=lambda *args, **kwargs: SimpleNamespace(
                collect_items=AsyncMock(
                    return_value=[SimpleNamespace(mid="other"), SimpleNamespace(mid="123")]
                )
            )
        ),
    )
    monkeypatch.setattr("music_tui.sources.qqmusic.Client", lambda **kwargs: client)
    return QQMusic(tmp_path)


@pytest.fixture
def enhanced_music(monkeypatch, tmp_path):
    """Build NetEase sources with an in-memory HTTP responder supplied by each test."""
    original_client = httpx.AsyncClient

    def build(handler):
        """Use a verified fake account and replace the external transport."""
        monkeypatch.setattr(
            "music_tui.sources.netease.httpx.AsyncClient",
            lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)),
        )
        music = NetEaseMusic(tmp_path, api_url="http://api.test")
        music.user_id = 42
        music.cookie = "MUSIC_U=test"
        return music

    return build


@pytest.mark.parametrize("liked", [True, False])
async def test_qq_like_queries_all_pages_and_writes_numeric_identity(qq_music, liked):
    """Use the SDK collection and numeric song/type pair for explicit like writes."""
    assert qq_music.account_id == "42"
    assert await qq_music.is_liked(TRACK)
    await qq_music.set_liked(TRACK, liked)
    qq_music.client.song.get_detail.assert_awaited_once_with("123")
    operation = (
        qq_music.client.songlist.like_song if liked else qq_music.client.songlist.unlike_song
    )
    operation.assert_awaited_once_with([(123, 0)])


async def test_qq_append_uses_owned_directory_and_reports_write_failure(qq_music):
    """Append using directory and playlist IDs, and never claim a rejected write succeeded."""
    await qq_music.add_to_playlist(TRACK, EDITABLE)
    qq_music.client.songlist.add_songs.assert_awaited_once_with(301, [(123, 0)], tid=10)
    qq_music.client.songlist.add_songs.return_value = False
    with pytest.raises(ValueError, match="未能添加"):
        await qq_music.add_to_playlist(TRACK, EDITABLE)
    qq_music.client.songlist.like_song.return_value = False
    with pytest.raises(ValueError, match="未能更新"):
        await qq_music.set_liked(TRACK, True)


@pytest.mark.parametrize("operation", ["like", "append", "query"])
async def test_library_actions_require_login_without_issuing_requests(qq_music, operation):
    """Reject library actions before SDK requests when account credentials are absent."""
    qq_music.client.credential.musicid = 0
    assert qq_music.account_id == ""
    with pytest.raises(ValueError, match="扫码登录"):
        if operation == "like":
            await qq_music.set_liked(TRACK, True)
        elif operation == "append":
            await qq_music.add_to_playlist(TRACK, EDITABLE)
        else:
            await qq_music.is_liked(TRACK)
    qq_music.client.song.get_detail.assert_not_awaited()


async def test_readonly_playlist_is_rejected_before_qq_requests(qq_music):
    """Do not attempt to edit a collected playlist whose owner is another user."""
    with pytest.raises(ValueError, match="不可编辑"):
        await qq_music.add_to_playlist(TRACK, Playlist(10, "收藏歌单"))
    qq_music.client.song.get_detail.assert_not_awaited()


@pytest.mark.parametrize("liked", [True, False])
async def test_enhanced_like_and_append_send_correct_post_bodies(enhanced_music, liked):
    """Respect Enhanced's string boolean contract and invalidate successful-write caches."""
    calls = []

    def handler(request):
        """Capture operation bodies while keeping credentials out of request URLs."""
        body = json.loads(request.content)
        assert request.method == "POST"
        assert not request.url.query
        assert body.pop("cookie") == "MUSIC_U=test"
        body.pop("timestamp")
        calls.append((request.url.path, body))
        return httpx.Response(200, json={"code": 200, "ids": [123, 456]})

    music = enhanced_music(handler)
    try:
        assert music.account_id == "42"
        assert await music.is_liked(TRACK)
        music._playlist_ids = {10: [456], 20: [789]}
        await music.set_liked(TRACK, liked)
        assert music._playlist_ids == {}
        music._playlist_ids = {10: [456], 20: [789]}
        await music.add_to_playlist(TRACK, EDITABLE)
        assert music._playlist_ids == {20: [789]}
        assert calls == [
            ("/likelist", {"uid": 42}),
            ("/like", {"id": "123", "like": "true" if liked else "false"}),
            ("/playlist/tracks", {"op": "add", "pid": 10, "tracks": "123"}),
        ]
    finally:
        await music.close()


async def test_enhanced_playlist_ownership_and_like_list(enhanced_music):
    """Expose only verified-account playlists as editable and compare numeric song IDs."""

    def handler(request):
        """Return one owned liked list and one followed playlist from a different owner."""
        if request.url.path == "/likelist":
            return httpx.Response(200, json={"code": 200, "ids": [456]})
        return httpx.Response(
            200,
            json={
                "code": 200,
                "playlist": [
                    {"id": 10, "name": "喜欢", "specialType": 5, "creator": {"userId": 42}},
                    {"id": 20, "name": "收藏", "creator": {"userId": 99}},
                    {"id": 30, "name": "无所有者信息"},
                ],
                "more": False,
            },
        )

    music = enhanced_music(handler)
    try:
        playlists = await music.playlists()
        assert [playlist.editable for playlist in playlists] == [True, False, False]
        assert playlists[0].favorite
        assert not await music.is_liked(TRACK)
    finally:
        await music.close()


async def test_enhanced_failed_write_preserves_cached_playlist(enhanced_music):
    """Surface API rejection without pretending success or discarding known song order."""
    music = enhanced_music(lambda request: httpx.Response(200, json={"code": 403}))
    try:
        music._playlist_ids = {10: [456]}
        with pytest.raises(ValueError, match="403"):
            await music.add_to_playlist(TRACK, EDITABLE)
        assert music._playlist_ids == {10: [456]}
    finally:
        await music.close()


@pytest.mark.parametrize(
    "payload, success",
    [
        ({"status": 200, "body": {"code": 200}, "cookie": []}, True),
        ({"status": 200, "body": {"code": 403}, "cookie": []}, False),
        ({"code": 200}, True),
        ({"code": 403}, False),
        ({"status": 200, "body": {}}, False),
    ],
)
async def test_enhanced_playlist_writes_check_business_code_in_both_response_shapes(
    enhanced_music, payload, success
):
    """Accept nested normal writes and direct duplicate retries only after business success."""
    music = enhanced_music(lambda request: httpx.Response(200, json=payload))
    try:
        music._playlist_ids = {10: [456], 20: [789]}
        if success:
            await music.add_to_playlist(TRACK, EDITABLE)
            assert music._playlist_ids == {20: [789]}
        else:
            with pytest.raises(ValueError, match="网易云请求失败"):
                await music.add_to_playlist(TRACK, EDITABLE)
            assert music._playlist_ids == {10: [456], 20: [789]}
    finally:
        await music.close()


async def test_enhanced_like_does_not_accept_playlist_specific_response_wrapper(enhanced_music):
    """Keep top-level success validation for every endpoint other than playlist writes."""
    music = enhanced_music(
        lambda request: httpx.Response(200, json={"status": 200, "body": {"code": 200}})
    )
    try:
        music._playlist_ids = {10: [456]}
        with pytest.raises(ValueError, match="状态 未知"):
            await music.set_liked(TRACK, True)
        assert music._playlist_ids == {10: [456]}
    finally:
        await music.close()


@pytest.mark.parametrize("operation", ["like", "append", "query", "readonly"])
async def test_enhanced_library_guards_do_not_issue_requests(enhanced_music, operation):
    """Block logged-out writes and readonly playlists before any service request."""

    def handler(request):
        """Fail if a rejected library operation reaches the API transport."""
        pytest.fail(f"Unexpected request: {request.url.path}")

    music = enhanced_music(handler)
    try:
        if operation == "readonly":
            with pytest.raises(ValueError, match="不可编辑"):
                await music.add_to_playlist(TRACK, Playlist(10, "收藏歌单"))
        else:
            music.user_id = None
            assert music.account_id == ""
            with pytest.raises(ValueError, match="扫码登录"):
                if operation == "like":
                    await music.set_liked(TRACK, False)
                elif operation == "append":
                    await music.add_to_playlist(TRACK, EDITABLE)
                else:
                    await music.is_liked(TRACK)
    finally:
        await music.close()


@pytest.mark.parametrize("operation", ["lyrics", "is_liked", "set_liked", "add_to_playlist"])
async def test_official_unsupported_actions_are_explicit_and_never_fallback(
    monkeypatch, tmp_path, operation
):
    """Keep official API ownership and identify unsupported operations before requests."""
    client = SimpleNamespace(request=AsyncMock())
    monkeypatch.setattr("music_tui.sources.netease_official.OfficialClient", lambda *args: client)
    music = OfficialNetEaseMusic(tmp_path, "test", None)
    assert music.account_id == ""
    music.user_id = "opaque-user"
    assert music.account_id == "opaque-user"
    arguments = (TRACK,)
    if operation == "set_liked":
        arguments += (True,)
    elif operation == "add_to_playlist":
        arguments += (EDITABLE,)
    with pytest.raises(ValueError, match="官方模式暂不支持"):
        await getattr(music, operation)(*arguments)
    client.request.assert_not_awaited()
