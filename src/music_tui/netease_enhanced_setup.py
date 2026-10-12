"""Prepare local Enhanced service settings and run its pinned foreground process."""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .netease_config import config_directory, load_enhanced_config

PACKAGE = "@neteasecloudmusicapienhanced/api@4.41.1"


def _url(value: str) -> str:
    """Accept an HTTP service address without embedded credentials or query data."""
    try:
        parts = urlsplit(value.strip())
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError(
            "API 地址须为 http:// 或 https:// 地址，且不能包含账号、密码或查询参数。"
        ) from None
    return value.strip().rstrip("/")


def _local_port(value: str) -> int | None:
    """Return a saved loopback HTTP port suitable for a local service."""
    parts = urlsplit(_url(value))
    if parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"} and not parts.path:
        return parts.port or 80
    return None


def _is_enhanced(url: str) -> bool:
    """Identify the service through its unauthenticated login status response."""
    try:
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = client.get(f"{url}/login/status")
            response.raise_for_status()
            payload = response.json()
            return (
                isinstance(payload, dict)
                and isinstance(payload.get("data"), dict)
                and payload["data"].get("code") == 200
            )
    except (httpx.HTTPError, ValueError):
        return False


def _port_available(port: int) -> bool:
    """Check whether a local port can be bound without affecting existing listeners."""
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _select_port(saved_url: str) -> int:
    """Reuse a saved local port or select a free port when another service occupies it."""
    port = _local_port(saved_url) or 3000
    if _port_available(port) or _is_enhanced(f"http://127.0.0.1:{port}"):
        return port
    if port != 3000 and _port_available(3000):
        return 3000
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _node_status() -> tuple[bool, str]:
    """Check Node.js 22 or newer and npx without installing software."""
    if not shutil.which("node") or not shutil.which("npx"):
        return False, "请安装 Node.js 22 或更新版本（包含 npx），然后重跑向导。"
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, check=False, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "无法运行 Node.js，请检查安装后重跑向导。"
    match = re.fullmatch(r"v(\d+)\.\d+\.\d+\s*", result.stdout)
    if result.returncode or not match or int(match[1]) < 22:
        return False, "需要 Node.js 22 或更新版本，请升级后重跑向导。"
    return True, "Node.js 和 npx 已就绪。"


def _write_json(path: Path, value: dict) -> None:
    """Replace configuration atomically with owner-only file permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare(target: Path) -> None:
    """Write safe wizard defaults with detected prerequisites and an available local port."""
    saved_url = _url(load_enhanced_config()["apiUrl"])
    ready, message = _node_status()
    port = _select_port(saved_url)
    _write_json(
        target / "defaults.json",
        {
            "apiUrl": saved_url,
            "nodeReady": ready,
            "nodeMessage": message,
            "port": port,
            "localApiUrl": f"http://127.0.0.1:{port}",
        },
    )


def defaults(target: Path, field: str) -> None:
    """Print one allowed default for the bundled Bash wizard."""
    if field not in {"apiUrl", "localApiUrl", "nodeMessage", "nodeReady"}:
        raise ValueError("未知配置字段。")
    value = json.loads((target / "defaults.json").read_text())[field]
    print(str(value).lower() if isinstance(value, bool) else value)


def save(target: Path) -> None:
    """Validate the selected mode before replacing persisted Enhanced settings."""
    prepared = json.loads((target / "defaults.json").read_text())
    mode = os.environ.get("SETUP_MODE", "")
    if mode == "local":
        if not prepared["nodeReady"]:
            raise ValueError(prepared["nodeMessage"])
        port = int(prepared["port"])
        url = _url(f"http://127.0.0.1:{port}")
    elif mode == "existing":
        url = _url(os.environ.get("SETUP_API_URL", ""))
    else:
        raise ValueError("请选择本机服务或已有 API 服务。")
    _write_json(config_directory() / "enhanced.json", {"apiUrl": url})
    print(f"已保存网易云 Enhanced 配置：{url}")


def run_service() -> int:
    """Run the pinned Enhanced package in the foreground on the saved loopback port."""
    try:
        url = _url(load_enhanced_config()["apiUrl"])
        port = _local_port(url)
        if port is None:
            raise ValueError("当前配置使用已有 API 服务，无需启动本机服务。")
        if not _port_available(port):
            if _is_enhanced(url):
                print("网易云 Enhanced 服务已经运行，可直接启动 music-tui。")
                return 0
            raise ValueError("配置的端口已被其他服务占用，请重跑 music-tui --setup-netease。")
        ready, message = _node_status()
        if not ready:
            raise ValueError(message)
        print(f"正在启动网易云 Enhanced：{url}；请保留此终端。", flush=True)
        result = subprocess.run(
            ["npx", "--yes", PACKAGE],
            env={
                **os.environ,
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "ENABLE_GENERAL_UNBLOCK": "false",
                "ENABLE_PROXY": "false",
            },
            check=False,
        )
        return result.returncode
    except KeyboardInterrupt:
        return 130
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("网易云服务启动失败，请检查 Node.js 安装后重试。", file=sys.stderr)
        return 1


def main() -> int:
    """Expose prepare, defaults, and save operations to the bundled setup script."""
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "save", "defaults"])
    parser.add_argument("target", type=Path)
    parser.add_argument("field", nargs="?")
    arguments = parser.parse_args()
    try:
        if arguments.operation == "prepare":
            prepare(arguments.target)
        elif arguments.operation == "save":
            save(arguments.target)
        else:
            defaults(arguments.target, arguments.field)
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError:
        print("配置文件无法读取或保存，请检查目录权限。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
