import pytest

from infertwin.latency.factory import build_batch_latency_backend
from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.latency.profile import ServingLatencyProfile


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
