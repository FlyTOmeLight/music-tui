"""Detect local device defaults and validate private official NetEase configuration."""

import argparse
import asyncio
import base64
import ipaddress
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import uuid
from importlib.metadata import version
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .netease_config import config_directory, load_official_config
from .sources.netease_openapi import OfficialClient

DEVICE_FIELDS = (
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
)
DEFAULT_FIELDS = {*DEVICE_FIELDS, "appId", "privateKeyFile"}


def _command(*arguments: str) -> str:
    """Read local system information without network requests or shell expansion."""
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _network_defaults() -> tuple[str, str]:
    """Detect the default local interface's address and Wi-Fi only when identifiable."""
    if sys.platform == "darwin":
        route = _command("route", "-n", "get", "default")
        interface = re.search(r"^\s*interface:\s*(\S+)", route, re.MULTILINE)
        if not interface:
            return "", ""
        name = interface[1]
        address = _command("ipconfig", "getifaddr", name)
        ports = _command("networksetup", "-listallhardwareports")
        wifi = any(
            re.search(r"Hardware Port: (?:Wi-Fi|AirPort)\nDevice: " + re.escape(name) + r"\b", port)
            for port in ports.split("\n\n")
        )
        return address, "wifi" if wifi else ""
    if sys.platform.startswith("linux"):
        try:
            routes = json.loads(_command("ip", "-j", "route", "show", "default") or "[]")
            name = next(route["dev"] for route in routes if route.get("dev"))
            interfaces = json.loads(_command("ip", "-j", "address", "show", "dev", name) or "[]")
            address = next(
                info["local"]
                for interface in interfaces
                for info in interface.get("addr_info", [])
                if info.get("family") == "inet" and info.get("scope") == "global"
            )
        except (ValueError, TypeError, KeyError, StopIteration):
            return "", ""
        wifi = (Path("/sys/class/net") / name / "wireless").is_dir()
        return address, "wifi" if wifi else ""
    return "", ""


def _model() -> str:
    """Detect the machine model without assigning NetEase-controlled device values."""
    if sys.platform == "darwin":
        return _command("sysctl", "-n", "hw.model")
    if sys.platform.startswith("linux"):
        try:
            return Path("/sys/devices/virtual/dmi/id/product_name").read_text().strip()
        except OSError:
            return ""
    return platform.machine()


def _read_json(path: Path) -> dict:
    """Read an optional configuration object and reject corrupt existing files."""
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f"无法读取配置文件：{path}") from error
    if not isinstance(value, dict):
        raise ValueError(f"配置文件必须是 JSON 对象：{path}")
    return value


def _write_private(path: Path, content: bytes) -> None:
    """Replace one configuration file atomically with owner-only permissions."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".netease-setup-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(target: Path) -> dict:
    """Reuse saved inputs and generate automatic non-secret defaults in a private directory."""
    directory = config_directory()
    config = load_official_config()
    if any(not isinstance(value, str) for value in config.values()):
        raise ValueError("网易云配置字段必须是文本，请检查 netease.json")
    device_path = Path(config.get("deviceFile") or directory / "netease-device.json")
    previous = _read_json(device_path)
    address, network = _network_defaults()
    defaults = {field: previous.get(field, "") for field in DEVICE_FIELDS}
    identity = defaults.get("deviceId")
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", identity):
        defaults["deviceId"] = uuid.uuid4().hex
    defaults.update(
        appId=config.get("appId", ""),
        privateKeyFile=config.get("privateKeyFile", ""),
        appVer=version("music-tui"),
        osVer=platform.mac_ver()[0] if sys.platform == "darwin" else platform.release(),
        model=_model() or previous.get("model", ""),
        clientIp=address or previous.get("clientIp", ""),
        netStatus=network or previous.get("netStatus", ""),
    )
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write_private(target / "defaults.json", json.dumps(defaults, ensure_ascii=False).encode())
    return defaults


def default_value(target: Path, field: str) -> str:
    """Expose one allowed non-secret value for the interactive shell prompts."""
    if field not in DEFAULT_FIELDS:
        raise ValueError("不支持的默认配置字段")
    value = _read_json(target / "defaults.json").get(field, "")
    return value if isinstance(value, str) else ""


def check_credentials(target: Path) -> tuple[str, bytes]:
    """Validate only the app ID and RSA key without writing the final configuration."""
    app_id = os.environ.get("SETUP_APP_ID", default_value(target, "appId")).strip()
    if not app_id:
        raise ValueError("必须填写应用 ID（appId）")
    input_file = target / "private-key-input"
    value = (
        input_file.read_text() if input_file.exists() else os.environ.get("SETUP_PRIVATE_KEY", "")
    ).strip()
    candidate = value or default_value(target, "privateKeyFile")
    key_bytes = value.encode()
    try:
        path = Path(candidate).expanduser()
        if candidate and len(candidate) < 1024 and path.is_file():
            key_bytes = path.read_bytes().strip()
    except OSError as error:
        raise ValueError("无法读取私钥文件") from error
    if not key_bytes:
        raise ValueError("必须填写控制台私钥或现有私钥文件路径")
    try:
        if key_bytes.startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(key_bytes, password=None)
        else:
            key = serialization.load_der_private_key(base64.b64decode(key_bytes), password=None)
    except (ValueError, TypeError) as error:
        raise ValueError("私钥无效，请填写控制台 Base64 私钥或有效 PEM 文件路径") from error
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("网易云官方 API 需要 RSA 私钥")
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return app_id, pem


async def _validate(target: Path, app_id: str) -> bytes:
    """Reuse the runtime client to validate the device and canonicalize its RSA key offline."""
    client = OfficialClient(target, app_id, target / "key-input", target / "device-input.json")
    try:
        return client.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    finally:
        await client.close()


def save(target: Path) -> Path:
    """Validate all user inputs before writing the final private official profile."""
    defaults = _read_json(target / "defaults.json")
    app_id, key_bytes = check_credentials(target)
    device = {field: defaults.get(field, "") for field in DEVICE_FIELDS}
    for field in (
        "channel",
        "deviceType",
        "os",
        "brand",
        "clientIp",
        "netStatus",
        "model",
        "osVer",
    ):
        environment = "SETUP_" + re.sub(r"(?<!^)(?=[A-Z])", "_", field).upper()
        value = os.environ.get(environment, device[field])
        if not isinstance(value, str):
            raise ValueError("设备配置字段必须是文本")
        device[field] = value.strip()
    if any(not isinstance(value, str) or not value.strip() for value in device.values()):
        raise ValueError("设备配置不完整，请填写向导中的必填项")
    if device["netStatus"] not in {"wifi", "2g", "3g", "4g", "5g"}:
        raise ValueError("netStatus 必须为实际网络类型：wifi、2g、3g、4g 或 5g")
    try:
        ipaddress.ip_address(device["clientIp"])
    except ValueError as error:
        raise ValueError("clientIp 必须是有效的本机 IP 地址") from error
    _write_private(target / "key-input", key_bytes)
    _write_private(target / "device-input.json", json.dumps(device, ensure_ascii=False).encode())
    try:
        pem = asyncio.run(_validate(target, app_id))
    finally:
        (target / "key-input").unlink(missing_ok=True)
        (target / "device-input.json").unlink(missing_ok=True)
    directory = config_directory().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_file = directory / "netease-private-key.pem"
    device_file = directory / "netease-device.json"
    profile = {"appId": app_id, "privateKeyFile": str(key_file), "deviceFile": str(device_file)}
    _write_private(key_file, pem)
    _write_private(device_file, json.dumps(device, ensure_ascii=False, indent=2).encode())
    _write_private(directory / "netease.json", json.dumps(profile, indent=2).encode())
    return directory / "netease.json"


def main() -> None:
    """Run the narrow prepare, defaults, and save operations used by the setup wizard."""
    parser = argparse.ArgumentParser(description="网易云配置向导的本地支撑命令")
    parser.add_argument(
        "operation",
        choices=("prepare", "defaults", "read-private-key", "check-credentials", "save"),
    )
    parser.add_argument("target", type=Path)
    parser.add_argument("field", nargs="?")
    arguments = parser.parse_args()
    try:
        if arguments.operation == "prepare":
            prepare(arguments.target)
        elif arguments.operation == "defaults":
            print(default_value(arguments.target, arguments.field))
        elif arguments.operation == "read-private-key":
            from .netease_key_input import read_private_key

            _write_private(arguments.target / "private-key-input", read_private_key().encode())
        elif arguments.operation == "check-credentials":
            check_credentials(arguments.target)
        else:
            print(save(arguments.target))
    except KeyboardInterrupt:
        parser.exit(130, "已取消私钥输入。\n")
    except (ValueError, OSError, EOFError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
