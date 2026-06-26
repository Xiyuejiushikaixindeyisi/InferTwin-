"""Waiting queue abstraction used by batch-aware replay."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from infertwin.scheduler.state import RequestState


class WaitingQueue:
    """FIFO queue with logical index access for bounded frontier scans.

    Replay needs FIFO admission, but it also scans a bounded waiting frontier by
    index before scheduling. A plain list makes head pops O(n); this queue keeps
    an internal head offset so the common `popleft()` path is O(1).
    """

    _COMPACT_HEAD_THRESHOLD = 64

    def __init__(self, states: Iterable[RequestState] = ()) -> None:
        self._items = list(states)
        self._head = 0

    def append(self, state: RequestState) -> None:
        self._items.append(state)

    def popleft(self) -> RequestState:
        if not self:
            raise IndexError("popleft from empty WaitingQueue")
        state = self._items[self._head]
        self._head += 1
        self._compact_if_needed()
        return state

    def pop(self, index: int = 0) -> RequestState:
        logical_index = self._normalize_index(index)
        if logical_index == 0:
            return self.popleft()

        physical_index = self._head + logical_index
        state = self._items.pop(physical_index)
        self._compact_if_needed()
        return state

    def __len__(self) -> int:
        return len(self._items) - self._head

    def __bool__(self) -> bool:
        return len(self) > 0

    def __iter__(self) -> Iterator[RequestState]:
        return iter(self._items[self._head :])

    def __getitem__(self, index: int) -> RequestState:
        logical_index = self._normalize_index(index)
        return self._items[self._head + logical_index]

    def _normalize_index(self, index: int) -> int:
        length = len(self)
        logical_index = index
        if logical_index < 0:
            logical_index += length
        if logical_index < 0 or logical_index >= length:
            raise IndexError("WaitingQueue index out of range")
        return logical_index

    def _compact_if_needed(self) -> None:
        if self._head < self._COMPACT_HEAD_THRESHOLD:
            return
        if self._head * 2 < len(self._items):
            return
        self._items = self._items[self._head :]
        self._head = 0
