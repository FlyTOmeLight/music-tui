"""Keyboard-driven Music TUI with QQ Music as its current music source."""

import asyncio
import os
import random
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from qqmusic_api.models.login import QRCodeLoginEvents
from rich.text import Text
from terminal_qrcode import draw
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, ListItem, ListView, Static

from .music import Music, Playlist, Quality, SongPage, Track
from .player import Player


def timestamp(seconds: float | None) -> str:
    """Format elapsed seconds as a nonnegative minute-and-second timestamp."""
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60:02}:{seconds % 60:02}"


def error_text(error: Exception) -> str:
    # Network exceptions may contain authenticated URLs; keep those out of the UI.
    """Return a user-facing error without exposing authenticated network URLs."""
    if type(error) in (ValueError, RuntimeError):
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
    #qr-frame { width: 100%; height: auto; }
    #qr { width: auto; height: auto; color: black; background: white; }
    #login-keys { color: $text-muted; }
    """

    def __init__(self, music: Music):
        """Attach the music account used for QR login."""
        super().__init__()
        self.music = music

    def compose(self) -> ComposeResult:
        """Build the QR display, login status, and keyboard instructions."""
        with Vertical(id="login-box"):
            yield Static("QQ 音乐 App 扫码登录", id="login-title")
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
            rendered = draw(qr.data, renderer="halfblock", fit=False, img_width=512).__rich__()
            if rendered.plain.endswith("\n"):
                rendered = rendered[:-1]
            rendered.style = "black on white"
            self.query_one("#qr", Static).update(rendered)
            rows = len(rendered.plain.splitlines()) + 3
            if self.size.height < rows:
                state.update(f"请将终端增高到 {rows} 行，以完整扫码")
            else:
                state.update("请使用 QQ 音乐 App 扫码（3 分钟有效）")
            async for result in session:
                if result.event == QRCodeLoginEvents.CONF:
                    state.update("已扫码，请在 QQ 音乐 App 确认")
                elif result.event == QRCodeLoginEvents.DONE and result.credential:
                    self.music.accept_login(result.credential)
                    self.dismiss(True)
                    return
                elif result.event in (QRCodeLoginEvents.TIMEOUT, QRCodeLoginEvents.REFUSE):
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


@dataclass(frozen=True)
class PlaylistSearch:
    playlist: Playlist
    keyword: str
    tracks: tuple[Track, ...]
    playlist_total: int


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

    def __init__(self, music: Music, player: Player):
        """Initialize library, browsing, playback, and shuffle state."""
        super().__init__()
        self.music = music
        self.player = player
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
        self.index = -1
        self.current: Track | None = None
        self.loading = False
        self.ready = False
        self.table_width = 0
        self.now_playing_text = "尚未播放"
        self.quality = Quality.STANDARD
        self.current_quality: Quality | None = None
        self.account_logged_in = False
        self.repeat_mode = RepeatMode.SEQUENCE
        self.shuffle_pages: dict[int, tuple[list[Track], bool]] = {}
        self.shuffle_remaining: list[tuple[int, int]] = []
        self.shuffle_history: list[tuple[int, int]] = []
        self.shuffle_cursor = -1

    def compose(self) -> ComposeResult:
        """Build the search, playlist, song table, and playback interface."""
        yield Static(
            "Music TUI · QQ 音乐   未登录   标准音质   顺序播放", id="heading", markup=False
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
        yield Static(
            "L 扫码登录 · / 搜索 · Tab 切换区域 · ←/→ 快进退 · −/+ 音量", id="status", markup=False
        )
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

    def action_login(self) -> None:
        """Open QR login after cancelling concurrent credential restoration."""
        if isinstance(self.screen, ModalScreen):
            return
        self.workers.cancel_group(self, "restore")
        self.push_screen(LoginScreen(self.music), self.logged_in)

    def logged_in(self, success: bool) -> None:
        """Update account state and refresh playlists after successful login."""
        if success:
            self.account_logged_in = True
            self.update_heading()
            self.load_playlists()

    @work(exclusive=True, group="library")
    async def load_playlists(self) -> None:
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
            self.status("歌单已更新 · Tab 切换区域 · Enter 打开歌单或播放歌曲")
            if self.source is None and self.playlists:
                self.load_view(self.playlists[0])
        except Exception as error:
            self.status(error_text(error))

    @on(ListView.Selected, "#playlists")
    def select_playlist(self, event: ListView.Selected) -> None:
        """Open the playlist selected in the library sidebar."""
        index = event.list_view.index
        if index is not None and index < len(self.playlists):
            self.load_view(self.playlists[index])

    @on(Input.Submitted, "#search")
    def search_submitted(self, event: Input.Submitted) -> None:
        """Open global search results for a nonempty submitted query."""
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
        if not self.ready:
            self.status("播放器尚未就绪")
            return
        self.queue = self.tracks.copy()
        self.queue_source, self.queue_page = self.source, self.page
        self.queue_has_more = self.has_more
        self.reset_shuffle(event.cursor_row)
        self.play_index(event.cursor_row)

    @work(exclusive=True, group="play")
    async def play_index(self, index: int, *, resume: bool = False, shuffle: int = 0) -> None:
        """Resolve queue boundaries and load a track, optionally preserving playback position."""
        self.loading = True
        quality = self.quality
        try:
            if shuffle:
                self.status("正在准备整个列表的随机顺序…")
                page, index = await self.shuffle_position(shuffle)
                tracks, more = self.shuffle_pages[page]
                self.queue, self.queue_page, self.queue_has_more = tracks, page, more
            if not resume:
                await self.player.command("stop")
                self.current = None
                self.current_quality = None
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
                self.index = len(self.queue) - 1
                self.status("播放队列已结束")
                return
            self.queue, self.queue_page, self.queue_has_more = tracks, page, more
            self.index = index
            track = self.queue[index]
            self.status(f"正在获取：{track.title} · {quality.label}")
            url = await self.music.stream_url(track, quality)
            start = (self.player.properties.get("time-pos") or 0) if resume else 0
            paused = bool(self.player.properties.get("pause")) if resume else False
            await self.player.load(url, start=start, paused=paused)
            self.current = track
            self.current_quality = quality
            self.mark_playing_track()
            self.status("Space 暂停 · N/P 切歌 · U 音质 · ←/→ 快进退 · −/+ 音量")
        except Exception as error:
            self.status(error_text(error))
        finally:
            self.loading = False

    def update_player(self) -> None:
        """Consume mpv events, advance completed tracks, and refresh playback details."""
        if not self.is_running:
            return
        while not self.player.events.empty():
            event = self.player.events.get_nowait()
            if event.get("event") == "end-file" and not self.loading:
                if event.get("playlist_entry_id") != self.player.entry_id:
                    continue
                if event.get("reason") == "eof" and self.current:
                    if self.repeat_mode == RepeatMode.SINGLE:
                        self.play_index(self.index)
                    else:
                        self.action_next()
                elif event.get("reason") == "error":
                    self.current = None
                    self.status("音频加载失败，按 Enter 重试或 N 下一首")
            elif event.get("event") == "disconnected":
                self.ready = False
                self.current = None
                self.status("播放器已退出，请重启 music-tui")
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

    @work
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
        if self.queue:
            self.play_index(
                self.index + 1, shuffle=1 if self.repeat_mode == RepeatMode.RANDOM else 0
            )

    def action_previous(self) -> None:
        """Return to the previous queue track or shuffle-history entry."""
        if self.queue:
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

    def update_heading(self) -> None:
        """Show account status, preferred quality, and repeat mode in the heading."""
        label = "已登录" if self.account_logged_in else "未登录"
        self.query_one("#heading", Static).update(
            f"Music TUI · QQ 音乐   {label}   {self.quality.label}   {self.repeat_mode.value}"
        )

    def reset_shuffle(self, index: int) -> None:
        """Clear cached shuffle choices and seed history with the selected track."""
        self.shuffle_pages = {}
        self.shuffle_remaining = []
        self.shuffle_history = [(self.queue_page, index)] if self.queue else []
        self.shuffle_cursor = len(self.shuffle_history) - 1

    async def shuffle_position(self, direction: int) -> tuple[int, int]:
        """Traverse shuffle history or choose uniformly from all queue positions."""
        if not self.shuffle_pages:
            # Load the full list once; choosing pages at random would bias short pages.
            pages = {self.queue_page: (self.queue.copy(), self.queue_has_more)}
            if self.queue_source is not None:
                page, more = 1, True
                while more:
                    if page not in pages:
                        result = await self.fetch(self.queue_source, page)
                        pages[page] = (result.tracks, result.has_more)
                    tracks, more = pages[page]
                    if not tracks:
                        break
                    page += 1
            self.shuffle_pages = pages
        if direction < 0:
            self.shuffle_cursor = max(0, self.shuffle_cursor - 1)
            return self.shuffle_history[self.shuffle_cursor]
        if self.shuffle_cursor + 1 < len(self.shuffle_history):
            self.shuffle_cursor += 1
            return self.shuffle_history[self.shuffle_cursor]
        current = (self.queue_page, self.index)
        if not self.shuffle_remaining:
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


async def run() -> None:
    """Run the interface with QQ Music and mpv, then close both resources."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    music = Music(data_home / "qqmusic-tui")
    player = Player()
    try:
        await MusicApp(music, player).run_async()
    finally:
        await player.close()
        await music.close()


def main() -> None:
    """Start the asynchronous application from the command-line entry point."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
