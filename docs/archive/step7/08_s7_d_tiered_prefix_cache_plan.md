# S7-D：TieredPrefixCache 开发方案与执行记录

状态：已完成。

阶段类型：核心仿真器开发。

## 1. Batch 目标

S7-D 实现一个组合型 cache backend：

```text
TieredPrefixCache = HBMCache + DDRLRUCache
```

它实现 replay 已经消费的 `PrefixCache` 协议：

```text
lookup_prefix()
materialize()
take_events()
contains()
resident_blocks
```

S7-D 的目标是让一次 request lookup 能得到正确的 tier-aware result：

```text
HBM contiguous hit -> DDR contiguous hit -> final miss
```

并让 finish-time materialization 同时写入：

```text
HBM materialize + DDR store
```

S7-D 仍不接 streaming runner，不新增 `batch_aware_hbm_ddr_lru` mode。runner 接入放到 S7-E。

## 2. 为什么需要 S7-D

S7-A 已经让配置可以表达单实例 DDR/CPU pooling。

S7-B 已经让 cache event schema 可以表达 DDR tier。

S7-C 已经实现了独立 `DDRLRUCache`。

但 replay 当前只消费一个 `PrefixCache` 对象。如果直接把 HBM 和 DDR 逻辑塞进 runner，会让 replay 代码知道太多 cache tier 细节，破坏模块边界。

因此 S7-D 需要新增 `TieredPrefixCache`，把多级 cache 的组合逻辑封装在 cache backend 内部。replay 仍然只调用：

```python
cache.lookup_prefix(...)
cache.materialize(...)
cache.take_events()
```

这样 S7-E 接入 streaming runner 时只需要替换 cache factory，不需要改 replay state machine。

## 3. 当前代码现状

相关文件：

```text
src/infertwin/cache/base.py
src/infertwin/cache/hbm_lru.py
src/infertwin/cache/ddr_lru.py
src/infertwin/cache/results.py
src/infertwin/cache/events.py
src/infertwin/cache/materialization.py
src/infertwin/replay/event_loop.py
src/infertwin/streaming/replay.py
```

当前 replay 对 cache 的协议要求：

```python
class PrefixCache(Protocol):
    @property
    def resident_blocks(self) -> int: ...
    def contains(self, block_key: str) -> bool: ...
    def lookup_prefix(...) -> PrefixLookupResult: ...
    def materialize(...) -> None: ...
    def take_events(...) -> tuple[CacheEvent, ...]: ...
```

当前材料化策略：

```text
FinishTimeMaterializationPolicy -> cache.materialize(miss_blocks, finish_time_ms)
```

因此只要 `TieredPrefixCache.materialize()` 同时写 HBM 和 DDR，就能保持 replay 代码不变。

## 4. S7-D 核心语义

### 4.1 Lookup 顺序

S7-D 固定采用：

```text
1. HBM lookup over full request blocks.
2. DDR lookup over HBM miss tail only.
3. Final miss is DDR miss tail.
```

伪代码：

```python
hbm_lookup = hbm.lookup_prefix(blocks, now_ms, request_id, instance_uuid)
ddr_lookup = ddr.lookup_prefix(
    hbm_lookup.miss_blocks,
    now_ms,
    request_id,
    instance_uuid,
    hbm_used_blocks=hbm.resident_blocks,
    hbm_capacity_blocks=hbm.capacity_blocks,
)
return PrefixLookupResult(
    hbm_hit_blocks=hbm_lookup.hbm_hit_blocks,
    ddr_hit_blocks=ddr_lookup.ddr_hit_blocks,
    miss_blocks=ddr_lookup.miss_blocks,
)
```

### 4.2 连续 prefix 规则

Tiered lookup 只能从左到右连续命中。

示例：

```text
blocks:     b0 b1 b2 b3 b4
HBM:        hit hit miss
DDR:                 hit hit miss
result:    HBM=b0,b1  DDR=b2,b3  MISS=b4
```

反例：

```text
blocks:     b0 b1 b2
HBM:        hit miss
DDR:              miss hit
result:    HBM=b0  DDR=()  MISS=b1,b2
```

原因：

- prefix cache 的可复用段必须是连续 prefix。
- 不能跳过中间 miss 后继续把后续 block 算成 hit。
- 该规则与 vLLM full-attention prefix cache lookup 口径一致。

### 4.3 DDR hit 不 promote HBM

S7-D 仍遵守冻结决策：

```text
DDR hit 不自动写回 HBM。
```

原因：

- promotion 涉及 load target allocation。
- promotion 是否发生应绑定 KV load completion。
- 这属于 Step8+。

S7-D 中 DDR lookup hit 只影响：

- `PrefixLookupResult.ddr_hit_blocks`。
- DDR LRU recency。
- DDR hit event。

不会影响：

- HBM resident set。
- HBM capacity。
- HBM eviction。

### 4.4 Materialization 同时写 HBM 和 DDR

S7-D 固定采用：

```text
TieredPrefixCache.materialize(miss_blocks)
-> HBMCache.materialize(miss_blocks)
-> DDRLRUCache.store(miss_blocks)
```

事件顺序建议固定为：

```text
HBM materialize / evict events first
DDR store / evict events second
```

原因：

- replay 已经把 finish-time materialization 作为 request prefill 完成后的可见点。
- Step7 冻结决策是 finish-time 同时写 HBM 和 DDR。
- 固定事件顺序可以让 tests 和 review 更稳定。

### 4.5 HBM eviction 不 backfill DDR

S7-D 不把 HBM eviction 解释为 DDR store。

如果某个 block 因 HBM capacity 被淘汰，是否已经在 DDR 中，由 `TieredPrefixCache.materialize()` 的 DDR store 决定，而不是由 HBM victim event 决定。

原因：

- 避免把 offload 和 eviction 混成一个事件。
- 未来如果要模拟 store-on-evict，应新增明确 policy，而不是改变当前 `materialize_to_all_tiers` 语义。

## 5. Event 口径

### 5.1 保留 tier-scoped raw events

S7-D 建议保留 tier-scoped raw events：

```text
HBM miss 后如果 DDR hit，事件流中会出现：
  HBM lookup_miss
  DDR lookup_hit
```

这不是重复统计 request miss，而是表达：

- HBM tier 没有命中。
- DDR tier 命中了。

request-level 命中以 `PrefixLookupResult` / `LookupMetrics` 为准：

```text
hbm_hit_blocks + ddr_hit_blocks + miss_blocks
```

### 5.2 为什么不在 S7-D 去掉 HBM miss events

当前 `HBMCache.lookup_prefix()` 会为 HBM miss tail 发出 `lookup_miss` events。为了去掉这些 events，需要给 HBMCache 增加 silent lookup 或 partial lookup API，这会扩大 S7-D 范围并改变 HBM event 语义。

S7-D 不做这个调整，原因：

- 保持 HBMCache 行为稳定。
- event stats 原本就是 raw cache event stats，不是 request-level hit accounting。
- tier-scoped miss 有助于分析 HBM pressure。

如果后续认为 raw miss events 容易误导，可在 S7-F summary 中明确区分：

```text
raw tier events
request-level token accounting
```

### 5.3 S7-D event order

lookup event order：

```text
HBM lookup events
DDR lookup events
```

materialize event order：

```text
HBM materialize / evict events
DDR store / evict events
```

`take_events()` 应返回合并后的事件，并 drain 两个 tier。

## 6. 目标 API

新增文件：

```text
src/infertwin/cache/tiered.py
```

建议类型：

```python
@dataclass(frozen=True, slots=True)
class TieredCacheStats:
    hbm_resident_blocks: int
    hbm_capacity_blocks: int
    ddr_resident_blocks: int
    ddr_capacity_blocks: int
```

建议类：

```python
class TieredPrefixCache:
    def __init__(
        self,
        *,
        hbm: HBMCache,
        ddr: DDRLRUCache,
    ) -> None: ...

    @property
    def resident_blocks(self) -> int: ...

    @property
    def hbm_resident_blocks(self) -> int: ...

    @property
    def ddr_resident_blocks(self) -> int: ...

    def contains(self, block_key: str) -> bool: ...

    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
    ) -> PrefixLookupResult: ...

    def materialize(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str = "",
        instance_uuid: str = "",
    ) -> None: ...

    def take_events(self) -> tuple[CacheEvent, ...]: ...
```

### 6.1 `resident_blocks` 口径

`PrefixCache` 协议只有一个 `resident_blocks`。S7-D 建议：

```text
resident_blocks = hbm.resident_blocks + ddr.resident_blocks
```

同时提供：

```text
hbm_resident_blocks
ddr_resident_blocks
```

原因：

- replay 当前只需要 `PrefixCache` 协议兼容。
- tests / review 需要观察两层 resident。
- 后续 report 如果要展示分层 resident，应使用分层属性而不是 `resident_blocks`。

### 6.2 `contains()` 口径

建议：

```text
contains(block_key) == hbm.contains(block_key) or ddr.contains(block_key)
```

这是 cache backend 的整体 resident 判断，不代表一定能成为 request prefix hit，因为 prefix hit 还要求连续。

## 7. 代码编写方案

### D1. 新增 TieredPrefixCache

新增：

```text
src/infertwin/cache/tiered.py
```

实现内容：

- `TieredCacheStats`。
- `TieredPrefixCache.__init__()`。
- `resident_blocks`。
- `hbm_resident_blocks`。
- `ddr_resident_blocks`。
- `contains()`。
- `lookup_prefix()`。
- `materialize()`。
- `take_events()`。

实现原则：

- 不继承 `HBMCache` 或 `DDRLRUCache`。
- 只组合两个 tier。
- 不修改 `HBMCache` 和 `DDRLRUCache`。
- 不新增 policy 抽象。

### D2. 更新 package export

修改：

```text
src/infertwin/cache/__init__.py
```

新增：

```python
TieredCacheStats
TieredPrefixCache
```

### D3. 新增单测

新增：

```text
tests/unit/cache/test_tiered_prefix_cache.py
```

测试覆盖：

1. HBM 优先：
   - HBM 有 b0，DDR 也有 b0，lookup b0 应计 HBM hit，不计 DDR hit。
2. HBM miss 后 DDR contiguous hit：
   - HBM 有 b0，DDR 有 b1/b2，lookup b0/b1/b2/b3 -> HBM=b0，DDR=b1,b2，MISS=b3。
3. DDR 不允许跳过中间 miss：
   - HBM 有 b0，DDR 有 b2，lookup b0/b1/b2 -> DDR hit empty，MISS=b1,b2。
4. DDR hit 不 promote HBM：
   - lookup 后 HBM resident 不增加。
5. materialize 同时写 HBM 和 DDR：
   - store 后 HBM 和 DDR 都 contains block。
6. HBM capacity 小于 prompt blocks 时仍不 OOM：
   - HBM suffix retention，DDR 根据自己 capacity retention。
7. `contains()` 是任一 tier resident。
8. `resident_blocks` 是 HBM + DDR 总和，分层属性正确。
9. event order：
   - lookup: HBM events before DDR events。
   - materialize: HBM events before DDR events。
10. `take_events()` drain 两个 tier。

### D4. 不修改的文件

S7-D 不修改：

```text
src/infertwin/replay/
src/infertwin/streaming/
src/infertwin/experiment/
src/infertwin/report/
```

如果开发过程中发现必须修改这些文件，应暂停并重新评审，因为那说明 S7-D 越界进入 S7-E/S7-F。

## 8. 测试计划

优先运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_tiered_prefix_cache.py
```

再运行 cache backend baseline：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_hbm_lru_cache.py \
  tests/unit/cache/test_ddr_lru_cache.py \
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

## 9. S7-D 成功标准

S7-D 完成时应满足：

- `TieredPrefixCache` 实现 `PrefixCache` 协议。
- lookup result 满足 HBM contiguous -> DDR contiguous -> final miss。
- `hbm_hit_tokens + ddr_hit_tokens + miss_tokens` 保持不变量。
- DDR hit 不 promote HBM。
- materialize 同时写 HBM 和 DDR。
- event order 稳定。
- `take_events()` drain 两个 tier。
- HBM / DDR backend 原有单测不受影响。
- 未引入 replay 行为变化。

## 10. 对后续 Batch 的影响

S7-E 可以在 streaming runner 中新增 cache mode：

```text
batch_aware_hbm_ddr_lru
```

并创建：

```python
TieredPrefixCache(
    hbm=HBMCache(capacity_blocks=sweep_capacity),
    ddr=DDRLRUCache(capacity_blocks=model_default.ddr_capacity_blocks),
)
```

S7-F 可以基于 request metrics 和 event stats 做 E2E 验收：

- `ddr_hit_tokens > 0` 的合成 trace。
- trace row / instance row DDR hit rate。
- cache event dump 中有 DDR store / hit / evict。

## 11. 风险与边界

### 11.1 风险

- raw `lookup_miss_events` 会包含 HBM tier miss，即使后续 DDR hit；这不能直接解释成 request-level miss。
- `resident_blocks` 总和可能被误用为 HBM resident；因此后续 report 应优先使用分层字段。
- S7-D 不接 runner，开发完成后 DDR hit 仍不会出现在端到端 replay 输出里，直到 S7-E。

### 11.2 控制方式

- 在 S7-D tests 中明确 event order 和 tiered lookup result。
- 在 S7-F summary/report 中区分 raw tier events 与 request-level token accounting。
- S7-D 不修改 replay，确保核心 state machine 不被本 batch 影响。

## 12. 执行记录

### 12.1 做了什么

- 新增 `src/infertwin/cache/tiered.py`。
- 实现 `TieredCacheStats`。
- 实现 `TieredPrefixCache`：
  - `resident_blocks`。
  - `hbm_resident_blocks`。
  - `ddr_resident_blocks`。
  - `stats`。
  - `contains()`。
  - `lookup_prefix()`。
  - `materialize()`。
  - `take_events()`。
- `lookup_prefix()` 实现 HBM contiguous hit -> DDR contiguous hit -> final miss。
- `materialize()` 实现 HBM materialize -> DDR store。
- `take_events()` 合并并 drain 两个 tier events。
- 更新 `src/infertwin/cache/__init__.py`，导出 `TieredCacheStats` 和 `TieredPrefixCache`。
- 新增 `tests/unit/cache/test_tiered_prefix_cache.py`。

### 12.2 没有做什么

- 没有修改 replay / streaming replay。
- 没有新增 `batch_aware_hbm_ddr_lru` mode。
- 没有接 streaming runner。
- 没有修改 HBMCache / DDRLRUCache 的既有语义。
- 没有实现 DDR hit promotion。
- 没有实现 KV load latency。
- 没有实现 store-on-HBM-evict policy。

### 12.3 影响

- 新增一个 replay-compatible 的组合型 `PrefixCache` backend。
- HBM-only baseline 不受影响。
- DDR hit 现在可以通过 `TieredPrefixCache.lookup_prefix()` 进入 `PrefixLookupResult.ddr_hit_blocks`。
- 后续 S7-E 可以通过 cache factory 把 `TieredPrefixCache` 接入 streaming runner。

### 12.4 边界

- S7-D 只完成 backend 组合，不进入 runner。
- raw cache event stats 仍是 tier-scoped event 口径，不等价于 request-level miss/hit accounting。
- HBM miss 后 DDR hit 时，事件流会同时包含 HBM `lookup_miss` 和 DDR `lookup_hit`。
- `resident_blocks` 是 HBM + DDR 总和；分层 resident 应使用 `hbm_resident_blocks` / `ddr_resident_blocks`。

### 12.5 风险

- tier-scoped raw miss events 可能被误读为 request-level miss；S7-F summary/report 应明确区分 raw tier events 和 request-level token accounting。
- `TieredPrefixCache` 当前固定 materialize-to-all-tiers；未来 store-on-evict 或 promotion 需要新增 policy，不应静默改变本模式。
- 直到 S7-E 完成，DDR hit 仍不会出现在端到端 streaming replay 输出中。

### 12.6 测试结果

目标测试：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/cache/test_tiered_prefix_cache.py \
  tests/unit/cache/test_hbm_lru_cache.py \
  tests/unit/cache/test_ddr_lru_cache.py \
  tests/unit/cache/test_cache_events.py \
  tests/unit/cache/test_cache_event_sink.py \
  tests/unit/report/test_cache_event_writer.py
```

结果：

```text
37 passed
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

建议进入 S7-E：Streaming Runner Integration。

进入方式仍应遵循 Step7 门禁：先提交 S7-E 详细代码开发方案和原因，经用户评审通过后再写代码。
