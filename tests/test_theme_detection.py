"""Verify native appearance preferences and bounded asynchronous detection."""

import asyncio
import plistlib
import sys

import pytest

from music_tui import themes


@pytest.mark.parametrize(
    ("preferences", "expected"),
    [({}, "music-day"), ({"AppleInterfaceStyle": "Dark"}, "music-night")],
)
async def test_macos_appearance_uses_native_global_preferences(monkeypatch, preferences, expected):
    """Treat the missing light-mode preference as day and recognize system dark mode."""
    monkeypatch.setattr(themes.sys, "platform", "darwin")

    async def read_preferences(*command):
        """Return native plist output without accessing the user's actual settings."""
        assert command == ("/usr/bin/defaults", "export", "-g", "-")
        return plistlib.dumps(preferences)

    monkeypatch.setattr(themes, "_theme_command", read_preferences)
    assert await themes.detect_system_theme() == expected


@pytest.mark.parametrize(
    "output",
    [None, b"invalid plist", plistlib.dumps([]), plistlib.dumps({"AppleInterfaceStyle": 7})],
)
async def test_macos_unreadable_preference_is_reported_as_unknown(monkeypatch, output):
    """Do not claim to follow a system whose preference is unavailable or malformed."""
    monkeypatch.setattr(themes.sys, "platform", "darwin")

    async def read_preferences(*command):
        """Return failed or malformed command output."""
        return output

    monkeypatch.setattr(themes, "_theme_command", read_preferences)
    assert await themes.detect_system_theme() is None


@pytest.mark.parametrize(
    ("scheme", "gtk_theme", "expected"),
    [
        (b"'prefer-dark'\n", None, "music-night"),
        (b"'prefer-light'\n", None, "music-day"),
        (b"'default'\n", b"'Adwaita'\n", "music-day"),
        (None, b"'Adwaita-dark'\n", "music-night"),
        (None, None, None),
    ],
)
async def test_linux_appearance_reads_available_desktop_preferences(
    monkeypatch, scheme, gtk_theme, expected
):
    """Use explicit GNOME color-scheme settings before its GTK theme preference."""
    monkeypatch.setattr(themes.sys, "platform", "linux")

    async def read_preferences(*command):
        """Return the requested desktop preference from controlled offline values."""
        return scheme if command[-1] == "color-scheme" else gtk_theme

    monkeypatch.setattr(themes, "_theme_command", read_preferences)
    assert await themes.detect_system_theme() == expected


async def test_unsupported_platform_does_not_claim_system_detection(monkeypatch):
    """Return an unavailable result on platforms without an implemented appearance API."""
    monkeypatch.setattr(themes.sys, "platform", "unknown")
    assert await themes.detect_system_theme() is None


@pytest.mark.parametrize("cancel", [False, True])
async def test_theme_command_timeout_and_cancellation_reap_the_process(monkeypatch, cancel):
    """Keep the event loop responsive and reap detection commands on timeout or shutdown."""
    create_process = asyncio.create_subprocess_exec
    created = asyncio.Event()
    process = None

    async def start_process(*command, **kwargs):
        """Start a real slow command to exercise timeout and cancellation cleanup."""
        nonlocal process
        process = await create_process(
            sys.executable, "-c", "import time; time.sleep(30)", **kwargs
        )
        created.set()
        return process

    monkeypatch.setattr(themes.asyncio, "create_subprocess_exec", start_process)
    monkeypatch.setattr(themes, "SYSTEM_THEME_TIMEOUT", 0.1)
    task = asyncio.create_task(themes._theme_command("slow-detection"))
    async with asyncio.timeout(2):
        await created.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
    assert process.returncode is not None


async def test_missing_native_command_is_unavailable(monkeypatch):
    """Handle a missing desktop command without turning it into a light or dark setting."""
    monkeypatch.setattr(themes.sys, "platform", "linux")

    async def missing_command(*command):
        """Simulate a Linux desktop without gsettings installed."""
        raise FileNotFoundError("gsettings")

    monkeypatch.setattr(themes, "_theme_command", missing_command)
    assert await themes.detect_system_theme() is None
