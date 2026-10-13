"""Verify queue navigation and checkpoints through the playback queue interface."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from music_tui.models import SongPage, Track
from music_tui.playback_queue import PlaybackQueue
from music_tui.playback_session import PlaybackSnapshot, QueuedTrack, RepeatMode

FIRST = Track("first", "第一首", "歌手", 60)
SECOND = Track("second", "第二首", "歌手", 60)
THIRD = Track("third", "第三首", "歌手", 60)


async def no_fetch(source, page):
    """Reject network access when all required tracks are already available."""
    pytest.fail(f"Unexpected page request: {source}, {page}")


@pytest.mark.parametrize("navigation", ["sequential", "shuffle"])
async def test_replaced_queue_rejects_pending_page_results(navigation):
    """A previous source's response cannot overwrite a replacement queue."""
    queue = PlaybackQueue()
    queue.replace([FIRST], "old", 1, True, 0)
    requested, release = asyncio.Event(), asyncio.Event()
    mode = RepeatMode.SEQUENCE if navigation == "sequential" else RepeatMode.RANDOM

    async def fetch(source, page):
        """Hold the old source's second page until a new source is selected."""
        assert source == "old" and page == 2
        requested.set()
        await release.wait()
        return SongPage([SECOND], False, 2)

    task = asyncio.create_task(
        queue.select(1, mode, fetch) if navigation == "sequential" else queue.prepare(mode, fetch)
    )
    try:
        async with asyncio.timeout(2):
            await requested.wait()
        queue.replace([THIRD], "new", 1, False, 0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        view = queue.view(mode)
        assert view.tracks == (THIRD,) and view.source == "new"
        assert view.pending == () and view.manual is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_page_request_retains_position_and_retry_navigation():
    """Retry continues the failed next-page request rather than replaying the old song."""
    queue = PlaybackQueue()
    queue.replace([FIRST], "source", 1, True, 0)
    calls = []

    async def fetch(source, page):
        """Fail the first request and provide the same requested page on retry."""
        calls.append((source, page))
        if len(calls) == 1:
            raise ValueError("请求失败")
        return SongPage([SECOND], False, 2)

    with pytest.raises(ValueError, match="请求失败"):
        await queue.move(1, RepeatMode.SEQUENCE, fetch)
    view = queue.view(RepeatMode.SEQUENCE)
    assert view.tracks == (FIRST,) and view.page == 1 and view.index == 0
    selection = await queue.retry(RepeatMode.SEQUENCE, fetch)
    assert selection.entry.track == SECOND and not selection.replay
    assert selection.entry.source == "source" and selection.entry.page == 2
    assert calls == [("source", 2), ("source", 2)]


@pytest.mark.parametrize("mode", list(RepeatMode))
async def test_manual_next_precedes_source_and_previous_returns_to_source(mode):
    """Inserted songs preserve the source cursor and play in insertion order."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND], "original", 1, False, 0)
    first_request = QueuedTrack(THIRD, "requested", 3)
    second_request = QueuedTrack(SECOND, "requested", 4)
    queue.enqueue(first_request)
    queue.enqueue(second_request)
    selection = await queue.move(1, mode, no_fetch)
    assert selection.entry is first_request
    assert queue.view(mode).index == 0
    selection = await queue.move(-1, mode, no_fetch)
    assert selection.entry.track == FIRST and selection.entry.source == "original"
    assert queue.view(mode).pending == (second_request,)
    assert queue.view(mode).manual is None
    selection = await queue.move(1, mode, no_fetch)
    assert selection.entry is second_request


@pytest.mark.parametrize("manual", [False, True])
async def test_single_repeat_preserves_pending_on_eof_but_manual_next_consumes(manual):
    """Automatic single repeat keeps the current song, including an inserted song."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND], "source", 1, False, 0)
    current = QueuedTrack(THIRD, "request", 2)
    if manual:
        queue.enqueue(current)
        await queue.move(1, RepeatMode.SINGLE, no_fetch)
    pending = QueuedTrack(SECOND, "request", 3)
    queue.enqueue(pending)
    selection = await queue.move(1, RepeatMode.SINGLE, no_fetch, automatic=True)
    assert selection.entry.track == (THIRD if manual else FIRST)
    assert queue.view(RepeatMode.SINGLE).pending == (pending,)
    assert (await queue.move(1, RepeatMode.SINGLE, no_fetch)).entry is pending


@pytest.mark.parametrize(
    ("action", "target", "operation", "order"),
    [
        ("up", 0, "unchanged", (0, 1)),
        ("down", 1, "unchanged", (0, 1)),
        ("up", 1, "move", (1, 0)),
        ("down", 0, "move", (1, 0)),
        ("remove", 0, "remove", (1,)),
        ("clear", 0, "clear", ()),
        ("unknown", 0, "unchanged", (0, 1)),
        ("remove", None, "missing", (0, 1)),
    ],
)
def test_pending_edits_use_entry_identity_and_leave_source_position(
    action, target, operation, order
):
    """Equal songs remain distinct queued requests, and stale identities cannot edit them."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND], "source", 1, False, 1)
    entries = [QueuedTrack(THIRD, "request", 1), QueuedTrack(THIRD, "request", 1)]
    for entry in entries:
        queue.enqueue(entry)
    key = entries[target] if target is not None else QueuedTrack(THIRD, "request", 1)
    result = queue.edit_pending(action, key)
    assert result.operation == operation
    if operation == "remove":
        assert result.entry is key
    view = queue.view(RepeatMode.SEQUENCE)
    assert len(view.pending) == len(order)
    assert all(actual is entries[index] for actual, index in zip(view.pending, order, strict=True))
    assert view.tracks == (FIRST, SECOND) and view.index == 1


async def test_random_round_does_not_repeat_and_navigation_revisits_history():
    """Random mode plays the whole list once before starting a new round."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND, THIRD], "source", 1, False, 0)
    queue.change_mode(RepeatMode.RANDOM)
    await queue.prepare(RepeatMode.RANDOM, no_fetch)
    second = await queue.move(1, RepeatMode.RANDOM, no_fetch)
    third = await queue.move(1, RepeatMode.RANDOM, no_fetch)
    assert {FIRST, second.entry.track, third.entry.track} == {FIRST, SECOND, THIRD}
    assert (await queue.move(-1, RepeatMode.RANDOM, no_fetch)).entry == second.entry
    assert (await queue.move(1, RepeatMode.RANDOM, no_fetch)).entry == third.entry
    assert (await queue.move(1, RepeatMode.RANDOM, no_fetch)).entry.track != third.entry.track


@pytest.mark.parametrize("mode", [RepeatMode.SEQUENCE, RepeatMode.RANDOM])
async def test_checkpoint_roundtrip_and_projections_do_not_mutate_queue(mode):
    """Views and checkpoints can be retained without exposing mutable queue collections."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND, THIRD], "source", 1, False, 0)
    queue.change_mode(mode)
    await queue.prepare(mode, no_fetch)
    pending = QueuedTrack(THIRD, "requested", 4)
    queue.enqueue(pending)
    original = queue.view(mode)
    assert queue.view(mode) == original
    with pytest.raises((FrozenInstanceError, AttributeError)):
        original.index = 99
    snapshot = queue.snapshot(FIRST, 12.5, True, QueuedTrack(FIRST, "source", 1))
    restored = PlaybackQueue()
    decoded = PlaybackSnapshot.decode(snapshot.encode())
    restored.restore(snapshot)
    assert restored.view(mode) == original
    from_encoded = PlaybackQueue()
    from_encoded.restore(decoded)
    assert from_encoded.view(mode) == original
    assert restored.snapshot(FIRST, 12.5, True, snapshot.origin).encode() == snapshot.encode()
    snapshot.queue.clear()
    snapshot.pending.clear()
    snapshot.shuffle_pages.clear()
    snapshot.shuffle_remaining.clear()
    snapshot.shuffle_history.clear()
    assert queue.view(mode) == original
    queue.edit_pending("clear", None)
    queue.replace([SECOND], "replacement", 1, False, 0)
    assert original.tracks == (FIRST, SECOND, THIRD) and original.pending == (pending,)
    assert restored.view(mode) == original


async def test_sequence_end_and_list_wrap_have_distinct_results():
    """End of a finite sequence reports completion while list mode wraps to the first song."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND], None, 1, False, 1)
    selection = await queue.move(1, RepeatMode.SEQUENCE, no_fetch)
    assert selection.entry is None and selection.ended
    selection = await queue.move(1, RepeatMode.LIST, no_fetch)
    assert selection.entry.track == FIRST and not selection.ended


async def test_consumed_duplicate_identity_cannot_edit_remaining_request():
    """A stale row key cannot remove an equal request still waiting to play."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND], "source", 1, False, 0)
    consumed = QueuedTrack(THIRD, "request", 1)
    remaining = QueuedTrack(THIRD, "request", 1)
    queue.enqueue(consumed)
    queue.enqueue(remaining)
    assert (await queue.move(1, RepeatMode.SEQUENCE, no_fetch)).entry is consumed
    assert queue.edit_pending("remove", consumed).operation == "missing"
    assert queue.view(RepeatMode.SEQUENCE).pending[0] is remaining
    retried = await queue.retry(RepeatMode.SEQUENCE, no_fetch)
    assert retried.entry is consumed and retried.replay
    assert queue.view(RepeatMode.SEQUENCE).pending == (remaining,)


async def test_retry_after_sequence_end_keeps_playback_stopped_but_previous_navigates():
    """Recovery cannot restart an ended sequence, while explicit navigation still works."""
    queue = PlaybackQueue()
    queue.replace([FIRST, SECOND], "source", 1, False, 1)
    ended = await queue.move(1, RepeatMode.SEQUENCE, no_fetch)
    assert ended.ended and ended.entry is None
    assert (await queue.retry(RepeatMode.SEQUENCE, no_fetch)).entry is None
    previous = await queue.move(-1, RepeatMode.SEQUENCE, no_fetch)
    assert previous.entry.track == FIRST and not previous.ended
    assert (await queue.retry(RepeatMode.SEQUENCE, no_fetch)).entry == previous.entry


async def test_replay_after_failed_page_request_discards_failed_navigation():
    """Reloading the retained song leaves later retry on that song instead of fetching a page."""
    queue = PlaybackQueue()
    queue.replace([FIRST], "source", 1, True, 0)
    current = await queue.select(0, RepeatMode.SEQUENCE, no_fetch)
    calls = []

    async def fetch(source, page):
        """Record the failed navigation so subsequent recovery cannot repeat it unnoticed."""
        calls.append((source, page))
        raise ValueError("请求失败")

    with pytest.raises(ValueError, match="请求失败"):
        await queue.move(1, RepeatMode.SEQUENCE, fetch)
    replayed = queue.replay()
    assert replayed.entry == current.entry and replayed.replay
    retried = await queue.retry(RepeatMode.SEQUENCE, fetch)
    assert retried.entry == current.entry and retried.replay
    assert calls == [("source", 2)]
