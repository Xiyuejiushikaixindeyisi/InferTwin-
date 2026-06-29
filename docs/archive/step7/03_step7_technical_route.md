# Step7 技术路线与代码结构方案

状态：初步评审通过，细化方案待评审。

说明：本文是 Step7 初版技术路线。初审后新增的差异对比、修改意见和细化 Batch 顺序记录在：

```text
docs/step7/04_gap_analysis_and_refined_batches.md
```

后续 Batch 开发顺序、职责、边界和审批门禁以 `04_gap_analysis_and_refined_batches.md` 为准。本文保留初版设计背景和结构草图。

## 1. 产品目标

Step7 是核心仿真器能力：单实例 KV pooling。

给定 routed trace，每个请求仍按 `instance_uuid` 进入对应实例 replay。每个实例内部维护两级 cache：

```text
HBM tier
DDR/CPU tier
```

请求 lookup 时：

1. 先查 HBM prefix cache。
2. HBM miss 后继续查同实例 DDR/CPU prefix cache。
3. DDR/CPU miss 后才进入 miss tokens，参与 prefill compute。

输出 request metrics / sweep rows 时：

- `hbm_hit_tokens` 表示 HBM 命中。
- `ddr_hit_tokens` 表示 DDR/CPU tier 命中。
- `miss_tokens` 表示需要重新 prefill compute 的 token。
- `effective_hit_rate = (hbm_hit_tokens + ddr_hit_tokens) / prompt_tokens`。

Step7 暂时保持：

```text
kv_load_ms = 0
```

Step8 再根据 `ddr_hit_tokens` / `ddr_hit_blocks` / bytes 接入 KV load latency。

## 2. 范围边界

### 2.1 Step7 做

- 单实例两级 cache backend。
- HBM LRU + DDR/CPU LRU。
- tier-aware prefix lookup。
- tier-aware materialization / store。
- tier-aware eviction events。
- capacity sweep 输出 DDR hit tokens / rate。
- streaming runner 接入新 cache mode。
- 配置和 model runtime defaults 支持 DDR capacity。
- 严格单测、集成测试、合成 E2E。

### 2.2 Step7 不做

- 不做跨实例 remote hit。
- 不做 Mooncake global store。
- 不做 gateway routing。
- 不做真实 KV tensor、真实 RDMA / CPU-NPU copy。
- 不做 KV load latency，Step8 做。
- 不做 progressive chunk visibility，Step9 做。
- 不做 Decode / TPOT。
- 不做 complex Hybrid cache group 语义。
- 不做 lease / pin / refcount。

## 3. 关键设计判断

### 3.1 DDR hit 是否应该提升到 HBM

Step7 v1 建议采用：

```text
DDR hit counts as cached tokens, but does not automatically promote to HBM.
```

原因：

- 当前 replay 不建真实 load time，也不建 GPU physical slot。
- 如果 DDR hit 自动写入 HBM，会引入“load target block allocation”语义，接近真实 KV load 过程，应与 Step8 一起设计。
- Step7 首要目标是 tier-aware hit accounting 和 DDR resident lifecycle。

保留未来选项：

```text
promotion_policy:
  none
  on_lookup
  on_load_complete
```

Step7 v1 只实现 `none`。

### 3.2 HBM evict 是否写入 DDR

Step7 v1 建议采用：

```text
finish-time materialization writes miss blocks to both HBM and DDR/CPU tier.
HBM eviction does not automatically backfill DDR.
```

原因：

- 真实系统中 DDR/CPU tier 的 store 可能来自异步 offload / connector / external store，不一定由 HBM eviction 触发。
- 如果把 HBM eviction 直接解释成 DDR store，会混淆 eviction 与 offload。
- Step7 需要先明确“哪些 block 被写入 DDR”，不应隐式依赖 HBM victim。

后续可新增：

```text
store_policy:
  materialize_to_all_tiers
  store_on_hbm_evict
  store_on_finish_async
  reuse_frequency_gated
```

Step7 v1 只实现 `materialize_to_all_tiers`。

### 3.3 DDR capacity 与 model default cache

cache 容量属于模型运行参数，不属于硬件或实例本身。

建议扩展 model runtime defaults：

```yaml
default_cache:
  hbm_capacity_blocks: 4096
  ddr_capacity_blocks: 65536
  block_size_tokens: 128
  eviction_policy: lru
  pooling:
    enabled: true
    single_instance: true
    multi_instance: false
    ddr_enabled: true
    remote_enabled: false
```

Step7 仍允许 capacity sweep 覆盖 HBM capacity：

```text
effective_hbm_capacity_blocks = current sweep candidate
effective_ddr_capacity_blocks = model default ddr_capacity_blocks
```

第一版不 sweep DDR capacity。后续如需，可新增外围 sweep 能力。

### 3.4 Event schema 是否扩展

当前 `CacheEvent` 只有：

```text
cache_tier
hbm_used_blocks
hbm_capacity_blocks
```

Step7 需要新增 DDR 相关字段。

建议新增 v2-compatible 字段到同一 dataclass：

```text
ddr_used_blocks: int = 0
ddr_capacity_blocks: int = 0
source_tier: str = ""
target_tier: str = ""
load_tokens: int = 0
store_tokens: int = 0
```

事件类型建议：

```text
lookup_hit
lookup_miss
materialize
evict
store
```

cache_tier 表示事件发生的 tier：

- HBM hit: `cache_tier=hbm`
- DDR hit: `cache_tier=ddr`
- HBM materialize: `cache_tier=hbm`
- DDR store: `cache_tier=ddr`
- HBM evict: `cache_tier=hbm`
- DDR evict: `cache_tier=ddr`

如果担心破坏 CSV 兼容，也可以新增 `CacheEventV2`；但考虑现有事件消费主要走 dataclass fieldnames，Step7 可以直接扩展字段并更新测试。

### 3.5 Materialization policy

Step7 继续使用：

```text
FinishTimeMaterializationPolicy
```

但 cache backend 的 materialize 需要 tier-aware：

```text
materialize(miss_blocks)
-> hbm_tier.store(miss_blocks)
-> ddr_tier.store(miss_blocks)
```

progressive materialization 不在 Step7 实现。

## 4. 建议代码结构

### 4.1 新增或调整模块

建议代码结构：

```text
src/infertwin/cache/
  tiers.py                    # cache tier constants / enum-like strings
  tiered.py                   # TieredPrefixCache / HBM+DDR orchestrator
  ddr_lru.py                  # DDR/CPU LRU metadata store
  policies.py                 # 可选，通用 tier policy 抽象；若现有 eviction.py 足够可不新增
  events.py                   # 扩展 CacheEvent 字段和事件常量
  results.py                  # PrefixLookupResult 已有 ddr_hit_blocks，可扩展 tier stats

src/infertwin/config/
  model_runtime.py            # 扩展 ModelCacheDefaults
  model_registry.py           # 解析 ddr_capacity_blocks / pooling
  model_binding.py            # pooling fail-fast / consistency guard

src/infertwin/streaming/
  sweep.py                    # 按 runtime profile 创建 tiered cache factory
  replay.py                   # 如已有 streaming replay cache factory 入口，接入新 mode

src/infertwin/experiment/
  runner.py / sweep.py        # 小 trace path 可保持 legacy，或明确 Step7 主入口是 sweep-streaming

tests/unit/cache/
  test_ddr_lru_cache.py
  test_tiered_prefix_cache.py
  test_cache_events_tiered.py

tests/unit/config/
  test_model_runtime_pooling.py

tests/integration/
  test_step7_hbm_ddr_replay.py
  test_step7_streaming_capacity_sweep.py
```

### 4.2 核心类型草图

方案层类型，不是最终代码：

```python
@dataclass(frozen=True, slots=True)
class TierCapacity:
    tier: str
    capacity_blocks: int
    eviction_policy: str


@dataclass(slots=True)
class TierBlockMeta:
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


class TieredPrefixCache:
    def lookup_prefix(...) -> PrefixLookupResult: ...
    def materialize(...) -> None: ...
    def take_events(...) -> tuple[CacheEvent, ...]: ...
```

Step7 v1 中：

```text
TieredPrefixCache
  - hbm: HBMCache
  - ddr: DDRLRUCache
```

lookup 算法：

```text
cursor = 0
hbm_hits = scan contiguous blocks from cursor in HBM
cursor += len(hbm_hits)
ddr_hits = scan contiguous blocks from cursor in DDR
cursor += len(ddr_hits)
miss_blocks = blocks[cursor:]
```

注意：必须保持连续 prefix hit。不能出现“block0 HBM hit、block1 miss、block2 DDR hit”仍计入 block2 hit。

materialize 算法：

```text
for miss block:
  store into HBM tier
  store into DDR tier
```

如果单 prompt blocks 大于 HBM capacity，沿用现有 suffix blocks / eviction 行为；DDR capacity 也可能小于 prompt blocks，此时 DDR LRU 保留最后写入的一段 blocks。

## 5. 请求处理路径

Step7 后，一条 request 的处理路径应为：

```text
trace row
-> request parser
-> tokenizer / chat template
-> prefix block hash
-> instance runtime resolver
-> waiting queue
-> scheduler frontier lookup
   -> TieredPrefixCache.lookup_prefix()
      -> HBM contiguous lookup
      -> DDR contiguous lookup after HBM prefix
      -> miss blocks
   -> LookupMetrics.from_result()
      -> hbm_hit_tokens
      -> ddr_hit_tokens
      -> miss_tokens
-> vLLM-like scheduler schedules miss tokens only
-> latency backend estimates prefill compute from miss tokens
-> request finish
-> materialization policy
   -> TieredPrefixCache.materialize(miss_blocks)
      -> HBM materialize / evict
      -> DDR store / evict
-> request metrics / iteration metrics / cache event stats
```

Step7 仍保持：

- queue waiting time 不建模。
- kv load time 不计入 duration。
- decode / TPOT 不建模。

## 6. 与真实 vLLM / vLLM-Ascend 的差异

| 维度 | 真实 vLLM / vLLM-Ascend | Step7 计划 |
|---|---|---|
| 本地 HBM block | physical slot + ref_cnt + free queue | hash metadata + capacity |
| HBM eviction | lazy eviction on future allocation | 现有 immediate eviction，是否调整另行评审 |
| CPU/DDR offload | lookup / touch / allocate / async load / async store | 同实例 DDR LRU metadata store |
| delay-free | connector 可延迟释放本地 block | Step7 不建 delay-free |
| load latency | 真实传输 / copy | Step7 仅统计 DDR hit tokens，Step8 计入 latency |
| store completion | async 完成后可读 | Step7 finish-time materialization 后立即可读 |
| Mooncake global pool | Master / Client / Segment / Transfer Engine | 不实现，V2 / 后续 Step |

这些差异必须写入开发治理和 Step7 收口 review。

## 7. Batch 开发建议

这里给代码开发批次，不给函数级实现细节。每个 batch 开始前仍需单独审批。

注意：本节是初版 batch 草案。初审后已小范围重构，后续执行以：

```text
docs/step7/04_gap_analysis_and_refined_batches.md
```

为准。重构后的顺序是：

```text
S7-A Config / Schema Guard
S7-B CacheEvent Tier Schema
S7-C DDR LRU Tier
S7-D TieredPrefixCache
S7-E Streaming Runner Integration
S7-F Report / Metrics / E2E
S7-G Review / Docs / Archive
```

### S7-A：Schema / Config Guard

目标：

- 扩展 model runtime default cache schema。
- 增加 pooling 配置。
- V1 只允许 `single_instance=true`、`multi_instance=false`。
- 如果 `remote_enabled=true` 或 `ssd_enabled=true`，fail-fast。

验收：

- model registry 示例可解析。
- pooling false 走现有 HBM-only。
- pooling true 但 multi-instance true 失败。
- DDR capacity 缺失时失败或根据配置明确关闭。

### S7-B：DDR LRU Tier

目标：

- 新增 DDR/CPU tier metadata store。
- 实现 lookup contiguous、store、evict、events。
- 不接 replay。

验收：

- DDR hit 必须连续。
- capacity 满时 LRU evict。
- prompt 大于 DDR capacity 时不 OOM，保留后写入 blocks。
- events 包含 DDR tier。

### S7-C：TieredPrefixCache

目标：

- 新增 HBM + DDR orchestrator。
- lookup 先 HBM 后 DDR。
- materialize 写 HBM 和 DDR。
- events 从两个 tier 合并。

验收：

- HBM hit 优先于 DDR。
- HBM miss 后 DDR hit 计入 `ddr_hit_tokens`。
- 中间 miss 后后续 DDR block 不计入 hit。
- HBM / DDR eviction 互不污染。

### S7-D：Replay / Streaming Runner Integration

目标：

- 新增 replay/cache mode，例如 `batch_aware_hbm_ddr_lru`。
- streaming runner 根据 model runtime defaults 创建 tiered cache factory。
- legacy HBM-only mode 不变。

验收：

- 同一合成 trace 下，开启 DDR 后 `ddr_hit_tokens > 0`。
- `hbm_hit_tokens + ddr_hit_tokens + miss_tokens == prompt_tokens`。
- `kv_load_ms` 仍为 0。
- 多实例隔离：实例 A 的 DDR 不影响实例 B。

### S7-E：Report / Event / E2E

目标：

- cache event CSV 支持新字段。
- summary / capacity sweep 保留 DDR hit rate。
- 合成 E2E 覆盖 HBM small、DDR large 的命中效果。

验收：

- `capacity_sweep.csv` 中 `ddr_hit_tokens` / `ddr_hit_rate` 非零。
- cache events 有 HBM / DDR tier。
- streaming sweep 默认仍可关闭 event dump。
- 全量 pytest / ruff / format 通过。

### S7-F：Review / Docs / Archive

目标：

- 写 Step7 review。
- 更新主产品文档、核心技术路线、开发治理、全局记忆。
- 将 `docs/step7/` 移入 `docs/archive/step7/`。

验收：

- 说明 Step7 与 vLLM / vLLM-Ascend / Mooncake 的差异。
- 明确 Step8 要接 KV load latency。
- 明确 Step9 要接 progressive block visibility。

## 8. 风险与待用户决策

### 8.1 是否接受 HBM eviction 不自动写 DDR

建议接受。Step7 v1 用 finish-time materialization 同时写 HBM 和 DDR，避免把 offload 和 eviction 混成一个事件。

### 8.2 是否接受 DDR hit 不自动 promote HBM

建议接受。promotion 涉及 load target allocation 和 KV load completion，更适合 Step8 之后实现。

### 8.3 是否接受 Step7 仍保持 `kv_load_ms = 0`

建议接受。Step7 先把 tier hit accounting 做准，Step8 再接 latency。

### 8.4 是否接受扩展现有 CacheEvent 而非新增 CacheEventV2

建议接受扩展现有 `CacheEvent`。这是最小改动，但需要更新 CSV golden / tests。

### 8.5 是否接受 Step7 主入口优先支持 `sweep-streaming`

建议接受。大 trace 主路径已经是 streaming；legacy `simulate` / non-streaming `sweep` 可保持 HBM-only 或后续再接。

## 9. Step7 成功标准

Step7 完成时，InferTwin 应具备：

- 单实例 HBM + DDR/CPU 两级 prefix cache replay。
- DDR hit tokens / rate 可观测。
- HBM / DDR store / hit / evict events 可观测。
- `kv_load_ms` 保持 0，但 latency schema 已能识别未来接入点。
- 多实例隔离仍成立。
- 现有 HBM-only replay 语义不变。
- 为 Step8 KV load latency 和 Step9 progressive visibility 留好接口。
