"""Check official API library, login, and playback behavior without credentials."""

import asyncio
from types import SimpleNamespace

import pytest
from textual.widgets import DataTable

from music_tui.app import MusicApp
from music_tui.models import LoginState, Playlist, Quality, QualityUnavailableError, Track
from music_tui.sources.netease_official import API, OfficialNetEaseMusic
from music_tui.sources.netease_openapi import OfficialAPIError


@pytest.fixture
def make_music(monkeypatch, tmp_path):
    """Provide a source with deterministic official API data and token storage."""

    def build(handler):
        """Connect a handler to the client contract used by the business adapter."""

        class Client:
            """Record authentication changes while delegating API responses."""

            def __init__(self, *args):
                """Start without a restored or newly saved account."""
                self.token = ""
                self.saved = None
                self.restored = False
                self.closed = False

            async def request(self, endpoint, parameters, token=None):
                """Let each test check exact official request semantics."""
                return handler(endpoint.removeprefix(API + "/"), parameters, token)

            async def restore(self):
                """Simulate local token restoration separately from API validation."""
                return self.restored

            def save_token(self, credentials):
                """Capture only tokens that passed the profile check."""
                self.saved = credentials
                self.token = credentials["accessToken"]

            async def close(self):
                """Record ownership of the client lifecycle."""
                self.closed = True

        monkeypatch.setattr("music_tui.sources.netease_official.OfficialClient", Client)
        return OfficialNetEaseMusic(tmp_path, "test-app", tmp_path / "key.pem")

    return build


def song(song_id="ABC123"):
    """Return official metadata with a nonnumeric ID and millisecond duration."""
    return {
        "id": song_id,
        "name": "Song",
        "duration": 183456,
        "artists": [],
        "fullArtists": [{"name": "Artist A"}, {"name": "Artist B"}],
    }


async def test_guest_search_and_playlist_pages_preserve_opaque_ids(make_music):
    """Guest browsing reuses one anonymous token and pages by requested offset."""
    anonymous_calls = 0

    def handler(endpoint, parameters, token):
        """Serve guest browsing and verify official parameter names and pagination."""
        nonlocal anonymous_calls
        if endpoint == "oauth2/login/anonymous":
            anonymous_calls += 1
            assert parameters == {"clientId": "test-app"} and token == ""
            return {"accessToken": "guest"}
        assert token == "guest"
        if endpoint == "search/song/get/v3":
            assert parameters == {
                "keyword": "hello",
                "limit": 30,
                "offset": 30,
                "qualityFlag": True,
            }
            return {"recordCount": 64, "records": [song()]}
        assert endpoint == "playlist/song/list/get/v5"
        assert parameters == {
            "playlistId": "PLAYLIST",
            "limit": 100,
            "offset": 100,
            "qualityFlag": True,
        }
        return {"trackCount": 101, "tracks": [song()]}

    music = make_music(handler)
    search = await music.search("hello", page=2)
    page = await music.songs(Playlist("PLAYLIST", "Playlist"), page=2)
    assert search.tracks == page.tracks == [Track("ABC123", "Song", "Artist A / Artist B", 183)]
    assert search.has_more and search.total == 64 and search.page_size == 30
    assert not page.has_more and page.total == 101 and page.page_size == 100
    assert anonymous_calls == 1
    await music.close()
    assert music.client.closed


async def test_created_and_followed_playlists_fetch_all_pages(make_music):
    """Merge both account collections and deduplicate repeated playlist identities."""
    calls = []

    def handler(endpoint, parameters, token):
        """Return short pages to check offset progression by actual record count."""
        assert token == "account"
        calls.append((endpoint, parameters["offset"]))
        if endpoint == "playlist/created/get/v2":
            items = ["A", "B"] if parameters["offset"] == 0 else ["C"]
            total = 3
        else:
            assert endpoint == "playlist/subed/get/v2"
            items, total = ["C", "D"], 2
        return {
            "records": [
                {
                    "id": item,
                    "name": item,
                    "trackCount": 10,
                    "playCount": 0 if item == "A" else 1200,
                    "coverImgUrl": f"https://cover.test/{item}.jpg",
                }
                for item in items
            ],
            "recordCount": total,
        }

    music = make_music(handler)
    with pytest.raises(ValueError, match="登录"):
        await music.playlists()
    music.client.token, music.user_id = "account", "USER"
    playlists = await music.playlists()
    assert [item.id for item in playlists] == ["A", "B", "C", "D"]
    assert [item.play_count for item in playlists] == [0, 1200, 1200, 1200]
    assert [item.editable for item in playlists] == [True, True, True, False]
    assert [item.cover_url for item in playlists] == [
        "https://cover.test/A.jpg",
        "https://cover.test/B.jpg",
        "https://cover.test/C.jpg",
        "https://cover.test/D.jpg",
    ]
    assert [offset for _, offset in calls] == [0, 2, 0]


@pytest.mark.parametrize("count", [0, 1])
async def test_missing_playlist_is_empty_only_when_known_empty(make_music, count):
    """Do not turn an inaccessible nonempty playlist into a successful empty result."""

    def handler(endpoint, parameters, token):
        """Return the official ambiguous empty-or-inaccessible resource status."""
        raise OfficialAPIError("10007")

    music = make_music(handler)
    music.client.token = "account"
    playlist = Playlist("PLAYLIST", "Playlist", song_count=count)
    if count == 0:
        assert (await music.songs(playlist)).tracks == []
    else:
        with pytest.raises(OfficialAPIError):
            await music.songs(playlist)


@pytest.mark.parametrize("valid", [True, False])
async def test_restore_requires_verified_profile(make_music, valid):
    """Local credentials alone cannot mark a user as logged in."""

    def handler(endpoint, parameters, token):
        """Verify restored credentials using the official account endpoint."""
        assert endpoint == "user/profile/get/v2" and token == "saved"
        return {"id": "USER"} if valid else {}

    music = make_music(handler)
    assert not await music.restore()
    music.client.restored, music.client.token = True, "saved"
    if valid:
        assert await music.restore()
    else:
        with pytest.raises(ValueError, match="登录"):
            await music.restore()
        assert music.client.token == ""
    assert music.logged_in is valid


@pytest.mark.parametrize("valid", [True, False])
async def test_qr_login_uses_guest_token_and_saves_only_verified_account(
    make_music, monkeypatch, valid
):
    """Convert scan states and verify issued tokens before saving and DONE."""
    statuses = iter([801, 802, 803])
    credentials = {"accessToken": "new-account", "refreshToken": "refresh", "expireTime": 604800}

    async def immediate_sleep(seconds):
        """Advance the deterministic poll sequence without a real delay."""

    def handler(endpoint, parameters, token):
        """Exercise guest QR creation and nested login-token response data."""
        if endpoint == "oauth2/login/anonymous":
            assert token == ""
            return {"accessToken": "guest"}
        if endpoint == "user/profile/get/v2":
            assert token == "new-account"
            return {"id": "USER"} if valid else {}
        assert token == "guest"
        if endpoint == "user/oauth2/qrcodekey/get/v2":
            assert parameters == {"type": 2, "expiredKey": "300"}
            return {"qrCodeUrl": "https://163cn.tv/example", "uniKey": "key"}
        assert endpoint == "oauth2/device/login/qrcode/get"
        assert parameters == {"key": "key", "clientId": "test-app"}
        return {"status": next(statuses), "accessToken": credentials}

    monkeypatch.setattr("music_tui.sources.netease_official.asyncio.sleep", immediate_sleep)
    music = make_music(handler)
    session = music.login_session()
    assert (await session.get_qrcode()).startswith(b"\x89PNG\r\n\x1a\n")
    if valid:
        assert [state async for state in session] == [LoginState.CONFIRMED, LoginState.DONE]
        assert music.client.saved == credentials and music.logged_in
    else:
        with pytest.raises(ValueError):
            _ = [state async for state in session]
        assert music.client.saved is None and not music.logged_in


@pytest.mark.parametrize("status, expected", [(800, LoginState.EXPIRED), (804, LoginState.REFUSED)])
async def test_unsuccessful_qr_does_not_save_tokens(make_music, status, expected):
    """Expiry and rejected authorization end the login session without persistence."""

    def handler(endpoint, parameters, token):
        """Return the requested terminal QR state."""
        return {"status": status}

    music = make_music(handler)
    session = music.login_session()
    session.key, session.token = "key", "guest"
    assert [state async for state in session] == [expected]
    assert music.client.saved is None and not music.logged_in


@pytest.mark.parametrize(
    "changes",
    [
        {"visible": False},
        {"playFlag": False},
        {"playUrl": None},
        {"freeTrail": {"start": 0, "end": 30}},
        {"level": "standard"},
    ],
)
async def test_stream_rejects_denied_preview_and_downgraded_audio(make_music, changes):
    """Honor copyright and account permissions without playing preview or downgraded audio."""

    def handler(endpoint, parameters, token):
        """Return a denied or mismatched official detail response."""
        assert endpoint == "song/detail/get/v2"
        assert parameters == {"songId": "ABC123", "withUrl": True, "level": "lossless"}
        return {
            "visible": True,
            "playFlag": True,
            "playUrl": "https://audio.test/song.flac",
            "freeTrail": None,
            "level": "lossless",
        } | changes

    music = make_music(handler)
    music.client.token = "account"
    with pytest.raises(QualityUnavailableError, match="不可用"):
        await music.stream_url(Track("ABC123", "Song", "Artist", 180), Quality.LOSSLESS)


@pytest.mark.parametrize("code", ["10003", "10004", "300", "1406", "1407", "1408", "500"])
async def test_stream_error_codes_distinguish_song_permissions_from_api_failures(make_music, code):
    """Retry only explicit song copyright or membership restrictions, preserving other errors."""

    def handler(endpoint, parameters, token):
        """Raise a machine-readable official permission, authentication, or API error."""
        raise OfficialAPIError(code)

    music = make_music(handler)
    music.client.token = "account"
    expected = QualityUnavailableError if code in {"10003", "10004"} else OfficialAPIError
    with pytest.raises(expected):
        await music.stream_url(Track("ABC123", "Song", "Artist", 180), Quality.LOSSLESS)


async def test_available_qualities_respect_per_quality_permissions(make_music):
    """Only playable quality entries are offered, and accepted streams retain their URL."""

    def handler(endpoint, parameters, token):
        """Authorize standard and lossless without allowing higher membership tiers."""
        if endpoint == "song/detail/get/v2":
            return {
                "visible": True,
                "playFlag": True,
                "level": "lossless",
                "playUrl": "https://audio.test/song.flac",
                "freeTrail": None,
            }
        assert endpoint == "song/music/quality/sound/sp/get"
        return {
            "sqMusic": {"sqPrivilege": {"playFlag": True}},
            "lmusic": {"sqPrivilege": {"playFlag": True}},
            "hrMusic": {"sqPrivilege": {"playFlag": False}},
        }

    music = make_music(handler)
    music.client.token = "account"
    track = Track("ABC123", "Song", "Artist", 180)
    assert await music.available_qualities(track) == [Quality.LOSSLESS, Quality.STANDARD]
    assert await music.stream_url(track, Quality.LOSSLESS) == "https://audio.test/song.flac"


async def test_official_source_search_and_play_through_music_app(make_music):
    """Pass real official field mappings through terminal browsing and playback."""
    loaded = []

    async def start():
        """Start the fake audio backend without launching mpv."""

    async def load(url, **options):
        """Record the URL selected by the app from the official source."""
        loaded.append(url)

    async def command(*args):
        """Accept app playback commands without touching system audio."""

    def handler(endpoint, parameters, token):
        """Serve a guest search and a full standard-quality stream."""
        if endpoint == "oauth2/login/anonymous":
            return {"accessToken": "guest"}
        assert token == "guest"
        if endpoint == "search/song/get/v3":
            return {"recordCount": 1, "records": [song()]}
        assert endpoint == "song/detail/get/v2"
        assert parameters["songId"] == "ABC123"
        return {
            "visible": True,
            "playFlag": True,
            "playUrl": "https://audio.test/song.mp3",
            "level": "standard",
            "freeTrail": None,
        }

    player = SimpleNamespace(
        start=start,
        load=load,
        command=command,
        properties={"volume": 70, "pause": False},
        events=asyncio.Queue(),
        entry_id=None,
    )
    app = MusicApp(make_music(handler), player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("/", "h", "i", "enter")
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 1
        assert app.tracks[0].mid == "ABC123"
        await pilot.press("enter")
        await pilot.pause()
        assert loaded == ["https://audio.test/song.mp3"]
        assert app.current.title == "Song"
