from infertwin.cache.hbm_lru import HBMCache
from infertwin.cache.materialization import FinishTimeMaterializationPolicy
from infertwin.request.block_hasher import PrefixBlock


def test_finish_time_materialization_policy_materializes_at_finish_time() -> None:
    block = _block("a", 0)
    cache = HBMCache(capacity_blocks=2)
    policy = FinishTimeMaterializationPolicy()

    before = cache.lookup_prefix((block,), now_ms=9.0, request_id="before", instance_uuid="i1")
    policy.materialize_finished_request(
        cache=cache,
        blocks=(block,),
        finish_time_ms=10.0,
        request_id="r1",
        instance_uuid="i1",
    )
    after = cache.lookup_prefix((block,), now_ms=10.0, request_id="after", instance_uuid="i1")

    assert before.hbm_hit_tokens == 0
    assert after.hbm_hit_tokens == 16
    assert cache.contains(block.block_key)


def test_finish_time_materialization_policy_is_named() -> None:
    assert FinishTimeMaterializationPolicy.name == "finish_time"


def _block(block_key: str, block_index: int) -> PrefixBlock:
    return PrefixBlock(
        block_key=block_key,
        content_hash=f"content-{block_key}",
        block_index=block_index,
        token_count=16,
        size_bytes=0,
    )
