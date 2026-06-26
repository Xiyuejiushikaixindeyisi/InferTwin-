"""Internal request objects used by replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hitfloor.request.block_hasher import PrefixBlock, build_prefix_blocks
from hitfloor.request.parser import parse_request_params
from hitfloor.request.tokenizer_registry import TokenizerRegistry
from hitfloor.trace.schema import TraceRecord


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    request_id: str
    tenant_id: str
    instance_uuid: str
    model: str
    service_start_time: datetime
    start_time_ms: float
    tokenizer_profile: str
    prompt_tokens: int
    prompt_blocks: tuple[PrefixBlock, ...]
    kv_bytes_per_token: int | None


def build_simulation_request(
    record: TraceRecord,
    tokenizer_registry: TokenizerRegistry,
    block_size_tokens: int,
    cache_scope: str = "tenant_isolated",
) -> SimulationRequest:
    parsed = parse_request_params(record.request_params)
    tokenization = tokenizer_registry.encode(parsed)
    blocks = build_prefix_blocks(
        token_ids=tokenization.prompt_token_ids,
        block_size_tokens=block_size_tokens,
        model=parsed.model,
        tenant_id=record.tenant_id,
        kv_bytes_per_token=tokenization.kv_bytes_per_token,
        cache_scope=cache_scope,
    )
    return SimulationRequest(
        request_id=record.request_id,
        tenant_id=record.tenant_id,
        instance_uuid=record.instance_uuid,
        model=parsed.model,
        service_start_time=record.service_start_time,
        start_time_ms=_datetime_to_ms(record.service_start_time),
        tokenizer_profile=tokenization.tokenizer_profile,
        prompt_tokens=tokenization.prompt_tokens,
        prompt_blocks=tuple(blocks),
        kv_bytes_per_token=tokenization.kv_bytes_per_token,
    )


def build_simulation_requests(
    records: list[TraceRecord],
    tokenizer_registry: TokenizerRegistry,
    block_size_tokens: int,
    cache_scope: str = "tenant_isolated",
) -> list[SimulationRequest]:
    requests = [
        build_simulation_request(
            record,
            tokenizer_registry=tokenizer_registry,
            block_size_tokens=block_size_tokens,
            cache_scope=cache_scope,
        )
        for record in records
    ]
    return sorted(
        requests,
        key=lambda request: (
            request.service_start_time,
            request.instance_uuid,
            request.request_id,
        ),
    )


def _datetime_to_ms(value: datetime) -> float:
    return value.timestamp() * 1000.0
