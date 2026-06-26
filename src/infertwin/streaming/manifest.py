"""Schemas for streaming request shard manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

STREAMING_MANIFEST_SCHEMA_VERSION = "infertwin.streaming.manifest.v1"


@dataclass(frozen=True, slots=True)
class RequestShard:
    """One sorted shard of serialized requests for a single instance."""

    instance_uuid: str
    path: Path
    request_count: int
    min_start_time_ms: float | None
    max_start_time_ms: float | None

    def __post_init__(self) -> None:
        if not self.instance_uuid:
            raise ValueError("instance_uuid must be a non-empty string")
        if self.request_count < 0:
            raise ValueError("request_count must be non-negative")
        if (
            self.min_start_time_ms is not None
            and self.max_start_time_ms is not None
            and self.min_start_time_ms > self.max_start_time_ms
        ):
            raise ValueError("min_start_time_ms must be <= max_start_time_ms")


@dataclass(frozen=True, slots=True)
class StreamingBuildManifest:
    """Manifest for a streaming request build output directory."""

    schema_version: str
    trace_path: Path
    shard_root: Path
    shards: tuple[RequestShard, ...]
    accepted_count: int
    rejected_count: int
    require_sorted_trace: bool

    def __post_init__(self) -> None:
        if self.schema_version != STREAMING_MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported streaming manifest schema_version {self.schema_version!r}"
            )
        if self.accepted_count < 0:
            raise ValueError("accepted_count must be non-negative")
        if self.rejected_count < 0:
            raise ValueError("rejected_count must be non-negative")
        shard_total = sum(shard.request_count for shard in self.shards)
        if shard_total != self.accepted_count:
            raise ValueError("accepted_count must equal sum(shard.request_count)")
