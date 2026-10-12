"""Verify setup configuration is consumed by startup without shell exports."""

import json

import pytest

from music_tui import app
from music_tui.netease_config import config_directory, load_official_config


@pytest.fixture
def configuration(monkeypatch, tmp_path):
    """Isolate saved settings and remove all external official configuration."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for variable in ("NETEASE_APP_ID", "NETEASE_PRIVATE_KEY_FILE", "NETEASE_DEVICE_FILE"):
        monkeypatch.delenv(variable, raising=False)
    directory = config_directory()
    directory.mkdir()
    saved = {
        "appId": "saved-app",
        "privateKeyFile": str(directory / "key.pem"),
        "deviceFile": str(directory / "device.json"),
    }
    (directory / "netease.json").write_text(json.dumps(saved))
    return saved


def test_saved_configuration_and_individual_environment_override(configuration, monkeypatch):
    """Read the persisted profile and override only explicitly supplied fields."""
    assert load_official_config() == configuration
    monkeypatch.setenv("NETEASE_APP_ID", "other-app")
    assert load_official_config() == configuration | {"appId": "other-app"}


@pytest.mark.parametrize("content", ["broken", "[]"])
def test_invalid_saved_configuration_has_actionable_error(configuration, content):
    """Point users to the wizard without exposing malformed file contents."""
    (config_directory() / "netease.json").write_text(content)
    with pytest.raises(ValueError, match="--setup-netease"):
        load_official_config()


def test_setup_option_runs_wizard_without_starting_music(monkeypatch):
    """Route setup separately from Textual and propagate the wizard exit status."""
    from music_tui import netease_config

    def unexpected_run(*args):
        """Prevent player or music initialization during configuration."""
        pytest.fail("setup started the music interface")

    monkeypatch.setattr(app, "run", unexpected_run)
    monkeypatch.setattr(netease_config, "run_setup", lambda: 0)
    monkeypatch.setattr("sys.argv", ["music-tui", "--setup-netease"])
    with pytest.raises(SystemExit) as result:
        app.main()
    assert result.value.code == 0
