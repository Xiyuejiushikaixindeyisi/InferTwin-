from datetime import datetime, timedelta
from pathlib import Path

import pytest

from infertwin.instance.request import SimulationRequest
from infertwin.request.block_hasher import build_prefix_blocks
from infertwin.streaming.request_codec import encode_simulation_request_line
from infertwin.streaming.source import (
    JsonlRequestSource,
    ListRequestSource,
    UnsortedRequestSourceError,
)


def test_list_request_source_peek_does_not_consume() -> None:
    first = _request("r1", start_time_ms=0.0)
    second = _request("r2", start_time_ms=1.0)
    source = ListRequestSource([first, second])

    assert source.peek() == first
    assert source.peek() == first
    assert source.pop() == first
    assert source.peek() == second
    assert source.pop() == second
    assert source.peek() is None

    with pytest.raises(IndexError, match="empty request source"):
        source.pop()


def test_list_request_source_rejects_unsorted_requests() -> None:
    source = ListRequestSource(
        [
            _request("r2", start_time_ms=2.0),
            _request("r1", start_time_ms=1.0),
        ]
    )

    assert source.pop().request_id == "r2"
    with pytest.raises(UnsortedRequestSourceError, match="request source"):
        source.pop()


def test_jsonl_request_source_reads_encoded_requests(tmp_path: Path) -> None:
    shard_path = tmp_path / "instance-a.jsonl"
    first = _request("r1", start_time_ms=0.0)
    second = _request("r2", start_time_ms=1.0)
    shard_path.write_text(
        "\n".join(
            [
                encode_simulation_request_line(first),
                encode_simulation_request_line(second),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with JsonlRequestSource(shard_path) as source:
        assert source.peek() == first
        assert source.pop() == first
        assert source.pop() == second
        assert source.peek() is None


def test_jsonl_request_source_reports_line_number_for_invalid_json(tmp_path: Path) -> None:
    shard_path = tmp_path / "instance-a.jsonl"
    shard_path.write_text("{\n", encoding="utf-8")

    with JsonlRequestSource(shard_path) as source:
        with pytest.raises(ValueError, match="line 1"):
            source.peek()


def _request(request_id: str, *, start_time_ms: float) -> SimulationRequest:
    token_ids = [1, 2, 3, 4]
    service_start_time = datetime.fromisoformat("2026-06-05 09:01:23") + timedelta(
        milliseconds=start_time_ms
    )
    blocks = build_prefix_blocks(
        token_ids=token_ids,
        block_size_tokens=4,
        model="glm-v5",
        tenant_id="tenant-a",
        kv_bytes_per_token=1,
    )
    return SimulationRequest(
        request_id=request_id,
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        model="glm-v5",
        service_start_time=service_start_time,
        start_time_ms=start_time_ms,
        tokenizer_profile="glm-v5",
        prompt_tokens=len(token_ids),
        prompt_blocks=tuple(blocks),
        kv_bytes_per_token=1,
    )
