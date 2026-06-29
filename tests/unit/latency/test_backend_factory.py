import pytest

from infertwin.latency.factory import build_batch_latency_backend
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.latency.profile import ServingLatencyProfile
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_factory_builds_fitted_ttft_backend() -> None:
    backend = build_batch_latency_backend(
        {
            "latency": {
                "backend": "fitted_ttft",
                "model_name": "glm-v5",
                "hardware_name": "ascend910c",
                "fitted_ttft": {
                    "profile": "glm-v5_ascend910c_default",
                    "function": "token_linear_v1",
                    "intercept_ms": 1.0,
                    "ms_per_uncached_token": 0.5,
                    "calibrated_from": "unit-test",
                },
            }
        }
    )

    assert isinstance(backend, FittedTTFTLatencyBackend)
    assert backend.name == "fitted_ttft"
    assert backend.profile == "glm-v5_ascend910c_default"


def test_factory_builds_formula_backend_for_existing_test_path() -> None:
    backend = build_batch_latency_backend(
        {
            "latency": {
                "backend": "formula",
                "model_name": "glm-v5",
                "hardware_name": "local-dev",
                "formula": {
                    "iteration_fixed_overhead_ms": 0.0,
                    "iteration_prefill_token_ms": 1.0,
                    "iteration_batch_overhead_ms": 0.0,
                    "iteration_context_token_ms": 0.0,
                },
            }
        }
    )

    assert isinstance(backend, FormulaLatencyBackend)
    assert backend.name == "formula"


def test_factory_builds_serving_latency_profile_backend() -> None:
    backend = build_batch_latency_backend(
        {
            "latency": {
                "backend": "serving_latency_profile",
                "model_name": "glm-v5",
                "hardware_name": "ascend910c",
                "serving_latency_profile": {
                    "profile": "glm-v5_ascend910c_serving_v1",
                    "ttft_backend": "fitted_ttft",
                    "calibrated_from": "unit-test",
                    "calibration_window_requests": 64,
                },
                "fitted_ttft": {
                    "profile": "glm-v5_ascend910c_ttft",
                    "function": "token_linear_v1",
                    "intercept_ms": 1.0,
                    "ms_per_uncached_token": 0.5,
                    "calibrated_from": "unit-test-fit",
                },
            }
        }
    )

    assert isinstance(backend, ServingLatencyProfile)
    assert backend.name == "serving_latency_profile"
    assert backend.profile == "glm-v5_ascend910c_serving_v1"
    assert backend.ttft_backend.name == "fitted_ttft"


def test_factory_builds_serving_latency_profile_with_kv_load_component() -> None:
    backend = build_batch_latency_backend(
        {
            "latency": {
                "backend": "serving_latency_profile",
                "model_name": "glm-v5",
                "hardware_name": "ascend910c",
                "serving_latency_profile": {
                    "profile": "glm-v5_ascend910c_serving_v1",
                    "ttft_backend": "fitted_ttft",
                    "kv_load": {
                        "mode": "token_linear_v1",
                        "aggregation": "shared_link_sum",
                        "overlap_mode": "none_v1",
                        "ddr_fixed_overhead_ms": 2.0,
                        "ddr_ms_per_cached_token": 0.25,
                        "calibrated_from": "unit-test-kv-load",
                    },
                },
                "fitted_ttft": {
                    "profile": "glm-v5_ascend910c_ttft",
                    "function": "token_linear_v1",
                    "intercept_ms": 1.0,
                    "ms_per_uncached_token": 0.5,
                    "calibrated_from": "unit-test-fit",
                },
            }
        }
    )

    assert isinstance(backend, ServingLatencyProfile)
    result = backend.estimate_iteration(_shape(kv_load_tokens=16, kv_load_bytes=4096))

    assert result.duration_ms == 11.0
    assert result.details["kv_load_ms"] == 6.0
    assert result.details["kv_load_mode"] == "token_linear_v1"


def test_factory_rejects_unsupported_serving_profile_ttft_backend() -> None:
    with pytest.raises(ValueError, match="ttft_backend only supports fitted_ttft"):
        build_batch_latency_backend(
            {
                "latency": {
                    "backend": "serving_latency_profile",
                    "model_name": "glm-v5",
                    "hardware_name": "ascend910c",
                    "serving_latency_profile": {
                        "profile": "glm-v5_ascend910c_serving_v1",
                        "ttft_backend": "AIConfigurator",
                    },
                }
            }
        )


def test_factory_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unsupported latency backend"):
        build_batch_latency_backend(
            {
                "latency": {
                    "backend": "unknown",
                    "model_name": "glm-v5",
                    "hardware_name": "ascend910c",
                }
            }
        )


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
