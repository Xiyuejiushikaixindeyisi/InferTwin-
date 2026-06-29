"""KV-load latency components for non-HBM cache hits."""

from __future__ import annotations

from dataclasses import dataclass

from infertwin.config.profiles import KVLoadLatencyProfile
from infertwin.latency.profile import IterationLatencyComponent, LatencyComponentResult
from infertwin.scheduler.batch_shape import BatchShape


@dataclass(frozen=True, slots=True)
class ZeroKVLoadLatencyComponent:
    """Compatibility component for replay modes without KV-load latency."""

    aggregation: str = "shared_link_sum"
    overlap_mode: str = "none_v1"
    transfer_path: str = "local_ddr_cpu"
    calibrated_from: str = "manual_default"

    name: str = "kv_load"

    def __post_init__(self) -> None:
        _validate_common(
            aggregation=self.aggregation,
            overlap_mode=self.overlap_mode,
            transfer_path=self.transfer_path,
            calibrated_from=self.calibrated_from,
        )

    def estimate_iteration(self, shape: BatchShape) -> LatencyComponentResult:
        return LatencyComponentResult(
            name=self.name,
            duration_ms=0.0,
            modeled=False,
            details={
                **_base_details(
                    mode="zero",
                    aggregation=self.aggregation,
                    overlap_mode=self.overlap_mode,
                    transfer_path=self.transfer_path,
                    calibrated_from=self.calibrated_from,
                    shape=shape,
                ),
                "reason": "kv_load_latency_disabled_by_profile",
            },
        )


@dataclass(frozen=True, slots=True)
class TokenLinearKVLoadLatencyComponent:
    """Token-linear DDR/CPU KV-load latency component."""

    ddr_fixed_overhead_ms: float = 0.0
    ddr_ms_per_cached_token: float = 0.0
    aggregation: str = "shared_link_sum"
    overlap_mode: str = "none_v1"
    transfer_path: str = "local_ddr_cpu"
    calibrated_from: str = "manual_default"

    name: str = "kv_load"

    def __post_init__(self) -> None:
        _validate_common(
            aggregation=self.aggregation,
            overlap_mode=self.overlap_mode,
            transfer_path=self.transfer_path,
            calibrated_from=self.calibrated_from,
        )
        if self.ddr_fixed_overhead_ms < 0:
            raise ValueError("ddr_fixed_overhead_ms must be non-negative")
        if self.ddr_ms_per_cached_token < 0:
            raise ValueError("ddr_ms_per_cached_token must be non-negative")

    def estimate_iteration(self, shape: BatchShape) -> LatencyComponentResult:
        load_active = _load_active(shape)
        duration_ms = 0.0
        if load_active:
            duration_ms = (
                self.ddr_fixed_overhead_ms
                + shape.kv_load_tokens * self.ddr_ms_per_cached_token
            )
        return LatencyComponentResult(
            name=self.name,
            duration_ms=duration_ms,
            modeled=True,
            details={
                **_base_details(
                    mode="token_linear_v1",
                    aggregation=self.aggregation,
                    overlap_mode=self.overlap_mode,
                    transfer_path=self.transfer_path,
                    calibrated_from=self.calibrated_from,
                    shape=shape,
                ),
                "ddr_fixed_overhead_ms": self.ddr_fixed_overhead_ms,
                "ddr_ms_per_cached_token": self.ddr_ms_per_cached_token,
            },
        )


@dataclass(frozen=True, slots=True)
class ByteLinearKVLoadLatencyComponent:
    """Byte-linear DDR/CPU KV-load latency component."""

    ddr_fixed_overhead_ms: float = 0.0
    ddr_ms_per_byte: float = 0.0
    aggregation: str = "shared_link_sum"
    overlap_mode: str = "none_v1"
    transfer_path: str = "local_ddr_cpu"
    calibrated_from: str = "manual_default"

    name: str = "kv_load"

    def __post_init__(self) -> None:
        _validate_common(
            aggregation=self.aggregation,
            overlap_mode=self.overlap_mode,
            transfer_path=self.transfer_path,
            calibrated_from=self.calibrated_from,
        )
        if self.ddr_fixed_overhead_ms < 0:
            raise ValueError("ddr_fixed_overhead_ms must be non-negative")
        if self.ddr_ms_per_byte < 0:
            raise ValueError("ddr_ms_per_byte must be non-negative")

    def estimate_iteration(self, shape: BatchShape) -> LatencyComponentResult:
        load_active = _load_active(shape)
        if load_active and shape.kv_load_tokens > 0 and shape.kv_load_bytes == 0:
            raise ValueError("byte-linear KV load requires kv_load_bytes when tokens are loaded")
        duration_ms = 0.0
        if load_active:
            duration_ms = self.ddr_fixed_overhead_ms + shape.kv_load_bytes * self.ddr_ms_per_byte
        return LatencyComponentResult(
            name=self.name,
            duration_ms=duration_ms,
            modeled=True,
            details={
                **_base_details(
                    mode="byte_linear_v1",
                    aggregation=self.aggregation,
                    overlap_mode=self.overlap_mode,
                    transfer_path=self.transfer_path,
                    calibrated_from=self.calibrated_from,
                    shape=shape,
                ),
                "ddr_fixed_overhead_ms": self.ddr_fixed_overhead_ms,
                "ddr_ms_per_byte": self.ddr_ms_per_byte,
            },
        )


def build_kv_load_component(profile: KVLoadLatencyProfile) -> IterationLatencyComponent:
    """Build a KV-load latency component from a typed profile."""

    if profile.remote_ms_per_cached_token > 0:
        raise ValueError("remote_ms_per_cached_token is reserved for future remote KV load")
    if profile.mode == "zero":
        return ZeroKVLoadLatencyComponent(
            aggregation=profile.aggregation,
            overlap_mode=profile.overlap_mode,
            transfer_path=profile.transfer_path,
            calibrated_from=profile.calibrated_from,
        )
    if profile.mode == "token_linear_v1":
        return TokenLinearKVLoadLatencyComponent(
            ddr_fixed_overhead_ms=profile.ddr_fixed_overhead_ms,
            ddr_ms_per_cached_token=profile.ddr_ms_per_cached_token,
            aggregation=profile.aggregation,
            overlap_mode=profile.overlap_mode,
            transfer_path=profile.transfer_path,
            calibrated_from=profile.calibrated_from,
        )
    if profile.mode == "byte_linear_v1":
        return ByteLinearKVLoadLatencyComponent(
            ddr_fixed_overhead_ms=profile.ddr_fixed_overhead_ms,
            ddr_ms_per_byte=profile.ddr_ms_per_byte,
            aggregation=profile.aggregation,
            overlap_mode=profile.overlap_mode,
            transfer_path=profile.transfer_path,
            calibrated_from=profile.calibrated_from,
        )
    raise ValueError(f"unsupported KV load mode: {profile.mode}")


def _validate_common(
    *,
    aggregation: str,
    overlap_mode: str,
    transfer_path: str,
    calibrated_from: str,
) -> None:
    if aggregation != "shared_link_sum":
        raise ValueError("KV load aggregation only supports shared_link_sum")
    if overlap_mode != "none_v1":
        raise ValueError("KV load overlap_mode only supports none_v1")
    if not transfer_path:
        raise ValueError("transfer_path must be non-empty")
    if not calibrated_from:
        raise ValueError("calibrated_from must be non-empty")


def _base_details(
    *,
    mode: str,
    aggregation: str,
    overlap_mode: str,
    transfer_path: str,
    calibrated_from: str,
    shape: BatchShape,
) -> dict[str, float | int | str | bool]:
    return {
        "mode": mode,
        "aggregation": aggregation,
        "overlap_mode": overlap_mode,
        "transfer_path": transfer_path,
        "calibrated_from": calibrated_from,
        "kv_load_tokens": shape.kv_load_tokens,
        "kv_load_bytes": shape.kv_load_bytes,
        "kv_load_request_count": shape.kv_load_request_count,
        "load_active": _load_active(shape),
    }


def _load_active(shape: BatchShape) -> bool:
    return (
        shape.kv_load_request_count > 0
        or shape.kv_load_tokens > 0
        or shape.kv_load_bytes > 0
    )
