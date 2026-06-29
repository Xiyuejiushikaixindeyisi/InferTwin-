import pytest

from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.kv_load import TokenLinearKVLoadLatencyComponent
from infertwin.latency.profile import (
    ServingLatencyProfile,
    StaticLatencyComponent,
    ZeroLatencyComponent,
)
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_serving_latency_profile_composes_iteration_components() -> None:
    profile = ServingLatencyProfile(
        profile="glm-v5_ascend910c_serving_v1",
        ttft_backend=FittedTTFTLatencyBackend(
            intercept_ms=1.0,
            ms_per_uncached_token=0.5,
            model_name="glm-v5",
            hardware_name="ascend910c",
            profile="glm-v5_ascend910c_ttft",
            calibrated_from="unit-test-fit",
        ),
        queue_component=StaticLatencyComponent(name="queue", duration_ms=2.0),
        kv_load_component=StaticLatencyComponent(name="kv_load", duration_ms=3.0),
        calibrated_from="unit-test-profile",
        calibration_window_requests=128,
    )

    result = profile.estimate_iteration(_shape([8]))

    assert result.duration_ms == 10.0
    assert result.backend == "serving_latency_profile"
    assert result.shape_key.backend == "serving_latency_profile"
    assert result.shape_key.model_name == "glm-v5"
    assert result.shape_key.hardware_name == "ascend910c"
    assert result.details["profile"] == "glm-v5_ascend910c_serving_v1"
    assert result.details["ttft_backend"] == "fitted_ttft"
    assert result.details["ttft_ms"] == 5.0
    assert result.details["queue_ms"] == 2.0
    assert result.details["kv_load_ms"] == 3.0
    assert result.details["decode_mode"] == "not_modeled_in_current_replay"
    assert result.details["tpot_mode"] == "not_modeled_in_current_replay"
    assert result.details["ttft_calibrated_from"] == "unit-test-fit"


def test_serving_latency_profile_defaults_queue_and_kv_load_to_zero() -> None:
    profile = ServingLatencyProfile(
        profile="glm-v5_ascend910c_serving_v1",
        ttft_backend=FittedTTFTLatencyBackend(
            intercept_ms=0.0,
            ms_per_uncached_token=1.0,
            model_name="glm-v5",
            hardware_name="ascend910c",
            profile="glm-v5_ascend910c_ttft",
        ),
    )

    result = profile.estimate_iteration(_shape([4]))

    assert result.duration_ms == 4.0
    assert result.details["queue_ms"] == 0.0
    assert result.details["queue_modeled"] is False
    assert result.details["kv_load_ms"] == 0.0
    assert result.details["kv_load_modeled"] is False


def test_serving_latency_profile_composes_real_kv_load_component() -> None:
    profile = ServingLatencyProfile(
        profile="glm-v5_ascend910c_serving_v1",
        ttft_backend=FittedTTFTLatencyBackend(
            intercept_ms=1.0,
            ms_per_uncached_token=0.5,
            model_name="glm-v5",
            hardware_name="ascend910c",
            profile="glm-v5_ascend910c_ttft",
        ),
        kv_load_component=TokenLinearKVLoadLatencyComponent(
            ddr_fixed_overhead_ms=2.0,
            ddr_ms_per_cached_token=0.25,
            calibrated_from="unit-test-kv-load",
        ),
    )

    result = profile.estimate_iteration(_shape([8], kv_load_tokens=16, kv_load_bytes=4096))

    assert result.duration_ms == 11.0
    assert result.details["ttft_ms"] == 5.0
    assert result.details["kv_load_ms"] == 6.0
    assert result.details["kv_load_modeled"] is True
    assert result.details["kv_load_mode"] == "token_linear_v1"
    assert result.details["kv_load_calibrated_from"] == "unit-test-kv-load"


def test_serving_latency_profile_load_only_shape_does_not_charge_ttft_intercept() -> None:
    profile = ServingLatencyProfile(
        profile="glm-v5_ascend910c_serving_v1",
        ttft_backend=FittedTTFTLatencyBackend(
            intercept_ms=99.0,
            ms_per_uncached_token=0.5,
            model_name="glm-v5",
            hardware_name="ascend910c",
            profile="glm-v5_ascend910c_ttft",
        ),
        kv_load_component=TokenLinearKVLoadLatencyComponent(
            ddr_fixed_overhead_ms=2.0,
            ddr_ms_per_cached_token=0.25,
        ),
    )

    result = profile.estimate_iteration(_load_only_shape(kv_load_tokens=16, kv_load_bytes=4096))

    assert result.duration_ms == 6.0
    assert result.details["ttft_ms"] == 0.0
    assert result.details["ttft_reason"] == "load_only_kv_load"
    assert result.details["kv_load_ms"] == 6.0


def test_serving_latency_profile_rejects_unsupported_decode_mode() -> None:
    with pytest.raises(ValueError, match="not-modeled decode mode"):
        ServingLatencyProfile(
            profile="glm-v5_ascend910c_serving_v1",
            ttft_backend=FittedTTFTLatencyBackend(
                intercept_ms=0.0,
                ms_per_uncached_token=1.0,
                model_name="glm-v5",
                hardware_name="ascend910c",
                profile="glm-v5_ascend910c_ttft",
            ),
            decode_mode="decode_aware",
        )


def test_zero_latency_component_exposes_not_modeled_reason() -> None:
    component = ZeroLatencyComponent(name="queue", reason="queue_not_modeled")

    result = component.estimate_iteration(_shape([4]))

    assert result.duration_ms == 0.0
    assert result.modeled is False
    assert result.details["reason"] == "queue_not_modeled"


def _shape(
    tokens: list[int],
    *,
    kv_load_tokens: int = 0,
    kv_load_bytes: int = 0,
) -> BatchShape:
    slices = []
    for index, scheduled_tokens in enumerate(tokens):
        slices.append(
            ScheduledSlice(
                request_id=f"r{index}",
                scheduled_prefill_tokens=scheduled_tokens,
                computed_tokens_before=0,
                computed_tokens_after=scheduled_tokens,
                prompt_tokens=scheduled_tokens,
                cached_prefix_tokens=0,
                previous_chunk_tokens=0,
                kv_load_tokens=kv_load_tokens if index == 0 else 0,
                kv_load_bytes=kv_load_bytes if index == 0 else 0,
            )
        )

    return BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=tuple(slices),
    )


def _load_only_shape(
    *,
    kv_load_tokens: int,
    kv_load_bytes: int,
) -> BatchShape:
    return BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=(
            ScheduledSlice(
                request_id="r0",
                scheduled_prefill_tokens=0,
                computed_tokens_before=16,
                computed_tokens_after=16,
                prompt_tokens=16,
                cached_prefix_tokens=16,
                previous_chunk_tokens=0,
                kv_load_tokens=kv_load_tokens,
                kv_load_bytes=kv_load_bytes,
            ),
        ),
    )
