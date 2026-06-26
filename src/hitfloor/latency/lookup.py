"""Lookup-table latency backend placeholder.

This backend is not selected by the Step1-Step5 runner. Current executable paths
use `formula` or `fitted_ttft`; lookup-table latency should be implemented and
tested in a dedicated stage before being added to the backend factory.
"""

from __future__ import annotations

from hitfloor.latency.base import (
    KVRestoreEstimateInput,
    LatencyEstimate,
    PrefillEstimateInput,
)


class LookupTableLatencyBackend:
    name = "lookup"

    def estimate_prefill(self, request: PrefillEstimateInput) -> LatencyEstimate:
        raise NotImplementedError("Lookup-table prefill latency is not implemented yet.")

    def estimate_kv_restore(self, request: KVRestoreEstimateInput) -> LatencyEstimate:
        raise NotImplementedError("Lookup-table KV restore latency is not implemented yet.")
