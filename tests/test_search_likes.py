"""Verify search like confirmations, account isolation, and overlapping operations."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from music_tui.likes import SearchLikes
from music_tui.models import Track
from music_tui.sources.netease import NetEaseMusic
from music_tui.sources.qqmusic import QQMusic

TRACK = Track("opaque-mid", "歌曲", "歌手", 180)
OTHER = Track("other", "另一首", "歌手", 180)


def fake_source(ids=None):
    """Provide an authenticated source with in-memory library operations."""
    return SimpleNamespace(
        account_id="42",
        liked_ids=AsyncMock(return_value=set(ids or [])),
        set_liked=AsyncMock(),
    )


async def test_search_likes_confirms_whole_list_and_toggles():
    """One full-list query gives all rows confirmed states and writes update them."""
    source = fake_source([TRACK.mid])
    likes = SearchLikes(source)
    assert likes.state(TRACK) is None
    assert await likes.refresh()
    assert likes.state(TRACK) is True
    assert likes.state(OTHER) is False
    assert await likes.toggle(TRACK) is False
    assert likes.state(TRACK) is False
    source.set_liked.assert_awaited_once_with(TRACK, False)
    source.liked_ids.assert_awaited_once()


async def test_write_failure_keeps_confirmation_but_refresh_failure_invalidates_it():
    """Keep failed-write confirmation but require a new query after failed refresh."""
    source = fake_source([TRACK.mid])
    source.set_liked.side_effect = ValueError("write failed")
    likes = SearchLikes(source)
    with pytest.raises(ValueError, match="write failed"):
        await likes.toggle(TRACK)
    assert likes.state(TRACK) is True
    source.set_liked.assert_awaited_once_with(TRACK, False)
    source.liked_ids.side_effect = ValueError("query failed")
    with pytest.raises(ValueError, match="query failed"):
        await likes.refresh()
    assert likes.state(TRACK) is None
    source.set_liked.reset_mock()
    with pytest.raises(ValueError, match="query failed"):
        await likes.toggle(TRACK)
    source.set_liked.assert_not_awaited()


async def test_refresh_invalidates_while_waiting_for_an_inflight_write():
    """Refreshing masks old confirmation without breaking an already accepted write."""
    source = fake_source()
    likes = SearchLikes(source)
    await likes.refresh()
    started, release = asyncio.Event(), asyncio.Event()

    async def write(track, liked):
        """Hold the write and expose its accepted state to the following refresh."""
        started.set()
        await release.wait()
        source.liked_ids.return_value = {track.mid} if liked else set()

    source.set_liked.side_effect = write
    write_task = asyncio.create_task(likes.toggle(TRACK))
    refresh_task = None
    try:
        async with asyncio.timeout(2):
            await started.wait()
            refresh_task = asyncio.create_task(likes.refresh())
            await asyncio.sleep(0)
            assert likes.state(TRACK) is None
            release.set()
            assert await write_task is True
            assert await refresh_task
            assert likes.state(TRACK) is True
    finally:
        release.set()
        await asyncio.gather(
            write_task, *([refresh_task] if refresh_task else []), return_exceptions=True
        )


async def test_unknown_query_failure_never_writes():
    """An unavailable confirmation remains unknown and cannot cause an inverse write."""
    source = fake_source()
    source.liked_ids.side_effect = ValueError("query failed")
    likes = SearchLikes(source)
    with pytest.raises(ValueError, match="query failed"):
        await likes.toggle(TRACK)
    assert likes.state(TRACK) is None
    source.set_liked.assert_not_awaited()
    source.account_id = ""
    assert await likes.refresh() is False
    with pytest.raises(ValueError, match="扫码登录"):
        await likes.toggle(TRACK)


@pytest.mark.parametrize("switch_source", [False, True])
async def test_late_query_cannot_populate_new_account(switch_source):
    """Source and same-source account changes invalidate a pending full-list result."""
    source = fake_source()
    started, release = asyncio.Event(), asyncio.Event()

    async def read():
        """Hold the old account's response until the UI has switched identity."""
        started.set()
        await release.wait()
        return {TRACK.mid}

    source.liked_ids.side_effect = read
    likes = SearchLikes(source)
    task = asyncio.create_task(likes.refresh())
    async with asyncio.timeout(2):
        await started.wait()
        if switch_source:
            likes.bind(fake_source())
        else:
            source.account_id = "99"
        assert likes.state(TRACK) is None
        release.set()
        assert await task is False
    assert likes.state(TRACK) is None
    assert await likes.refresh()
    assert likes.state(TRACK) is (not switch_source)


async def test_duplicate_toggle_is_ignored_and_refresh_follows_write():
    """Repeated clicks cannot undo an in-flight write and refresh cannot race it."""
    source = fake_source()
    started, release = asyncio.Event(), asyncio.Event()

    async def write(track, liked):
        """Pause a successful like and expose its server state only after completion."""
        started.set()
        await release.wait()
        source.liked_ids.return_value = {track.mid} if liked else set()

    source.set_liked.side_effect = write
    likes = SearchLikes(source)
    await likes.refresh()
    task = asyncio.create_task(likes.toggle(TRACK))
    async with asyncio.timeout(2):
        await started.wait()
        assert await likes.toggle(TRACK) is None
        refresh = asyncio.create_task(likes.refresh())
        release.set()
        assert await task is True
        assert await refresh
    assert likes.state(TRACK) is True
    source.set_liked.assert_awaited_once_with(TRACK, True)


async def test_switch_account_during_write_discards_confirmation():
    """A write's old-account confirmation cannot reappear after identity changes."""
    source = fake_source()
    started, release = asyncio.Event(), asyncio.Event()

    async def write(track, liked):
        """Delay an old account's write response while switching source accounts."""
        started.set()
        await release.wait()

    source.set_liked.side_effect = write
    likes = SearchLikes(source)
    task = asyncio.create_task(likes.toggle(TRACK))
    async with asyncio.timeout(2):
        await started.wait()
        source.account_id = "99"
        release.set()
        assert await task is None
    assert likes.state(TRACK) is None


async def test_qq_liked_ids_are_search_mids_not_numeric_song_ids(monkeypatch, tmp_path):
    """Collect all SDK pages and retain opaque MIDs rather than numeric write IDs."""
    collection = AsyncMock(
        return_value=[
            SimpleNamespace(mid=TRACK.mid, id=123),
            SimpleNamespace(mid=OTHER.mid, id=456),
        ]
    )
    client = SimpleNamespace(
        credential=SimpleNamespace(musicid=42, musickey="test", encrypt_uin="fake"),
        user=SimpleNamespace(
            get_fav_song=lambda *args, **kwargs: SimpleNamespace(collect_items=collection)
        ),
    )
    monkeypatch.setattr("music_tui.sources.qqmusic.Client", lambda **kwargs: client)
    source = QQMusic(tmp_path)
    assert await source.liked_ids() == {TRACK.mid, OTHER.mid}
    collection.assert_awaited_once()


async def test_enhanced_liked_ids_normalize_complete_numeric_list(monkeypatch, tmp_path):
    """Enhanced's full liked IDs match the opaque string IDs carried by search tracks."""
    original = httpx.AsyncClient

    def respond(request):
        """Supply the fake authenticated account's complete liked-song list."""
        assert request.url.path == "/likelist"
        return httpx.Response(200, json={"code": 200, "ids": [123, 456]})

    monkeypatch.setattr(
        "music_tui.sources.netease.httpx.AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
    )
    source = NetEaseMusic(tmp_path, api_url="http://api.test")
    source.user_id = 42
    source.cookie = "MUSIC_U=test"
    try:
        assert await source.liked_ids() == {"123", "456"}
    finally:
        await source.close()


async def test_cancelled_write_releases_pending_song_for_retry():
    """Cancelling a waiting write keeps confirmed state and permits a later retry."""
    source = fake_source()
    started = asyncio.Event()

    async def write(track, liked):
        """Wait until the owning UI cancels this unfinished operation."""
        started.set()
        await asyncio.Event().wait()

    source.set_liked.side_effect = write
    likes = SearchLikes(source)
    await likes.refresh()
    task = asyncio.create_task(likes.toggle(TRACK))
    async with asyncio.timeout(2):
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert likes.state(TRACK) is False
    source.set_liked.side_effect = None
    assert await likes.toggle(TRACK) is True
