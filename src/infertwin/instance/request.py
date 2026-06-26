"""Internal request objects used by replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from infertwin.cache.cache_block_conversion import CacheBlockConversionResult
from infertwin.request.build_context import RequestBuildContext
from infertwin.request.block_hasher import PrefixBlock, build_prefix_blocks
from infertwin.request.parser import parse_request_params
from infertwin.request.tokenizer_registry import TokenizerRegistry
from infertwin.trace.schema import TraceRecord


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
    requested_block_size: int | None = None
    runtime_block_size: int | None = None
    effective_block_size: int | None = None
    block_conversion_result: CacheBlockConversionResult | None = None


def build_simulation_request(
    record: TraceRecord,
    tokenizer_registry: TokenizerRegistry,
    block_size_tokens: int,
    cache_scope: str = "tenant_isolated",
    build_context: RequestBuildContext | None = None,
) -> SimulationRequest:
    context = build_context or RequestBuildContext.legacy(block_size_tokens)
    parsed = parse_request_params(record.request_params)
    context.validate_request_model(parsed.model)
    tokenization = tokenizer_registry.encode(
        parsed,
        max_prompt_tokens=context.max_prompt_tokens,
    )
    block_conversion = context.calculate_block_conversion(tokenization.prompt_tokens)
    blocks = build_prefix_blocks(
        token_ids=tokenization.prompt_token_ids,
        block_size_tokens=context.effective_block_size,
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
        requested_block_size=context.requested_block_size,
        runtime_block_size=context.runtime_block_size,
        effective_block_size=context.effective_block_size,
        block_conversion_result=block_conversion,
    )


def build_simulation_requests(
    records: list[TraceRecord],
    tokenizer_registry: TokenizerRegistry,
    block_size_tokens: int,
    cache_scope: str = "tenant_isolated",
    build_context: RequestBuildContext | None = None,
) -> list[SimulationRequest]:
    requests = [
        build_simulation_request(
            record,
            tokenizer_registry=tokenizer_registry,
            block_size_tokens=block_size_tokens,
            cache_scope=cache_scope,
            build_context=build_context,
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
