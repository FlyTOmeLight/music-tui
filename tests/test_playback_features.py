"""Exercise manual queues, explicit recovery and library shortcuts through the TUI."""

import asyncio

import pytest
from test_app import FIRST, SECOND, THIRD, Audio, Library
from textual.widgets import DataTable, Input, ListView, Static

from music_tui.app import MusicApp, RepeatMode
from music_tui.models import LyricLine, Lyrics, Playlist
from music_tui.screens import HelpScreen, LyricsScreen, PlaylistPickerScreen, QueueScreen


class FeatureAudio(Audio):
    """Model position updates and player restarts as observable playback behavior."""

    def __init__(self):
        """Track lifecycle, position and one-shot decoder failures."""
        super().__init__()
        self.started = 0
        self.closed = 0
        self.fail_load = False

    async def start(self):
        """Start with a fresh position, as a replacement mpv process would."""
        self.started += 1
        self.properties.update({"time-pos": 0, "pause": False, "volume": 70})

    async def close(self):
        """Record process cleanup and publish its disconnect event."""
        self.closed += 1
        self.events.put_nowait({"event": "disconnected"})

    async def command(self, *args):
        """Apply volume commands and the absence of progress after stopping audio."""
        await super().command(*args)
        if args == ("stop",):
            self.properties["time-pos"] = None
        elif args[:2] == ("set_property", "volume"):
            self.properties["volume"] = args[2]

    async def load(self, url, *, start=0, paused=False):
        """Accept fresh URLs or simulate one failed decoder load."""
        if self.fail_load:
            self.fail_load = False
            raise RuntimeError("音频连接失败")
        await super().load(url, start=start, paused=paused)
        self.properties.update({"time-pos": start, "pause": paused})


class FeatureLibrary(Library):
    """Provide an account with observable lyrics and playlist-write operations."""

    account_id = "account-1"

    def __init__(self):
        """Record writes and offer one editable playlist and one read-only playlist."""
        super().__init__()
        self.liked = False
        self.writes = []
        self.fail_write = False
        self.editable = Playlist(1, "可写歌单", editable=True)

    async def restore(self):
        """Restore a signed-in account for library actions."""
        return True

    async def playlists(self):
        """Return writable and collected destinations."""
        return [self.editable, Playlist(2, "收藏歌单")]

    async def lyrics(self, track):
        """Provide deterministic timed lyrics for the selected song."""
        return Lyrics((LyricLine(0, "第一句"), LyricLine(5, "第二句")))

    async def is_liked(self, track):
        """Return the account's current liked state."""
        return self.liked

    async def set_liked(self, track, liked):
        """Update liked state only after a successful simulated server write."""
        if self.fail_write:
            raise ValueError("账号权限不足")
        self.liked = liked
        self.writes.append(("like", track, liked))

    async def add_to_playlist(self, track, playlist):
        """Record a successful selected-song write to the chosen destination."""
        if self.fail_write:
            raise ValueError("账号权限不足")
        self.writes.append(("add", track, playlist))


@pytest.mark.parametrize("mode", list(RepeatMode))
async def test_manual_next_tracks_preserve_original_queue_and_browsing(mode):
    """Play inserted songs first, then continue the original sequence or shuffle."""
    app = MusicApp(Library(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.repeat_mode = mode
        app.reset_shuffle(app.index)
        await app.load_view(Playlist(3, "其他歌单")).wait()
        await pilot.press("a", "a", "n")
        await pilot.pause()
        assert app.current == THIRD
        assert app.queue_source == "original" and app.index == 0
        assert app.source == Playlist(3, "其他歌单")
        await pilot.press("n")
        await pilot.pause()
        assert app.player.loaded == ["a", "c", "c"]
        await pilot.press("n")
        await pilot.pause()
        assert app.current in ({SECOND, THIRD} if mode == RepeatMode.RANDOM else {SECOND})
        assert app.manual_current is None and not app.pending_tracks
        assert app.queue_source == "original"


async def test_single_repeat_replays_manual_song_then_manual_next_returns_to_source():
    """Repeat an inserted song without advancing the original list cursor."""
    app = MusicApp(Library(), FeatureAudio())
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.repeat_mode = RepeatMode.SINGLE
        await app.load_view(Playlist(3, "其他歌单")).wait()
        await pilot.press("a", "n")
        await pilot.pause()
        app.player.events.put_nowait({"event": "end-file", "reason": "eof"})
        app.update_player()
        await pilot.pause()
        assert app.player.loaded == ["a", "c", "c"]
        await pilot.press("n")
        await pilot.pause()
        assert app.current == SECOND


async def test_queue_delete_and_locate_preserve_audio_and_original_source():
    """Delete a staged track and locate both base and manual songs without restarting audio."""
    app = MusicApp(Library(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        await app.load_view(Playlist(3, "其他歌单")).wait()
        await pilot.press("a", "a", "f3")
        assert isinstance(app.screen, QueueScreen)
        await pilot.press("delete")
        await pilot.pause()
        assert len(app.pending_tracks) == 1 and isinstance(app.screen, QueueScreen)
        await pilot.press("escape", "g")
        await pilot.pause()
        assert app.source == "original"
        assert app.query_one("#songs", DataTable).cursor_row == 0
        assert app.player.loaded == ["a"]
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        assert app.source == Playlist(3, "其他歌单") and app.current == THIRD
        await pilot.press("p")
        await pilot.pause()
        assert app.current == FIRST


@pytest.mark.parametrize("failure", ["decoder", "disconnected"])
async def test_retry_restores_failed_song_after_browsing_elsewhere(failure):
    """Recover the original song and its checkpoint without selecting from the visible table."""
    player = FeatureAudio()
    app = MusicApp(Library(), player)
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        player.properties.update({"time-pos": 23.5, "pause": True, "volume": 31})
        app.update_player()
        await app.load_view(Playlist(3, "其他歌单")).wait()
        player.properties["time-pos"] = None
        player.events.put_nowait(
            {"event": "end-file", "reason": "error"}
            if failure == "decoder"
            else {"event": "disconnected"}
        )
        app.update_player()
        assert app.current is None
        assert "F5" in str(app.query_one("#status", Static).render())
        await pilot.press("f5")
        await pilot.pause()
        assert app.current == FIRST and app.queue_source == "original"
        assert player.load_options[-1] == (23.5, True)
        assert app.source == Playlist(3, "其他歌单")
        if failure == "disconnected":
            assert player.closed == 1 and player.started == 2
            assert player.properties["volume"] == 31


async def test_retry_manual_failure_does_not_advance_base_queue():
    """Retain a failed manual item until explicit retry succeeds or next skips it."""
    player = FeatureAudio()
    app = MusicApp(Library(), player)
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        await app.load_view(Playlist(3, "其他歌单")).wait()
        await pilot.press("a")
        player.fail_load = True
        await pilot.press("n")
        await pilot.pause()
        assert app.current is None and app.manual_current.track == THIRD
        await pilot.press("f5")
        await pilot.pause()
        assert app.current == THIRD and app.index == 0
        await pilot.press("n")
        await pilot.pause()
        assert app.current == SECOND


async def test_retry_failed_next_page_repeats_navigation_without_replacing_visible_page():
    """Preserve audio on failed pagination and retry the requested queue transition."""

    class MissingPage(Library):
        fail = True

        async def search(self, keyword, page):
            """Reject page two until the network recovers."""
            if page == 2 and self.fail:
                raise ValueError("分页请求失败")
            return await super().search(keyword, page)

    library = MissingPage()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        app.query_one("#songs", DataTable).move_cursor(row=1)
        await pilot.press("enter", "n")
        await pilot.pause()
        assert app.current == SECOND and app.queue_page == 1
        library.fail = False
        await pilot.press("f5")
        await pilot.pause()
        assert app.current == THIRD and app.queue_page == 2
        assert app.page == 1


async def test_help_and_new_letter_shortcuts_leave_search_input_usable():
    """Function keys open dialogs while new letter shortcuts remain ordinary input."""
    app = MusicApp(Library(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("/", "a", "s", "w", "g", "f1")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert app.query_one("#search", Input).value == "aswg"
        assert app.query_one("#search", Input).has_focus
        assert not app.pending_tracks


async def test_lyrics_follow_current_song_and_close_on_track_change():
    """Show live-position lyrics and remove them when another song starts."""
    app = MusicApp(FeatureLibrary(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter", "f4")
        await pilot.pause()
        assert isinstance(app.screen, LyricsScreen)
        app.player.properties["time-pos"] = 6
        app.screen.sync_position()
        assert app.screen.active_line == 1
        await app.play_index(1).wait()
        assert not isinstance(app.screen, LyricsScreen)
        assert app.current == SECOND


@pytest.mark.parametrize("failure", [False, True])
async def test_library_actions_use_selected_song_and_report_server_outcome(failure):
    """Like and add the highlighted song without altering the active audio queue."""
    library = FeatureLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.query_one("#songs", DataTable).move_cursor(row=1)
        library.fail_write = failure
        await pilot.press("s")
        await pilot.pause()
        assert library.liked is not failure
        assert app.current == FIRST
        if failure:
            assert "权限不足" in str(app.query_one("#status", Static).render())
        else:
            assert library.writes == [("like", SECOND, True)]
        await pilot.press("w")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        assert len(app.screen.query_one(ListView).children) == 1
        await pilot.press("enter")
        await pilot.pause()
        assert app.current == FIRST
        if not failure:
            assert library.writes[-1] == ("add", SECOND, library.editable)


async def test_stale_lyrics_request_cannot_open_over_a_new_song():
    """Discard lyrics that return after the playback target has changed."""
    started, finish = asyncio.Event(), asyncio.Event()

    class SlowLyrics(FeatureLibrary):
        async def lyrics(self, track):
            """Wait for a later song to begin before returning the old lyrics."""
            started.set()
            await finish.wait()
            return await super().lyrics(track)

    app = MusicApp(SlowLyrics(), FeatureAudio())
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        worker = app.action_lyrics()
        async with asyncio.timeout(2):
            await started.wait()
        await app.play_index(1).wait()
        finish.set()
        await worker.wait()
        assert not isinstance(app.screen, LyricsScreen)
        assert app.current == SECOND


async def test_replaced_queue_cancels_old_random_queue_preparation():
    """An old queue dialog cannot install its pages into a newly selected playback list."""
    fetching, finish = asyncio.Event(), asyncio.Event()

    class SlowPage(Library):
        async def search(self, keyword, page):
            """Delay the second random-source page while another playlist is selected."""
            if page == 2:
                fetching.set()
                await finish.wait()
            return await super().search(keyword, page)

    app = MusicApp(SlowPage(), FeatureAudio())
    async with app.run_test() as pilot:
        await app.load_view("old source").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.repeat_mode = RepeatMode.RANDOM
        app.action_queue()
        async with asyncio.timeout(2):
            await fetching.wait()
        new_source = Playlist(3, "新播放来源")
        await app.load_view(new_source).wait()
        await pilot.press("enter")
        finish.set()
        await pilot.pause()
        assert app.current == THIRD and app.queue_source == new_source
        assert not app.shuffle_pages
        assert not isinstance(app.screen, QueueScreen)
