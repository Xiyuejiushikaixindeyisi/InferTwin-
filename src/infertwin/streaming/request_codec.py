"""JSON-compatible codec for streaming SimulationRequest shards."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from infertwin.cache.cache_block_conversion import CacheBlockConversionResult
from infertwin.instance.request import SimulationRequest
from infertwin.request.block_hasher import PrefixBlock

STREAMING_REQUEST_SCHEMA_VERSION = "infertwin.streaming.request.v1"


def encode_simulation_request(request: SimulationRequest) -> dict[str, Any]:
    """Encode a request into a stable JSON-compatible shard record."""

    return {
        "schema_version": STREAMING_REQUEST_SCHEMA_VERSION,
        "request_id": request.request_id,
        "tenant_id": request.tenant_id,
        "instance_uuid": request.instance_uuid,
        "model": request.model,
        "service_start_time": request.service_start_time.isoformat(),
        "start_time_ms": request.start_time_ms,
        "tokenizer_profile": request.tokenizer_profile,
        "prompt_tokens": request.prompt_tokens,
        "prompt_blocks": [_encode_prefix_block(block) for block in request.prompt_blocks],
        "kv_bytes_per_token": request.kv_bytes_per_token,
        "requested_block_size": request.requested_block_size,
        "runtime_block_size": request.runtime_block_size,
        "effective_block_size": request.effective_block_size,
        "block_conversion_result": _encode_block_conversion_result(request.block_conversion_result),
    }


def decode_simulation_request(record: dict[str, Any]) -> SimulationRequest:
    """Decode a shard record into a SimulationRequest."""

    _require_schema_version(record)
    return SimulationRequest(
        request_id=_required_str(record, "request_id"),
        tenant_id=_required_str(record, "tenant_id"),
        instance_uuid=_required_str(record, "instance_uuid"),
        model=_required_str(record, "model"),
        service_start_time=_decode_datetime(record, "service_start_time"),
        start_time_ms=_required_number(record, "start_time_ms"),
        tokenizer_profile=_required_str(record, "tokenizer_profile"),
        prompt_tokens=_required_int(record, "prompt_tokens"),
        prompt_blocks=tuple(
            _decode_prefix_block(item) for item in _required_list(record, "prompt_blocks")
        ),
        kv_bytes_per_token=_optional_int(record, "kv_bytes_per_token"),
        requested_block_size=_optional_int(record, "requested_block_size"),
        runtime_block_size=_optional_int(record, "runtime_block_size"),
        effective_block_size=_optional_int(record, "effective_block_size"),
        block_conversion_result=_decode_block_conversion_result(
            record.get("block_conversion_result")
        ),
    )


def encode_simulation_request_line(request: SimulationRequest) -> str:
    """Encode a request as one deterministic JSONL line."""

    return json.dumps(
        encode_simulation_request(request),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_simulation_request_line(line: str) -> SimulationRequest:
    """Decode one JSONL line from a streaming shard."""

    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid streaming request JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise ValueError("streaming request line must decode to a mapping")
    return decode_simulation_request(record)


def _encode_prefix_block(block: PrefixBlock) -> dict[str, Any]:
    return {
        "block_key": block.block_key,
        "content_hash": block.content_hash,
        "block_index": block.block_index,
        "token_count": block.token_count,
        "size_bytes": block.size_bytes,
    }


def _decode_prefix_block(record: object) -> PrefixBlock:
    if not isinstance(record, dict):
        raise ValueError("prompt_blocks[] must be a mapping")
    return PrefixBlock(
        block_key=_required_str(record, "block_key"),
        content_hash=_required_str(record, "content_hash"),
        block_index=_required_int(record, "block_index"),
        token_count=_required_int(record, "token_count"),
        size_bytes=_required_int(record, "size_bytes"),
    )


def _encode_block_conversion_result(
    result: CacheBlockConversionResult | None,
) -> dict[str, Any] | None:
    if result is None:
        return None
    return asdict(result)


def _decode_block_conversion_result(
    value: object,
) -> CacheBlockConversionResult | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("block_conversion_result must be a mapping or null")
    return CacheBlockConversionResult(
        requested_block_size=_required_int(value, "requested_block_size"),
        runtime_block_size=_required_int(value, "runtime_block_size"),
        effective_block_size=_required_int(value, "effective_block_size"),
        max_cache_hit_length=_required_int(value, "max_cache_hit_length"),
        max_matchable_blocks=_required_int(value, "max_matchable_blocks"),
        matched_blocks=_required_int(value, "matched_blocks"),
        speculative_drop_blocks=_required_int(value, "speculative_drop_blocks"),
        cached_blocks=_required_int(value, "cached_blocks"),
        cached_tokens=_required_int(value, "cached_tokens"),
        unsupported_reason=_optional_str(value, "unsupported_reason"),
    )


def _require_schema_version(record: dict[str, Any]) -> None:
    schema_version = record.get("schema_version")
    if schema_version != STREAMING_REQUEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported streaming request schema_version {schema_version!r}")


def _decode_datetime(record: dict[str, Any], key: str) -> datetime:
    value = _required_str(record, key)
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO datetime") from exc


def _required_str(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_str(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value


def _required_int(record: dict[str, Any], key: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(record: dict[str, Any], key: str) -> int | None:
    value = record.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer when provided")
    return value


def _required_number(record: dict[str, Any], key: str) -> float:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number")
    return float(value)


def _required_list(record: dict[str, Any], key: str) -> list[object]:
    value = record.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value
