from hitfloor.cache.eviction import LRUEvictionPolicy, LRUEvictor
from hitfloor.cache.hbm_lru import HBMBlockMeta


def test_lru_policy_selects_oldest_inserted_block() -> None:
    blocks = {
        "older": _meta("older", last_access_time_ms=20.0, last_access_seq=2),
        "newer": _meta("newer", last_access_time_ms=10.0, last_access_seq=1),
    }
    policy = LRUEvictionPolicy()
    policy.on_insert(blocks["older"])
    policy.on_insert(blocks["newer"])

    victim = policy.select_victim(blocks)

    assert victim.block_key == "older"


def test_lru_policy_access_moves_block_to_newest_position() -> None:
    blocks = {
        "first": _meta("first", last_access_time_ms=10.0, last_access_seq=1),
        "second": _meta("second", last_access_time_ms=20.0, last_access_seq=2),
    }
    policy = LRUEvictionPolicy()
    policy.on_insert(blocks["first"])
    policy.on_insert(blocks["second"])

    policy.on_access(blocks["first"], reason="lookup_hit")
    victim = policy.select_victim(blocks)

    assert victim.block_key == "second"


def test_lru_policy_remove_updates_resident_queue() -> None:
    blocks = {
        "first": _meta("first", last_access_time_ms=10.0, last_access_seq=1),
        "second": _meta("second", last_access_time_ms=20.0, last_access_seq=2),
    }
    policy = LRUEvictionPolicy()
    policy.on_insert(blocks["first"])
    policy.on_insert(blocks["second"])

    policy.on_remove(blocks["first"])
    victim = policy.select_victim({"second": blocks["second"]})

    assert victim.block_key == "second"


def test_lru_evictor_fails_on_empty_cache() -> None:
    try:
        LRUEvictor().select_victim({})
    except ValueError as exc:
        assert "empty cache" in str(exc)
    else:
        raise AssertionError("expected empty cache to fail")


def test_lru_policy_fails_when_queue_references_non_resident_block() -> None:
    policy = LRUEvictionPolicy()
    policy.on_insert(_meta("stale", last_access_time_ms=10.0, last_access_seq=1))

    try:
        policy.select_victim(
            {"resident": _meta("resident", last_access_time_ms=20.0, last_access_seq=2)}
        )
    except ValueError as exc:
        assert "non-resident" in str(exc)
    else:
        raise AssertionError("expected stale policy queue to fail")


def test_lru_evictor_alias_preserves_existing_imports() -> None:
    assert LRUEvictor is LRUEvictionPolicy


def _meta(
    block_key: str,
    *,
    last_access_time_ms: float,
    last_access_seq: int,
) -> HBMBlockMeta:
    return HBMBlockMeta(
        block_key=block_key,
        block_index=0,
        token_count=16,
        size_bytes=0,
        created_time_ms=0.0,
        last_access_time_ms=last_access_time_ms,
        last_access_seq=last_access_seq,
    )
