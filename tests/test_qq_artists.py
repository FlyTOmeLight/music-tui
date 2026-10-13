"""Verify QQ artist identity, optional biography, and server-ordered pagination."""

from types import SimpleNamespace

import pytest
from qqmusic_api import Client
from qqmusic_api.models.singer import SingerDetailResponse

from music_tui.models import Artist, ArtistOrder
from music_tui.sources.qqmusic import QQMusic, track_from_song


def artist_song(mid, singers=None):
    """Build a song with stable singer identities and ordinary QQ media metadata."""
    return SimpleNamespace(
        mid=mid,
        title=mid,
        name=mid,
        singer=singers or [SimpleNamespace(mid="artist", name="歌手")],
        interval=180,
        file=SimpleNamespace(media_mid=mid),
        type=0,
        vs=[],
        cover_url=lambda: "",
    )


def test_qq_track_preserves_individual_artist_identity():
    """Keep chorus artists separate and omit identities absent in upstream metadata."""
    track = track_from_song(
        artist_song(
            "song",
            [
                SimpleNamespace(mid="a", name="甲"),
                SimpleNamespace(mid="b", name="乙"),
                SimpleNamespace(mid="", name="未知"),
            ],
        )
    )
    assert track.artist == "甲 / 乙 / 未知"
    assert track.artists == (Artist("a", "甲"), Artist("b", "乙"))


@pytest.mark.parametrize("order,qq_order", [(ArtistOrder.HOT, 1), (ArtistOrder.LATEST, 2)])
async def test_qq_artist_pages_preserve_server_order(monkeypatch, tmp_path, order, qq_order):
    """Request the verified upstream num/offset fields and preserve page order and totals."""
    calls = []

    async def execute(client, request):
        """Return server-ordered pages at the SDK execution boundary without networking."""
        calls.append(request)
        mids = ["z", "a"] if request.param["begin"] == 0 else ["b"]
        return SimpleNamespace(song_list=[artist_song(mid) for mid in mids], total_num=31)

    monkeypatch.setattr(Client, "execute", execute)
    music = QQMusic(tmp_path)
    try:
        first = await music.artist_songs(Artist("artist", "歌手"), order)
        second = await music.artist_songs(Artist("artist", "歌手"), order, page=2)
    finally:
        await music.close()
    assert [track.mid for track in first.tracks + second.tracks] == ["z", "a", "b"]
    assert first.has_more and not second.has_more
    assert first.total == second.total == 31
    assert first.page_size == second.page_size == 30
    assert all(request.module == "musichall.song_list_server" for request in calls)
    assert all(request.method == "GetSingerSongList" for request in calls)
    assert [request.param for request in calls] == [
        {"singerMid": "artist", "order": qq_order, "num": 30, "begin": 0},
        {"singerMid": "artist", "order": qq_order, "num": 30, "begin": 30},
    ]


@pytest.mark.parametrize("metadata", [[], [{"basic_info": {"singer_mid": "a", "name": "歌手"}}]])
async def test_qq_artist_optional_detail_can_be_absent(monkeypatch, tmp_path, metadata):
    """Retain exact identity when optional photo and biography metadata is missing."""

    async def execute(client, request):
        """Return optional singer detail through the real SDK response model."""
        assert request.param["singer_mids"] == ["a"]
        return SingerDetailResponse.model_validate({"singer_list": metadata})

    monkeypatch.setattr(Client, "execute", execute)
    music = QQMusic(tmp_path)
    try:
        detail = await music.artist_detail(Artist("a", "歌手"))
    finally:
        await music.close()
    assert detail.artist == Artist("a", "歌手")
    assert detail.cover_url == detail.description == ""


async def test_qq_artist_detail_keeps_exact_identity(monkeypatch, tmp_path):
    """Use the matching MID and returned artwork rather than another listed artist."""

    async def execute(client, request):
        """Return two biographies so identity matching is observable."""
        return SingerDetailResponse.model_validate(
            {
                "singer_list": [
                    {"basic_info": {"singer_mid": "wrong", "name": "其他"}},
                    {
                        "basic_info": {"singer_mid": "a", "name": "准确姓名"},
                        "ex_info": {"desc": "简介"},
                        "pic": {"pic": "https://cover.test/artist.jpg"},
                    },
                ]
            }
        )

    monkeypatch.setattr(Client, "execute", execute)
    music = QQMusic(tmp_path)
    try:
        detail = await music.artist_detail(Artist("a", "旧名"))
    finally:
        await music.close()
    assert detail.artist == Artist("a", "准确姓名")
    assert detail.description == "简介"
    assert detail.cover_url == "https://cover.test/artist.jpg"
