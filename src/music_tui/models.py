"""Shared song, playlist, quality, and QR login values used by music sources."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Quality(Enum):
    """Audio tiers shown by the interface; each source maps its supported tiers."""

    MASTER = "臻品母带"
    ATMOS = "臻品全景声"
    ENHANCED = "臻品音质"
    HIRES = "Hi-Res 高解析"
    LOSSLESS = "SQ 无损"
    HIGH = "HQ 高品质"
    STANDARD = "标准音质"

    @property
    def label(self) -> str:
        """Return the human-readable quality label."""
        return self.value


@dataclass(frozen=True)
class Track:
    """Song metadata with an opaque source ID and optional QQ media variants."""

    mid: str
    title: str
    artist: str
    seconds: int
    media_mid: str = ""
    song_type: int = 0
    variants: tuple[str, ...] = ()


@dataclass(frozen=True)
class Playlist:
    """Playlist identity, title, and source-reported track count."""

    id: int | str
    title: str
    dirid: int = 0
    favorite: bool = False
    song_count: int | None = None


@dataclass(frozen=True)
class SongPage:
    """A page of songs together with server totals and pagination state."""

    tracks: list[Track]
    has_more: bool
    total: int
    page_size: int = 100


class LoginState(Enum):
    """Platform-independent transitions from QR scanning to account confirmation."""

    CONFIRMED = "confirmed"
    DONE = "done"
    EXPIRED = "expired"
    REFUSED = "refused"


class LoginSession(Protocol):
    """A QR attempt owns platform credentials and exposes only display state."""

    async def get_qrcode(self) -> bytes:
        """Return a PNG image for the current login attempt."""
        ...

    def __aiter__(self) -> AsyncIterator[LoginState]:
        """Yield login states, persisting credentials before DONE."""
        ...
