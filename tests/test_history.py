"""Verify recent playback and source navigation in compact keyboard-driven dialogs."""

from dataclasses import asdict

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Label, ListView, Static

from music_tui.history_screen import HistoryEntry, HistoryScreen
from music_tui.models import Track

FIRST = Track("a", "[red]第一首[/red]", "歌手 A", 120)
SECOND = Track("b", "第二首", "歌手 B", 90)


class HistoryHarness(App):
    """Host recent playback above a focusable widget without any music resources."""

    def compose(self) -> ComposeResult:
        """Provide the original focus target for dialog dismissal."""
        yield Input(id="original-focus")


def entry(track=FIRST, source="query", label="[blue]来源歌单[/blue]") -> HistoryEntry:
    """Build an entry using literal metadata and the navigation record returned to callers."""
    record = {"track": asdict(track), "source": source, "page": 1}
    return HistoryEntry(track, label, "2026-10-13T05:00:00+00:00", record)


@pytest.mark.parametrize("key, action", [("enter", "play"), ("o", "open")])
async def test_history_returns_selected_play_or_source_action(key, action):
    """Choose the second recent song and return its complete navigation record."""
    entries = [entry(), entry(SECOND, {"id": 2, "title": "歌单 B"}, "歌单 B")]
    app = HistoryHarness()
    results = []
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = HistoryScreen(entries)
        app.push_screen(dialog, results.append)
        await pilot.pause()
        assert isinstance(app.focused, ListView)
        assert any(FIRST.title in str(label.render()) for label in dialog.query(Label))
        assert any(entries[0].source_label in str(label.render()) for label in dialog.query(Label))
        await pilot.press("down", key)
        await pilot.pause()
        assert results == [(action, entries[1].record)]


@pytest.mark.parametrize("size", [(80, 24), (48, 16)])
@pytest.mark.parametrize("key", ["escape", "f8"])
async def test_history_controls_fit_small_terminal_and_restore_focus(size, key):
    """Keep exit instructions visible and return focus after closing in either mode."""
    app = HistoryHarness()
    results = []
    async with app.run_test(size=size) as pilot:
        original = app.query_one(Input)
        original.focus()
        dialog = HistoryScreen([entry() for _ in range(15)])
        app.push_screen(dialog, results.append)
        await pilot.pause()
        for _ in range(2):
            keys = dialog.query_one("#history-keys", Static)
            assert 0 <= keys.region.y < keys.region.bottom <= size[1]
            assert keys.region.right <= size[0]
            assert "F8/Esc" in str(keys.render())
            await pilot.press("tab")
            await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert results == [None]
        assert app.focused is original


async def test_empty_history_remains_open_until_cancel_in_both_modes():
    """Explain empty recent playback and sources without inventing a selectable record."""
    app = HistoryHarness()
    results = []
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = HistoryScreen([])
        app.push_screen(dialog, results.append)
        await pilot.pause()
        for message in ("暂无最近播放", "暂无可打开的播放来源"):
            assert message in str(dialog.query_one("#history-empty", Static).render())
            await pilot.press("enter", "o")
            await pilot.pause()
            assert app.screen is dialog and results == []
            await pilot.press("tab")
            await pilot.pause()
        await pilot.press("f8")
        await pilot.pause()
        assert results == [None]


async def test_history_without_source_explains_open_and_still_allows_replay():
    """Leave a source-less record visible after O while allowing Enter to replay it."""
    recent = entry(source=None, label="无来源")
    app = HistoryHarness()
    results = []
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = HistoryScreen([recent])
        app.push_screen(dialog, results.append)
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        assert app.screen is dialog and results == []
        assert "没有可打开" in str(dialog.query_one("#history-keys", Static).render())
        await pilot.press("enter")
        await pilot.pause()
        assert results == [("play", recent.record)]


async def test_recent_sources_deduplicate_playlist_id_and_search_query():
    """Expose one newest source entry per playlist ID or search text, excluding absent sources."""
    playlist = {"id": 1, "title": "歌单"}
    newest = entry(FIRST, playlist, "歌单")
    search = entry(SECOND, "same query", "搜索：same query")
    entries = [
        newest,
        entry(SECOND, playlist | {"title": "以前名称"}, "以前名称"),
        search,
        entry(FIRST, "same query", "搜索：same query"),
        entry(source=None),
        entry(SECOND, "different query", "搜索：different query"),
    ]
    app = HistoryHarness()
    results = []
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = HistoryScreen(entries)
        app.push_screen(dialog, results.append)
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert dialog.sources == [newest, search, entries[-1]]
        assert dialog.query_one(ListView).index == 0
        assert len(dialog.query(ListView).first().children) == 3
        assert "Enter 打开来源" in str(dialog.query_one("#history-keys", Static).render())
        await pilot.press("down", "enter")
        await pilot.pause()
        assert results == [("open", search.record)]
