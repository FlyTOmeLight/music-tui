"""Clickable artist identities, profile presentation, and keyboard selection."""

from dataclasses import dataclass

from rich.style import Style
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Static

from .artwork import AlbumArtwork
from .models import Artist, ArtistDetail, SongPage, Track
from .playback_session import PlaybackSource


def artist_names(track: Track) -> Text:
    """Render individually clickable identities while preserving literal names."""
    if not track.artists:
        return Text(track.artist)
    text = Text()
    for artist in track.artists:
        if text:
            text.append(" / ")
        text.append(
            artist.name,
            Style(
                underline=True, meta={"@click": f"app.open_artist({artist.id!r}, {artist.name!r})"}
            ),
        )
    return text


@dataclass
class ArtistReturn:
    """Retain the exact browser and screen to which artist exploration returns."""

    source: PlaybackSource
    page: int
    result: SongPage
    sort_order: int
    row: int
    scroll_y: float
    focus_id: str | None
    screen: str
    playback_tab: str
    detail: ArtistDetail | None


class ArtistPanel(Vertical):
    """Keep optional artwork and biography above the shared song browser."""

    DEFAULT_CSS = """
    ArtistPanel { height: 5; display: none; background: $surface; }
    #artist-summary { height: 4; }
    #artist-portrait { width: 8; height: 4; margin-right: 1; }
    #artist-bio { width: 1fr; height: 4; text-overflow: ellipsis; }
    #artist-actions { height: 1; }
    #artist-actions Button {
        height: 1; width: 1fr; min-width: 0; border: none; padding: 0 1;
        background: $surface; color: $accent;
    }
    #artist-actions Button:focus { background: $block-cursor-background; }
    """

    def compose(self) -> ComposeResult:
        """Present a compact profile with return and global-order controls."""
        with Horizontal(id="artist-summary"):
            yield AlbumArtwork(id="artist-portrait")
            yield Static("", id="artist-bio", markup=False)
        with Horizontal(id="artist-actions"):
            yield Button("返回", id="artist-back", compact=True)
            yield Button("热门 / 最新", id="artist-order", compact=True)

    def show_detail(self, detail: ArtistDetail) -> None:
        """Show the identity even when optional artwork or biography is absent."""
        self.query_one(AlbumArtwork).set_cover(detail.cover_url)
        self.query_one("#artist-bio", Static).update(
            f"{detail.artist.name}\n{detail.description.strip() or '暂无简介'}"
        )


class ArtistPicker(ModalScreen[Artist | None]):
    """Let keyboard users choose an exact contributor to a duet or group song."""

    BINDINGS = [Binding("escape", "cancel", "返回")]
    DEFAULT_CSS = """
    ArtistPicker { align: center middle; background: $background 95%; }
    #artist-picker { width: 44; max-width: 100%; height: auto; padding: 1 2;
                     border: solid $accent; background: $surface; }
    #artist-choices { height: auto; max-height: 12; background: transparent; }
    #artist-choices ListItem { height: 2; }
    """

    def __init__(self, artists: tuple[Artist, ...]):
        """Retain the contributors in their source-reported order."""
        super().__init__()
        self.artists = artists

    def compose(self) -> ComposeResult:
        """Display artist names without interpreting user-facing text as markup."""
        with Vertical(id="artist-picker"):
            yield Static("选择歌手")
            yield ListView(
                *(ListItem(Label(a.name, markup=False)) for a in self.artists),
                id="artist-choices",
            )
            yield Static("↑/↓ 选择 · Enter 确认 · Esc 返回")

    def on_mount(self) -> None:
        """Focus the contributor list for immediate keyboard selection."""
        self.query_one(ListView).focus()

    @on(ListView.Selected)
    def select(self, event: ListView.Selected) -> None:
        """Return the contributor selected by identity rather than display text."""
        if event.list_view.index is not None:
            self.dismiss(self.artists[event.list_view.index])

    def action_cancel(self) -> None:
        """Close the picker without entering a profile."""
        self.dismiss(None)
