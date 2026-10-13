"""Exercise top-level tab navigation without changing audio or accumulating pages."""

import asyncio
from dataclasses import asdict

import pytest
from test_app import Library
from test_platform_switch import PlatformLibrary
from test_playback_features import FeatureAudio, FeatureLibrary
from textual.widgets import DataTable, TabbedContent, Tabs

from music_tui.app import MusicApp
from music_tui.history_screen import HistoryScreen
from music_tui.playback_screen import PlaybackScreen
from music_tui.playback_session import RepeatMode
from music_tui.session import SessionStore


@pytest.mark.parametrize("size", [(80, 24), (48, 16)])
async def test_navigation_clicks_preserve_playback_and_return_focus(size):
    """Move through all primary pages and restore the selected browsing context."""
    app = MusicApp(FeatureLibrary(), FeatureAudio())
    async with app.run_test(size=size) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        track = app.current
        loaded = list(app.player.loaded)
        await pilot.click("#navigation #playback")
        await pilot.pause()
        assert isinstance(app.screen, PlaybackScreen)
        await pilot.click("#navigation #history")
        await pilot.pause()
        assert isinstance(app.screen, HistoryScreen)
        await pilot.click("#navigation #playback")
        await pilot.pause()
        assert isinstance(app.screen, PlaybackScreen)
        await pilot.click("#navigation #browse")
        await pilot.pause()
        assert app.screen is app.screen_stack[0]
        assert app.query_one("#navigation", Tabs).active == "browse"

        assert app.query_one("#songs", DataTable).has_focus
        assert app.current == track and app.player.loaded == loaded
        assert app.source == "original"
        await pilot.click("#navigation #history")
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#navigation", Tabs).active == "browse"


@pytest.mark.parametrize("change", ["platform", "account"])
async def test_identity_change_leaves_history_snapshot_and_clears_old_records(tmp_path, change):
    """Destroy the previous identity's history page before exposing the new account."""
    original = PlatformLibrary("qq")
    original.account_id = "first-account"
    store = SessionStore(tmp_path)
    store.record_play(
        "qq",
        original.account_id,
        {"track": asdict(original.track), "source": asdict(original.playlist), "page": 1},
    )
    app = MusicApp(
        original,
        FeatureAudio(),
        session_store=store,
        source_factory=lambda key: PlatformLibrary(key),
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("f8")
        await pilot.pause()
        page = app.screen
        assert isinstance(page, HistoryScreen) and page.entries[0].track == original.track
        if change == "platform":
            await app.switch_platform("netease-enhanced").wait()
        else:
            original.account_id = "second-account"
            await app.logged_in(True).wait()
        await pilot.pause()
        assert app.screen is app.screen_stack[0]
        assert page not in app.screen_stack
        await pilot.press("f8")
        await pilot.pause()
        assert isinstance(app.screen, HistoryScreen) and not app.screen.entries
        assert not app.player.loaded


@pytest.mark.parametrize("destination", ["search", "history", "lyrics"])
async def test_random_queue_result_does_not_override_later_navigation(destination):
    """Finish a slow queue request on the page the user subsequently chose."""
    started, finish = asyncio.Event(), asyncio.Event()

    class SlowLibrary(Library):
        async def search(self, keyword, page):
            """Delay full-source shuffle preparation without delaying the first page."""
            if page == 2:
                started.set()
                await finish.wait()
            return await super().search(keyword, page)

    app = MusicApp(SlowLibrary(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.repeat_mode = RepeatMode.RANDOM
        await pilot.press("f3")
        async with asyncio.timeout(2):
            await started.wait()
        await pilot.press({"search": "/", "history": "f8", "lyrics": "f4"}[destination])
        await pilot.pause()
        page, focus = app.screen, app.focused
        finish.set()
        async with asyncio.timeout(2):
            while not app.playback_queue.shuffle_pages:
                await pilot.pause()
        assert app.screen is page and app.focused is focus
        assert app.playback_queue.shuffle_pages[2][0]


async def test_changing_mode_and_reselecting_queue_prepares_random_successors():
    """Refresh an already visible queue when random mode is selected or F3 is repeated."""
    app = MusicApp(Library(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter", "f3")
        await pilot.pause()
        page = app.screen
        assert isinstance(page, PlaybackScreen)
        assert page.query_one("#playback-tabs", TabbedContent).active == "queue"
        await pilot.press("r", "r", "r")
        async with asyncio.timeout(2):
            while not app.playback_queue.shuffle_pages:
                await pilot.pause()
        assert app.repeat_mode == RepeatMode.RANDOM
        app.playback_queue.reset_shuffle(app.playback_queue.index)
        await pilot.press("f3")
        async with asyncio.timeout(2):
            while not app.playback_queue.shuffle_pages:
                await pilot.pause()
        assert app.screen is page and app.playback_queue.shuffle_pages[2][0]
