"""Cache lookup results."""

from __future__ import annotations

from dataclasses import dataclass

from hitfloor.request.block_hasher import PrefixBlock


@dataclass(frozen=True, slots=True)
class PrefixLookupResult:
    hbm_hit_blocks: tuple[PrefixBlock, ...]
    ddr_hit_blocks: tuple[PrefixBlock, ...]
    miss_blocks: tuple[PrefixBlock, ...]

    @property
    def hbm_hit_tokens(self) -> int:
        return sum(block.token_count for block in self.hbm_hit_blocks)

    @property
    def ddr_hit_tokens(self) -> int:
        return sum(block.token_count for block in self.ddr_hit_blocks)

    @property
    def miss_tokens(self) -> int:
        return sum(block.token_count for block in self.miss_blocks)

    @property
    def effective_hit_tokens(self) -> int:
        return self.hbm_hit_tokens + self.ddr_hit_tokens
