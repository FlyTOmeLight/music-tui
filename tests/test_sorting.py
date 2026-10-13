"""Verify current-page sorting preserves song identities and active playback order."""

import pytest
from test_app import Audio, Library
from textual.widgets import Button, DataTable

from music_tui.app import MusicApp
from music_tui.models import Playlist, SongPage, Track

TRACKS = [
    Track("z", "Zulu", "Alice", 80),
    Track("b", "Bravo", "Charlie", 100),
    Track("a", "Alpha", "Bob", 20),
]


class SortingLibrary(Library):
    """Return deliberately different title, artist and numeric duration orders."""

    account_id = "sorting-user"

    def __init__(self):
        """Record writes without making requests to a real account."""
        super().__init__()
        self.writes = []
        self.editable = Playlist(1, "可写歌单", editable=True)

    async def restore(self):
        """Provide a logged-in account for selected-row library actions."""
        return True

    async def playlists(self):
        """Expose one writable playlist for the destination picker."""
        return [self.editable]

    async def liked_ids(self):
        """Start with no liked songs, matching selected-row shortcut tests."""
        return set()

    async def is_liked(self, track):
        """Report an unliked song so the shortcut performs an observable write."""
        return False

    async def set_liked(self, track, liked):
        """Record which visible song was liked."""
        self.writes.append(("like", track, liked))

    async def add_to_playlist(self, track, playlist):
        """Record which visible song was added to the destination."""
        self.writes.append(("add", track, playlist))

    async def search(self, keyword, page):
        """Provide two pages so sorting scope and playback origins remain observable."""
        tracks = TRACKS if page == 1 else [Track("next", "Next", "Artist", 90)]
        return SongPage(tracks.copy(), page == 1, 4, 3)


def displayed_ids(table: DataTable) -> list[str]:
    """Read rendered row identities in their visible order."""
    return [TRACKS[int(row.key.value)].mid for row in table.ordered_rows]


@pytest.mark.parametrize(
    ("column", "ascending"),
    [("title", ["a", "b", "z"]), ("artist", ["z", "a", "b"]), ("duration", ["a", "z", "b"])],
)
async def test_header_sort_toggles_direction_and_sequence_restores_source(column, ascending):
    """Mouse headers sort real rows in both directions and retain the selected song."""
    app = MusicApp(SortingLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.pause()
        table = app.query_one("#songs", DataTable)
        table.move_cursor(row=1)
        x = 0
        for entry in table.ordered_columns:
            if entry.key.value == column:
                break
            x += entry.width + table.cell_padding * 2
        await pilot.click("#songs", offset=(x + 1, 0))
        await pilot.pause()
        assert displayed_ids(table) == ascending
        assert app.selected_track() == TRACKS[1]
        assert app.quality_track() == TRACKS[1]
        assert "↑" in table.columns[column].label.plain
        await pilot.click("#songs", offset=(x + 1, 0))
        await pilot.pause()
        assert displayed_ids(table) == ascending[::-1]
        assert "↓" in table.columns[column].label.plain
        await pilot.click("#songs", offset=(1, 0))
        await pilot.pause()
        assert displayed_ids(table) == [track.mid for track in TRACKS]
        assert app.tracks == TRACKS and app.selected_track() == TRACKS[1]


async def test_keyboard_sort_library_actions_and_locate_use_visible_song_identity():
    """Sorting directs Enter/A/S/W to displayed songs and leaves active playback intact."""
    library, player = SortingLibrary(), Audio()
    app = MusicApp(library, player)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        table = app.query_one("#songs", DataTable)
        await pilot.press("o")
        assert displayed_ids(table) == ["a", "b", "z"]
        assert app.current == TRACKS[0] and app.playback_queue.view(
            app.repeat_mode
        ).tracks == tuple(TRACKS)
        assert app.selected_track() == TRACKS[0]
        assert "▸" in str(table.get_cell("0", "position"))
        table.move_cursor(row=0)
        await pilot.press("a", "s")
        await pilot.pause()
        assert app.playback_queue.view(app.repeat_mode).pending[0].track == TRACKS[2]
        assert library.writes[-1] == ("like", TRACKS[2], True)
        await pilot.press("w")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert library.writes[-1] == ("add", TRACKS[2], library.editable)
        assert (
            app.playback_queue.view(app.repeat_mode).tracks == tuple(TRACKS)
            and app.current == TRACKS[0]
        )
        await pilot.press("g")
        await pilot.pause()
        assert table.cursor_row == 2 and app.selected_track() == TRACKS[0]
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        assert player.loaded[-1] == "a" and app.current == TRACKS[2]
        assert app.playback_queue.view(app.repeat_mode).tracks == tuple(TRACKS)


async def test_sort_control_cycles_back_to_original_and_applies_only_to_loaded_page():
    """Inline sorting stays within one page and fits beside metadata in a small terminal."""
    app = MusicApp(SortingLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        app.display_page(Playlist(1, "歌单"), 1, SongPage(TRACKS.copy(), True, 4, 3))
        await pilot.pause()
        table = app.query_one("#songs", DataTable)
        control = app.query_one("#sort-order", Button)
        assert table.region.height >= 8 and control.region.height == 1
        assert "本页" in str(control.label)
        await pilot.click("#sort-order")
        await pilot.pause()
        assert displayed_ids(table) == ["a", "b", "z"]
        table.focus()
        await pilot.press(*(["o"] * 6))
        assert displayed_ids(table) == ["z", "b", "a"]
        await app.load_view("original", page=2).wait()
        await pilot.press("o")
        assert table.row_count == 1 and app.page == 2
        assert app.tracks[0].mid == "next"
