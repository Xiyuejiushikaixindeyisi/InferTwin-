from dataclasses import replace
from datetime import datetime
import json

import pytest

from infertwin.cache.cache_block_conversion import CacheBlockConversionResult
from infertwin.instance.request import SimulationRequest
from infertwin.request.block_hasher import PrefixBlock
from infertwin.streaming.request_codec import (
    STREAMING_REQUEST_SCHEMA_VERSION,
    decode_simulation_request,
    decode_simulation_request_line,
    encode_simulation_request,
    encode_simulation_request_line,
)


def test_simulation_request_json_roundtrip_preserves_replay_fields() -> None:
    request = _request()

    decoded = decode_simulation_request(encode_simulation_request(request))

    assert decoded == request
    assert decoded.prompt_blocks[0].block_key == "block-key-0"
    assert decoded.block_conversion_result is not None
    assert decoded.block_conversion_result.cached_tokens == 128


def test_simulation_request_line_roundtrip_is_jsonl_compatible() -> None:
    request = _request()

    line = encode_simulation_request_line(request)
    decoded = decode_simulation_request_line(line)

    assert line.endswith("}") is True
    assert "\n" not in line
    assert json.loads(line)["schema_version"] == STREAMING_REQUEST_SCHEMA_VERSION
    assert decoded == request


def test_simulation_request_codec_supports_missing_block_conversion() -> None:
    request = replace(_request(), block_conversion_result=None)

    decoded = decode_simulation_request(encode_simulation_request(request))

    assert decoded.block_conversion_result is None
    assert decoded == request


def test_simulation_request_decode_rejects_schema_mismatch() -> None:
    encoded = encode_simulation_request(_request())
    encoded["schema_version"] = "wrong"

    with pytest.raises(ValueError, match="schema_version"):
        decode_simulation_request(encoded)


def test_simulation_request_decode_rejects_missing_required_field() -> None:
    encoded = encode_simulation_request(_request())
    del encoded["request_id"]

    with pytest.raises(ValueError, match="request_id"):
        decode_simulation_request(encoded)


def test_simulation_request_decode_rejects_invalid_json_line() -> None:
    with pytest.raises(ValueError, match="invalid streaming request JSON"):
        decode_simulation_request_line("{")


def test_simulation_request_decode_rejects_invalid_prompt_block() -> None:
    encoded = encode_simulation_request(_request())
    encoded["prompt_blocks"] = ["not-a-block"]

    with pytest.raises(ValueError, match="prompt_blocks"):
        decode_simulation_request(encoded)


def _request() -> SimulationRequest:
    return SimulationRequest(
        request_id="request-a",
        tenant_id="tenant-a",
        instance_uuid="instance-a",
        model="glm-v5",
        service_start_time=datetime(2026, 6, 5, 9, 1, 23),
        start_time_ms=1_780_000_000_000.0,
        tokenizer_profile="glm-v5",
        prompt_tokens=257,
        prompt_blocks=(
            PrefixBlock(
                block_key="block-key-0",
                content_hash="content-hash-0",
                block_index=0,
                token_count=128,
                size_bytes=1024,
            ),
            PrefixBlock(
                block_key="block-key-1",
                content_hash="content-hash-1",
                block_index=1,
                token_count=128,
                size_bytes=1024,
            ),
        ),
        kv_bytes_per_token=8,
        requested_block_size=128,
        runtime_block_size=128,
        effective_block_size=128,
        block_conversion_result=CacheBlockConversionResult(
            requested_block_size=128,
            runtime_block_size=128,
            effective_block_size=128,
            max_cache_hit_length=256,
            max_matchable_blocks=2,
            matched_blocks=1,
            speculative_drop_blocks=0,
            cached_blocks=1,
            cached_tokens=128,
        ),
    )
