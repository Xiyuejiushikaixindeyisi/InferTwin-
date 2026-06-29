import pytest

from infertwin.config.profiles import KVLoadLatencyProfile
from infertwin.latency.kv_load import (
    ByteLinearKVLoadLatencyComponent,
    TokenLinearKVLoadLatencyComponent,
    ZeroKVLoadLatencyComponent,
    build_kv_load_component,
)
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_zero_kv_load_component_returns_unmodeled_zero() -> None:
    result = ZeroKVLoadLatencyComponent().estimate_iteration(
        _shape(kv_load_tokens=16, kv_load_bytes=4096)
    )

    assert result.duration_ms == 0.0
    assert result.modeled is False
    assert result.details["mode"] == "zero"
    assert result.details["load_active"] is True


def test_token_linear_kv_load_returns_zero_when_shape_has_no_load() -> None:
    result = TokenLinearKVLoadLatencyComponent(
        ddr_fixed_overhead_ms=2.0,
        ddr_ms_per_cached_token=0.5,
    ).estimate_iteration(_shape(kv_load_tokens=0, kv_load_bytes=0))

    assert result.duration_ms == 0.0
    assert result.modeled is True
    assert result.details["load_active"] is False


def test_token_linear_kv_load_increases_with_tokens() -> None:
    result = TokenLinearKVLoadLatencyComponent(
        ddr_fixed_overhead_ms=2.0,
        ddr_ms_per_cached_token=0.5,
        calibrated_from="unit-test",
    ).estimate_iteration(_shape(kv_load_tokens=16, kv_load_bytes=4096))

    assert result.duration_ms == 10.0
    assert result.details["mode"] == "token_linear_v1"
    assert result.details["aggregation"] == "shared_link_sum"
    assert result.details["overlap_mode"] == "none_v1"
    assert result.details["calibrated_from"] == "unit-test"
    assert result.details["kv_load_tokens"] == 16
    assert result.details["ddr_ms_per_cached_token"] == 0.5


def test_byte_linear_kv_load_increases_with_bytes() -> None:
    result = ByteLinearKVLoadLatencyComponent(
        ddr_fixed_overhead_ms=1.0,
        ddr_ms_per_byte=0.25,
    ).estimate_iteration(_shape(kv_load_tokens=16, kv_load_bytes=40))

    assert result.duration_ms == 11.0
    assert result.details["mode"] == "byte_linear_v1"
    assert result.details["kv_load_bytes"] == 40
    assert result.details["ddr_ms_per_byte"] == 0.25


def test_byte_linear_kv_load_requires_bytes_when_tokens_are_loaded() -> None:
    component = ByteLinearKVLoadLatencyComponent(ddr_ms_per_byte=0.25)

    with pytest.raises(ValueError, match="requires kv_load_bytes"):
        component.estimate_iteration(_shape(kv_load_tokens=16, kv_load_bytes=0))


def test_kv_load_component_rejects_unsupported_aggregation_and_overlap() -> None:
    with pytest.raises(ValueError, match="aggregation"):
        TokenLinearKVLoadLatencyComponent(aggregation="per_request_parallel_max")

    with pytest.raises(ValueError, match="overlap_mode"):
        TokenLinearKVLoadLatencyComponent(overlap_mode="max_compute_or_load_v1")


def test_kv_load_builder_rejects_remote_coefficient() -> None:
    profile = KVLoadLatencyProfile.from_mapping(
        {
            "mode": "token_linear_v1",
            "ddr_ms_per_cached_token": 0.1,
            "remote_ms_per_cached_token": 0.2,
        },
        field_name="kv_load",
    )

    with pytest.raises(ValueError, match="remote_ms_per_cached_token"):
        build_kv_load_component(profile)


def test_kv_load_builder_builds_mode_specific_components() -> None:
    zero = build_kv_load_component(KVLoadLatencyProfile.from_mapping(None, field_name="kv_load"))
    token = build_kv_load_component(
        KVLoadLatencyProfile.from_mapping(
            {"mode": "token_linear_v1", "ddr_ms_per_cached_token": 0.1},
            field_name="kv_load",
        )
    )
    byte = build_kv_load_component(
        KVLoadLatencyProfile.from_mapping(
            {"mode": "byte_linear_v1", "ddr_ms_per_byte": 0.01},
            field_name="kv_load",
        )
    )

    assert isinstance(zero, ZeroKVLoadLatencyComponent)
    assert isinstance(token, TokenLinearKVLoadLatencyComponent)
    assert isinstance(byte, ByteLinearKVLoadLatencyComponent)


def _shape(*, kv_load_tokens: int, kv_load_bytes: int) -> BatchShape:
    scheduled_tokens = 8
    return BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            ScheduledSlice(
                request_id="r1",
                scheduled_prefill_tokens=scheduled_tokens,
                computed_tokens_before=0,
                computed_tokens_after=scheduled_tokens,
                prompt_tokens=scheduled_tokens,
                cached_prefix_tokens=0,
                previous_chunk_tokens=0,
                kv_load_tokens=kv_load_tokens,
                kv_load_bytes=kv_load_bytes,
            ),
        ),
    )
