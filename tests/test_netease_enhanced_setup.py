"""Verify Enhanced setup without installing packages or touching account data."""

import json
import subprocess
from unittest.mock import MagicMock

import httpx
import pytest

from music_tui import netease_enhanced_setup as setup


@pytest.fixture
def environment(tmp_path, monkeypatch):
    """Keep saved configuration isolated and provide usable Node prerequisites."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("NETEASE_API_URL", raising=False)
    monkeypatch.setattr(setup, "_node_status", lambda: (True, "Node.js 和 npx 已就绪。"))
    return tmp_path


def test_prepare_uses_free_saved_port(environment, monkeypatch):
    """Preserve the user's local port when it is available."""
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://localhost:4567"})
    monkeypatch.setattr(setup, "_port_available", lambda port: port == 4567)
    setup.prepare(environment)
    path = environment / "defaults.json"
    assert json.loads(path.read_text())["port"] == 4567
    assert path.stat().st_mode & 0o777 == 0o600


def test_prepare_does_not_reuse_other_service_port(environment, monkeypatch):
    """Choose a free port instead of replacing an unrelated listener on port 3000."""
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:3000"})
    monkeypatch.setattr(setup, "_port_available", lambda port: False)
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: False)
    listener = MagicMock()
    listener.__enter__.return_value = listener
    listener.getsockname.return_value = ("127.0.0.1", 45678)
    monkeypatch.setattr(setup.socket, "socket", lambda: listener)
    setup.prepare(environment)
    port = json.loads((environment / "defaults.json").read_text())["port"]
    assert port == 45678
    assert (
        json.loads((environment / "defaults.json").read_text())["localApiUrl"]
        == "http://127.0.0.1:45678"
    )
    listener.bind.assert_called_once_with(("127.0.0.1", 0))


def test_prepare_reuses_running_enhanced(environment, monkeypatch):
    """Reuse an existing local Enhanced listener rather than changing its URL."""
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:3000"})
    monkeypatch.setattr(setup, "_port_available", lambda port: False)
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: True)
    setup.prepare(environment)
    assert json.loads((environment / "defaults.json").read_text())["port"] == 3000


@pytest.mark.parametrize("version, ready", [("v22.15.0\n", True), ("v21.7.3\n", False)])
def test_node_version_requirement(monkeypatch, version, ready):
    """Accept Node.js 22 or newer and reject older installed runtimes."""
    monkeypatch.setattr(setup.shutil, "which", lambda command: "/bin/" + command)
    monkeypatch.setattr(
        setup.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, version, ""),
    )
    assert setup._node_status()[0] is ready


def test_missing_npx_requires_install(monkeypatch):
    """Report a missing package runner before attempting to launch a service."""
    monkeypatch.setattr(
        setup.shutil, "which", lambda command: "/bin/node" if command == "node" else None
    )
    assert setup._node_status()[0] is False


@pytest.mark.parametrize(
    "payload, expected", [({"data": {"code": 200}}, True), ({"code": 200}, False)]
)
def test_service_detection(monkeypatch, payload, expected):
    """Use the unauthenticated login-status shape without proxy inheritance."""
    original = httpx.Client

    def client(**options):
        """Capture transport settings and supply an offline API response."""
        assert options == {"trust_env": False, "timeout": 2}
        return original(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        )

    monkeypatch.setattr(setup.httpx, "Client", client)
    assert setup._is_enhanced("http://127.0.0.1:3000") is expected


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "http://user:secret@example.com",
        "https://example.com?secret=1",
        "http://example.com:99999",
    ],
)
def test_invalid_existing_url_preserves_config(environment, monkeypatch, url):
    """Reject unsafe service URLs without replacing the user's saved settings."""
    config = setup.config_directory() / "enhanced.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"apiUrl":"http://127.0.0.1:7777"}')
    (environment / "defaults.json").write_text("{}")
    monkeypatch.setenv("SETUP_MODE", "existing")
    monkeypatch.setenv("SETUP_API_URL", url)
    with pytest.raises(ValueError, match="API 地址") as error:
        setup.save(environment)
    assert "secret" not in str(error.value)
    assert json.loads(config.read_text())["apiUrl"] == "http://127.0.0.1:7777"


def test_save_existing_remote_service_without_node(environment, monkeypatch):
    """Persist a valid remote service without requiring local Node.js."""
    (environment / "defaults.json").write_text('{"nodeReady":false}')
    monkeypatch.setenv("SETUP_MODE", "existing")
    monkeypatch.setenv("SETUP_API_URL", "https://music.example/api/")
    setup.save(environment)
    config = setup.config_directory() / "enhanced.json"
    assert json.loads(config.read_text()) == {"apiUrl": "https://music.example/api"}
    assert config.stat().st_mode & 0o777 == 0o600


def test_save_local_uses_detected_port(environment, monkeypatch):
    """Persist the automatic local port without asking users to enter it."""
    (environment / "defaults.json").write_text('{"nodeReady":true,"port":4567}')
    monkeypatch.setenv("SETUP_MODE", "local")
    setup.save(environment)
    assert (
        json.loads((setup.config_directory() / "enhanced.json").read_text())["apiUrl"]
        == "http://127.0.0.1:4567"
    )


def test_save_local_requires_node(environment, monkeypatch):
    """Leave configuration unchanged when local prerequisites are missing."""
    (environment / "defaults.json").write_text('{"nodeReady":false,"nodeMessage":"需要 Node.js"}')
    monkeypatch.setenv("SETUP_MODE", "local")
    with pytest.raises(ValueError, match="Node.js"):
        setup.save(environment)
    assert not (setup.config_directory() / "enhanced.json").exists()


def test_run_service_pins_package_and_safety_settings(environment, monkeypatch):
    """Run a foreground child with the saved port and disabled unblock/proxy features."""
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:4567"})
    monkeypatch.setattr(setup, "_port_available", lambda port: True)
    calls = []

    def run(command, **options):
        """Record the service command without starting Node.js."""
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(setup.subprocess, "run", run)
    assert setup.run_service() == 0
    command, options = calls[0]
    assert command == ["npx", "--yes", "@neteasecloudmusicapienhanced/api@4.41.1"]
    assert options["env"]["PORT"] == "4567"
    assert options["env"]["HOST"] == "127.0.0.1"
    assert options["env"]["ENABLE_GENERAL_UNBLOCK"] == "false"
    assert options["env"]["ENABLE_PROXY"] == "false"


@pytest.mark.parametrize("enhanced, expected", [(True, 0), (False, 1)])
def test_occupied_port_never_starts_child(environment, monkeypatch, enhanced, expected):
    """Reuse Enhanced or report another listener without launching or stopping processes."""
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:4567"})
    monkeypatch.setattr(setup, "_port_available", lambda port: False)
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: enhanced)

    def forbidden(*args, **kwargs):
        """Fail if an occupied port triggers process execution."""
        pytest.fail("A service process must not be started")

    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    assert setup.run_service() == expected


def test_remote_service_cannot_be_started_locally(environment, monkeypatch):
    """Tell users that an existing remote endpoint requires no local child."""
    monkeypatch.setattr(
        setup, "load_enhanced_config", lambda: {"apiUrl": "https://music.example/api"}
    )
    assert setup.run_service() == 1


def test_defaults_only_prints_allowed_fields(environment, capsys):
    """Supply script-readable booleans while rejecting unexpected field names."""
    (environment / "defaults.json").write_text('{"nodeReady":true}')
    setup.defaults(environment, "nodeReady")
    assert capsys.readouterr().out == "true\n"
    with pytest.raises(ValueError):
        setup.defaults(environment, "port")
