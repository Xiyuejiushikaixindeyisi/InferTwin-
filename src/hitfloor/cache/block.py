"""KV block data structures."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KVBlockKey:
    model: str
    tenant_id: str
    prefix_hash: str
    block_index: int


@dataclass(slots=True)
class KVBlock:
    key: KVBlockKey
    token_count: int
    size_bytes: int
    last_access_time_ms: float = 0.0
    refcount: int = 0
