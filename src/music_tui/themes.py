"""Shared palettes and asynchronous operating-system appearance detection."""

import asyncio
import plistlib
import sys

from textual.theme import Theme

SYSTEM_THEME_TIMEOUT = 1.0

NIGHT = Theme(
    name="music-night",
    primary="#f2c879",
    accent="#f2c879",
    secondary="#a5b4cc",
    foreground="#e8edf7",
    background="#101827",
    surface="#19253a",
    panel="#19253a",
    dark=True,
    variables={
        "text-muted": "#a5b4cc",
        "border": "#34445e",
        "border-blurred": "#34445e",
        "block-cursor-background": "#30405a",
        "block-cursor-foreground": "#e8edf7",
        "input-cursor-background": "#f2c879",
        "input-cursor-foreground": "#101827",
        "input-selection-background": "#30405a",
        "footer-key-foreground": "#f2c879",
        "music-row": "#141f31",
    },
)

DAY = Theme(
    name="music-day",
    primary="#805100",
    accent="#805100",
    secondary="#52637b",
    foreground="#1b2a40",
    background="#f4f6fa",
    surface="#e6ecf5",
    panel="#e6ecf5",
    dark=False,
    variables={
        "text-muted": "#52637b",
        "border": "#b2bfd1",
        "border-blurred": "#b2bfd1",
        "block-cursor-background": "#ccdaed",
        "block-cursor-foreground": "#1b2a40",
        "input-cursor-background": "#805100",
        "input-cursor-foreground": "#ffffff",
        "input-selection-background": "#ccdaed",
        "footer-key-foreground": "#805100",
        "music-row": "#edf1f7",
    },
)


async def _theme_command(*command: str) -> bytes | None:
    """Read a native preference command without blocking or leaving timed-out processes."""
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        async with asyncio.timeout(SYSTEM_THEME_TIMEOUT):
            output, _ = await process.communicate()
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        raise
    return output if process.returncode == 0 else None


def _windows_theme() -> str | None:
    """Read the Windows app appearance preference from the current user's registry."""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except OSError:
        return None
    return DAY.name if light else NIGHT.name


async def detect_system_theme() -> str | None:
    """Detect macOS, Windows, or GNOME appearance; return None when unavailable."""
    try:
        if sys.platform == "darwin":
            output = await _theme_command("/usr/bin/defaults", "export", "-g", "-")
            if output is None:
                return None
            preferences = plistlib.loads(output)
            if not isinstance(preferences, dict):
                return None
            style = preferences.get("AppleInterfaceStyle", "Light")
            if style in ("Light", "Dark"):
                return NIGHT.name if style == "Dark" else DAY.name
            return None
        if sys.platform == "win32":
            return await asyncio.to_thread(_windows_theme)
        if sys.platform.startswith("linux"):
            output = await _theme_command(
                "gsettings", "get", "org.gnome.desktop.interface", "color-scheme"
            )
            if output is not None:
                scheme = output.decode().strip().strip("'")
                if scheme in ("prefer-dark", "prefer-light"):
                    return NIGHT.name if scheme == "prefer-dark" else DAY.name
                if scheme != "default":
                    return None
            output = await _theme_command(
                "gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"
            )
            if output is not None:
                return NIGHT.name if "dark" in output.decode().lower() else DAY.name
    except (OSError, TimeoutError, ValueError, plistlib.InvalidFileException):
        return None
    return None
