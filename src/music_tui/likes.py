"""Account-scoped search like states and serialized, confirmed library changes."""

import asyncio

from .models import Track
from .sources import MusicSource


class SearchLikes:
    """Keep unknown states explicit and prevent old accounts from changing the view."""

    def __init__(self, source: MusicSource):
        """Bind the initial source without issuing account requests."""
        self.source = source
        self.account_id = source.account_id
        self._generation = 0
        self._ids: set[str] | None = None
        self._lock = asyncio.Lock()
        self._pending: set[str] = set()

    def bind(self, source: MusicSource) -> None:
        """Clear confirmed states on source or account changes, invalidating old work."""
        if source is self.source and source.account_id == self.account_id:
            return
        self.source = source
        self.account_id = source.account_id
        self._generation += 1
        self._ids = None
        self._lock = asyncio.Lock()
        self._pending = set()

    def state(self, track: Track) -> bool | None:
        """Return the current account's confirmed state, or None while unknown."""
        self.bind(self.source)
        return None if self._ids is None else track.mid in self._ids

    def _current(self, generation: int) -> bool:
        """Reject work whose source or mutable account identity has changed."""
        self.bind(self.source)
        return generation == self._generation

    def invalidate(self) -> None:
        """Mark cached confirmations unknown before a fresh account query begins."""
        self._ids = None

    async def refresh(self) -> bool:
        """Invalidate old confirmations, retaining unknown state if the query fails."""
        self.bind(self.source)
        self.invalidate()
        if not self.account_id:
            return False
        generation, source, lock = self._generation, self.source, self._lock
        async with lock:
            if not self._current(generation):
                return False
            self.invalidate()
            ids = await source.liked_ids()
            if not self._current(generation):
                return False
            self._ids = ids
            return True

    async def toggle(self, track: Track) -> bool | None:
        """Query unknown state before writing; ignore duplicates and obsolete results."""
        self.bind(self.source)
        if not self.account_id:
            raise ValueError("请先按 L 扫码登录")
        if track.mid in self._pending:
            return None
        generation, source, lock = self._generation, self.source, self._lock
        pending = self._pending
        pending.add(track.mid)
        try:
            async with lock:
                if not self._current(generation):
                    return None
                if self._ids is None:
                    ids = await source.liked_ids()
                    if not self._current(generation):
                        return None
                    self._ids = ids
                ids = self._ids
                liked = track.mid not in ids
                await source.set_liked(track, liked)
                if not self._current(generation):
                    return None
                if self._ids is ids:
                    if liked:
                        ids.add(track.mid)
                    else:
                        ids.discard(track.mid)
                return liked
        finally:
            pending.discard(track.mid)
