from pathlib import Path

import pytest

from infertwin.streaming.manifest import (
    RequestShard,
    StreamingBuildManifest,
    STREAMING_MANIFEST_SCHEMA_VERSION,
)


def test_streaming_manifest_validates_counts() -> None:
    manifest = StreamingBuildManifest(
        schema_version=STREAMING_MANIFEST_SCHEMA_VERSION,
        trace_path=Path("trace.csv"),
        shard_root=Path("shards"),
        shards=(
            RequestShard(
                instance_uuid="instance-a",
                path=Path("shards/instance-a.jsonl"),
                request_count=2,
                min_start_time_ms=1.0,
                max_start_time_ms=3.0,
            ),
            RequestShard(
                instance_uuid="instance-b",
                path=Path("shards/instance-b.jsonl"),
                request_count=1,
                min_start_time_ms=2.0,
                max_start_time_ms=2.0,
            ),
        ),
        accepted_count=3,
        rejected_count=1,
        require_sorted_trace=True,
    )

    assert manifest.accepted_count == 3
    assert manifest.shards[0].instance_uuid == "instance-a"


def test_streaming_manifest_rejects_schema_mismatch() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        StreamingBuildManifest(
            schema_version="wrong",
            trace_path=Path("trace.csv"),
            shard_root=Path("shards"),
            shards=(),
            accepted_count=0,
            rejected_count=0,
            require_sorted_trace=True,
        )


def test_streaming_manifest_rejects_count_mismatch() -> None:
    with pytest.raises(ValueError, match="accepted_count"):
        StreamingBuildManifest(
            schema_version=STREAMING_MANIFEST_SCHEMA_VERSION,
            trace_path=Path("trace.csv"),
            shard_root=Path("shards"),
            shards=(
                RequestShard(
                    instance_uuid="instance-a",
                    path=Path("shards/instance-a.jsonl"),
                    request_count=1,
                    min_start_time_ms=1.0,
                    max_start_time_ms=1.0,
                ),
            ),
            accepted_count=2,
            rejected_count=0,
            require_sorted_trace=True,
        )


def test_request_shard_validates_time_bounds() -> None:
    with pytest.raises(ValueError, match="min_start_time_ms"):
        RequestShard(
            instance_uuid="instance-a",
            path=Path("shards/instance-a.jsonl"),
            request_count=1,
            min_start_time_ms=3.0,
            max_start_time_ms=1.0,
        )
