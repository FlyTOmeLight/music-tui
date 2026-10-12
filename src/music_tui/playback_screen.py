"""A focused full-screen player which reads the application's live playback state."""

from collections.abc import Callable
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, ProgressBar, Static

from .models import Track


@dataclass(frozen=True)
class PlaybackState:
    """A player snapshot containing only values needed by the playback page."""

    track: Track | None = None
    position: float = 0
    duration: float = 0
    paused: bool = False
    buffering: bool = False
    volume: float = 70
    quality: str = ""
    repeat_mode: str = ""
    source_label: str = ""
    platform: str = ""
    loading: bool = False
    ready: bool = False
    active: bool = False
    status: str = ""
    pending_count: int = 0
    next_track: Track | None = None
    next_hint: str = ""


def _timestamp(seconds: float) -> str:
    """Format an elapsed or total duration as a nonnegative minute/second value."""
    minutes, remainder = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{remainder:02d}"


class PlaybackScreen(Screen):
    """Present song identity and live controls without owning audio or queue state."""

    BINDINGS = [
        Binding("escape,f7", "dispatch('browse')", "返回浏览", priority=True),
        Binding("p", "dispatch('previous')", "上一首", priority=True),
        Binding("space", "dispatch('pause')", "暂停", priority=True),
        Binding("n", "dispatch('next')", "下一首", priority=True),
        Binding("u", "dispatch('quality')", "音质", priority=True),
        Binding("f1", "dispatch('help')", "帮助", priority=True),
        Binding("f2", "dispatch('platform')", "平台", priority=True),
        Binding("f3", "dispatch('queue')", "队列", priority=True),
        Binding("f4", "dispatch('lyrics')", "歌词", priority=True),
        Binding("f5", "dispatch('retry')", "重试", priority=True),
        Binding("f6", "dispatch('resume_session')", "继续上次", priority=True),
        Binding("slash", "dispatch('search')", "搜索", priority=True),
        Binding("g", "dispatch('locate')", "定位当前", priority=True),
        Binding("f", "dispatch('filter')", "歌单筛选", priority=True),
    ]
    DEFAULT_CSS = """
    PlaybackScreen { background: #111821; color: #e4ecf5; padding: 0 1; }
    #playback-heading { height: 1; color: #65d9a4; background: #182330; text-style: bold; }
    #playback-body { height: 1fr; padding: 1 1; }
    #playback-state { height: auto; color: #65d9a4; }
    #playback-title { height: auto; min-height: 1; text-style: bold; margin-top: 1; }
    #playback-artist { height: auto; color: #a1b2c8; }
    #playback-time { height: 1; margin-top: 1; }
    #playback-progress { height: 1; width: 100%; }
    #playback-progress Bar { width: 1fr; }
    #playback-progress .bar--bar { color: #65d9a4; }
    #playback-settings { height: auto; margin-top: 1; }
    #playback-source, #playback-next { height: auto; color: #a1b2c8; }
    #playback-message { height: auto; margin-top: 1; }
    #playback-controls { height: 6; grid-size: 4 2; grid-gutter: 0 1; }
    #playback-controls Button {
        width: 1fr; min-width: 0; height: 3; background: #182330; border: tall #304254;
    }
    #playback-controls Button:focus { background: #254a41; border: tall #65d9a4; }
    #playback-controls Button:disabled { color: #7e93aa; }
    #playback-keys { height: 1; color: #a1b2c8; text-overflow: ellipsis; }
    """

    def __init__(self, get_state: Callable[[], PlaybackState], action: Callable[[str], None]):
        """Read the same state as the browser and delegate all actions to its owner."""
        super().__init__()
        self.get_state = get_state
        self.callback = action
        self._state: PlaybackState | None = None
        self._text: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        """Keep transport and return buttons visible above a scrollable song summary."""
        yield Static("播放页", id="playback-heading", markup=False)
        with VerticalScroll(id="playback-body"):
            yield Static("", id="playback-state", markup=False)
            yield Static("", id="playback-title", markup=False)
            yield Static("", id="playback-artist", markup=False)
            yield Static("", id="playback-time", markup=False)
            yield ProgressBar(
                total=1, show_percentage=False, show_eta=False, id="playback-progress"
            )
            yield Static("", id="playback-settings", markup=False)
            yield Static("", id="playback-source", markup=False)
            yield Static("", id="playback-next", markup=False)
            yield Static("", id="playback-message", markup=False)
        with Grid(id="playback-controls"):
            yield Button("上一首", id="playback-previous")
            yield Button("暂停", id="playback-pause")
            yield Button("下一首", id="playback-next-button")
            yield Button("返回浏览", id="playback-browse")
            yield Button("音质", id="playback-quality")
            yield Button("队列", id="playback-queue")
            yield Button("歌词", id="playback-lyrics")
            yield Button("重试", id="playback-retry")
        yield Static("Tab 选择 · Enter 操作 · F7/Esc 返回", id="playback-keys", markup=False)

    def on_mount(self) -> None:
        """Start polling and place keyboard focus on pause or return when idle."""
        self.refresh_playback()
        pause = self.query_one("#playback-pause", Button)
        (pause if not pause.disabled else self.query_one("#playback-browse", Button)).focus()
        self.set_interval(0.25, self.refresh_playback)

    def update_text(self, widget_id: str, text: str) -> None:
        """Refresh text only when its visible value changed."""
        if self._text.get(widget_id) != text:
            self._text[widget_id] = text
            self.query_one(f"#{widget_id}", Static).update(text)

    def refresh_playback(self) -> None:
        """Display real player values while retaining buttons and keyboard focus."""
        state = self.get_state()
        if state == self._state:
            return
        previous = self._state
        self._state = state
        if state.loading:
            activity = "正在获取播放地址…"
        elif not state.ready:
            activity = "正在准备播放器…"
        elif state.buffering:
            activity = "正在缓冲…"
        elif state.track is None:
            activity = "尚未播放"
        elif not state.active:
            activity = "等待播放"
        elif state.paused:
            activity = "已暂停"
        else:
            activity = "正在播放"
        self.update_text("playback-heading", f"播放页 · {state.platform or 'Music TUI'}")
        self.update_text("playback-state", activity)
        self.update_text("playback-title", state.track.title if state.track else "选一首歌开始听")
        self.update_text(
            "playback-artist", state.track.artist if state.track else "返回浏览页搜索歌曲"
        )
        duration = _timestamp(state.duration) if state.duration > 0 else "--:--"
        self.update_text("playback-time", f"{_timestamp(state.position)} / {duration}")
        if previous is None or (state.position, state.duration) != (
            previous.position,
            previous.duration,
        ):
            self.query_one("#playback-progress", ProgressBar).update(
                total=state.duration if state.duration > 0 else 1,
                progress=min(max(0, state.position), state.duration) if state.duration > 0 else 0,
            )
        self.update_text(
            "playback-settings",
            f"音量 {state.volume:g}% · {state.quality or '尚未选择音质'} · "
            f"{state.repeat_mode or '顺序播放'}",
        )
        self.update_text("playback-source", f"来源：{state.source_label or '尚未选择播放列表'}")
        if state.next_track:
            following = f"下一首：{state.next_track.title} · {state.next_track.artist}"
        elif state.next_hint:
            following = state.next_hint
        elif state.pending_count:
            following = f"手动待播 {state.pending_count} 首，按 F3 查看队列"
        else:
            following = "按 F3 查看队列，返回浏览页按 A 添加下一首"
        self.update_text("playback-next", following)
        self.update_text("playback-message", state.status)
        self.query_one("#playback-pause", Button).label = "继续" if state.paused else "暂停"
        for name, disabled in (
            ("pause", not state.active or state.loading),
            ("previous", not state.ready),
            ("next-button", not state.ready),
            ("quality", state.track is None),
            ("lyrics", state.track is None),
            ("retry", state.track is None),
        ):
            button = self.query_one(f"#playback-{name}", Button)
            if button.disabled != disabled:
                button.disabled = disabled

    @on(Button.Pressed)
    def press_control(self, event: Button.Pressed) -> None:
        """Route the selected transport or navigation button through the shared owner."""
        event.stop()
        name = (event.button.id or "").removeprefix("playback-")
        self.callback("next" if name == "next-button" else name)

    def action_dispatch(self, name: str) -> None:
        """Delegate shortcuts, including leaving the page before browser navigation."""
        self.callback(name)
