"""Exercise artist browsing, transport and source restoration through the real app."""

import asyncio

import pytest
from test_app import Audio, Library
from test_playback_features import FeatureAudio
from textual.widgets import Button, DataTable, Static

from music_tui.app import MusicApp
from music_tui.artists import ArtistPanel, ArtistPicker
from music_tui.history_screen import HistoryScreen
from music_tui.models import Artist, ArtistDetail, ArtistOrder, SongPage, Track
from music_tui.playback_screen import PlaybackScreen
from music_tui.playback_session import ArtistSongs, decode_source
from music_tui.session import SessionStore

SINGER = Artist("1", "歌手甲")
GUEST = Artist("2", "歌手乙")
DUET = Track("duet", "合唱歌曲", "歌手甲 / 歌手乙", 120, artists=(SINGER, GUEST))
SONGS = tuple(Track(str(i), f"歌曲{i}", SINGER.name, 60, artists=(SINGER,)) for i in range(1, 6))


class ArtistLibrary(Library):
    """Provide two-page artist orders and exact contributors without external access."""

    account_id = "artist-account"

    def __init__(self):
        """Record artist page requests while retaining the standard library fake."""
        super().__init__()
        self.artist_requests = []

    async def search(self, keyword, page):
        """Keep a duet on both browser pages for keyboard and pointer entry."""
        return SongPage([DUET, SONGS[0]], page == 1, 4, 2)

    async def artist_detail(self, artist):
        """Return a profile with absent optional portrait and description."""
        return ArtistDetail(artist)

    async def artist_songs(self, artist, order, page=1):
        """Apply ordering to all songs before slicing into two-song pages."""
        self.artist_requests.append((artist, order, page))
        songs = list(SONGS) if order is ArtistOrder.HOT else list(reversed(SONGS))
        start = (page - 1) * 2
        return SongPage(songs[start : start + 2], start + 2 < len(songs), len(songs), 2)


async def click_artist(pilot, app, artist, widget=None):
    """Click a rendered Rich metadata hotspot rather than invoking an app action."""
    region = widget.region if widget is not None else app.screen.region
    for y in range(region.y, region.bottom):
        for x in range(region.x, region.right):
            action = app.screen._compositor.get_style_at(x, y).meta.get("@click", "")
            if action == f"app.open_artist({artist.id!r}, {artist.name!r})":
                await pilot.click(offset=(x, y))
                return
    pytest.fail(f"No visible contributor hotspot for {artist.name}")


async def test_keyboard_duet_choice_and_return_restore_browser_page_and_focus():
    """Choose a contributor without playing and return to the original selected row."""
    app = MusicApp(ArtistLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query", page=2).wait()
        table = app.query_one("#songs", DataTable)
        table.move_cursor(row=0)
        await pilot.press("i")
        assert isinstance(app.screen, ArtistPicker)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.source == ArtistSongs(GUEST)
        assert app.player.loaded == []
        assert "暂无简介" in str(app.query_one("#artist-bio", Static).render())
        await pilot.click("#artist-back")
        await pilot.pause()
        assert app.source == "query" and app.page == 2
        assert table.cursor_row == 0 and app.focused is table
        assert not app.query_one(ArtistPanel).display


@pytest.mark.parametrize("artist", [SINGER, GUEST])
async def test_each_duet_contributor_is_individually_clickable(artist):
    """Each artist name opens its exact profile without selecting the song for playback."""
    app = MusicApp(ArtistLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        await pilot.pause()
        await click_artist(pilot, app, artist)
        await pilot.pause()
        assert app.source == ArtistSongs(artist)
        assert app.player.loaded == []


@pytest.mark.parametrize("entry", ["bottom", "playback"])
async def test_current_song_artist_entry_returns_to_original_screen(entry):
    """Open an exact contributor from either player view and preserve active audio."""
    app = MusicApp(ArtistLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.update_player()
        if entry == "playback":
            await pilot.press("f7")
            await pilot.pause()
        original = app.screen
        target = "#playback-artist" if entry == "playback" else "#now-playing"
        await click_artist(pilot, app, GUEST, app.screen.query_one(target))
        await pilot.pause()
        assert app.source == ArtistSongs(GUEST)
        assert app.current == DUET and app.player.loaded == ["duet"]
        await pilot.click("#artist-back")
        await pilot.pause()
        assert isinstance(app.screen, PlaybackScreen) is (entry == "playback")
        if entry == "bottom":
            assert app.screen is original
        assert app.source == "query" and app.current == DUET


@pytest.mark.parametrize("order", list(ArtistOrder))
async def test_artist_selection_plays_across_pages_in_global_order(order):
    """The selected ordering continues beyond the visible page while browsing stays put."""
    library, audio = ArtistLibrary(), Audio()
    app = MusicApp(library, audio)
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        app.action_open_artist(SINGER.id, SINGER.name)
        await pilot.pause()
        if order is ArtistOrder.LATEST:
            await pilot.click("#artist-order")
            await pilot.pause()
        assert app.source == ArtistSongs(SINGER, order)
        expected = list(SONGS) if order is ArtistOrder.HOT else list(reversed(SONGS))
        app.query_one("#songs", DataTable).move_cursor(row=1)
        await pilot.press("enter")
        await pilot.pause()
        assert app.current == expected[1]
        await pilot.press("n")
        await pilot.pause()
        assert app.current == expected[2]
        assert app.page == 1
        assert (SINGER, order, 2) in library.artist_requests
        assert app.playback_origin.source == ArtistSongs(SINGER, order)


async def test_browsing_and_changing_artist_order_does_not_replace_playback():
    """Profile navigation and sorting preserve the current song and original queue."""
    app = MusicApp(ArtistLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        await pilot.press("enter")
        await pilot.pause()
        app.action_open_artist(SINGER.id, SINGER.name)
        await pilot.pause()
        await pilot.click("#artist-order")
        await pilot.pause()
        assert app.current == DUET and app.player.loaded == ["duet"]
        assert app.playback_origin.source == "query"


async def test_artist_profile_failure_leaves_songs_playable():
    """An optional biography failure reports the problem without hiding loaded songs."""

    class FailingProfile(ArtistLibrary):
        async def artist_detail(self, artist):
            """Simulate an unavailable optional metadata endpoint."""
            raise ValueError("简介不可用")

    app = MusicApp(FailingProfile(), Audio())
    async with app.run_test() as pilot:
        await app.load_view(ArtistSongs(SINGER)).wait()
        assert app.query_one("#songs", DataTable).row_count == 2
        assert "简介不可用" in str(app.query_one("#status", Static).render())
        await pilot.press("enter")
        await pilot.pause()
        assert app.current == SONGS[0]


async def test_late_artist_profile_cannot_overwrite_new_browser():
    """A cancelled optional request that finishes late cannot reopen an old profile."""
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class SlowProfile(ArtistLibrary):
        async def artist_detail(self, artist):
            """Return stale profile data even when transport cancellation arrives."""
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            finished.set()
            return ArtistDetail(artist, description="过期简介")

    app = MusicApp(SlowProfile(), Audio())
    async with app.run_test() as pilot:
        app.load_view(ArtistSongs(SINGER))
        async with asyncio.timeout(2):
            await started.wait()
        await app.load_view("new").wait()
        release.set()
        async with asyncio.timeout(2):
            await finished.wait()
        await pilot.pause()
        assert app.source == "new" and app.tracks == [DUET, SONGS[0]]
        assert not app.query_one(ArtistPanel).display


async def test_artist_history_and_session_restore_keep_order_and_cross_page_playback(tmp_path):
    """Saved artist origins retain identities and ordering for resume and subsequent songs."""

    class SignedArtistLibrary(ArtistLibrary):
        async def restore(self):
            """Restore the account owning saved history and playback sessions."""
            return True

        async def playlists(self):
            """Keep an authenticated library with no sidebar playlists."""
            return []

        async def liked_ids(self):
            """Provide the account's empty liked-song set for search rendering."""
            return set()

    store = SessionStore(tmp_path)
    first = MusicApp(SignedArtistLibrary(), FeatureAudio(), session_store=store)
    origin = ArtistSongs(SINGER, ArtistOrder.LATEST)
    async with first.run_test() as pilot:
        await first.load_view(origin).wait()
        first.query_one("#songs", DataTable).move_cursor(row=1)
        await pilot.press("enter")
        await pilot.pause()
        first.player.properties["time-pos"] = 12
        first.save_state()
        record = store.history("qq", "artist-account")[0]
        assert decode_source(record["source"]) == origin
    restored = MusicApp(SignedArtistLibrary(), FeatureAudio(), session_store=store)
    async with restored.run_test() as pilot:
        await pilot.press("f6")
        await pilot.pause()
        assert restored.current == SONGS[3]
        assert restored.player.load_options[-1] == (12, False)
        assert restored.playback_origin.source == origin
        await pilot.press("n")
        await pilot.pause()
        assert restored.current == SONGS[2]
        await pilot.press("g")
        await pilot.pause()
        assert restored.source == origin
        before = restored.player.loaded.copy()
        await pilot.press("f8")
        assert isinstance(restored.screen, HistoryScreen)
        await pilot.press("tab", "enter")
        await pilot.pause()
        assert restored.source == origin and restored.page == 2
        assert restored.player.loaded == before
        await pilot.press("f8")
        assert isinstance(restored.screen, HistoryScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert restored.current == SONGS[2]
        assert restored.playback_origin.source == origin
        assert restored.playback_queue.view(restored.repeat_mode).tracks == (SONGS[2],)


async def test_locate_discards_cancelled_artist_response_and_loads_saved_profile():
    """A delayed artist request cannot replace G's source; locating refreshes its profile."""

    class DelayedLibrary(ArtistLibrary):
        """Hold one cancellable request while supplying distinguishable biographies."""

        def __init__(self):
            """Own gates for an upstream response that finishes after cancellation."""
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()
            self.delay = False

        async def artist_songs(self, artist, order, page=1):
            """Finish a cancelled response only when the test releases it."""
            if self.delay:
                self.delay = False
                self.started.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    await self.release.wait()
                self.finished.set()
            return await super().artist_songs(artist, order, page)

        async def artist_detail(self, artist):
            """Return a profile visibly tied to the requested artist."""
            return ArtistDetail(artist, description=f"{artist.name}的简介")

    music = DelayedLibrary()
    app = MusicApp(music, Audio())
    async with app.run_test() as pilot:
        await app.load_view("query").wait()
        await pilot.press("enter")
        await pilot.pause()
        music.delay = True
        app.action_open_artist(SINGER.id, SINGER.name)
        async with asyncio.timeout(3):
            await music.started.wait()
            await app.action_locate().wait()
            assert app.source == "query"
            music.release.set()
            await music.finished.wait()
        await pilot.pause()
        assert app.source == "query"
        await app.load_view(ArtistSongs(SINGER)).wait()
        await pilot.press("enter")
        await pilot.pause()
        await app.load_view(ArtistSongs(GUEST)).wait()
        await app.action_locate().wait()
        assert app.source == ArtistSongs(SINGER)
        assert "歌手甲的简介" in str(app.query_one("#artist-bio", Static).render())


async def test_real_browser_bottom_transport_reuses_playback_and_fits_80_by_24():
    """Pointer transport follows the same queue while every label remains visible."""
    app = MusicApp(ArtistLibrary(), Audio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("query").wait()
        assert app.query_one("#bottom-next", Button).disabled
        await pilot.press("enter")
        await pilot.pause()
        app.update_player()
        for name in ("previous", "pause", "next"):
            button = app.query_one(f"#bottom-{name}", Button)
            assert button.region.height == 1 and button.region.bottom <= 24
            assert button.region.right <= 80
            button.focus()
            await pilot.pause()
            assert str(button.label) in app.screen._compositor.render_strips()[button.region.y].text
        await pilot.click("#bottom-pause")
        await pilot.pause()
        app.update_player()
        assert app.player.properties["pause"]
        assert str(app.query_one("#bottom-pause", Button).label) == "继续"
        await pilot.click("#bottom-next")
        await pilot.pause()
        assert app.current == SONGS[0]
        await pilot.click("#bottom-previous")
        await pilot.pause()
        assert app.current == DUET
