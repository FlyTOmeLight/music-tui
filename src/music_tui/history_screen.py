"""Show recent account playback with actions to replay a song or reopen its source."""

from collections.abc import Callable
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, Static, Tab, Tabs

from .models import Track


@dataclass(frozen=True)
class HistoryEntry:
    """Combine decoded song metadata and display labels with the original saved record."""

    track: Track
    source_label: str
    played_at: str
    record: dict


class HistoryScreen(Screen[tuple[str, dict] | None]):
    """Browse this account's recent songs without changing playback until selection."""

    BINDINGS = [
        Binding("escape,f8", "cancel", "返回", priority=True),
        Binding("o", "open_source", "打开来源", priority=True),
        Binding("tab", "toggle_mode", "切换歌曲/来源", priority=True),
    ]
    DEFAULT_CSS = """
    HistoryScreen { background: $background; color: $foreground; }
    HistoryScreen #navigation { height: 1; background: $surface; }
    HistoryScreen #navigation Underline { display: none; }
    HistoryScreen #navigation Tab { color: $text-muted; }
    HistoryScreen #navigation Tab.-active { color: $accent; text-style: bold; }
    HistoryScreen #history-page { height: 1fr; padding: 0 2; }
    HistoryScreen #history-title { height: 1; margin-top: 1; text-style: bold; }
    HistoryScreen #history-subtitle { height: 1; color: $text-muted; }
    HistoryScreen #history-mode { height: 2; margin-top: 1; }
    HistoryScreen #history-items { height: 1fr; background: $background; }
    HistoryScreen #history-items ListItem { height: 3; padding: 0 1; }
    HistoryScreen #history-items ListItem.-highlight { background: $block-cursor-background; }
    HistoryScreen #history-items ListItem.-highlight Label { color: $foreground; }
    HistoryScreen #history-items ListItem .history-detail {
        color: $text-muted; text-style: none;
    }
    HistoryScreen #history-empty { height: 1fr; padding: 1; color: $text-muted; }
    HistoryScreen #history-keys {
        dock: bottom; height: 3; padding: 0 2;
        border-top: solid $accent; background: $surface; color: $text-muted;
    }
    """

    def __init__(
        self,
        entries: list[HistoryEntry],
        on_navigate: Callable[[str], None] | None = None,
    ):
        """Take a newest-first snapshot and return actions with its original record."""
        super().__init__()
        self.on_navigate = on_navigate
        self.entries = list(entries)
        self.source_mode = False
        self.sources = []
        seen = set()
        for entry in self.entries:
            source = entry.record.get("source")
            if source is None:
                continue
            if isinstance(source, dict) and "artist" in source:
                key = ("artist", source["artist"]["id"], source["order"])
            else:
                key = (
                    ("playlist", str(source["id"]))
                    if isinstance(source, dict)
                    else ("search", source)
                )
            if key not in seen:
                seen.add(key)
                self.sources.append(entry)

    @property
    def visible_entries(self) -> list[HistoryEntry]:
        """Return recent songs or the newest record for each distinct source."""
        return self.sources if self.source_mode else self.entries

    def compose(self) -> ComposeResult:
        """Keep controls visible while song and source labels scroll as literal text."""
        yield Tabs(
            Tab("歌曲", id="browse"),
            Tab("播放", id="playback"),
            Tab("最近", id="history"),
            id="navigation",
            active="history",
        )
        with Vertical(id="history-page"):
            yield Static(
                "最近来源" if self.source_mode else "最近播放",
                markup=False,
                id="history-title",
            )
            yield Static(
                f"当前账号 · 最近 {len(self.visible_entries)} "
                f"{'个来源' if self.source_mode else '首歌曲'}",
                markup=False,
                id="history-subtitle",
            )
            yield Tabs(
                Tab("最近歌曲", id="recent-songs"),
                Tab("最近来源", id="recent-sources"),
                id="history-mode",
                active="recent-sources" if self.source_mode else "recent-songs",
            )
            if self.visible_entries:
                items = []
                for entry in self.visible_entries:
                    date = entry.played_at.replace("T", " ")[:16]
                    song = f"{entry.track.title} · {entry.track.artist}"
                    title = entry.source_label if self.source_mode else song
                    detail = song if self.source_mode else entry.source_label or "无来源"
                    items.append(
                        ListItem(
                            Label(title, markup=False),
                            Label(
                                f"{detail} · {date} UTC",
                                markup=False,
                                classes="history-detail",
                            ),
                        )
                    )
                yield ListView(*items, id="history-items")
            else:
                yield Static(
                    (
                        "暂无可打开的播放来源。先从歌单或搜索结果播放歌曲。"
                        if self.source_mode
                        else "暂无最近播放。登录后播放歌曲，会保留最近 100 首。"
                    ),
                    markup=False,
                    id="history-empty",
                )
        yield Static(
            f"↑/↓ 选择 · Enter {'打开来源' if self.source_mode else '播放'} · O 打开来源\n"
            f"Tab {'最近歌曲' if self.source_mode else '最近来源'} · F8/Esc 返回",
            markup=False,
            id="history-keys",
        )

    def on_mount(self) -> None:
        """Focus the recent-song list so keyboard selection works immediately."""
        if self.visible_entries:
            self.query_one("#history-items", ListView).focus()

    async def action_toggle_mode(self) -> None:
        """Switch between recent songs and deduplicated sources with the same exit keys."""
        self.source_mode = not self.source_mode
        await self.recompose()
        self.on_mount()

    @on(Tabs.TabActivated, "#history-mode")
    async def select_history_mode(self, event: Tabs.TabActivated) -> None:
        """Switch the recent-song and source list when its visible tab is selected."""
        event.stop()
        source_mode = event.tab.id == "recent-sources"
        if source_mode != self.source_mode:
            await self.action_toggle_mode()

    @on(Tabs.TabActivated, "#navigation")
    def navigate(self, event: Tabs.TabActivated) -> None:
        """Ask the app to open the selected main page without changing playback."""
        event.stop()
        if event.tab.id != "history" and self.on_navigate is not None:
            self.on_navigate(event.tab.id)

    @on(ListView.Selected, "#history-items")
    def play_selected(self, event: ListView.Selected) -> None:
        """Return the highlighted record for playback without interpreting its metadata."""
        if event.list_view.index is not None:
            action = "open" if self.source_mode else "play"
            self.dismiss((action, self.visible_entries[event.list_view.index].record))

    def action_open_source(self) -> None:
        """Return the highlighted song's origin, or explain when no origin was saved."""
        if not self.visible_entries:
            return
        index = self.query_one("#history-items", ListView).index
        if index is None:
            return
        record = self.visible_entries[index].record
        if record.get("source") is None:
            self.query_one("#history-keys", Static).update("此歌曲没有可打开的来源 · F8/Esc 返回")
            return
        self.dismiss(("open", record))

    def action_cancel(self) -> None:
        """Close recent playback and restore the previous screen's focus."""
        self.dismiss(None)
