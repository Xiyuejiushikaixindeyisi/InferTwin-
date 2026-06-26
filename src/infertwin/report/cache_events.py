"""Streaming CSV writer for cache events."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import asdict, fields
from pathlib import Path
from types import TracebackType

from infertwin.cache.event_sink import CacheEventStats
from infertwin.cache.events import CacheEvent


CACHE_EVENT_FIELDNAMES = tuple(field.name for field in fields(CacheEvent))


class CsvCacheEventWriter:
    """Write cache events incrementally while keeping aggregate stats."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._file = None
        self._writer: csv.DictWriter | None = None
        self._stats = CacheEventStats()

    @property
    def stats(self) -> CacheEventStats:
        return self._stats

    def snapshot_stats(self) -> CacheEventStats:
        return self._stats.snapshot()

    def __enter__(self) -> CsvCacheEventWriter:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=CACHE_EVENT_FIELDNAMES)
        self._writer.writeheader()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None
        self._writer = None

    def emit_many(self, events: Iterable[CacheEvent]) -> None:
        if self._writer is None:
            raise ValueError("CsvCacheEventWriter must be opened before writing events")

        for event in events:
            self._writer.writerow(asdict(event))
            self._stats.record(event)

    def snapshot_events(self) -> tuple[CacheEvent, ...]:
        return ()
