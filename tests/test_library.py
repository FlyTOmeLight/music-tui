"""Verify playlist and song metadata conversion with controlled API responses."""

from types import SimpleNamespace

import pytest

from music_tui.models import Playlist
from music_tui.sources.qqmusic import QQMusic


def song(mid):
    """Build the API song metadata used by library tests."""
    return SimpleNamespace(
        mid=mid,
        title="歌曲",
        name="歌曲",
        singer=[SimpleNamespace(mid="singer-mid", name="歌手")],
        interval=180,
        file=SimpleNamespace(media_mid=mid),
        type=0,
        vs=[],
        cover_url=lambda: "https://cover.test/song.jpg",
    )


@pytest.mark.parametrize("favorite", [False, True])
async def test_song_page_preserves_server_total_not_loaded_count(monkeypatch, tmp_path, favorite):
    """Verify regular and favorite pages retain the server total and requested pagination."""
    calls = []

    async def detail(*args, **kwargs):
        """Record pagination arguments and return a partial page with the server total."""
        calls.append(kwargs)
        return SimpleNamespace(songs=[song("a"), song("b")], hasmore=1, total=236)

    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(
            credential=SimpleNamespace(encrypt_uin="test"),
            songlist=SimpleNamespace(get_detail=detail),
            user=SimpleNamespace(get_fav_song=detail),
        ),
    )
    result = await QQMusic(tmp_path).songs(Playlist(1, "歌单", favorite=favorite), page=2)
    assert result.total == 236 and result.has_more
    assert [track.mid for track in result.tracks] == ["a", "b"]
    assert all(track.cover_url == "https://cover.test/song.jpg" for track in result.tracks)
    assert calls[0]["page"] == 2 and calls[0]["num"] == 100


@pytest.mark.parametrize("favorite_count", [None, 0, 500])
async def test_created_and_favorite_playlist_counts_are_preserved(
    monkeypatch, tmp_path, favorite_count
):
    """Verify created and collected playlists retain their server-reported counts."""

    async def created(musicid):
        """Return created playlists with distinct server-reported song counts."""
        return SimpleNamespace(
            playlists=[
                SimpleNamespace(
                    id=0, title="我喜欢", dirid=201, songnum=236, play_cnt=45, picurl=""
                ),
                SimpleNamespace(
                    id=1,
                    title="自建",
                    dirid=301,
                    songnum=145,
                    play_cnt=123,
                    picurl="https://cover.test/playlist.jpg",
                ),
            ]
        )

    class FavoriteRequest:
        async def collect_items(self):
            """Return one collected playlist without accessing the network."""
            return [
                SimpleNamespace(
                    id=2,
                    title="收藏",
                    songnum=92,
                    listennum=favorite_count or 0,
                    model_fields_set={"listennum"} if favorite_count is not None else set(),
                    picurl="",
                )
            ]

    monkeypatch.setattr(
        "music_tui.sources.qqmusic.Client",
        lambda **kwargs: SimpleNamespace(
            credential=SimpleNamespace(musicid=123, musickey="test", encrypt_uin="test"),
            user=SimpleNamespace(
                get_created_songlist=created,
                get_fav_songlist=lambda *args, **kwargs: FavoriteRequest(),
            ),
        ),
    )
    result = await QQMusic(tmp_path).playlists()
    assert [playlist.song_count for playlist in result] == [236, 145, 92]
    assert [playlist.play_count for playlist in result] == [45, 123, favorite_count]
    assert result[1].cover_url == "https://cover.test/playlist.jpg"
