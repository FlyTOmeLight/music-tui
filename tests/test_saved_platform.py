"""Verify saved platform selection and cleanup without accessing music accounts."""

import pytest

from music_tui import app
from music_tui.session import SessionStore
from music_tui.sources import netease, netease_official


@pytest.fixture
def startup(monkeypatch, tmp_path):
    """Replace runtime dependencies and isolate persisted source selection."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    state = {"created": [], "closed": [], "selected": [], "failure": None}

    class Music:
        def __init__(self, key):
            """Track ownership of one synthetic platform resource."""
            self.key = key

        async def close(self):
            """Record cleanup of the selected platform resource."""
            state["closed"].append(self.key)

    def source_factory(key):
        """Build a source constructor which records selection without using the network."""

        def create(directory, **configuration):
            """Record the backend and its private data directory."""
            state["created"].append((key, directory))
            return Music(key)

        return create

    class Player:
        async def close(self):
            """Record cleanup without starting a real mpv process."""
            state["closed"].append("player")

    class Interface:
        def __init__(self, music, player, **configuration):
            """Record the selected source or fail during application construction."""
            self.music = music
            state["selected"].append(configuration["source_key"])
            assert configuration["source_key"] == music.key
            assert configuration["session_store"].directory == tmp_path / "music-tui"
            if state["failure"] == "construct":
                raise ValueError("interface construction failed")

        async def run_async(self):
            """Finish normally or reproduce an interface startup failure."""
            if state["failure"] == "run":
                raise RuntimeError("interface startup failed")

    def official_config():
        """Provide synthetic official settings without reading real credentials."""
        return {"appId": "test-app"}

    def enhanced_config():
        """Provide an unused local address without reading real configuration."""
        return {"apiUrl": "http://127.0.0.1:3999"}

    monkeypatch.setattr(app, "QQMusic", source_factory("qq"))
    monkeypatch.setattr(netease, "NetEaseMusic", source_factory("netease-enhanced"))
    monkeypatch.setattr(
        netease_official, "OfficialNetEaseMusic", source_factory("netease-official")
    )
    monkeypatch.setattr(app, "load_official_config", official_config)
    monkeypatch.setattr(app, "load_enhanced_config", enhanced_config)
    monkeypatch.setattr(app, "Player", Player)
    monkeypatch.setattr(app, "MusicApp", Interface)
    return state


@pytest.mark.parametrize(
    ("saved", "source", "backend", "expected", "relative_directory"),
    [
        (None, None, "enhanced", "qq", "qqmusic-tui"),
        ("qq", None, "enhanced", "qq", "qqmusic-tui"),
        ("netease-enhanced", None, "enhanced", "netease-enhanced", "music-tui/netease"),
        (
            "netease-official",
            None,
            "enhanced",
            "netease-official",
            "music-tui/netease-official",
        ),
        ("netease-official", "qq", "enhanced", "qq", "qqmusic-tui"),
        ("netease-official", "netease", "enhanced", "netease-enhanced", "music-tui/netease"),
        ("qq", "netease", "official", "netease-official", "music-tui/netease-official"),
    ],
)
async def test_run_selects_saved_platform_unless_explicitly_overridden(
    startup, tmp_path, saved, source, backend, expected, relative_directory
):
    """Choose QQ on first start, remember each backend, and prefer explicit selection."""
    if saved is not None:
        SessionStore(tmp_path / "music-tui").set_last_source(saved)
    if backend == "enhanced":
        await app.run(source)
    else:
        await app.run(source, netease_backend=backend)
    assert startup["selected"] == [expected]
    assert startup["created"] == [(expected, tmp_path / relative_directory)]
    assert startup["closed"] == ["player", expected]


@pytest.mark.parametrize(
    ("stage", "exception", "message"),
    [
        ("construct", ValueError, "interface construction failed"),
        ("run", RuntimeError, "interface startup failed"),
    ],
)
async def test_saved_platform_startup_failure_closes_owned_resources(
    startup, tmp_path, stage, exception, message
):
    """Release player and remembered source when construction or startup fails."""
    SessionStore(tmp_path / "music-tui").set_last_source("netease-official")
    startup["failure"] = stage
    with pytest.raises(exception, match=message):
        await app.run()
    assert startup["selected"] == ["netease-official"]
    assert startup["closed"] == ["player", "netease-official"]
