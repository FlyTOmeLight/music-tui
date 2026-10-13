"""Verify Enhanced setup without installing packages or touching account data."""

import json
import plistlib
import subprocess
from unittest.mock import MagicMock

import httpx
import pytest

from music_tui import netease_enhanced_setup as setup


def loaded_job_output(port=3000, **changes):
    """Describe loaded execution settings independently of the on-disk plist."""
    job = setup._service_configuration(port)
    job.update(changes)
    arguments = "\n".join(f"\t\t{argument}" for argument in job["ProgramArguments"])
    environment = "\n".join(
        f"\t\t{key} => {value}" for key, value in job["EnvironmentVariables"].items()
    )
    return (
        f"gui/501/{setup.SERVICE_LABEL} = {{\n"
        f"\targuments = {{\n{arguments}\n\t}}\n"
        f"\tworking directory = {job['WorkingDirectory']}\n"
        f"\tenvironment = {{\n{environment}\n\t}}\n"
        "}\n"
    )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    """Keep saved configuration isolated and provide usable Node prerequisites."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("NETEASE_API_URL", raising=False)
    monkeypatch.setattr(setup, "_node_status", lambda: (True, "Node.js 和 npx 已就绪。"))
    monkeypatch.setattr(setup.sys, "platform", "darwin")
    monkeypatch.setattr(
        setup,
        "_launchctl",
        lambda *args: subprocess.CompletedProcess(args, 113 if args[0] == "print" else 0),
    )
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
    """Register a restarting launchd job and return once the API is ready."""
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:4567"})
    monkeypatch.setattr(setup, "_port_available", lambda port: True)
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: True)
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/opt/node/bin/" + name)
    calls = []

    def launchctl(*command):
        """Record native registration without modifying the desktop session."""
        calls.append(command)
        return subprocess.CompletedProcess(command, 113 if command[0] == "print" else 0)

    monkeypatch.setattr(setup, "_launchctl", launchctl)
    assert setup.run_service() == 0
    path = setup.config_directory() / "enhanced-service.plist"
    job = plistlib.loads(path.read_bytes())
    assert calls[-1] == ("bootstrap", f"gui/{setup.os.getuid()}", str(path.resolve()))
    assert job["ProgramArguments"] == [
        "/opt/node/bin/npx",
        "--yes",
        "@neteasecloudmusicapienhanced/api@4.41.1",
    ]
    assert job["KeepAlive"] is True
    assert job["ThrottleInterval"] == 5
    assert job["Umask"] == 0o077
    assert job["EnvironmentVariables"]["PORT"] == "4567"
    assert job["EnvironmentVariables"]["HOST"] == "127.0.0.1"
    assert job["EnvironmentVariables"]["ENABLE_GENERAL_UNBLOCK"] == "false"
    assert job["EnvironmentVariables"]["ENABLE_PROXY"] == "false"
    assert path.stat().st_mode & 0o777 == 0o600
    assert (path.parent / "enhanced-service.log").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("enhanced", [True, False])
def test_occupied_port_never_starts_child(environment, monkeypatch, enhanced):
    """Reject unmanaged listeners without silently claiming they are supervised."""
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:4567"})
    monkeypatch.setattr(setup, "_port_available", lambda port: False)
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: enhanced)
    monkeypatch.setattr(setup, "_write_service", lambda port: pytest.fail("occupied port"))
    assert setup.run_service() == 1


def test_repeat_start_does_not_register_second_job(environment, monkeypatch):
    """Reuse a healthy managed job without touching its process or configuration."""
    monkeypatch.setattr(
        setup,
        "_launchctl",
        lambda *args: subprocess.CompletedProcess(args, 0, loaded_job_output()),
    )
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: True)
    monkeypatch.setattr(setup, "_write_service", lambda port: pytest.fail("duplicate registration"))
    assert setup.run_service() == 0


@pytest.mark.parametrize("loaded", [True, False])
def test_stop_unregisters_only_managed_job(environment, monkeypatch, loaded):
    """Remove launchd ownership before stopping so the child will not restart."""
    calls = []

    def launchctl(*args):
        """Simulate a present or absent service and record native control calls."""
        calls.append(args)
        return subprocess.CompletedProcess(args, 113 if args[0] == "print" and not loaded else 0)

    monkeypatch.setattr(setup, "_launchctl", launchctl)
    assert setup.run_service("stop") == 0
    assert [call[0] for call in calls] == (["print", "bootout"] if loaded else ["print"])


@pytest.mark.parametrize("healthy", [True, False])
def test_status_distinguishes_registration_and_api_readiness(environment, monkeypatch, healthy):
    """Report API readiness separately from the registered restart policy."""
    monkeypatch.setattr(
        setup,
        "_launchctl",
        lambda *args: subprocess.CompletedProcess(args, 0, loaded_job_output()),
    )
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: healthy)
    assert setup.run_service("status") == (0 if healthy else 1)


def test_unready_service_reports_log_without_claiming_success(environment, monkeypatch, capsys):
    """Keep a slow-starting job registered but report that the API is not ready."""
    monkeypatch.setattr(
        setup,
        "_launchctl",
        lambda *args: subprocess.CompletedProcess(args, 0, loaded_job_output()),
    )
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: False)
    clock = iter([0, 31])
    monkeypatch.setattr(setup.time, "monotonic", lambda: next(clock))
    assert setup.run_service() == 1
    assert "尚未就绪" in capsys.readouterr().err


def test_registration_failure_is_reported(environment, monkeypatch, capsys):
    """Do not wait for an API after launchd rejects job registration."""
    monkeypatch.setattr(setup, "_port_available", lambda port: True)
    monkeypatch.setattr(setup, "_launchctl", lambda *args: subprocess.CompletedProcess(args, 1))
    assert setup.run_service() == 1
    assert "注册网易云后台服务失败" in capsys.readouterr().err


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


@pytest.mark.parametrize("change", ["port", "arguments", "environment", "directory"])
def test_start_replaces_changed_loaded_configuration(environment, monkeypatch, change):
    """Replace stale loaded settings even when the saved plist is already current."""
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/opt/node/bin/" + name)
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:4567"})
    path = setup._write_service(4567)
    desired = plistlib.loads(path.read_bytes())
    changes = {}
    old_port = 4567
    if change == "port":
        old_port = 3000
    elif change == "arguments":
        changes["ProgramArguments"] = ["/old/bin/npx", "--yes", "old-package"]
    elif change == "environment":
        changes["EnvironmentVariables"] = {
            **desired["EnvironmentVariables"],
            "ENABLE_PROXY": "true",
        }
    else:
        changes["WorkingDirectory"] = "/old/config"
    calls = []
    loaded = True

    def launchctl(*command):
        """Model launchd's loaded job separately from the mutable plist file."""
        nonlocal loaded
        calls.append(command)
        if command[0] == "print":
            return subprocess.CompletedProcess(command, 0, loaded_job_output(old_port, **changes))
        if command[0] == "bootout":
            loaded = False
        elif command[0] == "bootstrap":
            loaded = True
            assert plistlib.loads(path.read_bytes()) == desired
        return subprocess.CompletedProcess(command, 0)

    def port_available(port):
        """Make the old service retain its port until bootout finishes."""
        assert port == 4567
        return not loaded

    monkeypatch.setattr(setup, "_launchctl", launchctl)
    monkeypatch.setattr(setup, "_port_available", port_available)
    monkeypatch.setattr(setup, "_is_enhanced", lambda url: loaded)
    assert setup.run_service() == 0
    target = f"gui/{setup.os.getuid()}/{setup.SERVICE_LABEL}"
    assert calls == [
        ("print", target),
        ("bootout", "--wait", target),
        ("bootstrap", f"gui/{setup.os.getuid()}", str(path.resolve())),
    ]


def test_changed_service_stop_failure_preserves_saved_job(environment, monkeypatch, capsys):
    """Do not replace the plist or register another job when stopping fails."""
    path = setup._write_service(3000)
    original = path.read_bytes()
    monkeypatch.setattr(setup, "load_enhanced_config", lambda: {"apiUrl": "http://127.0.0.1:4567"})
    calls = []

    def launchctl(*command):
        """Reject bootout while reporting an old loaded configuration."""
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 1 if command[0] == "bootout" else 0, loaded_job_output()
        )

    monkeypatch.setattr(setup, "_launchctl", launchctl)
    assert setup.run_service() == 1
    assert [call[0] for call in calls] == ["print", "bootout"]
    assert path.read_bytes() == original
    assert "停止旧服务失败" in capsys.readouterr().err


def test_unknown_loaded_configuration_requires_explicit_stop(environment, monkeypatch, capsys):
    """Avoid changing a running job when launchctl output cannot be recognized."""
    calls = []

    def launchctl(*command):
        """Supply an unsupported output format and record attempted operations."""
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "unknown format")

    monkeypatch.setattr(setup, "_launchctl", launchctl)
    assert setup.run_service() == 1
    assert [call[0] for call in calls] == ["print"]
    assert "无法读取已注册后台服务的运行配置" in capsys.readouterr().err
