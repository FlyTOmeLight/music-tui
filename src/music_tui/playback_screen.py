"""A focused full-screen player which reads the application's live playback state."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    ProgressBar,
    Static,
    Tab,
    TabbedContent,
    TabPane,
    Tabs,
)

from .artwork import AlbumArtwork
from .models import Lyrics, Track
from .screens import LyricsPanel, QueuePanel, QueueState


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
    can_add_to_playlist: bool = False
    can_create_playlist: bool = False


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
        Binding("w", "dispatch('add_to_playlist')", "加入歌单", priority=True),
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
    PlaybackScreen {
        background: $background; color: $foreground; padding: 0 1; align: center top;
    }
    PlaybackScreen #navigation { height: 1; }
    PlaybackScreen #navigation Underline { display: none; }
    PlaybackScreen #playback-heading {
        height: 1; padding: 0 1; color: $text-muted; text-overflow: ellipsis;
    }
    PlaybackScreen #playback-tabs { height: 1fr; }
    PlaybackScreen TabPane { padding: 0; height: 1fr; background: $surface; }
    PlaybackScreen ContentSwitcher { height: 1fr; }
    PlaybackScreen #playback-body {
        height: 1fr; padding: 0 2; background: $background; align: center middle;
        scrollbar-background: $background; scrollbar-color: $border;
        scrollbar-color-hover: $text-muted; scrollbar-color-active: $accent;
    }
    PlaybackScreen #song { background: $background; }
    PlaybackScreen #song-identity { width: 100%; max-width: 88; height: auto; min-height: 12; }
    PlaybackScreen #playback-artwork { width: 24; height: 12; margin: 0 3 0 0; }
    PlaybackScreen #song-summary { width: 1fr; height: auto; margin: 1 0; }
    PlaybackScreen.-wide #song-identity { min-height: 18; }
    PlaybackScreen.-wide #song-summary { margin: 4 0; }
    PlaybackScreen.-wide #playback-artwork { width: 36; height: 18; }
    PlaybackScreen.-compact #playback-body { padding: 0 1; }
    PlaybackScreen.-compact #song-identity { height: auto; min-height: 6; }
    PlaybackScreen.-compact #song-summary { margin: 0; }
    PlaybackScreen.-compact #playback-artwork { width: 12; height: 6; margin: 0 1 0 0; }
    PlaybackScreen #playback-state { height: 1; color: $accent; }
    PlaybackScreen #playback-title {
        height: auto; min-height: 1; text-style: bold; margin-top: 1;
    }
    PlaybackScreen #playback-artist { height: auto; color: $text-muted; }
    PlaybackScreen #transport-row { height: auto; align: center top; }
    PlaybackScreen #playback-transport { height: auto; width: 90; max-width: 100%; }
    PlaybackScreen #playback-time { height: 1; text-align: center; }
    PlaybackScreen #playback-progress { height: 1; width: 100%; }
    PlaybackScreen #playback-progress Bar { width: 1fr; }
    PlaybackScreen #playback-progress .bar--bar { color: $accent; background: $border; }
    PlaybackScreen #playback-progress .bar--complete { color: $accent; }
    PlaybackScreen #playback-settings { height: auto; margin-top: 1; }
    PlaybackScreen #playback-source, PlaybackScreen #playback-next {
        height: auto; color: $text-muted;
    }
    PlaybackScreen #playback-message {
        height: 1; color: $text-muted; text-wrap: nowrap; text-overflow: ellipsis;
    }
    PlaybackScreen #playback-controls {
        height: 2; align: center top; background: $background;
    }
    PlaybackScreen #transport-buttons, PlaybackScreen #playback-actions {
        width: 44; max-width: 100%; height: 1; grid-size: 4 1; grid-gutter: 0 1;
    }
    PlaybackScreen.-inline-controls #playback-controls { layout: horizontal; height: 1; }
    PlaybackScreen.-inline-controls #transport-buttons { margin-right: 2; }
    PlaybackScreen.-narrow-controls #playback-controls { height: 4; }
    PlaybackScreen.-narrow-controls #transport-buttons,
    PlaybackScreen.-narrow-controls #playback-actions {
        grid-size: 2 2; grid-rows: 1 1; height: 2;
    }
    PlaybackScreen #playback-controls Button {
        width: 1fr; min-width: 0; height: 1; color: $foreground;
        background: $surface; text-style: none;
    }
    PlaybackScreen #playback-pause { color: $accent; text-style: bold; }
    PlaybackScreen #playback-controls .secondary { color: $text-muted; background: $background; }
    PlaybackScreen #playback-controls Button:hover { background: $primary-muted; }
    PlaybackScreen #playback-controls Button:focus {
        background: $primary-muted; color: $accent; text-style: bold;
    }
    PlaybackScreen #playback-controls Button:disabled {
        color: $text-muted; background: $background; opacity: 60%;
    }
    PlaybackScreen.-compact #playback-heading { display: none; }
    PlaybackScreen.-compact #queue-current { display: none; }
    PlaybackScreen #playback-keys {
        height: 1; color: $text-muted; text-align: center; text-overflow: ellipsis;
    }
    """

    def __init__(
        self,
        get_state: Callable[[], PlaybackState],
        action: Callable[[str], None],
        *,
        get_queue_state: Callable[[], QueueState],
        edit_queue: Callable[[str, object | None], None],
        load_lyrics: Callable[[Track], Awaitable[Lyrics]],
    ):
        """Read live player and queue state and request lyrics only for the active tab."""
        super().__init__()
        self.get_state = get_state
        self.callback = action
        self.get_queue_state = get_queue_state
        self.edit_queue = edit_queue
        self.load_lyrics = load_lyrics
        self._state: PlaybackState | None = None
        self._text: dict[str, str] = {}
        self._lyrics_track: Track | None = None
        self._lyrics_request = 0
        self._initial_tab = "song"

    def compose(self) -> ComposeResult:
        """Keep transport visible while song, lyrics, and queue share a tabbed workspace."""
        yield Tabs(
            Tab("歌曲", id="browse"),
            Tab("播放", id="playback"),
            Tab("最近", id="history"),
            active="playback",
            id="navigation",
        )
        yield Static("", id="playback-heading", markup=False)
        with TabbedContent(initial=self._initial_tab, id="playback-tabs"):
            with TabPane("概览", id="song"):
                with VerticalScroll(id="playback-body"):
                    with Horizontal(id="song-identity"):
                        yield AlbumArtwork(id="playback-artwork")
                        with Vertical(id="song-summary"):
                            yield Static("", id="playback-state", markup=False)
                            yield Static("", id="playback-title", markup=False)
                            yield Static("", id="playback-artist", markup=False)
                            yield Static("", id="playback-settings", markup=False)
                            yield Static("", id="playback-source", markup=False)
                            yield Static("", id="playback-next", markup=False)
                            yield Static("", id="playback-message", markup=False)
            with TabPane("歌词 F4", id="lyrics"):
                yield LyricsPanel(lambda: self.get_state().position)
            with TabPane("队列 F3", id="queue"):
                yield QueuePanel(self.get_queue_state, self.edit_queue)
        with Horizontal(id="transport-row"):
            with Vertical(id="playback-transport"):
                yield Static("", id="playback-time", markup=False)
                yield ProgressBar(
                    total=1, show_percentage=False, show_eta=False, id="playback-progress"
                )
                with Vertical(id="playback-controls"):
                    with Grid(id="transport-buttons"):
                        yield Button("上一首", id="playback-previous", compact=True)
                        yield Button("暂停", id="playback-pause", compact=True)
                        yield Button("下一首", id="playback-next-button", compact=True)
                        yield Button("返回歌曲", id="playback-browse", compact=True)
                    with Grid(id="playback-actions"):
                        yield Button(
                            "音质", id="playback-quality", classes="secondary", compact=True
                        )
                        yield Button(
                            "加入歌单",
                            id="playback-add_to_playlist",
                            classes="secondary",
                            compact=True,
                        )
                        yield Button(
                            "新建歌单",
                            id="playback-create_playlist",
                            classes="secondary",
                            compact=True,
                        )
                        yield Button("重试", id="playback-retry", classes="secondary", compact=True)
        yield Static(
            "F3队列  F4歌词  Esc返回  F9主题  Shift+F9系统", id="playback-keys", markup=False
        )

    def on_mount(self) -> None:
        """Start polling and place keyboard focus on pause or return when idle."""
        self.refresh_playback()
        pause = self.query_one("#playback-pause", Button)
        (pause if not pause.disabled else self.query_one("#playback-browse", Button)).focus()
        if self._initial_tab != "song":
            self.call_after_refresh(self.focus_active_tab)
        self.set_interval(0.25, self.refresh_playback)

    def on_resize(self, event: Resize) -> None:
        """Enlarge spacious artwork and retain dense controls in short terminals."""
        self.set_class(event.size.height < 20, "-compact")
        self.set_class(event.size.width >= 100 and event.size.height >= 30, "-wide")
        self.set_class(event.size.width >= 100, "-inline-controls")
        self.set_class(event.size.width < 48, "-narrow-controls")

    def update_text(self, widget_id: str, text: str) -> None:
        """Refresh text only when its visible value changed."""
        if self._text.get(widget_id) != text:
            self._text[widget_id] = text
            self.query_one(f"#{widget_id}", Static).update(text)

    def refresh_playback(self) -> None:
        """Display real player values while retaining buttons and keyboard focus."""
        state = self.get_state()
        self.query_one(AlbumArtwork).set_cover(state.track.cover_url if state.track else "")
        self.refresh_lyrics()
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
            ("add_to_playlist", state.track is None or not state.can_add_to_playlist),
            ("create_playlist", not state.can_create_playlist),
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
        self.action_dispatch("next" if name == "next-button" else name)

    def action_dispatch(self, name: str) -> None:
        """Delegate shortcuts, including leaving the page before browser navigation."""
        if name == "retry" and self.query_one("#playback-tabs", TabbedContent).active == "lyrics":
            self._lyrics_request += 1
            self.fetch_current_lyrics(self.get_state().track, self._lyrics_request)
        elif name in {"lyrics", "queue"}:
            self.show_tab(name)
        else:
            self.callback(name)

    def show_tab(self, name: str) -> None:
        """Open a listening tab without changing the screen or interrupting playback."""
        self._initial_tab = name
        if not self.is_mounted:
            return
        tabs = self.query_one("#playback-tabs", TabbedContent)
        already_active = tabs.active == name
        tabs.active = name
        if name == "queue" and already_active:
            self.callback("queue")
        self.refresh_lyrics()
        self.call_after_refresh(self.focus_active_tab)

    def focus_active_tab(self) -> None:
        """Put keyboard focus on the active tab's scrollable content."""
        active = self.query_one("#playback-tabs", TabbedContent).active
        if active == "lyrics":
            self.query_one("#lyrics-body", VerticalScroll).focus()
        elif active == "queue":
            self.query_one("#queue-items", DataTable).focus()
        else:
            self.query_one("#playback-body", VerticalScroll).focus()

    @on(TabbedContent.TabActivated, "#playback-tabs")
    def activate_tab(self, event: TabbedContent.TabActivated) -> None:
        """Request the current song on lyric-tab activation and synchronize its position."""
        event.stop()
        self.refresh_lyrics()
        self.query_one(LyricsPanel).sync_position()
        if event.pane.id == "queue":
            self.callback("queue")

    @on(Tabs.TabActivated, "#navigation")
    def navigate(self, event: Tabs.TabActivated) -> None:
        """Delegate navigation to the browser or recent listening page."""
        event.stop()
        if event.tab.id in {"browse", "history"}:
            self.callback(event.tab.id)

    def refresh_lyrics(self) -> None:
        """Fetch changed songs only while lyrics are visible, retaining results across tabs."""
        if self.query_one("#playback-tabs", TabbedContent).active != "lyrics":
            return
        track = self.get_state().track
        if track != self._lyrics_track:
            self._lyrics_track = track
            self._lyrics_request += 1
            self.fetch_current_lyrics(track, self._lyrics_request)

    @work(exclusive=True, group="playback-lyrics")
    async def fetch_current_lyrics(self, track: Track | None, request: int) -> None:
        """Show loading and errors and prevent an older response from replacing a new song."""
        panel = self.query_one(LyricsPanel)
        if track is None:
            panel.query_one("#lyrics-title", Static).update("歌词")
            await panel.show_message("播放歌曲后可查看歌词。")
            return
        panel.query_one("#lyrics-title", Static).update(f"{track.title} · {track.artist}")
        await panel.show_message("正在加载歌词…")
        try:
            lyrics = await self.load_lyrics(track)
        except Exception as error:
            if request == self._lyrics_request:
                if self.get_state().track == track:
                    await panel.show_message(f"歌词加载失败：{error}；按 F5 重试。")
                else:
                    self._lyrics_track = None
            return
        if request == self._lyrics_request:
            if self.get_state().track == track:
                await panel.show_lyrics(track, lyrics)
            else:
                self._lyrics_track = None
