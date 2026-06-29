"""Request-level TTFT composition for progressive replay timelines."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.instance.request import SimulationRequest
from infertwin.replay.timeline import (
    CHUNK_TTFT_GRANULARITY,
    ITERATION_TTFT_GRANULARITY,
    LEGACY_TIMELINE_MODE,
    PROGRESSIVE_TIMELINE_MODE,
)
from infertwin.scheduler.state import RequestState


@dataclass(frozen=True, slots=True)
class RequestTTFTComposition:
    """Closed TTFT decomposition for one completed request."""

    timeline_mode: str
    ttft_granularity: str
    observed_ttft_ms: float
    ttft_ms: float
    scheduler_wait_ms: float
    compute_wait_ms: float
    kv_load_wait_ms: float
    uncached_prefill_compute_ms: float
    unattributed_ttft_ms: float
    chunk_count: int
    load_event_count: int

    def __post_init__(self) -> None:
        _require_non_empty(self.timeline_mode, "timeline_mode")
        _require_non_empty(self.ttft_granularity, "ttft_granularity")
        _require_non_negative_float(self.observed_ttft_ms, "observed_ttft_ms")
        _require_non_negative_float(self.ttft_ms, "ttft_ms")
        _require_non_negative_float(self.scheduler_wait_ms, "scheduler_wait_ms")
        _require_non_negative_float(self.compute_wait_ms, "compute_wait_ms")
        _require_non_negative_float(self.kv_load_wait_ms, "kv_load_wait_ms")
        _require_non_negative_float(
            self.uncached_prefill_compute_ms,
            "uncached_prefill_compute_ms",
        )
        _require_non_negative_float(self.unattributed_ttft_ms, "unattributed_ttft_ms")
        _require_non_negative_int(self.chunk_count, "chunk_count")
        _require_non_negative_int(self.load_event_count, "load_event_count")


class RequestTTFTComposer:
    """Compose request TTFT from replay timeline accounting fields."""

    def __init__(self, *, epsilon_ms: float = 1e-9) -> None:
        if epsilon_ms < 0:
            raise ValueError("epsilon_ms must be non-negative")
        self.epsilon_ms = epsilon_ms

    def compose(
        self,
        *,
        request: SimulationRequest,
        state: RequestState,
        finish_time_ms: float,
        first_scheduled_time_ms: float,
    ) -> RequestTTFTComposition:
        """Return a closed TTFT composition for one finished request."""

        if finish_time_ms < request.start_time_ms:
            raise ValueError(
                f"finish_time_ms cannot be earlier than arrival for request {request.request_id}"
            )
        if first_scheduled_time_ms < request.start_time_ms:
            raise ValueError(
                "first_scheduled_time_ms cannot be earlier than arrival for "
                f"request {request.request_id}"
            )

        if state.timeline_mode == LEGACY_TIMELINE_MODE:
            return self._compose_legacy(
                request=request,
                state=state,
                finish_time_ms=finish_time_ms,
                first_scheduled_time_ms=first_scheduled_time_ms,
            )
        if state.timeline_mode == PROGRESSIVE_TIMELINE_MODE:
            return self._compose_progressive(
                request=request,
                state=state,
                finish_time_ms=finish_time_ms,
            )
        raise ValueError(f"unsupported timeline_mode {state.timeline_mode!r}")

    def _compose_legacy(
        self,
        *,
        request: SimulationRequest,
        state: RequestState,
        finish_time_ms: float,
        first_scheduled_time_ms: float,
    ) -> RequestTTFTComposition:
        observed_ttft_ms = finish_time_ms - request.start_time_ms
        return RequestTTFTComposition(
            timeline_mode=LEGACY_TIMELINE_MODE,
            ttft_granularity=ITERATION_TTFT_GRANULARITY,
            observed_ttft_ms=observed_ttft_ms,
            ttft_ms=observed_ttft_ms,
            scheduler_wait_ms=first_scheduled_time_ms - request.start_time_ms,
            compute_wait_ms=0.0,
            kv_load_wait_ms=0.0,
            uncached_prefill_compute_ms=state.prefill_compute_ms,
            unattributed_ttft_ms=0.0,
            chunk_count=0,
            load_event_count=0,
        )

    def _compose_progressive(
        self,
        *,
        request: SimulationRequest,
        state: RequestState,
        finish_time_ms: float,
    ) -> RequestTTFTComposition:
        observed_ttft_ms = finish_time_ms - request.start_time_ms
        scheduler_wait_ms = state.compute_wait_ms + state.kv_load_wait_ms
        base_ms = (
            scheduler_wait_ms
            + state.prefill_compute_ms
            + state.unattributed_ttft_ms
        )
        residual_ms = observed_ttft_ms - base_ms
        if residual_ms < -self.epsilon_ms:
            raise ValueError(
                "negative unattributed TTFT residual for request "
                f"{request.request_id}: observed_ttft_ms={observed_ttft_ms}, "
                f"compute_wait_ms={state.compute_wait_ms}, "
                f"kv_load_wait_ms={state.kv_load_wait_ms}, "
                f"prefill_compute_ms={state.prefill_compute_ms}, "
                f"unattributed_ttft_ms={state.unattributed_ttft_ms}"
            )
        if abs(residual_ms) <= self.epsilon_ms:
            residual_ms = 0.0

        unattributed_ttft_ms = state.unattributed_ttft_ms + residual_ms
        ttft_ms = scheduler_wait_ms + state.prefill_compute_ms + unattributed_ttft_ms
        return RequestTTFTComposition(
            timeline_mode=PROGRESSIVE_TIMELINE_MODE,
            ttft_granularity=CHUNK_TTFT_GRANULARITY,
            observed_ttft_ms=observed_ttft_ms,
            ttft_ms=ttft_ms,
            scheduler_wait_ms=scheduler_wait_ms,
            compute_wait_ms=state.compute_wait_ms,
            kv_load_wait_ms=state.kv_load_wait_ms,
            uncached_prefill_compute_ms=state.prefill_compute_ms,
            unattributed_ttft_ms=unattributed_ttft_ms,
            chunk_count=state.chunk_count,
            load_event_count=state.load_event_count,
        )


def _require_non_empty(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_non_negative_float(value: float, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
