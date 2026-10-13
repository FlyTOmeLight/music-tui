"""QQ login, library and stream access."""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from qqmusic_api import Client, Credential
from qqmusic_api.models.base import Song
from qqmusic_api.models.login import QRCodeLoginEvents, QRLoginType
from qqmusic_api.modules.login_utils import QRCodeLoginSession
from qqmusic_api.modules.song import BaseSongFileType, SongFileInfo, SongFileType

from ..models import (
    Artist,
    ArtistDetail,
    ArtistOrder,
    LoginState,
    Lyrics,
    Playlist,
    Quality,
    QualityUnavailableError,
    SongPage,
    Track,
)
from .lyrics import parse_lyrics


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
        cover_url=song.cover_url(),
        artists=tuple(Artist(s.mid, s.name) for s in song.singer if s.mid),
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
    """Return False only for missing CDN files; preserve network and server failures."""
    try:
        address = urlsplit(url)
        if address.scheme not in ("http", "https") or not address.netloc:
            raise ValueError("无效地址")
        with urlopen(Request(url, method="HEAD"), timeout=8) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"QQ 音乐音源服务器响应异常（HTTP {response.status}）")
            return True
    except HTTPError as error:
        if error.code in (404, 410):
            error.close()
            return False
        raise
    except ValueError as error:
        raise ValueError("QQ 音乐返回了无效音源地址") from error


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
    supports_playlist_creation = True

    def __init__(self, directory: Path):
        """Create a QQ client with account files in the supplied directory."""
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.credential_path = directory / "credential.json"
        self.client = Client(device_path=str(directory / "device.json"))

    @property
    def logged_in(self) -> bool:
        """Report whether the client has both required login credential fields."""
        return bool(self.client.credential.musicid and self.client.credential.musickey)

    @property
    def account_id(self) -> str:
        """Return the logged-in QQ Music account ID without exposing credentials."""
        return str(self.client.credential.musicid) if self.logged_in else ""

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
            liked = next((p for p in created.playlists if p.dirid == 201), None)
            result = [
                Playlist(
                    0,
                    "我喜欢",
                    201,
                    True,
                    liked.songnum if liked else None,
                    editable=True,
                    play_count=liked.play_cnt if liked else None,
                    cover_url=liked.picurl if liked else "",
                )
            ]
            result.extend(
                Playlist(
                    p.id,
                    p.title,
                    p.dirid,
                    song_count=p.songnum,
                    editable=True,
                    play_count=p.play_cnt,
                    cover_url=p.picurl,
                )
                for p in created.playlists
                if p.dirid != 201 and p.id
            )
            euin = self.client.credential.encrypt_uin
            if euin:
                favorites = await self.client.user.get_fav_songlist(euin, num=50).collect_items()
                known = {p.id for p in result}
                result.extend(
                    Playlist(
                        p.id,
                        p.title,
                        song_count=p.songnum,
                        play_count=p.listennum if "listennum" in p.model_fields_set else None,
                        cover_url=p.picurl,
                    )
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

    async def artist_detail(self, artist: Artist) -> ArtistDetail:
        """Fetch optional biography and artwork for the exact QQ singer MID."""
        async with asyncio.timeout(25):
            result = await self.client.singer.get_desc([artist.id])
        detail = next((s for s in result.singer_list if s.basic_info.mid == artist.id), None)
        if detail is None:
            return ArtistDetail(artist)
        basic = detail.basic_info
        return ArtistDetail(
            Artist(basic.mid, basic.name or artist.name),
            detail.pic.pic or (basic.cover_url() if basic.has_photo else ""),
            detail.ex_info.desc,
        )

    async def artist_songs(self, artist: Artist, order: ArtistOrder, page: int = 1) -> SongPage:
        """Page the server's complete hot or latest order using its verified num field."""
        offset = (page - 1) * 30
        request = self.client.singer.get_songs_list(artist.id, num=30, page=page)
        request = replace(
            request,
            param={
                "singerMid": artist.id,
                "order": 1 if order == ArtistOrder.HOT else 2,
                "num": 30,
                "begin": offset,
            },
        )
        async with asyncio.timeout(25):
            result = await request
        return SongPage(
            [track_from_song(song) for song in result.song_list],
            offset + len(result.song_list) < result.total_num,
            result.total_num,
            page_size=30,
        )

    async def lyrics(self, track: Track) -> Lyrics:
        """Let the pinned SDK decrypt original and translated LRC before parsing."""
        async with asyncio.timeout(25):
            result = await self.client.lyric.get_lyric(track.mid, trans=True)
        return parse_lyrics(result.lyric, result.trans)

    async def liked_ids(self) -> set[str]:
        """Collect every liked-song page using the same opaque MID as search tracks."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        async with asyncio.timeout(25):
            songs = await self.client.user.get_fav_song(
                self.client.credential.encrypt_uin, num=100
            ).collect_items()
        return {song.mid for song in songs}

    async def is_liked(self, track: Track) -> bool:
        """Recognize liked songs beyond the first page by their platform identity."""
        return track.mid in await self.liked_ids()

    async def set_liked(self, track: Track, liked: bool) -> None:
        """Resolve the numeric song identity and explicitly set the QQ like state."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        async with asyncio.timeout(25):
            detail = await self.client.song.get_detail(track.mid)
            operation = (
                self.client.songlist.like_song if liked else self.client.songlist.unlike_song
            )
            success = await operation([(detail.track.id, detail.track.type)])
        if not success:
            raise ValueError("QQ 音乐未能更新喜欢状态，请重试")

    async def add_to_playlist(self, track: Track, playlist: Playlist) -> None:
        """Append one song to a user-created QQ playlist using its directory ID."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        if not playlist.editable:
            raise ValueError("此歌单不可编辑，请选择自己的歌单")
        async with asyncio.timeout(25):
            detail = await self.client.song.get_detail(track.mid)
            success = await self.client.songlist.add_songs(
                playlist.dirid, [(detail.track.id, detail.track.type)], tid=int(playlist.id)
            )
        if not success:
            raise ValueError("QQ 音乐未能添加歌曲，请重试")

    async def create_playlist(self, title: str) -> Playlist:
        """Create a QQ playlist and retain its actual name and editable directory ID."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        title = title.strip()
        if not title:
            raise ValueError("请输入歌单名称")
        async with asyncio.timeout(25):
            result = await self.client.songlist.create(title)
        if result.retCode != 0 or not result.id or not result.dirid:
            raise ValueError("QQ 音乐未能新建歌单，请重试")
        return Playlist(result.id, result.name, result.dirid, song_count=0, editable=True)

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
        """Resolve the exact authorized tier, distinguishing unavailable audio from API errors."""
        info = self._file_info(track, quality)
        if info is None:
            raise QualityUnavailableError(f"此歌曲暂无{quality.label}音源")
        async with asyncio.timeout(25):
            urls = await self.client.song.get_song_urls(
                [info],
                file_type=QQ_QUALITIES[quality][0],
            )
            if not urls.data or not urls.data[0].purl or urls.data[0].result != 0:
                code = urls.data[0].result if urls.data else "空响应"
                raise QualityUnavailableError(
                    f"QQ 结果码 {code}：未提供可用的{quality.label}播放地址"
                )
            cdn = await self.client.song.get_cdn_dispatch()
            if not cdn.sip:
                raise ValueError("QQ 音乐未返回播放服务器")
            url = urljoin(cdn.sip[0], urls.data[0].purl)
            if not await asyncio.to_thread(url_reachable, url):
                raise QualityUnavailableError(f"此歌曲的{quality.label}音源文件不存在")
            return url

    async def close(self) -> None:
        """Release the QQ API client connection resources."""
        await self.client.close()
