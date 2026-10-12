"""Verify the focused playback page against shared state using an offline app."""

from dataclasses import replace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, ProgressBar, Static

from music_tui.models import Track
from music_tui.playback_screen import PlaybackScreen, PlaybackState
from music_tui.screens import HelpScreen

FIRST = Track("a", "[red]第一首[/red]", "[bold]歌手[/bold]", 120)
SECOND = Track("b", "第二首", "另一位歌手", 180)


class PlaybackHarness(App):
    """Own shared playback snapshots and record action callbacks for pilot tests."""

    def __init__(self, state=None):
        """Start with a playable song and a deterministic progress snapshot."""
        super().__init__()
        self.state = state or PlaybackState(
            track=FIRST,
            position=30,
            duration=120,
            ready=True,
            active=True,
            quality="HQ 高品质",
            repeat_mode="列表循环",
            source_label="[green]歌单[/green]",
            platform="QQ 音乐",
            next_track=SECOND,
        )
        self.requests = []

    def compose(self) -> ComposeResult:
        """Provide a browser input whose focus should return after leaving the page."""
        yield Input(id="browser-focus")

    def dispatch(self, name):
        """Record actions and model browsing navigation and the help modal stack."""
        self.requests.append(name)
        if name in {"browse", "search", "locate", "filter"}:
            self.pop_screen()
        elif name == "help":
            self.push_screen(HelpScreen())

    def open_playback(self):
        """Push a page which reads current state without capturing a stale snapshot."""
        page = PlaybackScreen(lambda: self.state, self.dispatch)
        self.push_screen(page)
        return page


@pytest.mark.parametrize("size", [(80, 24), (48, 16)])
async def test_playback_controls_fit_compact_terminals(size):
    """Keep all buttons and exit guidance visible even when details need scrolling."""
    app = PlaybackHarness()
    async with app.run_test(size=size) as pilot:
        page = app.open_playback()
        await pilot.pause()
        for widget in [*page.query(Button), page.query_one("#playback-keys", Static)]:
            assert widget.region.y >= 0
            assert widget.region.bottom <= size[1]
            assert widget.region.right <= size[0]
            assert widget.region.width > 0
        assert app.focused is page.query_one("#playback-pause", Button)
        if size == (80, 24):
            message = page.query_one("#playback-message", Static)
            assert message.region.bottom <= page.query_one("#playback-body").region.bottom
        await pilot.press("tab", "enter")
        assert app.requests == ["next"]


async def test_live_values_refresh_without_changing_page_or_focus():
    """Show pause, buffering, track changes and progress from the shared player snapshot."""
    app = PlaybackHarness()
    async with app.run_test(size=(80, 24)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        pause = page.query_one("#playback-pause", Button)
        progress = page.query_one(ProgressBar)
        assert progress.progress == 30
        assert progress.total == 120
        assert FIRST.title in str(page.query_one("#playback-title", Static).render())
        assert FIRST.artist in str(page.query_one("#playback-artist", Static).render())
        assert "[green]歌单[/green]" in str(page.query_one("#playback-source", Static).render())
        app.state = replace(app.state, paused=True, position=45)
        await pilot.pause(0.3)
        assert "已暂停" in str(page.query_one("#playback-state", Static).render())
        assert str(pause.label) == "继续"
        assert progress.progress == 45
        assert app.focused is pause
        app.state = replace(app.state, track=SECOND, position=0, duration=180, buffering=True)
        await pilot.pause(0.3)
        assert app.screen is page
        assert SECOND.title in str(page.query_one("#playback-title", Static).render())
        assert "正在缓冲" in str(page.query_one("#playback-state", Static).render())
        assert progress.total == 180
        assert progress.progress == 0
        assert app.requests == []


async def test_all_buttons_delegate_to_the_shared_owner():
    """Route keyboard-selected buttons through the same app actions as browsing."""
    app = PlaybackHarness()
    names = ["previous", "pause", "next-button", "quality", "queue", "lyrics", "retry", "browse"]
    async with app.run_test(size=(80, 24)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        for name in names:
            page.query_one(f"#playback-{name}", Button).focus()
            await pilot.press("enter")
        assert app.requests == [
            "previous",
            "pause",
            "next",
            "quality",
            "queue",
            "lyrics",
            "retry",
            "browse",
        ]
        assert app.screen is not page


@pytest.mark.parametrize(
    "key, action",
    [("escape", "browse"), ("f7", "browse"), ("slash", "search"), ("g", "locate"), ("f", "filter")],
)
async def test_navigation_shortcuts_restore_browser_focus(key, action):
    """Let the owner leave playback before applying navigation to browser widgets."""
    app = PlaybackHarness()
    async with app.run_test(size=(80, 24)) as pilot:
        original = app.query_one(Input)
        original.focus()
        page = app.open_playback()
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert app.requests == [action]
        assert app.screen is not page
        assert app.focused is original


@pytest.mark.parametrize(
    "key, action",
    [
        ("p", "previous"),
        ("space", "pause"),
        ("n", "next"),
        ("u", "quality"),
        ("f2", "platform"),
        ("f3", "queue"),
        ("f4", "lyrics"),
        ("f5", "retry"),
        ("f6", "resume_session"),
    ],
)
async def test_playback_shortcuts_delegate_without_leaving_page(key, action):
    """Keep playback shortcuts active regardless of which page button has focus."""
    app = PlaybackHarness()
    async with app.run_test(size=(80, 24)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        page.query_one("#playback-queue", Button).focus()
        await pilot.press(key)
        assert app.requests == [action]
        assert app.screen is page


async def test_modal_escape_returns_to_playback_and_retains_focus():
    """Stack help above playback and restore the page instead of returning to browsing."""
    app = PlaybackHarness()
    async with app.run_test(size=(80, 24)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        focused = app.focused
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        app.state = replace(app.state, track=SECOND)
        await pilot.press("escape")
        await pilot.pause(0.3)
        assert app.screen is page
        assert app.focused is focused
        assert SECOND.title in str(page.query_one("#playback-title", Static).render())


@pytest.mark.parametrize(
    "state, activity",
    [
        (PlaybackState(), "正在准备播放器"),
        (PlaybackState(ready=True), "尚未播放"),
        (PlaybackState(track=FIRST, ready=True, loading=True), "正在获取播放地址"),
        (PlaybackState(track=FIRST, ready=True, status="播放失败，请按 F5 重试"), "等待播放"),
    ],
)
async def test_idle_loading_and_failure_states_do_not_fabricate_playback(state, activity):
    """Render truthful empty and failure states with a usable return path."""
    app = PlaybackHarness(state)
    async with app.run_test(size=(48, 16)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        assert activity in str(page.query_one("#playback-state", Static).render())
        assert page.query_one("#playback-pause", Button).disabled
        assert app.focused is page.query_one("#playback-browse", Button)
        assert page.query_one(ProgressBar).progress == 0
        assert "--:--" in str(page.query_one("#playback-time", Static).render())
        if state.status:
            assert state.status in str(page.query_one("#playback-message", Static).render())
        await pilot.press("enter")
        assert app.requests == ["browse"]


async def test_unknown_next_track_uses_owner_hint():
    """Explain an unloaded next page without claiming that playback will end."""
    app = PlaybackHarness()
    app.state = replace(app.state, next_track=None, next_hint="下一首将从原列表下一页加载")
    async with app.run_test(size=(80, 24)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        assert app.state.next_hint in str(page.query_one("#playback-next", Static).render())
