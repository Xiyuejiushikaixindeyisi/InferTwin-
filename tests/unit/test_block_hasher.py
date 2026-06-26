from hitfloor.request.block_hasher import build_prefix_blocks


def test_block_hash_is_stable_for_same_prompt() -> None:
    first = build_prefix_blocks(
        [1, 2, 3, 4],
        block_size_tokens=2,
        model="glm-v5",
        tenant_id="tenant-a",
    )
    second = build_prefix_blocks(
        [1, 2, 3, 4],
        block_size_tokens=2,
        model="glm-v5",
        tenant_id="tenant-a",
    )

    assert [block.block_key for block in first] == [block.block_key for block in second]


def test_chained_block_hash_depends_on_prefix_path() -> None:
    first = build_prefix_blocks(
        [1, 2, 3, 4],
        block_size_tokens=2,
        model="glm-v5",
        tenant_id="tenant-a",
    )
    second = build_prefix_blocks(
        [9, 9, 3, 4],
        block_size_tokens=2,
        model="glm-v5",
        tenant_id="tenant-a",
    )

    assert first[1].content_hash == second[1].content_hash
    assert first[1].block_key != second[1].block_key


def test_tenant_isolated_scope_separates_tenants() -> None:
    first = build_prefix_blocks(
        [1, 2],
        block_size_tokens=2,
        model="glm-v5",
        tenant_id="tenant-a",
    )
    second = build_prefix_blocks(
        [1, 2],
        block_size_tokens=2,
        model="glm-v5",
        tenant_id="tenant-b",
    )

    assert first[0].block_key != second[0].block_key
