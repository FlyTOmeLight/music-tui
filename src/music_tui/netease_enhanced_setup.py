"""Prepare local Enhanced service settings and manage its macOS background service."""

import argparse
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
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


SERVICE_LABEL = "org.music-tui.netease-enhanced"


def _launchctl(*arguments: str) -> subprocess.CompletedProcess:
    """Run the native service controller without exposing its environment output."""
    return subprocess.run(
        ["/bin/launchctl", *arguments], capture_output=True, text=True, check=False, timeout=10
    )


def _write_service(port: int) -> Path:
    """Save a private launchd job with automatic restart and private output logs."""
    directory = config_directory().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "enhanced-service.log"
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    job = {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [shutil.which("npx"), "--yes", PACKAGE],
        "WorkingDirectory": str(directory),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "ENABLE_GENERAL_UNBLOCK": "false",
            "ENABLE_PROXY": "false",
        },
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "Umask": 0o077,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    path = directory / "enhanced-service.plist"
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".enhanced-service-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(job, stream)
        Path(temporary).replace(path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def run_service(action: str = "start") -> int:
    """Start, inspect, or stop the login-session service managed by macOS launchd."""
    try:
        if sys.platform != "darwin":
            raise ValueError("后台服务目前支持 macOS；其他系统请按配置指南手动运行 npx。")
        domain = f"gui/{os.getuid()}"
        target = f"{domain}/{SERVICE_LABEL}"
        loaded = _launchctl("print", target).returncode == 0
        log = config_directory().resolve() / "enhanced-service.log"
        if action == "stop":
            if loaded and _launchctl("bootout", target).returncode:
                raise ValueError("停止网易云后台服务失败，请检查 launchctl。")
            print("网易云后台服务已停止。" if loaded else "网易云后台服务未启动。")
            return 0
        url = _url(load_enhanced_config()["apiUrl"])
        if action == "status":
            healthy = _is_enhanced(url)
            print(
                f"后台守护：{'已注册' if loaded else '未注册'}；"
                f"API：{'可连接' if healthy else '不可连接'}"
            )
            print(f"日志：{log}")
            return 0 if loaded and healthy else 1
        if action != "start":
            raise ValueError("未知服务操作。")
        port = _local_port(url)
        if port is None:
            raise ValueError("当前配置使用已有 API 服务，无需启动本机服务。")
        if not loaded:
            if not _port_available(port):
                if _is_enhanced(url):
                    raise ValueError(
                        "已有网易云服务正在运行，但未纳入后台守护。"
                        "请先停止原服务，再运行 --netease-service。"
                    )
                raise ValueError("配置的端口已被其他服务占用，请重跑 music-tui --setup-netease。")
            ready, message = _node_status()
            if not ready:
                raise ValueError(message)
            path = _write_service(port)
            if _launchctl("bootstrap", domain, str(path)).returncode:
                raise ValueError("注册网易云后台服务失败，请检查 launchctl 和当前桌面登录会话。")
        print(f"正在检查网易云后台服务：{url}…", flush=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if _is_enhanced(url):
                print("网易云后台服务已就绪，关闭终端后继续运行，异常退出后自动重启。")
                print(f"日志：{log}")
                return 0
            time.sleep(0.5)
        raise ValueError(
            "后台服务已注册，但 API 尚未就绪。首次下载可能较慢，"
            f"请稍后运行 --netease-service-status。日志：{log}"
        )
    except KeyboardInterrupt:
        print("已取消等待；后台服务可用 --netease-service-stop 停止。", file=sys.stderr)
        return 130
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, subprocess.TimeoutExpired):
        print("网易云后台服务操作失败，请检查目录权限和 launchctl。", file=sys.stderr)
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
