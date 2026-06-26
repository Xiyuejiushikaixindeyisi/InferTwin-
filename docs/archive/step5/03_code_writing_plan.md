# Step5 Code Writing Plan

本文是 Step5 的代码编写方案，供 review 后执行。当前阶段只输出方案，不进入代码实现。

Step5 目标：

```text
在 Step4 batch-aware replay 基础上接入有限 HBM LRU cache，
补齐 KV block 生命周期、命中、materialization、淘汰和事件信号。
```

## 1. 开发总原则

- 不改 README 中已经冻结的 Core Semantics。
- 不改变 `BatchShape`、`ScheduledSlice`、`ttft_ms`、`scheduler_wait_ms` 的含义。
- 不把 vLLM physical slot allocation 搬进 HitFloor Step5。
- 不让 report 层重算 cache 分析。
- 先扩展 cache schema，再接 replay，再接 runner/report。
- 默认保留 `batch_aware_infinite_hbm`，新增有限 HBM 模式，不破坏 Step4。

## 2. 建议代码结构

新增或调整：

```text
src/hitfloor/cache/
  base.py              # PrefixCache protocol / common type boundary
  events.py            # CacheEvent schema and event serialization helpers
  eviction.py          # HBMEvictor protocol and eviction policy implementations
  hbm_lru.py           # Finite HBM cache implementation with LRU default
  infinite_hbm.py      # Existing infinite HBM implementation, adapt to protocol
  results.py           # Existing PrefixLookupResult

src/hitfloor/replay/
  event_loop.py        # Add cache_factory injection, keep event loop centralized
  metrics.py           # Add cache_events to BatchAwareReplayResult if needed

src/hitfloor/experiment/
  runner.py            # Add batch_aware_hbm_lru mode and write cache_events.csv

src/hitfloor/report/
  tables.py            # Existing CSV writer can be reused
  summary.py           # Add cache event summary for Step5 mode

configs/experiments/
  step5_hbm_lru.yaml   # Synthetic-friendly finite HBM config

tests/unit/cache/
  test_hbm_lru_cache.py
  test_eviction_policy.py
  test_cache_events.py

tests/unit/replay/
  test_batch_aware_replay_hbm_lru.py

tests/integration/
  test_step5_hbm_lru_runner.py
```

## 3. Cache Protocol

新增 `src/hitfloor/cache/base.py`：

```python
class PrefixCache(Protocol):
    def lookup_prefix(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str,
        instance_uuid: str,
    ) -> PrefixLookupResult:
        ...

    def materialize(
        self,
        blocks: tuple[PrefixBlock, ...],
        now_ms: float,
        request_id: str,
        instance_uuid: str,
    ) -> None:
        ...

    def take_events(self) -> tuple[CacheEvent, ...]:
        ...
```

说明：

- `request_id` 和 `instance_uuid` 进入 cache API，是为了 cache event 能准确归因。
- `InfiniteHBMCache` 可以兼容实现该 protocol。
- replay 只依赖 protocol，不依赖具体 cache class。

## 4. HBM LRU 数据结构

新增 `src/hitfloor/cache/hbm_lru.py`。

建议内部 dataclass：

```python
@dataclass(slots=True)
class HBMBlockMeta:
    block_key: str
    block_index: int
    token_count: int
    size_bytes: int
    created_time_ms: float
    last_access_time_ms: float
    last_access_seq: int
    hit_count: int = 0
    materialized_by_request_id: str = ""
    instance_uuid: str = ""
```

建议 cache class：

```python
class HBMCache:
    def __init__(
        self,
        *,
        capacity_blocks: int,
        evictor: HBMEvictor,
    ) -> None: ...
    @property
    def resident_blocks(self) -> int: ...
    @property
    def capacity_blocks(self) -> int: ...
    @property
    def eviction_policy(self) -> str: ...
    def contains(self, block_key: str) -> bool: ...
    def lookup_prefix(...) -> PrefixLookupResult: ...
    def materialize(...) -> None: ...
    def take_events(self) -> tuple[CacheEvent, ...]: ...
```

实现建议：

- `_blocks: dict[str, HBMBlockMeta]` 保存 resident blocks。
- `_access_seq: int` 提供 deterministic LRU tie-break。
- `_events: list[CacheEvent]` 保存事件。
- 不用 `OrderedDict` 隐式表达所有语义，避免后续难以解释 tie-break。
- eviction 时不在 cache 内硬编码 LRU，而是调用 evictor：

```python
victim = self._evictor.select_victim(self._blocks)
```

第一版默认 evictor 是 `LRUEvictor`。

## 4.1 Eviction Policy

新增 `src/hitfloor/cache/eviction.py`。

建议接口：

```python
class HBMEvictor(Protocol):
    name: str

    def select_victim(
        self,
        blocks: Mapping[str, HBMBlockMeta],
    ) -> HBMBlockMeta:
        ...
```

第一版实现：

```python
class LRUEvictor:
    name = "lru"

    def select_victim(
        self,
        blocks: Mapping[str, HBMBlockMeta],
    ) -> HBMBlockMeta:
        ...
```

LRU victim selection：

```text
min(_blocks.values(), key=(last_access_time_ms, last_access_seq, created_time_ms, block_key))
```

如果性能后续不足，再改 heap 或 ordered index。Step5 优先清晰可测。

后续如果接入其他淘汰算法，新增类：

```text
TLRUEvictor
TTLEvictor
FrequencyAwareEvictor
CostAwareEvictor
```

不要修改 `LRUEvictor` 已冻结语义。

## 5. CacheEvent Schema

新增 `src/hitfloor/cache/events.py`。

第一版字段：

```python
@dataclass(frozen=True, slots=True)
class CacheEvent:
    event_type: str
    timestamp_ms: float
    instance_uuid: str
    request_id: str
    block_key: str
    block_index: int
    token_count: int
    cache_tier: str
    reason: str
    eviction_policy: str
    hbm_used_blocks: int
    hbm_capacity_blocks: int
```

第一版枚举值用字符串常量即可：

```text
LOOKUP_HIT = "lookup_hit"
LOOKUP_MISS = "lookup_miss"
MATERIALIZE = "materialize"
EVICT = "evict"
CACHE_TIER_HBM = "hbm"
```

暂不引入复杂 enum，避免 CSV 序列化和文档解释变重。

## 6. Replay 接入

修改 `BatchAwareReplayEngine`：

```python
def __init__(
    *,
    scheduler: VllmLikeBatchScheduler,
    latency_backend: BatchLatencyBackend,
    shape_memo: ShapeMemo | None = None,
    cache_factory: Callable[[str], PrefixCache] | None = None,
) -> None:
```

默认：

```python
cache_factory = lambda instance_uuid: InfiniteHBMCache()
```

`run()` 中替换：

```python
cache = InfiniteHBMCache()
```

为：

```python
cache = self.cache_factory(instance_uuid)
```

`_ensure_lookup()` 和 `_apply_schedule_result()` 传入 `request_id`、`instance_uuid`：

```text
cache.lookup_prefix(..., request_id=state.request_id, instance_uuid=state.instance_uuid)
cache.materialize(..., request_id=state.request_id, instance_uuid=state.instance_uuid)
```

`BatchAwareReplayResult` 建议新增：

```python
cache_events: tuple[CacheEvent, ...] = ()
```

每个 instance replay 结束后 drain cache events：

```python
cache_events.extend(cache.take_events())
```

排序：

```text
(timestamp_ms, instance_uuid, request_id, event_type, block_index, block_key)
```

## 7. Runner 接入

新增 simulation mode：

```yaml
simulation:
  mode: batch_aware_hbm_lru
```

cache config：

```yaml
cache:
  block_size_tokens: 16
  policy: hbm
  eviction_policy: lru
  hbm_capacity_blocks: 4096
```

Runner 新增 `_run_batch_aware_hbm_lru(...)`：

- build scheduler。
- build fitted TTFT backend。
- build `cache_factory(instance_uuid) -> HBMCache(capacity_blocks=..., evictor=LRUEvictor())`。
- run replay。
- write:

```text
request_metrics.csv
iteration_metrics.csv
cache_events.csv
summary.md
```

不要把 `cache.policy` 偷偷用于 `batch_aware_infinite_hbm`。模式必须显式，避免配置误读。

## 8. Summary 输出

`summary.md` Step5 新增：

- simulation mode。
- HBM capacity blocks。
- eviction policy。
- peak resident blocks。
- materialized blocks。
- evicted blocks。
- lookup hit events。
- lookup miss events。
- final resident blocks。

仍保留 Step4：

- request count。
- instance count。
- P50/P90/P99 TTFT。
- P50/P90/P99 scheduler wait。
- latency backend details。
- not modeled items。

## 9. 测试计划

### 9.1 Unit: HBM LRU

文件：

```text
tests/unit/cache/test_hbm_lru_cache.py
tests/unit/cache/test_eviction_policy.py
```

覆盖：

- empty lookup 全 miss。
- materialize 后同 prompt 命中。
- prefix hit 只连续到第一个 miss。
- capacity 超限触发 eviction。
- hit 会刷新 LRU。
- `LRUEvictor` 独立测试 victim selection 和 deterministic tie-break。
- materialize 已存在 block 不重复占容量。
- 单个 prompt 大于 capacity 时 resident blocks 不超过 capacity。
- eviction 顺序 deterministic。

### 9.2 Unit: cache events

文件：

```text
tests/unit/cache/test_cache_events.py
```

覆盖：

- lookup_hit / lookup_miss / materialize / evict event schema。
- `take_events()` drain 后为空。
- event 中 instance/request/block/capacity 字段完整。

### 9.3 Unit: replay + finite HBM

文件：

```text
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
```

覆盖：

- materialization 只在 finish 后可见。
- eviction 后重复 prompt 不再命中被淘汰的 prefix。
- zero-miss fast-finish 在有限 HBM 下仍成立。
- 多实例隔离，相同 prompt 不跨实例命中。

### 9.4 Integration: runner

文件：

```text
tests/integration/test_step5_hbm_lru_runner.py
```

覆盖：

- synthetic trace 跑通 `batch_aware_hbm_lru`。
- 输出 4 个文件：

```text
request_metrics.csv
iteration_metrics.csv
cache_events.csv
summary.md
```

- summary 包含 finite HBM LRU 信息。
- request metrics 中 hit/miss 与 cache_events 可互相校验。

## 10. 建议开发批次

### Batch A: Cache schema, HBMCache, and LRUEvictor

修改：

```text
src/hitfloor/cache/base.py
src/hitfloor/cache/events.py
src/hitfloor/cache/eviction.py
src/hitfloor/cache/hbm_lru.py
src/hitfloor/cache/infinite_hbm.py
tests/unit/cache/test_hbm_lru_cache.py
tests/unit/cache/test_eviction_policy.py
tests/unit/cache/test_cache_events.py
```

验收：

- cache unit tests passed。
- `InfiniteHBMCache` 仍能通过旧 replay tests。
- 不接入 `BatchAwareReplayEngine`。

Batch A 不接 replay 的原因：

- Batch A 只验证 cache 层自身的生命周期、事件和 eviction policy。
- 接入 `BatchAwareReplayEngine` 会改变 replay API，需要新增 `cache_factory`、传递 `request_id` / `instance_uuid`、收集 `cache_events`，并影响 replay result schema。
- replay 接入会引入 first-schedule-time lookup、zero-miss fast-finish、finish-time materialization 等 Step4 语义回归风险，应放在 Batch B 单独 review。
- 这样能先把 cache correctness 固定住，再验证 replay integration。

### Batch B: Replay cache factory

修改：

```text
src/hitfloor/replay/event_loop.py
src/hitfloor/replay/metrics.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
```

验收：

- Step4 replay tests passed。
- 新有限 HBM replay tests passed。
- 默认 `BatchAwareReplayEngine` 不传 cache_factory 时行为不变。

### Batch C: Runner/report integration

修改：

```text
src/hitfloor/experiment/runner.py
src/hitfloor/report/summary.py
configs/experiments/step5_hbm_lru.yaml
tests/integration/test_step5_hbm_lru_runner.py
```

验收：

- `batch_aware_hbm_lru` 输出 request/iteration/cache_events/summary。
- summary 和 `cache_events.csv` 标明 eviction policy = `lru`。
- 默认 config 仍跑 `batch_aware_infinite_hbm`。

### Batch D: Synthetic E2E and documentation closure

修改：

```text
docs/development_status.md
docs/global_memory.md
docs/step5/README.md
```

验收：

- 全量 pytest passed。
- 合成数据 E2E 展示：
  - capacity 足够时重复 prompt 命中。
  - capacity 不足时发生 eviction。
  - eviction 后命中下降。
  - 多实例不共享 HBM。

## 11. 审批点

进入代码前建议确认：

已确认：

- Step5 第一版容量单位固定为 `hbm_capacity_blocks`。
- Step5 不把淘汰逻辑写死为单一 `evict_lru` 函数，而是抽成 evictor / eviction policy 类。
- `cache_events.csv` 是 Step5 标准输出。
- Step5 runner 模式名称使用 `batch_aware_hbm_lru`。
- 单 prompt 大于 capacity 时，接受保留 suffix blocks、后续 prefix hit 可能为 0。
- Step5 第一版不建 pinned/refcount，仅保留 finish-time materialization + eviction policy。
