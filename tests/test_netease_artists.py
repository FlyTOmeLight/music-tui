"""Verify Enhanced artist metadata and global pagination at the HTTP boundary."""

import json

import httpx
import pytest

from music_tui.models import Artist, ArtistOrder
from music_tui.sources.netease import NetEaseMusic


async def test_artist_detail_uses_avatar_and_exact_identity(tmp_path):
    """Convert optional profile fields while preserving the requested platform ID."""

    def handler(request):
        """Return a representative profile with differing portrait and cover images."""
        assert request.url.path == "/artist/detail"
        assert json.loads(request.content)["id"] == "6452"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "artist": {
                        "id": 6452,
                        "name": "歌手",
                        "avatar": "https://image.test/avatar",
                        "cover": "https://image.test/cover",
                        "briefDesc": "简介",
                        "musicSize": 568,
                    }
                },
            },
        )

    music = NetEaseMusic(tmp_path)
    await music.client.aclose()
    music.client = httpx.AsyncClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler)
    )
    try:
        detail = await music.artist_detail(Artist("6452", "旧名称"))
        assert detail.artist == Artist("6452", "歌手")
        assert detail.cover_url == "https://image.test/avatar"
        assert detail.description == "简介"
    finally:
        await music.close()


@pytest.mark.parametrize("order", list(ArtistOrder))
@pytest.mark.parametrize("page,total,more,count", [(2, 566, True, 30), (19, 566, False, 26)])
async def test_artist_songs_preserves_server_order_and_page_metadata(
    tmp_path, order, page, total, more, count
):
    """Keep globally sorted song identities, contributors, total and end-of-list state."""
    identities = list(range(count, 0, -1))

    def handler(request):
        """Check server ordering and offset before providing deliberately reversed IDs."""
        assert request.url.path == "/artist/songs"
        params = json.loads(request.content)
        assert params["id"] == "6452"
        assert params["order"] == order.value
        assert params["limit"] == 30
        assert params["offset"] == (page - 1) * 30
        return httpx.Response(
            200,
            json={
                "code": 200,
                "total": total,
                "more": more,
                "songs": [
                    {
                        "id": identity,
                        "name": str(identity),
                        "dt": 60000,
                        "ar": [{"id": 6452, "name": "歌手"}, {"id": 9612, "name": "合唱"}],
                    }
                    for identity in identities
                ],
            },
        )

    music = NetEaseMusic(tmp_path)
    await music.client.aclose()
    music.client = httpx.AsyncClient(
        base_url="http://api.test", transport=httpx.MockTransport(handler)
    )
    try:
        result = await music.artist_songs(Artist("6452", "歌手"), order, page)
        assert [track.mid for track in result.tracks] == [str(identity) for identity in identities]
        assert result.total == total and result.has_more is more and result.page_size == 30
        assert result.tracks[0].artists == (Artist("6452", "歌手"), Artist("9612", "合唱"))
    finally:
        await music.close()
