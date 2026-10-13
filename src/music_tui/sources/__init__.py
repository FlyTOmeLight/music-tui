"""Music platform contracts used by the terminal interface."""

from typing import Protocol

from ..models import (
    Artist,
    ArtistDetail,
    ArtistOrder,
    LoginSession,
    Lyrics,
    Playlist,
    Quality,
    SongPage,
    Track,
)


class MusicSource(Protocol):
    """The library, account, and playback operations consumed by Music TUI."""

    display_name: str
    supports_playlist_creation: bool

    @property
    def account_id(self) -> str:
        """Return the verified account ID, or an empty string while logged out."""
        ...

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

    async def lyrics(self, track: Track) -> Lyrics:
        """Return timed or plain lyrics, or an empty value when lyrics are absent."""
        ...

    async def artist_detail(self, artist: Artist) -> ArtistDetail:
        """Read an artist's profile without selecting playback."""
        ...

    async def artist_songs(self, artist: Artist, order: ArtistOrder, page: int = 1) -> SongPage:
        """Fetch songs ordered across the whole artist collection before paging."""
        ...

    async def liked_ids(self) -> set[str]:
        """Return the account's liked song identities in Track.mid form."""
        ...

    async def is_liked(self, track: Track) -> bool:
        """Read whether the verified account likes this song."""
        ...

    async def set_liked(self, track: Track, liked: bool) -> None:
        """Set the account's like state, raising on unsupported or failed writes."""
        ...

    async def add_to_playlist(self, track: Track, playlist: Playlist) -> None:
        """Add one song to an editable playlist without replacing existing songs."""
        ...

    async def create_playlist(self, title: str) -> Playlist:
        """Create an account-owned playlist and return its server identity."""
        ...

    async def available_qualities(self, track: Track) -> list[Quality]:
        """List full-track playable tiers in descending quality order."""
        ...

    async def stream_url(self, track: Track, quality: Quality) -> str:
        """Resolve an exact full-track tier; raise QualityUnavailableError if unavailable."""
        ...

    async def close(self) -> None:
        """Release the platform client's connection resources."""
        ...
