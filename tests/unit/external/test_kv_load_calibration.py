import pytest

from infertwin.config.profiles import KVLoadLatencyProfile
from infertwin.external.kv_load_calibration import (
    KVLoadCalibrationObservation,
    fit_byte_linear_v1,
    fit_token_linear_v1,
    to_kv_load_profile_mapping,
)
from infertwin.latency.kv_load import (
    ByteLinearKVLoadLatencyComponent,
    TokenLinearKVLoadLatencyComponent,
    build_kv_load_component,
)


def test_observation_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="source"):
        _observation(source="")

    with pytest.raises(ValueError, match="kv_load_tokens"):
        _observation(kv_load_tokens=-1)

    with pytest.raises(ValueError, match="kv_load_bytes"):
        _observation(kv_load_bytes=-1)

    with pytest.raises(ValueError, match="duration_ms"):
        _observation(duration_ms=-1.0)


def test_token_linear_fit_with_intercept() -> None:
    fit = fit_token_linear_v1(
        (
            _observation(kv_load_tokens=10, duration_ms=6.0),
            _observation(kv_load_tokens=20, duration_ms=11.0),
            _observation(kv_load_tokens=30, duration_ms=16.0),
        ),
        calibrated_from="ramulator2_git:run-a",
    )

    assert fit.mode == "token_linear_v1"
    assert fit.transfer_path == "local_ddr_cpu"
    assert fit.ddr_fixed_overhead_ms == pytest.approx(1.0)
    assert fit.ddr_ms_per_cached_token == pytest.approx(0.5)
    assert fit.sample_count == 3
    assert fit.calibrated_from == "ramulator2_git:run-a"


def test_token_linear_fit_through_origin() -> None:
    fit = fit_token_linear_v1(
        (
            _observation(kv_load_tokens=10, duration_ms=5.0),
            _observation(kv_load_tokens=20, duration_ms=10.0),
        ),
        calibrated_from="synthetic:origin",
        fit_intercept=False,
    )

    assert fit.ddr_fixed_overhead_ms == 0.0
    assert fit.ddr_ms_per_cached_token == pytest.approx(0.5)


def test_byte_linear_fit_maps_to_existing_kv_load_profile() -> None:
    fit = fit_byte_linear_v1(
        (
            _observation(kv_load_bytes=100, duration_ms=3.0),
            _observation(kv_load_bytes=200, duration_ms=5.0),
            _observation(kv_load_bytes=300, duration_ms=7.0),
        ),
        calibrated_from="mooncake_benchmark:run-b",
    )

    mapping = to_kv_load_profile_mapping(fit)
    profile = KVLoadLatencyProfile.from_mapping(mapping, field_name="kv_load")
    component = build_kv_load_component(profile)

    assert isinstance(component, ByteLinearKVLoadLatencyComponent)
    assert mapping == {
        "mode": "byte_linear_v1",
        "aggregation": "shared_link_sum",
        "overlap_mode": "none_v1",
        "transfer_path": "local_ddr_cpu",
        "ddr_fixed_overhead_ms": pytest.approx(1.0),
        "ddr_ms_per_byte": pytest.approx(0.02),
        "calibrated_from": "mooncake_benchmark:run-b",
    }


def test_token_linear_mapping_builds_existing_component() -> None:
    fit = fit_token_linear_v1(
        (
            _observation(kv_load_tokens=10, duration_ms=2.0),
            _observation(kv_load_tokens=20, duration_ms=4.0),
        ),
        calibrated_from="production_measurement:run-c",
        fit_intercept=False,
    )
    profile = KVLoadLatencyProfile.from_mapping(
        to_kv_load_profile_mapping(fit),
        field_name="kv_load",
    )

    component = build_kv_load_component(profile)

    assert isinstance(component, TokenLinearKVLoadLatencyComponent)
    assert profile.calibrated_from == "production_measurement:run-c"
    assert profile.ddr_ms_per_cached_token == pytest.approx(0.2)


def test_fit_rejects_empty_and_mixed_observations() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        fit_token_linear_v1((), calibrated_from="synthetic:empty")

    with pytest.raises(ValueError, match="model_name"):
        fit_token_linear_v1(
            (
                _observation(model_name="model-a"),
                _observation(model_name="model-b"),
            ),
            calibrated_from="synthetic:mixed-model",
        )

    with pytest.raises(ValueError, match="hardware_name"):
        fit_token_linear_v1(
            (
                _observation(hardware_name="hw-a"),
                _observation(hardware_name="hw-b"),
            ),
            calibrated_from="synthetic:mixed-hardware",
        )

    with pytest.raises(ValueError, match="transfer_path"):
        fit_token_linear_v1(
            (
                _observation(transfer_path="local_ddr_cpu"),
                _observation(transfer_path="mooncake_rdma"),
            ),
            calibrated_from="synthetic:mixed-transfer",
        )


def test_fit_rejects_unreliable_or_negative_results() -> None:
    with pytest.raises(ValueError, match="all calibration x values are equal"):
        fit_token_linear_v1(
            (
                _observation(kv_load_tokens=10, duration_ms=1.0),
                _observation(kv_load_tokens=10, duration_ms=2.0),
            ),
            calibrated_from="synthetic:equal-x",
        )

    with pytest.raises(ValueError, match="positive kv_load_bytes"):
        fit_byte_linear_v1(
            (
                _observation(kv_load_bytes=0, duration_ms=0.0),
                _observation(kv_load_bytes=0, duration_ms=1.0),
            ),
            calibrated_from="synthetic:no-bytes",
        )

    with pytest.raises(ValueError, match="fitted slope is negative"):
        fit_token_linear_v1(
            (
                _observation(kv_load_tokens=10, duration_ms=10.0),
                _observation(kv_load_tokens=20, duration_ms=5.0),
            ),
            calibrated_from="synthetic:negative-slope",
        )

    with pytest.raises(ValueError, match="fixed overhead is negative"):
        fit_token_linear_v1(
            (
                _observation(kv_load_tokens=10, duration_ms=1.0),
                _observation(kv_load_tokens=20, duration_ms=5.0),
            ),
            calibrated_from="synthetic:negative-intercept",
        )


def _observation(
    *,
    source: str = "synthetic",
    model_name: str = "glm-v5",
    hardware_name: str = "ascend910c",
    transfer_path: str = "local_ddr_cpu",
    kv_load_tokens: int = 10,
    kv_load_bytes: int = 100,
    kv_load_request_count: int = 1,
    batch_size: int = 1,
    duration_ms: float = 1.0,
) -> KVLoadCalibrationObservation:
    return KVLoadCalibrationObservation(
        source=source,
        model_name=model_name,
        hardware_name=hardware_name,
        transfer_path=transfer_path,
        kv_load_tokens=kv_load_tokens,
        kv_load_bytes=kv_load_bytes,
        kv_load_request_count=kv_load_request_count,
        batch_size=batch_size,
        duration_ms=duration_ms,
    )
