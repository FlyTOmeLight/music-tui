"""Verify automatic per-song quality selection without changing the saved preference."""

import asyncio

import pytest
from test_app import FIRST, SECOND
from test_playback_features import FeatureAudio, FeatureLibrary
from textual.widgets import Static

from music_tui.app import MusicApp
from music_tui.models import Quality, QualityUnavailableError
from music_tui.playback_session import QueuedTrack
from music_tui.session import SessionStore


class TierLibrary(FeatureLibrary):
    """Expose requested tiers and authorize a chosen exact quality for each song."""

    def __init__(self, allowed):
        """Track per-song requested tiers independently of library and login actions."""
        super().__init__()
        self.allowed = allowed
        self.requests = []

    async def stream_url(self, track, quality):
        """Authorize exact full audio or report that this song's tier is unavailable."""
        self.requests.append((track, quality))
        if quality not in self.allowed.get(track, []):
            raise QualityUnavailableError(f"此歌曲的{quality.label}不可用")
        return f"{track.mid}/{quality.name}"


async def test_play_falls_through_tiers_and_next_song_restarts_from_preference(tmp_path):
    """Play the first available lower tier while retaining the preferred tier for the next song."""
    music = TierLibrary({FIRST: [Quality.LOSSLESS], SECOND: [Quality.MASTER]})
    player, store = FeatureAudio(), SessionStore(tmp_path)
    app = MusicApp(music, player, session_store=store)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.quality = Quality.MASTER
        app.playback_queue.replace([FIRST, SECOND], None, 1, False, 0)
        await app.play_index(0).wait()
        assert music.requests == [(FIRST, quality) for quality in list(Quality)[:5]]
        assert player.loaded == ["a/LOSSLESS"]
        assert app.current == FIRST and app.current_quality == Quality.LOSSLESS
        assert app.quality == Quality.MASTER
        assert "已自动切换到 SQ 无损" in app.last_status_message
        app.save_state()
        assert store.load_preferences("qq")["quality"] == "MASTER"
        await app.play_index(1).wait()
        assert music.requests[-1] == (SECOND, Quality.MASTER)
        assert player.loaded[-1] == "b/MASTER" and app.current_quality == Quality.MASTER
        assert "已自动切换" not in app.last_status_message


@pytest.mark.parametrize("manual", [False, True])
async def test_quality_change_and_retry_use_lower_tiers_preserving_progress_and_pause(manual):
    """Apply fallback to queue and manual playback reloads without losing position or pause."""
    music = TierLibrary({FIRST: [Quality.STANDARD]})
    player = FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        if manual:
            app.playback_queue.enqueue(QueuedTrack(FIRST, "original", 1))
            await app.play_move(1).wait()
        else:
            app.playback_queue.replace([FIRST], None, 1, False, 0)
            await app.play_index(0).wait()
        player.properties.update({"time-pos": 23.5, "pause": True})
        command_count = len(player.commands)
        music.requests.clear()
        app.choose_quality(Quality.LOSSLESS, FIRST)
        await pilot.pause()
        assert music.requests == [
            (FIRST, Quality.LOSSLESS),
            (FIRST, Quality.HIGH),
            (FIRST, Quality.STANDARD),
        ]
        assert app.quality == Quality.LOSSLESS and app.current_quality == Quality.STANDARD
        assert player.load_options[-1] == (23.5, True)
        assert len(player.commands) == command_count
        music.requests.clear()
        await app.action_retry().wait()
        assert music.requests[0] == (FIRST, Quality.LOSSLESS)
        assert music.requests[-1] == (FIRST, Quality.STANDARD)
        assert player.load_options[-1] == (23.5, True)


@pytest.mark.parametrize("manual", [False, True])
async def test_saved_session_recovery_uses_preference_and_records_actual_tier(tmp_path, manual):
    """Resolve fresh lower-tier audio when restoring a paused account queue or manual song."""
    store = SessionStore(tmp_path)
    first = MusicApp(TierLibrary({FIRST: [Quality.HIGH]}), FeatureAudio(), session_store=store)
    async with first.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        first.quality = Quality.HIRES
        if manual:
            first.playback_queue.enqueue(QueuedTrack(FIRST, "original", 1))
            await first.play_move(1).wait()
        else:
            first.playback_queue.replace([FIRST], None, 1, False, 0)
            await first.play_index(0).wait()
        first.player.properties.update({"time-pos": 15, "pause": True})
    music = TierLibrary({FIRST: [Quality.STANDARD]})
    player = FeatureAudio()
    restored = MusicApp(music, player, session_store=store)
    async with restored.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await restored.action_resume_session().wait()
        assert music.requests == [
            (FIRST, Quality.HIRES),
            (FIRST, Quality.LOSSLESS),
            (FIRST, Quality.HIGH),
            (FIRST, Quality.STANDARD),
        ]
        assert restored.quality == Quality.HIRES and restored.current_quality == Quality.STANDARD
        assert player.load_options[-1] == (15, True)


async def test_all_unavailable_tiers_stop_at_standard_with_a_clear_error():
    """Stop after every permitted tier is unavailable, retaining explicit retry controls."""
    music, player = TierLibrary({}), FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        app.quality = Quality.MASTER
        await app.play_index(0).wait()
        assert music.requests == [(FIRST, quality) for quality in Quality]
        assert not player.loaded and app.current is None and app.current_quality is None
        assert "已尝试至标准音质" in str(app.query_one("#status", Static).render())
        assert "F5 重试" in app.last_status_message


async def test_exhausted_tiers_preserve_song_and_standard_tier_reason():
    """Explain the final source failure without replacing it with a membership guess."""

    class UnavailableLibrary(TierLibrary):
        async def stream_url(self, track, quality):
            """Return distinct high-tier and standard-tier unavailability reasons."""
            self.requests.append((track, quality))
            reason = "接口未返回播放地址" if quality == Quality.STANDARD else "该档音质无可用文件"
            raise QualityUnavailableError(reason)

    app = MusicApp(UnavailableLibrary({}), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        app.quality = Quality.MASTER
        await app.play_index(0).wait()
        assert FIRST.title in app.last_status_message
        assert "标准音质" in app.last_status_message
        assert "接口未返回播放地址" in app.last_status_message
        assert "会员" not in app.last_status_message
        assert "F5 重试" in app.last_status_message and "N 下一首" in app.last_status_message


@pytest.mark.parametrize("manual", [False, True])
async def test_loading_and_failed_song_identity_stays_visible_until_explicit_retry(manual):
    """Keep the requested song visible during resolution and failure without skipping it."""
    started, release = asyncio.Event(), asyncio.Event()

    class DelayedLibrary(TierLibrary):
        async def stream_url(self, track, quality):
            """Hold resolution until the test can observe the loading song summary."""
            started.set()
            await release.wait()
            return await super().stream_url(track, quality)

    music, player = DelayedLibrary({}), FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.quality = Quality.MASTER
        app.playback_queue.replace([FIRST, SECOND], None, 1, False, 0)
        if manual:
            app.playback_queue.enqueue(QueuedTrack(FIRST, "original", 1))
        worker = app.play_move(1) if manual else app.play_index(0)
        await asyncio.wait_for(started.wait(), 1)
        app.update_player()
        title = str(app.query_one("#now-playing", Static).render())
        detail = str(app.query_one("#playback-detail", Static).render())
        assert FIRST.title in title and FIRST.artist in title
        assert "正在获取" in detail and "Enter" not in detail
        release.set()
        await worker.wait()
        app.update_player()
        title = str(app.query_one("#now-playing", Static).render())
        detail = str(app.query_one("#playback-detail", Static).render())
        assert FIRST.title in title and FIRST.artist in title
        assert "F5 重试" in detail and "N 下一首" in detail
        assert "尚未播放" not in title and "Enter" not in detail
        assert app.playback_track == FIRST and app.current is None
        assert all(track == FIRST for track, _ in music.requests)
        music.allowed[FIRST] = [Quality.STANDARD]
        await app.action_retry().wait()
        assert app.current == FIRST and player.loaded == ["a/STANDARD"]
        assert app.quality == Quality.MASTER


@pytest.mark.parametrize(
    "error",
    [
        ValueError("登录已失效"),
        ValueError("API 请求失败"),
        TimeoutError(),
        RuntimeError("网络错误"),
    ],
)
async def test_non_quality_errors_do_not_retry_lower_tiers(error):
    """Surface authentication, API, and network failures after exactly one stream request."""

    class ErrorLibrary(TierLibrary):
        async def stream_url(self, track, quality):
            """Raise an ordinary request failure without labeling it a tier restriction."""
            self.requests.append((track, quality))
            raise error

    music, player = ErrorLibrary({}), FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        app.quality = Quality.LOSSLESS
        await app.play_index(0).wait()
        assert music.requests == [(FIRST, Quality.LOSSLESS)]
        assert not player.loaded and app.current is None


async def test_player_load_error_does_not_resolve_another_tier():
    """Keep decoder failures distinct from unavailable stream tiers."""
    music = TierLibrary({FIRST: [Quality.LOSSLESS, Quality.HIGH]})
    player = FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        app.quality = Quality.LOSSLESS
        player.fail_load = True
        await app.play_index(0).wait()
        assert music.requests == [(FIRST, Quality.LOSSLESS)]
        assert "音频连接失败" in app.last_status_message
        assert app.current is None


async def test_fallback_does_not_try_tiers_above_the_preference():
    """Start with the chosen tier and continue downward, without upgrading unexpectedly."""
    music, player = TierLibrary({FIRST: [Quality.MASTER, Quality.STANDARD]}), FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        app.quality = Quality.HIGH
        await app.play_index(0).wait()
        assert music.requests == [(FIRST, Quality.HIGH), (FIRST, Quality.STANDARD)]
        assert player.loaded == ["a/STANDARD"]


async def test_reselecting_preferred_tier_retries_a_song_playing_at_a_lower_tier():
    """Retry the preferred tier when it becomes available after a per-song downgrade."""
    music, player = TierLibrary({FIRST: [Quality.STANDARD]}), FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        app.quality = Quality.LOSSLESS
        await app.play_index(0).wait()
        player.properties.update({"time-pos": 17, "pause": True})
        music.allowed[FIRST] = [Quality.LOSSLESS]
        app.choose_quality(Quality.LOSSLESS, FIRST)
        await pilot.pause()
        assert player.loaded[-1] == "a/LOSSLESS"
        assert player.load_options[-1] == (17, True)
        assert app.current_quality == Quality.LOSSLESS


async def test_exhausted_quality_reload_keeps_existing_audio_and_actual_tier():
    """Keep currently playing audio and its actual tier when every replacement tier fails."""
    music, player = TierLibrary({FIRST: [Quality.STANDARD]}), FeatureAudio()
    app = MusicApp(music, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.playback_queue.replace([FIRST], None, 1, False, 0)
        await app.play_index(0).wait()
        player.properties.update({"time-pos": 27, "pause": True})
        commands_before = list(player.commands)
        music.allowed.clear()
        app.choose_quality(Quality.LOSSLESS, FIRST)
        await pilot.pause()
        assert app.current == FIRST and app.current_quality == Quality.STANDARD
        assert app.quality == Quality.LOSSLESS
        assert player.loaded == ["a/STANDARD"]
        assert player.commands == commands_before
        assert player.properties["time-pos"] == 27 and player.properties["pause"]
        assert "已尝试至标准音质" in app.last_status_message
