"""Verify playlist creation, addition, and reported play-count sorting through the UI."""

import asyncio

import pytest
from test_app import FIRST, SECOND, THIRD, CatalogLibrary
from test_playback_features import FeatureAudio, FeatureLibrary
from textual.widgets import Button, Input, ListView, Static

from music_tui.app import LoginScreen, MusicApp
from music_tui.models import Playlist, SongPage, Track
from music_tui.playback_screen import PlaybackScreen
from music_tui.playback_session import PlaylistSearch
from music_tui.screens import (
    CreatePlaylistScreen,
    PlaylistPickerScreen,
    RemoveFromPlaylistScreen,
)


class PlaylistLibrary(FeatureLibrary):
    """Model real playlist IDs and separately observable creation and song writes."""

    supports_playlist_creation = True
    supports_playlist_removal = True

    def __init__(self):
        """Prepare known, zero, and unavailable play counts with writable destinations."""
        super().__init__()
        self.entries = [
            Playlist(1, "热门歌单", editable=True, play_count=25_000),
            Playlist(2, "新歌单", editable=True, play_count=0),
            Playlist(3, "未知统计", editable=True),
            Playlist(4, "常听歌单", editable=True, play_count=100),
        ]
        self.created = []
        self.removed = []
        self.fail_create = False
        self.fail_remove = False

    async def playlists(self):
        """Return the current library including newly created server-side IDs."""
        return list(self.entries)

    async def create_playlist(self, title):
        """Return a new writable playlist or fail before changing the library."""
        if self.fail_create:
            raise ValueError("账号权限不足")
        playlist = Playlist(20 + len(self.created), title, editable=True, play_count=0)
        self.created.append(playlist)
        self.entries.append(playlist)
        return playlist

    async def remove_from_playlist(self, track, playlist):
        """Record a successful removal or fail without changing account state."""
        if self.fail_remove:
            raise ValueError("账号权限不足")
        self.removed.append((track, playlist))


@pytest.mark.parametrize("size", [(80, 24), (48, 16)])
async def test_creation_form_validates_and_creates_without_changing_audio(size):
    """Create a trimmed playlist name using the visible entry in compact terminal sizes."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.click("#create-playlist")
        await pilot.pause()
        assert isinstance(app.screen, CreatePlaylistScreen)
        assert app.screen.query_one("#create-confirm", Button).disabled
        field = app.screen.query_one("#create-name", Input)
        field.value = "  "
        await pilot.press("enter")
        assert "请输入" in str(app.screen.query_one("#create-error", Static).render())
        for button in app.screen.query(Button):
            assert button.region.bottom <= size[1] and button.region.right <= size[0]
        field.value = "  下班听  "
        await pilot.press("enter")
        await pilot.pause()
        assert [playlist.title for playlist in library.created] == ["下班听"]
        assert library.created[0] in app.playlists
        assert not library.writes and not app.player.loaded
        assert "已新建" in app.last_status_message


@pytest.mark.parametrize("cancel", [True, False])
async def test_cancel_or_creation_failure_does_not_add_a_song(cancel):
    """Cancel cleanly or report server refusal without inventing a playlist or write."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+n")
        assert isinstance(app.screen, CreatePlaylistScreen)
        if cancel:
            await pilot.press("escape")
        else:
            library.fail_create = True
            app.screen.query_one("#create-name", Input).value = "新歌单"
            await pilot.press("enter")
            await pilot.pause()
            assert "新建歌单失败" in app.last_status_message
        assert not library.created and not library.writes


@pytest.mark.parametrize("fail_add", [False, True])
async def test_create_destination_and_add_preserves_song_and_partial_success(fail_add):
    """Keep the selected song and distinguish successful creation from a failed song addition."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("down", "w")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        await pilot.click("#picker-create")
        await pilot.pause()
        assert isinstance(app.screen, CreatePlaylistScreen)
        app.screen.query_one("#create-name", Input).value = "新收藏"
        library.fail_write = fail_add
        await pilot.press("enter")
        await pilot.pause()
        assert len(library.created) == 1 and library.created[0] in app.playlists
        if fail_add:
            assert not library.writes and "歌曲未加入" in app.last_status_message
        else:
            assert library.writes == [("add", SECOND, library.created[0])]
        assert "已新建歌单" in app.last_status_message


async def test_playback_add_button_uses_active_song_instead_of_hidden_selection():
    """Save the playing song while the browser is showing an unrelated playlist."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view("original").wait()
        await pilot.press("enter")
        await pilot.pause()
        await app.load_view(Playlist(9, "另外一页")).wait()
        await pilot.press("f7")
        assert isinstance(app.screen, PlaybackScreen)
        await pilot.click("#playback-add_to_playlist")
        await pilot.pause()
        assert isinstance(app.screen, PlaylistPickerScreen)
        await pilot.press("enter")
        await pilot.pause()
        assert library.writes == [("add", FIRST, library.entries[0])]
        assert app.current == FIRST and app.player.loaded == ["a"]


@pytest.mark.parametrize("confirm", [True, False])
async def test_delete_removes_selected_song_only_after_confirmation(confirm):
    """Confirm or cancel removal without starting playback or changing the other songs."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view(library.editable).wait()
        assert app.tracks == [THIRD]
        await pilot.press("delete")
        await pilot.pause()
        assert isinstance(app.screen, RemoveFromPlaylistScreen)
        assert THIRD.title in str(app.screen.query_one(".remove-text", Static).render())
        if confirm:
            await pilot.press("y")
        else:
            await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert library.removed == ([(THIRD, library.editable)] if confirm else [])
        assert app.tracks == ([] if confirm else [THIRD])
        assert app.playlist_counts[library.editable] == (0 if confirm else 1)
        if confirm:
            assert "已从" in app.last_status_message
        assert not app.player.loaded


async def test_delete_failure_keeps_song_and_reports_server_error():
    """Do not remove the local row when the platform rejects the write."""
    library = PlaylistLibrary()
    library.fail_remove = True
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view(library.editable).wait()
        await pilot.press("delete", "y")
        await app.workers.wait_for_complete()
        assert app.tracks == [THIRD]
        assert app.playlist_counts[library.editable] == 1
        assert "账号权限不足" in app.last_status_message


async def test_delete_on_readonly_playlist_does_not_open_confirmation():
    """Reject removal from a followed playlist before any local state changes."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view(Playlist(2, "收藏歌单")).wait()
        await pilot.press("delete")
        await pilot.pause()
        assert not isinstance(app.screen, RemoveFromPlaylistScreen)
        assert "不可编辑" in app.last_status_message
        assert app.tracks == [THIRD]


async def test_delete_from_filtered_playlist_updates_match_and_total_counts():
    """Keep a filtered source's matched rows and original playlist count consistent."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view(library.editable).wait()
        await app.filter_playlist(library.editable, "下一").wait()
        assert isinstance(app.source, PlaylistSearch)
        await pilot.press("delete", "y")
        await app.workers.wait_for_complete()
        assert app.source == PlaylistSearch(library.editable, "下一", (), 0)
        assert app.tracks == [] and app.total == 0


async def test_delete_last_filtered_page_returns_to_previous_filtered_page():
    """Keep the playlist filter active when removing the only song on its last page."""

    class PagedPlaylistLibrary(CatalogLibrary):
        """Serve 101 matching songs across two pages for one editable playlist."""

        account_id = "account-1"
        supports_playlist_removal = True

        def __init__(self):
            """Replace the inherited catalog with one full page plus one matching song."""
            super().__init__()
            self.playlist = Playlist(1, "分页歌单", editable=True, song_count=101)
            self.catalog = [Track(str(index), f"歌 {index}", "歌手", 180) for index in range(101)]
            self.removed = []

        async def restore(self):
            """Restore an account without requiring QR login in the UI test."""
            return True

        async def playlists(self):
            """Return the single editable playlist used by this test."""
            return [self.playlist]

        async def songs(self, playlist, page):
            """Return the requested 100-song page of the matching catalog."""
            start = (page - 1) * 100
            return SongPage(
                self.catalog[start : start + 100],
                start + 100 < len(self.catalog),
                len(self.catalog),
            )

        async def remove_from_playlist(self, track, playlist):
            """Record the accepted removal request."""
            self.removed.append((track, playlist))

    library = PagedPlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await app.load_view(library.playlist).wait()
        await app.filter_playlist(library.playlist, "歌").wait()
        await pilot.press("]")
        await pilot.pause()
        assert app.page == 2 and app.tracks == [library.catalog[-1]]
        await pilot.press("delete", "y")
        await app.workers.wait_for_complete()
        assert app.source == PlaylistSearch(
            library.playlist, "歌", tuple(library.catalog[:100]), 100
        )
        assert app.page == 1 and app.total == 100 and len(app.tracks) == 100
        assert library.removed == [(library.catalog[-1], library.playlist)]


async def test_playlist_sorting_uses_complete_reported_counts_and_preserves_selection():
    """Sort all sidebar playlists, keep unknown counts last, and open the displayed identity."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.click("#playlist-sort")
        await pilot.pause()
        assert [playlist.id for playlist in app.displayed_playlists] == [1, 4, 2, 3]
        view = app.query_one("#playlists", ListView)
        view.index = 1
        view.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.source.id == 4
        view.index = 1
        await app.sort_playlists()
        assert [playlist.id for playlist in app.displayed_playlists] == [2, 4, 1, 3]
        assert app.displayed_playlists[view.index].id == 4
        labels = list(view.query(".playlist-plays"))
        assert "0次" in str(labels[0].render())
        assert "0次" not in str(labels[-1].render())
        await app.sort_playlists()
        await app.sort_playlists()
        assert app.displayed_playlists == library.entries


@pytest.mark.parametrize("logged_in", [True, False])
async def test_creation_entry_respects_account_and_source_capability(logged_in):
    """Disable unavailable creation and avoid opening a form with a false promise."""
    library = PlaylistLibrary()
    library.supports_playlist_creation = not logged_in

    async def restore():
        """Choose an unsupported logged-in source or a supported guest source."""
        return logged_in

    library.restore = restore
    app = MusicApp(library, FeatureAudio())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#create-playlist", Button).disabled
        await pilot.press("ctrl+n")
        assert not isinstance(app.screen, CreatePlaylistScreen)
        assert not library.created


async def test_refresh_and_sort_keep_rendered_rows_and_selection_consistent(monkeypatch):
    """Keep labels and playlist identities consistent after overlapping refresh and sorting."""
    library = PlaylistLibrary()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test() as pilot:
        await pilot.pause()
        view = app.query_one("#playlists", ListView)
        original_clear = view.clear
        started, release = asyncio.Event(), asyncio.Event()
        first = True

        async def delayed_clear():
            """Pause the first real sidebar clear while another sort request is issued."""
            nonlocal first
            if first:
                first = False
                started.set()
                await release.wait()
            return await original_clear()

        monkeypatch.setattr(view, "clear", delayed_clear)
        refresh = app.load_playlists()
        async with asyncio.timeout(2):
            await started.wait()
        original_draw = app.draw_playlists
        sort_requested = asyncio.Event()

        async def observe_sort():
            """Observe a real button-triggered redraw before it waits for the refresh lock."""
            sort_requested.set()
            await original_draw()

        monkeypatch.setattr(app, "draw_playlists", observe_sort)
        sorting = asyncio.create_task(pilot.click("#playlist-sort"))
        async with asyncio.timeout(2):
            await sort_requested.wait()
            assert not sorting.done()
            release.set()
            await refresh.wait()
            await sorting
        assert [playlist.id for playlist in app.displayed_playlists] == [1, 4, 2, 3]
        assert [item.playlist for item in view.children] == app.displayed_playlists
        view.index = 1
        view.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert app.source.id == 4


async def test_login_waits_for_playlist_creation_and_song_addition():
    """Do not replace source credentials between creating a playlist and adding its song."""
    started, release = asyncio.Event(), asyncio.Event()

    class SlowCreation(PlaylistLibrary):
        async def create_playlist(self, title):
            """Keep a real creation flow pending while the user requests login."""
            started.set()
            await release.wait()
            return await super().create_playlist(title)

    library = SlowCreation()
    app = MusicApp(library, FeatureAudio())
    async with app.run_test() as pilot:
        await pilot.pause()
        operation = app.create_playlist("通勤", FIRST)
        async with asyncio.timeout(2):
            await started.wait()
        await pilot.press("l")
        assert not isinstance(app.screen, LoginScreen)
        assert "请稍后登录" in app.last_status_message
        release.set()
        await operation.wait()
        await pilot.pause()
        assert library.account_id == "account-1"
        assert library.writes == [("add", FIRST, library.created[0])]
