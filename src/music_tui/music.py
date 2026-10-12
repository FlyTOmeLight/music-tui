"""QQ login, library and stream access."""

import asyncio
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from qqmusic_api import Client, Credential
from qqmusic_api.models.base import Song
from qqmusic_api.models.login import QRLoginType
from qqmusic_api.modules.login_utils import QRCodeLoginSession
from qqmusic_api.modules.song import BaseSongFileType, SongFileInfo, SongFileType


class AdditionalSongFileType(BaseSongFileType):
    HIRES = ("RS01", ".flac")


class Quality(Enum):
    MASTER = ("臻品母带", SongFileType.MASTER, 3)
    ATMOS = ("臻品全景声", SongFileType.ATMOS_71, 24)
    ENHANCED = ("臻品音质", SongFileType.ATMOS_2, 4)
    HIRES = ("Hi-Res 高解析", AdditionalSongFileType.HIRES, None)
    LOSSLESS = ("SQ 无损", SongFileType.FLAC, None)
    HIGH = ("HQ 高品质", SongFileType.MP3_320, None)
    STANDARD = ("标准音质", SongFileType.MP3_128, None)

    def __init__(self, label: str, file_type: BaseSongFileType, variant_index: int | None):
        """Associate a display label with its QQ audio format and source variant."""
        self.label = label
        self.file_type = file_type
        self.variant_index = variant_index


@dataclass(frozen=True)
class Track:
    mid: str
    title: str
    artist: str
    seconds: int
    media_mid: str = ""
    song_type: int = 0
    variants: tuple[str, ...] = ()

    @classmethod
    def from_song(cls, song: Song) -> "Track":
        """Convert a QQ API song into the track fields used by the interface."""
        return cls(
            song.mid,
            song.title or song.name,
            " / ".join(s.name for s in song.singer),
            song.interval,
            song.file.media_mid,
            song.type,
            tuple(song.vs),
        )


@dataclass(frozen=True)
class Playlist:
    id: int
    title: str
    dirid: int = 0
    favorite: bool = False
    song_count: int | None = None


@dataclass(frozen=True)
class SongPage:
    tracks: list[Track]
    has_more: bool
    total: int
    page_size: int = 100


def url_reachable(url: str) -> bool:
    """Reject playback paths that the audio server cannot serve."""
    try:
        with urlopen(Request(url, method="HEAD"), timeout=8) as response:
            return 200 <= response.status < 300
    except HTTPError:
        return False


def save_credential(path: Path, credential: Credential) -> None:
    """Atomically save credentials with owner-only access."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".credential-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(credential.model_dump_json(by_alias=True))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Music:
    def __init__(self, directory: Path):
        """Create a QQ client with account files in the supplied directory."""
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.credential_path = directory / "credential.json"
        self.client = Client(device_path=str(directory / "device.json"))

    @property
    def logged_in(self) -> bool:
        """Report whether the client has both required login credential fields."""
        return bool(self.client.credential.musicid and self.client.credential.musickey)

    async def restore(self) -> bool:
        """Load saved credentials and refresh them if the server reports expiry."""
        if not self.credential_path.exists():
            return False
        credential = Credential.model_validate_json(self.credential_path.read_text())
        async with asyncio.timeout(25):
            if await self.client.login.check_expired(credential):
                credential = await self.client.login.refresh_credential(credential)
                save_credential(self.credential_path, credential)
        self.client.credential = credential
        return self.logged_in

    def login_session(self) -> QRCodeLoginSession:
        """Create a QR login session for confirmation in the mobile QQ Music app."""
        return QRCodeLoginSession(self.client.login, QRLoginType.MOBILE, timeout_seconds=180)

    def accept_login(self, credential: Credential) -> None:
        """Persist confirmed credentials and apply them to the current client."""
        save_credential(self.credential_path, credential)
        self.client.credential = credential

    async def playlists(self) -> list[Playlist]:
        """Combine liked, created, and collected playlists without duplicate IDs."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        async with asyncio.timeout(30):
            created = await self.client.user.get_created_songlist(self.client.credential.musicid)
            liked = next((p.songnum for p in created.playlists if p.dirid == 201), None)
            result = [Playlist(0, "我喜欢", 201, True, liked)]
            result.extend(
                Playlist(p.id, p.title, p.dirid, song_count=p.songnum)
                for p in created.playlists
                if p.dirid != 201 and p.id
            )
            euin = self.client.credential.encrypt_uin
            if euin:
                favorites = await self.client.user.get_fav_songlist(euin, num=50).collect_items()
                known = {p.id for p in result}
                result.extend(
                    Playlist(p.id, p.title, song_count=p.songnum)
                    for p in favorites
                    if p.id not in known
                )
            return result

    async def search(self, keyword: str, page: int = 1) -> SongPage:
        """Fetch one search page while preserving the server total and page size."""
        async with asyncio.timeout(25):
            result = await self.client.search.search_by_type(
                keyword,
                num=30,
                page=page,
                highlight=False,
            )
        return SongPage(
            [Track.from_song(s) for s in result.song],
            result.nextpage != -1,
            result.total_num,
            page_size=30,
        )

    async def songs(self, playlist: Playlist, page: int = 1) -> SongPage:
        """Fetch a page from liked songs or a regular playlist with its server total."""
        async with asyncio.timeout(25):
            if playlist.favorite:
                result = await self.client.user.get_fav_song(
                    self.client.credential.encrypt_uin,
                    page=page,
                    num=100,
                )
            else:
                result = await self.client.songlist.get_detail(
                    playlist.id,
                    dirid=playlist.dirid,
                    num=100,
                    page=page,
                )
        return SongPage(
            [Track.from_song(s) for s in result.songs], bool(result.hasmore), result.total
        )

    def _file_info(self, track: Track, quality: Quality) -> SongFileInfo | None:
        """Select the audio source for a quality, returning None if its variant is absent."""
        media_mid = track.media_mid
        if quality.variant_index is not None:
            index = quality.variant_index
            if len(track.variants) <= index or not track.variants[index]:
                return None
            media_mid = track.variants[index]
        return SongFileInfo(
            track.mid,
            file_type=quality.file_type,
            media_mid=media_mid or None,
            song_type=track.song_type,
        )

    async def available_qualities(self, track: Track) -> list[Quality]:
        """Only offer qualities for which QQ returns an authorized playback path."""
        candidates = [info for quality in Quality if (info := self._file_info(track, quality))]
        async with asyncio.timeout(25):
            urls = await self.client.song.get_song_urls(candidates)
            valid = [info for info in urls.data if info.purl and info.result == 0]
            if not valid:
                return []
            cdn = await self.client.song.get_cdn_dispatch()
            if not cdn.sip:
                raise ValueError("QQ 音乐未返回播放服务器")
            reachable = await asyncio.gather(
                *(
                    asyncio.to_thread(url_reachable, urljoin(cdn.sip[0], info.purl))
                    for info in valid
                )
            )
        prefixes = {info.filename[:4] for info, ok in zip(valid, reachable, strict=True) if ok}
        return [quality for quality in Quality if quality.file_type.s in prefixes]

    async def stream_url(self, track: Track, quality: Quality) -> str:
        """Resolve the requested quality or raise a visible error without downgrading."""
        info = self._file_info(track, quality)
        if info is None:
            raise ValueError(f"此歌曲暂无{quality.label}音源，请按 U 选择其他音质")
        async with asyncio.timeout(25):
            urls = await self.client.song.get_song_urls(
                [info],
                file_type=quality.file_type,
            )
            if not urls.data or not urls.data[0].purl:
                code = urls.data[0].result if urls.data else "空响应"
                raise ValueError(f"此歌曲的{quality.label}不可用（{code}），请检查音源和会员权限")
            cdn = await self.client.song.get_cdn_dispatch()
            if not cdn.sip:
                raise ValueError("QQ 音乐未返回播放服务器")
            return urljoin(cdn.sip[0], urls.data[0].purl)

    async def close(self) -> None:
        """Release the QQ API client connection resources."""
        await self.client.close()
