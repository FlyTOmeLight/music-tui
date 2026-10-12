"""Read independent official and Enhanced NetEase profiles and launch setup guides."""

import json
import os
from pathlib import Path


def config_directory() -> Path:
    """Use the user's XDG configuration directory, separate from the repository."""
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "music-tui"


def load_official_config() -> dict:
    """Read setup results while allowing each existing NETEASE variable to override it."""
    path = config_directory() / "netease.json"
    config = {}
    if path.exists():
        try:
            config = json.loads(path.read_text())
            if not isinstance(config, dict):
                raise ValueError("Invalid configuration object")
        except (OSError, ValueError) as error:
            raise ValueError(
                "网易云配置无法读取，请运行 music-tui --setup-netease --netease-backend official"
            ) from error
    for field, variable in {
        "appId": "NETEASE_APP_ID",
        "privateKeyFile": "NETEASE_PRIVATE_KEY_FILE",
        "deviceFile": "NETEASE_DEVICE_FILE",
    }.items():
        if variable in os.environ:
            config[field] = os.environ[variable]
    return config


def load_enhanced_config() -> dict:
    """Load the third-party service address without consulting official credentials."""
    path = config_directory() / "enhanced.json"
    if not path.exists():
        return {"apiUrl": "http://127.0.0.1:3000"}
    try:
        config = json.loads(path.read_text())
        if not isinstance(config, dict) or not isinstance(config.get("apiUrl"), str):
            raise ValueError("Invalid Enhanced configuration")
        if not config["apiUrl"]:
            raise ValueError("Empty Enhanced URL")
        return config
    except (OSError, ValueError) as error:
        raise ValueError("Enhanced 配置无法读取，请运行 music-tui --setup-netease") from error


def run_setup(backend: str = "enhanced") -> int:
    """Launch the bundled terminal wizard with this installation's Python interpreter."""
    import subprocess
    import sys
    from importlib.resources import as_file, files

    filename = "setup-netease.sh" if backend == "official" else "setup-netease-enhanced.sh"
    script = files("music_tui").joinpath(f"scripts/{filename}")
    try:
        with as_file(script) as path:
            result = subprocess.run(
                ["bash", str(path)],
                env={**os.environ, "MUSIC_TUI_PYTHON": sys.executable},
                check=False,
            )
        return result.returncode
    except FileNotFoundError:
        print("配置向导需要 Bash，请先安装 Bash。", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
