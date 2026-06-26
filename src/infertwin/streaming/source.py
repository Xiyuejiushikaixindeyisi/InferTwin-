"""Request sources for streaming replay."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Protocol, TextIO

from infertwin.instance.request import SimulationRequest
from infertwin.streaming.request_codec import decode_simulation_request_line


class RequestSource(Protocol):
    """Peekable source of replay-ready SimulationRequest objects."""

    def peek(self) -> SimulationRequest | None:
        """Return the next request without consuming it."""

    def pop(self) -> SimulationRequest:
        """Consume and return the next request."""


class ListRequestSource:
    """RequestSource backed by an in-memory iterable, primarily for tests."""

    def __init__(
        self,
        requests: Iterable[SimulationRequest],
        *,
        require_sorted: bool = True,
    ) -> None:
        self._requests = tuple(requests)
        self._index = 0
        self._last_emitted_key: tuple[float, str] | None = None
        self._require_sorted = require_sorted

    def peek(self) -> SimulationRequest | None:
        if self._index >= len(self._requests):
            return None
        return self._requests[self._index]

    def pop(self) -> SimulationRequest:
        request = self.peek()
        if request is None:
            raise IndexError("pop from empty request source")
        self._index += 1
        self._record_emitted(request)
        return request

    def _record_emitted(self, request: SimulationRequest) -> None:
        key = _source_sort_key(request)
        if self._require_sorted and self._last_emitted_key is not None:
            _guard_sorted(
                previous_key=self._last_emitted_key,
                current_key=key,
            )
        self._last_emitted_key = key


class JsonlRequestSource:
    """RequestSource backed by a JSONL request shard."""

    def __init__(
        self,
        path: str | Path,
        *,
        require_sorted: bool = True,
    ) -> None:
        self._path = Path(path)
        self._require_sorted = require_sorted
        self._file: TextIO | None = None
        self._next_request: SimulationRequest | None = None
        self._exhausted = False
        self._line_number = 0
        self._last_emitted_key: tuple[float, str] | None = None

    def __enter__(self) -> JsonlRequestSource:
        self._open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def peek(self) -> SimulationRequest | None:
        if self._next_request is None and not self._exhausted:
            self._next_request = self._read_next()
        return self._next_request

    def pop(self) -> SimulationRequest:
        request = self.peek()
        if request is None:
            raise IndexError("pop from empty request source")
        self._next_request = None
        self._record_emitted(request)
        return request

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None

    def _open(self) -> None:
        if self._file is None:
            self._file = self._path.open("r", encoding="utf-8")

    def _read_next(self) -> SimulationRequest | None:
        self._open()
        assert self._file is not None
        line = self._file.readline()
        if line == "":
            self._exhausted = True
            return None
        self._line_number += 1
        try:
            return decode_simulation_request_line(line)
        except ValueError as exc:
            raise ValueError(
                f"{self._path}: invalid request shard line {self._line_number}: {exc}"
            ) from exc

    def _record_emitted(self, request: SimulationRequest) -> None:
        key = _source_sort_key(request)
        if self._require_sorted and self._last_emitted_key is not None:
            try:
                _guard_sorted(
                    previous_key=self._last_emitted_key,
                    current_key=key,
                )
            except UnsortedRequestSourceError as exc:
                raise UnsortedRequestSourceError(
                    f"{self._path}: line {self._line_number}: {exc}"
                ) from exc
        self._last_emitted_key = key


class UnsortedRequestSourceError(ValueError):
    """Raised when a request source is not sorted by replay order."""


def _source_sort_key(request: SimulationRequest) -> tuple[float, str]:
    return (request.start_time_ms, request.request_id)


def _guard_sorted(
    *,
    previous_key: tuple[float, str],
    current_key: tuple[float, str],
) -> None:
    if current_key >= previous_key:
        return
    raise UnsortedRequestSourceError(
        "request source must be sorted by (start_time_ms, request_id); "
        f"previous_key={previous_key}, current_key={current_key}"
    )
