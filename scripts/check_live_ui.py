"""Check the actual QQ library, Textual interface and mpv without changing account data."""

import asyncio
import html
import os
import re
import shutil
import tempfile
from pathlib import Path

from rich.cells import cell_len
from textual.widgets import DataTable, Input, ListView, TabbedContent

from music_tui.app import MusicApp
from music_tui.artwork import AlbumArtwork
from music_tui.models import Quality
from music_tui.playback_screen import PlaybackScreen
from music_tui.player import Player
from music_tui.screens import LyricsPanel
from music_tui.session import SessionStore
from music_tui.sources.qqmusic import QQMusic


async def until(pilot, condition, deadline_seconds=40):
    """Wait for an observable UI or player state with a fixed deadline."""
    async with asyncio.timeout(deadline_seconds):
        while not condition():
            await pilot.pause(0.1)


def save_screenshot(app, filename, directory):
    """Export a screenshot with full-width Chinese characters preserved in the SVG."""
    path = Path(app.save_screenshot(filename, path=str(directory)))
    svg = path.read_text()

    def fix_text_width(match):
        """Account for terminal cells rather than character count in Rich's export."""
        text = html.unescape(match[4])
        width = float(match[2]) * cell_len(text) / max(1, len(text))
        return f"{match[1]}{width}{match[3]}{match[4]}{match[5]}"

    svg = re.sub(r'(<text[^>]*textLength=")([\d.]+)("[^>]*>)([^<]*)(</text>)', fix_text_width, svg)
    path.write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n")


async def main():
    """Use a credential copy and a silent mpv instance for live end-to-end checks."""
    os.environ.pop("NO_COLOR", None)
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    screenshots = Path(tempfile.gettempdir()) / "music-tui-e2e"
    screenshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="music-tui-live-") as sandbox:
        root = Path(sandbox)
        account = root / "qq"
        account.mkdir(mode=0o700)
        for name in ("credential.json", "device.json"):
            origin = data / "qqmusic-tui" / name
            if origin.is_file():
                shutil.copyfile(origin, account / name)
                (account / name).chmod(0o600)
        music = QQMusic(account)
        player = Player(audio_output="null")
        app = MusicApp(music, player, session_store=SessionStore(root / "session"))
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await until(pilot, lambda: app.ready and bool(app.playlists))
                print("PASS saved login + real playlist list", flush=True)
                library = app.query_one("#playlists", ListView)
                library.focus()
                library.index = 0
                await pilot.press("enter")
                await until(pilot, lambda: app.query_one("#songs", DataTable).row_count > 0)
                print("PASS playlist selection + real song rows", flush=True)
                await pilot.press("slash")
                app.query_one("#search", Input).value = "不死鸟 霍尊"
                await pilot.press("enter")
                await until(pilot, lambda: app.source == "不死鸟 霍尊" and bool(app.tracks))
                table = app.query_one("#songs", DataTable)
                missing_master = next(
                    (
                        index
                        for index, track in enumerate(app.tracks)
                        if not music._file_info(track, Quality.MASTER)
                    ),
                    None,
                )
                table.move_cursor(row=missing_master if missing_master is not None else 0)
                table.focus()
                app.quality = Quality.MASTER
                await pilot.press("enter")
                await until(pilot, lambda: (player.properties.get("time-pos") or 0) > 1, 50)
                print("PASS real music URL + mpv decoded playback", flush=True)
                assert app.quality == Quality.MASTER and app.current_quality is not None
                if missing_master is not None:
                    assert app.current_quality != Quality.MASTER
                    assert "已自动切换" in app.last_status_message
                    print(
                        "PASS unavailable preferred tier + automatic lower-tier playback",
                        flush=True,
                    )
                else:
                    print(
                        "No missing master tier here; fallback covered by offline tests",
                        flush=True,
                    )
                print(f"ACTUAL QUALITY {app.current_quality.label}", flush=True)
                if missing_master is not None and missing_master != 0:
                    table.move_cursor(row=0)
                    await pilot.press("enter")
                    await until(pilot, lambda: app.current == app.tracks[0] and not app.loading, 50)
                    await until(pilot, lambda: (player.properties.get("time-pos") or 0) > 1, 50)
                    assert app.quality == Quality.MASTER
                    print("PASS next song playback retains the preferred tier", flush=True)
                await pilot.press("f7")
                await until(pilot, lambda: isinstance(app.screen, PlaybackScreen))
                page = app.screen
                cover = page.query_one(AlbumArtwork)
                await until(pilot, lambda: cover._image is not None, 12)
                print("PASS real album cover download", flush=True)
                assert cover.region.width >= 32 and cover.region.height >= 16
                identity = page.query_one("#song-identity").region
                controls = page.query_one("#playback-controls").region
                assert identity.width <= 90 and abs(identity.center[0] - 60) <= 2
                assert controls.width <= 90 and abs(controls.center[0] - 60) <= 2
                print(
                    "LAYOUT",
                    {
                        name: tuple(page.query_one(name).region)
                        for name in ("#song-identity", "#playback-artwork", "#playback-controls")
                    },
                    flush=True,
                )
                save_screenshot(app, "playback-wide.svg", screenshots)
                await pilot.press("space")
                await until(pilot, lambda: player.properties.get("pause") is True)
                await pilot.press("space")
                await until(pilot, lambda: player.properties.get("pause") is False)
                print("PASS pause + resume via keyboard", flush=True)
                await pilot.press("f4")
                await until(pilot, lambda: bool(page.query_one(LyricsPanel).lyrics.lines))
                assert page.query_one("#playback-tabs", TabbedContent).active == "lyrics"
                print("PASS real lyrics in player tab", flush=True)
                await pilot.resize_terminal(80, 24)
                await pilot.pause()
                save_screenshot(app, "lyrics-80x24.svg", screenshots)
                await pilot.press("f3")
                await until(
                    pilot, lambda: page.query_one("#playback-tabs", TabbedContent).active == "queue"
                )
                print("PASS queue tab", flush=True)
                save_screenshot(app, "queue-80x24.svg", screenshots)
                page.show_tab("song")
                await pilot.pause()
                for size in ((80, 24), (48, 16), (36, 16), (120, 40)):
                    await pilot.resize_terminal(*size)
                    await pilot.pause()
                    for button in page.query("Button"):
                        assert button.region.width > 0 and button.region.height == 1
                        assert button.region.x >= 0 and button.region.y >= 0
                        assert button.region.bottom <= size[1]
                        assert button.region.right <= size[0]
                    for name in ("quality", "add_to_playlist", "create_playlist", "retry"):
                        button = page.query_one(f"#playback-{name}")
                        button.focus()
                        await pilot.pause()
                        row = page._compositor.render_strips()[button.region.y].text
                        assert str(button.label) in row
                    page.query_one("#playback-quality").focus()
                    await pilot.pause()
                    save_screenshot(app, f"playback-{size[0]}x{size[1]}.svg", screenshots)
                    print(
                        f"PASS visible controls + focused labels at {size[0]}x{size[1]}", flush=True
                    )
                previous_theme = app.theme
                await pilot.press("f9")
                await pilot.pause()
                assert app.theme != previous_theme
                save_screenshot(app, "playback-other-theme.svg", screenshots)
                await pilot.resize_terminal(80, 24)
                await pilot.pause()
                save_screenshot(app, "playback-other-theme-80x24.svg", screenshots)
                await pilot.press("escape")
                assert not isinstance(app.screen, PlaybackScreen)
                print("PASS theme switch + return to song list", flush=True)
                print(f"Screenshots: {screenshots}", flush=True)
        finally:
            await player.close()
            await music.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(f"FAIL {type(error).__name__}; authenticated responses and URLs are not logged")
        raise SystemExit(1) from None
