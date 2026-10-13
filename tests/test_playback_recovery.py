"""Exercise player failures that arrive before loading or selecting a song finishes."""

import asyncio

import pytest
from test_app import FIRST, SECOND, Library
from test_playback_features import FeatureAudio

from music_tui.app import MusicApp


@pytest.mark.parametrize("reason", ["error", "eof"])
async def test_current_end_event_survives_incomplete_load(reason):
    """Retain the current entry's end event until its load command has returned."""
    player = FeatureAudio()
    app = MusicApp(Library(), player)
    entered, release = asyncio.Event(), asyncio.Event()
    original_load = player.load

    async def delayed_load(url, **options):
        """Publish a decoder event before assigning the acknowledged entry identity."""
        await original_load(url, **options)
        if url == FIRST.mid:
            player.events.put_nowait(
                {"event": "end-file", "reason": reason, "playlist_entry_id": 1}
            )
            entered.set()
            await release.wait()
            player.entry_id = 1
        else:
            player.entry_id = 2

    player.load = delayed_load
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        async with asyncio.timeout(2):
            await entered.wait()
        app.update_player()
        assert app.current is None
        release.set()
        await app.workers.wait_for_complete()
        app.update_player()
        await pilot.pause()
        if reason == "error":
            assert app.current is None
            assert app.playback_track == FIRST
            assert "音频加载失败" in app.last_status_message
        else:
            assert app.current == SECOND


async def test_old_entry_end_event_during_load_does_not_end_new_song():
    """Ignore a replaced song's delayed error while retaining the new current song."""
    player = FeatureAudio()
    app = MusicApp(Library(), player)
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        app.loading = True
        player.events.put_nowait({"event": "end-file", "reason": "error", "playlist_entry_id": 10})
        app.update_player()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        player.entry_id = 11
        app.update_player()
        assert app.current == FIRST
        await pilot.pause()


@pytest.mark.parametrize("failure", ["startup", "disconnect"])
async def test_retry_recovers_player_without_a_selected_song(failure):
    """F5 can start a usable player before the first track has ever been selected."""
    player = FeatureAudio()
    original_start = player.start

    async def start():
        """Fail the first launch only when testing initial startup recovery."""
        await original_start()
        if failure == "startup" and player.started == 1:
            raise RuntimeError("mpv 启动失败")

    player.start = start
    app = MusicApp(Library(), player)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        if failure == "disconnect":
            player.events.put_nowait({"event": "disconnected"})
            app.update_player()
        assert not app.ready
        await pilot.press("f5")
        await app.workers.wait_for_complete()
        assert app.ready and player.started == 2
        assert app.playback_track is None
        await app.load_view("original").wait()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert app.current == FIRST
