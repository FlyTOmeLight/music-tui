"""Exercise search like symbols and actual mouse and keyboard interactions."""

import asyncio

from test_app import Audio, Library
from textual.widgets import DataTable, Static

from music_tui.app import MusicApp
from music_tui.models import SongPage, Track

TRACKS = [Track("z", "Zulu", "Singer", 80), Track("a", "Alpha", "Singer", 60)]


class LikesLibrary(Library):
    """Expose deterministic account-scoped search likes and fallible writes."""

    account_id = "user-one"

    def __init__(self):
        """Start with one confirmed like and observable offline server writes."""
        super().__init__()
        self.ids = {"z"}
        self.writes = []
        self.fail_write = False
        self.query_started = asyncio.Event()
        self.query_release = None

    async def restore(self):
        """Restore the fake authenticated account."""
        return bool(self.account_id)

    async def playlists(self):
        """Keep the search view available without additional fake playlists."""
        return []

    async def search(self, keyword, page):
        """Return songs whose natural and title orders differ."""
        return SongPage(TRACKS.copy(), False, 2)

    async def liked_ids(self):
        """Return a snapshot, optionally holding an old account's response."""
        ids = self.ids.copy()
        self.query_started.set()
        if self.query_release is not None:
            await self.query_release.wait()
        return ids

    async def set_liked(self, track, liked):
        """Record successful writes and preserve server state for rejected writes."""
        if self.fail_write:
            raise ValueError("写入失败")
        self.writes.append((track.mid, liked))
        if liked:
            self.ids.add(track.mid)
        else:
            self.ids.discard(track.mid)


def symbols(app):
    """Read title prefixes in the current visible table order."""
    table = app.query_one("#songs", DataTable)
    return [table.get_row(row.key)[1].plain[0] for row in table.ordered_rows]


async def test_mouse_like_and_keyboard_after_sort_target_actual_song():
    """A real symbol click and S use song identities without starting playback."""
    music, audio = LikesLibrary(), Audio()
    app = MusicApp(music, audio)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        await pilot.pause()
        assert symbols(app) == ["♥", "♡"]
        table = app.query_one("#songs", DataTable)
        position = table.ordered_columns[0]
        x = position.width + table.cell_padding * 2 + table.cell_padding
        await pilot.click("#songs", offset=(x, 1))
        await pilot.pause()
        assert music.writes == [("z", False)]
        assert symbols(app) == ["♡", "♡"]
        assert audio.loaded == []
        await pilot.press("o")
        await pilot.pause()
        table.move_cursor(row=0)
        await pilot.press("s")
        await pilot.pause()
        assert music.writes[-1] == ("a", True)
        assert symbols(app) == ["♥", "♡"]
        assert audio.loaded == []


async def test_rejected_write_keeps_confirmed_symbol_and_shows_error():
    """A server rejection cannot optimistically change a displayed confirmation."""
    music = LikesLibrary()
    music.fail_write = True
    app = MusicApp(music, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        await pilot.pause()
        table = app.query_one("#songs", DataTable)
        table.focus()
        await pilot.press("s")
        await pilot.pause()
        assert symbols(app) == ["♥", "♡"]
        assert "写入失败" in str(app.query_one("#status", Static).render())
        assert music.writes == []


async def test_logged_out_search_shows_unknown_and_requires_login():
    """Logged-out rows remain unknown and clicking a symbol prompts login."""
    music = LikesLibrary()
    music.account_id = ""
    app = MusicApp(music, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        await pilot.pause()
        assert symbols(app) == ["?", "?"]
        table = app.query_one("#songs", DataTable)
        table.focus()
        await pilot.press("s")
        await pilot.pause()
        assert "登录" in str(app.query_one("#status", Static).render())
        assert music.writes == []


async def test_late_like_list_does_not_show_previous_account_states():
    """An old-account response leaves new-account search symbols unknown."""
    music = LikesLibrary()
    music.query_release = asyncio.Event()
    app = MusicApp(music, Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        async with asyncio.timeout(2):
            await music.query_started.wait()
        assert symbols(app) == ["?", "?"]
        music.account_id = "user-two"
        app.search_likes.bind(music)
        music.query_release.set()
        await pilot.pause()
        app.draw_tracks()
        assert symbols(app) == ["?", "?"]
        music.query_release = None
        music.ids = {"a"}
        await app.refresh_search_likes().wait()
        await pilot.pause()
        assert symbols(app) == ["♡", "♥"]
