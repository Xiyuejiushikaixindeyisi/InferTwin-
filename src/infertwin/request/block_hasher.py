"""Build hash-only prefix blocks from token ids."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable


NONE_HASH = hashlib.sha256(b"infertwin:none_hash:v1").digest()


@dataclass(frozen=True, slots=True)
class PrefixBlock:
    block_key: str
    content_hash: str
    block_index: int
    token_count: int
    size_bytes: int


def build_prefix_blocks(
    token_ids: list[int],
    block_size_tokens: int,
    model: str,
    tenant_id: str,
    kv_bytes_per_token: int | None = None,
    cache_scope: str = "tenant_isolated",
) -> list[PrefixBlock]:
    if block_size_tokens <= 0:
        raise ValueError("block_size_tokens must be positive")

    blocks: list[PrefixBlock] = []
    parent = NONE_HASH
    for block_index, start in enumerate(range(0, len(token_ids), block_size_tokens)):
        block_tokens = token_ids[start : start + block_size_tokens]
        content_hash = _content_hash(block_tokens)
        block_key_bytes = _chain_hash(
            parent=parent,
            model=model,
            tenant_id=tenant_id,
            cache_scope=cache_scope,
            content_hash=content_hash,
        )
        parent = block_key_bytes
        token_count = len(block_tokens)
        blocks.append(
            PrefixBlock(
                block_key=block_key_bytes.hex(),
                content_hash=content_hash,
                block_index=block_index,
                token_count=token_count,
                size_bytes=token_count * (kv_bytes_per_token or 0),
            )
        )
    return blocks


def _content_hash(token_ids: Iterable[int]) -> str:
    digest = hashlib.sha256()
    for token_id in token_ids:
        digest.update(int(token_id).to_bytes(16, "big", signed=False))
    return digest.hexdigest()


def _chain_hash(
    parent: bytes,
    model: str,
    tenant_id: str,
    cache_scope: str,
    content_hash: str,
) -> bytes:
    digest = hashlib.sha256()
    digest.update(parent)
    digest.update(_encode_field(model))
    if cache_scope == "tenant_isolated":
        digest.update(_encode_field(tenant_id))
    elif cache_scope != "model_shared":
        raise ValueError(
            f"Unsupported cache_scope {cache_scope!r}; expected tenant_isolated or model_shared"
        )
    digest.update(_encode_field(content_hash))
    return digest.digest()


def _encode_field(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded
