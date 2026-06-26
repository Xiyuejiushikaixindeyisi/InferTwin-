"""Streaming writers for accepted request shards."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TextIO

from infertwin.instance.request import SimulationRequest
from infertwin.streaming.manifest import RequestShard
from infertwin.streaming.request_codec import encode_simulation_request_line


@dataclass(slots=True)
class _ShardState:
    path: Path
    request_count: int = 0
    min_start_time_ms: float | None = None
    max_start_time_ms: float | None = None

    def record(self, request: SimulationRequest) -> None:
        start_time_ms = request.start_time_ms
        if self.min_start_time_ms is None or start_time_ms < self.min_start_time_ms:
            self.min_start_time_ms = start_time_ms
        if self.max_start_time_ms is None or start_time_ms > self.max_start_time_ms:
            self.max_start_time_ms = start_time_ms
        self.request_count += 1


class StreamingRequestShardStore:
    """Write accepted requests into one JSONL shard per instance."""

    def __init__(self, shard_root: str | Path) -> None:
        self.shard_root = Path(shard_root)
        self._files: dict[str, TextIO] = {}
        self._states: dict[str, _ShardState] = {}

    def __enter__(self) -> StreamingRequestShardStore:
        self.shard_root.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def write(self, request: SimulationRequest) -> None:
        file = self._file_for_instance(request.instance_uuid)
        file.write(encode_simulation_request_line(request))
        file.write("\n")
        self._states[request.instance_uuid].record(request)

    def close(self) -> None:
        for file in self._files.values():
            file.close()
        self._files = {}

    def build_shards(self) -> tuple[RequestShard, ...]:
        return tuple(
            RequestShard(
                instance_uuid=instance_uuid,
                path=state.path,
                request_count=state.request_count,
                min_start_time_ms=state.min_start_time_ms,
                max_start_time_ms=state.max_start_time_ms,
            )
            for instance_uuid, state in sorted(self._states.items())
        )

    def _file_for_instance(self, instance_uuid: str) -> TextIO:
        file = self._files.get(instance_uuid)
        if file is not None:
            return file

        path = shard_path_for_instance(self.shard_root, instance_uuid)
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("w", encoding="utf-8", newline="")
        self._files[instance_uuid] = file
        self._states[instance_uuid] = _ShardState(path=path)
        return file


def shard_path_for_instance(shard_root: str | Path, instance_uuid: str) -> Path:
    """Return a stable, filesystem-safe shard path for one instance."""

    if not instance_uuid:
        raise ValueError("instance_uuid must be a non-empty string")
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance_uuid).strip("._")
    if not sanitized:
        sanitized = "instance"
    digest = hashlib.sha256(instance_uuid.encode("utf-8")).hexdigest()[:12]
    return Path(shard_root) / f"{sanitized}-{digest}.jsonl"
