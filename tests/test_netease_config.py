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
    monkeypatch.setattr(netease_config, "run_setup", lambda backend: 0)
    monkeypatch.setattr("sys.argv", ["music-tui", "--setup-netease"])
    with pytest.raises(SystemExit) as result:
        app.main()
    assert result.value.code == 0


def test_saved_enhanced_address_is_independent_of_official_profile(configuration):
    """Read a saved third-party port while preserving unrelated official credentials."""
    from music_tui.netease_config import load_enhanced_config

    assert load_enhanced_config() == {"apiUrl": "http://127.0.0.1:3000"}
    path = config_directory() / "enhanced.json"
    path.write_text(json.dumps({"apiUrl": "http://127.0.0.1:49173"}))
    assert load_enhanced_config()["apiUrl"] == "http://127.0.0.1:49173"
    assert load_official_config() == configuration


@pytest.mark.parametrize("backend", ["enhanced", "official"])
def test_setup_routes_requested_backend(monkeypatch, backend):
    """Select the third-party guide by default and the official guide explicitly."""
    from music_tui import netease_config

    requested = []

    def launch(selected):
        """Record the requested guide without spawning interactive processes."""
        requested.append(selected)
        return 0

    monkeypatch.setattr(netease_config, "run_setup", launch)
    args = ["music-tui", "--setup-netease"]
    if backend == "official":
        args.extend(["--netease-backend", "official"])
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit):
        app.main()
    assert requested == [backend]


def test_service_option_starts_service_without_music_interface(monkeypatch):
    """Invoke the foreground service runner separately from the player."""
    from music_tui import netease_enhanced_setup

    def unexpected_run(*args):
        """Reject player initialization when the service flag is used."""
        pytest.fail("service command started the player")

    monkeypatch.setattr(app, "run", unexpected_run)
    monkeypatch.setattr(netease_enhanced_setup, "run_service", lambda: 7)
    monkeypatch.setattr("sys.argv", ["music-tui", "--netease-service"])
    with pytest.raises(SystemExit) as result:
        app.main()
    assert result.value.code == 7


@pytest.mark.parametrize("override", [None, "http://localhost:4000"])
async def test_default_netease_start_reads_saved_enhanced_address(
    configuration, monkeypatch, override
):
    """Use the third-party profile by default and honor an explicit command-line address."""
    from music_tui.sources import netease

    configured = "http://127.0.0.1:49173"
    (config_directory() / "enhanced.json").write_text(json.dumps({"apiUrl": configured}))
    recorded = []

    class Music:
        def __init__(self, directory, *, api_url):
            """Record the selected third-party address without real network requests."""
            recorded.append(api_url)

        async def close(self):
            """Release the synthetic music source."""

    class Player:
        async def close(self):
            """Release the synthetic player."""

    class Interface:
        def __init__(self, music, player, **configuration):
            """Keep source ownership while verifying the default source key."""
            self.music = music
            assert configuration["source_key"] == "netease-enhanced"

        async def run_async(self):
            """Finish without running Textual or launching mpv."""

    monkeypatch.setattr(netease, "NetEaseMusic", Music)
    monkeypatch.setattr(app, "Player", Player)
    monkeypatch.setattr(app, "MusicApp", Interface)
    await app.run("netease", override)
    assert recorded == [override or configured]
