"""Command-line platform selection and resource ownership tests."""

import importlib
from pathlib import Path

import pytest

from music_tui import app


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ([], ("qq", "http://127.0.0.1:3000")),
        (["--source", "netease"], ("netease", "http://127.0.0.1:3000")),
        (
            ["--source", "netease", "--netease-api-url", "http://localhost:4000"],
            ("netease", "http://localhost:4000"),
        ),
    ],
)
def test_main_selects_requested_platform(monkeypatch, arguments, expected):
    """Use QQ by default and forward an explicit NetEase selection to startup."""
    selected = []

    async def run(source, api_url):
        """Record the platform passed to asynchronous application startup."""
        selected.append((source, api_url))

    monkeypatch.setattr(app, "run", run)
    monkeypatch.setattr("sys.argv", ["music-tui", *arguments])
    app.main()
    assert selected == [expected]


def test_main_rejects_unknown_platform(monkeypatch):
    """Reject an unsupported platform before initializing any runtime resources."""
    monkeypatch.setattr("sys.argv", ["music-tui", "--source", "unknown"])
    with pytest.raises(SystemExit) as error:
        app.main()
    assert error.value.code == 2


@pytest.mark.parametrize(
    "source, relative_directory",
    [("qq", "qqmusic-tui"), ("netease", "music-tui/netease")],
)
async def test_run_uses_platform_directory_and_closes_resources(
    monkeypatch, tmp_path, source, relative_directory
):
    """Keep platform accounts separate and release music and player resources on failure."""
    created = []
    closed = []

    class Music:
        def __init__(self, directory, *, api_url=None):
            """Record the account directory without accessing a real music platform."""
            created.append((Path(directory), api_url))

        async def close(self):
            """Record release of the selected music source."""
            closed.append("music")

    class Player:
        async def close(self):
            """Record release of the audio player."""
            closed.append("player")

    class Interface:
        def __init__(self, music, player):
            """Accept the runtime dependencies used by the interface."""
            assert isinstance(music, Music)
            assert isinstance(player, Player)

        async def run_async(self):
            """Fail startup to verify cleanup also runs when the interface exits abnormally."""
            raise RuntimeError("interface failed")

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(app, "QQMusic", Music)
    if source == "netease":
        netease = importlib.import_module("music_tui.sources.netease")
        monkeypatch.setattr(netease, "NetEaseMusic", Music)
    monkeypatch.setattr(app, "Player", Player)
    monkeypatch.setattr(app, "MusicApp", Interface)
    with pytest.raises(RuntimeError, match="interface failed"):
        await app.run(source, "http://localhost:4000")
    assert created == [
        (tmp_path / relative_directory, "http://localhost:4000" if source == "netease" else None)
    ]
    assert closed == ["player", "music"]
