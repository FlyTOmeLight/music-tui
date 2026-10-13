"""Verify offline detection and private official profile creation for the setup wizard."""

import base64
import json
import os
import stat
from importlib.metadata import version

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from music_tui import netease_setup as setup


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    """Prepare deterministic local defaults and isolated real configuration paths."""
    directory = tmp_path / "config"
    monkeypatch.setattr(setup, "config_directory", lambda: directory)
    monkeypatch.setattr("music_tui.netease_config.config_directory", lambda: directory)
    for variable in ("NETEASE_APP_ID", "NETEASE_PRIVATE_KEY_FILE", "NETEASE_DEVICE_FILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(setup, "_network_defaults", lambda: ("192.168.1.2", "wifi"))
    monkeypatch.setattr(setup, "_model", lambda: "LocalModel")
    target = tmp_path / "work"
    setup.prepare(target)
    return target, directory


@pytest.fixture
def rsa_key():
    """Generate a temporary RSA key instead of including any private key fixture."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def fill_credentials(monkeypatch, key):
    """Provide the required console inputs and assigned device values."""
    encoded = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setenv("SETUP_APP_ID", "TestApplication")
    monkeypatch.setenv("SETUP_PRIVATE_KEY", base64.b64encode(encoded).decode())
    for field in ("CHANNEL", "DEVICE_TYPE", "OS", "BRAND"):
        monkeypatch.setenv(f"SETUP_{field}", "test-assigned")


def test_prepare_automates_local_fields_and_limits_defaults(prepared):
    """Generate a valid stable identity and expose non-secret local device defaults."""
    target, _ = prepared
    defaults = json.loads((target / "defaults.json").read_text())
    assert len(defaults["deviceId"]) == 32
    assert defaults["deviceId"].isalnum()
    assert defaults["appVer"] == version("melody-tui")
    assert defaults["model"] == "LocalModel"
    assert defaults["clientIp"] == "192.168.1.2"
    assert defaults["netStatus"] == "wifi"
    assert defaults["channel"] == ""
    assert stat.S_IMODE((target / "defaults.json").stat().st_mode) == 0o600
    assert setup.default_value(target, "clientIp") == "192.168.1.2"
    with pytest.raises(ValueError):
        setup.default_value(target, "privateKey")


def test_save_reuses_profile_and_key_without_more_required_inputs(prepared, rsa_key, monkeypatch):
    """Save a private profile, then reuse account inputs and stable device identity."""
    target, directory = prepared
    fill_credentials(monkeypatch, rsa_key)
    original = json.loads((target / "defaults.json").read_text())["deviceId"]
    path = setup.save(target)
    profile = json.loads(path.read_text())
    device = json.loads((directory / "netease-device.json").read_text())
    assert device["deviceId"] == original
    assert profile["appId"] == "TestApplication"
    assert profile["privateKeyFile"] == str(directory / "netease-private-key.pem")
    for filename in ("netease.json", "netease-device.json", "netease-private-key.pem"):
        assert stat.S_IMODE((directory / filename).stat().st_mode) == 0o600
    loaded = serialization.load_pem_private_key(
        (directory / "netease-private-key.pem").read_bytes(), password=None
    )
    assert loaded.public_key().public_numbers() == rsa_key.public_key().public_numbers()
    assert not (target / "key-input").exists()
    for field in list(os.environ):
        if field.startswith("SETUP_"):
            monkeypatch.delenv(field)
    setup.prepare(target)
    assert setup.default_value(target, "appId") == "TestApplication"
    assert json.loads((target / "defaults.json").read_text())["deviceId"] == original
    assert setup.save(target) == path
    assert not list(directory.glob(".netease-setup-*"))


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("SETUP_PRIVATE_KEY", "bad-secret", "私钥无效"),
        ("SETUP_APP_ID", "", "appId"),
        ("SETUP_CHANNEL", "", "不完整"),
        ("SETUP_CLIENT_IP", "not-ip", "IP 地址"),
        ("SETUP_NET_STATUS", "ethernet", "实际网络"),
    ],
)
def test_invalid_inputs_preserve_existing_profile(
    prepared, rsa_key, monkeypatch, field, value, message
):
    """Reject bad inputs before modifying any existing final configuration file."""
    target, directory = prepared
    fill_credentials(monkeypatch, rsa_key)
    setup.save(target)
    previous = {path.name: path.read_bytes() for path in directory.iterdir()}
    monkeypatch.setenv(field, value)
    with pytest.raises(ValueError, match=message):
        setup.save(target)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == previous


def test_credential_check_accepts_pem_path_and_rejects_non_rsa(prepared, rsa_key, monkeypatch):
    """Validate a supplied key file independently before prompting for device inputs."""
    target, directory = prepared
    pem_path = target / "existing.pem"
    pem_path.write_bytes(
        rsa_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("SETUP_APP_ID", "TestApplication")
    monkeypatch.setenv("SETUP_PRIVATE_KEY", str(pem_path))
    assert setup.check_credentials(target)[0] == "TestApplication"
    assert not directory.exists()
    other = ec.generate_private_key(ec.SECP256R1())
    pem_path.write_bytes(
        other.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(ValueError, match="RSA 私钥"):
        setup.check_credentials(target)


def test_detect_macos_route_wifi_without_network_requests(monkeypatch):
    """Use local route and interface metadata to detect an active Wi-Fi address."""
    commands = {
        ("route", "-n", "get", "default"): " interface: en1",
        ("ipconfig", "getifaddr", "en1"): "192.168.10.2",
        ("networksetup", "-listallhardwareports"): "Hardware Port: Wi-Fi\nDevice: en1\nEthernet: x",
    }
    monkeypatch.setattr(setup.sys, "platform", "darwin")
    monkeypatch.setattr(setup, "_command", lambda *args: commands[args])
    assert setup._network_defaults() == ("192.168.10.2", "wifi")
    commands[("route", "-n", "get", "default")] = ""
    assert setup._network_defaults() == ("", "")


def test_corrupt_existing_configuration_is_not_replaced(prepared):
    """Stop early on corrupt saved JSON rather than silently discarding user inputs."""
    target, directory = prepared
    directory.mkdir()
    path = directory / "netease.json"
    path.write_text("invalid-json")
    with pytest.raises(ValueError, match="配置无法读取"):
        setup.prepare(target)
    assert path.read_text() == "invalid-json"


def test_prepare_reuses_manual_environment_configuration(prepared, monkeypatch):
    """Reuse an existing manually configured app, private key path, and device profile."""
    target, directory = prepared
    profile = target / "manual-device.json"
    profile.write_text(json.dumps({"deviceId": "ManualDevice", "channel": "assigned-channel"}))
    monkeypatch.setenv("NETEASE_APP_ID", "ManualApplication")
    monkeypatch.setenv("NETEASE_PRIVATE_KEY_FILE", str(target / "manual.pem"))
    monkeypatch.setenv("NETEASE_DEVICE_FILE", str(profile))
    setup.prepare(target)
    assert setup.default_value(target, "appId") == "ManualApplication"
    assert setup.default_value(target, "privateKeyFile") == str(target / "manual.pem")
    assert setup.default_value(target, "deviceId") == "ManualDevice"
    assert setup.default_value(target, "channel") == "assigned-channel"
    assert not directory.exists()


def test_missing_automatic_model_can_be_entered(prepared, rsa_key, monkeypatch):
    """Accept the two machine fields when automatic system detection is unavailable."""
    target, directory = prepared
    fill_credentials(monkeypatch, rsa_key)
    defaults = json.loads((target / "defaults.json").read_text())
    defaults["model"] = defaults["osVer"] = ""
    (target / "defaults.json").write_text(json.dumps(defaults))
    monkeypatch.setenv("SETUP_MODEL", "EnteredModel")
    monkeypatch.setenv("SETUP_OS_VER", "EnteredVersion")
    setup.save(target)
    saved = json.loads((directory / "netease-device.json").read_text())
    assert saved["model"] == "EnteredModel"
    assert saved["osVer"] == "EnteredVersion"


@pytest.mark.parametrize("encoding", [serialization.Encoding.PEM, serialization.Encoding.DER])
def test_pasted_private_key_file_is_validated_without_secret_environment(
    prepared, rsa_key, monkeypatch, encoding
):
    """Validate the complete long or multiline key written by the terminal reader."""
    target, _ = prepared
    monkeypatch.setenv("SETUP_APP_ID", "test-app")
    monkeypatch.delenv("SETUP_PRIVATE_KEY", raising=False)
    content = rsa_key.private_bytes(
        encoding, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    if encoding == serialization.Encoding.DER:
        content = base64.b64encode(content)
    (target / "private-key-input").write_bytes(content)
    app_id, pem = setup.check_credentials(target)
    restored = serialization.load_pem_private_key(pem, password=None)
    assert app_id == "test-app"
    assert restored.public_key().public_numbers() == rsa_key.public_key().public_numbers()
