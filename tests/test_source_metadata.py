"""Check source cover conversion without inventing per-song play statistics."""

import pytest

from music_tui.models import Playlist, Track
from music_tui.sources.netease import _track as enhanced_track
from music_tui.sources.netease_official import _track as official_track


@pytest.mark.parametrize("cover", [None, "", "https://cover.test/song.jpg"])
@pytest.mark.parametrize("backend", ["enhanced", "official"])
def test_track_cover_uses_documented_source_field(backend, cover):
    """Use album art in Enhanced and the direct song cover in the official API."""
    metadata = {"id": "123", "name": "Song", "playCount": 999}
    if backend == "enhanced":
        metadata["al"] = {"picUrl": cover, "playCount": 777}
        track = enhanced_track(metadata)
    else:
        metadata["coverImgUrl"] = cover
        track = official_track(metadata)
    assert track.cover_url == (cover or "")
    assert not hasattr(track, "play_count")


def test_new_metadata_defaults_preserve_old_model_construction():
    """Keep positional construction valid and distinguish unknown play counts from zero."""
    track = Track("123", "Song", "Artist", 180, "media", 0, ("variant",))
    playlist = Playlist(10, "Playlist", 301, False, 5, True)
    assert track.cover_url == ""
    assert playlist.cover_url == "" and playlist.play_count is None
