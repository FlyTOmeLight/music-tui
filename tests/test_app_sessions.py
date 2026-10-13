"""Verify user-triggered session recovery and per-platform playback preferences."""

import asyncio

import pytest
from test_app import FIRST, SECOND, THIRD
from test_playback_features import FeatureAudio, FeatureLibrary
from textual.widgets import Static

from music_tui.app import MusicApp
from music_tui.models import Playlist, Quality
from music_tui.playback_session import PlaylistSearch, RepeatMode
from music_tui.session import SessionStore


@pytest.mark.parametrize("manual", [False, True])
async def test_restart_recovers_queue_progress_and_manual_pending_without_autoplay(
    tmp_path, manual
):
    """Restore the saved account only on F6 and resume its song at the checkpoint."""
    store = SessionStore(tmp_path)
    first = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with first.run_test() as pilot:
        await first.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        first.quality = Quality.LOSSLESS
        first.repeat_mode = RepeatMode.LIST
        await first.load_view(Playlist(3, "其他歌单")).wait()
        await pilot.press("a", "a")
        if manual:
            await pilot.press("n")
        await pilot.pause()
        first.player.properties.update({"time-pos": 18.25, "pause": True, "volume": 29})
        first.update_player()
    snapshot = store.load_session("qq", "account-1")
    assert snapshot["position"] == 18.25
    assert "url" not in snapshot and "credential" not in snapshot

    player = FeatureAudio()
    second = MusicApp(FeatureLibrary(), player, session_store=store)
    async with second.run_test() as pilot:
        await pilot.pause()
        assert not player.loaded and second.current is None
        assert second.quality == Quality.LOSSLESS and second.repeat_mode == RepeatMode.LIST
        assert player.properties["volume"] == 29
        await pilot.press("f6")
        await pilot.pause()
        assert second.current == (THIRD if manual else FIRST)
        assert player.load_options[-1] == (18.25, True)
        assert (
            second.playback_queue.view(second.repeat_mode).source == "original"
            and second.playback_queue.view(second.repeat_mode).index == 0
        )
        assert len(second.playback_queue.view(second.repeat_mode).pending) == (1 if manual else 2)
        while second.playback_queue.view(second.repeat_mode).pending:
            await pilot.press("n")
            await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert second.current == SECOND


async def test_restored_filter_queue_and_shuffle_keep_their_saved_order(tmp_path):
    """Persist filtered metadata, shuffle history and remaining positions across restarts."""
    store = SessionStore(tmp_path)
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    source = PlaylistSearch(Playlist(5, "过滤歌单"), "歌", (FIRST, SECOND, THIRD), 10)
    async with app.run_test() as pilot:
        await app.load_view(source).wait()
        await pilot.press("enter")
        await pilot.pause()
        app.repeat_mode = RepeatMode.RANDOM
        await pilot.press("n")
        await pilot.pause()
        app.player.properties["time-pos"] = 12
        app.update_player()
        current = app.current
        history = app.playback_queue.snapshot(app.current, 0, False, None).shuffle_history
        remaining = app.playback_queue.snapshot(app.current, 0, False, None).shuffle_remaining

    restored = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with restored.run_test() as pilot:
        await pilot.press("f6")
        await pilot.pause()
        assert restored.current == current
        assert (
            restored.playback_queue.snapshot(restored.current, 0, False, None).shuffle_history
            == history
            and restored.playback_queue.snapshot(restored.current, 0, False, None).shuffle_remaining
            == remaining
        )
        assert restored.playback_queue.view(restored.repeat_mode).source == source
        await pilot.press("p")
        await pilot.pause()
        assert restored.current == FIRST
        await pilot.press("n")
        await pilot.pause()
        assert restored.current == current


async def test_other_account_cannot_resume_previous_accounts_session(tmp_path):
    """Keep recovery isolated from another user's saved playlist and queue."""
    store = SessionStore(tmp_path)
    first = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with first.run_test() as pilot:
        await first.load_view("private query").wait()
        await pilot.press("enter")
        await pilot.pause()

    library = FeatureLibrary()
    library.account_id = "account-2"
    second = MusicApp(library, FeatureAudio(), session_store=store)
    async with second.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f6")
        await pilot.pause()
        assert not second.player.loaded
        assert "没有已保存" in str(second.query_one("#status", Static).render())


async def test_sequential_session_resumes_after_crossing_a_source_page(tmp_path):
    """Recover a later sequential page even when unused shuffle history names page one."""
    store = SessionStore(tmp_path)
    first = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with first.run_test() as pilot:
        await first.load_view("original").wait()
        await pilot.press("enter", "n", "n")
        await first.workers.wait_for_complete()
        assert first.current == THIRD and first.playback_queue.view(first.repeat_mode).page == 2
        first.player.properties["time-pos"] = 17
        first.save_state()

    restored = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with restored.run_test() as pilot:
        await restored.workers.wait_for_complete()
        await pilot.press("f6")
        await restored.workers.wait_for_complete()
        assert (
            restored.current == THIRD
            and restored.playback_queue.view(restored.repeat_mode).page == 2
        )
        assert restored.player.load_options[-1] == (17, False)


async def test_invalid_snapshot_does_not_stop_existing_audio(tmp_path):
    """Report a malformed session before changing the running queue or player."""
    store = SessionStore(tmp_path)
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        store.save_session("qq", "account-1", {"queue": []})
        before = app.player.commands.copy()
        await pilot.press("f6")
        await pilot.pause()
        assert app.current == FIRST and app.player.commands == before
        assert "播放会话无效" in str(app.query_one("#status", Static).render())


async def test_platform_switch_saves_session_and_restores_each_platforms_preferences(tmp_path):
    """Keep platform preferences while requiring F6 to restore the previous queue."""
    store = SessionStore(tmp_path)
    original = FeatureLibrary()

    async def close():
        """Release an offline source without account network calls."""
        pass

    original.close = close

    def factory(key):
        """Construct an offline account for the newly selected platform."""
        library = FeatureLibrary()
        library.close = close
        return library

    app = MusicApp(original, FeatureAudio(), source_factory=factory, session_store=store)
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.quality = Quality.LOSSLESS
        app.repeat_mode = RepeatMode.LIST
        app.player.properties.update({"time-pos": 19, "volume": 32})
        await app.switch_platform("netease-enhanced").wait()
        assert app.quality == Quality.STANDARD and app.repeat_mode == RepeatMode.SEQUENCE
        assert app.current is None and not app.playback_queue.view(app.repeat_mode).tracks
        app.quality = Quality.HIGH
        app.player.properties["volume"] = 48
        await app.switch_platform("qq").wait()
        assert app.quality == Quality.LOSSLESS and app.repeat_mode == RepeatMode.LIST
        assert app.player.properties["volume"] == 32
        await pilot.press("f6")
        await pilot.pause()
        assert app.current == FIRST and app.player.load_options[-1] == (19, False)
        assert store.load_preferences("netease-enhanced")["volume"] == 48


async def test_relogin_as_another_account_keeps_saved_queue_isolated(tmp_path):
    """Save the old account before clearing its audio and browsing on account replacement."""
    store = SessionStore(tmp_path)
    library = FeatureLibrary()
    app = MusicApp(library, FeatureAudio(), session_store=store)
    async with app.run_test() as pilot:
        await app.load_view("private query").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.player.properties["time-pos"] = 21
        library.account_id = "account-2"
        await app.logged_in(True).wait()
        await pilot.pause()
        assert app.current is None and not app.playback_queue.view(app.repeat_mode).tracks
        assert app.active_account_id == "account-2"
        assert store.load_session("qq", "account-1")["position"] == 21
        assert store.load_session("qq", "account-2") is None
        await pilot.press("f6")
        await pilot.pause()
        assert app.current is None


async def test_manual_transition_keeps_a_consistent_saved_checkpoint(tmp_path):
    """Do not drop pending songs at EOF or save mismatched metadata during mpv stop."""
    stopping, finish = asyncio.Event(), asyncio.Event()

    class SlowStop(FeatureAudio):
        blocked = False

        async def command(self, *args):
            """Pause a manual transition while the last complete snapshot remains readable."""
            if self.blocked and args == ("stop",):
                stopping.set()
                await finish.wait()
            return await super().command(*args)

    store = SessionStore(tmp_path)
    player = SlowStop()
    app = MusicApp(FeatureLibrary(), player, session_store=store)
    async with app.run_test() as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        await app.load_view(Playlist(3, "其他歌单")).wait()
        await pilot.press("a", "a")
        app.save_state()
        player.blocked = True
        app.session_saved_at = 0
        player.events.put_nowait({"event": "end-file", "reason": "eof"})
        app.update_player()
        snapshot = store.load_session("qq", "account-1")
        assert snapshot["track"]["mid"] == "a" and len(snapshot["pending"]) == 2
        async with asyncio.timeout(2):
            await stopping.wait()
        app.save_state()
        assert store.load_session("qq", "account-1") == snapshot
        finish.set()
        await pilot.pause()
        app.save_state()
        saved = store.load_session("qq", "account-1")
        assert saved["track"]["mid"] == saved["manual"]["track"]["mid"] == "c"
        assert len(saved["pending"]) == 1
