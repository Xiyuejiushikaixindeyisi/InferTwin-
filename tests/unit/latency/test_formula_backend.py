from infertwin.latency.formula import FormulaLatencyBackend
from infertwin.scheduler.batch_shape import BatchShape, ScheduledSlice


def test_formula_iteration_latency_increases_with_prefill_tokens() -> None:
    backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=1.0,
        iteration_prefill_token_ms=0.5,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.0,
        model_name="glm-v5",
        hardware_name="local-dev",
    )

    small = backend.estimate_iteration(_shape([4]))
    large = backend.estimate_iteration(_shape([8]))

    assert large.duration_ms > small.duration_ms
    assert large.backend == "formula"
    assert large.shape_key.model_name == "glm-v5"
    assert large.shape_key.hardware_name == "local-dev"


def test_formula_iteration_latency_increases_with_batch_size() -> None:
    backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=1.0,
        iteration_prefill_token_ms=0.0,
        iteration_batch_overhead_ms=0.25,
        iteration_context_token_ms=0.0,
    )

    single = backend.estimate_iteration(_shape([4]))
    batched = backend.estimate_iteration(_shape([4, 4]))

    assert batched.duration_ms > single.duration_ms
    assert batched.details["batch_size"] == 2


def test_formula_iteration_latency_includes_context_tokens() -> None:
    backend = FormulaLatencyBackend(
        iteration_fixed_overhead_ms=1.0,
        iteration_prefill_token_ms=0.0,
        iteration_batch_overhead_ms=0.0,
        iteration_context_token_ms=0.1,
    )

    no_context = backend.estimate_iteration(_shape([4], computed_before=[0]))
    with_context = backend.estimate_iteration(_shape([4], computed_before=[20]))

    assert with_context.duration_ms > no_context.duration_ms
    assert with_context.details["total_context_tokens"] == 20


def _shape(tokens: list[int], computed_before: list[int] | None = None) -> BatchShape:
    if computed_before is None:
        computed_before = [0 for _ in tokens]

    slices = []
    for index, scheduled_tokens in enumerate(tokens):
        before = computed_before[index]
        slices.append(
            ScheduledSlice(
                request_id=f"r{index}",
                scheduled_prefill_tokens=scheduled_tokens,
                computed_tokens_before=before,
                computed_tokens_after=before + scheduled_tokens,
                prompt_tokens=before + scheduled_tokens,
                cached_prefix_tokens=0,
                previous_chunk_tokens=before,
            )
        )

    return BatchShape(
        instance_uuid="instance-a",
        iteration_id=0,
        start_time_ms=0.0,
        request_slices=tuple(slices),
    )
