"""Verify day/night appearance across pages, dialogs, and persisted preferences."""

import asyncio

import pytest
from test_app import FIRST, Library
from test_playback_features import FeatureAudio, FeatureLibrary
from textual.color import Color
from textual.widgets import DataTable, Input, Static

from music_tui.app import MusicApp
from music_tui.models import Track
from music_tui.playback_screen import PlaybackScreen
from music_tui.session import SessionStore


@pytest.mark.parametrize("theme", [None, "music-day"])
async def test_system_appearance_result_during_shutdown_does_not_update_removed_widgets(
    monkeypatch, theme
):
    """Finish delayed theme detection after widget removal without failing application shutdown."""
    started, release = asyncio.Event(), asyncio.Event()

    async def detect():
        """Return a native theme only after shutdown has removed the interface."""
        started.set()
        await release.wait()
        return theme

    monkeypatch.setattr("music_tui.app.detect_system_theme", detect)
    app = MusicApp(Library(), FeatureAudio())
    close_all = app._close_all
    worker = None

    async def close_widgets():
        """Expose the real shutdown window before the background workers are cancelled."""
        await close_all()
        release.set()
        await worker.wait()

    monkeypatch.setattr(app, "_close_all", close_widgets)
    async with app.run_test(size=(80, 24)):
        async with asyncio.timeout(2):
            await started.wait()
        worker = next(worker for worker in app.workers if worker.name == "update_system_theme")


async def test_delayed_system_appearance_failure_preserves_playback_error(monkeypatch):
    """Report unreadable system appearance in the heading without hiding a newer playback error."""
    started, release = asyncio.Event(), asyncio.Event()

    async def detect():
        """Return an unavailable desktop appearance after playback has already failed."""
        started.set()
        await release.wait()
        return None

    monkeypatch.setattr("music_tui.app.detect_system_theme", detect)
    app = MusicApp(Library(), FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        async with asyncio.timeout(2):
            await started.wait()
        app.queue = [Track("denied", "会员歌曲", "歌手", 200), FIRST]
        await app.play_index(0).wait()
        assert "暂无播放权限" in app.last_status_message
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "暂无播放权限" in str(app.query_one("#status", Static).render())
        assert "系统外观不可用" in str(app.query_one("#heading", Static).render())
        assert app.current is None and app.player.loaded == []


@pytest.mark.parametrize("theme", ["music-day", "music-night"])
async def test_theme_restores_and_switches_without_changing_playback(tmp_path, theme):
    """Restore global appearance and retain playback, selection, and search when toggling."""
    store = SessionStore(tmp_path)
    store.save_theme(theme)
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.theme == theme
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        table = app.query_one("#songs", DataTable)
        table.move_cursor(row=1)
        app.action_sort()
        selected = app.selected_track()
        queue = list(app.queue)
        loaded = list(app.player.loaded)
        await pilot.press("f9")
        await pilot.pause()
        assert app.theme != theme and store.theme() == app.theme
        assert app.current == FIRST and app.queue == queue and app.player.loaded == loaded
        assert app.selected_track() == selected
        assert str(table.get_cell("1", "artist").style) == app.get_css_variables()["text-muted"]
        await pilot.press("/", "f9")
        assert app.query_one("#search", Input).value == ""
        assert app.theme == theme
        await pilot.press("escape", "f7", "f4")
        await pilot.pause()
        page = app.screen
        assert isinstance(page, PlaybackScreen)
        before = page.styles.background
        await pilot.press("f9")
        await pilot.pause()
        assert app.screen is page and page.styles.background != before
        await pilot.press("f1")
        await pilot.pause()
        box = app.screen.query_one(".dialog-box")
        before = box.styles.background
        await pilot.press("f9")
        await pilot.pause()
        assert box.styles.background != before


def test_appearance_is_global_and_preserves_platform_settings(tmp_path):
    """Save the theme alongside platform preferences without overwriting either."""
    store = SessionStore(tmp_path)
    assert store.theme() == "system"
    store.save_preferences("qq", {"volume": 41})
    store.save_theme("music-day")
    store.save_preferences("netease-enhanced", {"volume": 62})
    store.set_last_source("netease-enhanced")
    restored = SessionStore(tmp_path)
    assert restored.theme() == "music-day"
    assert restored.load_preferences("qq") == {"volume": 41}
    assert restored.load_preferences("netease-enhanced") == {"volume": 62}


@pytest.mark.parametrize("theme", [None, "unknown", 7])
def test_invalid_appearance_does_not_replace_saved_preferences(tmp_path, theme):
    """Reject invalid appearance values before writing preferences."""
    store = SessionStore(tmp_path)
    store.save_theme("music-day")
    with pytest.raises(ValueError, match="主题无效"):
        store.save_theme(theme)
    assert store.theme() == "music-day"


async def test_day_mode_uses_readable_song_and_progress_colors(tmp_path):
    """Repaint Rich text content as well as widget surfaces when appearance changes."""
    store = SessionStore(tmp_path)
    store.save_theme("music-night")
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.player.properties.update({"time-pos": 20, "duration": 60})
        await pilot.press("f9")
        await pilot.pause()
        detail = app.query_one("#playback-detail", Static).render()
        assert app.theme == "music-day"
        assert detail.spans[0].style.foreground == Color.parse(app.get_css_variables()["accent"])
        assert app.get_css_variables()["text-muted"] == "#52637b"


def test_system_appearance_can_be_restored_after_a_manual_choice(tmp_path):
    """Persist the system preference without changing unrelated platform preferences."""
    store = SessionStore(tmp_path)
    store.save_theme("music-day")
    store.save_preferences("qq", {"volume": 52})
    store.save_theme("system")
    restored = SessionStore(tmp_path)
    assert restored.theme() == "system"
    assert restored.load_preferences("qq") == {"volume": 52}


@pytest.mark.parametrize("initial", ["music-day", "music-night"])
async def test_system_changes_manual_override_and_restore(tmp_path, monkeypatch, initial):
    """Follow system changes until a manual selection, then allow returning to the system."""
    current_system_theme = initial

    async def system_theme():
        """Return the current simulated operating-system appearance."""
        return current_system_theme

    monkeypatch.setattr("music_tui.app.detect_system_theme", system_theme)
    store = SessionStore(tmp_path)
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=store)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.appearance == "system"
        assert app.theme == initial
        current_system_theme = "music-day" if initial == "music-night" else "music-night"
        await app.update_system_theme().wait()
        assert app.theme == current_system_theme
        await pilot.press("f9")
        selected = app.theme
        assert app.appearance == selected and store.theme() == selected
        await app.update_system_theme().wait()
        assert app.theme == selected
        await pilot.press("shift+f9")
        await pilot.pause()
        assert app.appearance == "system" and store.theme() == "system"
        assert app.theme == current_system_theme


async def test_pending_system_detection_cannot_replace_manual_selection(tmp_path, monkeypatch):
    """Retain a user's manual choice made while native appearance detection was pending."""

    async def initial_theme():
        """Start in a deterministic light appearance."""
        return "music-day"

    monkeypatch.setattr("music_tui.app.detect_system_theme", initial_theme)
    app = MusicApp(FeatureLibrary(), FeatureAudio(), session_store=SessionStore(tmp_path))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        started, release = asyncio.Event(), asyncio.Event()

        async def pending_theme():
            """Hold a system result until the test has chosen a manual appearance."""
            started.set()
            await release.wait()
            return "music-day"

        monkeypatch.setattr("music_tui.app.detect_system_theme", pending_theme)
        task = asyncio.create_task(app.update_system_theme().wait())
        async with asyncio.timeout(2):
            await started.wait()
            await pilot.press("f9")
            assert app.appearance == "music-night"
            release.set()
            await task
        assert app.theme == "music-night"
