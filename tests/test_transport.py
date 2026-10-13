"""Verify compact transport input and live availability using Textual's pilot."""

from dataclasses import replace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from music_tui.models import Track
from music_tui.playback_screen import PlaybackState
from music_tui.transport import BottomTransport


class TransportHarness(App):
    """Supply shared playback state and record commands without an audio process."""

    def __init__(self, state: PlaybackState):
        """Keep one mutable snapshot for exercising live state changes."""
        super().__init__()
        self.state = state
        self.commands: list[str] = []

    def compose(self) -> ComposeResult:
        """Mount the same transport widget used by the browser page."""
        yield BottomTransport(lambda: self.state, self.commands.append)


@pytest.mark.parametrize("method", ["mouse", "keyboard"])
async def test_mouse_and_keyboard_request_the_same_playback_operations(method):
    """All three controls dispatch once by click or by focused Enter."""
    app = TransportHarness(
        PlaybackState(track=Track("1", "歌曲", "歌手", 120), ready=True, active=True)
    )
    async with app.run_test(size=(80, 24)) as pilot:
        transport = app.query_one(BottomTransport)
        for name in ("previous", "pause", "next"):
            button = transport.query_one(f"#bottom-{name}", Button)
            if method == "mouse":
                await pilot.click(button)
            else:
                button.focus()
                await pilot.press("enter")
        assert app.commands == ["previous", "pause", "next"]


@pytest.mark.parametrize(
    "state, allowed",
    [
        (PlaybackState(), []),
        (PlaybackState(ready=True), []),
        (
            PlaybackState(
                track=Track("1", "歌曲", "歌手", 120), ready=True, loading=True, active=True
            ),
            [],
        ),
        (
            PlaybackState(track=Track("1", "歌曲", "歌手", 120), ready=True, active=True),
            ["previous", "pause", "next"],
        ),
    ],
)
async def test_readiness_and_loading_gate_input(state, allowed):
    """Disabled controls cannot issue commands during startup or inactive playback."""
    app = TransportHarness(state)
    async with app.run_test(size=(80, 24)) as pilot:
        for name in ("previous", "pause", "next"):
            await pilot.click(f"#bottom-{name}")
        assert app.commands == allowed


async def test_pause_and_disconnect_refresh_preserve_controls_and_focus():
    """A paused song offers continue while a disconnect disables transport input."""
    app = TransportHarness(
        PlaybackState(track=Track("1", "歌曲", "歌手", 120), ready=True, active=True)
    )
    async with app.run_test(size=(80, 24)) as pilot:
        transport = app.query_one(BottomTransport)
        pause = transport.query_one("#bottom-pause", Button)
        pause.focus()
        app.state = replace(app.state, paused=True)
        transport.refresh_state()
        await pilot.pause()
        assert str(pause.label) == "继续"
        assert app.focused is pause
        await pilot.click(pause)
        assert app.commands == ["pause"]
        app.state = replace(app.state, ready=False, active=False)
        transport.refresh_state()
        await pilot.pause()
        assert all(button.disabled for button in transport.query(Button))
        await pilot.click("#bottom-next")
        assert app.commands == ["pause"]


@pytest.mark.parametrize("size", [(80, 24), (48, 16)])
async def test_compact_buttons_keep_labels_visible_in_one_row(size):
    """Focus and clicks leave every label visible within the compact terminal width."""
    app = TransportHarness(
        PlaybackState(track=Track("1", "歌曲", "歌手", 120), ready=True, active=True)
    )
    async with app.run_test(size=size) as pilot:
        transport = app.query_one(BottomTransport)
        assert transport.region.height == 1
        for button in transport.query(Button):
            button.focus()
            await pilot.pause()
            assert button.region.height == 1
            assert button.region.right <= size[0]
            assert str(button.label) in app.screen._compositor.render_strips()[button.region.y].text
