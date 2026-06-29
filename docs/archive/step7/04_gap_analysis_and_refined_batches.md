# Step7 差异对比、修改意见与 Batch 细化方案

状态：待用户评审。

## 1. 本轮已确认的冻结决策

以下决策已经通过初步评审，Step7 v1 不再反复讨论，除非用户显式改变产品边界：

1. Step7 v1 使用 finish-time materialization 同时写 HBM 和 DDR，避免把 offload 和 eviction 混成一个事件。
2. DDR hit 不自动 promote 到 HBM。promotion 涉及 load target allocation 和 KV load completion，放到 Step8 之后。
3. Step7 先把 tier hit accounting 做准，`kv_load_ms = 0`；Step8 再接 KV load latency。
4. 扩展现有 `CacheEvent`，不新增 `CacheEventV2`。
5. Step7 主入口优先支持 `sweep-streaming`；legacy `simulate` / non-streaming `sweep` 可保持 HBM-only 或后续再接。

## 2. 对比对象

本文件比较四套机制：

- InferTwin 当前核心仿真器。
- vLLM v1 本地 KV cache / prefix cache。
- vLLM-Ascend CPU offload / KV pool / Mooncake connector 增量。
- Mooncake Store 全局 KVCache pooling。

目标不是完全复刻真实系统，而是明确：

- 当前仿真器哪里已经对齐。
- 哪里是为了 offline simulation 做的合理简化。
- 哪里必须在 Step7 修改。
- 哪里应放到 Step8 / Step9 / V2。

## 3. KV cache 管理机制对比

| 维度 | InferTwin 当前 | vLLM v1 | vLLM-Ascend | Mooncake Store | Step7 修改意见 |
|---|---|---|---|---|---|
| KV 数据 | 只保存 block hash metadata | 保存真实 KV tensor slot + metadata | 本地 KV 仍走上游 slot，外部 CPU/store 另建元数据 | 分布式对象，真实 KV bytes 在 Segment | Step7 继续 hash-only，不保存 KV tensor |
| 本地 HBM 管理 | `HBMCache` 按 capacity immediate eviction | `BlockPool` + `KVCacheBlock` + ref_cnt + free queue + lazy eviction | 复用上游 HBM block 管理 | 不负责 vLLM 本地 HBM slot | Step7 不重写 HBM，保留 immediate eviction 差异并记录 |
| 外部 tier | 只有 `ddr_hit_blocks` 字段预留，实际恒 0 | `kv_offload` 有 OffloadingManager | CPUKVCacheManager / AscendStore / connector | DRAM / SSD / distributed Segment | Step7 新增单实例 DDR/CPU tier |
| Eviction policy | HBM stateful policy，当前 LRU | free queue 顺序 + lazy eviction | 本地同上，CPU/offload tier 可有独立 policy | Store 内部高水位 / 近似 LRU / allocator | Step7 给 DDR tier 独立 LRU，不改 HBM policy |
| Refcount / pin | 不建 | ref_cnt / touch / free | CPU offload 也 touch / free | Lease / soft pin / hard pin | Step7 不建 pin/refcount，只记录差异 |
| Store completion | finish 后 materialize 立即可见 | full block cache_blocks 后可 hit；真实执行中逐步更新 | CPU offload 异步 save 完成后可用 | PutEnd 后可读 | Step7 finish-time 后 DDR 立即可见；Step9 再处理 progressive |
| Scope | instance-local | engine-local | CPU offload 可进程/节点级，KV pool 可外部 | cluster-global | Step7 只做 instance-local |

### 3.1 结论

Step7 必须修改：

- 增加 `DDRLRUCache` 或等价单实例 DDR tier。
- 增加 `TieredPrefixCache`，统一 HBM + DDR lookup/materialize/events。
- 扩展 cache event schema，能表达 DDR used/capacity、source/target tier、load/store token 计数。
- 扩展 model runtime defaults，表达 DDR capacity 和 pooling flags。

Step7 不修改：

- 不重写当前 HBM immediate eviction。
- 不引入 physical slot/ref_cnt/pin。
- 不引入真实 async store/load。
- 不引入跨实例 global store。

## 4. Prefix cache 命中逻辑对比

| 维度 | InferTwin 当前 | vLLM v1 | vLLM-Ascend | Mooncake Store | Step7 修改意见 |
|---|---|---|---|---|---|
| Hit 粒度 | full prefix block hash | full block hash；不计 partial block | CPU offload 复用 vLLM block hash | 对象 key，可由上层 prefix index 组织 | 保持 full block prefix hit |
| 连续性 | 从 block0 连续命中，遇 miss 停止 | full attention 左到右，遇 miss 停止 | CPUKVCacheManager 同样 find_longest_cache_hit | Store 本身不定义 prefix 连续性 | Tiered lookup 必须保持跨 tier 连续 prefix |
| HBM hit | 已实现 | BlockPool hash map | 本地同 vLLM | 不适用 | 保持 |
| DDR hit | 未实现 | offload lookup 可返回 external computed tokens | CPU get_matched_num_and_touch 返回 CPU computed tokens | GetReplicaList / external prefix index | Step7 HBM miss 后查 DDR，计入 `ddr_hit_tokens` |
| HBM + DDR 混合 | 无 | local + external computed tokens 都进入 allocation | CPU hit 可补足 GPU miss | 由上层 connector/store 决定 | Step7 只允许 prefix 顺序：HBM contiguous 后 DDR contiguous |
| MTP / CP / runtime block size | 已有 conversion/accounting | vLLM scheduler accounting | vLLM-Ascend 遵循 vLLM 口径 | 不直接定义 | Step7 继续使用现有 conversion，不新增口径 |

### 4.1 Step7 tiered lookup 规则

Step7 v1 固定采用：

```text
cursor = 0
hbm_hit_blocks = contiguous lookup from blocks[cursor:]
cursor += len(hbm_hit_blocks)
ddr_hit_blocks = contiguous lookup from blocks[cursor:]
cursor += len(ddr_hit_blocks)
miss_blocks = blocks[cursor:]
```

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

原因：prefix cache 必须是连续 prefix，不能跳过中间 miss 后再把后续 block 算成 hit。

## 5. Prefix cache hit 统计机制对比

| 维度 | InferTwin 当前 | vLLM / vLLM-Ascend | Mooncake Store | Step7 修改意见 |
|---|---|---|---|---|
| 统计字段 | request metrics 有 `hbm_hit_tokens`、`ddr_hit_tokens`、`miss_tokens` | `num_cached_tokens` / local + external stats；usage 可拆 local/external | Store 只知道对象命中，不天然等价 token hit | 继续 request-level token accounting |
| Token 不变量 | HBM + DDR + miss = prompt_tokens | scheduler usage 中 cached tokens 按 block 对齐；最后 token重算 | 不定义 prompt token accounting | 必须保持现有不变量 |
| cached_tokens cap | 已有 `account_prefix_lookup()` 处理 prompt-1、CP/MTP/drop | vLLM 同类规则 | 不适用 | DDR hit 也经过同一 conversion |
| Hit rate | effective hit rate = HBM+DDR / prompt | local/external 可拆分 | 需要上层换算 | `capacity_sweep.csv` 输出 HBM/DDR/effective |
| KV load tokens | 当前无显式 request 字段 | external tokens 可触发 load | Get bytes / objects | Step7 可在 event/latency detail 预留，不计 ms |

### 5.1 Step7 必须维护的不变量

每条 request：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == prompt_tokens
effective_hit_tokens = hbm_hit_tokens + ddr_hit_tokens
```

capacity sweep row：

```text
sum(request.hbm_hit_tokens) == row.hbm_hit_tokens
sum(request.ddr_hit_tokens) == row.ddr_hit_tokens
sum(request.miss_tokens) == row.miss_tokens
row.total_hit_tokens == row.hbm_hit_tokens + row.ddr_hit_tokens
```

Step7 不改变 `cached_tokens` conversion 规则。DDR hit blocks 和 HBM hit blocks 都必须通过现有 `account_prefix_lookup()` 进行 token accounting。

## 6. 信号 / Event 对比

| 信号 | InferTwin 当前 | vLLM v1 | vLLM-Ascend | Mooncake Store | Step7 修改意见 |
|---|---|---|---|---|---|
| lookup hit | `lookup_hit` + `cache_tier=hbm` | prefix stats / block events | CPU cache stats / connector metadata | Get / lease | 保留，新增 DDR hit event |
| lookup miss | `lookup_miss` | 无完全等价 block event | CPU lookup miss stats | Get miss / fallback | 保留，miss event 可继续 tier=hbm 或统一 `cache_tier=none` 待实现时定 |
| materialize | `materialize` | `BlockStored` | CPU cache_blocks after send finish | PutEnd complete | HBM materialize + DDR store 分开记录 |
| store | 无 | offload prepare_store/complete_store | CPU save/store | PutStart/PutEnd | Step7 新增 `store`，用于 DDR |
| evict | `evict` + HBM fields | `BlockRemoved` on lazy eviction | CPU/offload eviction independent | Store eviction | 新增 DDR evict event |
| load | 无 | connector load metadata | async CPU/NPU load | Transfer Engine Get | Step7 不建 load event，最多预留 `load_tokens=0` |
| delay-free | 无 | connector request_finished delay | Mooncake / CPU offload 可 delay-free | Lease/pin | Step7 不建 |

### 6.1 CacheEvent 扩展建议

在现有 `CacheEvent` 上追加字段，保持旧字段不变：

```text
ddr_used_blocks: int = 0
ddr_capacity_blocks: int = 0
source_tier: str = ""
target_tier: str = ""
load_tokens: int = 0
store_tokens: int = 0
```

建议新增常量：

```text
CACHE_TIER_DDR = "ddr"
STORE = "store"
```

事件语义：

- HBM lookup hit：`event_type=lookup_hit, cache_tier=hbm`
- DDR lookup hit：`event_type=lookup_hit, cache_tier=ddr`
- lookup miss：继续 `event_type=lookup_miss`，实现时决定是否 `cache_tier=hbm` 或 `none`，但必须测试固定。
- HBM materialize：`event_type=materialize, cache_tier=hbm`
- DDR store：`event_type=store, cache_tier=ddr, store_tokens=block.token_count`
- DDR evict：`event_type=evict, cache_tier=ddr`

## 7. 修改意见汇总

### 7.1 Step7 必须改

1. Config/schema：
   - model default cache 增加 DDR capacity 和 pooling flags。
   - V1 guard：只允许 single-instance DDR/CPU pooling。
2. Cache event：
   - 扩展 `CacheEvent` 字段和 CSV writer tests。
3. Cache backend：
   - 新增 DDR LRU tier。
   - 新增 TieredPrefixCache。
4. Streaming runner：
   - 新增 `batch_aware_hbm_ddr_lru` mode。
   - 根据 instance runtime/model default 创建 tiered cache factory。
5. Tests / E2E：
   - 多实例隔离。
   - HBM hit / DDR hit / miss 不变量。
   - capacity sweep 中 DDR hit rate 非零。

### 7.2 Step7 不改，但必须记录差异

1. HBM immediate eviction vs vLLM lazy eviction。
2. 无 physical KV slot/ref_cnt/pin。
3. DDR store 立即完成，不建 async completion。
4. DDR hit 不 promote HBM。
5. `kv_load_ms = 0`。
6. finish-time materialization 可能低估长 prefill 中途复用，Step9 处理。

### 7.3 Step8 / Step9 / V2 再改

- Step8：根据 DDR hit blocks/tokens/bytes 接入 KV load latency。
- Step8+：可引入 promotion on load completion。
- Step9：progressive chunk/block visibility。
- V2：Hybrid cache group、cross-instance pooling、Mooncake global store、gateway、queue、decode/TPOT。

## 8. 细化后的 Batch 顺序

重构原则：

- 先扩展 schema 和事件，不改 replay。
- 再做独立 DDR tier。
- 再做 tiered orchestrator。
- 最后接入 streaming runner。
- 每个 batch 必须有独立验收，不能跨 batch 偷偷改语义。

### S7-A：Config / Schema Guard

类型：核心仿真器配置治理。

职责：

- 扩展 `ModelCacheDefaults` / model registry schema。
- 增加 `ddr_capacity_blocks`。
- 增加 pooling flags。
- 明确 V1 只支持 single-instance DDR/CPU pooling。

不负责：

- 不实现 DDR cache。
- 不改 replay。
- 不改 cache event。

输出：

- schema 类型。
- config guard。
- 示例 model registry。
- 单测。

进入下一批条件：

- HBM-only 旧配置仍通过。
- pooling true + missing DDR capacity fail-fast。
- pooling true + multi_instance true fail-fast。
- pooling true + remote/ssd true fail-fast。

### S7-B：CacheEvent Tier Schema

类型：核心事件 schema 扩展。

职责：

- 扩展 `CacheEvent` 字段。
- 新增 `CACHE_TIER_DDR` / `STORE` 常量。
- 更新 CSV cache event writer tests。
- 保持旧事件默认字段兼容。

不负责：

- 不实现 DDR cache。
- 不改变 HBMCache 语义。
- 不接 streaming runner。

输出：

- event schema。
- event writer 单测。
- HBM-only 旧 cache event 测试更新。

进入下一批条件：

- 旧 HBM events 仍可写 CSV。
- 新字段默认值稳定。
- `cache_events.csv` header 更新有测试覆盖。

### S7-C：DDR LRU Tier

类型：核心 cache backend 子模块。

职责：

- 新增 `DDRLRUCache` 或 `DDRTierCache`。
- 实现 hash-only metadata store。
- 实现 contiguous lookup。
- 实现 store / evict / take_events。
- DDR eviction policy 第一版固定 LRU。

不负责：

- 不接 HBM。
- 不接 replay。
- 不做 promotion。
- 不做 kv load latency。

输出：

- `src/infertwin/cache/ddr_lru.py`。
- 单测覆盖 lookup/store/evict/events。

进入下一批条件：

- DDR lookup 连续性正确。
- capacity 小于 prompt blocks 不 OOM。
- LRU touch / evict 确定性可测。
- DDR events 有 `cache_tier=ddr`。

### S7-D：TieredPrefixCache

类型：核心 cache backend orchestrator。

职责：

- 新增 `TieredPrefixCache`。
- 组合 HBMCache + DDRLRUCache。
- lookup 顺序：HBM contiguous -> DDR contiguous -> miss。
- materialize miss blocks 同时写 HBM 和 DDR。
- 合并 HBM / DDR events。

不负责：

- 不接 runner。
- 不改 scheduler。
- 不做 DDR hit promotion。
- 不做 HBM eviction backfill DDR。

输出：

- `src/infertwin/cache/tiered.py`。
- 单测覆盖 tiered lookup / materialize / event order / invariant。

进入下一批条件：

- HBM 优先。
- DDR 只补 HBM prefix 后的连续 miss。
- 中间 miss 后不允许后续 DDR hit。
- materialize 同时产生 HBM materialize 和 DDR store。

### S7-E：Streaming Runner Integration

类型：核心 replay 集成。

职责：

- 新增 cache mode：`batch_aware_hbm_ddr_lru`。
- `sweep-streaming` 根据 model runtime defaults 创建 tiered cache factory。
- HBM capacity 仍由 sweep candidate 覆盖。
- DDR capacity 从 model default 读取。
- legacy `simulate` / non-streaming `sweep` 保持 HBM-only。

不负责：

- 不做 report 美化。
- 不做 cache event dump 之外的新外围能力。
- 不做 DDR capacity sweep。

输出：

- streaming runner 集成。
- config 示例。
- 集成测试。

进入下一批条件：

- HBM-only mode 测试不变。
- DDR mode 下 `ddr_hit_tokens > 0` 的合成测试通过。
- 多实例 DDR cache 隔离。
- `kv_load_ms = 0` 保持。

### S7-F：Report / Metrics / E2E

类型：核心结果验收 + 外围 report 适配。

职责：

- 确认 request metrics / streaming aggregator / capacity sweep rows 正确消费 DDR hit。
- 更新 summary / CSV 中 DDR 字段口径。
- 端到端合成验收。
- 检查 cache event dump 文件。

不负责：

- 不新增 hit floor search。
- 不新增 Web UI / dashboard。
- 不修改 replay 语义。

输出：

- E2E 测试。
- 验收文档。

进入下一批条件：

- `hbm_hit_tokens + ddr_hit_tokens + miss_tokens == prompt_tokens`。
- `capacity_sweep.csv` trace row / instance row 统计一致。
- cache event stats 与 raw event dump 一致。
- full pytest / ruff / format 通过。

### S7-G：Review / Docs / Archive

类型：工程收口。

职责：

- 写 Step7 核心仿真器 review。
- 更新产品设计、核心技术路线、开发治理、全局记忆。
- 写清 Step7 与 vLLM / vLLM-Ascend / Mooncake 的差异。
- 将 `docs/step7/` 移入 `docs/archive/step7/`。

不负责：

- 不再补功能。
- 不开始 Step8。

输出：

- Step7 review。
- archive。
- 最终测试记录。

## 9. 每个 Batch 的强制流程

从 S7-A 开始，每个 batch 必须遵循：

1. 先给详细代码开发方案。
2. 说明为什么需要本 batch。
3. 说明会改哪些文件、不会改哪些文件。
4. 说明核心语义影响。
5. 说明测试计划和验收标准。
6. 用户评审通过后才能写代码。
7. 开发完成后写执行记录，至少包含：
   - 做了什么。
   - 没有做什么。
   - 影响。
   - 边界。
   - 风险。
   - 测试结果。
   - 是否建议进入下一个 batch。

建议执行记录放在：

```text
docs/step7/05_s7_a_execution.md
docs/step7/06_s7_b_execution.md
...
```

## 10. 当前建议

建议下一步不是直接进入 S7-A 代码开发，而是先评审本文件。

评审通过后，进入：

```text
S7-A：Config / Schema Guard 代码开发方案
```

该方案仍只会给出开发计划，不会直接写代码。
