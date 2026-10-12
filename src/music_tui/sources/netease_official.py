"""Access NetEase's official music API and authorized QR login from Python."""

import asyncio
from collections.abc import AsyncIterator
from io import BytesIO
from pathlib import Path

import qrcode

from ..models import LoginState, Playlist, Quality, SongPage, Track
from .netease_openapi import OfficialAPIError, OfficialClient

API = "/openapi/music/basic"
LEVELS = {
    Quality.MASTER: "jymaster",
    Quality.ATMOS: "vivid",
    Quality.ENHANCED: "jyeffect",
    Quality.HIRES: "hires",
    Quality.LOSSLESS: "lossless",
    Quality.HIGH: "exhigh",
    Quality.STANDARD: "standard",
}
QUALITY_FIELDS = {
    Quality.MASTER: "jyMasterMusic",
    Quality.ATMOS: "vividMusic",
    Quality.ENHANCED: "jyEffectMusic",
    Quality.HIRES: "hrMusic",
    Quality.LOSSLESS: "sqMusic",
    Quality.HIGH: "hmusic",
    Quality.STANDARD: "lmusic",
}


def _track(song: dict) -> Track:
    """Keep opaque resource IDs and convert official metadata to shared tracks."""
    artists = song.get("fullArtists") or song.get("artists") or []
    return Track(
        str(song["id"]),
        song["name"],
        " / ".join(artist["name"] for artist in artists if artist.get("name")),
        int(song.get("duration", 0)) // 1000,
    )


class OfficialNetEaseMusic:
    """Expose library and playback operations through the official signed API."""

    display_name = "网易云音乐（官方）"

    def __init__(
        self,
        directory: Path,
        app_id: str,
        private_key_path: Path | None,
        device_file: Path | None = None,
    ):
        """Create a signed client with separate official account storage."""
        self.client = OfficialClient(directory, app_id, private_key_path, device_file)
        self.app_id = app_id
        self.user_id: str | None = None
        self._anonymous_token = ""

    @property
    def logged_in(self) -> bool:
        """Report login only after the user profile has been verified."""
        return self.user_id is not None

    async def _anonymous(self) -> str:
        """Reuse a device-bound guest token for browsing and QR polling."""
        if not self._anonymous_token:
            data = await self.client.request(
                f"{API}/oauth2/login/anonymous", {"clientId": self.app_id}, token=""
            )
            token = data.get("accessToken")
            if not isinstance(token, str) or not token or token == "null":
                raise ValueError("网易云官方 API 未返回匿名登录令牌")
            self._anonymous_token = token
        return self._anonymous_token

    async def _request(self, endpoint: str, parameters: dict) -> dict:
        """Browse with an account token when present, otherwise use guest access."""
        token = self.client.token or await self._anonymous()
        return await self.client.request(f"{API}/{endpoint}", parameters, token=token)

    async def _profile(self, token: str) -> str:
        """Check an account token without persisting unverified credentials."""
        data = await self.client.request(f"{API}/user/profile/get/v2", {}, token=token)
        user_id = data.get("id")
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("网易云登录已失效，请重新扫码")
        return user_id

    async def restore(self) -> bool:
        """Restore local credentials and verify the account with the official API."""
        self.user_id = None
        if not await self.client.restore():
            return False
        try:
            self.user_id = await self._profile(self.client.token)
        except ValueError:
            self.client.token = ""
            raise
        return True

    def login_session(self) -> "OfficialNetEaseLoginSession":
        """Start an independent QR attempt that uses anonymous authorization."""
        return OfficialNetEaseLoginSession(self)

    async def playlists(self) -> list[Playlist]:
        """Collect created and followed playlists, preserving encrypted IDs."""
        if not self.logged_in:
            raise ValueError("请先按 L 扫码登录")
        playlists: dict[str, Playlist] = {}
        for kind in ("created", "subed"):
            offset = 0
            while True:
                data = await self._request(
                    f"playlist/{kind}/get/v2", {"limit": 100, "offset": offset}
                )
                items = data.get("records", [])
                for item in items:
                    playlist_id = str(item["id"])
                    playlists.setdefault(
                        playlist_id,
                        Playlist(
                            playlist_id,
                            item["name"],
                            favorite=item.get("specialType") == 5,
                            song_count=item.get("trackCount"),
                        ),
                    )
                offset += len(items)
                if not items or offset >= int(data.get("recordCount", 0)):
                    break
        return list(playlists.values())

    async def search(self, keyword: str, page: int = 1) -> SongPage:
        """Request a 30-song search page with its server-reported total."""
        offset = (page - 1) * 30
        data = await self._request(
            "search/song/get/v3",
            {"keyword": keyword, "limit": 30, "offset": offset, "qualityFlag": True},
        )
        total = int(data.get("recordCount", 0))
        return SongPage(
            [_track(song) for song in data.get("records", [])], offset + 30 < total, total, 30
        )

    async def songs(self, playlist: Playlist, page: int = 1) -> SongPage:
        """Read a playlist page directly without converting its opaque ID."""
        offset = (page - 1) * 100
        try:
            data = await self._request(
                "playlist/song/list/get/v5",
                {"playlistId": playlist.id, "limit": 100, "offset": offset, "qualityFlag": True},
            )
        except OfficialAPIError as error:
            if error.code == "10007" and playlist.song_count == 0:
                return SongPage([], False, 0)
            raise
        total = int(data.get("trackCount", 0))
        return SongPage(
            [_track(song) for song in data.get("tracks", [])], offset + 100 < total, total
        )

    async def available_qualities(self, track: Track) -> list[Quality]:
        """Offer exact audio tiers for which the current account has playback rights."""
        detail = await self._request("song/detail/get/v2", {"songId": track.mid, "withUrl": False})
        if detail.get("visible") is False or not detail.get("playFlag"):
            return []
        data = await self._request("song/music/quality/sound/sp/get", {"songId": track.mid})
        return [
            quality
            for quality, field in QUALITY_FIELDS.items()
            if ((data.get(field) or {}).get("sqPrivilege") or {}).get("playFlag") is True
        ]

    async def stream_url(self, track: Track, quality: Quality) -> str:
        """Resolve only full-length audio at the selected, authorized quality."""
        level = LEVELS[quality]
        data = await self._request(
            "song/detail/get/v2", {"songId": track.mid, "withUrl": True, "level": level}
        )
        if (
            data.get("visible") is False
            or not data.get("playFlag")
            or not data.get("playUrl")
            or data.get("freeTrail") is not None
            or data.get("level") != level
        ):
            raise ValueError(f"此歌曲的{quality.label}不可用，请检查会员权限或按 U 更换音质")
        return data["playUrl"]

    async def close(self) -> None:
        """Release the official client's HTTP resources."""
        await self.client.close()


class OfficialNetEaseLoginSession:
    """Generate a QR image and translate official polling states for the UI."""

    def __init__(self, music: OfficialNetEaseMusic):
        """Keep the QR key and guest token isolated from logged-in credentials."""
        self.music = music
        self.key = ""
        self.token = ""

    async def get_qrcode(self) -> bytes:
        """Encode the official login URL as PNG without an external image service."""
        self.token = await self.music._anonymous()
        data = await self.music.client.request(
            f"{API}/user/oauth2/qrcodekey/get/v2",
            {"type": 2, "expiredKey": "300"},
            token=self.token,
        )
        self.key = data["uniKey"]
        image = qrcode.make(data["qrCodeUrl"])
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def __aiter__(self) -> AsyncIterator[LoginState]:
        """Begin polling after the login QR has been generated."""
        return self._poll()

    async def _poll(self) -> AsyncIterator[LoginState]:
        """Verify and persist issued credentials before announcing login success."""
        if not self.key or not self.token:
            raise ValueError("请先生成网易云登录二维码")
        deadline = asyncio.get_running_loop().time() + 300
        while asyncio.get_running_loop().time() < deadline:
            data = await self.music.client.request(
                f"{API}/oauth2/device/login/qrcode/get",
                {"key": self.key, "clientId": self.music.app_id},
                token=self.token,
            )
            status = data.get("status")
            if status == 800:
                yield LoginState.EXPIRED
                return
            if status == 802:
                yield LoginState.CONFIRMED
            elif status == 803:
                credentials = data.get("accessToken") or {}
                token = credentials.get("accessToken")
                if not isinstance(token, str) or not token or token == "null":
                    raise ValueError("网易云登录响应缺少账号令牌")
                user_id = await self.music._profile(token)
                self.music.client.save_token(credentials)
                self.music.client.token = token
                self.music.user_id = user_id
                yield LoginState.DONE
                return
            elif status != 801:
                yield LoginState.REFUSED
                return
            await asyncio.sleep(2)
        yield LoginState.EXPIRED
