"""Encode account playback checkpoints and validate them before restoring playback."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum

from music_tui.models import Playlist, Track


@dataclass(frozen=True)
class PlaylistSearch:
    """Keep a playlist filter's complete results and original playlist count."""

    playlist: Playlist
    keyword: str
    tracks: tuple[Track, ...]
    playlist_total: int


type PlaybackSource = str | Playlist | PlaylistSearch | None


@dataclass(frozen=True)
class QueuedTrack:
    """A manually queued song keeps its browsing origin for later navigation."""

    track: Track
    source: PlaybackSource
    page: int


class RepeatMode(Enum):
    """Select sequence, repeating or random navigation through a playback queue."""

    SEQUENCE = "顺序播放"
    LIST = "列表循环"
    SINGLE = "单曲循环"
    RANDOM = "随机播放"


def encode_source(source: PlaybackSource) -> dict | str | None:
    """Serialize an origin without credentials or temporary audio URLs."""
    if isinstance(source, PlaylistSearch):
        return {
            "playlist": asdict(source.playlist),
            "keyword": source.keyword,
            "tracks": [asdict(track) for track in source.tracks],
            "playlist_total": source.playlist_total,
        }
    return asdict(source) if isinstance(source, Playlist) else source


def _mapping(value: object) -> dict:
    """Require a JSON object before decoding its fields."""
    if not isinstance(value, dict):
        raise ValueError("保存的播放会话字段必须是对象")
    return value


def _list(value: object) -> list:
    """Require a JSON array before decoding its entries."""
    if not isinstance(value, list):
        raise ValueError("保存的播放会话字段必须是列表")
    return value


def _flag(value: object) -> bool:
    """Reject malformed flags rather than changing their meaning through truthiness."""
    if not isinstance(value, bool):
        raise ValueError("保存的播放会话布尔字段无效")
    return value


def decode_track(value: object) -> Track:
    """Restore immutable metadata and reject values unusable by the player or UI."""
    data = _mapping(value)
    variants = data.get("variants", ())
    if not isinstance(variants, list | tuple) or any(not isinstance(v, str) for v in variants):
        raise ValueError("保存的歌曲媒体信息无效")
    track = Track(**{**data, "variants": tuple(variants)})
    if (
        not isinstance(track.mid, str)
        or not track.mid
        or any(
            not isinstance(value, str)
            for value in (track.title, track.artist, track.media_mid, track.cover_url)
        )
        or type(track.seconds) is not int
        or track.seconds < 0
        or type(track.song_type) is not int
    ):
        raise ValueError("保存的歌曲信息无效")
    return track


def decode_source(value: object) -> PlaybackSource:
    """Restore a playlist, search query or complete playlist-filter results."""
    if value is None or isinstance(value, str):
        return value
    data = _mapping(value)
    if "playlist" in data:
        keyword = data["keyword"]
        total = int(data["playlist_total"])
        playlist = decode_source(data["playlist"])
        if not isinstance(playlist, Playlist) or not isinstance(keyword, str) or total < 0:
            raise ValueError("保存的歌单筛选信息无效")
        return PlaylistSearch(
            playlist, keyword, tuple(decode_track(t) for t in _list(data["tracks"])), total
        )
    playlist = Playlist(**data)
    if (
        type(playlist.id) not in (int, str)
        or not isinstance(playlist.title, str)
        or not isinstance(playlist.cover_url, str)
        or type(playlist.dirid) is not int
        or any(type(flag) is not bool for flag in (playlist.favorite, playlist.editable))
        or any(
            count is not None and (type(count) is not int or count < 0)
            for count in (playlist.song_count, playlist.play_count)
        )
    ):
        raise ValueError("保存的歌单信息无效")
    return playlist


def _encode_queued(entry: QueuedTrack | None) -> dict | None:
    """Serialize a song together with its browsing location when present."""
    if entry is None:
        return None
    return {"track": asdict(entry.track), "source": encode_source(entry.source), "page": entry.page}


def _decode_queued(value: object) -> QueuedTrack:
    """Validate a saved song and its browsing page before restoring it."""
    data = _mapping(value)
    page = int(data["page"])
    if page < 1:
        raise ValueError("保存的歌曲来源页码无效")
    return QueuedTrack(decode_track(data["track"]), decode_source(data["source"]), page)


@dataclass(frozen=True)
class PlaybackSnapshot:
    """A complete queue checkpoint whose decoding precedes any player mutation."""

    queue: list[Track]
    source: PlaybackSource
    page: int
    has_more: bool
    index: int
    track: Track
    position: float
    paused: bool
    pending: list[QueuedTrack]
    manual: QueuedTrack | None
    origin: QueuedTrack | None
    queue_origin: QueuedTrack | None
    shuffle_pages: dict[int, tuple[list[Track], bool]]
    shuffle_remaining: list[tuple[int, int]]
    shuffle_history: list[tuple[int, int]]
    shuffle_cursor: int

    def encode(self) -> dict:
        """Keep the established JSON layout without persisting resolved audio URLs."""
        return {
            "queue": [asdict(track) for track in self.queue],
            "source": encode_source(self.source),
            "page": self.page,
            "has_more": self.has_more,
            "index": self.index,
            "track": asdict(self.track),
            "position": self.position,
            "paused": self.paused,
            "pending": [_encode_queued(entry) for entry in self.pending],
            "manual": _encode_queued(self.manual),
            "origin": _encode_queued(self.origin),
            "queue_origin": _encode_queued(self.queue_origin),
            "shuffle_pages": {
                str(page): {"tracks": [asdict(track) for track in tracks], "more": more}
                for page, (tracks, more) in self.shuffle_pages.items()
            },
            "shuffle_remaining": self.shuffle_remaining,
            "shuffle_history": self.shuffle_history,
            "shuffle_cursor": self.shuffle_cursor,
        }

    @classmethod
    def decode(cls, value: object) -> PlaybackSnapshot:
        """Decode and validate a checkpoint, including older optional origin fields."""
        try:
            data = _mapping(value)
            tracks = [decode_track(track) for track in _list(data["queue"])]
            source = decode_source(data["source"])
            page, index = int(data["page"]), int(data["index"])
            track = decode_track(data["track"])
            position = float(data["position"])
            if not math.isfinite(position):
                raise ValueError("保存的播放进度无效")
            pending = [_decode_queued(entry) for entry in _list(data["pending"])]
            manual = _decode_queued(data["manual"]) if data["manual"] is not None else None
            origin = (
                _decode_queued(data["origin"])
                if data.get("origin") is not None
                else (manual or QueuedTrack(track, source, page))
            )
            queue_origin = (
                _decode_queued(data["queue_origin"])
                if data.get("queue_origin") is not None
                else (origin if source is None and manual is None else None)
            )
            pages = {}
            for key, raw_entry in _mapping(data["shuffle_pages"]).items():
                saved_page, entry = int(key), _mapping(raw_entry)
                if saved_page < 1:
                    raise ValueError("保存的随机播放页码无效")
                pages[saved_page] = (
                    [decode_track(t) for t in _list(entry["tracks"])],
                    _flag(entry["more"]),
                )
            remaining, history = [], []
            for field, positions in (
                ("shuffle_remaining", remaining),
                ("shuffle_history", history),
            ):
                for raw_position in _list(data[field]):
                    if not isinstance(raw_position, list | tuple) or len(raw_position) != 2:
                        raise ValueError("保存的随机播放位置无效")
                    saved_page, saved_index = (int(item) for item in raw_position)
                    # Sequential navigation leaves its initial shuffle seed untouched.
                    # Its page may no longer be loaded until random mode prepares all pages.
                    if pages and not 0 <= saved_index < len(pages[saved_page][0]):
                        raise ValueError("保存的随机播放位置无效")
                    positions.append((saved_page, saved_index))
            cursor = int(data["shuffle_cursor"])
            if page < 1 or (tracks and not 0 <= index < len(tracks)):
                raise ValueError("保存的播放队列位置无效")
            if manual is None and (not tracks or tracks[index] != track):
                raise ValueError("保存的当前歌曲与播放队列不一致")
            if manual is not None and manual.track != track:
                raise ValueError("保存的手动待播歌曲无效")
            if (history and not 0 <= cursor < len(history)) or (not history and cursor != -1):
                raise ValueError("保存的随机播放历史无效")
            return cls(
                tracks,
                source,
                page,
                _flag(data["has_more"]),
                index,
                track,
                max(0, position),
                _flag(data["paused"]),
                pending,
                manual,
                origin,
                queue_origin,
                pages,
                remaining,
                history,
                cursor,
            )
        except (KeyError, TypeError, ValueError, IndexError, OverflowError) as error:
            raise ValueError(f"保存的播放会话无效：{error}") from error
