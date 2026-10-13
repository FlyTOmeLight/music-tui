"""Verify checkpoint format compatibility and validation before playback recovery."""

import json
from dataclasses import asdict, replace

import pytest

from music_tui.models import Playlist, Track
from music_tui.playback_session import (
    PlaybackSnapshot,
    PlaylistSearch,
    QueuedTrack,
    decode_source,
    decode_track,
    encode_source,
)

FIRST = Track("first", "第一首", "歌手", 120, variants=("original", "live"))
SECOND = Track("second", "第二首", "歌手", 180)


@pytest.fixture
def snapshot():
    """Provide a filtered random queue with a pending song and playback checkpoint."""
    source = PlaylistSearch(Playlist(5, "歌单"), "歌", (FIRST, SECOND), 8)
    origin = QueuedTrack(FIRST, source, 1)
    return PlaybackSnapshot(
        queue=[FIRST, SECOND],
        source=source,
        page=1,
        has_more=False,
        index=0,
        track=FIRST,
        position=18.25,
        paused=True,
        pending=[QueuedTrack(SECOND, "搜索", 2)],
        manual=None,
        origin=origin,
        queue_origin=None,
        shuffle_pages={1: ([FIRST, SECOND], False)},
        shuffle_remaining=[(1, 1)],
        shuffle_history=[(1, 0)],
        shuffle_cursor=0,
    )


def test_snapshot_round_trip_preserves_metadata_and_queue(snapshot):
    """Round-trip through JSON retains origins, media variants and random order."""
    encoded = snapshot.encode()
    assert encoded["queue"][0] == asdict(FIRST)
    assert encoded["source"] == encode_source(snapshot.source)
    assert encoded["shuffle_pages"] == {
        "1": {"tracks": [asdict(FIRST), asdict(SECOND)], "more": False}
    }
    assert PlaybackSnapshot.decode(json.loads(json.dumps(encoded))) == snapshot
    assert "url" not in encoded and "credential" not in encoded


@pytest.mark.parametrize("manual", [False, True])
def test_legacy_origins_are_reconstructed(snapshot, manual):
    """Older checkpoints without origins keep manual and standalone navigation."""
    encoded = snapshot.encode()
    encoded.pop("origin")
    encoded.pop("queue_origin")
    encoded["source"] = None
    if manual:
        encoded["manual"] = {"track": asdict(FIRST), "source": "查询", "page": 2}
    restored = PlaybackSnapshot.decode(encoded)
    expected = QueuedTrack(FIRST, "查询" if manual else None, 2 if manual else 1)
    assert restored.origin == expected
    assert restored.queue_origin == (None if manual else expected)


def test_track_defaults_allow_older_metadata():
    """Restore metadata saved before optional media and cover fields were introduced."""
    assert decode_track(
        {"mid": "first", "title": "歌曲", "artist": "歌手", "seconds": 10}
    ) == Track("first", "歌曲", "歌手", 10)
    assert decode_source({"id": 5, "title": "歌单"}) == Playlist(5, "歌单")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("queue", None),
        ("source", 123),
        ("page", 0),
        ("index", 2),
        ("track", asdict(SECOND)),
        ("position", float("nan")),
        ("position", float("inf")),
        ("has_more", "false"),
        ("paused", []),
        ("pending", [{"track": asdict(SECOND), "source": "搜索", "page": 0}]),
        ("manual", {"track": asdict(SECOND), "source": None, "page": 1}),
        ("origin", {"track": {"mid": "first"}, "source": None, "page": 1}),
        ("shuffle_remaining", [[1, 2]]),
        ("shuffle_history", [[2, 0]]),
        ("shuffle_history", [[1, 0, 2]]),
        ("shuffle_cursor", 1),
        ("shuffle_pages", {"0": {"tracks": [], "more": False}}),
    ],
)
def test_invalid_checkpoint_is_rejected(snapshot, field, value):
    """Reject broken metadata and navigation before returning any restored state."""
    encoded = snapshot.encode()
    encoded[field] = value
    with pytest.raises(ValueError, match="播放会话无效"):
        PlaybackSnapshot.decode(encoded)


def test_sequential_checkpoint_keeps_unloaded_shuffle_history(snapshot):
    """Sequential playback can record its current position before caching random pages."""
    encoded = snapshot.encode()
    encoded["shuffle_pages"] = {}
    encoded["shuffle_remaining"] = []
    restored = PlaybackSnapshot.decode(encoded)
    assert restored.shuffle_history == [(1, 0)]


def test_negative_checkpoint_position_resumes_from_start(snapshot):
    """Preserve the established behavior of clamping a negative checkpoint to zero."""
    encoded = snapshot.encode()
    encoded["position"] = -1
    assert PlaybackSnapshot.decode(encoded).position == 0


def test_sequential_page_change_keeps_initial_shuffle_seed(snapshot):
    """A shorter later page still restores the original, currently uncached random seed."""
    sequential = replace(
        snapshot,
        queue=[SECOND],
        page=2,
        index=0,
        track=SECOND,
        origin=QueuedTrack(SECOND, snapshot.source, 2),
        shuffle_pages={},
        shuffle_remaining=[],
        shuffle_history=[(1, 1)],
    )
    assert PlaybackSnapshot.decode(json.loads(json.dumps(sequential.encode()))) == sequential
