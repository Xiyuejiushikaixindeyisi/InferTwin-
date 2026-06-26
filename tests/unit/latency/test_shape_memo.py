from hitfloor.latency.memo import ShapeMemo
from hitfloor.latency.schema import LatencyResult, ShapeKey


def test_shape_memo_reuses_identical_shape() -> None:
    memo = ShapeMemo()
    key = _key()
    calls = 0

    def compute() -> LatencyResult:
        nonlocal calls
        calls += 1
        return LatencyResult(duration_ms=2.5, backend="formula", shape_key=key)

    first = memo.get_or_compute(key, compute)
    second = memo.get_or_compute(key, compute)

    assert calls == 1
    assert first.memoized is False
    assert second.memoized is True
    assert second.duration_ms == first.duration_ms


def test_shape_memo_separates_model_and_hardware() -> None:
    memo = ShapeMemo()
    first_key = _key(model_name="glm-v5", hardware_name="local-a")
    second_key = _key(model_name="glm-v5", hardware_name="local-b")

    memo.put(LatencyResult(duration_ms=1.0, backend="formula", shape_key=first_key))

    assert memo.get(first_key) is not None
    assert memo.get(second_key) is None


def _key(
    *,
    model_name: str = "glm-v5",
    hardware_name: str = "local-dev",
) -> ShapeKey:
    return ShapeKey(
        backend="formula",
        model_name=model_name,
        hardware_name=hardware_name,
        batch_size=1,
        scheduled_prefill_tokens=8,
        scheduled_decode_tokens=0,
        max_query_len=8,
        total_context_tokens=0,
    )
