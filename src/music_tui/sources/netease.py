"""NetEase library and QR login through a locally configured API service."""

import asyncio
import base64
import json
import os
import tempfile
import time
from collections.abc import AsyncIterator
from http.cookies import SimpleCookie
from pathlib import Path

import httpx

from ..models import LoginState, Lyrics, Playlist, Quality, QualityUnavailableError, SongPage, Track
from .lyrics import parse_lyrics

LEVELS = {
    Quality.MASTER: "jymaster",
    Quality.ATMOS: "vivid",
    Quality.ENHANCED: "jyeffect",
    Quality.HIRES: "hires",
    Quality.LOSSLESS: "lossless",
    Quality.HIGH: "exhigh",
    Quality.STANDARD: "standard",
}


def _track(song: dict) -> Track:
    """Normalize NetEase song fields and convert milliseconds to seconds."""
    return Track(
        str(song["id"]),
        song["name"],
        " / ".join(artist["name"] for artist in song.get("ar", [])),
        int(song.get("dt", 0)) // 1000,
        cover_url=(song.get("al") or {}).get("picUrl") or "",
    )


class NetEaseMusic:
    """Keep account cookies and library access separate from the interface."""

    display_name = "网易云音乐（Enhanced）"
    supports_playlist_creation = True

    def __init__(self, directory: Path, api_url: str = "http://127.0.0.1:3000"):
        """Use a service with ENABLE_GENERAL_UNBLOCK explicitly disabled."""
        self.credential_path = directory / "netease-credential.json"
        self.client = httpx.AsyncClient(base_url=api_url, trust_env=False, timeout=25)
        self.cookie = ""
        self.user_id: int | None = None
        self._playlist_ids: dict[int, list[int]] = {}

    @property
    def logged_in(self) -> bool:
        """Report whether the server verified the current account cookies."""
        return self.user_id is not None

    @property
    def account_id(self) -> str:
        """Return the server-verified NetEase account ID, or an empty guest ID."""
        return str(self.user_id) if self.logged_in else ""

    async def _request(self, endpoint: str, *, qr: bool = False, **parameters) -> dict:
        """Send secrets in POST bodies and surface errors without response contents."""
        parameters["timestamp"] = int(time.time() * 1000)
        parameters["cookie"] = self.cookie
        try:
            # Enhanced caches by URL and HTTP cookies, ignoring the POST body.
            response = await self.client.post(
                endpoint, json=parameters, headers={"x-apicache-force-fetch": "true"}
            )
            response.raise_for_status()
            result = response.json()
        except httpx.ConnectError as error:
            raise ValueError(
                "无法连接网易云 API 服务。\n"
                "请另开终端运行：\n"
                "uv run music-tui --netease-service\n"
                "服务启动后按 R 重试。"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise ValueError(
                "网易云 API 请求失败。\n" "请检查服务终端和已配置的服务地址，按 R 重试。"
            ) from error
        if not isinstance(result, dict):
            raise ValueError("网易云 API 返回了无效响应")
        if endpoint == "/playlist/tracks" and isinstance(result.get("body"), dict):
            # Enhanced 4.41.1 wraps normal writes but returns duplicate retries directly.
            result = result["body"]
        allowed = {200, 800, 801, 802, 803} if qr else {200}
        if result.get("code") not in allowed:
            raise ValueError(f"网易云请求失败（状态 {result.get('code', '未知')}）")
        if qr and result["code"] == 803:
            issued = SimpleCookie()
            for cookie in response.cookies.jar:
                issued[cookie.name] = cookie.value
            if "MUSIC_U" in issued:
                # Use only this response's credentials, never the client's older account jar.
                result["cookie"] = "; ".join(
                    f"{name}={item.coded_value}" for name, item in issued.items()
                )
        return result

    async def _account(self) -> None:
        """Verify restored or scanned cookies before exposing a logged-in account."""
        self.user_id = None
        result = await self._request("/user/account")
        profile = result.get("profile") or {}
        account = result.get("account") or {}
        user_id = profile.get("userId") or account.get("id")
        if not user_id:
            raise ValueError("网易云登录已失效，请重新扫码")
        self.user_id = int(user_id)

    async def restore(self) -> bool:
        """Load only saved cookies and verify them against the account endpoint."""
        if not self.credential_path.exists():
            return False
        self.cookie = json.loads(self.credential_path.read_text())["cookie"]
        try:
            await self._account()
        except ValueError:
            self.user_id = None
            raise
        return True

    def _save_cookie(self) -> None:
        """Atomically write cookies with owner-only permissions."""
        path = self.credential_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".netease-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"cookie": self.cookie}, stream)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def login_session(self) -> "NetEaseLoginSession":
        """Create a cancellable QR login session using the local service."""
        return NetEaseLoginSession(self)

    async def playlists(self) -> list[Playlist]:
        """Collect all pages of the verified user's created and followed playlists."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        playlists: list[Playlist] = []
        offset = 0
        while True:
            result = await self._request(
                "/user/playlist", uid=self.user_id, limit=100, offset=offset
            )
            items = result.get("playlist", [])
            playlists.extend(
                Playlist(
                    int(item["id"]),
                    item["name"],
                    favorite=item.get("specialType") == 5,
                    song_count=item.get("trackCount"),
                    editable=str((item.get("creator") or {}).get("userId")) == self.account_id,
                    play_count=item.get("playCount"),
                    cover_url=item.get("coverImgUrl") or "",
                )
                for item in items
            )
            if not result.get("more") or not items:
                break
            offset += len(items)
        return playlists

    async def search(self, keyword: str, page: int = 1) -> SongPage:
        """Return one search page with the server's total song count."""
        offset = (page - 1) * 30
        result = await self._request(
            "/cloudsearch", keywords=keyword, type=1, limit=30, offset=offset
        )
        data = result.get("result") or {}
        total = int(data.get("songCount", 0))
        return SongPage(
            [_track(song) for song in data.get("songs", [])], offset + 30 < total, total, 30
        )

    async def songs(self, playlist: Playlist, page: int = 1) -> SongPage:
        """Page over all track IDs, then preserve their order in song details."""
        if page == 1 or playlist.id not in self._playlist_ids:
            result = await self._request("/playlist/detail", id=playlist.id)
            ids = [int(item["id"]) for item in result["playlist"]["trackIds"]]
            self._playlist_ids[playlist.id] = ids
        ids = self._playlist_ids[playlist.id]
        offset = (page - 1) * 100
        selected = ids[offset : offset + 100]
        if not selected:
            return SongPage([], False, len(ids))
        result = await self._request("/song/detail", ids=",".join(map(str, selected)))
        by_id = {int(song["id"]): song for song in result.get("songs", [])}
        if any(song_id not in by_id for song_id in selected):
            raise ValueError("网易云未返回完整歌曲信息，请重试")
        return SongPage(
            [_track(by_id[song_id]) for song_id in selected],
            offset + 100 < len(ids),
            len(ids),
        )

    async def lyrics(self, track: Track) -> Lyrics:
        """Fetch line-timed lyrics and their optional translated counterpart."""
        result = await self._request("/lyric", id=track.mid)
        return parse_lyrics(
            (result.get("lrc") or {}).get("lyric", ""),
            (result.get("tlyric") or {}).get("lyric", ""),
        )

    async def is_liked(self, track: Track) -> bool:
        """Read the verified account's complete liked-song ID list."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        result = await self._request("/likelist", uid=self.user_id)
        return track.mid in {str(song_id) for song_id in result.get("ids", [])}

    async def set_liked(self, track: Track, liked: bool) -> None:
        """Set the like state using Enhanced's string-valued boolean parameter."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        await self._request("/like", id=track.mid, like="true" if liked else "false")
        self._playlist_ids.clear()

    async def add_to_playlist(self, track: Track, playlist: Playlist) -> None:
        """Append to an owned playlist and invalidate its cached song ordering."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        if not playlist.editable:
            raise ValueError("此歌单不可编辑，请选择自己的歌单")
        await self._request("/playlist/tracks", op="add", pid=playlist.id, tracks=track.mid)
        self._playlist_ids.pop(playlist.id, None)

    async def create_playlist(self, title: str) -> Playlist:
        """Create a normal public playlist using the verified account's cookies."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        title = title.strip()
        if not title:
            raise ValueError("请输入歌单名称")
        result = await self._request("/playlist/create", name=title, privacy="0", type="NORMAL")
        item = result.get("playlist") or {}
        if not item.get("id"):
            raise ValueError("网易云未返回新建歌单信息，请刷新歌单列表")
        return Playlist(
            int(item["id"]),
            item.get("name") or title,
            song_count=item.get("trackCount", 0),
            editable=True,
            play_count=item.get("playCount"),
            cover_url=item.get("coverImgUrl") or "",
        )

    async def _audio(self, track: Track, quality: Quality) -> dict:
        """Reject missing, downgraded, or preview-only stream responses."""
        level = LEVELS[quality]
        result = await self._request("/song/url/v1", id=track.mid, level=level, unblock="false")
        audio = next(
            (item for item in result.get("data", []) if str(item.get("id")) == track.mid), None
        )
        if audio is None:
            reason = "未返回此歌曲的音源"
        elif audio.get("code") != 200:
            code = audio.get("code")
            result_code = code if isinstance(code, int) else "未知"
            reason = f"结果码 {result_code}：未提供可用音源"
        elif audio.get("freeTrialInfo") is not None:
            reason = "仅提供试听，无法完整播放"
        elif not audio.get("url"):
            reason = "未提供播放地址"
        elif audio.get("level") != level:
            reason = "返回音质与请求不符"
        else:
            return audio
        raise QualityUnavailableError(f"网易云{quality.label}不可用：{reason}")

    async def available_qualities(self, track: Track) -> list[Quality]:
        """Offer only exact quality levels authorized by NetEase for this account."""

        async def available(quality: Quality) -> bool:
            """Exclude unavailable tiers while preserving request and login failures."""
            try:
                await self._audio(track, quality)
            except QualityUnavailableError:
                return False
            return True

        results = await asyncio.gather(*(available(quality) for quality in Quality))
        return [quality for quality, allowed in zip(Quality, results, strict=True) if allowed]

    async def stream_url(self, track: Track, quality: Quality) -> str:
        """Resolve the exact full tier, preserving the source's failure reason."""
        audio = await self._audio(track, quality)
        return audio["url"]

    async def close(self) -> None:
        """Release HTTP connections when the application exits."""
        await self.client.aclose()


class NetEaseLoginSession:
    """Convert API QR polling into the application's shared login states."""

    def __init__(self, music: NetEaseMusic):
        """Keep the login key local to this attempt."""
        self.music = music
        self.key = ""

    async def get_qrcode(self) -> bytes:
        """Return the PNG generated by the service for this login key."""
        result = await self.music._request("/login/qr/key")
        self.key = result["data"]["unikey"]
        result = await self.music._request("/login/qr/create", key=self.key, qrimg=True)
        image = result["data"]["qrimg"]
        if not image.startswith("data:image/png;base64,"):
            raise ValueError("网易云 API 未返回有效的二维码图片")
        return base64.b64decode(image.split(",", 1)[1], validate=True)

    def __aiter__(self) -> AsyncIterator[LoginState]:
        """Start a bounded asynchronous poll loop after QR creation."""
        return self._poll()

    async def _poll(self) -> AsyncIterator[LoginState]:
        """Persist verified cookies before reporting successful login."""
        if not self.key:
            raise ValueError("请先生成网易云登录二维码")
        deadline = asyncio.get_running_loop().time() + 180
        while asyncio.get_running_loop().time() < deadline:
            result = await self.music._request("/login/qr/check", qr=True, key=self.key)
            code = result["code"]
            if code == 800:
                yield LoginState.EXPIRED
                return
            if code == 802:
                yield LoginState.CONFIRMED
            elif code == 803:
                cookies = SimpleCookie()
                raw_cookie = result.get("cookie", "")
                if not isinstance(raw_cookie, str):
                    raise ValueError("网易云登录响应缺少账号凭据")
                # Enhanced joins Set-Cookie strings; trailing delimiters can create empty parts.
                normalized = "; ".join(
                    part.strip() for part in raw_cookie.split(";") if part.strip()
                )
                cookies.load(normalized)
                if "MUSIC_U" not in cookies or not cookies["MUSIC_U"].value:
                    raise ValueError("网易云登录响应缺少账号凭据")
                self.music.cookie = "; ".join(
                    f"{name}={item.coded_value}" for name, item in cookies.items()
                )
                await self.music._account()
                self.music._save_cookie()
                self.music._playlist_ids.clear()
                yield LoginState.DONE
                return
            await asyncio.sleep(2)
        yield LoginState.EXPIRED
