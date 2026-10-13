"""Own source pagination, manual insertions, and nonrepeating shuffle navigation."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .models import SongPage, Track
from .playback_session import PlaybackSnapshot, PlaybackSource, QueuedTrack, RepeatMode

type FetchPage = Callable[[PlaybackSource, int], Awaitable[SongPage]]


@dataclass
class PlaybackQueue:
    """Keep playback navigation independent of the library page displayed by the UI."""

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
        self.generation += 1
        self.tracks, self.source, self.page, self.has_more = tracks.copy(), source, page, has_more
        self.index, self.origin = index, origin
        self.pending.clear()
        self.manual = None
        self.reset_shuffle(index)

    def restore(self, snapshot: PlaybackSnapshot) -> None:
        """Apply an already validated account snapshot without performing network requests."""
        self.generation += 1
        self.tracks, self.source, self.page = snapshot.queue, snapshot.source, snapshot.page
        self.has_more, self.index = snapshot.has_more, snapshot.index
        self.origin, self.pending, self.manual = (
            snapshot.queue_origin,
            snapshot.pending,
            snapshot.manual,
        )
        self.shuffle_pages = snapshot.shuffle_pages
        self.shuffle_remaining = snapshot.shuffle_remaining
        self.shuffle_history, self.shuffle_cursor = (
            snapshot.shuffle_history,
            snapshot.shuffle_cursor,
        )

    async def resolve(
        self, index: int, mode: RepeatMode, fetch: FetchPage, *, shuffle: int = 0
    ) -> Track | None:
        """Resolve list boundaries before committing the next source page and position."""
        generation = self.generation
        if shuffle:
            page, index = await self.shuffle_position(shuffle, fetch)
            tracks, more = self.shuffle_pages[page]
        else:
            tracks, page, more = self.tracks, self.page, self.has_more
        if index >= len(tracks):
            if more and self.source is not None:
                page += 1
                result = await fetch(self.source, page)
                tracks, more, index = result.tracks, result.has_more, 0
            if not tracks or index >= len(tracks):
                if mode == RepeatMode.LIST:
                    page = 1
                    if self.source is not None:
                        result = await fetch(self.source, page)
                        tracks, more = result.tracks, result.has_more
                    index = 0
        elif index < 0:
            if page > 1 and self.source is not None:
                page -= 1
                result = await fetch(self.source, page)
                tracks, more = result.tracks, result.has_more
            elif mode == RepeatMode.LIST:
                while more and self.source is not None:
                    page += 1
                    result = await fetch(self.source, page)
                    tracks, more = result.tracks, result.has_more
                    if not tracks:
                        break
            index = len(tracks) - 1 if page > 1 or mode == RepeatMode.LIST else 0
        if generation != self.generation:
            raise asyncio.CancelledError
        if not 0 <= index < len(tracks):
            self.manual = None
            self.index = len(self.tracks) - 1
            return None
        self.tracks, self.page, self.has_more, self.index = tracks, page, more, index
        self.manual = None
        return tracks[index]

    def reset_shuffle(self, index: int) -> None:
        """Seed a new random round with the selected source position."""
        self.shuffle_pages = {}
        self.shuffle_remaining = []
        self.shuffle_history = [(self.page, index)] if self.tracks else []
        self.shuffle_cursor = len(self.shuffle_history) - 1

    async def prepare_shuffle(self, fetch: FetchPage) -> None:
        """Load complete source pages, discarding results if the queue was replaced."""
        if self.shuffle_pages:
            return
        generation, source = self.generation, self.source
        pages = {self.page: (self.tracks.copy(), self.has_more)}
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
        if generation != self.generation:
            raise asyncio.CancelledError
        self.shuffle_pages = pages

    def fill_shuffle_remaining(self) -> None:
        """Choose a random round once, avoiding an immediate repeat when possible."""
        if self.shuffle_remaining:
            return
        current = (self.page, self.index)
        positions = [
            (page, index)
            for page, (tracks, _) in self.shuffle_pages.items()
            for index in range(len(tracks))
        ]
        if len(positions) > 1 and len(self.shuffle_history) == 1:
            positions.remove(current)
        random.shuffle(positions)
        if len(positions) > 1 and positions[-1] == current:
            positions[0], positions[-1] = positions[-1], positions[0]
        self.shuffle_remaining = positions

    async def shuffle_position(self, direction: int, fetch: FetchPage) -> tuple[int, int]:
        """Revisit random history or append a uniformly chosen source position."""
        await self.prepare_shuffle(fetch)
        if direction < 0:
            self.shuffle_cursor = max(0, self.shuffle_cursor - 1)
            return self.shuffle_history[self.shuffle_cursor]
        if self.shuffle_cursor + 1 < len(self.shuffle_history):
            self.shuffle_cursor += 1
            return self.shuffle_history[self.shuffle_cursor]
        self.fill_shuffle_remaining()
        position = self.shuffle_remaining.pop()
        self.shuffle_history.append(position)
        self.shuffle_cursor += 1
        return position

    def upcoming(self, mode: RepeatMode) -> tuple[list[Track], bool]:
        """Expose successors in their actual playing order without advancing navigation."""
        if mode == RepeatMode.RANDOM and self.shuffle_pages:
            positions = self.shuffle_history[self.shuffle_cursor + 1 :] + list(
                reversed(self.shuffle_remaining)
            )
            return [self.shuffle_pages[page][0][index] for page, index in positions], False
        if mode == RepeatMode.RANDOM:
            return [], self.has_more
        return self.tracks[self.index + 1 :], self.has_more


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
