"""Verify in-app platform changes keep accounts, browsing, and playback isolated."""

import asyncio

import pytest
from test_app import Audio, Library
from textual.widgets import Button, DataTable, Input, Label, ListView, Static

from music_tui.app import MusicApp
from music_tui.models import Playlist, Quality, SongPage, Track
from music_tui.playback_session import RepeatMode


class PlatformLibrary(Library):
    def __init__(self, key, *, logged_in=True, restore_error=None):
        """Create an identifiable account with observable restoration and cleanup."""
        super().__init__()
        self.display_name = key
        self.logged_in = logged_in
        self.restore_error = restore_error
        self.restored = 0
        self.closed = 0
        self.playlist = Playlist(f"{key}-list", f"{key} 歌单")
        self.track = Track(f"{key}-song", f"{key} 歌曲", "歌手", 60)

    async def restore(self):
        """Restore the configured account or report its connection failure."""
        self.restored += 1
        if self.restore_error:
            raise ValueError(self.restore_error)
        return self.logged_in

    async def playlists(self):
        """Return this platform's account-specific library."""
        return [self.playlist]

    async def songs(self, playlist, page):
        """Return this platform's track for its first playlist page."""
        return SongPage([self.track], False, 1)

    async def close(self):
        """Record release of the platform's client."""
        self.closed += 1


@pytest.mark.parametrize("target", ["netease-official", "netease-enhanced"])
async def test_switch_platform_and_return_restore_accounts_and_clear_old_state(target):
    """Switch both ways without carrying the previous account's queue or cache."""
    original = PlatformLibrary("qq")
    created = []

    def factory(key):
        """Create a fresh client for each selected platform."""
        music = PlatformLibrary(key)
        created.append(music)
        return music

    player = Audio()
    app = MusicApp(original, player, source_factory=factory, source_key="qq")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.load_view(original.playlist).wait()
        await pilot.press("enter")
        await pilot.pause()
        assert app.current == original.track
        app.playlist_cache[original.playlist] = (original.track,)
        app.quality = Quality.LOSSLESS
        app.repeat_mode = RepeatMode.RANDOM
        await app.playback_queue.prepare(app.repeat_mode, app.fetch)
        app.query_one("#search", Input).value = "旧平台搜索"
        before = len(player.commands)

        await app.switch_platform(target).wait()
        await pilot.pause()
        assert app.music is created[0]
        assert original.closed == 1
        assert created[0].restored == 1
        assert app.account_logged_in
        assert app.playlists == [created[0].playlist]
        assert app.tracks == [created[0].track]
        assert (
            app.current is None
            and not app.playback_queue.view(app.repeat_mode).tracks
            and app.playback_queue.view(app.repeat_mode).source is None
        )
        assert original.playlist not in app.playlist_cache
        assert original.playlist not in app.playlist_counts
        assert app.quality == Quality.STANDARD
        assert app.query_one("#search", Input).value == ""
        assert ("stop",) in player.commands[before:]
        assert target in str(app.query_one("#heading", Static).render())

        await app.switch_platform("qq").wait()
        await pilot.pause()
        assert app.music is created[1]
        assert created[0].closed == 1
        assert app.playlists == [created[1].playlist]
        assert app.tracks == [created[1].track]
        assert "qq" in str(app.query_one("#heading", Static).render())


@pytest.mark.parametrize("failure", ["configuration", "restore"])
async def test_switch_failure_keeps_current_account_and_playback(failure):
    """Keep the working source and audio when replacement setup cannot complete."""
    original = PlatformLibrary("qq")
    candidate = PlatformLibrary("netease-official", restore_error="官方连接失败")

    def factory(key):
        """Simulate either missing configuration or a failed token restoration."""
        if failure == "configuration":
            raise ValueError("缺少 NETEASE_APP_ID")
        return candidate

    player = Audio()
    app = MusicApp(original, player, source_factory=factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.load_view(original.playlist).wait()
        await pilot.press("enter")
        await pilot.pause()
        before = player.commands.copy()
        await app.switch_platform("netease-official").wait()
        await pilot.pause()
        assert app.music is original and original.closed == 0
        assert app.current == original.track
        assert app.playback_queue.view(app.repeat_mode).tracks == (original.track,)
        assert app.playlists == [original.playlist]
        assert player.commands == before
        assert candidate.closed == (failure == "restore")
        expected = "NETEASE_APP_ID" if failure == "configuration" else "官方连接失败"
        assert expected in str(app.query_one("#status", Static).render())


async def test_f2_opens_selector_from_search_and_escape_or_current_selection_is_unchanged():
    """Keep platform selection usable at 80×24 while text entry retains its contents."""
    from music_tui.app import PlatformScreen

    original = PlatformLibrary("qq", logged_in=False)
    requested = []

    def factory(key):
        """Record unexpected construction on cancellation or current selection."""
        requested.append(key)
        return PlatformLibrary(key)

    app = MusicApp(original, Audio(), source_factory=factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("/", "a", "f2")
        assert isinstance(app.screen, PlatformScreen)
        choices = app.screen.query_one("#platform-options", ListView)
        assert len(choices.children) == 2
        assert [str(label.render()) for label in choices.query(Label)] == [
            "QQ 音乐",
            "网易云音乐 · Enhanced（推荐）",
        ]
        assert choices.region.bottom <= 24
        await pilot.press("escape")
        assert not isinstance(app.screen, PlatformScreen)
        assert app.query_one("#search", Input).value == "a"
        assert app.music is original
        await app.switch_platform("qq").wait()
        assert requested == [] and original.closed == 0


async def test_official_cli_session_can_open_and_cancel_platform_selector():
    """Hide the official option while preserving a CLI-selected session on cancel."""
    original = PlatformLibrary("netease-official")
    app = MusicApp(original, Audio(), source_key="netease-official")
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("f2")
        choices = app.screen.query_one("#platform-options", ListView)
        assert len(choices.children) == 2
        assert choices.index is None
        assert "官方" not in str(app.screen.query_one("#platform-help", Static).render())
        await pilot.press("escape")
        assert app.music is original
        assert app.source_key == "netease-official"
        await pilot.press("f2", "down")
        assert app.screen.query_one("#platform-options", ListView).index == 0


async def test_selector_enter_changes_platform_and_offers_login_for_unsaved_account():
    """Select NetEase through the dialog without requiring an existing login."""
    original = PlatformLibrary("qq")
    candidate = PlatformLibrary("netease-enhanced", logged_in=False)

    def factory(key):
        """Return the selected NetEase account client."""
        return candidate

    app = MusicApp(original, Audio(), source_factory=factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("f2")
        choices = app.screen.query_one("#platform-options", ListView)
        choices.index = 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.music is candidate
        assert not app.account_logged_in
        assert not app.playlists and not app.tracks
        assert app.query_one(DataTable).row_count == 0
        assert app.query_one("#previous-page", Button).disabled
        assert app.query_one("#next-page", Button).disabled
        assert "未登录" in str(app.query_one("#heading", Static).render())
        assert original.closed == 1


async def test_cancelled_old_search_finishes_before_new_platform_state_is_applied():
    """Prevent a late response from the previous platform replacing the new library."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowLibrary(PlatformLibrary):
        async def search(self, keyword, page):
            """Return a late old result after observing request cancellation."""
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                return SongPage([self.track], False, 1)

    original = SlowLibrary("qq")
    candidate = PlatformLibrary("netease-official")

    def factory(key):
        """Return the selected NetEase account client."""
        return candidate

    app = MusicApp(original, Audio(), source_factory=factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.load_view("旧搜索")
        async with asyncio.timeout(2):
            await started.wait()
        await app.switch_platform("netease-official").wait()
        await pilot.pause()
        assert cancelled.is_set()
        assert original.closed == 1
        assert app.music is candidate
        assert app.source == candidate.playlist
        assert app.tracks == [candidate.track]
        assert app.query_one(DataTable).row_count == 1


async def test_cancel_after_source_transfer_closes_previous_client(monkeypatch):
    """Release the old source when exit interrupts the interface reset after transfer."""
    original = PlatformLibrary("qq", logged_in=False)
    candidate = PlatformLibrary("netease-enhanced", logged_in=False)
    clearing = asyncio.Event()

    def factory(key):
        """Return the replacement client owned by the app after transfer."""
        return candidate

    async def interrupted_clear(view):
        """Pause the first interface reset until the switching worker is cancelled."""
        clearing.set()
        await asyncio.Event().wait()

    app = MusicApp(original, Audio(), source_factory=factory)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        monkeypatch.setattr(ListView, "clear", interrupted_clear)
        worker = app.switch_platform("netease-official")
        async with asyncio.timeout(2):
            await clearing.wait()
        assert app.music is candidate
        worker.cancel()
        await asyncio.gather(worker.wait(), return_exceptions=True)
        assert original.closed == 1
        assert candidate.closed == 0
        assert app.music is candidate
        assert not app.switching
