import pytest

from infertwin.latency.fitted_ttft import FittedTTFTLatencyBackend
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_fitted_ttft_latency_increases_with_scheduled_prefill_tokens() -> None:
    backend = FittedTTFTLatencyBackend(
        intercept_ms=1.0,
        ms_per_uncached_token=0.5,
        model_name="glm-v5",
        hardware_name="ascend910c",
        profile="glm-v5_ascend910c_default",
    )

    small = backend.estimate_iteration(_shape([4]))
    large = backend.estimate_iteration(_shape([8]))

    assert small.duration_ms == 3.0
    assert large.duration_ms == 5.0
    assert large.backend == "fitted_ttft"
    assert large.shape_key.model_name == "glm-v5"
    assert large.shape_key.hardware_name == "ascend910c"
    assert large.details["function"] == "token_linear_v1"
    assert large.details["profile"] == "glm-v5_ascend910c_default"


def test_fitted_ttft_rejects_negative_coefficients() -> None:
    with pytest.raises(ValueError, match="intercept_ms"):
        FittedTTFTLatencyBackend(
            intercept_ms=-1.0,
            ms_per_uncached_token=0.5,
            model_name="glm-v5",
            hardware_name="ascend910c",
            profile="glm-v5_ascend910c_default",
        )

    with pytest.raises(ValueError, match="ms_per_uncached_token"):
        FittedTTFTLatencyBackend(
            intercept_ms=1.0,
            ms_per_uncached_token=-0.5,
            model_name="glm-v5",
            hardware_name="ascend910c",
            profile="glm-v5_ascend910c_default",
        )


def _shape(tokens: list[int]) -> BatchShape:
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
            )
        )

    return BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=tuple(slices),
    )
