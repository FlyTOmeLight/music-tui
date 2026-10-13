"""Verify queue navigation across pending requests and independent source pages."""

import asyncio

import pytest

from music_tui.models import SongPage, Track
from music_tui.playback_queue import PlaybackQueue
from music_tui.playback_session import RepeatMode

FIRST = Track("first", "第一首", "歌手", 60)
SECOND = Track("second", "第二首", "歌手", 60)
THIRD = Track("third", "第三首", "歌手", 60)


@pytest.mark.parametrize("navigation", ["sequential", "shuffle"])
async def test_replaced_queue_rejects_pending_page_results(navigation):
    """A previous source's response cannot overwrite a replacement queue."""
    queue = PlaybackQueue()
    queue.replace([FIRST], "old", 1, True, 0)
    requested, release = asyncio.Event(), asyncio.Event()

    async def fetch(source, page):
        """Hold the old source's second page until a new source is selected."""
        assert source == "old" and page == 2
        requested.set()
        await release.wait()
        return SongPage([SECOND], False, 2)

    task = asyncio.create_task(
        queue.resolve(1, RepeatMode.SEQUENCE, fetch)
        if navigation == "sequential"
        else queue.prepare_shuffle(fetch)
    )
    try:
        async with asyncio.timeout(2):
            await requested.wait()
        queue.replace([THIRD], "new", 1, False, 0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert queue.tracks == [THIRD] and queue.source == "new"
        assert not queue.shuffle_pages
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_page_request_retains_current_source_position():
    """A failed next-page request leaves the original list available for retry."""
    queue = PlaybackQueue()
    queue.replace([FIRST], "source", 1, True, 0)

    async def fetch(source, page):
        """Fail a source request without modifying the queue."""
        raise ValueError("请求失败")

    with pytest.raises(ValueError, match="请求失败"):
        await queue.resolve(1, RepeatMode.SEQUENCE, fetch)
    assert queue.tracks == [FIRST] and queue.page == 1 and queue.index == 0
