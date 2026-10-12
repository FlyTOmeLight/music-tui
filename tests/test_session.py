"""Verify private playback state persistence and account isolation."""

import json
import stat

import pytest

from music_tui import session
from music_tui.session import SessionStore


def test_missing_state_and_anonymous_session(tmp_path):
    """A first start and anonymous playback do not create local state files."""
    directory = tmp_path / "state"
    store = SessionStore(directory)
    assert store.last_source() is None
    assert store.load_preferences("qqmusic") == {}
    assert store.load_session("qqmusic", "account") is None
    assert store.load_session("qqmusic", "") is None
    store.save_session("qqmusic", "", {"position": 10})
    assert not directory.exists()


def test_preferences_and_last_source_roundtrip(tmp_path):
    """Preferences survive a restart and retain each platform's independent settings."""
    store = SessionStore(tmp_path)
    qq = {"quality": "high", "repeat_mode": "random", "volume": 75}
    netease = {"quality": "standard", "repeat_mode": "list", "volume": 40}
    store.save_preferences("qqmusic", qq)
    store.set_last_source("netease")
    store.save_preferences("netease", netease)
    restored = SessionStore(tmp_path)
    assert restored.last_source() == "netease"
    assert restored.load_preferences("qqmusic") == qq
    assert restored.load_preferences("netease") == netease
    assert restored.load_preferences("unknown") == {}


def test_session_platform_and_account_isolation(tmp_path):
    """Identical accounts on different platforms and path-like IDs remain isolated."""
    directory = tmp_path / "state"
    store = SessionStore(directory)
    sessions = [
        ("qqmusic", "account-a", {"position": 10, "queue": [{"mid": "one"}]}),
        ("qqmusic", "account-b", {"position": 20}),
        ("netease", "account-a", {"position": 30}),
        ("../platform", "../../account", {"position": 40}),
    ]
    for source, account, snapshot in sessions:
        store.save_session(source, account, snapshot)
    restored = SessionStore(directory)
    for source, account, snapshot in sessions:
        assert restored.load_session(source, account) == snapshot
    assert restored.load_session("netease", "account-b") is None
    store.save_session("qqmusic", "account-a", {"position": 50})
    assert restored.load_session("qqmusic", "account-a") == {"position": 50}
    assert restored.load_session("qqmusic", "account-b") == {"position": 20}
    assert list(tmp_path.iterdir()) == [directory]
    assert [path.name for path in directory.iterdir()] == ["sessions.json"]


@pytest.mark.parametrize("name", ["preferences.json", "sessions.json"])
def test_state_file_permissions_on_replacement(tmp_path, name):
    """Initial saves and replacements leave private files and no temporary files."""
    directory = tmp_path / "state"
    store = SessionStore(directory)
    for volume in (50, 60):
        if name == "preferences.json":
            store.save_preferences("qqmusic", {"volume": volume})
        else:
            store.save_session("qqmusic", "account", {"position": volume})
        assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert [path.name for path in directory.iterdir()] == [name]
        (directory / name).chmod(0o644)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("preferences.json", "{invalid"),
        ("preferences.json", "[]"),
        ("preferences.json", '{"preferences": {"qqmusic": []}}'),
        ("preferences.json", '{"last_source": null}'),
        ("sessions.json", "{invalid"),
        ("sessions.json", "[]"),
        ("sessions.json", '{"qqmusic": []}'),
        ("sessions.json", '{"qqmusic": {"account": []}}'),
    ],
)
def test_corrupt_state_is_reported_and_preserved(tmp_path, name, content):
    """Damaged existing state raises a useful error and is never silently overwritten."""
    path = tmp_path / name
    path.write_text(content)
    store = SessionStore(tmp_path)
    if name == "preferences.json":
        with pytest.raises(ValueError, match=name):
            store.load_preferences("qqmusic")
        with pytest.raises(ValueError, match=name):
            store.save_preferences("qqmusic", {"volume": 50})
        with pytest.raises(ValueError, match=name):
            store.set_last_source("qqmusic")
    else:
        with pytest.raises(ValueError, match=name):
            store.load_session("qqmusic", "account")
        with pytest.raises(ValueError, match=name):
            store.save_session("qqmusic", "account", {"position": 10})
    assert path.read_text() == content


def test_unserializable_snapshot_preserves_previous_session(tmp_path):
    """A rejected new snapshot keeps the last valid saved session intact."""
    store = SessionStore(tmp_path)
    store.save_session("qqmusic", "account", {"position": 10})
    with pytest.raises(TypeError):
        store.save_session("qqmusic", "account", {"queue": object()})
    assert store.load_session("qqmusic", "account") == {"position": 10}
    assert json.loads((tmp_path / "sessions.json").read_text()) == {
        "qqmusic": {"account": {"position": 10}}
    }


def test_failed_replacement_preserves_session_and_cleans_temporary_file(tmp_path, monkeypatch):
    """An atomic replacement failure preserves saved data and removes the temporary file."""
    store = SessionStore(tmp_path)
    store.save_session("qqmusic", "account", {"position": 10})

    def reject_replacement(source, destination):
        """Simulate the filesystem refusing to replace the existing session file."""
        raise OSError("replacement failed")

    monkeypatch.setattr(session.os, "replace", reject_replacement)
    with pytest.raises(OSError, match="replacement failed"):
        store.save_session("qqmusic", "account", {"position": 20})
    assert store.load_session("qqmusic", "account") == {"position": 10}
    assert [path.name for path in tmp_path.iterdir()] == ["sessions.json"]
