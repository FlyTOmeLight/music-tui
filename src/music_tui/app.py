"""Keyboard-driven Music TUI for QQ Music and NetEase Cloud Music."""

import argparse
import asyncio
import os
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from rich.text import Text
from terminal_qrcode import draw
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, ListItem, ListView, Static

from .models import LoginState, Playlist, Quality, SongPage, Track
from .netease_config import load_enhanced_config, load_official_config
from .player import Player
from .screens import HelpScreen, LyricsScreen, PlaylistPickerScreen, QueueScreen
from .session import SessionStore
from .sources import MusicSource
from .sources.netease_openapi import OfficialAPIError
from .sources.qqmusic import QQMusic


def timestamp(seconds: float | None) -> str:
    """Format elapsed seconds as a nonnegative minute-and-second timestamp."""
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60:02}:{seconds % 60:02}"


def error_text(error: Exception) -> str:
    # Network exceptions may contain authenticated URLs; keep those out of the UI.
    """Return a user-facing error without exposing authenticated network URLs."""
    if type(error) in (ValueError, RuntimeError) or isinstance(error, OfficialAPIError):
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
    #login-title { color: #58d6a0; text-style: bold; }
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
    #quality-box { width: 44; height: auto; max-height: 100%; border: solid #58d6a0; padding: 1 2; }
    #quality-title, #quality-keys { height: 1; }
    #quality-options { height: 14; margin: 1 0; }
    #quality-keys { color: $text-muted; }
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
        ("netease-official", "网易云音乐 · 官方 API（可选）"),
    ]
    DEFAULT_CSS = """
    PlatformScreen { align: center middle; background: $background 95%; }
    #platform-box { width: 64; max-width: 100%; height: auto; border: solid #58d6a0; padding: 1 2; }
    #platform-options { height: 9; margin: 1 0; }
    #platform-help { height: auto; color: $text-muted; }
    """

    def __init__(self, selected: str):
        """Highlight the active platform and explain switching behavior."""
        super().__init__()
        self.selected = selected

    def compose(self) -> ComposeResult:
        """Put the recommended third-party backend first among NetEase choices."""
        with Vertical(id="platform-box"):
            yield Static("切换音乐平台")
            yield ListView(
                *(ListItem(Label(label, markup=False)) for _, label in self.OPTIONS),
                initial_index=[key for key, _ in self.OPTIONS].index(self.selected),
                id="platform-options",
            )
            yield Static(
                "Enhanced：运行 --setup-netease 配置服务。\n"
                "官方：仅适用于已取得应用授权和设备参数的用户。\n"
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


@dataclass(frozen=True)
class PlaylistSearch:
    playlist: Playlist
    keyword: str
    tracks: tuple[Track, ...]
    playlist_total: int


@dataclass(frozen=True)
class QueuedTrack:
    """A manually queued song keeps its browsing origin for later navigation."""

    track: Track
    source: str | Playlist | PlaylistSearch | None
    page: int


def encode_source(source: str | Playlist | PlaylistSearch | None) -> dict | str | None:
    """Serialize a queue origin without credentials or temporary audio URLs."""
    if isinstance(source, PlaylistSearch):
        return {
            "playlist": asdict(source.playlist),
            "keyword": source.keyword,
            "tracks": [asdict(track) for track in source.tracks],
            "playlist_total": source.playlist_total,
        }
    return asdict(source) if isinstance(source, Playlist) else source


def decode_track(value: dict) -> Track:
    """Restore immutable track metadata, including tuple-valued media variants."""
    return Track(**{**value, "variants": tuple(value.get("variants", ()))})


def decode_source(value: dict | str | None) -> str | Playlist | PlaylistSearch | None:
    """Restore the saved playlist, search query, or complete playlist-filter results."""
    if not isinstance(value, dict):
        return value
    if "playlist" in value:
        return PlaylistSearch(
            Playlist(**value["playlist"]),
            value["keyword"],
            tuple(decode_track(track) for track in value["tracks"]),
            value["playlist_total"],
        )
    return Playlist(**value)


class RepeatMode(Enum):
    SEQUENCE = "顺序播放"
    LIST = "列表循环"
    SINGLE = "单曲循环"
    RANDOM = "随机播放"


class SongTable(DataTable):
    BINDINGS = [
        Binding("left", "app.seek(-5)", "后退", show=False),
        Binding("right", "app.seek(5)", "前进", show=False),
        Binding("pageup", "app.previous_page", "上一页", show=False),
        Binding("pagedown", "app.next_page", "下一页", show=False),
    ]


class MusicApp(App):
    TITLE = "Music TUI"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    $base: #111821;
    $surface: #182330;
    $ink: #e4ecf5;
    $muted: #a1b2c8;
    $accent: #65d9a4;
    $line: #304254;
    Screen { background: $base; color: $ink; }
    #heading { height: 1; padding: 0 2; background: $surface; color: $accent; text-style: bold; }
    #search { margin: 0 1; height: 3; border: tall $line; background: $base; }
    #search:focus { border: tall $accent; }
    #content { height: 1fr; margin: 0 1; }
    #library { width: 22; border-right: solid $line; padding-right: 1; margin-right: 1; }
    #library-title { height: 2; color: $muted; padding: 0 1; }
    #playlists { height: 1fr; background: transparent; }
    ListItem { padding: 0 1; height: 2; margin-bottom: 1; }
    ListItem > Label { text-overflow: ellipsis; width: 100%; height: 1; }
    .playlist-count { color: $muted; }
    ListView > ListItem.-highlight { background: $surface; }
    ListView:focus > ListItem.-highlight { background: #254a41; border-left: thick $accent; }
    #songs-box { width: 1fr; }
    #view-title { height: 1; color: $ink; text-style: bold; text-overflow: ellipsis; }
    #view-meta { height: 2; color: $muted; }
    #filter { height: 3; border: tall $line; background: $base; }
    #filter:focus { border: tall $accent; }
    #songs { height: 1fr; background: transparent; }
    DataTable > .datatable--header { background: $surface; color: $muted; }
    DataTable > .datatable--odd-row { background: #141e29; }
    DataTable > .datatable--cursor { background: $surface; }
    DataTable:focus > .datatable--cursor { background: #254a41; color: white; }
    #player-box { height: 3; padding: 0 2; border-top: solid $line; background: $surface; }
    #now-playing { height: 1; color: $accent; text-style: bold; }
    #playback-detail { height: 1; color: $muted; }
    #status { height: 1; padding: 0 1; color: $muted; }
    Footer { background: $base; }
    """
    BINDINGS = [
        Binding("f1", "help", "帮助", priority=True),
        Binding("f2", "platform", "切换平台", priority=True),
        Binding("f3", "queue", "队列", priority=True),
        Binding("f4", "lyrics", "歌词", priority=True),
        Binding("f5", "retry", "重试", priority=True, show=False),
        Binding("f6", "resume_session", "继续上次", priority=True, show=False),
        Binding("a", "enqueue", "下一首播放", show=False),
        Binding("s", "like", "喜欢", show=False),
        Binding("w", "add_to_playlist", "加入歌单", show=False),
        Binding("g", "locate", "定位当前", show=False),
        Binding("ctrl+r", "refresh", "刷新", show=False),
        Binding("p", "previous", "上一首"),
        Binding("n", "next", "下一首"),
        Binding("space", "pause", "暂停"),
        Binding("l", "login", "登录"),
        Binding("slash", "search", "搜索"),
        Binding("left_square_bracket", "previous_page", "上一页"),
        Binding("right_square_bracket", "next_page", "下一页"),
        Binding("r", "repeat", "模式"),
        Binding("u", "quality", "音质"),
        Binding("f", "filter", "歌单搜索", show=False),
        Binding("q", "quit", "退出"),
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
        self.music = music
        self.player = player
        self.source_factory = source_factory
        self.source_key = source_key
        self.session_store = session_store
        self.switching = False
        self.reset_library()
        self.ready = False
        self.table_width = 0
        self.apply_preferences()

    def reset_library(self) -> None:
        """Clear platform-specific account, browsing, queue and quality state."""
        self.playlists: list[Playlist] = []
        self.tracks: list[Track] = []
        self.source: str | Playlist | PlaylistSearch | None = None
        self.page = 1
        self.has_more = False
        self.total = 0
        self.page_size = 100
        self.playlist_counts: dict[Playlist, int] = {}
        self.playlist_cache: dict[Playlist, tuple[Track, ...]] = {}
        self.queue: list[Track] = []
        self.queue_source: str | Playlist | PlaylistSearch | None = None
        self.queue_page = 1
        self.queue_has_more = False
        self.queue_generation = 0
        self.index = -1
        self.current: Track | None = None
        self.playback_track: Track | None = None
        self.playback_position = 0.0
        self.playback_paused = False
        self.playback_volume = 70.0
        self.pending_tracks: list[QueuedTrack] = []
        self.manual_current: QueuedTrack | None = None
        self.failed_request: tuple[int, int] | None = None
        self.library_busy = False
        self.session_saved_at = 0.0
        self.loading = False
        self.now_playing_text = "尚未播放"
        self.quality = Quality.STANDARD
        self.current_quality: Quality | None = None
        self.account_logged_in = False
        self.active_account_id = ""
        self.repeat_mode = RepeatMode.SEQUENCE
        self.shuffle_pages: dict[int, tuple[list[Track], bool]] = {}
        self.shuffle_remaining: list[tuple[int, int]] = []
        self.shuffle_history: list[tuple[int, int]] = []
        self.shuffle_cursor = -1

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

            def queued(entry: QueuedTrack) -> dict:
                """Save a manually queued song together with its navigation context."""
                return {
                    "track": asdict(entry.track),
                    "source": encode_source(entry.source),
                    "page": entry.page,
                }

            self.session_store.save_session(
                self.source_key,
                self.active_account_id,
                {
                    "queue": [asdict(track) for track in self.queue],
                    "source": encode_source(self.queue_source),
                    "page": self.queue_page,
                    "has_more": self.queue_has_more,
                    "index": self.index,
                    "track": asdict(self.playback_track),
                    "position": self.playback_position,
                    "paused": self.playback_paused,
                    "pending": [queued(entry) for entry in self.pending_tracks],
                    "manual": queued(self.manual_current) if self.manual_current else None,
                    "shuffle_pages": {
                        str(page): {"tracks": [asdict(t) for t in tracks], "more": more}
                        for page, (tracks, more) in self.shuffle_pages.items()
                    },
                    "shuffle_remaining": self.shuffle_remaining,
                    "shuffle_history": self.shuffle_history,
                    "shuffle_cursor": self.shuffle_cursor,
                },
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
        yield Input(placeholder="/ 全站搜索歌曲或歌手，回车搜索", id="search")
        with Horizontal(id="content"):
            with Vertical(id="library"):
                yield Static("我的歌单", id="library-title")
                yield ListView(id="playlists")
            with Vertical(id="songs-box"):
                yield Static("选择歌单，或搜索一首歌", id="view-title", markup=False)
                yield Static("Enter 播放   [ / ] 翻页", id="view-meta", markup=False)
                field = Input(placeholder="F 歌单内搜索歌名或歌手，回车筛选", id="filter")
                field.display = False
                yield field
                yield SongTable(id="songs", cursor_type="row", zebra_stripes=True)
        with Vertical(id="player-box"):
            yield Static("尚未播放", id="now-playing", markup=False)
            yield Static("选中歌曲后按 Enter 播放", id="playback-detail", markup=False)
        yield Static("F2 切换平台 · L 扫码登录 · / 搜索 · Tab 切换区域", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        """Initialize song columns, playback polling, and account restoration."""
        table = self.query_one("#songs", DataTable)
        table.add_column("序", key="position", width=6)
        table.add_column("歌曲", key="title")
        table.add_column("歌手", key="artist")
        table.add_column("时长", key="duration", width=5)
        table.focus()
        self.set_interval(0.25, self.update_player)
        self.start_player()
        self.restore_login()

    def on_resize(self) -> None:
        """Schedule song-column sizing after the new layout is rendered."""
        if self.is_mounted:
            self.call_after_refresh(self.resize_columns)

    def resize_columns(self) -> None:
        """Fit song and artist columns to the available table width."""
        table = self.query_one("#songs", DataTable)
        width = table.size.width
        if width == self.table_width or width < 20:
            return
        self.table_width = width
        table.clear(columns=True)
        available = width - 21
        artist_width = max(8, available // 3)
        table.add_column("序", key="position", width=6)
        table.add_column("歌曲", key="title", width=max(8, available - artist_width))
        table.add_column("歌手", key="artist", width=artist_width)
        table.add_column("时长", key="duration", width=5)
        if self.source is not None:
            self.draw_tracks()

    def status(self, message: str) -> None:
        """Replace the status-line message without changing the active view."""
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
        return not self.switching or action == "quit"

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
            ):
                pending.extend(self.workers.cancel_group(self, group))
            await asyncio.gather(*(worker.wait() for worker in pending), return_exceptions=True)
            self.save_state()
            if self.ready:
                await self.player.command("stop")
            previous = self.music
            self.music, candidate = candidate, None
            self.source_key = key
            self.reset_library()
            self.quality, self.repeat_mode, self.playback_volume = preferences
            if self.session_store is not None and self.ready:
                await self.player.command("set_property", "volume", self.playback_volume)
            self.account_logged_in = logged_in
            self.active_account_id = getattr(self.music, "account_id", "") if logged_in else ""
            self.query_one("#search", Input).value = ""
            field = self.query_one("#filter", Input)
            field.value = ""
            field.display = False
            await self.query_one("#playlists", ListView).clear()
            self.query_one("#songs", DataTable).clear()
            self.query_one("#view-title", Static).update("选择歌单，或搜索一首歌")
            self.query_one("#view-meta", Static).update("Enter 播放   [ / ] 翻页")
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
                    ):
                        pending.extend(self.workers.cancel_group(self, group))
                    await asyncio.gather(
                        *(worker.wait() for worker in pending), return_exceptions=True
                    )
                    if self.ready:
                        await self.player.command("stop")
                    self.reset_library()
                    self.apply_preferences()
                    await self.query_one("#playlists", ListView).clear()
                    self.query_one("#songs", DataTable).clear()
                    self.query_one("#search", Input).value = ""
                    field = self.query_one("#filter", Input)
                    field.value = ""
                    field.display = False
                    self.query_one("#view-title", Static).update("选择歌单，或搜索一首歌")
                    self.query_one("#view-meta", Static).update("Enter 播放   [ / ] 翻页")
                finally:
                    self.switching = False
            self.account_logged_in = True
            self.active_account_id = account_id
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
            view = self.query_one("#playlists", ListView)
            await view.clear()
            await view.extend(
                ListItem(
                    Label(p.title, markup=False),
                    Label("", classes="playlist-count", markup=False),
                )
                for p in self.playlists
            )
            self.update_playlist_counts()
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
        index = event.list_view.index
        if index is not None and index < len(self.playlists):
            self.load_view(self.playlists[index])

    @on(Input.Submitted, "#search")
    def search_submitted(self, event: Input.Submitted) -> None:
        """Open global search results for a nonempty submitted query."""
        if self.switching:
            return
        if keyword := event.value.strip():
            self.load_view(keyword)

    async def fetch(self, source: str | Playlist | PlaylistSearch, page: int) -> SongPage:
        """Fetch a page from global search, a playlist, or cached playlist matches."""
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
    async def load_view(self, source: str | Playlist | PlaylistSearch, *, page: int = 1) -> None:
        """Fetch a song page and report failures in the status line."""
        self.status("正在加载歌曲…")
        try:
            result = await self.fetch(source, page)
            self.display_page(source, page, result)
        except Exception as error:
            self.status(error_text(error))

    def display_page(
        self, source: str | Playlist | PlaylistSearch, page: int, result: SongPage
    ) -> None:
        """Apply page metadata, playlist counts, and song-table focus."""
        if isinstance(source, Playlist) and page == 1:
            self.playlist_cache.pop(source, None)
        self.source, self.page, self.has_more = source, page, result.has_more
        self.tracks, self.total, self.page_size = result.tracks, result.total, result.page_size
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
            "Enter 播放 · F 歌单内搜索 · [/] 翻页 · R 播放方式"
            if result.tracks
            else (
                "没有找到歌曲；清空搜索框并回车可返回完整歌单"
                if isinstance(source, PlaylistSearch)
                else "没有找到歌曲"
            )
        )

    def update_playlist_counts(self) -> None:
        """Refresh sidebar counts from loaded metadata or playlist defaults."""
        view = self.query_one("#playlists", ListView)
        for playlist, item in zip(self.playlists, view.children, strict=False):
            count = self.playlist_counts.get(playlist, playlist.song_count)
            item.query_one(".playlist-count", Label).update(
                f"{count} 首" if count is not None else "打开查看歌曲数"
            )

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
        field = self.query_one("#filter", Input)
        if field.display:
            field.focus()
        else:
            self.status("请先打开一个歌单，再按 F 搜索歌单内歌曲")

    def draw_tracks(self) -> None:
        """Render song rows and page metadata while preserving the cursor row."""
        table = self.query_one("#songs", DataTable)
        row = table.cursor_row
        table.clear()
        for index, track in enumerate(self.tracks):
            table.add_row(
                self.track_number(index, track),
                Text(track.title),
                Text(track.artist, style="#a1b2c8"),
                Text(timestamp(track.seconds), style="#a1b2c8"),
                key=str(index),
            )
        table.move_cursor(row=min(row, max(0, len(self.tracks) - 1)))
        if isinstance(self.source, PlaylistSearch):
            title = self.source.playlist.title
            count = f"共 {self.source.playlist_total} 首 / 匹配 {self.total} 首"
        else:
            title = (
                self.source.title if isinstance(self.source, Playlist) else f"搜索：{self.source}"
            )
            count = f"共 {self.total} 首"
        pages = max(1, (self.total + self.page_size - 1) // self.page_size)
        self.query_one("#view-title", Static).update(title)
        self.query_one("#view-meta", Static).update(
            f"{count}   第 {self.page} 页 / {pages} 页   本页 {len(self.tracks)} 首"
        )

    @on(DataTable.RowSelected, "#songs")
    def select_track(self, event: DataTable.RowSelected) -> None:
        """Snapshot the displayed page as the queue and play the selected row."""
        if self.switching:
            return
        if not self.ready:
            self.status("播放器尚未就绪")
            return
        self.queue = self.tracks.copy()
        self.queue_generation += 1
        self.workers.cancel_group(self, "queue-view")
        self.loading = True
        self.pending_tracks.clear()
        self.manual_current = None
        self.queue_source, self.queue_page = self.source, self.page
        self.queue_has_more = self.has_more
        self.reset_shuffle(event.cursor_row)
        self.play_index(event.cursor_row)

    @work(exclusive=True, group="play")
    async def play_index(self, index: int, *, resume: bool = False, shuffle: int = 0) -> None:
        """Serialize list navigation with manual playback and explicit recovery."""
        await self.resolve_index(index, resume=resume, shuffle=shuffle)

    async def resolve_index(self, index: int, *, resume: bool = False, shuffle: int = 0) -> None:
        """Resolve queue boundaries and load a track, optionally preserving playback position."""
        self.loading = True
        quality = self.quality
        resolved = False
        try:
            if shuffle:
                self.status("正在准备整个列表的随机顺序…")
                page, index = await self.shuffle_position(shuffle)
                tracks, more = self.shuffle_pages[page]
                self.queue, self.queue_page, self.queue_has_more = tracks, page, more
            tracks, page, more = self.queue, self.queue_page, self.queue_has_more
            if index >= len(tracks):
                if more and self.queue_source is not None:
                    page += 1
                    result = await self.fetch(self.queue_source, page)
                    tracks, more = result.tracks, result.has_more
                    index = 0
                if not tracks or index >= len(tracks):
                    if self.repeat_mode == RepeatMode.LIST:
                        page = 1
                        if self.queue_source is not None:
                            result = await self.fetch(self.queue_source, page)
                            tracks, more = result.tracks, result.has_more
                        index = 0
            elif index < 0:
                if page > 1 and self.queue_source is not None:
                    page -= 1
                    result = await self.fetch(self.queue_source, page)
                    tracks, more = result.tracks, result.has_more
                elif self.repeat_mode == RepeatMode.LIST:
                    # The API supplies a next-page flag, so discover the final page on demand.
                    while more and self.queue_source is not None:
                        page += 1
                        result = await self.fetch(self.queue_source, page)
                        tracks, more = result.tracks, result.has_more
                        if not tracks:
                            break
                index = len(tracks) - 1 if page > 1 or self.repeat_mode == RepeatMode.LIST else 0
            if not 0 <= index < len(tracks):
                await self.player.command("stop")
                self.current = None
                self.current_quality = None
                self.manual_current = None
                self.playback_track = None
                self.failed_request = None
                self.index = len(self.queue) - 1
                self.status("播放队列已结束")
                return
            self.queue, self.queue_page, self.queue_has_more = tracks, page, more
            self.index = index
            track = self.queue[index]
            self.manual_current = None
            resolved = True
            await self.load_track(track, quality, resume=resume)
        except Exception as error:
            self.failed_request = (index, 0 if resolved else shuffle)
            self.status(f"{error_text(error)} · F5 重试 · N 下一首")
        finally:
            self.loading = False

    async def load_track(self, track: Track, quality: Quality, *, resume: bool = False) -> None:
        """Load a queue song and retain its identity and progress for explicit recovery."""
        if isinstance(self.screen, LyricsScreen):
            self.pop_screen()
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
        self.status(f"正在获取：{track.title} · {quality.label}")
        url = await self.music.stream_url(track, quality)
        await self.player.load(
            url,
            start=self.playback_position if resume else 0,
            paused=self.playback_paused if resume else False,
        )
        self.current = track
        self.current_quality = quality
        self.failed_request = None
        self.mark_playing_track()
        self.status("Space 暂停 · N/P 切歌 · F3 队列 · F4 歌词 · F1 帮助")

    @work(exclusive=True, group="play")
    async def play_manual(
        self, entry: QueuedTrack, *, resume: bool = False, consume: bool = False
    ) -> None:
        """Play a manually queued song without moving the original list's cursor."""
        self.loading = True
        try:
            if consume:
                if not self.pending_tracks:
                    return
                entry = self.pending_tracks.pop(0)
            self.manual_current = entry
            self.failed_request = None
            await self.load_track(entry.track, self.quality, resume=resume)
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试 · N 下一首")
        finally:
            self.loading = False

    @work(exclusive=True, group="play")
    async def action_retry(self) -> None:
        """Retry the failed queue operation, rebuilding mpv when its connection is lost."""
        if self.playback_track is None and self.failed_request is None:
            self.status("没有可重试的歌曲")
            return
        self.loading = True
        try:
            if not self.ready:
                await self.player.close()
                while not self.player.events.empty():
                    self.player.events.get_nowait()
                await self.player.start()
                self.ready = True
                await self.player.command("set_property", "volume", self.playback_volume)
            if self.failed_request is not None:
                index, shuffle = self.failed_request
                if index != self.index or shuffle:
                    await self.resolve_index(index, shuffle=shuffle)
                    return
            if self.playback_track is not None:
                await self.load_track(self.playback_track, self.quality, resume=True)
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试 · N 下一首")
        finally:
            self.loading = False

    def update_player(self) -> None:
        """Consume mpv events, advance completed tracks, and refresh playback details."""
        if not self.is_running or self.switching:
            return
        self.remember_playback()
        while not self.player.events.empty():
            event = self.player.events.get_nowait()
            if event.get("event") == "end-file" and not self.loading:
                if event.get("playlist_entry_id") != self.player.entry_id:
                    continue
                if event.get("reason") == "eof" and self.current:
                    if self.repeat_mode == RepeatMode.SINGLE:
                        if self.manual_current is not None:
                            self.play_manual(self.manual_current)
                        else:
                            self.play_index(self.index)
                    else:
                        self.action_next()
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
            width = max(8, min(28, self.size.width - 60))
            filled = int(width * min(1, max(0, elapsed / duration))) if duration else 0
            bar = "━" * filled + "─" * (width - filled)
            title = f"{self.current.title} — {self.current.artist}  [{self.current_quality.label}]"
            detail = Text(f"{state}  {timestamp(elapsed)} ")
            detail.append(bar[:filled], style="#65d9a4")
            detail.append(bar[filled:], style="#52687e")
            detail.append(f" {timestamp(duration)}   音量 {volume}%")
        else:
            title = "正在加载…" if self.loading else "尚未播放"
            detail = Text("选中歌曲后按 Enter 播放")
        message = f"{title}\n{detail.plain}"
        if message != self.now_playing_text:
            self.now_playing_text = message
            self.query_one("#now-playing", Static).update(title)
            self.query_one("#playback-detail", Static).update(detail)
            self.mark_playing_track()

    def track_number(self, index: int, track: Track) -> Text:
        """Return a page-relative song number with the playing-track indicator."""
        number = (self.page - 1) * self.page_size + index + 1
        playing = self.current is not None and self.current.mid == track.mid
        return Text(
            f"{'▸' if playing else ' '} {number}", style="#65d9a4" if playing else "#7e93aa"
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
        """Advance through the queue or the existing shuffle sequence."""
        if self.pending_tracks:
            self.play_manual(self.pending_tracks[0], consume=True)
        elif self.queue:
            self.play_index(
                self.index + 1, shuffle=1 if self.repeat_mode == RepeatMode.RANDOM else 0
            )

    def action_previous(self) -> None:
        """Return to the previous queue track or shuffle-history entry."""
        if self.manual_current is not None and self.queue:
            self.play_index(self.index)
        elif self.queue:
            self.play_index(
                self.index - 1, shuffle=-1 if self.repeat_mode == RepeatMode.RANDOM else 0
            )

    def action_seek(self, seconds: int) -> None:
        """Seek by a relative number of seconds in the active track."""
        if self.current:
            self.control("seek", seconds, "relative")

    def action_volume(self, amount: int) -> None:
        """Adjust player volume by the supplied amount."""
        self.control("add", "volume", amount)

    def action_search(self) -> None:
        """Focus the global song and artist search field."""
        self.query_one("#search", Input).focus()

    def quality_track(self) -> Track | None:
        """Choose the active track, falling back to the highlighted song row."""
        if self.current:
            return self.current
        row = self.query_one("#songs", DataTable).cursor_row
        return self.tracks[row] if 0 <= row < len(self.tracks) else None

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
        if quality is None or quality == self.quality:
            return
        if self.quality_track() != track:
            self.status("歌曲已变化，请重新按 U 查询音质")
            return
        self.quality = quality
        self.update_heading()
        if self.current or self.loading:
            if self.manual_current is not None:
                self.play_manual(self.manual_current, resume=self.current is not None)
            else:
                self.play_index(self.index, resume=self.current is not None)
        else:
            self.status(f"已选择 {quality.label}，下一首播放时生效")

    def action_songs(self) -> None:
        """Return keyboard focus to the song table."""
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
        row = self.query_one("#songs", DataTable).cursor_row
        return self.tracks[row] if 0 <= row < len(self.tracks) else None

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
        self.pending_tracks.append(QueuedTrack(track, self.source, self.page))
        self.status(f"已加入手动待播：{track.title} · F3 查看 · N 播放下一首")
        self.save_state()

    @work(exclusive=True, group="queue-view")
    async def action_queue(self) -> None:
        """Show manual next tracks and the loaded remainder of the playback source."""
        if isinstance(self.screen, ModalScreen):
            return
        if self.repeat_mode == RepeatMode.RANDOM and self.queue:
            try:
                self.status("正在准备随机待播队列…")
                await self.prepare_shuffle()
                self.fill_shuffle_remaining()
            except Exception as error:
                self.status(error_text(error))
                return
            if isinstance(self.screen, ModalScreen):
                return
        snapshot = self.pending_tracks.copy()
        if self.repeat_mode == RepeatMode.RANDOM and self.shuffle_pages:
            positions = self.shuffle_history[self.shuffle_cursor + 1 :] + list(
                reversed(self.shuffle_remaining)
            )
            upcoming = [self.shuffle_pages[page][0][index] for page, index in positions]
            more = False
        else:
            upcoming = self.queue[self.index + 1 :]
            more = self.queue_has_more
        source = self.queue_source
        label = (
            source.playlist.title
            if isinstance(source, PlaylistSearch)
            else source.title if isinstance(source, Playlist) else source or "手动待播"
        )
        if self.repeat_mode == RepeatMode.RANDOM:
            label += " · 随机播放"

        def remove(index: int | None) -> None:
            """Apply deletion only to an unchanged snapshot, then refresh the queue view."""
            if index is None:
                return
            if self.pending_tracks == snapshot:
                del self.pending_tracks[index]
                self.save_state()
            else:
                self.status("播放队列已变化，请重新选择要删除的歌曲")
            self.action_queue()

        self.push_screen(
            QueueScreen(
                self.current,
                [entry.track for entry in snapshot],
                upcoming,
                more,
                label,
            ),
            remove,
        )

    @work(exclusive=True, group="view")
    async def action_locate(self) -> None:
        """Return to the active song's original browsing page without replacing playback."""
        track = self.playback_track
        if track is None:
            self.status("尚未播放歌曲")
            return
        source, page = (
            (self.manual_current.source, self.manual_current.page)
            if self.manual_current is not None
            else (self.queue_source, self.queue_page)
        )
        if source is None:
            self.status("当前歌曲没有浏览来源")
            return
        try:
            self.display_page(source, page, await self.fetch(source, page))
            row = next((i for i, item in enumerate(self.tracks) if item.mid == track.mid), None)
            if row is None:
                self.status("已返回播放来源，当前歌曲已不在这一页")
            else:
                self.query_one("#songs", DataTable).move_cursor(row=row)
        except Exception as error:
            self.status(error_text(error))

    def action_refresh(self) -> None:
        """Refresh the active list, rebuilding playlist-filter matches when necessary."""
        if isinstance(self.source, PlaylistSearch):
            self.playlist_cache.pop(self.source.playlist, None)
            self.filter_playlist(self.source.playlist, self.source.keyword)
        elif self.source is not None:
            if isinstance(self.source, Playlist):
                self.playlist_cache.pop(self.source, None)
            self.load_view(self.source, page=self.page)
        else:
            self.load_playlists()

    @work(exclusive=True, group="lyrics")
    async def action_lyrics(self) -> None:
        """Fetch lyrics for the active or highlighted song without blocking playback."""
        if isinstance(self.screen, ModalScreen):
            return
        track = self.quality_track()
        if track is None:
            self.status("请先播放或选中一首歌曲")
            return
        self.status(f"正在加载歌词：{track.title}")
        try:
            lyrics = await self.music.lyrics(track)
            if self.quality_track() != track or isinstance(self.screen, ModalScreen):
                self.status("歌曲已变化，请重新按 F4 查看歌词")
                return
            self.push_screen(
                LyricsScreen(
                    track,
                    lyrics,
                    lambda: (
                        float(self.player.properties.get("time-pos") or 0)
                        if self.current == track
                        else 0
                    ),
                )
            )
            self.status(f"{track.title} · F4/Esc 返回")
        except Exception as error:
            self.status(error_text(error))

    @work(group="library-action")
    async def action_like(self) -> None:
        """Toggle the highlighted song's liked state after successful account requests."""
        track = self.selected_track()
        if track is None or self.library_busy:
            return
        if not self.account_logged_in:
            self.status("请先按 L 登录，再操作喜欢的歌曲")
            return
        self.library_busy = True
        self.status(f"正在更新喜欢状态：{track.title}")
        try:
            liked = await self.music.is_liked(track)
            await self.music.set_liked(track, not liked)
            self.playlist_cache.clear()
            self.load_playlists(
                message=f"{'已取消喜欢' if liked else '已喜欢'}：{track.title} · Ctrl+R 刷新列表"
            )
        except Exception as error:
            self.status(error_text(error))
        finally:
            self.library_busy = False

    @work(group="library-action")
    async def action_add_to_playlist(self) -> None:
        """Fetch writable playlists and ask for a destination for the selected song."""
        track = self.selected_track()
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
                    PlaylistPickerScreen(playlists),
                    lambda playlist: self.add_selected_to_playlist(track, playlist),
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

            def queued(value: dict) -> QueuedTrack:
                """Decode a saved manual song with its original browsing location."""
                return QueuedTrack(
                    decode_track(value["track"]), decode_source(value["source"]), int(value["page"])
                )

            tracks = [decode_track(value) for value in snapshot["queue"]]
            source = decode_source(snapshot["source"])
            index, page = int(snapshot["index"]), int(snapshot["page"])
            track = decode_track(snapshot["track"])
            position = max(0, float(snapshot["position"]))
            pending = [queued(value) for value in snapshot["pending"]]
            manual = queued(snapshot["manual"]) if snapshot["manual"] else None
            pages = {
                int(key): ([decode_track(value) for value in entry["tracks"]], entry["more"])
                for key, entry in snapshot["shuffle_pages"].items()
            }
            remaining = [tuple(value) for value in snapshot["shuffle_remaining"]]
            history = [tuple(value) for value in snapshot["shuffle_history"]]
            cursor = int(snapshot["shuffle_cursor"])
            if page < 1 or (tracks and not 0 <= index < len(tracks)):
                raise ValueError("保存的播放队列位置无效")
            if manual is None and (not tracks or tracks[index] != track):
                raise ValueError("保存的当前歌曲与播放队列不一致")
            if manual is not None and manual.track != track:
                raise ValueError("保存的手动待播歌曲无效")
            for saved_page, saved_index in remaining + history:
                if pages and not 0 <= saved_index < len(pages[saved_page][0]):
                    raise ValueError("保存的随机播放位置无效")
            if history and not 0 <= cursor < len(history):
                raise ValueError("保存的随机播放历史无效")
            self.workers.cancel_group(self, "control")
            self.loading = True
            self.queue_generation += 1
            self.workers.cancel_group(self, "queue-view")
            await self.player.command("stop")
            self.current = None
            self.queue, self.queue_source = tracks, source
            self.queue_page, self.queue_has_more, self.index = page, snapshot["has_more"], index
            self.pending_tracks, self.manual_current = pending, manual
            self.shuffle_pages, self.shuffle_remaining = pages, remaining
            self.shuffle_history, self.shuffle_cursor = history, cursor
            self.playback_position, self.playback_paused = position, bool(snapshot["paused"])
            self.failed_request = None
            await self.load_track(track, self.quality, resume=True)
        except (KeyError, TypeError, IndexError) as error:
            self.status(f"保存的播放会话无效（{type(error).__name__}）")
        except Exception as error:
            self.status(f"{error_text(error)} · F5 重试")
        finally:
            self.loading = False

    def update_heading(self) -> None:
        """Show account status, preferred quality, and repeat mode in the heading."""
        label = "已登录" if self.account_logged_in else "未登录"
        self.query_one("#heading", Static).update(
            f"Music TUI · {self.music.display_name}   {label}   "
            f"{self.quality.label}   {self.repeat_mode.value}"
        )

    def reset_shuffle(self, index: int) -> None:
        """Clear cached shuffle choices and seed history with the selected track."""
        self.shuffle_pages = {}
        self.shuffle_remaining = []
        self.shuffle_history = [(self.queue_page, index)] if self.queue else []
        self.shuffle_cursor = len(self.shuffle_history) - 1

    async def prepare_shuffle(self) -> None:
        """Cache complete source pages before listing or selecting random next songs."""
        if not self.shuffle_pages:
            # Load the full list once; choosing pages at random would bias short pages.
            generation = self.queue_generation
            source = self.queue_source
            pages = {self.queue_page: (self.queue.copy(), self.queue_has_more)}
            if source is not None:
                page, more = 1, True
                while more:
                    if page not in pages:
                        result = await self.fetch(source, page)
                        pages[page] = (result.tracks, result.has_more)
                    tracks, more = pages[page]
                    if not tracks:
                        break
                    page += 1
            if generation != self.queue_generation:
                raise asyncio.CancelledError
            self.shuffle_pages = pages

    def fill_shuffle_remaining(self) -> None:
        """Prepare one nonrepeating random round, retaining any already chosen order."""
        if self.shuffle_remaining:
            return
        current = (self.queue_page, self.index)
        positions = [
            (page, index)
            for page, (tracks, _) in self.shuffle_pages.items()
            for index in range(len(tracks))
        ]
        if len(positions) > 1 and len(self.shuffle_history) == 1:
            positions.remove(current)
        random.shuffle(positions)
        if len(positions) > 1 and positions[-1] == current:
            positions[0], positions[-1] = positions[-1], positions[0]
        self.shuffle_remaining = positions

    async def shuffle_position(self, direction: int) -> tuple[int, int]:
        """Traverse shuffle history or choose uniformly from all queue positions."""
        await self.prepare_shuffle()
        if direction < 0:
            self.shuffle_cursor = max(0, self.shuffle_cursor - 1)
            return self.shuffle_history[self.shuffle_cursor]
        if self.shuffle_cursor + 1 < len(self.shuffle_history):
            self.shuffle_cursor += 1
            return self.shuffle_history[self.shuffle_cursor]
        self.fill_shuffle_remaining()
        position = self.shuffle_remaining.pop()
        self.shuffle_history.append(position)
        self.shuffle_cursor += 1
        return position

    def action_repeat(self) -> None:
        """Cycle playback modes and reset shuffle state when entering random mode."""
        modes = list(RepeatMode)
        self.repeat_mode = modes[(modes.index(self.repeat_mode) + 1) % len(modes)]
        if self.repeat_mode == RepeatMode.RANDOM:
            self.reset_shuffle(self.index)
        self.update_heading()
        self.status(f"{self.repeat_mode.value} · 作用于正在播放的整个列表")


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
