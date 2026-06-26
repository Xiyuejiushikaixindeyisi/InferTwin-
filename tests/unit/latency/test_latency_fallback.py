import pytest

from infertwin.latency.fallback import (
    LatencyFallbackConfig,
    build_latency_fallback_config,
)


def test_latency_fallback_defaults_to_fail() -> None:
    config = build_latency_fallback_config({})

    assert config == LatencyFallbackConfig(on_calibration_failure="fail")
    assert config.uses_model_default_on_calibration_failure is False


def test_latency_fallback_parses_use_model_default() -> None:
    config = build_latency_fallback_config(
        {
            "latency_fallback": {
                "on_calibration_failure": "use_model_default",
            }
        }
    )

    assert config.on_calibration_failure == "use_model_default"
    assert config.uses_model_default_on_calibration_failure is True


def test_latency_fallback_defaults_missing_policy_to_fail() -> None:
    config = build_latency_fallback_config({"latency_fallback": {}})

    assert config.on_calibration_failure == "fail"


def test_latency_fallback_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError, match="on_calibration_failure"):
        build_latency_fallback_config(
            {
                "latency_fallback": {
                    "on_calibration_failure": "retry_forever",
                }
            }
        )


def test_latency_fallback_rejects_non_mapping_section() -> None:
    with pytest.raises(ValueError, match="latency_fallback config"):
        build_latency_fallback_config({"latency_fallback": "use_model_default"})
