"""Music platform contracts used by the terminal interface."""

from typing import Protocol

from ..models import LoginSession, Playlist, Quality, SongPage, Track


class MusicSource(Protocol):
    """The library, account, and playback operations consumed by Music TUI."""

    display_name: str

    async def restore(self) -> bool:
        """Validate locally saved account state and report whether login is restored."""
        ...

    def login_session(self) -> LoginSession:
        """Create a cancellable QR login attempt."""
        ...

    async def playlists(self) -> list[Playlist]:
        """Return the account's playlists with their server-reported counts."""
        ...

    async def search(self, keyword: str, page: int = 1) -> SongPage:
        """Fetch a page of matching songs."""
        ...

    async def songs(self, playlist: Playlist, page: int = 1) -> SongPage:
        """Fetch a playlist page in its original order."""
        ...

    async def available_qualities(self, track: Track) -> list[Quality]:
        """List full-track playable tiers in descending quality order."""
        ...

    async def stream_url(self, track: Track, quality: Quality) -> str:
        """Resolve a full-track URL at the requested quality without downgrading."""
        ...

    async def close(self) -> None:
        """Release the platform client's connection resources."""
        ...
