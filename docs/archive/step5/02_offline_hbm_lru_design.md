# Step5 Offline HBM LRU Design

本文定义 HitFloor Step5 的产品级技术设计：在 Step4 fixed-routing, multi-instance isolated replay 基础上，将每个实例的无限 HBM prefix cache 替换为有限容量 HBM LRU cache。

## 1. Step5 范围

Step5 只做：

- 固定路由、多实例隔离 replay。
- 每个 `instance_uuid` 一个独立 HBM cache。
- HBM 容量有限。
- HBM eviction policy 第一版为 LRU，并通过 evictor 类封装。
- cache 只保存 hash-only `PrefixBlock` metadata。
- prefix block lookup、hit、miss、touch、materialize、evict 事件。
- request / iteration metrics 继续沿用 Step4 口径。
- 新增可选 `cache_events.csv`。

Step5 不做：

- 请求路由仿真。
- 跨实例 KV 共享。
- DDR LRU。
- Mooncake 池化。
- remote KV transfer time。
- decode TPOT。
- 真实 vLLM physical KV slot allocation。
- 真实 HBM bytes 到 block 数的复杂估算。

DDR LRU 建议放到 Step6。

## 2. 容量单位

Step5 第一版已确认使用 block 数作为 HBM 容量单位：

```yaml
cache:
  policy: hbm_lru
  block_size_tokens: 16
  hbm_capacity_blocks: 4096
```

原因：

- HitFloor 当前核心对象就是 `PrefixBlock`。
- vLLM prefix cache eviction 也是 block 级语义。
- 用 block 数可直接测试 eviction 边界。
- GB/HBM 到 block 数的换算依赖模型层数、hidden size、kv heads、dtype、TP 等参数，可在后续新增 `capacity.py` 明确转换，避免 Step5 混入硬件估算误差。

后续可以新增：

```yaml
cache:
  hbm_capacity:
    unit: gb
    value: 40
```

但不应改变 `hbm_capacity_blocks` 的含义。

## 3. 生命周期模型

Step5 使用离线生命周期，不复制 vLLM 的完整 `ref_cnt` 和 free queue。

建议状态：

```text
absent
cached
evicted
```

其中 `cached` block 有以下 metadata：

```text
block_key
block_index
token_count
size_bytes
created_time_ms
last_access_time_ms
last_access_seq
hit_count
materialized_by_request_id
instance_uuid
```

Step5 第一版不单独建 `pinned` 状态。

理由：

- Step4 已冻结 materialization only after finish。
- 未完成 request 的 miss blocks 不在 cache 中，因此不会被其他 request 命中，也不需要在 cache 内 pin。
- 已命中的 cached blocks 在 lookup 时 touch，之后 request 计算期间是否 pin 对离线 TTFT 第一版影响很小。
- Step5 不仿真 vLLM physical KV slot allocation。`HBMCache` 表示“可复用 prefix cache 的 resident metadata”，不是运行时真实 HBM block table。
- `refcount` / `pin` 主要用于真实运行时避免 active request 的物理 block 被分配器回收。当前 replay 只用 cache hit 来减少 miss tokens，不读取真实 KV tensor，因此引入 refcount 会制造未被使用的复杂状态。
- 如果未来要模拟 vLLM physical slot pressure，可新增 `PinnedHBMCache` 或 `PhysicalBlockPool`，不要改变 Step5 `HBMCache + HBMEvictor` 的语义。

## 4. 核心操作

### 4.1 `lookup_prefix`

输入：

```text
blocks: tuple[PrefixBlock, ...]
now_ms: float
request_id: str
instance_uuid: str
```

输出：

```text
PrefixLookupResult(
  hbm_hit_blocks,
  ddr_hit_blocks=(),
  miss_blocks,
)
```

规则：

- 从 block 0 开始连续查找。
- 遇到第一个 missing block 后停止。
- 对所有 hit blocks 执行 touch。
- touch 更新 `last_access_time_ms` 和单调递增 `last_access_seq`。
- 产生 lookup/hit/miss 事件。
- 不提前查询 waiting queue 中所有 request，只查询 scheduler 本轮可能考虑的 frontier。

### 4.2 `materialize`

输入：

```text
blocks: tuple[PrefixBlock, ...]
now_ms: float
request_id: str
instance_uuid: str
```

规则：

- 只在 request prefill finish 后调用。
- 按 block index 从小到大 materialize。
- 如果 block 已存在，更新 touch metadata，不重复占容量。
- 如果 block 不存在，插入 HBM cache。
- 插入后如 `resident_blocks > hbm_capacity_blocks`，执行 eviction policy；第一版 policy 为 LRU。
- eviction 可淘汰任何当前 cached block，包括刚插入的较早 block。

注意：

如果单条请求的 block 数大于 HBM 容量，materialization 后可能只保留该请求 suffix blocks。

`suffix blocks` 指 prompt block 序列尾部的 blocks。例如 prompt 被切成：

```text
B0, B1, B2, B3, B4
```

其中 `B0` 是 prefix 起点，`B3, B4` 就是更靠后的 suffix blocks。

当 capacity 为 2 且按 `B0 -> B1 -> B2 -> B3 -> B4` 顺序 materialize 时，LRU 会在插入过程中持续淘汰最老 block，最终可能只保留 `B3, B4`。由于 prefix hit 必须从 `B0` 开始连续命中，如果 `B0` 已经被淘汰，后续相同 prompt 即使 `B3, B4` 仍在 HBM 中，prefix hit 也可能是 0。

这不会导致 OOM，原因是 Step5 的 HBM capacity 表示可复用 cache resident blocks 上限，而不是一次真实 prefill 执行所需的物理 KV slot 上限。`materialize()` 插入每个 block 前都会先通过 eviction policy 腾出容量，因此 `resident_blocks` 永远不超过 `hbm_capacity_blocks`。该行为仿真的是“请求完成后 cache 中还能留下哪些可复用 blocks”，不是“真实 vLLM 是否有足够物理 block 完成这次 prefill”。

如果未来要研究真实运行时 physical KV slot OOM 或 active sequence allocation，应新增 physical block pool / pinned cache 模型，不修改 Step5 `HBMCache` 的含义。

#### 4.2.1 Finish-Time Materialization 与真实 vLLM / vLLM-Ascend 的差异

HitFloor Step5 的 materialization 规则固定为：

```text
cache lookup at first scheduler consideration
  -> miss tokens are computed by replay iterations
  -> request prefill finishes at finish_time_ms
  -> all miss blocks materialize into HBM cache
  -> later requests can hit those blocks
```

在这个规则下，请求 prefill 完成前，miss blocks 不对其他 request 可见。

真实 vLLM / vLLM-Ascend 更接近 runtime physical block manager：

- scheduler 按 iteration 推进 prefill / decode。
- chunked prefill 会把长 prompt 拆成多轮执行。
- full KV blocks 可能在请求运行过程中逐步进入 prefix cache index。
- physical KV blocks 可能带有 active allocation、free queue、ref count、hash index、remote KV 等状态。
- 因此，某些在 HitFloor 中必须等到 `finish_time_ms` 后才可见的 blocks，在真实系统中可能更早可被后续调度观察到。

Step5 不采用逐步 full-block materialization，原因是：

- 当前 `fitted_ttft` / batch latency backend 只输出 iteration duration 和 request finish time，不输出 block-level finish time。
- 如果要精确逐 block materialize，需要新增 sub-iteration timeline 或 block finish event。
- 如果同时贴近真实 physical allocation，还需要引入 pinned/refcount/free queue/active sequence allocation，这会把 Step5 从 cache reuse replay 扩展成 runtime memory manager 仿真。
- finish-time materialization 更保守，不会给 in-flight request 制造过早命中。
- Step5 的目标是搭建有限 HBM cache hit、eviction、event、runner/report 的可维护仿真骨架，不追求物理执行细节完全一致。

边界要求：

- `batch_aware_hbm_lru` 的 `materialize` 时间就是 `finish_time_ms`。
- `HBMCache` 表示 request finish 后可复用的 prefix cache resident metadata，不是真实运行时 HBM physical block table。
- 如果未来需要更贴近 vLLM 的逐步 full-block 可见性，应新增独立模式，例如 `batch_aware_hbm_lru_progressive`，不要静默修改 `batch_aware_hbm_lru` 的语义。

### 4.3 `evict`

Step5 不把淘汰逻辑写成 cache 内部的单一 `evict_lru` 函数，而是抽成可扩展的 stateful eviction policy。

第一版默认实现是 LRU：

```text
LRUEvictionPolicy
```

`LRUEvictor` 保留为兼容 alias，避免旧 import 失效。后续如果需要仿真其他淘汰算法，应新增 policy 类，而不是修改 `LRUEvictionPolicy` 的语义。

接口：

```python
class HBMEvictionPolicy(Protocol):
    name: str

    def on_insert(self, block: HBMBlockMeta) -> None:
        ...

    def on_access(self, block: HBMBlockMeta, *, reason: str) -> None:
        ...

    def on_remove(self, block: HBMBlockMeta) -> None:
        ...

    def select_victim(
        self,
        blocks: Mapping[str, HBMBlockMeta],
    ) -> HBMBlockMeta:
        ...
```

第一版实现：

```python
class LRUEvictionPolicy:
    name = "lru"
```

`LRUEvictionPolicy` 使用 recency queue：

- `on_insert`: 新 block 放到队尾。
- `on_access`: lookup hit 或 materialize existing block 时移动到队尾。
- `on_remove`: eviction 删除 block 时从队列移除。
- `select_victim`: capacity pressure 时选择队首 block。

`HBMCache` 只负责：

- 在 block 生命周期节点调用 policy hook。
- 在 capacity 超限时调用 policy 选择 victim。
- 删除 victim block。
- 产生 `evict` event。
- 维护 cache resident metadata。

eviction policy 只负责：

- 维护自己的 queue / priority state。
- 根据自身 state 选择 victim。
- 不修改 cache。
- 不写 report。
- 不读取配置文件。

LRU 规则：

- 淘汰 recency queue 中最久未访问 block。
- policy/cache 状态不一致时显式失败，不静默修复。

- 每个被淘汰 block 产生 `evict` event。
- event 中记录 eviction reason：

```text
capacity
```

- event 中记录 eviction policy：

```text
lru
```

Step5 不支持手工 reset cache；如果后续需要，新增 `clear()` 和 `clear` event。

### 4.4 `take_events`

cache 层维护 event queue：

```text
take_events() -> tuple[CacheEvent, ...]
```

调用后清空队列。

report 层只负责序列化事件，不重新推导 hit/miss/evict。

## 5. 事件模型

建议新增 `src/hitfloor/cache/events.py`。

核心 schema：

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
    hbm_used_blocks: int
    hbm_capacity_blocks: int
```

建议 `event_type` 第一版只支持：

```text
lookup_hit
lookup_miss
materialize
evict
```

不要把事件做得过细，避免 `touch`、`contains` 这类内部动作污染报告。

可选扩展字段：

```text
hit_run_blocks
miss_run_blocks
```

如果需要请求级聚合，优先在 report summary 中聚合 `cache_events.csv`，不要在 cache 层混入 report 逻辑。

## 6. 与 Step4 Replay 的关系

Step4 当前链路：

```text
SimulationRequest
  -> RequestState
  -> cache.lookup_prefix()
  -> scheduler.schedule()
  -> latency_backend.estimate_iteration()
  -> finish_time
  -> cache.materialize(miss_blocks)
  -> request_metrics / iteration_metrics
```

Step5 保持这条主链路，只替换 cache 实现：

```text
InfiniteHBMCache
  -> HBMCache + LRUEvictor
```

因此 replay 层需要的最小改动是引入 cache protocol/factory：

```text
BatchAwareReplayEngine(cache_factory=...)
```

默认仍可使用 `InfiniteHBMCache`，保持 Step4 测试稳定。

## 7. 关键不变量

Step5 必须通过测试固定以下不变量：

- 相同输入多次运行，`request_metrics.csv`、`iteration_metrics.csv`、`cache_events.csv` 顺序稳定。
- `hbm_hit_blocks + miss_blocks == prompt_blocks`，DDR 仍为 0。
- lookup 只返回连续 prefix hit。
- materialization 只在 finish time 后可见。
- eviction 后对应 block 不再可命中。
- eviction victim 由 evictor 选择，LRU tie-break 必须稳定。
- HBM resident block 数永远不超过 capacity。
- hit 会刷新 LRU，最近被命中的 block 不应被优先淘汰。
- 多实例 cache 隔离，相同 prompt 在不同 `instance_uuid` 之间不共享 HBM。

## 8. 推荐产品口径

Step5 对外可以描述为：

```text
有限 HBM LRU replay
```

它表达的是：

- 请求已经按 trace 固定路由到实例。
- 每个实例有独立 HBM prefix cache。
- HBM capacity 用 block 数控制。
- HBM block 命中、生成、淘汰由离线 replay 产生。
- TTFT 仍由 fitted TTFT backend 根据 miss prefill tokens 给出。
