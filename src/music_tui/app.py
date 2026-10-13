"""Keyboard-driven Music TUI for QQ Music and NetEase Cloud Music."""

import argparse
import asyncio
import os
from collections.abc import Callable
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from rich.style import Style
from rich.text import Text
from terminal_qrcode import draw
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    Tab,
    TabbedContent,
    Tabs,
)

from .artists import ArtistPanel, ArtistPicker, ArtistReturn, artist_names
from .history_screen import HistoryEntry, HistoryScreen
from .likes import SearchLikes
from .models import (
    Artist,
    ArtistDetail,
    ArtistOrder,
    LoginState,
    Lyrics,
    Playlist,
    Quality,
    QualityUnavailableError,
    SongPage,
    Track,
)
from .netease_config import load_enhanced_config, load_official_config
from .playback_queue import PlaybackEvents, PlaybackQueue, QueueSelection
from .playback_screen import PlaybackScreen, PlaybackState
from .playback_session import (
    ArtistSongs,
    PlaybackSnapshot,
    PlaybackSource,
    PlaylistSearch,
    QueuedTrack,
    RepeatMode,
    decode_source,
    decode_track,
    encode_source,
)
from .player import Player
from .screens import (
    CreatePlaylistScreen,
    HelpScreen,
    PlaylistPickerScreen,
    QueueItem,
    QueueState,
)
from .session import SessionStore
from .sources import MusicSource
from .sources.netease_openapi import OfficialAPIError
from .sources.qqmusic import QQMusic
from .themes import DAY, NIGHT, detect_system_theme
from .transport import BottomTransport


def timestamp(seconds: float | None) -> str:
    """Format elapsed seconds as a nonnegative minute-and-second timestamp."""
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60:02}:{seconds % 60:02}"


def play_count_text(count: int) -> str:
    """Display a platform-reported playlist play count in a compact sidebar label."""
    if count >= 100_000_000:
        return f"{count / 100_000_000:.1f}亿次"
    if count >= 10_000:
        return f"{count / 10_000:.1f}万次"
    return f"{count}次"


def error_text(error: Exception) -> str:
    # Network exceptions may contain authenticated URLs; keep those out of the UI.
    """Return a user-facing error without exposing authenticated network URLs."""
    if type(error) in (ValueError, RuntimeError) or isinstance(
        error, (OfficialAPIError, QualityUnavailableError)
    ):
        return str(error)
    if isinstance(error, TimeoutError):
        return "请求超时，请重试"
    if isinstance(error, FileNotFoundError):
        return "未找到 mpv，请先安装 mpv"
    return f"请求失败（{type(error).__name__}），请重试；登录失效时按 L 重新扫码"


class LoginScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "返回"), Binding("r", "refresh", "刷新")]
    DEFAULT_CSS = """
    LoginScreen { align: center middle; background: $background 95%; }
    #login-box { width: 58; max-width: 100%; height: auto; max-height: 100%; align: center middle; }
    #login-title, #login-state, #login-keys { width: 100%; height: 1; text-align: center; }
    #login-title { color: $accent; text-style: bold; }
    #login-state { height: auto; }
    #qr-frame { width: 100%; height: auto; }
    #qr { width: auto; height: auto; color: black; background: white; }
    #login-keys { color: $text-muted; }
    """

    def __init__(self, music: MusicSource):
        """Attach the music account used for QR login."""
        super().__init__()
        self.music = music

    def compose(self) -> ComposeResult:
        """Build the QR display, login status, and keyboard instructions."""
        with Vertical(id="login-box"):
            yield Static(f"{self.music.display_name} App 扫码登录", id="login-title")
            with Center(id="qr-frame"):
                yield Static("", id="qr")
            yield Static("正在获取二维码…", id="login-state", markup=False)
            yield Static("R 刷新 · Esc 返回", id="login-keys")

    def on_mount(self) -> None:
        """Start fetching a login QR code when the dialog opens."""
        self.login()

    @work(exclusive=True, group="login")
    async def login(self) -> None:
        """Render a QR code and accept credentials after account confirmation."""
        state = self.query_one("#login-state", Static)
        state.update("正在获取二维码…")
        self.query_one("#qr", Static).update("")
        try:
            session = self.music.login_session()
            async with asyncio.timeout(25):
                qr = await session.get_qrcode()
            # Half blocks use one terminal row for two QR modules; no lossy scaling.
            rendered = draw(qr, renderer="halfblock", fit=False, img_width=512).__rich__()
            if rendered.plain.endswith("\n"):
                rendered = rendered[:-1]
            rendered.style = "black on white"
            self.query_one("#qr", Static).update(rendered)
            rows = len(rendered.plain.splitlines()) + 3
            if self.size.height < rows:
                state.update(f"请将终端增高到 {rows} 行，以完整扫码")
            else:
                state.update(f"请使用 {self.music.display_name} App 扫码")
            async for result in session:
                if result == LoginState.CONFIRMED:
                    state.update(f"已扫码，请在 {self.music.display_name} App 确认")
                elif result == LoginState.DONE:
                    self.dismiss(True)
                    return
                elif result in (LoginState.EXPIRED, LoginState.REFUSED):
                    state.update("二维码已过期或已取消，按 R 重新生成")
                    return
        except Exception as error:
            state.update(error_text(error))

    def action_refresh(self) -> None:
        """Replace the current login attempt with a fresh QR code."""
        self.login()

    def action_cancel(self) -> None:
        """Cancel pending login work and close without accepting credentials."""
        self.workers.cancel_group(self, "login")
        self.dismiss(False)

    def on_unmount(self) -> None:
        """Cancel login polling when the dialog leaves the screen."""
        self.workers.cancel_group(self, "login")


class QualityScreen(ModalScreen[Quality | None]):
    BINDINGS = [Binding("escape", "cancel", "返回")]
    DEFAULT_CSS = """
    QualityScreen { align: center middle; background: $background 95%; }
    #quality-box {
        width: 44;
        max-width: 100%;
        background: $surface;
        color: $foreground;
        height: auto;
        max-height: 100%;
        border: solid $accent;
        padding: 1 2;
    }
    #quality-title, #quality-keys { height: 1; }
    #quality-options { height: 14; margin: 1 0; }
    #quality-title { color: $accent; text-style: bold; }
    #quality-keys { color: $text-muted; }
    #quality-options { background: transparent; }
    #quality-options ListItem { height: 2; padding: 0 1; }
    #quality-options:focus > ListItem.-highlight {
        background: $block-cursor-background;
        color: $accent;
    }
    """

    def __init__(self, qualities: list[Quality], selected: Quality):
        """Store available qualities and the currently selected preference."""
        super().__init__()
        self.qualities = qualities
        self.selected = selected

    def compose(self) -> ComposeResult:
        """Build the quality list with the current preference highlighted."""
        with Vertical(id="quality-box"):
            yield Static("选择播放音质", id="quality-title")
            view = ListView(
                *(ListItem(Label(quality.label)) for quality in self.qualities),
                initial_index=(
                    self.qualities.index(self.selected) if self.selected in self.qualities else 0
                ),
                id="quality-options",
            )
            view.styles.height = min(14, 2 * len(self.qualities))
            yield view
            yield Static("↑/↓ 选择 · Enter 确认 · Esc 返回", id="quality-keys")

    def on_mount(self) -> None:
        """Focus the quality list for immediate keyboard selection."""
        self.query_one(ListView).focus()

    @on(ListView.Selected, "#quality-options")
    def select_quality(self, event: ListView.Selected) -> None:
        """Return the quality selected in the list to the caller."""
        if event.list_view.index is not None:
            self.dismiss(self.qualities[event.list_view.index])

    def action_cancel(self) -> None:
        """Close the dialog without changing the quality preference."""
        self.dismiss(None)


class PlatformScreen(ModalScreen[str | None]):
    """Select a music platform without leaving the running application."""

    BINDINGS = [Binding("escape", "cancel", "返回")]
    OPTIONS = [
        ("qq", "QQ 音乐"),
        ("netease-enhanced", "网易云音乐 · Enhanced（推荐）"),
    ]
    DEFAULT_CSS = """
    PlatformScreen { align: center middle; background: $background 95%; }
    #platform-box {
        background: $surface;
        color: $foreground;
        width: 64;
        max-width: 100%;
        height: auto;
        border: solid $accent;
        padding: 1 2;
    }
    #platform-options { height: 6; margin: 1 0; }
    #platform-box > Static:first-child { color: $accent; text-style: bold; }
    #platform-help { height: auto; color: $text-muted; }
    #platform-options { background: transparent; }
    #platform-options ListItem { height: 2; margin-bottom: 1; padding: 0 1; }
    #platform-options:focus > ListItem.-highlight {
        background: $block-cursor-background;
        color: $accent;
    }
    """

    def __init__(self, selected: str):
        """Highlight the active platform and explain switching behavior."""
        super().__init__()
        self.selected = selected

    def compose(self) -> ComposeResult:
        """Offer supported UI entry points without changing CLI-selected backends."""
        selected_index = next(
            (index for index, (key, _) in enumerate(self.OPTIONS) if key == self.selected),
            None,
        )
        with Vertical(id="platform-box"):
            yield Static("切换音乐平台")
            yield ListView(
                *(ListItem(Label(label, markup=False)) for _, label in self.OPTIONS),
                initial_index=selected_index,
                id="platform-options",
            )
            yield Static(
                "Enhanced：运行 --setup-netease 配置服务。\n"
                "切换后停止播放并清空队列，保留各平台登录凭证。\n"
                "↑/↓ 选择 · Enter 确认 · Esc 返回",
                id="platform-help",
            )

    def on_mount(self) -> None:
        """Focus the platform choices for keyboard navigation."""
        self.query_one(ListView).focus()

    @on(ListView.Selected, "#platform-options")
    def select_platform(self, event: ListView.Selected) -> None:
        """Return the selected source key to the application."""
        if event.list_view.index is not None:
            self.dismiss(self.OPTIONS[event.list_view.index][0])

    def action_cancel(self) -> None:
        """Close the selector while retaining playback and account state."""
        self.dismiss(None)


class PlaylistItem(ListItem):
    """Keep a rendered sidebar row attached to its playlist identity."""

    def __init__(self, playlist: Playlist):
        """Show literal playlist text and retain its source metadata for selection."""
        super().__init__(
            Label(playlist.title, markup=False),
            Label("", classes="playlist-count", markup=False),
            Label("", classes="playlist-plays", markup=False),
        )
        self.playlist = playlist


class SongTable(DataTable):
    BINDINGS = [
        Binding("left", "app.seek(-5)", "后退", show=False),
        Binding("right", "app.seek(5)", "前进", show=False),
        Binding("pageup", "app.previous_page", "上一页", show=False),
        Binding("pagedown", "app.next_page", "下一页", show=False),
    ]

    async def _on_click(self, event: events.Click) -> None:
        """Handle inline artist/like actions without emitting a row-play selection."""
        event.prevent_default()
        if "@click" in event.style.meta:
            event.stop()
            await self.broker_event("click", event)
        else:
            await super()._on_click(event)


class MusicApp(App):
    TITLE = "Music TUI"
    ENABLE_COMMAND_PALETTE = False
    SORT_ORDERS = (
        ("position", False, "原序"),
        ("title", False, "歌曲↑"),
        ("title", True, "歌曲↓"),
        ("artist", False, "歌手↑"),
        ("artist", True, "歌手↓"),
        ("duration", False, "时长↑"),
        ("duration", True, "时长↓"),
    )
    CSS = """
    Screen { background: $background; color: $foreground; }
    #navigation { height: 1; background: $surface; }
    #navigation Underline { display: none; }
    #navigation Tab { padding: 0 2; color: $text-muted; }
    #navigation Tab.-active { color: $accent; text-style: bold; }
    #heading {
        height: 1;
        text-overflow: ellipsis;
        padding: 0 2;
        background: $surface;
        color: $foreground;
    }
    #search { margin: 0 1; height: 3; border: tall $border; background: $background; }
    #search:focus { border: tall $accent; }
    Input > .input--placeholder { color: $text-muted; }
    #content { height: 1fr; margin: 0 1; }
    #library {
        width: 25%;
        min-width: 16;
        max-width: 28;
        border-right: solid $border;
        padding-right: 1;
        margin-right: 1;
    }
    #library-heading { height: 1; }
    #library-title { height: 1; width: 1fr; color: $accent; text-style: bold; padding: 0 1; }
    #create-playlist { width: 6; min-width: 6; height: 1; border: none; padding: 0; }
    #playlist-sort {
        width: 100%; height: 1; border: none; padding: 0 1; content-align: left middle;
    }
    #create-playlist, #playlist-sort { background: $surface; color: $text-muted; }
    #create-playlist:focus, #playlist-sort:focus { color: $accent; text-style: bold; }
    #library-hint { height: 2; color: $text-muted; padding: 0 1; }
    #playlists { height: 1fr; background: transparent; }
    #playlists > ListItem { padding: 0 1; height: 3; margin-bottom: 1; }
    #playlists > ListItem > Label { text-overflow: ellipsis; width: 100%; height: 1; }
    .playlist-count, .playlist-plays { color: $text-muted; }
    #playlists > ListItem.-highlight { background: $surface; }
    #playlists:focus > ListItem.-highlight {
        background: $block-cursor-background;
        border-left: thick $accent;
    }
    #songs-box { width: 1fr; }
    #view-title {
        height: 1;
        background: $surface;
        padding: 0 1;
        color: $foreground;
        text-style: bold;
        text-overflow: ellipsis;
    }
    #view-toolbar { height: 1; }
    #view-meta { height: 1; text-overflow: ellipsis; width: 1fr; padding: 0 1; color: $text-muted; }
    #sort-order { height: 1; width: 18; min-width: 18; border: none; padding: 0 1;
                  background: $surface; color: $text-muted; }
    #sort-order:focus { background: $block-cursor-background; color: $accent; text-style: bold; }
    #filter { height: 3; border: tall $border; background: $background; }
    #filter:focus { border: tall $accent; }
    #search.-compact, #filter.-compact { height: 1; border: none; background: $surface; }
    #songs { height: 1fr; background: transparent; }
    #songs > .datatable--header { background: $surface; color: $text-muted; }
    #songs > .datatable--even-row { background: $background; }
    #songs > .datatable--odd-row { background: $music-row; }
    #songs > .datatable--cursor { background: $surface; }
    #songs:focus > .datatable--cursor { background: $block-cursor-background; color: $foreground; }
    #pagination { height: 1; }
    #pagination Button {
        width: 1fr; min-width: 11; height: 1; border: none; padding: 0 1;
        background: $surface; color: $text-muted;
    }
    #pagination Button:focus {
        background: $block-cursor-background; color: $accent; text-style: bold;
    }
    #player-box { height: 3; padding: 0 2; border-top: solid $border; background: $surface; }
    #playback-line { height: 1; }
    #now-playing { height: 1; text-overflow: ellipsis; color: $accent; text-style: bold; }
    #playback-detail { height: 1; width: 1fr; text-overflow: ellipsis; color: $text-muted; }
    #status { height: 1; text-overflow: ellipsis; padding: 0 2; color: $text-muted; }
    Footer { background: $surface; }
    FooterKey { background: $surface; color: $text-muted; }
    FooterKey > .footer-key--key { background: $surface; color: $accent; }
    """
    BINDINGS = [
        Binding("f1", "help", "帮助", priority=True),
        Binding("f2", "platform", "切换平台", priority=True),
        Binding("f3", "queue", "队列", priority=True),
        Binding("f4", "lyrics", "歌词", priority=True),
        Binding("f5", "retry", "重试", priority=True, show=False),
        Binding("f6", "resume_session", "继续上次", priority=True, show=False),
        Binding("f7", "playback", "播放页", priority=True),
        Binding("f8", "history", "最近播放", priority=True),
        Binding("f9", "appearance", "主题", priority=True),
        Binding("shift+f9", "system_appearance", "跟随系统", priority=True, show=False),
        Binding("ctrl+n", "create_playlist", "新建歌单", priority=True, show=False),
        Binding("a", "enqueue", "下一首播放", show=False),
        Binding("s", "like", "喜欢", show=False),
        Binding("w", "add_to_playlist", "加入歌单", show=False),
        Binding("g", "locate", "定位当前", show=False),
        Binding("i", "artist", "歌手详情", show=False),
        Binding("ctrl+r", "refresh", "刷新", show=False),
        Binding("p", "previous", "上一首", show=False),
        Binding("n", "next", "下一首", show=False),
        Binding("space", "pause", "暂停"),
        Binding("l", "login", "登录", show=False),
        Binding("slash", "search", "搜索", show=False),
        Binding("left_square_bracket", "previous_page", "上一页", show=False),
        Binding("right_square_bracket", "next_page", "下一页", show=False),
        Binding("r", "repeat", "模式", show=False),
        Binding("u", "quality", "音质", show=False),
        Binding("f", "filter", "歌单搜索", show=False),
        Binding("o", "sort", "本页排序", show=False),
        Binding("q", "quit", "退出", show=False),
        Binding("left", "seek(-5)", "后退", show=False),
        Binding("right", "seek(5)", "前进", show=False),
        Binding("minus", "volume(-5)", "音量−", show=False),
        Binding("equal,plus", "volume(5)", "音量+", show=False),
        Binding("escape", "songs", "歌曲", show=False),
    ]

    def __init__(
        self,
        music: MusicSource,
        player: Player,
        *,
        source_factory: Callable[[str], MusicSource] | None = None,
        source_key: str = "qq",
        session_store: SessionStore | None = None,
    ):
        """Initialize library, browsing, playback, and shuffle state."""
        super().__init__()
        self.register_theme(NIGHT)
        self.register_theme(DAY)
        self.appearance = session_store.theme() if session_store is not None else "system"
        self.theme = NIGHT.name if self.appearance == "system" else self.appearance
        self._system_theme_unavailable = False
        self.music = music
        self.player = player
        self.source_factory = source_factory
        self.source_key = source_key
        self.session_store = session_store
        self.switching = False
        self.sort_order = 0
        self.playlist_sort = 0
        self._playlist_render_lock = asyncio.Lock()
        self.reset_library()
        self.ready = False
        self.table_width = 0
        self.apply_preferences()

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Provide the row stripe variable used by the registered appearance themes."""
        return {"music-row": "#141f31"}

    def set_appearance(self, appearance: str) -> None:
        """Remember the user's system or manual appearance preference globally."""
        self.appearance = appearance
        if self.session_store is not None:
            try:
                self.session_store.save_theme(appearance)
            except (OSError, ValueError) as error:
                self.status(f"无法保存界面主题：{error_text(error)}")

    def apply_theme(self, theme: str) -> None:
        """Apply a palette and repaint embedded Rich text without changing selection."""
        if self.theme != theme:
            self.theme = theme
            self.draw_tracks()
            self.now_playing_text = ""
            self.update_player()
        self.update_heading()

    def action_appearance(self) -> None:
        """Select the opposite manual palette while retaining a system-following entry."""
        theme = DAY.name if self.theme == NIGHT.name else NIGHT.name
        self.set_appearance(theme)
        self.apply_theme(theme)

    def action_system_appearance(self) -> None:
        """Restore automatic appearance immediately and persist it for subsequent launches."""
        self.set_appearance("system")
        self.update_system_theme()

    @work(exclusive=True, group="system-appearance")
    async def update_system_theme(self) -> None:
        """Follow native appearance changes without overriding a later manual selection."""
        if self.appearance != "system":
            return
        theme = await detect_system_theme()
        if not self.is_running or self.appearance != "system":
            return
        if theme is None:
            self._system_theme_unavailable = True
            self.update_heading()
            return
        self._system_theme_unavailable = False
        self.apply_theme(theme)

    def reset_library(self) -> None:
        """Clear platform-specific account, browsing, queue and quality state."""
        self.playlists: list[Playlist] = []
        self.displayed_playlists: list[Playlist] = []
        self.tracks: list[Track] = []
        self.source: PlaybackSource = None
        self.artist_returns: list[ArtistReturn] = []
        self.artist_detail: ArtistDetail | None = None
        self.failed_artist_view: tuple[ArtistSongs, int] | None = None
        self.search_likes: SearchLikes | None = None
        self._view_generation = getattr(self, "_view_generation", 0) + 1
        self.page = 1
        self.has_more = False
        self.total = 0
        self.page_size = 100
        self.playlist_counts: dict[Playlist, int] = {}
        self.playlist_cache: dict[Playlist, tuple[Track, ...]] = {}
        self.playback_queue = PlaybackQueue()
        self.current: Track | None = None
        self.playback_track: Track | None = None
        self.playback_origin: QueuedTrack | None = None
        self.playback_position = 0.0
        self.playback_paused = False
        self.playback_volume = 70.0
        self.library_busy = False
        self.last_status_message = "按 L 扫码登录以读取个人歌单"
        self.session_saved_at = 0.0
        self.loading = False
        self.playback_events = PlaybackEvents()
        self.now_playing_text = "尚未播放"
        self.quality = Quality.STANDARD
        self.current_quality: Quality | None = None
        self.account_logged_in = False
        self.active_account_id = ""
        self.repeat_mode = RepeatMode.SEQUENCE

    def apply_preferences(self) -> None:
        """Restore the selected platform's quality, mode and volume preferences."""
        if self.session_store is None:
            return
        self.quality, self.repeat_mode, self.playback_volume = self.read_preferences(
            self.source_key
        )

    def read_preferences(self, source_key: str) -> tuple[Quality, RepeatMode, float]:
        """Validate a platform's saved settings before transferring source ownership."""
        if self.session_store is None:
            return Quality.STANDARD, RepeatMode.SEQUENCE, 70.0
        preferences = self.session_store.load_preferences(source_key)
        try:
            return (
                Quality[preferences.get("quality", "STANDARD")],
                RepeatMode[preferences.get("repeat_mode", "SEQUENCE")],
                max(0, min(130, float(preferences.get("volume", 70)))),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("保存的播放偏好无效") from error

    def remember_playback(self) -> None:
        """Keep a recovery checkpoint while the active song has valid player properties."""
        if self.current is not None and not self.loading:
            position = self.player.properties.get("time-pos")
            if position is not None:
                self.playback_position = float(position)
            self.playback_paused = bool(self.player.properties.get("pause"))
        if self.ready:
            self.playback_volume = float(self.player.properties.get("volume", 70) or 0)

    def save_state(self) -> None:
        """Save preferences and the account's queue checkpoint without audio URLs."""
        if self.session_store is None:
            return
        try:
            self.remember_playback()
            self.session_store.set_last_source(self.source_key)
            self.session_store.save_preferences(
                self.source_key,
                {
                    "quality": self.quality.name,
                    "repeat_mode": self.repeat_mode.name,
                    "volume": self.playback_volume,
                },
            )
            if self.playback_track is None or self.loading:
                return

            snapshot = self.playback_queue.snapshot(
                self.playback_track,
                self.playback_position,
                self.playback_paused,
                self.playback_origin,
            )
            self.session_store.save_session(
                self.source_key, self.active_account_id, snapshot.encode()
            )
        except (OSError, ValueError) as error:
            self.status(f"无法保存播放状态：{error_text(error)}")

    def on_unmount(self) -> None:
        """Checkpoint the current account before application resources are closed."""
        self.save_state()

    def compose(self) -> ComposeResult:
        """Build the search, playlist, song table, and playback interface."""
        yield Static(
            f"Music TUI · {self.music.display_name}   未登录   "
            f"{self.quality.label}   {self.repeat_mode.value}",
            id="heading",
            markup=False,
        )
        yield Tabs(
            Tab("歌曲", id="browse"),
            Tab("播放", id="playback"),
            Tab("最近", id="history"),
            active="browse",
            id="navigation",
        )
        yield Input(placeholder="搜索歌曲或歌手 · / 聚焦 · Enter 搜索", id="search")
        with Horizontal(id="content"):
            with Vertical(id="library"):
                with Horizontal(id="library-heading"):
                    yield Static("我的歌单", id="library-title")
                    yield Button("新建", id="create-playlist")
                yield Button("排序：默认", id="playlist-sort")
                yield ListView(id="playlists")
                yield Static("L 账号\nCtrl+R 刷新", id="library-hint", markup=False)
            with Vertical(id="songs-box"):
                yield ArtistPanel(id="artist-panel")
                yield Static("选择歌单，或搜索一首歌", id="view-title", markup=False)
                with Horizontal(id="view-toolbar"):
                    yield Static("尚未加载列表", id="view-meta", markup=False)
                    yield Button("O 本页：原序", id="sort-order")
                field = Input(placeholder="F 歌单内搜索歌名或歌手，回车筛选", id="filter")
                field.display = False
                yield field
                yield SongTable(id="songs", cursor_type="row", zebra_stripes=True)
                with Horizontal(id="pagination"):
                    yield Button(
                        "[ 上一页",
                        id="previous-page",
                        compact=True,
                        disabled=True,
                        tooltip="按 [ 或在歌曲列表按 PageUp，查看上一页",
                    )
                    yield Button(
                        "下一页 ]",
                        id="next-page",
                        compact=True,
                        disabled=True,
                        tooltip="按 ] 或在歌曲列表按 PageDown，查看下一页",
                    )
        with Vertical(id="player-box"):
            yield Static("尚未播放", id="now-playing", markup=False)
            with Horizontal(id="playback-line"):
                yield Static("选中歌曲后按 Enter 播放", id="playback-detail", markup=False)
                yield BottomTransport(self.get_playback_state, self.handle_playback_action)
        yield Static(self.last_status_message, id="status", markup=False)
        yield Footer(compact=True)

    def on_mount(self) -> None:
        """Initialize song columns, playback polling, and account restoration."""
        table = self.query_one("#songs", DataTable)
        table.add_column("序", key="position", width=6)
        table.add_column("歌曲", key="title")
        table.add_column("歌手", key="artist")
        table.add_column("时长", key="duration", width=5)
        table.focus()
        self.set_interval(0.25, self.update_player)
        self.set_interval(2, self.update_system_theme)
        self.update_system_theme()
        self.sync_playlist_actions()
        self.start_player()
        self.restore_login()

    def on_resize(self) -> None:
        """Schedule song-column sizing after the new layout is rendered."""
        if self.is_mounted:
            self.call_after_refresh(self.resize_columns)

    def resize_columns(self) -> None:
        """Fit song columns and compact text fields so short terminals retain usable rows."""
        table = self.query_one("#songs", DataTable)
        for selector in ("#search", "#filter"):
            self.query_one(selector, Input).set_class(self.size.height < 20, "-compact")
        width = table.size.width
        if width == self.table_width or width < 20:
            return
        self.table_width = width
        row = table.cursor_row
        selected_key = table.ordered_rows[row].key if 0 <= row < table.row_count else None
        table.clear(columns=True)
        available = width - 21
        artist_width = max(8, available // 3)
        table.add_column("序", key="position", width=6)
        table.add_column("歌曲", key="title", width=max(8, available - artist_width))
        table.add_column("歌手", key="artist", width=artist_width)
        table.add_column("时长", key="duration", width=5)
        if self.source is not None:
            self.draw_tracks()
            if selected_key in table.rows:
                table.move_cursor(row=table.get_row_index(selected_key))

    def status(self, message: str) -> None:
        """Replace the status-line message without changing the active view."""
        self.last_status_message = message
        self.query_one("#status", Static).update(message)

    @work
    async def start_player(self) -> None:
        """Start mpv and mark playback ready after successful initialization."""
        try:
            await self.player.start()
            self.ready = True
            if self.session_store is not None:
                await self.player.command("set_property", "volume", self.playback_volume)
        except Exception as error:
            self.status(error_text(error))

    @work(group="restore")
    async def restore_login(self) -> None:
        """Restore saved credentials and refresh the library when valid."""
        try:
            if await self.music.restore():
                self.logged_in(True)
        except Exception as error:
            self.status(error_text(error))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Keep application controls idle while platform ownership changes."""
        if self.switching:
            return action == "quit"
        closable = {
            "help": HelpScreen,
            "history": HistoryScreen,
        }
        if action in closable and isinstance(self.screen, closable[action]):
            return False
        if isinstance(self.screen, (PlaybackScreen, HistoryScreen, ModalScreen)) and action in (
            "enqueue",
            "like",
            "previous_page",
            "next_page",
        ):
            return False
        if action == "add_to_playlist" and isinstance(self.screen, (HistoryScreen, ModalScreen)):
            return False
        return True

    def action_platform(self) -> None:
        """Open the platform menu from browsing or a focused search field."""
        if self.switching or isinstance(self.screen, ModalScreen):
            return
        self.workers.cancel_group(self, "qualities")
        self.push_screen(PlatformScreen(self.source_key), self.choose_platform)

    def choose_platform(self, key: str | None) -> None:
        """Start a switch only when the user chooses a different platform."""
        if key is not None and key != self.source_key and not self.switching:
            self.switch_platform(key)

    @work(group="platform", exclusive=True)
    async def switch_platform(self, key: str) -> None:
        """Validate the new account, drain old work, and transfer source ownership."""
        if key == self.source_key or self.source_factory is None or self.switching:
            return
        self.switching = True
        candidate = None
        previous = None
        try:
            candidate = self.source_factory(key)
            self.status(f"正在切换到 {candidate.display_name}…")
            async with asyncio.timeout(25):
                logged_in = await candidate.restore()
            preferences = self.read_preferences(key)
            pending = []
            for group in (
                "restore",
                "library",
                "view",
                "play",
                "qualities",
                "control",
                "lyrics",
                "library-action",
                "queue-view",
                "account",
                "search-likes",
            ):
                pending.extend(self.workers.cancel_group(self, group))
            await asyncio.gather(*(worker.wait() for worker in pending), return_exceptions=True)
            self.save_state()
            if self.ready:
                await self.player.command("stop")
            previous = self.music
            self.music, candidate = candidate, None
            self.source_key = key
            self.return_to_browse()
            self.reset_library()
            self.query_one(ArtistPanel).display = False
            self.quality, self.repeat_mode, self.playback_volume = preferences
            if self.session_store is not None and self.ready:
                await self.player.command("set_property", "volume", self.playback_volume)
            self.account_logged_in = logged_in
            self.sync_playlist_actions()
            self.active_account_id = getattr(self.music, "account_id", "") if logged_in else ""
            self.query_one("#search", Input).value = ""
            field = self.query_one("#filter", Input)
            field.value = ""
            field.display = False
            await self.draw_playlists()
            self.query_one("#songs", DataTable).clear()
            self.query_one("#view-title", Static).update("选择歌单，或搜索一首歌")
            self.query_one("#view-meta", Static).update("尚未加载列表")
            self.sync_pagination()
            self.update_heading()
            self.status(f"已切换到 {self.music.display_name} · L 登录 · / 搜索 · F2 切换平台")
            if logged_in:
                self.load_playlists()
        except Exception as error:
            message = error_text(error)
            self.status(message)
            self.notify(message, title="平台切换", severity="error", timeout=12)
        finally:
            if previous is not None:
                await previous.close()
            if candidate is not None:
                await candidate.close()
            self.switching = False
            self.update_player()

    def action_login(self) -> None:
        """Open QR login after cancelling concurrent credential restoration."""
        if isinstance(self.screen, ModalScreen):
            return
        if self.library_busy:
            self.status("歌单操作尚未完成，请稍后登录")
            return
        self.workers.cancel_group(self, "restore")
        self.save_state()
        self.push_screen(LoginScreen(self.music), self.logged_in)

    @work(exclusive=True, group="account")
    async def logged_in(self, success: bool) -> None:
        """Update account state and refresh playlists after successful login."""
        if success:
            account_id = getattr(self.music, "account_id", "")
            if self.active_account_id and account_id != self.active_account_id:
                self.switching = True
                try:
                    self.save_state()
                    pending = []
                    for group in (
                        "library",
                        "view",
                        "play",
                        "qualities",
                        "control",
                        "lyrics",
                        "library-action",
                        "queue-view",
                    ):
                        pending.extend(self.workers.cancel_group(self, group))
                    await asyncio.gather(
                        *(worker.wait() for worker in pending), return_exceptions=True
                    )
                    if self.ready:
                        await self.player.command("stop")
                    self.return_to_browse()
                    self.reset_library()
                    self.apply_preferences()
                    await self.draw_playlists()
                    self.query_one("#songs", DataTable).clear()
                    self.query_one("#search", Input).value = ""
                    field = self.query_one("#filter", Input)
                    field.value = ""
                    field.display = False
                    self.query_one("#view-title", Static).update("选择歌单，或搜索一首歌")
                    self.query_one("#view-meta", Static).update("尚未加载列表")
                    self.sync_pagination()
                finally:
                    self.switching = False
            self.account_logged_in = True
            self.sync_playlist_actions()
            self.active_account_id = account_id
            if self.search_likes is not None:
                self.search_likes.bind(self.music)
            self.update_heading()
            self.load_playlists()
            if self.session_store is not None:
                try:
                    if self.session_store.load_session(self.source_key, self.active_account_id):
                        self.notify("按 F6 继续上次播放", title="已保存的播放会话", timeout=6)
                except ValueError as error:
                    self.status(str(error))

    @work(exclusive=True, group="library")
    async def load_playlists(self, *, message: str | None = None) -> None:
        """Refresh account playlists and open the first one for an empty view."""
        self.status("正在加载歌单…")
        try:
            self.playlists = await self.music.playlists()
            await self.draw_playlists()
            self.status(message or "歌单已更新 · Tab 切换区域 · Enter 打开歌单或播放歌曲")
            if self.source is None and self.playlists:
                self.load_view(self.playlists[0])
        except Exception as error:
            self.status(
                f"{message}；歌单刷新失败：{error_text(error)}" if message else error_text(error)
            )

    @on(ListView.Selected, "#playlists")
    def select_playlist(self, event: ListView.Selected) -> None:
        """Open the playlist selected in the library sidebar."""
        if self.switching:
            return
        if isinstance(event.item, PlaylistItem) and event.item.parent is event.list_view:
            self.load_view(event.item.playlist)

    @on(Input.Submitted, "#search")
    def search_submitted(self, event: Input.Submitted) -> None:
        """Open global search results for a nonempty submitted query."""
        if self.switching:
            return
        if keyword := event.value.strip():
            self.load_view(keyword)

    async def fetch(self, source: PlaybackSource, page: int) -> SongPage:
        """Fetch a page from global search, a playlist, or cached playlist matches."""
        if isinstance(source, ArtistSongs):
            return await self.music.artist_songs(source.artist, source.order, page)
        if isinstance(source, PlaylistSearch):
            start = (page - 1) * 100
            return SongPage(
                list(source.tracks[start : start + 100]),
                start + 100 < len(source.tracks),
                len(source.tracks),
            )
        if isinstance(source, str):
            return await self.music.search(source, page)
        return await self.music.songs(source, page)

    @work(exclusive=True, group="view")
    async def load_view(self, source: PlaybackSource, *, page: int = 1) -> None:
        """Fetch a song page and report failures in the status line."""
        self.status("正在加载歌曲…")
        if not isinstance(source, ArtistSongs):
            self.artist_returns.clear()
        self._view_generation += 1
        generation = self._view_generation
        try:
            result = await self.fetch(source, page)
            if generation != self._view_generation:
                return
            self.display_page(source, page, result)
            if isinstance(source, ArtistSongs):
                await self.load_artist_detail(source, generation)
        except Exception as error:
            if generation == self._view_generation:
                if isinstance(source, ArtistSongs):
                    self.failed_artist_view = source, page
                self.status(error_text(error))

    def display_page(self, source: PlaybackSource, page: int, result: SongPage) -> None:
        """Apply page metadata, playlist counts, and song-table focus."""
        if isinstance(source, Playlist) and page == 1:
            self.playlist_cache.pop(source, None)
        self.source, self.page, self.has_more = source, page, result.has_more
        self.failed_artist_view = None
        self.tracks, self.total, self.page_size = result.tracks, result.total, result.page_size
        panel = self.query_one(ArtistPanel)
        panel.display = isinstance(source, ArtistSongs)
        if isinstance(source, ArtistSongs):
            if self.artist_detail is None or self.artist_detail.artist.id != source.artist.id:
                self.artist_detail = ArtistDetail(source.artist)
            panel.show_detail(self.artist_detail)
            self.query_one("#artist-order", Button).label = f"排序：{source.order.label} ↔"
            self.query_one("#artist-back", Button).disabled = not self.artist_returns
        field = self.query_one("#filter", Input)
        field.display = isinstance(source, (Playlist, PlaylistSearch))
        field.value = source.keyword if isinstance(source, PlaylistSearch) else ""
        playlist = source.playlist if isinstance(source, PlaylistSearch) else source
        if isinstance(playlist, Playlist):
            total = source.playlist_total if isinstance(source, PlaylistSearch) else result.total
            self.playlist_counts[playlist] = total
            self.update_playlist_counts()
        self.draw_tracks()
        self.query_one("#songs", DataTable).move_cursor(row=0)
        self.query_one("#songs", DataTable).focus()
        self.status(
            ""
            if result.tracks
            else (
                "没有找到歌曲；清空搜索框并回车可返回完整歌单"
                if isinstance(source, PlaylistSearch)
                else "没有找到歌曲"
            )
        )
        if isinstance(source, str) and self.account_logged_in:
            self.refresh_search_likes()

    def update_playlist_counts(self) -> None:
        """Refresh sidebar counts from loaded metadata or playlist defaults."""
        view = self.query_one("#playlists", ListView)
        for item in view.query(PlaylistItem):
            playlist = item.playlist
            count = self.playlist_counts.get(playlist, playlist.song_count)
            item.query_one(".playlist-count", Label).update(
                f"{count} 首" if count is not None else "歌曲数未知"
            )
            item.query_one(".playlist-plays", Label).update(
                play_count_text(playlist.play_count)
                if playlist.play_count is not None
                else "播放量未知"
            )

    async def draw_playlists(self) -> None:
        """Sort the complete sidebar snapshot while retaining the selected playlist identity."""
        async with self._playlist_render_lock:
            view = self.query_one("#playlists", ListView)
            selected = (
                self.displayed_playlists[view.index].id
                if view.index is not None and view.index < len(self.displayed_playlists)
                else None
            )
            entries = list(self.playlists)
            if self.playlist_sort in (1, 2):
                direction = -1 if self.playlist_sort == 1 else 1
                entries.sort(
                    key=lambda playlist: (
                        playlist.play_count is None,
                        direction * (playlist.play_count or 0),
                    )
                )
            elif self.playlist_sort == 3:
                entries.sort(key=lambda playlist: playlist.title.casefold())
            self.displayed_playlists = entries
            await view.clear()
            await view.extend(PlaylistItem(playlist) for playlist in entries)
            view.index = next(
                (index for index, playlist in enumerate(entries) if playlist.id == selected),
                0 if entries else None,
            )
            self.query_one("#playlist-sort", Button).label = (
                "排序：默认",
                "播放量↓",
                "播放量↑",
                "名称↑",
            )[self.playlist_sort]
            self.update_playlist_counts()

    @on(Button.Pressed, "#playlist-sort")
    async def sort_playlists(self) -> None:
        """Cycle whole-library ordering using reported counts, keeping unknown counts last."""
        if self.switching:
            return
        self.playlist_sort = (self.playlist_sort + 1) % 4
        await self.draw_playlists()

    def sync_playlist_actions(self) -> None:
        """Expose creation only for a logged-in account and a supported music source."""
        button = self.query_one("#create-playlist", Button)
        supported = getattr(self.music, "supports_playlist_creation", False)
        button.disabled = not self.account_logged_in or not supported
        button.tooltip = (
            "Ctrl+N 新建歌单"
            if supported and self.account_logged_in
            else "请先登录" if supported else "当前接入不支持新建歌单"
        )

    @on(Button.Pressed, "#create-playlist")
    def press_create_playlist(self) -> None:
        """Open the name form from the sidebar's visible creation entry."""
        self.action_create_playlist()

    def action_create_playlist(self, track: Track | None = None) -> None:
        """Ask for a playlist name, optionally retaining the song to add after creation."""
        if self.switching or self.library_busy or isinstance(self.screen, ModalScreen):
            return
        if not self.account_logged_in:
            self.status("请先按 L 登录，再新建歌单")
            return
        if not getattr(self.music, "supports_playlist_creation", False):
            self.status("当前接入不支持新建歌单")
            return
        self.push_screen(
            CreatePlaylistScreen(),
            lambda title: self.create_playlist(title, track),
        )

    @work(group="library-action")
    async def create_playlist(self, title: str | None, track: Track | None = None) -> None:
        """Create a real platform playlist and report creation and optional addition separately."""
        if title is None or self.library_busy or self.switching:
            return
        self.library_busy = True
        self.status(f"正在新建歌单：{title}")
        try:
            playlist = await self.music.create_playlist(title)
        except Exception as error:
            self.status(f"新建歌单失败：{error_text(error)}")
        else:
            message = f"已新建歌单：{playlist.title}"
            if track is not None:
                try:
                    await self.music.add_to_playlist(track, playlist)
                except Exception as error:
                    message += f"；歌曲未加入：{error_text(error)}"
                else:
                    message += f"，已加入：{track.title}"
            self.playlist_cache.clear()
            self.load_playlists(message=message)
        finally:
            self.library_busy = False

    def choose_playlist_destination(self, track: Track, choice: Playlist | str | None) -> None:
        """Keep the chosen song when selecting an existing or new destination."""
        if choice == "create":
            self.action_create_playlist(track)
        elif isinstance(choice, Playlist):
            self.add_selected_to_playlist(track, choice)

    @on(Input.Submitted, "#filter")
    def filter_submitted(self, event: Input.Submitted) -> None:
        """Search the open playlist or restore its full list for an empty query."""
        if self.switching:
            return
        source = self.source
        playlist = source.playlist if isinstance(source, PlaylistSearch) else source
        if isinstance(playlist, Playlist):
            keyword = event.value.strip()
            if keyword:
                self.filter_playlist(playlist, keyword)
            else:
                self.load_view(playlist)

    @work(exclusive=True, group="view")
    async def filter_playlist(self, playlist: Playlist, keyword: str) -> None:
        """Cache the full playlist and search titles and artists case-insensitively."""
        try:
            if playlist not in self.playlist_cache:
                tracks: list[Track] = []
                page, more = 1, True
                while more:
                    self.status(f"正在搜索整个歌单… 已读取 {len(tracks)} 首")
                    result = await self.fetch(playlist, page)
                    tracks.extend(result.tracks)
                    self.playlist_counts[playlist] = result.total
                    more = result.has_more and bool(result.tracks)
                    page += 1
                self.playlist_cache[playlist] = tuple(tracks)
            text = keyword.casefold()
            matches = tuple(
                track
                for track in self.playlist_cache[playlist]
                if text in track.title.casefold() or text in track.artist.casefold()
            )
            source = PlaylistSearch(playlist, keyword, matches, self.playlist_counts[playlist])
            self.display_page(source, 1, await self.fetch(source, 1))
        except Exception as error:
            self.status(error_text(error))

    def action_filter(self) -> None:
        """Focus playlist search when the current view supports it."""
        self.return_to_browse()
        field = self.query_one("#filter", Input)
        if field.display:
            field.focus()
        else:
            self.status("请先打开一个歌单，再按 F 搜索歌单内歌曲")

    def action_artist(self) -> None:
        """Open the chosen song's exact artist, offering keyboard choice for duets."""
        if isinstance(self.screen, ModalScreen) or self.switching:
            return
        track = (
            self.quality_track()
            if isinstance(self.screen, PlaybackScreen)
            else self.selected_track()
        )
        if track is None or not track.artists:
            self.status("这首歌曲没有可用的歌手信息")
            return
        if len(track.artists) == 1:
            artist = track.artists[0]
            self.action_open_artist(artist.id, artist.name)
        else:
            self.push_screen(ArtistPicker(track.artists), self.choose_artist)

    def choose_artist(self, artist: Artist | None) -> None:
        """Navigate after a contributor is selected without starting audio."""
        if artist is not None:
            self.action_open_artist(artist.id, artist.name)

    def action_open_artist(self, identity: str, name: str) -> None:
        """Snapshot the browser before navigating from any clickable artist name."""
        if self.switching or isinstance(self.screen, ModalScreen):
            return
        table = self.query_one("#songs", DataTable)
        screen = "playback" if isinstance(self.screen, PlaybackScreen) else "browse"
        tab = (
            self.screen.query_one("#playback-tabs", TabbedContent).active
            if screen == "playback"
            else "song"
        )
        self.artist_returns.append(
            ArtistReturn(
                self.source,
                self.page,
                SongPage(list(self.tracks), self.has_more, self.total, self.page_size),
                self.sort_order,
                table.cursor_row,
                table.scroll_y,
                self.focused.id if self.focused else None,
                screen,
                tab,
                self.artist_detail,
            )
        )
        self.return_to_browse()
        self.sort_order = 0
        source = ArtistSongs(Artist(identity, name))
        self.display_page(source, 1, SongPage([], False, 0, 30))
        self.load_view(source)

    async def load_artist_detail(self, source: ArtistSongs, generation: int) -> None:
        """Enrich a loaded song list without letting optional profile failure hide songs."""
        try:
            detail = await self.music.artist_detail(source.artist)
        except Exception as error:
            if generation == self._view_generation:
                self.status(f"歌曲已加载；歌手简介加载失败：{error_text(error)}")
        else:
            if generation == self._view_generation and source == self.source:
                self.artist_detail = detail
                self.query_one(ArtistPanel).show_detail(detail)

    @on(Button.Pressed, "#artist-order")
    def change_artist_order(self) -> None:
        """Reload the whole artist order from page one without replacing playback."""
        if isinstance(self.source, ArtistSongs):
            order = ArtistOrder.LATEST if self.source.order is ArtistOrder.HOT else ArtistOrder.HOT
            self.sort_order = 0
            self.load_view(ArtistSongs(self.source.artist, order))

    @on(Button.Pressed, "#artist-back")
    def action_artist_back(self) -> None:
        """Restore the captured browser and listening page without network refetch."""
        if not self.artist_returns:
            return
        self._view_generation += 1
        self.workers.cancel_group(self, "view")
        state = self.artist_returns.pop()
        self.artist_detail = state.detail
        self.sort_order = state.sort_order
        self.display_page(state.source, state.page, state.result)
        table = self.query_one("#songs", DataTable)
        table.move_cursor(row=state.row)
        table.scroll_to(y=state.scroll_y, animate=False)
        if state.source is None:
            self.query_one("#view-title", Static).update("选择歌单，或搜索一首歌")
            self.query_one("#view-meta", Static).update("尚未加载列表")
        if state.screen == "playback":
            self.open_playback().show_tab(state.playback_tab)
        if state.focus_id:
            self.call_after_refresh(self.restore_artist_focus, state.focus_id)

    def restore_artist_focus(self, identity: str) -> None:
        """Restore a focus target only if it exists on the restored screen."""
        targets = self.screen.query(f"#{identity}")
        if targets:
            targets.first().focus()

    @work(group="search-likes", exclusive=True)
    async def refresh_search_likes(self) -> None:
        """Fetch account likes in one query and repaint only the current search."""
        if self.search_likes is None:
            self.search_likes = SearchLikes(self.music)
        else:
            self.search_likes.bind(self.music)
        likes = self.search_likes
        try:
            updated = await likes.refresh()
        except Exception as error:
            if likes is self.search_likes and isinstance(self.source, str):
                self.status(f"喜欢状态查询失败：{error_text(error)} · Ctrl+R 重试")
        else:
            if updated and likes is self.search_likes and isinstance(self.source, str):
                self.draw_tracks()

    def action_toggle_like(self, identity: str) -> None:
        """Toggle the actual clicked search song without selecting or playing its row."""
        track = next((track for track in self.tracks if track.mid == identity), None)
        if track is not None and isinstance(self.source, str):
            self.action_like(track)

    def draw_tracks(self) -> None:
        """Sort the visible page while retaining source indexes and the selected song."""
        table = self.query_one("#songs", DataTable)
        row = table.cursor_row
        selected_key = table.ordered_rows[row].key if 0 <= row < table.row_count else None
        table.clear()
        column, descending, label = self.SORT_ORDERS[self.sort_order]
        entries = list(enumerate(self.tracks))
        if column != "position":
            attribute = {"title": "title", "artist": "artist", "duration": "seconds"}[column]
            entries.sort(
                key=lambda entry: (
                    getattr(entry[1], attribute).casefold()
                    if attribute != "seconds"
                    else entry[1].seconds
                ),
                reverse=descending,
            )
        for key, title in (
            ("position", "序"),
            ("title", "歌曲"),
            ("artist", "歌手"),
            ("duration", "时长"),
        ):
            direction = "↓" if descending else "↑"
            table.columns[key].label = Text(
                title + (direction if key == column and column != "position" else "")
            )
        self.query_one("#sort-order", Button).label = f"O 本页：{label}"
        for index, track in entries:
            title = Text()
            if isinstance(self.source, str):
                state = self.search_likes.state(track) if self.search_likes else None
                symbol = "?" if state is None else "♥" if state else "♡"
                title.append(
                    symbol,
                    Style(
                        color=self.get_css_variables()["accent"],
                        meta={"@click": f"app.toggle_like({track.mid!r})"},
                    ),
                )
                title.append(" ")
            title.append(track.title)
            artist = artist_names(track)
            artist.style = self.get_css_variables()["text-muted"]
            table.add_row(
                self.track_number(index, track),
                title,
                artist,
                Text(timestamp(track.seconds), style=self.get_css_variables()["text-muted"]),
                key=str(index),
            )
        if selected_key in table.rows:
            row = table.get_row_index(selected_key)
        table.move_cursor(row=min(row, max(0, len(self.tracks) - 1)))
        if self.source is None:
            return
        if isinstance(self.source, ArtistSongs):
            title = f"歌手：{self.source.artist.name} · {self.source.order.label}"
            count = f"共 {self.total} 首"
        elif isinstance(self.source, PlaylistSearch):
            title = self.source.playlist.title
            count = f"共 {self.source.playlist_total} 首 / 匹配 {self.total} 首"
        else:
            title = (
                self.source.title if isinstance(self.source, Playlist) else f"搜索：{self.source}"
            )
            count = f"共 {self.total} 首"
        pages = max(1, (self.total + self.page_size - 1) // self.page_size)
        self.query_one("#view-title", Static).update(title)
        self.query_one("#view-meta", Static).update(f"{count}  第 {self.page} 页/{pages}")
        self.sync_pagination()

    def sync_pagination(self) -> None:
        """Disable unavailable page directions after loading or clearing a browsing source."""
        self.query_one("#previous-page", Button).disabled = self.source is None or self.page <= 1
        self.query_one("#next-page", Button).disabled = self.source is None or not self.has_more

    @on(Button.Pressed, "#previous-page")
    def previous_page(self) -> None:
        """Use the visible previous-page button to navigate the browsing list."""
        self.action_previous_page()

    @on(Button.Pressed, "#next-page")
    def next_page(self) -> None:
        """Use the visible next-page button to navigate the browsing list."""
        self.action_next_page()

    @on(DataTable.HeaderSelected, "#songs")
    def sort_header(self, event: DataTable.HeaderSelected) -> None:
        """Toggle a clicked column's direction; clicking sequence restores source order."""
        column = event.column_key.value
        current_column, descending, _ = self.SORT_ORDERS[self.sort_order]
        descending = not descending if column == current_column else False
        self.sort_order = next(
            index
            for index, (key, reverse, _) in enumerate(self.SORT_ORDERS)
            if key == column and (key == "position" or reverse == descending)
        )
        self.draw_tracks()

    @on(Button.Pressed, "#sort-order")
    def cycle_sort(self) -> None:
        """Cycle the visible page's sort order from the inline button."""
        self.action_sort()

    def action_sort(self) -> None:
        """Cycle page sorting without changing the loaded source or playback queue."""
        self.return_to_browse()
        self.sort_order = (self.sort_order + 1) % len(self.SORT_ORDERS)
        self.draw_tracks()

    @on(DataTable.RowSelected, "#songs")
    def select_track(self, event: DataTable.RowSelected) -> None:
        """Snapshot the displayed page as the queue and play the selected row."""
        if self.switching:
            return
        if not self.ready:
            self.status("播放器尚未就绪")
            return
        index = int(event.row_key.value)
        self.playback_queue.replace(self.tracks, self.source, self.page, self.has_more, index)
        self.workers.cancel_group(self, "queue-view")
        self.loading = True
        self.play_index(index)

    @work(exclusive=True, group="play")
    async def play_index(self, index: int) -> None:
        """Play an explicit source position through the queue's navigation interface."""
        self.loading = True
        try:
            selection = await self.playback_queue.select(index, self.repeat_mode, self.fetch)
            await self.load_selection(selection)
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试 · N 下一首")
        finally:
            self.loading = False

    async def load_selection(self, selection: QueueSelection, *, resume: bool = False) -> None:
        """Load a queue result or stop a finished list without interpreting navigation rules."""
        if selection.ended:
            await self.player.command("stop")
            self.current = None
            self.current_quality = None
            self.playback_track = None
            self.playback_origin = None
            self.status("播放队列已结束")
        elif selection.entry is not None:
            await self.load_track(
                selection.entry.track, self.quality, resume=resume, origin=selection.entry
            )

    async def load_track(
        self,
        track: Track,
        quality: Quality,
        *,
        resume: bool = False,
        origin: QueuedTrack | None = None,
    ) -> None:
        """Play the first authorized tier at or below the preference, retaining recovery state."""
        if origin is not None:
            self.playback_origin = origin
        if resume and self.current is not None:
            self.playback_position = float(self.player.properties.get("time-pos") or 0)
            self.playback_paused = bool(self.player.properties.get("pause"))
        if not resume:
            self.current = None
            self.current_quality = None
            self.playback_track = track
            self.playback_position = 0
            self.playback_paused = False
            await self.player.command("stop")
        self.playback_track = track
        tiers = list(Quality)
        unavailable_reason = ""
        for actual_quality in tiers[tiers.index(quality) :]:
            self.status(f"正在获取：{track.title} · {actual_quality.label}")
            try:
                url = await self.music.stream_url(track, actual_quality)
                break
            except QualityUnavailableError as error:
                unavailable_reason = error_text(error)
        else:
            raise QualityUnavailableError(
                f"《{track.title}》播放失败：{unavailable_reason} · 已尝试至标准音质"
            )
        await self.player.load(
            url,
            start=self.playback_position if resume else 0,
            paused=self.playback_paused if resume else False,
        )
        self.current = track
        self.current_quality = actual_quality

        self.mark_playing_track()
        self.status(
            f"{quality.label} 不可用，已自动切换到 {actual_quality.label}"
            if actual_quality != quality
            else "Space 暂停 · N/P 切歌 · F7 播放页 · F8 最近播放 · F1 帮助"
        )
        self.record_recent(track)

    @work(exclusive=True, group="play")
    async def play_move(self, direction: int, *, automatic: bool = False) -> None:
        """Play the queue's next or previous selection while retaining audio failure recovery."""
        self.loading = True
        try:
            selection = await self.playback_queue.move(
                direction, self.repeat_mode, self.fetch, automatic=automatic
            )
            await self.load_selection(selection)
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试 · N 下一首")
        finally:
            self.loading = False

    @work(exclusive=True, group="play")
    async def replay_track(self, *, resume: bool = False) -> None:
        """Reload the retained queue selection without consuming manual pending songs."""
        self.loading = True
        try:
            selection = self.playback_queue.replay()
            await self.load_selection(selection, resume=resume and selection.replay)
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试 · N 下一首")
        finally:
            self.loading = False

    @work(exclusive=True, group="play")
    async def action_retry(self) -> None:
        """Repair mpv when needed and retry navigation or the retained queue selection."""
        self.loading = True
        repaired = not self.ready
        try:
            if repaired:
                await self.player.close()
                self.playback_events.reset()
                while not self.player.events.empty():
                    self.player.events.get_nowait()
                await self.player.start()
                self.ready = True
                await self.player.command("set_property", "volume", self.playback_volume)
                self.status("播放器已恢复，请选择歌曲播放")
            selection = await self.playback_queue.retry(self.repeat_mode, self.fetch)
            if selection.entry is not None or selection.ended:
                await self.load_selection(selection, resume=selection.replay)
            elif not repaired:
                self.status("没有可重试的歌曲")
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试 · N 下一首")
        finally:
            self.loading = False

    def update_player(self) -> None:
        """Consume mpv events, advance completed tracks, and refresh playback details."""
        if not self.is_running or self.switching:
            return
        self.remember_playback()
        events = self.playback_events.drain(self.player.events, loading=self.loading)
        for event in events:
            if event.get("event") == "end-file":
                if event.get("playlist_entry_id") != self.player.entry_id:
                    continue
                if event.get("reason") == "eof" and self.current:
                    self.play_move(1, automatic=True)
                elif event.get("reason") == "error":
                    self.current = None
                    self.status("音频加载失败，按 F5 重试当前歌曲或 N 下一首")
            elif event.get("event") == "disconnected":
                self.ready = False
                self.current = None
                self.status("播放器已退出，按 F5 恢复当前歌曲")
        if self.session_store is not None and self.current is not None:
            now = asyncio.get_running_loop().time()
            if now - self.session_saved_at >= 5:
                self.session_saved_at = now
                self.save_state()
        props = self.player.properties
        if self.current:
            state = "暂停" if props.get("pause") else "播放"
            if props.get("paused-for-cache"):
                state = "缓冲"
            elapsed = props.get("time-pos") or 0
            duration = props.get("duration") or self.current.seconds
            volume = int(props.get("volume") or 0)
            width = max(4, min(28, self.query_one("#playback-detail", Static).size.width - 34))
            filled = int(width * min(1, max(0, elapsed / duration))) if duration else 0
            bar = "━" * filled + "─" * (width - filled)
            title = f"{self.current.title} — {self.current.artist}  [{self.current_quality.label}]"
            detail = Text(f"{state}  {timestamp(elapsed)} ")
            detail.append(bar[:filled], style=self.get_css_variables()["accent"])
            detail.append(bar[filled:], style=self.get_css_variables()["text-muted"])
            detail.append(f" {timestamp(duration)}   音量 {volume}%")
        elif self.playback_track is not None:
            title = f"{self.playback_track.title} — {self.playback_track.artist}"
            detail = Text("正在获取音源…" if self.loading else "等待重试 · F5 重试 · N 下一首")
        else:
            title = "正在加载…" if self.loading else "尚未播放"
            detail = Text("选中歌曲后按 Enter 播放")
        message = f"{title}\n{detail.plain}"
        if message != self.now_playing_text:
            self.now_playing_text = message
            track = self.current or self.playback_track
            if track is not None:
                heading = Text(f"{track.title} — ")
                heading.append_text(artist_names(track))
                if self.current_quality is not None:
                    heading.append(f"  [{self.current_quality.label}]")
                self.query_one("#now-playing", Static).update(heading)
            else:
                self.query_one("#now-playing", Static).update(title)
            self.query_one("#playback-detail", Static).update(detail)
            self.mark_playing_track()
        if self.size.width < 60 and self.current is not None:
            detail = Text(
                f"{timestamp(props.get('time-pos'))}/"
                f"{timestamp(props.get('duration') or self.current.seconds)}  "
                f"音量 {int(props.get('volume') or 0)}%"
            )
            detail.stylize(self.get_css_variables()["accent"], 0, 5)
            self.query_one("#playback-detail", Static).update(detail)
        self.query_one(BottomTransport).refresh_state()

    def track_number(self, index: int, track: Track) -> Text:
        """Return a page-relative song number with the playing-track indicator."""
        number = (self.page - 1) * self.page_size + index + 1
        playing = self.current is not None and self.current.mid == track.mid
        return Text(
            f"{'▸' if playing else ' '} {number}",
            style=(
                self.get_css_variables()["accent"]
                if playing
                else self.get_css_variables()["text-muted"]
            ),
        )

    def mark_playing_track(self) -> None:
        """Refresh row indicators to match the currently playing track."""
        table = self.query_one("#songs", DataTable)
        for index, track in enumerate(self.tracks):
            table.update_cell(str(index), "position", self.track_number(index, track))

    @work(group="control")
    async def control(self, *arguments) -> None:
        """Send a player command and report any failure in the status line."""
        try:
            await self.player.command(*arguments)
        except Exception as error:
            self.status(error_text(error))

    def action_pause(self) -> None:
        """Toggle pause when a track is active."""
        if self.current:
            self.control("cycle", "pause")

    def action_next(self) -> None:
        """Request the next song without choosing between manual and source navigation."""
        self.play_move(1)

    def action_previous(self) -> None:
        """Request the previous song while the queue owns manual return and random history."""
        self.play_move(-1)

    def action_seek(self, seconds: int) -> None:
        """Seek by a relative number of seconds in the active track."""
        if self.current:
            self.control("seek", seconds, "relative")

    def action_volume(self, amount: int) -> None:
        """Adjust player volume by the supplied amount."""
        self.control("add", "volume", amount)

    def action_search(self) -> None:
        """Focus the global song and artist search field."""
        self.return_to_browse()
        self.query_one("#search", Input).focus()

    def quality_track(self) -> Track | None:
        """Choose the active track, falling back to the highlighted song row."""
        if self.current:
            return self.current
        if isinstance(self.screen, PlaybackScreen) and self.playback_track is not None:
            return self.playback_track
        return self.selected_track()

    @work(exclusive=True, group="qualities")
    async def action_quality(self) -> None:
        """Query playable qualities and open a selector for the unchanged track."""
        if isinstance(self.screen, ModalScreen):
            return
        track = self.quality_track()
        if track is None:
            self.status("请先选中一首歌曲，再按 U 查询可用音质")
            return
        self.status(f"正在查询可用音质：{track.title}")
        try:
            qualities = await self.music.available_qualities(track)
            if self.quality_track() != track:
                self.status("歌曲已变化，请重新按 U 查询音质")
                return
            if not qualities:
                self.status("此歌曲暂无可用播放地址，请检查登录和会员权限")
                return
            self.status(f"{track.title} · {len(qualities)} 档可用音质")
            self.push_screen(
                QualityScreen(qualities, self.current_quality or self.quality),
                lambda quality: self.choose_quality(quality, track),
            )
        except Exception as error:
            self.status(error_text(error))

    def choose_quality(self, quality: Quality | None, track: Track) -> None:
        """Apply a quality selection and reload active playback at its current position."""
        if quality is None or (
            quality == self.quality and (self.current is None or self.current_quality == quality)
        ):
            return
        if self.quality_track() != track:
            self.status("歌曲已变化，请重新按 U 查询音质")
            return
        self.quality = quality
        self.update_heading()
        if self.current or self.loading:
            self.replay_track(resume=self.current is not None)
        else:
            self.status(
                f"已选择 {quality.label}，按 F5 重试当前歌曲"
                if isinstance(self.screen, PlaybackScreen) and self.playback_track is not None
                else f"已选择 {quality.label}，下一首播放时生效"
            )

    def action_songs(self) -> None:
        """Return keyboard focus to the song table."""
        if (
            not isinstance(self.screen, (PlaybackScreen, HistoryScreen, ModalScreen))
            and isinstance(self.source, ArtistSongs)
            and self.artist_returns
        ):
            self.action_artist_back()
            return
        self.return_to_browse()
        self.query_one("#songs", DataTable).focus()

    def action_previous_page(self) -> None:
        """Load the previous browsing page when one exists."""
        if self.source is not None and self.page > 1:
            self.load_view(self.source, page=self.page - 1)
        else:
            self.status("已是第一页")

    def action_next_page(self) -> None:
        """Load the next browsing page when the source has more songs."""
        if self.source is not None and self.has_more:
            self.load_view(self.source, page=self.page + 1)
        else:
            self.status("已是最后一页")

    def selected_track(self) -> Track | None:
        """Return the highlighted browsing song for queue and library actions."""
        table = self.query_one("#songs", DataTable)
        row = table.cursor_row
        if not 0 <= row < table.row_count:
            return None
        index = int(table.ordered_rows[row].key.value)
        return self.tracks[index] if 0 <= index < len(self.tracks) else None

    def action_help(self) -> None:
        """Open keyboard help without interrupting playback or text entry."""
        if not isinstance(self.screen, ModalScreen):
            self.push_screen(HelpScreen())

    def action_enqueue(self) -> None:
        """Append the selected song to manual next tracks in their insertion order."""
        track = self.selected_track()
        if track is None:
            self.status("请先选中一首歌曲，再按 A 加入待播队列")
            return
        self.playback_queue.enqueue(QueuedTrack(track, self.source, self.page))
        self.status(f"已加入手动待播：{track.title} · F3 查看 · N 播放下一首")
        self.save_state()

    def action_queue(self) -> None:
        """Open the queue tab immediately while it prepares any random-source data."""
        if not self.switching and not isinstance(self.screen, ModalScreen):
            self.open_playback().show_tab("queue")

    @work(exclusive=True, group="queue-view")
    async def prepare_queue(self) -> None:
        """Prepare random successors without changing the user's page or keyboard focus."""
        if (
            self.repeat_mode == RepeatMode.RANDOM
            and self.playback_queue.view(self.repeat_mode).tracks
        ):
            try:
                self.status("正在准备随机待播队列…")
                await self.playback_queue.prepare(self.repeat_mode, self.fetch)
            except Exception as error:
                self.status(error_text(error))

    def get_queue_state(self) -> QueueState:
        """Expose current manual entries and known source successors for live queue views."""
        queue = self.playback_queue.view(self.repeat_mode)
        source = queue.source or (
            self.playback_origin.source if self.playback_origin is not None else None
        )
        label = self.source_label(source) or "手动待播"
        if self.repeat_mode == RepeatMode.RANDOM:
            label += " · 随机播放"

        return QueueState(
            self.current,
            tuple(QueueItem(entry, entry.track) for entry in queue.pending),
            queue.upcoming,
            queue.has_more,
            label,
        )

    def edit_pending_queue(self, action: str, key: object | None) -> None:
        """Edit only the identified manual entry, preserving playback and the source cursor."""
        if self.switching:
            return
        result = self.playback_queue.edit_pending(action, key)
        if result.operation == "missing":
            self.status("这首歌已离开待播队列，请选择当前的待播项")
            return
        if result.operation == "unchanged":
            return
        if result.operation == "clear":
            self.status("已清空手动待播，当前歌曲和原列表继续播放")
        elif result.operation == "remove" and result.entry is not None:
            self.status(f"已移除手动待播：{result.entry.track.title}")
        elif result.operation == "move":
            self.status("已调整手动待播顺序")
        self.save_state()

    @staticmethod
    def source_label(source: PlaybackSource) -> str:
        """Describe a browsing origin using platform-independent song-library names."""
        if isinstance(source, ArtistSongs):
            return f"歌手：{source.artist.name} · {source.order.label}"
        if isinstance(source, PlaylistSearch):
            return f"{source.playlist.title} · 筛选：{source.keyword}"
        if isinstance(source, Playlist):
            return source.title
        return f"搜索：{source}" if source else ""

    def get_playback_state(self) -> PlaybackState:
        """Read shared playback state for the full player page without new audio ownership."""
        track = self.current or self.playback_track
        properties = self.player.properties
        queue = self.get_queue_state()
        next_track = (
            queue.pending[0].track
            if queue.pending
            else (queue.upcoming[0] if queue.upcoming else None)
        )
        hint = ""
        if next_track is None:
            if self.repeat_mode == RepeatMode.SINGLE and track is not None:
                hint = "当前歌曲结束后重播；N 可手动切歌"
            elif (
                self.playback_queue.view(self.repeat_mode).tracks
                and self.repeat_mode == RepeatMode.RANDOM
            ):
                hint = "下一首按随机顺序选择"
            elif (
                self.playback_queue.view(self.repeat_mode).tracks
                and self.repeat_mode == RepeatMode.LIST
            ):
                hint = "列表结束后从开头继续"
            elif queue.has_more:
                hint = "下一首将从后续页面加载"
            else:
                hint = "没有后续歌曲；返回列表按 A 添加手动待播"
        origin = self.playback_origin
        return PlaybackState(
            track=track,
            position=(
                float(properties.get("time-pos") or 0) if self.current else self.playback_position
            ),
            duration=(
                (
                    float(properties.get("duration") or track.seconds)
                    if self.current
                    else float(track.seconds)
                )
                if track
                else 0
            ),
            paused=bool(properties.get("pause")) if self.current else self.playback_paused,
            buffering=bool(properties.get("paused-for-cache")) if self.current else False,
            volume=int(properties.get("volume") or 0),
            quality=(self.current_quality or self.quality).label,
            repeat_mode=self.repeat_mode.value,
            source_label=self.source_label(origin.source) if origin else queue.source_label,
            platform=self.music.display_name,
            loading=self.loading,
            ready=self.ready,
            active=self.current is not None,
            status=self.last_status_message,
            pending_count=len(self.playback_queue.view(self.repeat_mode).pending),
            next_track=next_track,
            next_hint=hint,
            can_add_to_playlist=self.account_logged_in,
            can_create_playlist=self.account_logged_in
            and getattr(self.music, "supports_playlist_creation", False),
        )

    @on(Tabs.TabActivated, "#navigation")
    def navigate_tab(self, event: Tabs.TabActivated) -> None:
        """Move between the visible application pages using the main navigation."""
        event.stop()
        if event.tabs.screen is self.screen and event.tab.id != "browse":
            self.navigate(event.tab.id or "browse")

    def navigate(self, page: str) -> None:
        """Switch pages without accumulating navigation screens or replacing playback."""
        if self.switching or isinstance(self.screen, ModalScreen):
            return
        if page == "browse":
            self.return_to_browse()
        elif page == "playback":
            self.open_playback()
        elif page == "history" and not isinstance(self.screen, HistoryScreen):
            self.action_history()

    def open_playback(self) -> PlaybackScreen:
        """Reuse the player page or create it with the application's live dependencies."""
        if isinstance(self.screen, HistoryScreen):
            self.pop_screen()
        if isinstance(self.screen, PlaybackScreen):
            return self.screen
        page = PlaybackScreen(
            self.get_playback_state,
            self.handle_playback_action,
            get_queue_state=self.get_queue_state,
            edit_queue=self.edit_pending_queue,
            load_lyrics=self.fetch_lyrics,
        )
        self.push_screen(page)
        return page

    def action_playback(self) -> None:
        """Toggle the player page without changing the browser or playback queue."""
        if isinstance(self.screen, PlaybackScreen):
            self.return_to_browse()
        elif not isinstance(self.screen, ModalScreen):
            self.open_playback()

    def return_to_browse(self) -> None:
        """Restore the browser before search, filtering, or locating a playback source."""
        while isinstance(self.screen, (PlaybackScreen, HistoryScreen)):
            self.pop_screen()
        self.query_one("#navigation", Tabs).active = "browse"
        self.query_one("#songs", DataTable).focus()

    def handle_playback_action(self, action: str) -> None:
        """Route page controls to the same application actions used by keyboard shortcuts."""
        if self.switching:
            return
        if action in ("browse", "history"):
            self.navigate(action)
            return
        actions = {
            "previous": self.action_previous,
            "pause": self.action_pause,
            "next": self.action_next,
            "quality": self.action_quality,
            "queue": self.prepare_queue,
            "lyrics": self.action_lyrics,
            "retry": self.action_retry,
            "help": self.action_help,
            "platform": self.action_platform,
            "resume_session": self.action_resume_session,
            "search": self.action_search,
            "locate": self.action_locate,
            "filter": self.action_filter,
            "artist": self.action_artist,
            "add_to_playlist": self.action_add_to_playlist,
            "create_playlist": self.action_create_playlist,
        }
        if callback := actions.get(action):
            callback()

    def record_recent(self, track: Track) -> None:
        """Record accepted playback with a small origin reference and no temporary URL."""
        if self.session_store is None:
            return
        origin = self.playback_origin
        source = origin.source if origin else self.playback_queue.view(self.repeat_mode).source
        page = origin.page if origin else self.playback_queue.view(self.repeat_mode).page
        if isinstance(source, PlaylistSearch):
            source, page = source.playlist, 1
        try:
            self.session_store.record_play(
                self.source_key,
                self.active_account_id,
                {"track": asdict(track), "source": encode_source(source), "page": page},
            )
        except (OSError, ValueError) as error:
            self.status(f"播放正常，但无法保存最近播放：{error_text(error)}")

    def action_history(self) -> None:
        """Open this account's recent songs and source shortcuts without changing playback."""
        if isinstance(self.screen, HistoryScreen):
            self.pop_screen()
            return
        if isinstance(self.screen, ModalScreen):
            return
        try:
            records = (
                self.session_store.history(self.source_key, self.active_account_id)
                if self.session_store is not None
                else []
            )
            entries = [
                HistoryEntry(
                    decode_track(record["track"]),
                    self.source_label(decode_source(record["source"])),
                    record["played_at"],
                    record,
                )
                for record in records
            ]
            self.push_screen(HistoryScreen(entries, self.navigate), self.choose_recent)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status(f"无法读取最近播放：{error_text(error)}")

    def choose_recent(self, choice: tuple[str, dict] | None) -> None:
        """Play a recent song or open its saved origin while retaining current audio."""
        navigation = self.screen.query_one("#navigation", Tabs)
        navigation.active = "playback" if isinstance(self.screen, PlaybackScreen) else "browse"
        if choice is None:
            return
        action, record = choice
        if action == "play":
            self.play_recent(record)
        elif action == "open":
            source = decode_source(record["source"])
            if source is not None:
                self.return_to_browse()
                self.load_view(source, page=record["page"])

    @work(exclusive=True, group="play")
    async def play_recent(self, record: dict) -> None:
        """Play one recent song with a fresh URL and retain its original navigation context."""
        if not self.ready:
            self.status("播放器尚未就绪")
            return
        self.loading = True
        try:
            track = decode_track(record["track"])
            origin = QueuedTrack(track, decode_source(record["source"]), int(record["page"]))
            self.workers.cancel_group(self, "queue-view")
            self.playback_queue.replace([track], None, 1, False, 0, origin=origin)

            await self.load_track(track, self.quality, origin=origin)
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试")
        finally:
            self.loading = False

    @work(exclusive=True, group="view")
    async def action_locate(self) -> None:
        """Return to the active song's original browsing page without replacing playback."""
        self.return_to_browse()
        self._view_generation += 1
        generation = self._view_generation
        track = self.playback_track
        if track is None:
            self.status("尚未播放歌曲")
            return
        origin = self.playback_origin or self.playback_queue.view(self.repeat_mode).manual
        source, page = (
            (origin.source, origin.page)
            if origin
            else (
                self.playback_queue.view(self.repeat_mode).source,
                self.playback_queue.view(self.repeat_mode).page,
            )
        )
        if source is None:
            self.status("当前歌曲没有浏览来源")
            return
        try:
            result = await self.fetch(source, page)
            if generation != self._view_generation:
                return
            if not isinstance(source, ArtistSongs):
                self.artist_returns.clear()
            self.display_page(source, page, result)
            row = next((i for i, item in enumerate(self.tracks) if item.mid == track.mid), None)
            if row is None:
                self.status("已返回播放来源，当前歌曲已不在这一页")
            else:
                table = self.query_one("#songs", DataTable)
                table.move_cursor(row=table.get_row_index(str(row)))
            if isinstance(source, ArtistSongs):
                await self.load_artist_detail(source, generation)
        except Exception as error:
            if generation == self._view_generation:
                self.status(error_text(error))

    def action_refresh(self) -> None:
        """Refresh the active list, rebuilding playlist-filter matches when necessary."""
        if self.failed_artist_view is not None:
            source, page = self.failed_artist_view
            self.load_view(source, page=page)
        elif isinstance(self.source, PlaylistSearch):
            self.playlist_cache.pop(self.source.playlist, None)
            self.filter_playlist(self.source.playlist, self.source.keyword)
        elif self.source is not None:
            if isinstance(self.source, Playlist):
                self.playlist_cache.pop(self.source, None)
            self.load_view(self.source, page=self.page)
        else:
            self.load_playlists()

    def action_lyrics(self) -> None:
        """Open the player's lyric tab and let it follow subsequent track changes."""
        if isinstance(self.screen, ModalScreen):
            return
        self.open_playback().show_tab("lyrics")

    async def fetch_lyrics(self, track: Track) -> Lyrics:
        """Fetch lyrics through the active source and sanitize network errors for the page."""
        try:
            return await self.music.lyrics(track)
        except Exception as error:
            raise ValueError(error_text(error)) from error

    @work(group="library-action")
    async def action_like(self, track: Track | None = None) -> None:
        """Toggle the highlighted song's liked state after successful account requests."""
        track = track or self.selected_track()
        if track is None or self.library_busy:
            return
        if not self.account_logged_in:
            self.status("请先按 L 登录，再操作喜欢的歌曲")
            return
        self.library_busy = True
        self.status(f"正在更新喜欢状态：{track.title}")
        try:
            if self.search_likes is None:
                self.search_likes = SearchLikes(self.music)
            else:
                self.search_likes.bind(self.music)
            liked = await self.search_likes.toggle(track)
            if liked is None:
                return
            self.draw_tracks()
            self.playlist_cache.clear()
            self.load_playlists(message=f"{'已喜欢' if liked else '已取消喜欢'}：{track.title}")
        except Exception as error:
            self.status(error_text(error))
        finally:
            self.library_busy = False

    @work(group="library-action")
    async def action_add_to_playlist(self) -> None:
        """Fetch writable playlists and ask for a destination for the selected song."""
        track = (
            self.quality_track()
            if isinstance(self.screen, PlaybackScreen)
            else self.selected_track()
        )
        if track is None or self.library_busy or isinstance(self.screen, ModalScreen):
            return
        if not self.account_logged_in:
            self.status("请先按 L 登录，再加入歌单")
            return
        self.library_busy = True
        self.status("正在读取可写歌单…")
        try:
            playlists = await self.music.playlists()
            if not isinstance(self.screen, ModalScreen):
                self.push_screen(
                    PlaylistPickerScreen(
                        playlists,
                        allow_create=getattr(self.music, "supports_playlist_creation", False),
                    ),
                    lambda choice: self.choose_playlist_destination(track, choice),
                )
        except Exception as error:
            self.status(error_text(error))
        finally:
            self.library_busy = False

    @work(group="library-action")
    async def add_selected_to_playlist(self, track: Track, playlist: Playlist | None) -> None:
        """Write the chosen song once and invalidate cached account playlist contents."""
        if playlist is None or self.library_busy:
            return
        self.library_busy = True
        self.status(f"正在加入歌单：{playlist.title}")
        try:
            await self.music.add_to_playlist(track, playlist)
            self.playlist_cache.clear()
            self.load_playlists(
                message=f"已将 {track.title} 加入 {playlist.title} · Ctrl+R 刷新列表"
            )
        except Exception as error:
            self.status(error_text(error))
        finally:
            self.library_busy = False

    @work(exclusive=True, group="play")
    async def action_resume_session(self) -> None:
        """Restore this account's saved queue and resume with a freshly resolved URL."""
        if self.session_store is None or not self.ready:
            self.status("暂无可恢复的播放会话，或播放器尚未就绪")
            return
        try:
            snapshot = self.session_store.load_session(self.source_key, self.active_account_id)
            if snapshot is None:
                self.status("当前账号没有已保存的播放会话")
                return

            snapshot = PlaybackSnapshot.decode(snapshot)
            self.workers.cancel_group(self, "control")
            self.loading = True
            self.workers.cancel_group(self, "queue-view")
            await self.player.command("stop")
            self.current = None
            self.current_quality = None
            self.playback_queue.restore(snapshot)
            self.playback_origin = snapshot.origin
            self.playback_position, self.playback_paused = snapshot.position, snapshot.paused

            await self.load_track(snapshot.track, self.quality, resume=True)
        except (KeyError, TypeError, IndexError) as error:
            self.status(f"保存的播放会话无效（{type(error).__name__}）")
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试")
        finally:
            self.loading = False

    def update_heading(self) -> None:
        """Show account status, preferred quality, and repeat mode in the heading."""
        label = "已登录" if self.account_logged_in else "未登录"
        if self.appearance == "system":
            appearance = "系统外观不可用" if self._system_theme_unavailable else "跟随系统"
        else:
            appearance = "日间" if self.appearance == DAY.name else "夜间"
        self.query_one("#heading", Static).update(
            f"Music TUI · {self.music.display_name}   {label}   "
            f"{self.quality.label}   {self.repeat_mode.value}   "
            f"{appearance}"
        )

    def action_repeat(self) -> None:
        """Cycle playback modes and reset shuffle state when entering random mode."""
        self.workers.cancel_group(self, "queue-view")
        modes = list(RepeatMode)
        self.repeat_mode = modes[(modes.index(self.repeat_mode) + 1) % len(modes)]
        if self.repeat_mode == RepeatMode.RANDOM:
            self.playback_queue.change_mode(self.repeat_mode)
        self.update_heading()
        self.status(f"{self.repeat_mode.value} · 作用于正在播放的整个列表")
        if (
            isinstance(self.screen, PlaybackScreen)
            and self.screen.query_one("#playback-tabs", TabbedContent).active == "queue"
        ):
            self.prepare_queue()


async def run(
    source: str | None = None,
    netease_api_url: str | None = None,
    netease_backend: str = "enhanced",
) -> None:
    """Run the selected music source with mpv and close both resources on exit."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    session_store = SessionStore(data_home / "music-tui")

    def create_source(key: str) -> MusicSource:
        """Construct only the chosen platform with its own credential directory."""
        if key == "netease-official":
            from .sources.netease_official import OfficialNetEaseMusic

            configuration = load_official_config()
            private_key_file = configuration.get("privateKeyFile")
            device_file = configuration.get("deviceFile")
            return OfficialNetEaseMusic(
                data_home / "music-tui" / "netease-official",
                app_id=configuration.get("appId", ""),
                private_key_path=Path(private_key_file) if private_key_file else None,
                device_file=Path(device_file) if device_file else None,
            )
        if key == "netease-enhanced":
            from .sources.netease import NetEaseMusic

            api_url = netease_api_url or load_enhanced_config()["apiUrl"]
            return NetEaseMusic(data_home / "music-tui" / "netease", api_url=api_url)
        return QQMusic(data_home / "qqmusic-tui")

    key = (
        (f"netease-{netease_backend}" if source == "netease" else "qq")
        if source is not None
        else session_store.last_source() or "qq"
    )
    if key not in ("qq", "netease-enhanced", "netease-official"):
        raise ValueError("保存的平台无效，请使用 --source qq 或 --source netease 指定平台")
    music = create_source(key)
    player = Player()
    application = None
    try:
        application = MusicApp(
            music, player, source_factory=create_source, source_key=key, session_store=session_store
        )
        await application.run_async()
    finally:
        await player.close()
        await (application.music if application is not None else music).close()


def main() -> None:
    """Start the asynchronous application from the command-line entry point."""
    parser = argparse.ArgumentParser(description="A terminal player for QQ and NetEase Music.")
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"music-tui {version('music-tui')}",
        help="Show the installed version and exit",
    )
    parser.add_argument(
        "--source",
        choices=("qq", "netease"),
        default=None,
        help="Music platform (default: last used platform, or QQ on first use)",
    )
    parser.add_argument(
        "--netease-backend",
        choices=("official", "enhanced"),
        default="enhanced",
        help="NetEase API backend (default: enhanced)",
    )
    parser.add_argument(
        "--netease-api-url",
        default=None,
        help="Override the saved NeteaseCloudMusicApiEnhanced service URL",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--setup-netease",
        action="store_true",
        help="Configure NetEase access (Enhanced by default)",
    )
    actions.add_argument(
        "--netease-service",
        action="store_true",
        help="Start the configured local Enhanced API service in the background (macOS)",
    )
    actions.add_argument(
        "--netease-service-status",
        action="store_true",
        help="Check the Enhanced background service",
    )
    actions.add_argument(
        "--netease-service-stop", action="store_true", help="Stop the Enhanced background service"
    )
    arguments = parser.parse_args()
    if arguments.setup_netease:
        from .netease_config import run_setup

        raise SystemExit(run_setup(arguments.netease_backend))
    if arguments.netease_service_status or arguments.netease_service_stop:
        from .netease_enhanced_setup import run_service

        raise SystemExit(run_service("stop" if arguments.netease_service_stop else "status"))
    if arguments.netease_service:
        from .netease_enhanced_setup import run_service

        raise SystemExit(run_service())
    try:
        asyncio.run(run(arguments.source, arguments.netease_api_url, arguments.netease_backend))
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
