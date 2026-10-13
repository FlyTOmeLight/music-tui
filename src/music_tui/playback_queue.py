"""Own source pagination, manual insertions, and nonrepeating shuffle navigation."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from .models import SongPage, Track
from .playback_session import PlaybackSnapshot, PlaybackSource, QueuedTrack, RepeatMode

type FetchPage = Callable[[PlaybackSource, int], Awaitable[SongPage]]


@dataclass
class _QueueData:
    """Retain navigation state privately so callers cannot split queue transitions."""

    tracks: list[Track] = field(default_factory=list)
    source: PlaybackSource = None
    page: int = 1
    has_more: bool = False
    generation: int = 0
    index: int = -1
    origin: QueuedTrack | None = None
    pending: list[QueuedTrack] = field(default_factory=list)
    manual: QueuedTrack | None = None
    shuffle_pages: dict[int, tuple[list[Track], bool]] = field(default_factory=dict)
    shuffle_remaining: list[tuple[int, int]] = field(default_factory=list)
    shuffle_history: list[tuple[int, int]] = field(default_factory=list)
    shuffle_cursor: int = -1
    ended: bool = False


@dataclass(frozen=True)
class QueueSelection:
    """Describe a playable entry, a finished list, or an idle navigation request."""

    entry: QueuedTrack | None
    ended: bool = False
    replay: bool = False


@dataclass(frozen=True)
class QueueView:
    """Expose an immutable projection for presentation without writable navigation state."""

    tracks: tuple[Track, ...]
    source: PlaybackSource
    page: int
    index: int
    has_more: bool
    pending: tuple[QueuedTrack, ...]
    manual: QueuedTrack | None
    upcoming: tuple[Track, ...]


@dataclass(frozen=True)
class QueueEdit:
    """Report an edit outcome so the UI can display feedback without reimplementing edits."""

    operation: Literal["clear", "remove", "move", "missing", "unchanged"]
    entry: QueuedTrack | None = None


class PlaybackQueue:
    """Own source navigation, manual priority, editing, and navigation retry semantics."""

    def __init__(self) -> None:
        """Start an empty queue with no failed navigation to retry."""
        self._state = _QueueData()
        self._retry: tuple[int, int] | None = None

    def replace(
        self,
        tracks: list[Track],
        source: PlaybackSource,
        page: int,
        has_more: bool,
        index: int,
        *,
        origin: QueuedTrack | None = None,
    ) -> None:
        """Start a newly selected list and discard insertions from the previous list."""
        self._state.generation += 1
        self._state.tracks, self._state.source, self._state.page, self._state.has_more = (
            tracks.copy(),
            source,
            page,
            has_more,
        )
        self._state.index, self._state.origin = index, origin
        self._retry = None
        self._state.ended = False
        self._state.pending.clear()
        self._state.manual = None
        self._reset_shuffle(index)

    def restore(self, snapshot: PlaybackSnapshot) -> None:
        """Apply an already validated account snapshot without performing network requests."""
        self._state.generation += 1
        self._retry = None
        self._state.ended = False
        self._state.tracks, self._state.source, self._state.page = (
            snapshot.queue.copy(),
            snapshot.source,
            snapshot.page,
        )
        self._state.has_more, self._state.index = snapshot.has_more, snapshot.index
        self._state.origin, self._state.pending, self._state.manual = (
            snapshot.queue_origin,
            snapshot.pending.copy(),
            snapshot.manual,
        )
        self._state.shuffle_pages = {
            page: (tracks.copy(), more) for page, (tracks, more) in snapshot.shuffle_pages.items()
        }
        self._state.shuffle_remaining = snapshot.shuffle_remaining.copy()
        self._state.shuffle_history, self._state.shuffle_cursor = (
            snapshot.shuffle_history.copy(),
            snapshot.shuffle_cursor,
        )

    async def _resolve(
        self, index: int, mode: RepeatMode, fetch: FetchPage, *, shuffle: int = 0
    ) -> Track | None:
        """Resolve list boundaries before committing the next source page and position."""
        generation = self._state.generation
        if shuffle:
            page, index = await self._shuffle_position(shuffle, fetch)
            tracks, more = self._state.shuffle_pages[page]
        else:
            tracks, page, more = self._state.tracks, self._state.page, self._state.has_more
        if index >= len(tracks):
            if more and self._state.source is not None:
                page += 1
                result = await fetch(self._state.source, page)
                tracks, more, index = result.tracks, result.has_more, 0
            if not tracks or index >= len(tracks):
                if mode == RepeatMode.LIST:
                    page = 1
                    if self._state.source is not None:
                        result = await fetch(self._state.source, page)
                        tracks, more = result.tracks, result.has_more
                    index = 0
        elif index < 0:
            if page > 1 and self._state.source is not None:
                page -= 1
                result = await fetch(self._state.source, page)
                tracks, more = result.tracks, result.has_more
            elif mode == RepeatMode.LIST:
                while more and self._state.source is not None:
                    page += 1
                    result = await fetch(self._state.source, page)
                    tracks, more = result.tracks, result.has_more
                    if not tracks:
                        break
            index = len(tracks) - 1 if page > 1 or mode == RepeatMode.LIST else 0
        if generation != self._state.generation:
            raise asyncio.CancelledError
        if not 0 <= index < len(tracks):
            self._state.manual = None
            self._state.index = len(self._state.tracks) - 1
            return None
        self._state.tracks, self._state.page, self._state.has_more, self._state.index = (
            tracks,
            page,
            more,
            index,
        )
        self._state.manual = None
        return tracks[index]

    def _reset_shuffle(self, index: int) -> None:
        """Seed a new random round with the selected source position."""
        self._state.shuffle_pages = {}
        self._state.shuffle_remaining = []
        self._state.shuffle_history = [(self._state.page, index)] if self._state.tracks else []
        self._state.shuffle_cursor = len(self._state.shuffle_history) - 1

    async def _prepare_shuffle(self, fetch: FetchPage) -> None:
        """Load complete source pages, discarding results if the queue was replaced."""
        if self._state.shuffle_pages:
            return
        generation, source = self._state.generation, self._state.source
        pages = {self._state.page: (self._state.tracks.copy(), self._state.has_more)}
        if source is not None:
            page, more = 1, True
            while more:
                if page not in pages:
                    result = await fetch(source, page)
                    pages[page] = (result.tracks, result.has_more)
                tracks, more = pages[page]
                if not tracks:
                    break
                page += 1
        if generation != self._state.generation:
            raise asyncio.CancelledError
        self._state.shuffle_pages = pages

    def _fill_shuffle_remaining(self) -> None:
        """Choose a random round once, avoiding an immediate repeat when possible."""
        if self._state.shuffle_remaining:
            return
        current = (self._state.page, self._state.index)
        positions = [
            (page, index)
            for page, (tracks, _) in self._state.shuffle_pages.items()
            for index in range(len(tracks))
        ]
        if len(positions) > 1 and len(self._state.shuffle_history) == 1:
            positions.remove(current)
        random.shuffle(positions)
        if len(positions) > 1 and positions[-1] == current:
            positions[0], positions[-1] = positions[-1], positions[0]
        self._state.shuffle_remaining = positions

    async def _shuffle_position(self, direction: int, fetch: FetchPage) -> tuple[int, int]:
        """Revisit random history or append a uniformly chosen source position."""
        await self._prepare_shuffle(fetch)
        if direction < 0:
            self._state.shuffle_cursor = max(0, self._state.shuffle_cursor - 1)
            return self._state.shuffle_history[self._state.shuffle_cursor]
        if self._state.shuffle_cursor + 1 < len(self._state.shuffle_history):
            self._state.shuffle_cursor += 1
            return self._state.shuffle_history[self._state.shuffle_cursor]
        self._fill_shuffle_remaining()
        position = self._state.shuffle_remaining.pop()
        self._state.shuffle_history.append(position)
        self._state.shuffle_cursor += 1
        return position

    def _upcoming(self, mode: RepeatMode) -> tuple[list[Track], bool]:
        """Expose successors in their actual playing order without advancing navigation."""
        if mode == RepeatMode.RANDOM and self._state.shuffle_pages:
            positions = self._state.shuffle_history[self._state.shuffle_cursor + 1 :] + list(
                reversed(self._state.shuffle_remaining)
            )
            return [self._state.shuffle_pages[page][0][index] for page, index in positions], False
        if mode == RepeatMode.RANDOM:
            return [], self._state.has_more
        return self._state.tracks[self._state.index + 1 :], self._state.has_more

    def _entry(self) -> QueuedTrack | None:
        """Return the selected manual entry or its original list position and origin."""
        state = self._state
        if state.ended:
            return None
        if state.manual is not None:
            return state.manual
        if not 0 <= state.index < len(state.tracks):
            return None
        track = state.tracks[state.index]
        if state.source is None and state.origin is not None and state.origin.track == track:
            return state.origin
        return QueuedTrack(track, state.source, state.page)

    async def select(self, index: int, mode: RepeatMode, fetch: FetchPage) -> QueueSelection:
        """Select an explicit original-list position without consuming manual pending songs."""
        return await self._select(index, mode, fetch)

    async def _select(
        self, index: int, mode: RepeatMode, fetch: FetchPage, *, shuffle: int = 0
    ) -> QueueSelection:
        """Commit resolved navigation or retain its request for an explicit retry."""
        generation = self._state.generation
        try:
            track = await self._resolve(index, mode, fetch, shuffle=shuffle)
        except Exception:
            if generation == self._state.generation:
                self._retry = (index, shuffle)
            raise
        self._retry = None
        self._state.ended = track is None
        return QueueSelection(self._entry() if track is not None else None, ended=track is None)

    async def move(
        self, direction: int, mode: RepeatMode, fetch: FetchPage, *, automatic: bool = False
    ) -> QueueSelection:
        """Advance manual songs first or return to the interrupted source on previous."""
        state = self._state
        if automatic and mode == RepeatMode.SINGLE:
            return QueueSelection(self._entry())
        if direction > 0 and state.pending:
            state.manual = state.pending.pop(0)
            state.ended = False
            self._retry = None
            return QueueSelection(state.manual)
        if not state.tracks:
            return QueueSelection(None)
        if direction < 0 and state.manual is not None:
            return await self._select(state.index, mode, fetch)
        return await self._select(
            state.index + direction,
            mode,
            fetch,
            shuffle=direction if mode == RepeatMode.RANDOM else 0,
        )

    async def retry(self, mode: RepeatMode, fetch: FetchPage) -> QueueSelection:
        """Retry navigation or replay the retained entry without consuming pending songs."""
        if self._retry is not None:
            index, shuffle = self._retry
            return await self._select(index, mode, fetch, shuffle=shuffle)
        return self.replay()

    def replay(self) -> QueueSelection:
        """Reload the current entry, discarding a failed navigation without advancing it."""
        self._retry = None
        return QueueSelection(self._entry(), replay=True)

    def enqueue(self, entry: QueuedTrack) -> None:
        """Append a manual song with its stable identity and browsing origin."""
        self._state.pending.append(entry)

    def edit_pending(self, action: str, key: object | None) -> QueueEdit:
        """Edit a rendered entry by identity while preserving current playback and navigation."""
        pending = self._state.pending
        if action == "clear":
            pending.clear()
            return QueueEdit("clear")
        index = next((i for i, entry in enumerate(pending) if entry is key), None)
        if index is None:
            return QueueEdit("missing")
        entry = pending[index]
        if action == "remove":
            pending.pop(index)
            return QueueEdit("remove", entry)
        if action in ("up", "down"):
            destination = index + (-1 if action == "up" else 1)
            if 0 <= destination < len(pending):
                pending[index], pending[destination] = pending[destination], pending[index]
                return QueueEdit("move", entry)
        return QueueEdit("unchanged")

    async def prepare(self, mode: RepeatMode, fetch: FetchPage) -> None:
        """Prepare random successors once without changing the selected source position."""
        if mode == RepeatMode.RANDOM and self._state.tracks:
            await self._prepare_shuffle(fetch)
            self._fill_shuffle_remaining()

    def change_mode(self, mode: RepeatMode) -> None:
        """Start a new random history when the user enters random playback."""
        if mode == RepeatMode.RANDOM:
            self._reset_shuffle(self._state.index)

    def view(self, mode: RepeatMode) -> QueueView:
        """Read pending and source successors without exposing mutable lists or random history."""
        state = self._state
        upcoming, more = self._upcoming(mode)
        return QueueView(
            tuple(state.tracks),
            state.source,
            state.page,
            state.index,
            more,
            tuple(state.pending),
            state.manual,
            tuple(upcoming),
        )

    def snapshot(
        self, track: Track, position: float, paused: bool, origin: QueuedTrack | None
    ) -> PlaybackSnapshot:
        """Copy navigation into the established account checkpoint format."""
        state = self._state
        return PlaybackSnapshot(
            queue=state.tracks.copy(),
            source=state.source,
            page=state.page,
            has_more=state.has_more,
            index=state.index,
            track=track,
            position=position,
            paused=paused,
            pending=state.pending.copy(),
            manual=state.manual,
            origin=origin,
            queue_origin=state.origin,
            shuffle_pages={
                page: (tracks.copy(), more) for page, (tracks, more) in state.shuffle_pages.items()
            },
            shuffle_remaining=state.shuffle_remaining.copy(),
            shuffle_history=state.shuffle_history.copy(),
            shuffle_cursor=state.shuffle_cursor,
        )


class PlaybackEvents:
    """Delay end events until loading has acknowledged the current mpv entry identity."""

    def __init__(self) -> None:
        """Keep deferred terminal events local to the current player connection."""
        self.deferred: list[dict] = []

    def drain(self, events: asyncio.Queue[dict], *, loading: bool) -> list[dict]:
        """Deliver disconnects immediately and retain end events throughout a pending load."""
        result = [] if loading else self.deferred
        if not loading:
            self.deferred = []
        while not events.empty():
            event = events.get_nowait()
            if event.get("event") == "end-file" and loading:
                self.deferred.append(event)
            else:
                result.append(event)
        return result

    def reset(self) -> None:
        """Drop events from a discarded player whose entry IDs can be reused."""
        self.deferred.clear()
