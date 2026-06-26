# Step5 Review Follow-Up Modification Plan

本文是 Step5 代码 review 后的补充修改方案。当前阶段只沉淀方案，不进入代码实现。

本轮 review 结论：

1. `cache_events.csv` 改为 streaming writer，避免大 trace 下把所有 cache events 全量保存在内存里。
2. 保留 finish-time materialization，但必须在文档中明确它与真实 vLLM / vLLM-Ascend 行为的差异、采用原因和边界。
3. eviction 接口从无状态 victim selector 升级为 stateful policy，使后续淘汰算法可以通过 queue / priority 更新表达策略。

## 1. 修改目标

本轮修改目标不是改变 Step5 的仿真语义，而是加固工程边界：

- `batch_aware_hbm_lru` 仍表示固定路由、多实例隔离、有限 HBM、LRU eviction、finish-time materialization。
- `hbm_capacity_blocks` 仍是 Step5 容量单位。
- `cache_events.csv` 仍是 Step5 标准输出。
- `ttft_ms = finish_time - arrival_time` 不变。
- 不新增 DDR、SSD、remote KV、cross-instance pooling、physical KV slot allocation、pinned/refcount。

本轮允许改变的是内部接口和输出实现方式：

- cache event 从 replay result 全量收集改为 runner/report streaming 写出。
- summary 不再依赖完整 `Sequence[CacheEvent]`，改为依赖 streaming 过程中累计的统计。
- eviction policy 从 `select_victim(blocks)` 扩展为带 hook 的 stateful policy。

## 2. Streaming Cache Events

### 2.1 当前问题

当前链路：

```text
HBMCache._events: list[CacheEvent]
  -> BatchAwareReplayEngine._run_instance()
  -> BatchAwareReplayResult.cache_events: tuple[CacheEvent, ...]
  -> ExperimentRunner.write_csv_table(cache_events_path, all_events)
  -> summary reads all_events again
```

这在小规模合成测试中清晰，但对 2 小时高峰 trace 有内存风险：

- lookup miss 可能为每个 block 产生 event。
- materialize 可能为每个 miss block 产生 event。
- eviction 也可能为每个被淘汰 block 产生 event。
- event 数量与请求数、prompt blocks 和容量压力相关，不能假设足够小。

### 2.2 新增 event sink 边界

新增 cache event sink 抽象，职责是消费 cache events 并维护统计，不参与 cache 逻辑、scheduler 逻辑和 report 分析逻辑。

建议新增文件：

```text
src/hitfloor/cache/event_sink.py
src/hitfloor/report/cache_events.py
tests/unit/cache/test_cache_event_sink.py
```

建议接口：

```python
class CacheEventSink(Protocol):
    def emit_many(self, events: Iterable[CacheEvent]) -> None:
        ...

    @property
    def stats(self) -> CacheEventStats:
        ...

    def snapshot_events(self) -> tuple[CacheEvent, ...]:
        ...
```

`snapshot_events()` 的语义：

- `InMemoryCacheEventSink` 返回收集到的 events，只用于小规模 unit / integration tests。
- `CsvCacheEventWriter` 返回空 tuple，因为标准 runner 输出走 streaming 文件。
- `NullCacheEventSink` 返回空 tuple，用于不关心 cache events 的调用路径。

### 2.3 Streaming writer

新增 `CsvCacheEventWriter`，由 report 层负责文件 IO：

```python
class CsvCacheEventWriter:
    def __init__(self, path: str | Path) -> None: ...
    def __enter__(self) -> CsvCacheEventWriter: ...
    def __exit__(self, exc_type, exc, tb) -> None: ...
    def emit_many(self, events: Iterable[CacheEvent]) -> None: ...
    @property
    def stats(self) -> CacheEventStats: ...
    def snapshot_events(self) -> tuple[CacheEvent, ...]: ...
```

实现要求：

- 打开文件时立即写 header，保证即使没有 event，`cache_events.csv` 也有稳定 schema。
- 每次 `emit_many()` 逐行写入，不把所有 events 转成 list。
- 每写一条 event，同步更新 `CacheEventStats`。
- `CsvCacheEventWriter` 只序列化 `CacheEvent`，不重新计算 hit/miss/evict。
- 文件关闭由 context manager 管理。

`CacheEventStats` 建议字段：

```python
@dataclass(frozen=True, slots=True)
class CacheEventStats:
    total_events: int
    lookup_hit_events: int
    lookup_miss_events: int
    materialize_events: int
    evict_events: int
    peak_hbm_used_blocks: int
    final_hbm_used_blocks: int
```

`summary.md` 使用 `CacheEventStats`，不再要求拿到完整 event 列表。

### 2.4 Replay 接入方式

`BatchAwareReplayEngine.run()` 增加可选 sink 参数：

```python
def run(
    self,
    requests: list[SimulationRequest],
    *,
    cache_event_sink: CacheEventSink | None = None,
) -> BatchAwareReplayResult:
    ...
```

推荐默认行为：

- 未传 sink 时使用 `NullCacheEventSink`，不保存 cache events。
- 测试如果需要断言 events，显式传 `InMemoryCacheEventSink`。
- runner 的 `batch_aware_hbm_lru` 路径显式传 `CsvCacheEventWriter`。

replay 层 drain 规则：

```text
prepare scheduler frontier
  -> lookup emits events
  -> drain cache.take_events() to sink

apply schedule result
  -> materialize / evict emits events
  -> drain cache.take_events() to sink

instance replay finish
  -> final drain
```

这样 `HBMCache` 内部 `_events` 只暂存单个 replay 阶段产生的 events，不跨完整 trace 累积。

### 2.5 输出顺序

Streaming writer 不再对所有实例的 events 做全局内存排序。

标准顺序定义为：

- 实例处理顺序 deterministic，由 `_group_by_instance()` 的稳定顺序决定。
- 单实例内部按 replay 发生顺序写出，时间单调。
- 跨实例 events 不保证全局 `timestamp_ms` 单调。

原因：

- 多实例当前是隔离 replay，实例之间不互相影响。
- 为了全局时间排序，需要重新把所有 events 收集到内存，或者引入 per-instance temp file + k-way merge；这与本轮 streaming 目标冲突。
- `timestamp_ms` 字段仍是事件时间的权威来源；需要全局时间序的离线分析可以读取 CSV 后自行排序。

测试应固定：

- 输出 schema 稳定。
- 同一输入多次运行，CSV 行顺序稳定。
- 单实例内部 event 顺序符合 lookup、materialize、evict 发生顺序。
- summary stats 与 CSV 行数、event type counts 一致。

## 3. Finish-Time Materialization 文档补充

### 3.1 当前 HitFloor 行为

HitFloor Step5 采用 finish-time materialization：

```text
request lookup at first scheduler consideration
  -> miss blocks enter compute path
  -> request prefill finishes at finish_time
  -> all miss blocks materialize into HBM cache
  -> later requests can hit these blocks
```

请求 prefill 完成前，miss blocks 不对其他 request 可见。

### 3.2 与真实 vLLM / vLLM-Ascend 的差异

真实 vLLM / vLLM-Ascend 更接近运行时 physical block manager：

- scheduler 按 iteration 推进 prefill / decode。
- chunked prefill 可能让一个长 prompt 分多轮完成。
- full blocks 在运行过程中可能逐步进入 prefix cache index。
- physical KV blocks 可能存在 refcount、free queue、active allocation、swap / offload、remote KV 等状态。
- 某些在 HitFloor 中要等 finish 后才可见的 blocks，在真实系统中可能更早被 cache index 看见。

HitFloor Step5 不模拟这些运行时细节。

### 3.3 采用 finish-time materialization 的原因

采用该简化的原因：

- 当前 fitted TTFT backend / batch latency backend 只输出 iteration duration 和 finish time，不输出每个 block 的完成时间。
- 如果做逐 block materialization，需要新增 block-level finish event 或 sub-iteration timeline。
- 如果同时仿真 physical slot allocation，需要引入 pinned/refcount/free queue，这会把 Step5 从 cache reuse 仿真推进到 runtime memory manager 仿真。
- finish-time materialization 更保守：不会让 in-flight request 产生过早命中。
- 当前 Step5 的核心目标是验证有限 HBM cache hit、eviction 和 replay 闭环，不追求物理执行细节完全一致。

### 3.4 边界声明

文档需要更新：

- `docs/step5/02_offline_hbm_lru_design.md`
- `docs/step5/README.md`
- 根目录 `README.md` 的 frozen semantics section，如果需要

必须写清：

- Step5 的 `HBMCache` 表示 request finish 后可复用的 prefix cache resident metadata。
- Step5 不表示真实 prefill 执行过程中的 physical KV slot table。
- `materialize` 的时间是 `finish_time_ms`，不是 block compute 完成的真实时间。
- 未来如果要贴近 vLLM 逐步 cache full blocks，应新增独立模式，例如 `batch_aware_hbm_lru_progressive`，不要静默改变 `batch_aware_hbm_lru` 的语义。

## 4. Stateful Eviction Policy

### 4.1 当前问题

当前接口：

```python
class HBMEvictor(Protocol):
    name: str

    def select_victim(
        self,
        blocks: Mapping[str, EvictableBlock],
    ) -> EvictableBlock:
        ...
```

这对 LRU 可以工作，因为 LRU 可以扫描 `last_access_time_ms` 和 `last_access_seq`。

但它不足以表达后续策略：

- queue-based LRU / FIFO。
- LFU / LRU-K。
- size-aware policy。
- tenant-aware policy。
- sparse-attention-aware policy。
- future Mooncake / multi-level cache promotion policy。

这些策略通常不是在 eviction 时才计算，而是在 hit、insert、remove 时持续维护队列或优先级。

### 4.2 新接口

将接口升级为 stateful policy。

建议重命名：

```text
HBMEvictor -> HBMEvictionPolicy
LRUEvictor -> LRUEvictionPolicy
```

为了降低一次性改动风险，可以保留兼容 alias：

```python
HBMEvictor = HBMEvictionPolicy
LRUEvictor = LRUEvictionPolicy
```

建议接口：

```python
class HBMEvictionPolicy(Protocol):
    name: str

    def on_insert(self, block: EvictableBlock) -> None:
        ...

    def on_access(self, block: EvictableBlock, *, reason: str) -> None:
        ...

    def on_remove(self, block: EvictableBlock) -> None:
        ...

    def select_victim(
        self,
        blocks: Mapping[str, EvictableBlock],
    ) -> EvictableBlock:
        ...
```

hook 语义：

- `on_insert`: 新 block materialize 进入 HBM 后调用。
- `on_access`: lookup hit 或 materialize existing block 后调用。
- `on_remove`: eviction 从 HBM 移除 block 后调用。
- `select_victim`: capacity pressure 时选择 victim。

### 4.3 LRU policy 实现

`LRUEvictionPolicy` 使用 `OrderedDict[str, None]` 或等价结构维护 recency queue：

```text
oldest <---------------- newest
```

行为：

- `on_insert(block)`: 将 block 放到 newest。
- `on_access(block)`: 将 block 移到 newest。
- `on_remove(block)`: 从 queue 删除。
- `select_victim(blocks)`: 返回 queue 中最 old 且仍 resident 的 block。

`HBMBlockMeta` 仍保留：

- `created_time_ms`
- `last_access_time_ms`
- `last_access_seq`
- `hit_count`

这些字段用于 debug、event、summary 和可测性，但 LRU victim 选择由 policy queue 决定。

### 4.4 HBMCache 调用位置

`HBMCache` 负责在生命周期节点调用 policy hook：

```text
lookup_prefix hit
  -> _touch(meta)
  -> policy.on_access(meta, reason="lookup_hit")

materialize existing
  -> _touch(meta)
  -> policy.on_access(meta, reason="materialize_existing")

materialize new
  -> evict until capacity has room
  -> insert meta
  -> policy.on_insert(meta)
  -> emit materialize

evict_one
  -> victim = policy.select_victim(_blocks)
  -> removed = _blocks.pop(victim.block_key)
  -> policy.on_remove(removed)
  -> emit evict
```

重要说明：

- eviction 仍发生在 `materialize()` 的 capacity pressure 阶段。
- priority / queue 更新不是只在 eviction 时发生，而是在 hit / insert / remove 时持续发生。
- 这样可以满足后续基于 queue priority 的淘汰算法仿真。

### 4.5 失败行为

policy 与 cache 状态不一致时，不应静默修复。

建议：

- `select_victim()` 在 cache 非空但 policy queue 无可用 resident block 时抛出 `ValueError`。
- `HBMCache` 不吞掉该错误。
- 测试覆盖 policy hook 顺序和 LRU victim 稳定性。

## 5. 代码修改清单

### 5.1 新增文件

```text
src/hitfloor/cache/event_sink.py
src/hitfloor/report/cache_events.py
tests/unit/cache/test_cache_event_sink.py
tests/unit/report/test_cache_event_writer.py
```

### 5.2 修改文件

```text
src/hitfloor/cache/eviction.py
src/hitfloor/cache/hbm_lru.py
src/hitfloor/cache/base.py
src/hitfloor/replay/event_loop.py
src/hitfloor/replay/metrics.py
src/hitfloor/experiment/runner.py
src/hitfloor/report/summary.py
tests/unit/cache/test_eviction_policy.py
tests/unit/cache/test_hbm_lru_cache.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
tests/integration/test_step5_hbm_lru_runner.py
tests/integration/test_step5_hbm_lru_e2e.py
docs/step5/02_offline_hbm_lru_design.md
docs/step5/README.md
README.md
```

## 6. 开发批次

### Batch E1: Cache event sink and streaming writer

实现：

- 新增 `CacheEventStats`。
- 新增 `NullCacheEventSink`。
- 新增 `InMemoryCacheEventSink`。
- 新增 `CsvCacheEventWriter`。
- `CsvCacheEventWriter` 打开文件即写 header。

测试：

- `InMemoryCacheEventSink` 保留 events 且 stats 正确。
- `CsvCacheEventWriter` 对空 events 也写稳定 header。
- `CsvCacheEventWriter` streaming 写入后，CSV 行数和 stats 一致。

### Batch E2: Replay and runner integration

实现：

- `BatchAwareReplayEngine.run(..., cache_event_sink=...)`。
- replay 在 lookup / materialize 后 drain cache events 到 sink。
- `BatchAwareReplayResult` 新增 `cache_event_stats`。
- runner 的 `batch_aware_hbm_lru` 使用 `CsvCacheEventWriter`。
- `summary.md` 使用 `cache_event_stats`，不再依赖完整 `cache_events` tuple。

测试：

- runner 输出 `cache_events.csv`，不依赖 `replay_result.cache_events`。
- summary counts 与 `cache_events.csv` 一致。
- direct replay 如需 events，测试显式使用 `InMemoryCacheEventSink`。
- 默认 direct replay 不保存 events，避免隐藏内存成本。

### Batch E3: Stateful eviction policy

实现：

- `HBMEvictionPolicy` protocol。
- `LRUEvictionPolicy` stateful queue。
- 保留兼容 alias，或一次性更新 import 名称。
- `HBMCache` 在 hit、existing materialize、insert、remove 时调用 policy hook。

测试：

- hit 会刷新 LRU queue，capacity pressure 时不淘汰刚 hit 的 block。
- insert 顺序决定初始 LRU。
- remove 会更新 policy queue。
- policy/cache 不一致时失败，不静默跳过。

### Batch E4: Documentation and E2E verification

实现：

- 更新 finish-time materialization 文档。
- 更新 README frozen semantics。
- 更新 Step5 README 当前状态。

测试：

- `pytest tests/unit/cache tests/unit/report tests/unit/replay/test_batch_aware_replay_hbm_lru.py`
- `pytest tests/integration/test_step5_hbm_lru_runner.py tests/integration/test_step5_hbm_lru_e2e.py`
- full `pytest`

如果 `.venv` 中仍未安装 `ruff`，本轮不自动安装，只在最终结果中说明未运行 ruff 的原因。

## 7. 验收标准

代码验收：

- `batch_aware_hbm_lru` 在大 trace 下不再把所有 cache events 存入 `BatchAwareReplayResult`。
- `cache_events.csv` 由 streaming writer 生成。
- `summary.md` 的 cache event 聚合来自 streaming stats。
- LRU policy 是 stateful queue，不再只是 eviction 时扫描 metadata。
- `HBMCache` 不包含具体 LRU 逻辑，只调用 eviction policy hook。
- 现有 Step5 E2E 语义不变：有限 HBM、capacity eviction、多实例隔离、finish-time materialization。

文档验收：

- 清楚说明 finish-time materialization 与真实 vLLM / vLLM-Ascend 的差异。
- 清楚说明采用 finish-time materialization 的原因。
- 清楚说明未来如果做 progressive materialization，应新增模式，不修改 `batch_aware_hbm_lru` 语义。
- 清楚说明 streaming `cache_events.csv` 的行顺序不保证跨实例全局时间排序，`timestamp_ms` 是事件时间权威字段。

## 8. 暂不做

本轮不做：

- Progressive block materialization。
- Physical KV slot allocation。
- pinned / refcount。
- DDR / SSD / remote KV。
- Gateway routing simulation。
- Cross-instance pooling。
- 多级 cache promotion / demotion。
- 全局 cache event 外部 merge sort。
