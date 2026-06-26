from hitfloor.latency.base import KVRestoreEstimateInput, PrefillEstimateInput
from hitfloor.latency.formula import FormulaLatencyBackend


def test_formula_latency_backend_estimates_prefill_and_restore() -> None:
    backend = FormulaLatencyBackend()

    prefill = backend.estimate_prefill(
        PrefillEstimateInput(cached_prefix_tokens=80, uncached_suffix_tokens=20)
    )
    restore = backend.estimate_kv_restore(
        KVRestoreEstimateInput(hbm_hit_tokens=50, ddr_hit_tokens=30)
    )

    assert prefill.milliseconds > 0
    assert restore.milliseconds > 0
    assert prefill.backend == "formula"
    assert restore.backend == "formula"
