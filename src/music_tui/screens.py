"""Keyboard-driven dialogs for queue, lyrics, library actions, and shortcut help."""

from bisect import bisect_right
from collections.abc import Callable

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label, ListItem, ListView, Static

from .models import Lyrics, Playlist, Track

DIALOG_CSS = """
.feature-dialog { align: center middle; background: #111821 95%; }
.feature-dialog .dialog-box {
    width: 74; max-width: 100%; height: 90%; max-height: 100%;
    border: solid #58d6a0; padding: 0 1; background: #111821; color: #e4ecf5;
}
.feature-dialog .dialog-title { height: auto; color: #65d9a4; text-style: bold; }
.feature-dialog .dialog-subtitle { height: auto; color: #a1b2c8; }
.feature-dialog .dialog-body { height: 1fr; margin: 1 0; }
.feature-dialog .dialog-keys { height: auto; color: #a1b2c8; }
.feature-dialog ListItem { height: auto; min-height: 2; margin: 0; padding: 0 1; }
.feature-dialog ListItem > Label { height: auto; }
.feature-dialog ListView { background: transparent; }
.feature-dialog ListView:focus > ListItem.-highlight { background: #254a41; }
.feature-dialog DataTable { background: transparent; }
.feature-dialog DataTable:focus > .datatable--cursor { background: #254a41; color: white; }
.feature-dialog .lyric-line { height: auto; padding: 0 1; margin-bottom: 1; }
.feature-dialog .lyric-current { background: #254a41; color: #65d9a4; text-style: bold; }
"""


class HelpScreen(ModalScreen[None]):
    """Explain all keyboard actions in a scrollable compact dialog."""

    BINDINGS = [Binding("escape,f1", "cancel", "返回", priority=True)]
    SCOPED_CSS = False
    DEFAULT_CSS = DIALOG_CSS

    def compose(self) -> ComposeResult:
        """Keep navigation guidance visible while the shortcut list scrolls."""
        self.add_class("feature-dialog")
        with Vertical(classes="dialog-box"):
            yield Static("快捷键帮助", classes="dialog-title")
            with VerticalScroll(classes="dialog-body", id="help-body"):
                yield Static(
                    "播放与浏览\n"
                    "Enter  播放选中歌曲，并使用当前列表作为播放来源\n"
                    "Space  暂停／继续    P / N  上一首／下一首\n"
                    "← / →  后退／前进 5 秒    − / +  调整音量\n"
                    "R  切换播放模式    U  选择音质\n"
                    "[ / ] 或 PageUp / PageDown  翻页\n"
                    "/  搜索歌曲与歌手    F  歌单内筛选\n"
                    "Tab  切换焦点    Esc  返回歌曲列表\n\n"
                    "队列与会话\n"
                    "F3  查看待播队列    A  将选中歌曲插入下一首\n"
                    "队列内 Delete  删除选中的手动待播歌曲\n"
                    "F5  重试当前失败歌曲    F6  继续上次播放\n"
                    "G  返回当前播放来源并定位歌曲\n\n"
                    "歌词与歌单\n"
                    "F4  查看当前歌曲的歌词\n"
                    "S  喜欢／取消喜欢    W  加入已有歌单\n"
                    "Ctrl+R  刷新当前列表    L  扫码登录\n"
                    "F2  切换音乐平台    Q  退出\n\n"
                    "搜索框中可正常输入字母；先按 Esc 返回列表，再使用字母快捷键。",
                    markup=False,
                )
            yield Static("↑/↓ 或 PageUp/PageDown 滚动 · F1/Esc 返回", classes="dialog-keys")

    def on_mount(self) -> None:
        """Focus the scrollable text so arrow and page keys work immediately."""
        self.query_one("#help-body", VerticalScroll).focus()

    def action_cancel(self) -> None:
        """Close help and restore the preceding screen's focus."""
        self.dismiss(None)


class QueueScreen(ModalScreen[int | None]):
    """Show manual next tracks and the remaining original playback source."""

    BINDINGS = [
        Binding("escape,f3", "cancel", "返回", priority=True),
        Binding("delete,backspace", "remove", "删除待播"),
    ]
    SCOPED_CSS = False
    DEFAULT_CSS = DIALOG_CSS

    def __init__(
        self,
        current: Track | None,
        pending: list[Track],
        upcoming: list[Track],
        has_more: bool,
        source_label: str,
    ):
        """Take a queue snapshot; deletion returns the selected manual item's index."""
        super().__init__()
        self.current = current
        self.pending = list(pending)
        self.upcoming = list(upcoming)
        self.has_more = has_more
        self.source_label = source_label

    def compose(self) -> ComposeResult:
        """Separate the playing track from manual and original-source queue rows."""
        self.add_class("feature-dialog")
        with Vertical(classes="dialog-box"):
            yield Static("待播队列", classes="dialog-title")
            current = (
                f"正在播放：{self.current.title} · {self.current.artist}"
                if self.current
                else "当前没有播放歌曲"
            )
            yield Static(current, markup=False, classes="dialog-subtitle")
            yield Static(
                f"播放来源：{self.source_label or '尚未选择'}\n"
                "手动待播优先播放，听完后继续原列表。",
                markup=False,
                classes="dialog-subtitle",
            )
            yield DataTable(id="queue-items", classes="dialog-body", cursor_type="row")
            if not self.pending and not self.upcoming:
                yield Static("暂无待播歌曲，返回列表按 A 添加。", markup=False)
            if self.has_more:
                yield Static("原列表还有后续页面，播放时会继续加载。", markup=False)
            yield Static("↑/↓ 选择 · Delete 删除手动待播 · F3/Esc 返回", classes="dialog-keys")

    def on_mount(self) -> None:
        """Fill the queue table without interpreting song metadata as markup."""
        table = self.query_one("#queue-items", DataTable)
        table.add_columns("顺序", "歌曲", "歌手")
        for index, track in enumerate(self.pending):
            table.add_row(f"待播 {index + 1}", Text(track.title), Text(track.artist))
        for index, track in enumerate(self.upcoming):
            table.add_row(f"随后 {index + 1}", Text(track.title), Text(track.artist))
        table.focus()

    def action_remove(self) -> None:
        """Return only manual queue indices; original-source rows cannot be deleted."""
        row = self.query_one("#queue-items", DataTable).cursor_row
        if 0 <= row < len(self.pending):
            self.dismiss(row)

    def action_cancel(self) -> None:
        """Close the queue snapshot without editing it."""
        self.dismiss(None)


class LyricsScreen(ModalScreen[None]):
    """Follow the active lyric line while keeping plain lyrics readable."""

    BINDINGS = [
        Binding("escape,f4", "cancel", "返回", priority=True),
        Binding("left", "app.seek(-5)", "后退", show=False),
        Binding("right", "app.seek(5)", "前进", show=False),
    ]
    SCOPED_CSS = False
    DEFAULT_CSS = DIALOG_CSS

    def __init__(self, track: Track, lyrics: Lyrics, position: Callable[[], float]):
        """Bind lyrics to the current track and its live playback position."""
        super().__init__()
        self.track = track
        self.lyrics = lyrics
        self.position = position
        self.active_line = -1
        self._times = [line.seconds for line in lyrics.lines]

    def compose(self) -> ComposeResult:
        """Render literal lyrics and translations with explicit unavailable states."""
        self.add_class("feature-dialog")
        with Vertical(classes="dialog-box"):
            yield Static(f"歌词 · {self.track.title}", markup=False, classes="dialog-title")
            yield Static(self.track.artist, markup=False, classes="dialog-subtitle")
            with VerticalScroll(id="lyrics-body", classes="dialog-body"):
                if not self.lyrics.lines:
                    yield Static("暂无歌词，此歌曲可能是纯音乐。", markup=False)
                elif not self.lyrics.synced:
                    yield Static("歌词没有时间信息，可手动滚动阅读。", markup=False)
                for index, line in enumerate(self.lyrics.lines):
                    text = line.text + (f"\n{line.translation}" if line.translation else "")
                    yield Static(text, markup=False, id=f"lyric-{index}", classes="lyric-line")
            hint = "←/→ 后退/前进 5 秒 · " if self.lyrics.synced and self.lyrics.lines else ""
            yield Static(f"{hint}↑/↓ 滚动 · F4/Esc 返回", classes="dialog-keys")

    def on_mount(self) -> None:
        """Start position tracking after the lyric widgets are laid out."""
        self.query_one("#lyrics-body", VerticalScroll).focus()
        self.call_after_refresh(self.sync_position)
        self.set_interval(0.25, self.sync_position)

    def sync_position(self) -> None:
        """Update the active line after playback, pause, or forward/backward seeking."""
        if not self.lyrics.synced or not self._times:
            return
        index = bisect_right(self._times, self.position()) - 1
        if index == self.active_line:
            return
        if self.active_line >= 0:
            self.query_one(f"#lyric-{self.active_line}", Static).remove_class("lyric-current")
        self.active_line = index
        if index >= 0:
            line = self.query_one(f"#lyric-{index}", Static)
            line.add_class("lyric-current")
            self.query_one("#lyrics-body", VerticalScroll).scroll_to_widget(
                line, animate=False, center=True
            )

    def action_cancel(self) -> None:
        """Close lyrics and restore browsing focus without changing playback."""
        self.dismiss(None)


class PlaylistPickerScreen(ModalScreen[Playlist | None]):
    """Choose a source-reported writable playlist for the selected song."""

    BINDINGS = [Binding("escape", "cancel", "返回", priority=True)]
    SCOPED_CSS = False
    DEFAULT_CSS = DIALOG_CSS

    def __init__(self, playlists: list[Playlist]):
        """Exclude collected and other playlists which the account cannot edit."""
        super().__init__()
        self.playlists = [playlist for playlist in playlists if playlist.editable]

    def compose(self) -> ComposeResult:
        """Show writable playlists or a clear account/library empty state."""
        self.add_class("feature-dialog")
        with Vertical(classes="dialog-box"):
            yield Static("加入歌单", classes="dialog-title")
            if self.playlists:
                yield ListView(
                    *(ListItem(Label(playlist.title, markup=False)) for playlist in self.playlists),
                    id="playlist-options",
                    classes="dialog-body",
                )
            else:
                yield Static(
                    "暂无可写歌单，请确认账号和接入方式，再刷新歌单列表。",
                    markup=False,
                    classes="dialog-body",
                )
            yield Static("↑/↓ 选择 · Enter 确认 · Esc 返回", classes="dialog-keys")

    def on_mount(self) -> None:
        """Focus choices only when there is a writable destination."""
        if self.playlists:
            self.query_one("#playlist-options", ListView).focus()

    @on(ListView.Selected, "#playlist-options")
    def select_playlist(self, event: ListView.Selected) -> None:
        """Return the selected playlist so the caller can perform the source write."""
        if event.list_view.index is not None:
            self.dismiss(self.playlists[event.list_view.index])

    def action_cancel(self) -> None:
        """Close the picker without adding the song anywhere."""
        self.dismiss(None)
