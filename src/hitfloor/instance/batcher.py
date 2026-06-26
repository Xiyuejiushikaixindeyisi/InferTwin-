"""Continuous batching boundary."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatchShape:
    request_count: int
    total_uncached_tokens: int
    max_prompt_tokens: int
