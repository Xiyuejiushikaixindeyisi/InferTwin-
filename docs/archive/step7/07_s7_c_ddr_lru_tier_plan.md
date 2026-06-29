# S7-C：DDR LRU Tier 开发方案与执行记录

状态：已完成。

阶段类型：核心仿真器开发。

## 1. Batch 目标

S7-C 只实现一个独立的单实例 DDR/CPU tier cache backend：

```text
DDRLRUCache
```

它负责：

- 保存 hash-only prefix block metadata。
- 按 full-block prefix 连续命中。
- store request finish 后产生的 block metadata。
- 按 LRU 淘汰 DDR resident block。
- 发出 DDR tier cache events。

S7-C 不接 HBM，不接 replay，不接 streaming runner。完成后，S7-D 再用 `TieredPrefixCache` 组合现有 `HBMCache` 和新增 `DDRLRUCache`。

## 2. 为什么需要 S7-C

Step7 的最终目标是单实例 HBM + DDR/CPU pooling。为了避免一次性把多级 lookup、materialization、runner、report 全塞进一个 batch，S7-C 先实现一个可单测、可独立验证的 DDR tier。

这样做有三个好处：

1. DDR 生命周期可以独立验证：lookup / store / evict / take_events。
2. S7-D 只需要做 HBM + DDR 组合逻辑，不再同时调试 DDR LRU 细节。
3. S7-E 接 streaming runner 时，cache backend 语义已经稳定，风险更小。

当前 `HBMCache` 已经证明 hash-only metadata + stateful LRU policy 可以支撑 offline replay。DDR tier 应复用这个模型，但必须保持事件和 lookup result 口径不同：

- HBM hit 写入 `hbm_hit_blocks`。
- DDR hit 写入 `ddr_hit_blocks`。
- DDR event 使用 `cache_tier=ddr`。

## 3. 当前代码现状

相关文件：

```text
src/infertwin/cache/hbm_lru.py
src/infertwin/cache/events.py
src/infertwin/cache/event_sink.py
src/infertwin/cache/eviction.py
src/infertwin/cache/results.py
src/infertwin/request/block_hasher.py
src/infertwin/cache/__init__.py
```

当前可复用能力：

- `PrefixBlock` 已经是 hash-only block metadata。
- `PrefixLookupResult` 已经包含 `ddr_hit_blocks`。
- `CacheEvent` 已经支持 DDR used/capacity、source/target tier、load/store token 字段。
- `LRUEvictionPolicy` 是 stateful policy，基于 `block_key` 和 access order 管理 recency。
- `HBMCache` 已经有完整的 capacity / contiguous lookup / eviction / event 测试范式。

当前缺口：

- 没有 DDR resident metadata store。
- 没有 `store` event 的真实发出方。
- 没有 `cache_tier=ddr` 的真实 lookup / evict event。
- 没有独立 DDR tier 单测。

## 4. S7-C 设计原则

### 4.1 继续 hash-only，不保存真实 KV

`DDRLRUCache` 只保存：

```text
block_key
block_index
token_count
size_bytes
created_time_ms
last_access_time_ms
last_access_seq
hit_count
stored_by_request_id
instance_uuid
```

不保存真实 KV tensor，不保存 token ids，不保存 prompt 文本。

原因：

- InferTwin 是 offline simulator。
- Step7 只做 tier hit accounting，不做 KV load latency。
- 真实 KV bytes / transfer 进入 Step8 之后再建模。

### 4.2 DDR lookup 只返回 contiguous prefix

`DDRLRUCache.lookup_prefix(blocks)` 规则：

```text
for block in blocks from left to right:
    if resident:
        hit
    else:
        break
miss = remaining blocks
```

返回：

```python
PrefixLookupResult(
    hbm_hit_blocks=(),
    ddr_hit_blocks=tuple(hit_blocks),
    miss_blocks=tuple(miss_blocks),
)
```

注意：S7-C 是独立 DDR tier，因此它只知道自己收到的 blocks，不知道 HBM 已命中了多少。S7-D 的 `TieredPrefixCache` 会负责：

```text
HBM contiguous hit -> DDR contiguous hit -> miss
```

### 4.3 DDR store 不是 HBM eviction

`DDRLRUCache.store(blocks)` 只表示：

```text
这些 block 在 request finish-time materialization 后被写入 DDR tier metadata。
```

它不是：

- HBM eviction backfill。
- KV load completion。
- async offload completion。
- promotion target allocation。

这与 Step7 冻结决策一致：finish-time materialization 同时写 HBM 和 DDR，避免把 offload 和 eviction 混成一个事件。

### 4.4 Prompt 大于 DDR capacity 不 OOM

如果一次 store 的 block 数量大于 DDR capacity，`DDRLRUCache` 仍然逐个写入、逐个按 LRU 淘汰，最终保留 suffix blocks。

例如：

```text
capacity = 2
store: b0 b1 b2
resident after store: b1 b2
```

后续如果请求完整 prefix `b0 b1 b2`，由于 b0 已被淘汰，连续 prefix hit 为 0。

原因：

- offline simulator 不能因为单 prompt 大于 capacity 就 OOM。
- 真实系统通常也会通过调度/换出/不缓存完整 prefix 等方式处理容量压力。
- 当前语义与已有 `HBMCache` 的 suffix-retention 行为一致。

## 5. 目标 API

新增文件：

```text
src/infertwin/cache/ddr_lru.py
```

建议类型：

```python
@dataclass(slots=True)
class DDRBlockMeta:
    block_key: str
    block_index: int
    token_count: int
    size_bytes: int
    created_time_ms: float
    last_access_time_ms: float
    last_access_seq: int
    hit_count: int = 0
    stored_by_request_id: str = ""
    instance_uuid: str = ""
```

建议类：

```python
class DDRLRUCache:
    def __init__(
        self,
        *,
        capacity_blocks: int,
        evictor: HBMEvictionPolicy | None = None,
    ) -> None: ...

    @property
    def capacity_blocks(self) -> int: ...

    @property
    def resident_blocks(self) -> int: ...

    @property
    def eviction_policy(self) -> str: ...

    def contains(self, block_key: str) -> bool: ...

    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
        hbm_used_blocks: int = 0,
        hbm_capacity_blocks: int = 0,
    ) -> PrefixLookupResult: ...

    def store(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
        hbm_used_blocks: int = 0,
        hbm_capacity_blocks: int = 0,
    ) -> None: ...

    def take_events(self) -> tuple[CacheEvent, ...]: ...
```

### 5.1 为什么暂时使用 `HBMEvictionPolicy`

当前 eviction protocol 名字里带 HBM，但协议本身只依赖：

```text
block_key
last_access_time_ms
last_access_seq
created_time_ms
```

它对 HBM 没有真实硬件假设。

S7-C 为了减少重命名带来的大范围 churn，暂时复用该 protocol。后续如果需要更干净命名，可以在独立工程优化中把它重命名为 `CacheEvictionPolicy`，并保留兼容 alias。

## 6. Event 语义

### 6.1 DDR lookup hit

```text
event_type=lookup_hit
cache_tier=ddr
reason=prefix_hit
source_tier=ddr
target_tier=""
load_tokens=0
store_tokens=0
ddr_used_blocks=<resident after touch>
ddr_capacity_blocks=<capacity>
```

### 6.2 DDR lookup miss

```text
event_type=lookup_miss
cache_tier=ddr
reason=prefix_miss
source_tier=""
target_tier=""
load_tokens=0
store_tokens=0
```

说明：

- S7-C standalone DDR cache 可以发 DDR miss event。
- S7-D 是否保留 HBM miss + DDR miss 双事件，还是由 tiered orchestrator 统一收敛 miss event，需要在 S7-D 方案中单独评审。

### 6.3 DDR store

```text
event_type=store
cache_tier=ddr
reason=finish_time_store
source_tier=""
target_tier=ddr
store_tokens=block.token_count
```

如果 block 已存在：

- 只刷新 recency。
- 不重复发 store event。
- 不增加 resident count。

原因：与 `HBMCache.materialize(existing)` 当前行为保持一致，避免重复 store event 夸大写入量。

### 6.4 DDR evict

```text
event_type=evict
cache_tier=ddr
reason=capacity
ddr_used_blocks=<resident after removal>
ddr_capacity_blocks=<capacity>
```

## 7. 代码编写方案

### C1. 新增 DDR LRU backend

新增：

```text
src/infertwin/cache/ddr_lru.py
```

实现内容：

- `DDRBlockMeta`。
- `DDRLRUCache`。
- capacity 正整数 guard。
- `contains()`。
- `lookup_prefix()`。
- `store()`。
- `_evict_one()`。
- `_emit()`。
- `_touch()`。
- `_next_access_seq()`。
- `take_events()`。

实现风格：

- 参考 `HBMCache`，但不要复制粘贴后改得不可读。
- 函数边界保持清晰。
- 事件构造集中在 `_emit()` 和 `_evict_one()`。
- 不引入继承基类。

### C2. 更新 package export

修改：

```text
src/infertwin/cache/__init__.py
```

新增 export：

```python
DDRBlockMeta
DDRLRUCache
```

### C3. 新增单测

新增：

```text
tests/unit/cache/test_ddr_lru_cache.py
```

测试覆盖：

1. 空 DDR lookup 返回全 miss：
   - `ddr_hit_blocks == ()`。
   - `miss_blocks == blocks`。
   - events 为 `lookup_miss`，`cache_tier=ddr`。
2. store 后可被后续 lookup 命中：
   - `ddr_hit_blocks == blocks`。
   - `ddr_hit_tokens` 正确。
3. lookup 只命中连续 prefix：
   - resident 有 b0 和 b2，lookup b0 b1 b2，只命中 b0。
4. LRU 淘汰确定性：
   - hit 会刷新 recency。
   - 新 store 超容量时淘汰最久未访问 block。
5. store existing 不重复占容量、不重复发 store event。
6. prompt 大于 capacity 不 OOM，最终保留 suffix blocks，后续完整 prefix hit 可能为 0。
7. `capacity_blocks <= 0` fail-fast。
8. stateful eviction policy hooks：
   - insert / access:lookup_hit / access:store_existing / select / remove。
9. DDR events 字段：
   - `cache_tier=ddr`。
   - `ddr_used_blocks` / `ddr_capacity_blocks`。
   - store event `target_tier=ddr`、`store_tokens=block.token_count`。
   - hit event `source_tier=ddr`。
   - 可传入 `hbm_used_blocks` / `hbm_capacity_blocks` 作为事件上下文。
10. `take_events()` drain 行为。

### C4. 不修改的文件

S7-C 不修改：

```text
src/infertwin/replay/
src/infertwin/streaming/
src/infertwin/experiment/
src/infertwin/report/
src/infertwin/cache/hbm_lru.py
```

如果开发过程中发现必须修改这些文件，应暂停并重新评审，因为那说明 S7-C 越界进入 S7-D/S7-E。

## 8. 测试计划

优先运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_ddr_lru_cache.py
```

再运行 cache event / HBM baseline：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_hbm_lru_cache.py \
  tests/unit/cache/test_cache_events.py \
  tests/unit/cache/test_cache_event_sink.py \
  tests/unit/report/test_cache_event_writer.py
```

代码质量：

```bash
.venv/bin/python -m ruff check src/infertwin/cache tests/unit/cache tests/unit/report/test_cache_event_writer.py
.venv/bin/python -m ruff format --check src/infertwin/cache tests/unit/cache tests/unit/report/test_cache_event_writer.py
git diff --check
```

## 9. S7-C 成功标准

S7-C 完成时应满足：

- `DDRLRUCache` 可独立运行。
- DDR lookup 连续性正确。
- DDR store / evict / lookup events 字段正确。
- capacity 小于 prompt blocks 不 OOM。
- LRU touch / evict 行为确定性可测。
- `PrefixLookupResult.ddr_hit_blocks` 和 `miss_blocks` 口径正确。
- 现有 HBM cache 测试不受影响。
- 未引入 replay 行为变化。

## 10. 对后续 Batch 的影响

S7-D 可以直接组合：

```python
HBMCache
DDRLRUCache
```

实现：

```text
HBM contiguous lookup -> DDR contiguous lookup -> miss
finish-time materialization -> HBM materialize + DDR store
```

S7-D 需要重点评审：

- 是否保留 standalone HBM miss + DDR miss 双事件。
- tiered lookup 中 HBM miss event 和 DDR lookup event 的顺序。
- materialize 时 HBM event 和 DDR store event 的顺序。
- TieredPrefixCache 是否负责传递 HBM resident state 到 DDR events。

## 11. 风险与边界

### 11.1 风险

- `DDRLRUCache` 与 `HBMCache` 存在相似代码，未来可能需要抽象公共 helper。
- 现在立即抽象 base class 会扩大 S7-C 范围，反而降低可审查性。
- DDR standalone lookup miss event 可能与 S7-D tiered miss event 存在重复风险，需要在 S7-D 收敛。

### 11.2 控制方式

- S7-C 暂不抽象 base class。
- S7-C 不接 HBM/replay，所有行为只通过单测验证。
- S7-D 再讨论 tiered orchestrator 的 event order 和 miss event 去重。
- S7-C 明确不做 promotion、load latency、async store completion。

## 12. 执行记录

### 12.1 做了什么

- 新增 `src/infertwin/cache/ddr_lru.py`。
- 实现 `DDRBlockMeta`。
- 实现 `DDRLRUCache`：
  - positive capacity guard。
  - `capacity_blocks` / `resident_blocks` / `eviction_policy`。
  - `contains()`。
  - contiguous `lookup_prefix()`。
  - `store()`。
  - LRU eviction。
  - `take_events()`。
- DDR lookup result 写入 `PrefixLookupResult.ddr_hit_blocks`。
- DDR events 使用 `cache_tier=ddr`。
- DDR store events 使用 `event_type=store`、`target_tier=ddr`、`store_tokens=block.token_count`。
- DDR hit events 使用 `source_tier=ddr`。
- 更新 `src/infertwin/cache/__init__.py`，导出 `DDRBlockMeta` 和 `DDRLRUCache`。
- 新增 `tests/unit/cache/test_ddr_lru_cache.py`，覆盖 lookup/store/evict/events/LRU hooks。

### 12.2 没有做什么

- 没有接 HBM。
- 没有实现 `TieredPrefixCache`。
- 没有修改 replay、scheduler、streaming runner、experiment runner 或 report。
- 没有实现 DDR hit promotion。
- 没有实现 KV load latency。
- 没有实现 async store completion。
- 没有抽象 HBM/DDR 公共 base class。

### 12.3 影响

- 新增一个可独立运行的 DDR/CPU cache tier backend。
- `PrefixLookupResult.ddr_hit_blocks` 现在有真实 standalone backend 可填充。
- 后续 S7-D 可以直接组合 `HBMCache + DDRLRUCache`。
- 现有 HBM cache 测试不受影响。

### 12.4 边界

- `DDRLRUCache` 是 standalone tier，不知道 HBM 已命中多少。
- `hbm_used_blocks` / `hbm_capacity_blocks` 只是事件上下文参数。
- standalone DDR lookup miss event 使用 `cache_tier=ddr`；S7-D 是否保留 tiered lookup 中的双 miss event 需要单独评审。
- prompt 大于 capacity 时不 OOM，最终保留 suffix blocks，完整 prefix hit 可能为 0。

### 12.5 风险

- `DDRLRUCache` 与 `HBMCache` 结构相似，未来可能需要抽公共 helper；本 batch 为了可审查性暂不抽象。
- 当前复用 `HBMEvictionPolicy` protocol，名称仍带 HBM；协议本身是通用 block eviction interface。未来如有必要，可独立重命名为 `CacheEvictionPolicy`。
- DDR store event 已定义，但还没有 runner 消费；不能把 S7-C 解读为 Step7 DDR replay 已完成。

### 12.6 测试结果

目标测试：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_ddr_lru_cache.py \
  tests/unit/cache/test_hbm_lru_cache.py \
  tests/unit/cache/test_cache_events.py \
  tests/unit/cache/test_cache_event_sink.py \
  tests/unit/report/test_cache_event_writer.py
```

结果：

```text
29 passed
```

代码质量：

```bash
.venv/bin/python -m ruff check src/infertwin/cache tests/unit/cache tests/unit/report/test_cache_event_writer.py
.venv/bin/python -m ruff format --check src/infertwin/cache tests/unit/cache tests/unit/report/test_cache_event_writer.py
git diff --check
```

结果：

```text
passed
```

### 12.7 是否建议进入下一 Batch

建议进入 S7-D：TieredPrefixCache。

进入方式仍应遵循 Step7 门禁：先提交 S7-D 详细代码开发方案和原因，经用户评审通过后再写代码。
