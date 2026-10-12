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

from ..models import LoginState, Playlist, Quality, SongPage, Track

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
    )


class NetEaseMusic:
    """Keep account cookies and library access separate from the interface."""

    display_name = "网易云音乐（Enhanced）"

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
                Playlist(int(item["id"]), item["name"], song_count=item.get("trackCount"))
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

    async def _audio(self, track: Track, quality: Quality) -> dict | None:
        """Reject missing, downgraded, or preview-only stream responses."""
        level = LEVELS[quality]
        result = await self._request("/song/url/v1", id=track.mid, level=level, unblock="false")
        audio = next(
            (item for item in result.get("data", []) if str(item.get("id")) == track.mid), None
        )
        if (
            audio
            and audio.get("code") == 200
            and audio.get("url")
            and audio.get("level") == level
            and audio.get("freeTrialInfo") is None
        ):
            return audio
        return None

    async def available_qualities(self, track: Track) -> list[Quality]:
        """Offer only exact quality levels authorized by NetEase for this account."""
        results = await asyncio.gather(*(self._audio(track, quality) for quality in Quality))
        return [quality for quality, audio in zip(Quality, results, strict=True) if audio]

    async def stream_url(self, track: Track, quality: Quality) -> str:
        """Resolve the selected full-length stream without quality downgrades."""
        audio = await self._audio(track, quality)
        if not audio:
            raise ValueError(f"此歌曲的{quality.label}不可用，请检查会员权限或按 U 更换音质")
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
