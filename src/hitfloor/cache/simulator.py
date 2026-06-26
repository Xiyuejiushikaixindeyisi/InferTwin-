"""Two-level KV cache simulator skeleton.

This module is not wired into Step1-Step5 replay. The active finite-HBM cache
implementation is `hitfloor.cache.hbm_lru.HBMCache`; DDR/SSD tiers should be
introduced through a new reviewed stage instead of extending this placeholder
silently.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CacheLookupResult:
    hbm_hit_tokens: int
    ddr_hit_tokens: int
    miss_tokens: int

    @property
    def effective_hit_tokens(self) -> int:
        return self.hbm_hit_tokens + self.ddr_hit_tokens


class KVCacheSimulator:
    """Prefix KV cache simulator boundary.

    The concrete implementation will manage HBM/DDR block lifecycle, refcounts,
    promotion, demotion, and eviction.
    """

    def lookup_prefix(self, prompt_tokens: list[int]) -> CacheLookupResult:
        return CacheLookupResult(
            hbm_hit_tokens=0,
            ddr_hit_tokens=0,
            miss_tokens=len(prompt_tokens),
        )
