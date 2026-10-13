"""Inline listening panels and dialogs for library actions and shortcut help."""

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, ListItem, ListView, Static

from .models import Lyrics, Playlist, Track

DIALOG_CSS = """
.feature-dialog { align: center middle; background: $background 95%; }
.feature-dialog .dialog-box {
    width: 74; max-width: 100%; height: 90%; max-height: 100%;
    border: round $border; padding: 0 1; background: $background; color: $foreground;
}
.feature-dialog .dialog-title {
    background: $surface;
    padding: 0 1;
    height: auto;
    color: $accent;
    text-style: bold;
}
.feature-dialog .dialog-subtitle { height: auto; color: $text-muted; }
.feature-dialog .dialog-body { height: 1fr; margin: 1 0; }
.feature-dialog .dialog-keys { border-top: solid $border; height: auto; color: $text-muted; }
.feature-dialog ListItem { height: auto; min-height: 2; margin: 0; padding: 0 1; }
.feature-dialog ListItem > Label { height: auto; }
.feature-dialog ListView { background: transparent; }
.feature-dialog ListView > ListItem.-highlight { background: $surface; }
.feature-dialog ListView:focus > ListItem.-highlight { background: $primary-muted; color: $accent; }
.feature-dialog DataTable { background: transparent; }
.feature-dialog DataTable > .datatable--header { background: $surface; color: $text-muted; }
.feature-dialog DataTable:focus > .datatable--cursor {
    background: $primary-muted; color: $foreground;
}
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
                    "F3  打开播放页的队列页签    A  将选中歌曲插入下一首\n"
                    "队列内 Delete  删除选中的手动待播歌曲\n"
                    "Ctrl+↑/↓  调整待播顺序    Ctrl+Delete  清空手动待播\n"
                    "F5  重试当前失败歌曲    F6  继续上次播放\n"
                    "F7  打开／关闭独立播放页\n"
                    "F8  最近播放；Tab 切换歌曲／来源，O 打开来源\n"
                    "G  返回当前播放来源并定位歌曲\n\n"
                    "歌词与歌单\n"
                    "F4  打开播放页的歌词页签\n"
                    "S  喜欢／取消喜欢    W  加入歌单（播放页为当前歌曲）\n"
                    "Ctrl+N  新建歌单    歌单侧栏可按累计播放量排序\n"
                    "Ctrl+R  刷新当前列表    L  扫码登录\n"
                    "F2  切换音乐平台    F9  昼夜模式    Q  退出\n\n"
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


@dataclass(frozen=True)
class QueueItem:
    """A manual queue entry carrying the application's opaque identity token."""

    key: object
    track: Track


@dataclass(frozen=True)
class QueueState:
    """A live snapshot of the current track and both kinds of queued tracks."""

    current: Track | None
    pending: tuple[QueueItem, ...]
    upcoming: tuple[Track, ...]
    has_more: bool
    source_label: str


class QueuePanel(Vertical):
    """Edit the live queue inside the playback page."""

    BINDINGS = [
        Binding("delete,backspace", "edit('remove')", "删除待播"),
        Binding("ctrl+up", "edit('up')", "上移", priority=True),
        Binding("ctrl+down", "edit('down')", "下移", priority=True),
        Binding("ctrl+delete", "edit('clear')", "清空待播", priority=True),
    ]
    DEFAULT_CSS = """
    QueuePanel { height: 1fr; background: $surface; }
    QueuePanel .queue-summary { height: 1; color: $text-muted; text-overflow: ellipsis; }
    QueuePanel #queue-items { height: 1fr; background: $surface; }
    QueuePanel #queue-notice { height: auto; color: $text-muted; }
    QueuePanel .queue-keys { height: 1; color: $text-muted; text-overflow: ellipsis; }
    """

    def __init__(
        self,
        get_state: Callable[[], QueueState],
        edit: Callable[[str, object | None], None],
    ):
        """Read shared queue state and delegate changes using stable entry identities."""
        super().__init__()
        self.get_state = get_state
        self.edit = edit
        self._signature: tuple | None = None
        self._row_keys: list[object | None] = []

    def compose(self) -> ComposeResult:
        """Keep queue rows and editing guidance within the playback tab."""
        yield Static("", id="queue-current", markup=False, classes="queue-summary")
        yield Static("", id="queue-source", markup=False, classes="queue-summary")
        yield DataTable(id="queue-items", cursor_type="row")
        yield Static("", id="queue-notice", markup=False)
        yield Static(
            "Delete 删除 · Ctrl+↑/↓ 调序 · Ctrl+Delete 清空",
            classes="queue-keys",
        )

    def on_mount(self) -> None:
        """Start refreshing live rows without recreating the focused table."""
        table = self.query_one("#queue-items", DataTable)
        table.add_columns("顺序", "歌曲", "歌手")
        self.refresh_queue()
        self.set_interval(0.25, self.refresh_queue)

    def selected_key(self) -> object | None:
        """Return the identity rendered in the selected row, not a fresh queue index."""
        row = self.query_one("#queue-items", DataTable).cursor_row
        return self._row_keys[row] if 0 <= row < len(self._row_keys) else None

    def refresh_queue(self) -> None:
        """Redraw changed state and retain the selected manual entry by identity."""
        state = self.get_state()
        signature = (
            state.current,
            tuple((id(item.key), item.track) for item in state.pending),
            state.upcoming,
            state.has_more,
            state.source_label,
        )
        if signature == self._signature:
            return
        table = self.query_one("#queue-items", DataTable)
        selected = self.selected_key()
        row = table.cursor_row
        self._signature = signature
        current = (
            f"正在播放：{state.current.title} · {state.current.artist}"
            if state.current
            else "当前没有播放歌曲"
        )
        self.query_one("#queue-current", Static).update(current)
        self.query_one("#queue-source", Static).update(
            f"播放来源：{state.source_label or '尚未选择'} · 手动待播优先"
        )
        table.clear()
        self._row_keys = []
        for index, item in enumerate(state.pending):
            table.add_row(f"待播 {index + 1}", Text(item.track.title), Text(item.track.artist))
            self._row_keys.append(item.key)
        for index, track in enumerate(state.upcoming):
            table.add_row(f"随后 {index + 1}", Text(track.title), Text(track.artist))
            self._row_keys.append(None)
        if selected is not None:
            row = next((index for index, key in enumerate(self._row_keys) if key is selected), row)
        if self._row_keys:
            table.move_cursor(row=min(row, len(self._row_keys) - 1))
        notice = (
            "暂无待播歌曲，返回列表按 A 添加。" if not state.pending and not state.upcoming else ""
        )
        if state.has_more:
            notice = "原列表还有后续页面，播放时会继续加载。"
        self.query_one("#queue-notice", Static).update(notice)
        self.query_one("#queue-notice", Static).display = bool(notice)

    def action_edit(self, operation: str) -> None:
        """Edit the rendered manual identity and retain the queue tab."""
        key = self.selected_key()
        if operation == "clear" or key is not None:
            self.edit(operation, None if operation == "clear" else key)
            self.refresh_queue()


class LyricsPanel(Vertical):
    """Follow live lyrics inside a playback tab while keeping plain text readable."""

    BINDINGS = [
        Binding("left", "app.seek(-5)", "后退", show=False),
        Binding("right", "app.seek(5)", "前进", show=False),
    ]
    DEFAULT_CSS = """
    LyricsPanel { height: 1fr; background: $surface; }
    LyricsPanel #lyrics-title { height: 1; color: $text-muted; text-overflow: ellipsis; }
    LyricsPanel #lyrics-body { height: 1fr; }
    LyricsPanel .lyric-line { height: auto; padding: 0 1; margin-bottom: 1; }
    LyricsPanel .lyric-current { background: $primary-muted; color: $accent; text-style: bold; }
    """

    def __init__(self, position: Callable[[], float]):
        """Read playback time without retaining ownership of the player."""
        super().__init__()
        self.position = position
        self.lyrics = Lyrics()
        self.active_line = -1
        self._times: list[float] = []

    def compose(self) -> ComposeResult:
        """Reserve a scrollable lyric body for loading, failure, and lyric content."""
        yield Static("歌词", id="lyrics-title", markup=False)
        with VerticalScroll(id="lyrics-body"):
            yield Static("播放歌曲后可查看歌词。", markup=False)

    def on_mount(self) -> None:
        """Track lyric time while leaving initial focus to the playback page."""
        self.set_interval(0.25, self.sync_position)

    async def show_message(self, text: str) -> None:
        """Clear old song lyrics and show the current loading or unavailable state."""
        self.lyrics = Lyrics()
        self.active_line = -1
        self._times = []
        body = self.query_one("#lyrics-body", VerticalScroll)
        await body.remove_children()
        await body.mount(Static(text, markup=False))
        body.scroll_home(animate=False)

    async def show_lyrics(self, track: Track, lyrics: Lyrics) -> None:
        """Replace the previous song and render literal lyrics and translations."""
        await self.show_message("")
        self.query_one("#lyrics-title", Static).update(f"{track.title} · {track.artist}")
        body = self.query_one("#lyrics-body", VerticalScroll)
        await body.remove_children()
        widgets = []
        if not lyrics.lines:
            widgets.append(Static("暂无歌词，此歌曲可能是纯音乐。", markup=False))
        elif not lyrics.synced:
            widgets.append(Static("歌词没有时间信息，可手动滚动阅读。", markup=False))
        for index, line in enumerate(lyrics.lines):
            text = line.text + (f"\n{line.translation}" if line.translation else "")
            widgets.append(Static(text, markup=False, id=f"lyric-{index}", classes="lyric-line"))
        await body.mount(*widgets)
        self.lyrics = lyrics
        self._times = [line.seconds for line in lyrics.lines]
        self.call_after_refresh(self.sync_position)

    def sync_position(self) -> None:
        """Highlight and center the current line after playback or seeking."""
        if not self.is_on_screen or not self.lyrics.synced or not self._times:
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


class PlaylistPickerScreen(ModalScreen[Playlist | str | None]):
    """Choose a source-reported writable playlist for the selected song."""

    BINDINGS = [Binding("escape", "cancel", "返回", priority=True)]
    SCOPED_CSS = False
    DEFAULT_CSS = DIALOG_CSS

    def __init__(self, playlists: list[Playlist], *, allow_create: bool = False):
        """Exclude collected and other playlists which the account cannot edit."""
        super().__init__()
        self.playlists = [playlist for playlist in playlists if playlist.editable]
        self.allow_create = allow_create

    def compose(self) -> ComposeResult:
        """Show writable playlists or a clear account/library empty state."""
        self.add_class("feature-dialog")
        with Vertical(classes="dialog-box"):
            yield Static("加入歌单", classes="dialog-title")
            if self.allow_create:
                yield Button("新建歌单并加入", id="picker-create")
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

    @on(Button.Pressed, "#picker-create")
    def create_destination(self) -> None:
        """Return an explicit creation choice instead of inventing a destination ID."""
        self.dismiss("create")


class CreatePlaylistScreen(ModalScreen[str | None]):
    """Collect a playlist name before submitting an account write."""

    BINDINGS = [Binding("escape", "cancel", "返回", priority=True)]
    DEFAULT_CSS = """
    CreatePlaylistScreen { align: center middle; background: $background 95%; }
    CreatePlaylistScreen #create-box {
        width: 50; max-width: 100%; height: auto;
        background: $surface; border: round $border; padding: 0 2;
    }
    CreatePlaylistScreen #create-title { height: 1; color: $accent; text-style: bold; }
    CreatePlaylistScreen #create-name { height: 3; }
    CreatePlaylistScreen #create-error { height: 1; color: $error; }
    CreatePlaylistScreen #create-buttons { height: 3; }
    CreatePlaylistScreen #create-buttons Button { width: 1fr; min-width: 0; }
    CreatePlaylistScreen #create-keys { height: 1; color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        """Keep the name, inline validation, and submission controls in one compact form."""
        with Vertical(id="create-box"):
            yield Static("新建歌单", id="create-title")
            yield Input(placeholder="歌单名称", id="create-name")
            yield Static("", id="create-error", markup=False)
            with Horizontal(id="create-buttons"):
                yield Button("创建", id="create-confirm", disabled=True)
                yield Button("取消", id="create-cancel")
            yield Static("Enter 创建 · Esc 返回", id="create-keys")

    def on_mount(self) -> None:
        """Focus the name field before the user enters a playlist title."""
        self.query_one("#create-name", Input).focus()

    @on(Input.Changed, "#create-name")
    def validate_name(self, event: Input.Changed) -> None:
        """Enable submission only for a name containing non-whitespace text."""
        self.query_one("#create-confirm", Button).disabled = not event.value.strip()
        self.query_one("#create-error", Static).update("")

    @on(Input.Submitted, "#create-name")
    @on(Button.Pressed, "#create-confirm")
    def confirm_name(self) -> None:
        """Return the trimmed name or show a local validation error without creating anything."""
        title = self.query_one("#create-name", Input).value.strip()
        if title:
            self.dismiss(title)
        else:
            self.query_one("#create-error", Static).update("请输入歌单名称")

    @on(Button.Pressed, "#create-cancel")
    def action_cancel(self) -> None:
        """Leave the form without creating a playlist or adding a song."""
        self.dismiss(None)
