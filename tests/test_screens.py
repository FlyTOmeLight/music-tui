"""Exercise standalone music dialogs with keyboard input and literal user content."""

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import DataTable, Input, Label, Static

from music_tui.models import LyricLine, Lyrics, Playlist, Track
from music_tui.screens import (
    HelpScreen,
    LyricsScreen,
    PlaylistPickerScreen,
    QueueItem,
    QueueScreen,
    QueueState,
)

FIRST = Track("a", "[red]第一首[/red]", "歌手 A", 120)
SECOND = Track("b", "第二首", "歌手 B", 90)


class ScreenHarness(App):
    """Host modal screens above a focused input to verify focus restoration."""

    def __init__(self):
        """Record seek requests while keeping player work outside dialog tests."""
        super().__init__()
        self.seeks = []

    def compose(self) -> ComposeResult:
        """Provide a focusable widget which exists before the dialog opens."""
        yield Input(id="original-focus")

    def action_seek(self, seconds: int) -> None:
        """Record arrow-key requests forwarded from the lyrics screen."""
        self.seeks.append(seconds)


@pytest.mark.parametrize("size", [(80, 24), (48, 16)])
@pytest.mark.parametrize("dialog_name", ["help", "queue", "lyrics", "playlist"])
async def test_dialog_controls_fit_and_escape_restores_focus(size, dialog_name):
    """Keep closing instructions inside compact terminals and restore the previous focus."""
    app = ScreenHarness()
    dialogs = {
        "help": HelpScreen(),
        "queue": QueueScreen(
            lambda: QueueState(FIRST, (QueueItem(object(), SECOND),), (FIRST,), True, "歌单"),
            lambda operation, key: None,
        ),
        "lyrics": LyricsScreen(FIRST, Lyrics(), lambda: 0),
        "playlist": PlaylistPickerScreen([Playlist(1, "歌单", editable=True)]),
    }
    async with app.run_test(size=size) as pilot:
        original = app.query_one(Input)
        original.focus()
        app.push_screen(dialogs[dialog_name])
        await pilot.pause()
        keys = app.screen.query_one(".dialog-keys", Static)
        assert keys.region.y >= 0
        assert keys.region.bottom <= size[1]
        assert keys.region.right <= size[0]
        assert "Esc" in str(keys.render())
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is original


async def test_help_scrolls_and_function_key_closes():
    """Allow the long help content to scroll and close using the documented toggle."""
    app = ScreenHarness()
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(HelpScreen())
        await pilot.pause()
        scroll = app.screen.query_one("#help-body", VerticalScroll)
        await pilot.press("pagedown")
        await pilot.pause()
        assert scroll.scroll_y > 0
        await pilot.press("f1")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)


async def test_queue_delete_uses_manual_identity_and_stays_open():
    """Protect original rows, delegate manual deletion, and display literal metadata."""
    app = ScreenHarness()
    first, second = object(), object()
    pending = [QueueItem(first, FIRST), QueueItem(second, SECOND)]
    calls = []

    def state():
        """Read the application's manual queue without exposing mutable list ownership."""
        return QueueState(FIRST, tuple(pending), (SECOND,), True, "[red]歌单[/red]")

    def edit(operation, key):
        """Apply removal by identity and record which rendered item was requested."""
        calls.append((operation, key))
        pending[:] = [item for item in pending if item.key is not key]

    async with app.run_test(size=(80, 24)) as pilot:
        dialog = QueueScreen(state, edit)
        app.push_screen(dialog)
        await pilot.pause()
        table = dialog.query_one(DataTable)
        assert table.get_row_at(0)[1].plain == FIRST.title
        assert "[red]歌单[/red]" in str(dialog.query_one("#queue-source", Static).render())
        await pilot.press("down", "down", "delete")
        await pilot.pause()
        assert calls == []
        await pilot.press("up", "delete")
        await pilot.pause()
        assert app.screen is dialog
        assert calls == [("remove", second)]
        assert table.row_count == 2


async def test_queue_reorder_and_clear_keep_focus_and_selection():
    """Move the selected identity in both directions and clear only manual entries."""
    app = ScreenHarness()
    first, second = object(), object()
    pending = [QueueItem(first, FIRST), QueueItem(second, SECOND)]
    calls = []

    def state():
        """Expose current manual ordering with an unchanged original-source remainder."""
        return QueueState(FIRST, tuple(pending), (FIRST,), False, "来源")

    def edit(operation, key):
        """Model the owner callback's identity-based move and clear operations."""
        calls.append((operation, key))
        if operation == "clear":
            pending.clear()
        else:
            index = next(index for index, item in enumerate(pending) if item.key is key)
            target = index + (-1 if operation == "up" else 1)
            pending[index], pending[target] = pending[target], pending[index]

    async with app.run_test(size=(80, 24)) as pilot:
        dialog = QueueScreen(state, edit)
        app.push_screen(dialog)
        await pilot.pause()
        table = dialog.query_one(DataTable)
        await pilot.press("down", "ctrl+up")
        await pilot.pause()
        assert dialog.selected_key() is second
        assert table.cursor_row == 0
        await pilot.press("ctrl+down")
        await pilot.pause()
        assert dialog.selected_key() is second
        assert table.cursor_row == 1
        await pilot.press("ctrl+delete")
        await pilot.pause()
        assert app.screen is dialog
        assert app.focused is table
        assert table.row_count == 1
        assert calls == [("up", second), ("down", second), ("clear", None)]


async def test_queue_updates_when_playback_consumes_a_manual_entry():
    """Retain the selected identity when automatic playback removes an earlier row."""
    app = ScreenHarness()
    first, second = object(), object()
    pending = [QueueItem(first, FIRST), QueueItem(second, SECOND)]
    current = [FIRST]
    calls = []

    def state():
        """Provide live playing-track and pending changes from outside the dialog."""
        return QueueState(current[0], tuple(pending), (), False, "来源")

    async with app.run_test(size=(80, 24)) as pilot:
        dialog = QueueScreen(state, lambda operation, key: calls.append((operation, key)))
        app.push_screen(dialog)
        await pilot.pause()
        await pilot.press("down")
        pending.pop(0)
        current[0] = SECOND
        await pilot.pause(0.3)
        assert dialog.selected_key() is second
        assert dialog.query_one(DataTable).cursor_row == 0
        assert SECOND.title in str(dialog.query_one("#queue-current", Static).render())
        await pilot.press("delete")
        assert calls == [("remove", second)]


async def test_queue_edit_uses_rendered_identity_before_timer_refresh():
    """Pass the stale rendered token to the owner instead of deleting a replacement row."""
    app = ScreenHarness()
    first, second = object(), object()
    pending = [QueueItem(first, FIRST), QueueItem(second, FIRST)]
    calls = []

    def state():
        """Return identical track metadata for distinct manually queued identities."""
        return QueueState(FIRST, tuple(pending), (), False, "来源")

    async with app.run_test(size=(80, 24)) as pilot:
        dialog = QueueScreen(state, lambda operation, key: calls.append((operation, key)))
        app.push_screen(dialog)
        await pilot.pause()
        pending.pop(0)
        dialog.action_edit("remove")
        assert calls == [("remove", first)]
        assert dialog.selected_key() is second


async def test_picker_filters_readonly_playlists_and_returns_selection():
    """Choose a writable playlist while preserving literal bracketed titles."""
    writable = Playlist(2, "[red]自己的歌单[/red]", editable=True)
    app = ScreenHarness()
    results = []
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = PlaylistPickerScreen([Playlist(1, "收藏歌单"), writable])
        app.push_screen(dialog, results.append)
        await pilot.pause()
        assert dialog.playlists == [writable]
        assert writable.title in str(dialog.query_one(Label).render())
        await pilot.press("enter")
        await pilot.pause()
        assert results == [writable]


@pytest.mark.parametrize("dialog_name", ["queue", "playlist"])
async def test_empty_queue_and_picker_remain_closable(dialog_name):
    """Handle absent tracks and writable destinations without accidental selection."""
    app = ScreenHarness()
    results = []
    dialog = (
        QueueScreen(lambda: QueueState(None, (), (), False, ""), lambda operation, key: None)
        if dialog_name == "queue"
        else PlaylistPickerScreen([])
    )
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(dialog, results.append)
        await pilot.pause()
        await pilot.press("delete", "enter")
        await pilot.pause()
        assert app.screen is dialog
        await pilot.press("escape")
        await pilot.pause()
        assert results == [None]


async def test_lyrics_follow_position_forward_and_backward_with_literal_text():
    """Seek across timed lines and show lyrics and translations as literal content."""
    position = [0.0]
    lyrics = Lyrics(
        tuple(
            LyricLine(index * 5, f"[red]第 {index} 行[/red]", "[link]译文[/link]")
            for index in range(20)
        )
    )
    app = ScreenHarness()
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = LyricsScreen(FIRST, lyrics, lambda: position[0])
        app.push_screen(dialog)
        await pilot.pause()
        assert dialog.active_line == 0
        await pilot.press("right", "left")
        assert app.seeks == [5, -5]
        assert dialog.query_one("#lyric-0", Static).has_class("lyric-current")
        assert "[red]第 0 行[/red]" in str(dialog.query_one("#lyric-0", Static).render())
        assert "[link]译文[/link]" in str(dialog.query_one("#lyric-0", Static).render())
        position[0] = 75
        await pilot.pause(0.3)
        assert dialog.active_line == 15
        assert not dialog.query_one("#lyric-0", Static).has_class("lyric-current")
        assert dialog.query_one("#lyrics-body", VerticalScroll).scroll_y > 0
        position[0] = 2
        await pilot.pause(0.3)
        assert dialog.active_line == 0
        assert not dialog.query_one("#lyric-15", Static).has_class("lyric-current")


async def test_plain_lyrics_show_unsynced_message_without_highlighting():
    """Allow untimed lyrics to be read without treating every line as simultaneous."""
    app = ScreenHarness()
    async with app.run_test(size=(80, 24)) as pilot:
        dialog = LyricsScreen(FIRST, Lyrics((LyricLine(0, "正文"),), synced=False), lambda: 30)
        app.push_screen(dialog)
        await pilot.pause()
        assert dialog.active_line == -1
        assert not dialog.query_one("#lyric-0", Static).has_class("lyric-current")
        assert any("没有时间信息" in str(widget.render()) for widget in dialog.query(Static))
