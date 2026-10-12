"""Verify credential serialization and private filesystem permissions."""

import stat

from qqmusic_api import Credential

from music_tui.sources.qqmusic import save_credential


def test_credential_roundtrip_and_private_permissions(tmp_path):
    """Verify saved credentials round-trip and replacement preserves private permissions."""
    path = tmp_path / "account" / "credential.json"
    credential = Credential(musicid=123, musickey="test", encryptUin="test-euin")
    save_credential(path, credential)
    assert Credential.model_validate_json(path.read_text()) == credential
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    save_credential(path, Credential(musicid=456, musickey="new"))
    assert Credential.model_validate_json(path.read_text()).musicid == 456
    assert len(list(path.parent.iterdir())) == 1
