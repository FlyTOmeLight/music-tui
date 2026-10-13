"""Exercise the full player, live queue and recent listening through the real interface."""

from dataclasses import asdict

import pytest
from test_app import FIRST, SECOND, THIRD
from test_playback_features import FeatureAudio, FeatureLibrary
from textual.widgets import DataTable, Input, ListView, Static, TabbedContent

from music_tui.app import MusicApp, QualityScreen
from music_tui.history_screen import HistoryScreen
from music_tui.models import Playlist
from music_tui.playback_screen import PlaybackScreen
from music_tui.playback_session import PlaylistSearch, RepeatMode
from music_tui.screens import HelpScreen, QueuePanel
from music_tui.session import SessionStore


@pytest.mark.parametrize("size", [(80, 24), (48, 16)])
async def test_playback_page_controls_same_audio_and_returns_to_browser(size):
    """Keep page navigation independent from active audio, browsing and queue ownership."""
    app = MusicApp(FeatureLibrary(), FeatureAudio())
    async with app.run_test(size=size) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("f7")
        page = app.screen
        assert isinstance(page, PlaybackScreen)
        assert app.current == FIRST and app.player.loaded == ["a"]
        await pilot.press("space")
        await pilot.pause()
        assert app.player.properties["pause"]
        await pilot.click("#playback-next-button")
        await pilot.pause()
        page.refresh_playback()
        assert app.current == SECOND
        assert "第二首" in str(page.query_one("#playback-title", Static).render())
        assert app.screen is page
        await pilot.press("f7")
        assert not isinstance(app.screen, PlaybackScreen)
        assert app.source == "original" and app.current == SECOND
        assert app.query_one("#songs", DataTable).has_focus


async def test_playback_tabs_keep_the_page_and_search_returns_to_browser(tmp_path):
    """Keep lyrics and queue in player tabs, with help as a temporary modal."""
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=SessionStore(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter", "f7")
        await pilot.pause()
        page = app.screen
        assert isinstance(page, PlaybackScreen)
        for key, tab in (("f3", "queue"), ("f4", "lyrics")):
            await pilot.press(key)
            await pilot.pause()
            assert app.screen is page
            assert page.query_one("#playback-tabs", TabbedContent).active == tab
        await pilot.press("f1")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("f1")
        assert app.screen is page
        await pilot.press("f8")
        assert isinstance(app.screen, HistoryScreen)
        await pilot.press("f8")
        assert app.screen is page
        await pilot.press("/")
        assert not isinstance(app.screen, PlaybackScreen)
        assert app.query_one("#search", Input).has_focus
        assert app.current == FIRST


async def test_player_ignores_hidden_browser_library_actions_and_switching_dispatch():
    """Do not write an unseen selected song or issue player commands during source transfer."""
    library = FeatureLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter", "f7")
        await pilot.pause()
        await pilot.press("s", "a")
        await pilot.pause()
        assert not library.writes and not app.playback_queue.view(app.repeat_mode).pending
        assert isinstance(app.screen, PlaybackScreen)
        commands = app.player.commands.copy()
        app.switching = True
        app.handle_playback_action("pause")
        app.handle_playback_action("next")
        app.handle_playback_action("search")
        app.switching = False
        assert app.player.commands == commands and isinstance(app.screen, PlaybackScreen)


async def test_live_queue_reorders_clears_and_tracks_current_song_without_closing():
    """Keyboard editing changes only manual items and updates the live current-song label."""
    app = MusicApp(FeatureLibrary(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#songs", DataTable).move_cursor(row=1)
        await pilot.press("a")
        await app.load_view(Playlist(3, "另一个歌单")).wait()
        await pilot.press("a", "f3")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PlaybackScreen)
        assert screen.query_one("#playback-tabs", TabbedContent).active == "queue"
        table = screen.query_one("#queue-items", DataTable)
        table.move_cursor(row=1)
        await pilot.press("ctrl+up")
        assert [entry.track for entry in app.playback_queue.view(app.repeat_mode).pending] == [
            THIRD,
            SECOND,
        ]
        assert table.cursor_row == 0
        await pilot.press("ctrl+down")
        assert [entry.track for entry in app.playback_queue.view(app.repeat_mode).pending] == [
            SECOND,
            THIRD,
        ]
        assert table.cursor_row == 1
        await pilot.press("delete")
        assert [entry.track for entry in app.playback_queue.view(app.repeat_mode).pending] == [
            SECOND
        ]
        await pilot.press("n")
        await pilot.pause()
        screen.query_one(QueuePanel).refresh_queue()
        assert app.current == SECOND
        assert "第二首" in str(screen.query_one("#queue-current", Static).render())
        assert app.screen is screen and not app.playback_queue.view(app.repeat_mode).pending
        await pilot.press("ctrl+delete")
        assert (
            app.current == SECOND and app.playback_queue.view(app.repeat_mode).source == "original"
        )


async def test_recent_playback_records_replay_and_reopens_origin_without_audio_change(tmp_path):
    """Replay a history song as one track and reopen its origin without replacing that audio."""
    store = SessionStore(tmp_path)
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter", "n")
        await pilot.pause()
        records = store.history("qq", "account-1")
        assert [record["track"]["mid"] for record in records] == ["b", "a"]
        await pilot.press("f8")
        await pilot.pause()
        app.screen.query_one(ListView).index = 1
        await pilot.press("enter")
        await pilot.pause()
        assert (
            app.current == FIRST
            and app.playback_queue.view(app.repeat_mode).tracks == (FIRST,)
            and app.playback_queue.view(app.repeat_mode).source is None
        )
        assert app.playback_origin.source == "original"
        assert [record["track"]["mid"] for record in store.history("qq", "account-1")] == ["a", "b"]
        before = app.player.loaded.copy()
        await pilot.press("f8", "tab", "enter")
        await pilot.pause()
        assert app.source == "original" and app.player.loaded == before
        await pilot.press("f7", "g")
        await pilot.pause()
        assert not isinstance(app.screen, PlaybackScreen)
        assert app.query_one("#songs", DataTable).cursor_row == 0


async def test_recent_single_track_origin_survives_loop_and_session_resume(tmp_path):
    """Preserve navigation to the original playlist across replay and saved single-song queues."""
    store = SessionStore(tmp_path)
    record = {"track": asdict(THIRD), "source": {"id": 3, "title": "来源歌单"}, "page": 1}
    first = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with first.run_test() as pilot:
        await pilot.pause()
        await first.play_recent(record).wait()
        first.repeat_mode = RepeatMode.SINGLE
        first.player.events.put_nowait({"event": "end-file", "reason": "eof"})
        first.update_player()
        await pilot.pause()
        assert first.playback_origin.source == Playlist(3, "来源歌单")
        first.player.properties["time-pos"] = 12
        first.save_state()
    restored = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with restored.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert restored.current == THIRD and restored.player.load_options[-1] == (12, False)
        assert restored.playback_origin.source == Playlist(3, "来源歌单")
        await pilot.press("g")
        await pilot.pause()
        assert restored.source == Playlist(3, "来源歌单")


async def test_history_stores_filter_origin_without_full_matching_snapshot(tmp_path):
    """Store a small history origin while sessions retain the complete filtered queue."""
    store = SessionStore(tmp_path)
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    source = PlaylistSearch(Playlist(3, "过滤来源"), "歌", (FIRST, SECOND, THIRD), 400)
    async with app.run_test() as pilot:
        await app.load_view(source).wait()
        await pilot.press("enter")
        await pilot.pause()
        record = store.history("qq", "account-1")[0]
        assert record["source"]["id"] == 3 and "tracks" not in record["source"]
        assert record["page"] == 1
        app.save_state()
        assert store.load_session("qq", "account-1")["source"]["tracks"]


async def test_unprepared_shuffle_does_not_claim_sequential_next_on_playback_page():
    """Show a random-selection hint until a true random order is prepared."""
    app = MusicApp(FeatureLibrary(), FeatureAudio())
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.repeat_mode = RepeatMode.RANDOM
        state = app.get_playback_state()
        assert state.next_track is None and "随机顺序" in state.next_hint


async def test_failed_player_buttons_use_displayed_song_not_hidden_browsing_selection():
    """Fetch quality and lyrics for the failed song shown in the player page."""

    class TrackingLibrary(FeatureLibrary):
        def __init__(self):
            """Record metadata requests without using a real account or music endpoint."""
            super().__init__()
            self.lyric_targets = []
            self.quality_targets = []

        async def lyrics(self, track):
            """Record the exact song requested by the player lyrics control."""
            self.lyric_targets.append(track)
            return await super().lyrics(track)

        async def available_qualities(self, track):
            """Record the exact song requested by the player quality control."""
            self.quality_targets.append(track)
            return await super().available_qualities(track)

    library = TrackingLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        await app.load_view(Playlist(3, "隐藏浏览页")).wait()
        app.player.events.put_nowait({"event": "end-file", "reason": "error"})
        app.update_player()
        await pilot.press("f7", "f4")
        await pilot.pause()
        assert library.lyric_targets == [FIRST]
        assert isinstance(app.screen, PlaybackScreen)
        assert app.screen.query_one("#playback-tabs", TabbedContent).active == "lyrics"
        await pilot.press("f4", "u")
        await pilot.pause()
        assert library.quality_targets == [FIRST]
        assert isinstance(app.screen, QualityScreen)


async def test_recent_queue_origin_survives_manual_insertion_and_restart(tmp_path):
    """Return from a manual song to the recent single song's original source after F6."""
    store = SessionStore(tmp_path)
    record = {"track": asdict(FIRST), "source": "original", "page": 1}
    first = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with first.run_test() as pilot:
        await pilot.pause()
        await first.play_recent(record).wait()
        await first.load_view(Playlist(3, "插入来源")).wait()
        await pilot.press("a", "n")
        await pilot.pause()
        assert first.current == THIRD
        first.save_state()
    second = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with second.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert second.current == THIRD
        await pilot.press("p", "g")
        await pilot.pause()
        assert second.current == FIRST and second.source == "original"
        assert second.playback_origin.source == "original"
        assert store.history("qq", "account-1")[0]["source"] == "original"
