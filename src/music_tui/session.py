"""Persist local playback preferences and account-scoped session snapshots."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


class SessionStore:
    """Save platform preferences and playback snapshots as private JSON files."""

    def __init__(self, directory: Path):
        """Use the supplied directory without creating files until a state is saved."""
        self.directory = directory

    def _read(self, name: str) -> dict:
        """Read an optional JSON object and reject unreadable or corrupt state."""
        path = self.directory / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise ValueError(f"无法读取播放状态文件：{path}") from error
        if not isinstance(value, dict):
            raise ValueError(f"播放状态文件必须是 JSON 对象：{path}")
        return value

    def _write(self, name: str, value: dict) -> None:
        """Atomically replace a state file with owner-only read and write access."""
        content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=self.directory, prefix=".session-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, self.directory / name)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _preferences(self) -> dict:
        """Read preferences and validate the platform mapping and saved source."""
        value = self._read("preferences.json")
        preferences = value.get("preferences", {})
        if (
            not isinstance(preferences, dict)
            or any(not isinstance(item, dict) for item in preferences.values())
            or ("last_source" in value and not isinstance(value["last_source"], str))
        ):
            raise ValueError(f"播放偏好文件格式无效：{self.directory / 'preferences.json'}")
        return value

    def load_preferences(self, source_key: str) -> dict:
        """Return the saved platform preferences, or an empty mapping on first use."""
        return self._preferences().get("preferences", {}).get(source_key, {})

    def save_preferences(self, source_key: str, preferences: dict) -> None:
        """Replace one platform's preferences while retaining other platform settings."""
        value = self._preferences()
        value.setdefault("preferences", {})[source_key] = preferences
        self._write("preferences.json", value)

    def last_source(self) -> str | None:
        """Return the last selected platform when it has been saved."""
        return self._preferences().get("last_source")

    def set_last_source(self, source_key: str) -> None:
        """Save the selected platform without changing its playback preferences."""
        value = self._preferences()
        value["last_source"] = source_key
        self._write("preferences.json", value)

    def theme(self) -> str:
        """Return the global appearance preference, following the system on first use."""
        theme = self._preferences().get("theme", "system")
        if theme not in ("system", "music-night", "music-day", "music-glass"):
            raise ValueError("保存的界面主题无效")
        return theme

    def save_theme(self, theme: str) -> None:
        """Save appearance independently from platform and account preferences."""
        if theme not in ("system", "music-night", "music-day", "music-glass"):
            raise ValueError("界面主题无效")
        value = self._preferences()
        value["theme"] = theme
        self._write("preferences.json", value)

    def _sessions(self) -> dict:
        """Read sessions and validate platform, account, and snapshot mappings."""
        value = self._read("sessions.json")
        if any(
            not isinstance(accounts, dict)
            or any(not isinstance(snapshot, dict) for snapshot in accounts.values())
            for accounts in value.values()
        ):
            raise ValueError(f"播放会话文件格式无效：{self.directory / 'sessions.json'}")
        return value

    def load_session(self, source_key: str, account_id: str) -> dict | None:
        """Return only this account's platform session; anonymous playback has none."""
        if not account_id:
            return None
        return self._sessions().get(source_key, {}).get(account_id)

    def save_session(self, source_key: str, account_id: str, snapshot: dict) -> None:
        """Replace this account's snapshot; skip saving anonymous playback."""
        if not account_id:
            return
        value = self._sessions()
        value.setdefault(source_key, {})[account_id] = snapshot
        self._write("sessions.json", value)

    @staticmethod
    def _valid_history_entry(record: dict) -> bool:
        """Check the metadata needed to display and reopen a recent playback record."""
        return (
            isinstance(record, dict)
            and isinstance(record.get("track"), dict)
            and isinstance(record["track"].get("mid"), str)
            and bool(record["track"]["mid"])
            and "source" in record
            and isinstance(record["source"], dict | str | type(None))
            and type(record.get("page")) is int
            and record["page"] > 0
            and isinstance(record.get("played_at"), str)
        )

    def _history(self) -> dict:
        """Load account-scoped histories and reject malformed existing records."""
        value = self._read("history.json")
        if any(
            not isinstance(accounts, dict)
            or any(
                not isinstance(records, list)
                or any(not self._valid_history_entry(record) for record in records)
                for records in accounts.values()
            )
            for accounts in value.values()
        ):
            raise ValueError(f"最近播放文件格式无效：{self.directory / 'history.json'}")
        return value

    def history(self, source_key: str, account_id: str) -> list[dict]:
        """Return this platform account's most recent songs, newest first."""
        if not account_id:
            return []
        return self._history().get(source_key, {}).get(account_id, [])

    def record_play(self, source_key: str, account_id: str, record: dict) -> None:
        """Move a played song to the front with a UTC timestamp and keep at most 100 songs."""
        if not account_id:
            return
        entry = {
            **record,
            "played_at": datetime.now(UTC).isoformat(),
        }
        if not self._valid_history_entry(entry):
            raise ValueError("最近播放记录格式无效")
        entry = {key: entry[key] for key in ("track", "source", "page", "played_at")}
        value = self._history()
        accounts = value.setdefault(source_key, {})
        records = accounts.get(account_id, [])
        accounts[account_id] = [entry] + [
            previous for previous in records if previous["track"]["mid"] != entry["track"]["mid"]
        ][:99]
        self._write("history.json", value)
