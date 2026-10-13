"""Exercise terminal interactions and queue behavior with offline library and player doubles."""

import asyncio
from types import SimpleNamespace

import pytest
import qrcode
from terminal_qrcode import SimpleImage
from textual.widgets import Button, DataTable, Footer, Input, ListView, Static

from music_tui.app import LoginScreen, MusicApp, QualityScreen
from music_tui.models import LoginState, Playlist, Quality, SongPage, Track
from music_tui.playback_session import RepeatMode

FIRST = Track("a", "第一首", "歌手 A", 60)
SECOND = Track("b", "第二首", "歌手 B", 80)
THIRD = Track("c", "下一页", "歌手 C", 90)


class Library:
    account_id = ""
    display_name = "QQ 音乐"

    def __init__(self):
        """Track whether an unfinished login request was cancelled."""
        self.login_cancelled = False

    async def restore(self):
        """Simulate startup without a saved login."""
        return False

    async def search(self, keyword, page):
        """Return two deterministic search pages for queue and pagination tests."""
        return (
            SongPage([FIRST, SECOND], True, 3, 2) if page == 1 else SongPage([THIRD], False, 3, 2)
        )

    async def songs(self, playlist, page):
        """Return a single-song playlist for browsing tests."""
        return SongPage([THIRD], False, 1)

    async def stream_url(self, track, quality):
        """Return the track ID as a URL or simulate a playback permission denial."""
        if track.mid == "denied":
            raise ValueError("此歌曲暂无播放权限")
        return track.mid

    async def available_qualities(self, track):
        """Authorize every quality for the default test library."""
        return list(Quality)

    def login_session(self):
        """Return a login session whose QR request remains pending."""

        async def get_qrcode():
            """Wait until cancellation and record that login cleanup occurred."""
            try:
                await asyncio.Event().wait()
            finally:
                self.login_cancelled = True

        return SimpleNamespace(get_qrcode=get_qrcode)


class Audio:
    def __init__(self):
        """Initialize observable playback state and recorded player commands."""
        self.events = asyncio.Queue()
        self.properties = {"volume": 70, "pause": False}
        self.loaded = []
        self.commands = []
        self.entry_id = None
        self.load_options = []

    async def start(self):
        """Simulate starting a player without launching an external process."""
        pass

    async def command(self, *args):
        """Record commands and apply pause toggles to the simulated state."""
        self.commands.append(args)
        if args == ("cycle", "pause"):
            self.properties["pause"] = not self.properties["pause"]

    async def load(self, url, *, start=0, paused=False):
        """Record the selected URL, starting position, and pause state."""
        self.loaded.append(url)
        self.load_options.append((start, paused))


async def test_keyboard_and_queue_remain_independent_of_browsing():
    """Verify search input, playback shortcuts, and queue advancement across browsing changes."""
    music, player = Library(), Audio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("/", "q", "space", "n", "p", "l", "m")
        assert app.query_one("#search", Input).value == "q nplm"
        assert app.screen is not None and not isinstance(app.screen, LoginScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(DataTable).row_count == 2
        await pilot.press("enter")
        await pilot.pause()
        assert player.loaded == ["a"]
        await pilot.press("space", "right", "left", "equal")
        await pilot.pause()
        assert player.properties["pause"]
        assert ("seek", 5, "relative") in player.commands
        assert ("seek", -5, "relative") in player.commands
        assert ("add", "volume", 5) in player.commands

        app.load_view(Playlist(1, "另一个歌单"))
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert player.loaded[-1] == "b"
        player.events.put_nowait({"event": "end-file", "reason": "eof", "playlist_entry_id": 99})
        app.update_player()
        await pilot.pause()
        assert player.loaded[-1] == "b"
        player.events.put_nowait({"event": "end-file", "reason": "eof"})
        app.update_player()
        await pilot.pause()
        assert player.loaded[-1] == "c"
        assert app.source == Playlist(1, "另一个歌单")
        assert len(app.tracks) == 1
        player.events.put_nowait({"event": "end-file", "reason": "eof"})
        app.update_player()
        await pilot.pause()
        assert app.current is None
        assert "已结束" in str(app.query_one("#status", Static).render())


async def test_playback_permission_failure_is_visible_and_does_not_auto_skip():
    """Verify denied playback displays an error without starting the next track."""
    app = MusicApp(Library(), Audio())
    async with app.run_test() as pilot:
        app.playback_queue.replace(
            [Track("denied", "会员歌曲", "歌手", 200), FIRST], None, 1, False, 0
        )
        app.play_index(0)
        await pilot.pause()
        assert app.current is None
        assert app.player.loaded == []
        assert "暂无播放权限" in str(app.query_one("#status", Static).render())


@pytest.mark.parametrize("display_name", ["QQ 音乐", "网易云音乐"])
async def test_escape_cancels_pending_login_and_returns_focus(display_name):
    """Verify Escape cancels QR loading and restores focus to the song table."""
    library = Library()
    library.display_name = display_name
    app = MusicApp(library, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, LoginScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert library.login_cancelled
        assert not isinstance(app.screen, LoginScreen)
        assert isinstance(app.focused, DataTable)


async def test_replaced_search_cannot_overwrite_new_result():
    """Verify cancellation prevents a stale search from replacing the latest results."""

    class SlowLibrary(Library):
        async def search(self, keyword, page):
            """Block the old search while allowing its replacement to finish."""
            if keyword == "old":
                await asyncio.Event().wait()
            return SongPage([SECOND], False, 1)

    app = MusicApp(SlowLibrary(), Audio())
    async with app.run_test() as pilot:
        app.load_view("old")
        await pilot.pause()
        app.load_view("new")
        await pilot.pause()
        assert app.source == "new"
        assert app.tracks == [SECOND]


@pytest.mark.parametrize("display_name", ["QQ 音乐", "网易云音乐"])
async def test_qr_fits_small_terminal_and_success_loads_library(display_name):
    """Verify QR placement at 80×24 and library loading after confirmed login."""
    confirmed = asyncio.Event()
    qr = qrcode.QRCode(version=4, border=0)
    qr.add_data("https://example.com/login-test")
    qr.make(fit=False)
    data = SimpleImage.from_qr_matrix(qr.get_matrix()).resize((132, 132)).to_png_bytes()

    class Session:
        async def get_qrcode(self):
            """Return the generated QR image used by the terminal layout test."""
            return data

        async def __aiter__(self):
            """Emit confirmation and successful login after the test releases the session."""
            yield LoginState.CONFIRMED
            await confirmed.wait()
            music.saved = True
            yield LoginState.DONE

    class LoginLibrary(Library):
        saved = None

        def login_session(self):
            """Provide a controllable successful login session."""
            return Session()

        async def playlists(self):
            """Return the favorite playlist displayed after login."""
            return [Playlist(0, "我喜欢", 201, True)]

    music = LoginLibrary()
    music.display_name = display_name
    app = MusicApp(music, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        region = app.screen.query_one("#qr").region
        assert region.width == 41 and region.height == 21
        assert region.y >= 0 and region.bottom <= 22
        assert abs((region.x + region.width / 2) - 40) <= 1
        assert f"{display_name} App 确认" in str(
            app.screen.query_one("#login-state", Static).render()
        )
        assert f"{display_name} App 扫码登录" in str(
            app.screen.query_one("#login-title", Static).render()
        )
        confirmed.set()
        await pilot.pause()
        assert music.saved is True
        assert display_name in str(app.query_one("#heading", Static).render())
        assert not isinstance(app.screen, LoginScreen)
        assert app.playlists[0].title == "我喜欢"
        assert app.tracks == [THIRD]


@pytest.mark.parametrize(
    "message",
    [
        "无法连接网易云 API 服务。\n请另开终端运行：\n"
        "uv run music-tui --netease-service\n服务启动后按 R 重试。",
        "无法访问网易云 API，请另开终端运行 uv run music-tui --netease-service，"
        "保持服务运行后按 R 重试。",
    ],
)
async def test_login_connection_error_shows_full_command_in_small_terminal(message):
    """Show wrapped connection guidance and preserve refresh controls at 80×24."""

    class UnavailableLibrary(Library):
        display_name = "网易云音乐（Enhanced）"
        attempts = 0

        def login_session(self):
            """Provide a failed QR request and count refreshed login attempts."""
            self.attempts += 1

            async def get_qrcode():
                """Report service startup instructions without returning a QR image."""
                raise ValueError(message)

            return SimpleNamespace(get_qrcode=get_qrcode)

    music = UnavailableLibrary()
    app = MusicApp(music, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        status = app.screen.query_one("#login-state", Static)
        assert status.region.height >= max(2, len(message.splitlines()))
        assert status.region.y >= 0 and status.region.bottom <= 23
        lines = [status.render_line(row).text for row in range(status.region.height)]
        assert "".join(message.split()) in "".join("".join(lines).split())
        keys = app.screen.query_one("#login-keys").region
        assert keys.y >= status.region.bottom and keys.bottom <= 24
        await pilot.press("r")
        await pilot.pause()
        assert music.attempts == 2
        await pilot.press("escape")
        assert not isinstance(app.screen, LoginScreen)


@pytest.mark.parametrize("state", [LoginState.EXPIRED, LoginState.REFUSED])
async def test_netease_expired_or_refused_login_can_be_refreshed(state):
    """Keep unsuccessful NetEase login visible without treating it as a saved account."""
    qr = qrcode.QRCode(border=0)
    qr.add_data("https://example.com/login-test")
    qr.make(fit=True)
    data = SimpleImage.from_qr_matrix(qr.get_matrix()).to_png_bytes()

    class Session:
        async def get_qrcode(self):
            """Provide a QR image for the unsuccessful login attempt."""
            return data

        async def __aiter__(self):
            """End polling with the account's expiry or refusal state."""
            yield state

    class NetEaseLibrary(Library):
        display_name = "网易云音乐"

        def login_session(self):
            """Create a fresh unsuccessful login attempt on each refresh."""
            return Session()

    app = MusicApp(NetEaseLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        assert isinstance(app.screen, LoginScreen)
        assert "二维码已过期或已取消" in str(app.screen.query_one("#login-state", Static).render())
        assert not app.account_logged_in
        await pilot.press("r")
        await pilot.pause()
        assert "二维码已过期或已取消" in str(app.screen.query_one("#login-state", Static).render())


async def test_quality_switch_preserves_position_pause_and_applies_to_next_song():
    """Verify quality changes retain playback state and govern the next track."""

    class QualityLibrary(Library):
        async def stream_url(self, track, quality):
            """Encode requested quality in the fake URL."""
            return f"{track.mid}/{quality.name}"

    player = Audio()
    app = MusicApp(QualityLibrary(), player)
    async with app.run_test(size=(80, 24)) as pilot:
        app.playback_queue.replace([FIRST, SECOND], None, 1, False, 0)
        app.play_index(0)
        await pilot.pause()
        player.properties.update({"time-pos": 42, "pause": True})
        await pilot.press("u")
        await pilot.pause()
        assert isinstance(app.screen, QualityScreen)
        app.update_player()  # Playback state also updates while the selector is open.
        await pilot.press("up", "up", "enter")
        await pilot.pause()
        assert app.quality == Quality.LOSSLESS
        assert player.loaded[-1] == "a/LOSSLESS"
        assert player.load_options[-1] == (42, True)
        assert app.current_quality == Quality.LOSSLESS
        app.update_player()
        assert "SQ 无损" in str(app.query_one("#now-playing", Static).render())
        await pilot.press("n")
        await pilot.pause()
        assert player.loaded[-1] == "b/LOSSLESS"
        assert player.load_options[-1] == (0, False)
        await pilot.press("/", "u")
        assert app.query_one("#search", Input).value == "u"


async def test_quality_lookup_error_keeps_existing_playback_and_actual_quality():
    """Keep current audio on generic lookup errors, without inferring restrictions from text."""

    class QualityLibrary(Library):
        async def stream_url(self, track, quality):
            """Fail the lossless lookup with an ordinary error, leaving standard audio available."""
            if quality == Quality.LOSSLESS:
                raise ValueError("无损 FLAC 不可用")
            return track.mid

    player = Audio()
    app = MusicApp(QualityLibrary(), player)
    async with app.run_test(size=(80, 24)) as pilot:
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        app.play_index(0)
        await pilot.pause()
        commands_before = len(player.commands)
        await pilot.press("u", "up", "up", "enter")
        await pilot.pause()
        assert app.quality == Quality.LOSSLESS
        assert app.current_quality == Quality.STANDARD
        assert app.current == FIRST
        assert player.loaded == ["a"]
        assert len(player.commands) == commands_before
        assert "不可用" in str(app.query_one("#status", Static).render())
        await pilot.press("u", "up", "escape")
        assert app.quality == Quality.LOSSLESS


async def test_quality_menu_only_shows_current_song_authorized_formats():
    """Verify the selector lists authorized formats and handles songs with none."""

    class RestrictedLibrary(Library):
        async def available_qualities(self, track):
            """Authorize two formats for the first song and none for other songs."""
            return [Quality.HIGH, Quality.STANDARD] if track == FIRST else []

    app = MusicApp(RestrictedLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("u")
        await pilot.pause()
        assert not isinstance(app.screen, QualityScreen)
        app.load_view("test")
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert app.screen.qualities == [Quality.HIGH, Quality.STANDARD]
        assert len(app.screen.query_one(ListView).children) == 2
        await pilot.press("up", "enter")
        await pilot.pause()
        assert app.quality == Quality.HIGH
        await pilot.press("down", "u")
        await pilot.pause()
        assert not isinstance(app.screen, QualityScreen)
        assert "暂无可用播放地址" in str(app.query_one("#status", Static).render())


@pytest.mark.parametrize("size", [(80, 24), (112, 32), (48, 16)])
async def test_browse_controls_fit_without_repeated_playback_hints(size):
    """Keep paging controls visible and avoid duplicating the footer's playback hints."""
    app = MusicApp(Library(), Audio())
    async with app.run_test(size=size) as pilot:
        await app.load_view(Playlist(1, "歌单")).wait()
        await pilot.pause()
        detail = app.query_one("#playback-detail", Static)
        status = app.query_one("#status", Static)
        footer = app.query_one(Footer)
        assert "Space" not in str(detail.render())
        assert "Enter" not in str(status.render())
        assert detail.region.bottom <= status.region.y < footer.region.y
        songs = app.query_one("#songs-box")
        for selector in ("#previous-page", "#next-page"):
            button = app.query_one(selector, Button)
            assert button.region.height == 1
            assert songs.region.contains_region(button.region)
            assert button.region.bottom <= app.query_one("#player-box").region.y
        assert app.query_one(DataTable).region.height >= 1


async def test_visible_pagination_controls_keep_playback_and_show_page_limits():
    """Page through visible buttons without replacing the active playback queue."""
    app = MusicApp(Library(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("test").wait()
        await pilot.press("enter")
        await pilot.pause()
        previous = app.query_one("#previous-page", Button)
        following = app.query_one("#next-page", Button)
        assert previous.disabled and not following.disabled
        assert "[" in str(previous.label) and "]" in str(following.label)
        await pilot.click("#next-page")
        await app.workers.wait_for_complete()
        assert app.page == 2 and app.tracks == [THIRD]
        assert app.current == FIRST and app.playback_queue.view(app.repeat_mode).tracks == (
            FIRST,
            SECOND,
        )
        assert not previous.disabled and following.disabled
        await pilot.click("#previous-page")
        await app.workers.wait_for_complete()
        assert app.page == 1 and app.tracks == [FIRST, SECOND]
        assert previous.disabled and not following.disabled


async def test_bracket_pagination_works_after_leaving_text_entry():
    """Allow bracket text in search, then page with separate bracket keys after Escape."""
    app = MusicApp(Library(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("test").wait()
        await pilot.press("/", "[", "]")
        assert app.query_one("#search", Input).value == "[]"
        assert app.page == 1
        await pilot.press("escape", "]")
        await app.workers.wait_for_complete()
        assert app.page == 2 and app.tracks == [THIRD]
        await pilot.press("[")
        await app.workers.wait_for_complete()
        assert app.page == 1 and app.tracks == [FIRST, SECOND]


async def test_pagination_replaces_rows_and_keeps_playback_queue():
    """Verify page navigation updates browsing without replacing the active queue."""
    app = MusicApp(Library(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        app.load_view("test")
        await pilot.pause()
        await pilot.press("enter", "pagedown")
        await pilot.pause()
        assert app.current == FIRST
        assert app.page == 2 and app.tracks == [THIRD]
        assert app.query_one(DataTable).row_count == 1
        assert "第 2 页" in str(app.query_one("#view-meta", Static).render())
        await pilot.press("]")
        assert app.page == 2
        assert "最后一页" in str(app.query_one("#status", Static).render())
        await pilot.press("n")
        await pilot.pause()
        assert app.current == SECOND
        assert app.page == 2 and app.tracks == [THIRD]
        await pilot.press("pageup")
        await pilot.pause()
        assert app.page == 1 and app.tracks == [FIRST, SECOND]
        assert app.query_one(DataTable).cursor_row == 0
        await pilot.press("[")
        assert app.page == 1
        assert "第一页" in str(app.query_one("#status", Static).render())


async def test_loop_whole_list_when_started_on_later_page():
    """Verify list repeat crosses page boundaries independently of the visible page."""
    app = MusicApp(Library(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        app.load_view("test", page=2)
        await pilot.pause()
        await pilot.press("enter", "r")
        await pilot.pause()
        assert app.current == THIRD
        assert app.repeat_mode == RepeatMode.LIST
        assert "列表循环" in str(app.query_one("#heading", Static).render())
        app.player.events.put_nowait({"event": "end-file", "reason": "eof"})
        app.update_player()
        await pilot.pause()
        assert app.current == FIRST
        assert app.page == 2 and app.tracks == [THIRD]
        await pilot.press("p")
        await pilot.pause()
        assert app.current == THIRD
        await pilot.press("p")
        await pilot.pause()
        assert app.current == SECOND
        await pilot.press("n")
        await pilot.pause()
        assert app.current == THIRD


async def test_single_loop_replays_on_end_but_manual_next_changes_song():
    """Verify single repeat handles EOF while manual navigation still changes tracks."""
    app = MusicApp(Library(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        app.load_view("test")
        await pilot.pause()
        await pilot.press("enter", "r", "r")
        await pilot.pause()
        assert app.repeat_mode == RepeatMode.SINGLE
        app.player.events.put_nowait({"event": "end-file", "reason": "eof"})
        app.update_player()
        await pilot.pause()
        assert app.player.loaded == ["a", "a"]
        assert app.player.load_options[-1] == (0, False)
        await pilot.press("n")
        await pilot.pause()
        assert app.current == SECOND
        await pilot.press("r")
        assert app.repeat_mode == RepeatMode.RANDOM
        await pilot.press("r")
        assert app.repeat_mode == RepeatMode.SEQUENCE


async def test_failed_page_load_keeps_visible_page_and_can_retry():
    """Verify failed pagination retains displayed rows and permits a later retry."""

    class UnavailablePage(Library):
        fail = True

        async def search(self, keyword, page):
            """Fail the second page until the test enables a successful retry."""
            if page == 2 and self.fail:
                raise ValueError("分页请求失败")
            return await super().search(keyword, page)

    library = UnavailablePage()
    app = MusicApp(library, Audio())
    async with app.run_test() as pilot:
        app.load_view("test")
        await pilot.pause()
        await pilot.press("]")
        await pilot.pause()
        assert app.page == 1 and app.tracks == [FIRST, SECOND]
        assert "分页请求失败" in str(app.query_one("#status", Static).render())
        library.fail = False
        await pilot.press("]")
        await pilot.pause()
        assert app.page == 2 and app.tracks == [THIRD]


async def test_random_plays_all_pages_without_repeats_and_preserves_history():
    """Verify shuffle covers all pages and reuses navigation history without changing browsing."""
    fourth = Track("d", "最后一页", "歌手 D", 120)

    class PagedLibrary(Library):
        async def search(self, keyword, page):
            """Return three search pages with four distinct tracks."""
            tracks, more = {1: ([FIRST, SECOND], True), 2: ([THIRD], True), 3: ([fourth], False)}[
                page
            ]
            return SongPage(tracks, more, 4, 2)

    app = MusicApp(PagedLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        app.load_view("test", page=2)
        await pilot.pause()
        await pilot.press("enter", "r", "r", "r")
        await pilot.pause()
        assert app.repeat_mode == RepeatMode.RANDOM
        assert "随机播放" in str(app.query_one("#heading", Static).render())
        for _ in range(3):
            app.player.events.put_nowait({"event": "end-file", "reason": "eof"})
            app.update_player()
            await pilot.pause()
        assert len(set(app.player.loaded)) == 4
        assert set(app.player.loaded) == {"a", "b", "c", "d"}
        assert app.page == 2 and app.tracks == [THIRD]
        before = app.player.loaded.copy()
        await pilot.press("p")
        await pilot.pause()
        assert app.player.loaded[-1] == before[-2]
        await pilot.press("n")
        await pilot.pause()
        assert app.player.loaded[-1] == before[-1]
        for _ in range(4):
            await pilot.press("n")
            await pilot.pause()
        assert len(set(app.player.loaded[-4:])) == 4
        assert app.player.loaded[-4] != before[-1]
        # Browsing a different list does not replace the random queue.
        app.load_view(Playlist(1, "其他歌单"))
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert app.source == Playlist(1, "其他歌单")
        assert app.playback_queue.view(app.repeat_mode).source == "test"


async def test_random_single_song_replays_and_search_input_keeps_r():
    """Verify one-song shuffle replays and typing r does not change repeat mode."""
    app = MusicApp(Library(), Audio())
    async with app.run_test() as pilot:
        app.load_view(Playlist(1, "单首歌单"))
        await pilot.pause()
        await pilot.press("enter", "r", "r", "r", "n")
        await pilot.pause()
        assert app.player.loaded == ["c", "c"]
        await pilot.press("p")
        await pilot.pause()
        assert app.current == THIRD
        await pilot.press("/", "r")
        assert app.query_one("#search", Input).value == "r"
        assert app.repeat_mode == RepeatMode.RANDOM


async def test_random_page_failure_keeps_audio_and_retries_entire_list():
    """Verify failed shuffle expansion preserves playback and retries missing pages."""

    class UnavailablePage(Library):
        fail = True

        async def search(self, keyword, page):
            """Fail the second page until the test enables a successful retry."""
            if page == 2 and self.fail:
                raise ValueError("分页请求失败")
            return await super().search(keyword, page)

    library = UnavailablePage()
    app = MusicApp(library, Audio())
    async with app.run_test() as pilot:
        app.load_view("test")
        await pilot.pause()
        await pilot.press("enter", "r", "r", "r")
        await pilot.pause()
        before = app.player.commands.copy()
        await pilot.press("n")
        await pilot.pause()
        assert app.current == FIRST and app.player.commands == before
        assert "分页请求失败" in str(app.query_one("#status", Static).render())
        library.fail = False
        await pilot.press("n", "n")
        await pilot.pause()
        assert set(app.player.loaded) == {"a", "b", "c"}


class CatalogLibrary(Library):
    catalog = [
        Track(str(i), f"{'晨光' if i % 2 else '雨夜'} {i}", "Singer Q" if i % 2 else "歌手 Z", 180)
        for i in range(230)
    ]

    def __init__(self):
        """Record which catalog pages are requested during filtering."""
        super().__init__()
        self.pages = []

    async def playlists(self):
        """Return playlists with counts that full catalog loading can correct."""
        return [Playlist(1, "通勤歌单", song_count=200), Playlist(2, "其他歌单", song_count=1)]

    async def songs(self, playlist, page):
        """Record the request and return a 100-song slice of the catalog."""
        self.pages.append(page)
        start = (page - 1) * 100
        return SongPage(self.catalog[start : start + 100], start + 100 < len(self.catalog), 230)


async def test_playlist_counts_and_search_all_pages_keep_playback_independent():
    """Verify filtering uses the full cached catalog while the original queue continues."""
    library = CatalogLibrary()
    app = MusicApp(library, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        app.load_playlists()
        await pilot.pause()
        assert "共 230 首" in str(app.query_one("#view-meta", Static).render())
        assert "230 首" in str(
            app.query_one(ListView).children[0].query_one(".playlist-count").render()
        )
        assert app.query_one("#filter", Input).display
        await pilot.press("enter")
        await pilot.pause()
        original = app.current
        await pilot.press("f")
        assert app.focused.id == "filter"
        app.query_one("#filter", Input).value = "singer q"
        await pilot.press("enter")
        await pilot.pause()
        assert app.total == 115 and app.has_more
        assert len(app.tracks) == 100
        assert app.current == original
        assert "匹配 115 首" in str(app.query_one("#view-meta", Static).render())
        assert library.pages == [1, 1, 2, 3]
        await pilot.press("]")
        await pilot.pause()
        assert app.page == 2 and len(app.tracks) == 15
        assert all(track.artist == "Singer Q" for track in app.tracks)
        await pilot.press("n")
        await pilot.pause()
        assert app.current == library.catalog[1]  # The original queue still advances.
        await pilot.press("f")
        app.query_one("#filter", Input).value = "晨光"
        await pilot.press("enter")
        await pilot.pause()
        assert app.page == 1 and app.total == 115
        assert library.pages == [1, 1, 2, 3]  # Further searches reuse the complete song list.
        await pilot.press("f")
        app.query_one("#filter", Input).value = "不存在的歌"
        await pilot.press("enter")
        await pilot.pause()
        assert app.total == 0 and app.query_one(DataTable).row_count == 0
        assert "没有找到歌曲" in str(app.query_one("#status", Static).render())
        await pilot.press("f")
        app.query_one("#filter", Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
        assert app.total == 230 and len(app.tracks) == 100
        assert app.source == app.playlists[0]
        app.load_view("全站搜索")
        await pilot.pause()
        assert not app.query_one("#filter", Input).display


async def test_filtered_list_playback_loops_and_shuffles_only_matching_songs():
    """Verify repeat and shuffle queues contain only songs matching the playlist filter."""
    library = CatalogLibrary()
    app = MusicApp(library, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        playlist = Playlist(1, "通勤歌单")
        app.load_view(playlist)
        await pilot.pause()
        await pilot.press("f")
        app.query_one("#filter", Input).value = "晨光"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("]")
        await pilot.pause()
        app.query_one(DataTable).move_cursor(row=14)
        await pilot.press("enter", "r", "n")
        await pilot.pause()
        assert app.current == library.catalog[1]  # Last matching song wraps to first match.
        await pilot.press("r", "r")
        for _ in range(3):
            await pilot.press("n")
            await pilot.pause()
            assert app.current.title.startswith("晨光")
        assert len(set(app.player.loaded[-3:])) == 3
        assert app.page == 2 and app.total == 115


async def test_cancelled_playlist_search_cannot_replace_new_view_or_cache_partial_list():
    """Verify cancelled filtering leaves neither stale results nor a partial catalog cache."""

    class SlowCatalog(CatalogLibrary):
        waiting = asyncio.Event()

        async def songs(self, playlist, page):
            """Signal and block the second catalog page to exercise cancellation."""
            if page == 2:
                self.waiting.set()
                await asyncio.Event().wait()
            return await super().songs(playlist, page)

    library = SlowCatalog()
    app = MusicApp(library, Audio())
    async with app.run_test() as pilot:
        playlist = Playlist(1, "通勤歌单")
        app.load_view(playlist)
        await pilot.pause()
        app.filter_playlist(playlist, "晨光")
        async with asyncio.timeout(2):
            await library.waiting.wait()
        app.load_view("新的搜索")
        await pilot.pause()
        assert app.source == "新的搜索"
        assert playlist not in app.playlist_cache


@pytest.mark.parametrize("size", [(80, 24), (112, 32)])
async def test_compact_layout_keeps_filter_table_and_playback_visible(size):
    """Verify controls, song rows, and playback details fit supported terminal sizes."""
    app = MusicApp(CatalogLibrary(), Audio())
    async with app.run_test(size=size) as pilot:
        app.load_playlists()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        app.player.properties.update({"time-pos": 90, "duration": 180})
        app.update_player()
        table = app.query_one(DataTable)
        field = app.query_one("#filter", Input)
        detail = app.query_one("#playback-detail", Static)
        assert table.region.height >= 8 and table.max_scroll_x == 0
        assert field.region.bottom <= table.region.y
        assert detail.region.bottom <= size[1] - 2
        assert "01:30" in str(detail.render()) and "03:00" in str(detail.render())
        assert "▸" in str(table.get_cell("0", "position"))
