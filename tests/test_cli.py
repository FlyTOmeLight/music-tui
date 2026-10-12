"""Command-line platform selection and resource ownership tests."""

import importlib
from pathlib import Path

import pytest

from music_tui import app


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ([], (None, None, "enhanced")),
        (["--source", "qq"], ("qq", None, "enhanced")),
        (["--source", "netease"], ("netease", None, "enhanced")),
        (
            [
                "--source",
                "netease",
                "--netease-backend",
                "enhanced",
                "--netease-api-url",
                "http://localhost:4000",
            ],
            ("netease", "http://localhost:4000", "enhanced"),
        ),
    ],
)
def test_main_selects_requested_platform(monkeypatch, arguments, expected):
    """Leave implicit platform selection to saved state and honor explicit CLI choices."""
    selected = []

    async def run(source, api_url, backend):
        """Record the platform and backend passed to application startup."""
        selected.append((source, api_url, backend))

    monkeypatch.setattr(app, "run", run)
    monkeypatch.setattr("sys.argv", ["music-tui", *arguments])
    app.main()
    assert selected == [expected]


@pytest.mark.parametrize("option", ["--source", "--netease-backend"])
def test_main_rejects_unknown_selection(monkeypatch, option):
    """Reject unsupported platforms and backends before runtime initialization."""
    monkeypatch.setattr("sys.argv", ["music-tui", option, "unknown"])
    with pytest.raises(SystemExit) as error:
        app.main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    "source, backend, relative_directory",
    [
        ("qq", "official", "qqmusic-tui"),
        ("netease", "official", "music-tui/netease-official"),
        ("netease", "enhanced", "music-tui/netease"),
    ],
)
async def test_run_uses_platform_directory_and_closes_resources(
    monkeypatch, tmp_path, source, backend, relative_directory
):
    """Keep backend accounts separate and release resources after interface failure."""
    created = []
    closed = []

    class Music:
        def __init__(self, directory, **configuration):
            """Record source configuration without accessing a real music platform."""
            created.append((Path(directory), configuration))

        async def close(self):
            """Record release of the selected music source."""
            closed.append("music")

    class Player:
        async def close(self):
            """Record release of the audio player."""
            closed.append("player")

    class Interface:
        def __init__(self, music, player, **configuration):
            """Accept the runtime dependencies used by the interface."""
            self.music = music
            assert isinstance(music, Music)
            assert isinstance(player, Player)

        async def run_async(self):
            """Fail startup to verify cleanup on abnormal interface exit."""
            raise RuntimeError("interface failed")

    def unexpected_source(*args, **kwargs):
        """Reject initialization of any unselected music backend."""
        pytest.fail("initialized an unselected music backend")

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("NETEASE_APP_ID", "test-app")
    monkeypatch.setenv("NETEASE_PRIVATE_KEY_FILE", str(tmp_path / "key.pem"))
    monkeypatch.setenv("NETEASE_DEVICE_FILE", str(tmp_path / "device.json"))
    monkeypatch.setattr(app, "QQMusic", Music if source == "qq" else unexpected_source)
    enhanced = importlib.import_module("music_tui.sources.netease")
    official = importlib.import_module("music_tui.sources.netease_official")
    monkeypatch.setattr(
        enhanced,
        "NetEaseMusic",
        Music if source == "netease" and backend == "enhanced" else unexpected_source,
    )
    monkeypatch.setattr(
        official,
        "OfficialNetEaseMusic",
        Music if source == "netease" and backend == "official" else unexpected_source,
    )
    monkeypatch.setattr(app, "Player", Player)
    monkeypatch.setattr(app, "MusicApp", Interface)
    with pytest.raises(RuntimeError, match="interface failed"):
        await app.run(source, "http://localhost:4000", backend)
    configuration = {}
    if source == "netease":
        configuration = (
            {
                "app_id": "test-app",
                "private_key_path": tmp_path / "key.pem",
                "device_file": tmp_path / "device.json",
            }
            if backend == "official"
            else {"api_url": "http://localhost:4000"}
        )
    assert created == [(tmp_path / relative_directory, configuration)]
    assert closed == ["player", "music"]


def test_missing_official_credentials_reports_error_without_fallback(monkeypatch, capsys, tmp_path):
    """Explain missing official credentials without silently selecting Enhanced."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    enhanced = importlib.import_module("music_tui.sources.netease")

    def unexpected_enhanced(*args, **kwargs):
        """Fail if configuration errors silently switch account backends."""
        pytest.fail("official configuration failure selected Enhanced")

    monkeypatch.delenv("NETEASE_APP_ID", raising=False)
    monkeypatch.delenv("NETEASE_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.delenv("NETEASE_DEVICE_FILE", raising=False)
    monkeypatch.setattr(enhanced, "NetEaseMusic", unexpected_enhanced)
    monkeypatch.setattr(
        "sys.argv", ["music-tui", "--source", "netease", "--netease-backend", "official"]
    )
    with pytest.raises(SystemExit) as error:
        app.main()
    assert error.value.code == 2
    message = capsys.readouterr().err
    assert "NETEASE_APP_ID" in message
    assert "enhanced" in message
    assert "Traceback" not in message
