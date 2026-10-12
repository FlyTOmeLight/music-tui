"""Signed official NetEase requests and isolated, owner-only token storage."""

import base64
import json
import os
import re
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class OfficialAPIError(ValueError):
    """Expose safe API error codes without returning credentials or response bodies."""

    def __init__(self, code: str):
        """Keep the machine-readable code available to business operations."""
        self.code = code
        messages = {
            "300": "应用未获此接口授权",
            "1406": "登录已过期，请按 L 重新扫码",
            "1407": "账号或授权无效，请按 L 重新扫码",
            "1408": "登录令牌无效，请按 L 重新扫码",
            "10003": "当前端无版权，请在网易云手机端收听",
            "10004": "歌曲需要会员或单独购买",
        }
        detail = messages.get(code, "请求失败")
        super().__init__(f"网易云官方 API {detail}（状态 {code}）")


class OfficialClient:
    """Own signing keys, device identity, HTTP connections and account tokens."""

    def __init__(
        self,
        directory: Path,
        app_id: str,
        private_key_path: Path | None,
        device_file: Path | None = None,
    ):
        """Require application credentials and an explicitly approved device profile."""
        if not app_id or private_key_path is None or device_file is None:
            raise ValueError(
                "官方 API 需要 NETEASE_APP_ID、NETEASE_PRIVATE_KEY_FILE 和 NETEASE_DEVICE_FILE；"
                "可运行 music-tui --setup-netease --netease-backend official 自动配置，"
                "或使用 --netease-backend enhanced"
            )
        try:
            key_bytes = private_key_path.read_bytes().strip()
            if key_bytes.startswith(b"-----BEGIN"):
                key = serialization.load_pem_private_key(key_bytes, password=None)
            else:
                key = serialization.load_der_private_key(base64.b64decode(key_bytes), password=None)
            device = json.loads(device_file.read_text())
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("无法读取网易云官方私钥或设备配置文件") from error
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("网易云官方 API 需要 RSA 私钥")
        required = {
            "channel",
            "deviceId",
            "deviceType",
            "appVer",
            "os",
            "osVer",
            "brand",
            "model",
            "clientIp",
            "netStatus",
        }
        if not isinstance(device, dict) or any(
            name not in device or device[name] in (None, "") for name in required
        ):
            raise ValueError("网易云设备配置缺少公共参数，请按官方接入资料填写")
        if not re.fullmatch(r"[A-Za-z0-9]{1,64}", str(device["deviceId"])):
            raise ValueError("网易云 deviceId 必须为 1 至 64 位字母或数字")
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(device["appVer"])):
            raise ValueError("网易云 appVer 必须采用 x.x.x 格式")
        self.app_id = app_id
        self.key = key
        self.device = device
        self.token = ""
        self.credential_path = directory / "token.json"
        self.http = httpx.AsyncClient(
            base_url="https://openapi.music.163.com", timeout=25, trust_env=False
        )

    async def request(self, endpoint: str, biz: dict, token: str | None = None) -> dict:
        """Sign unescaped parameters and send them over HTTPS exactly once encoded."""
        params = {
            "appId": self.app_id,
            "signType": "RSA_SHA256",
            "timestamp": str(int(time.time() * 1000)),
            "device": json.dumps(self.device, ensure_ascii=False, separators=(",", ":")),
            "bizContent": json.dumps(biz, ensure_ascii=False, separators=(",", ":")),
        }
        access_token = self.token if token is None else token
        if access_token:
            params["accessToken"] = access_token
        canonical = "&".join(f"{key}={params[key]}" for key in sorted(params) if params[key] != "")
        params["sign"] = base64.b64encode(
            self.key.sign(canonical.encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        try:
            response = await self.http.get(endpoint, params=params)
            response.raise_for_status()
            result = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ValueError("无法访问网易云官方 API，请检查网络和应用配置") from error
        if not isinstance(result, dict):
            raise ValueError("网易云官方 API 返回无效响应")
        code = str(result.get("code", "未知"))
        subcode = str(result.get("subCode") or "200")
        if code != "200":
            raise OfficialAPIError(code if re.fullmatch(r"-?\d+", code) else "未知")
        if subcode not in {"200", "0"}:
            raise OfficialAPIError(subcode if re.fullmatch(r"-?\d+", subcode) else "未知")
        data = result.get("data")
        if not isinstance(data, dict):
            raise ValueError("网易云官方 API 缺少有效数据")
        return data

    async def restore(self) -> bool:
        """Restore tokens only for the same application and stable device identity."""
        self.token = ""
        if not self.credential_path.exists():
            return False
        try:
            saved = json.loads(self.credential_path.read_text())
            if (
                saved["appId"] != self.app_id
                or saved["deviceId"] != self.device["deviceId"]
                or saved["expiresAt"] <= time.time()
            ):
                return False
            token = saved["accessToken"]
            if not isinstance(token, str) or not token or token == "null":
                return False
            self.token = token
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError("网易云官方登录凭据损坏，请删除 token.json 后重新扫码") from error
        return True

    def save_token(self, data: dict) -> None:
        """Persist a successful login atomically with owner-only file permissions."""
        token = data.get("accessToken")
        if not isinstance(token, str) or not token or token == "null":
            raise ValueError("网易云官方登录响应缺少有效令牌")
        lifetime = int(data.get("expireTime") or data.get("expiresTime") or 0)
        if lifetime <= 0:
            raise ValueError("网易云官方登录响应缺少令牌有效期")
        saved = {
            "appId": self.app_id,
            "deviceId": self.device["deviceId"],
            "accessToken": token,
            "expiresAt": time.time() + lifetime,
        }
        path = self.credential_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".token-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(saved, stream)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.token = token

    async def close(self) -> None:
        """Release all HTTP connections."""
        await self.http.aclose()
