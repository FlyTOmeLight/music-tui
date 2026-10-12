"""QQ login, library and stream access."""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from qqmusic_api import Client, Credential
from qqmusic_api.models.base import Song
from qqmusic_api.models.login import QRCodeLoginEvents, QRLoginType
from qqmusic_api.modules.login_utils import QRCodeLoginSession
from qqmusic_api.modules.song import BaseSongFileType, SongFileInfo, SongFileType

from ..models import LoginState, Playlist, Quality, SongPage, Track


class AdditionalSongFileType(BaseSongFileType):
    HIRES = ("RS01", ".flac")


QQ_QUALITIES = {
    Quality.MASTER: (SongFileType.MASTER, 3),
    Quality.ATMOS: (SongFileType.ATMOS_71, 24),
    Quality.ENHANCED: (SongFileType.ATMOS_2, 4),
    Quality.HIRES: (AdditionalSongFileType.HIRES, None),
    Quality.LOSSLESS: (SongFileType.FLAC, None),
    Quality.HIGH: (SongFileType.MP3_320, None),
    Quality.STANDARD: (SongFileType.MP3_128, None),
}


def track_from_song(song: Song) -> Track:
    """Convert QQ song metadata into a shared track with its media variants."""
    return Track(
        song.mid,
        song.title or song.name,
        " / ".join(s.name for s in song.singer),
        song.interval,
        song.file.media_mid,
        song.type,
        tuple(song.vs),
    )


class QQLoginSession:
    """Translate QQ SDK QR events while keeping credentials inside the source."""

    def __init__(self, music: "QQMusic"):
        """Start an SDK login attempt owned by this account."""
        self.music = music
        self.session = QRCodeLoginSession(
            music.client.login, QRLoginType.MOBILE, timeout_seconds=180
        )

    async def get_qrcode(self) -> bytes:
        """Return the SDK's QR image without exposing its response type."""
        return (await self.session.get_qrcode()).data

    async def __aiter__(self) -> AsyncIterator[LoginState]:
        """Map events and persist confirmed QQ credentials before signalling success."""
        async for result in self.session:
            if result.event == QRCodeLoginEvents.CONF:
                yield LoginState.CONFIRMED
            elif result.event == QRCodeLoginEvents.DONE and result.credential:
                self.music.accept_login(result.credential)
                yield LoginState.DONE
                return
            elif result.event == QRCodeLoginEvents.TIMEOUT:
                yield LoginState.EXPIRED
                return
            elif result.event == QRCodeLoginEvents.REFUSE:
                yield LoginState.REFUSED
                return


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


class QQMusic:
    """QQ Music library and account implementation."""

    display_name = "QQ 音乐"

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

    def login_session(self) -> QQLoginSession:
        """Create a QR login session for confirmation in the mobile QQ Music app."""
        return QQLoginSession(self)

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
            [track_from_song(s) for s in result.song],
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
            [track_from_song(s) for s in result.songs], bool(result.hasmore), result.total
        )

    def _file_info(self, track: Track, quality: Quality) -> SongFileInfo | None:
        """Select the audio source for a quality, returning None if its variant is absent."""
        file_type, variant_index = QQ_QUALITIES[quality]
        media_mid = track.media_mid
        if variant_index is not None:
            index = variant_index
            if len(track.variants) <= index or not track.variants[index]:
                return None
            media_mid = track.variants[index]
        return SongFileInfo(
            track.mid,
            file_type=QQ_QUALITIES[quality][0],
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
        return [quality for quality in Quality if QQ_QUALITIES[quality][0].s in prefixes]

    async def stream_url(self, track: Track, quality: Quality) -> str:
        """Resolve the requested quality or raise a visible error without downgrading."""
        info = self._file_info(track, quality)
        if info is None:
            raise ValueError(f"此歌曲暂无{quality.label}音源，请按 U 选择其他音质")
        async with asyncio.timeout(25):
            urls = await self.client.song.get_song_urls(
                [info],
                file_type=QQ_QUALITIES[quality][0],
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
