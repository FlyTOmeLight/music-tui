"""Show recent account playback with actions to replay a song or reopen its source."""

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from .models import Track
from .screens import DIALOG_CSS


@dataclass(frozen=True)
class HistoryEntry:
    """Combine decoded song metadata and display labels with the original saved record."""

    track: Track
    source_label: str
    played_at: str
    record: dict


class HistoryScreen(ModalScreen[tuple[str, dict] | None]):
    """Browse this account's recent songs without changing playback until selection."""

    BINDINGS = [
        Binding("escape,f8", "cancel", "返回", priority=True),
        Binding("o", "open_source", "打开来源", priority=True),
        Binding("tab", "toggle_mode", "切换歌曲/来源", priority=True),
    ]
    SCOPED_CSS = False
    DEFAULT_CSS = DIALOG_CSS

    def __init__(self, entries: list[HistoryEntry]):
        """Take a newest-first snapshot and return actions with its original record."""
        super().__init__()
        self.entries = list(entries)
        self.source_mode = False
        self.sources = []
        seen = set()
        for entry in self.entries:
            source = entry.record.get("source")
            if source is None:
                continue
            key = (
                ("playlist", str(source["id"])) if isinstance(source, dict) else ("search", source)
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
        self.add_class("feature-dialog")
        with Vertical(classes="dialog-box"):
            yield Static(
                "最近来源" if self.source_mode else "最近播放",
                markup=False,
                classes="dialog-title",
            )
            yield Static(
                f"当前账号 · 最近 {len(self.visible_entries)} "
                f"{'个来源' if self.source_mode else '首歌曲'}",
                markup=False,
                classes="dialog-subtitle",
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
                                classes="dialog-subtitle",
                            ),
                        )
                    )
                yield ListView(*items, id="history-items", classes="dialog-body")
            else:
                yield Static(
                    (
                        "暂无可打开的播放来源。先从歌单或搜索结果播放歌曲。"
                        if self.source_mode
                        else "暂无最近播放。登录后播放歌曲，会保留最近 100 首。"
                    ),
                    markup=False,
                    id="history-empty",
                    classes="dialog-body",
                )
            yield Static(
                f"↑/↓ 选择 · Enter {'打开来源' if self.source_mode else '播放'} · O 打开来源\n"
                f"Tab {'最近歌曲' if self.source_mode else '最近来源'} · F8/Esc 返回",
                markup=False,
                id="history-keys",
                classes="dialog-keys",
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
