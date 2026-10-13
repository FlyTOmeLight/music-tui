"""Compact browser transport controls reading the shared playback snapshot."""

from collections.abc import Callable

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button

from .playback_screen import PlaybackState


class BottomTransport(Horizontal):
    """Keep mouse and keyboard controls synchronized with the existing player owner."""

    DEFAULT_CSS = """
    BottomTransport { width: 24; height: 1; background: $surface; }
    BottomTransport Button {
        width: 8; min-width: 0; height: 1; padding: 0; border: none;
        color: $foreground; background: $surface; text-style: none;
    }
    BottomTransport #bottom-pause { color: $accent; }
    BottomTransport Button:hover { background: $primary-muted; }
    BottomTransport Button:focus {
        background: $block-cursor-background; color: $accent; text-style: bold;
    }
    BottomTransport Button:disabled { color: $text-muted; opacity: 60%; }
    """

    def __init__(
        self,
        get_state: Callable[[], PlaybackState],
        action: Callable[[str], None],
    ):
        """Read current state on refresh and delegate commands to the application."""
        super().__init__()
        self.get_state = get_state
        self.callback = action

    def compose(self) -> ComposeResult:
        """Expose the three playback operations as focusable single-row buttons."""
        yield Button("上一首", id="bottom-previous", compact=True, disabled=True)
        yield Button("暂停", id="bottom-pause", compact=True, disabled=True)
        yield Button("下一首", id="bottom-next", compact=True, disabled=True)

    def on_mount(self) -> None:
        """Apply readiness before accepting the first input."""
        self.refresh_state()

    def refresh_state(self) -> None:
        """Update labels and availability without rebuilding buttons or moving focus."""
        state = self.get_state()
        pause = self.query_one("#bottom-pause", Button)
        pause.label = "继续" if state.paused else "暂停"
        pause.disabled = not state.active or state.loading
        for name in ("previous", "next"):
            self.query_one(f"#bottom-{name}", Button).disabled = (
                not state.ready or state.track is None or state.loading
            )

    @on(Button.Pressed)
    def press_control(self, event: Button.Pressed) -> None:
        """Route one button action without bubbling into other application handlers."""
        event.stop()
        self.callback((event.button.id or "").removeprefix("bottom-"))
