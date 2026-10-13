"""Verify the focused playback page against shared state using an offline app."""

import asyncio
from dataclasses import replace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, Input, ProgressBar, Static, TabbedContent

from music_tui.models import LyricLine, Lyrics, Track
from music_tui.playback_screen import PlaybackScreen, PlaybackState
from music_tui.screens import HelpScreen, LyricsPanel, QueueState

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
            can_add_to_playlist=True,
            can_create_playlist=True,
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
        page = PlaybackScreen(
            lambda: self.state,
            self.dispatch,
            get_queue_state=lambda: QueueState(self.state.track, (), (), False, "来源"),
            edit_queue=lambda operation, key: None,
            load_lyrics=self.load_lyrics,
        )
        self.push_screen(page)
        return page

    async def load_lyrics(self, track):
        """Return synchronized lyrics for the requested song without network access."""
        return Lyrics((LyricLine(0, track.title), LyricLine(5, "第二行")))


async def test_wide_playback_keeps_cover_details_and_controls_together():
    """Use a centered readable composition with a larger cover on a spacious terminal."""
    app = PlaybackHarness()
    async with app.run_test(size=(120, 40)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        identity = page.query_one("#song-identity").region
        cover = page.query_one("#playback-artwork").region
        controls = page.query_one("#playback-controls").region
        assert cover.width >= 32
        assert cover.height >= 16
        assert identity.width <= 90
        assert abs(identity.center[0] - 60) <= 2
        assert controls.width <= 90
        assert abs(controls.center[0] - 60) <= 2
        assert controls.height <= 4
        assert page.query_one("#playback-title").region.x < cover.right + 6


@pytest.mark.parametrize("size, rows", [((120, 40), 1), ((80, 24), 2), ((36, 16), 4)])
async def test_compact_controls_adapt_groups_to_terminal_width(size, rows):
    """Use single-row buttons, arranging transport and actions without stretching labels."""
    app = PlaybackHarness()
    async with app.run_test(size=size) as pilot:
        page = app.open_playback()
        await pilot.pause()
        buttons = list(page.query(Button))
        assert len({button.region.y for button in buttons}) == rows
        for button in buttons:
            assert button.region.height == 1
            assert button.region.width <= 18
            assert 0 <= button.region.x < button.region.right <= size[0]
            button.focus()
            await pilot.pause()
            row = page._compositor.render_strips()[button.region.y].text
            assert str(button.label) in row


@pytest.mark.parametrize("size", [(48, 24), (80, 24)])
async def test_long_song_details_remain_visible_or_scrollable(size):
    """Allow long titles and artists to grow the scroll area instead of clipping notices."""
    app = PlaybackHarness()
    app.state = replace(
        app.state,
        track=Track("long", "很长的歌曲名称" * 8, "很长的歌手名称" * 6, 120),
        status="播放失败，请按 F5 重试",
    )
    async with app.run_test(size=size) as pilot:
        page = app.open_playback()
        await pilot.pause()
        body = page.query_one("#playback-body")
        body.scroll_end(animate=False)
        await pilot.pause()
        message = page.query_one("#playback-message")
        assert message.region.y >= body.region.y
        assert message.region.bottom <= body.region.bottom
        assert "播放失败" in page._compositor.render_strips()[message.region.y].text


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


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (48, 16)])
async def test_secondary_button_labels_remain_visible_when_focused_or_clicked(size):
    """Keep single-row action labels on the actual screen while navigating and clicking."""
    app = PlaybackHarness()
    async with app.run_test(size=size) as pilot:
        page = app.open_playback()
        await pilot.pause()
        for name in ("quality", "add_to_playlist", "create_playlist", "retry"):
            button = page.query_one(f"#playback-{name}", Button)
            button.focus()
            await pilot.pause()
            row = page._compositor.render_strips()[button.region.y].text
            assert str(button.label) in row
            await pilot.click(button)
            row = page._compositor.render_strips()[button.region.y].text
            assert str(button.label) in row
        assert app.requests == ["quality", "add_to_playlist", "create_playlist", "retry"]


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
    """Route transport through the owner and switch listening buttons in place."""
    app = PlaybackHarness()
    names = [
        "previous",
        "pause",
        "next-button",
        "quality",
        "add_to_playlist",
        "create_playlist",
        "retry",
        "browse",
    ]
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
            "add_to_playlist",
            "create_playlist",
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
        page.query_one("#playback-add_to_playlist", Button).focus()
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


@pytest.mark.parametrize("key, tab", [("f3", "queue"), ("f4", "lyrics")])
async def test_listening_tabs_keep_transport_and_screen(key, tab):
    """Switch lyrics and queue in place and keep pause available on compact terminals."""
    app = PlaybackHarness()
    async with app.run_test(size=(48, 16)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert app.screen is page
        assert page.query_one(TabbedContent).active == tab
        assert page.query_one("#playback-pause", Button).region.bottom <= 16
        assert app.requests == (["queue"] if tab == "queue" else [])
        if tab == "queue":
            assert page.query_one("#queue-items", DataTable).region.height >= 3
        if tab == "lyrics":
            assert FIRST.title in str(page.query_one("#lyric-0", Static).render())


async def test_visible_lyrics_follow_song_changes_and_retry_errors():
    """Reload active lyrics after a song change and retry visible failures with F5."""
    app = PlaybackHarness()
    calls = []

    async def load(track):
        """Fail once for the new track before returning its real lyric content."""
        calls.append(track)
        if track == SECOND and calls.count(SECOND) == 1:
            raise ValueError("暂时不可用")
        return Lyrics((LyricLine(0, track.title),))

    app.load_lyrics = load
    async with app.run_test(size=(80, 24)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        await pilot.press("f4")
        await pilot.pause()
        app.state = replace(app.state, track=SECOND)
        await pilot.pause(0.3)
        assert any(
            "歌词加载失败" in str(w.render()) for w in page.query(LyricsPanel).first().query(Static)
        )
        assert not page.query("#lyric-0")
        await pilot.press("f5")
        await pilot.pause()
        assert SECOND.title in str(page.query_one("#lyric-0", Static).render())
        assert app.requests == []


async def test_slow_previous_song_lyrics_never_replace_the_new_song():
    """Discard delayed lyric responses when the active track changes mid-request."""
    app = PlaybackHarness()
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def load(track):
        """Model a cancelled external request that still produces its old response."""
        if track == FIRST:
            first_started.set()
            try:
                await release_first.wait()
            except asyncio.CancelledError:
                await release_first.wait()
        return Lyrics((LyricLine(0, track.title),))

    app.load_lyrics = load
    async with app.run_test(size=(80, 24)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        await pilot.press("f4")
        await asyncio.wait_for(first_started.wait(), 1)
        app.state = replace(app.state, track=SECOND)
        page.refresh_playback()
        await pilot.pause()
        assert SECOND.title in str(page.query_one("#lyric-0", Static).render())
        release_first.set()
        await pilot.pause()
        assert SECOND.title in str(page.query_one("#lyric-0", Static).render())


async def test_empty_lyrics_have_a_visible_instrumental_state():
    """Keep a successful empty lyric result readable without opening another screen."""
    app = PlaybackHarness()

    async def load(track):
        """Return the music source's successful no-lyrics response."""
        return Lyrics()

    app.load_lyrics = load
    async with app.run_test(size=(48, 16)) as pilot:
        page = app.open_playback()
        await pilot.pause()
        await pilot.press("f4")
        await pilot.pause()
        assert any(
            "纯音乐" in str(w.render()) for w in page.query(LyricsPanel).first().query(Static)
        )
        assert app.screen is page
