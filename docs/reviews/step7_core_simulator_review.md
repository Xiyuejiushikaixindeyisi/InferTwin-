# Step7 Core Simulator Review

评审时间：2026-06-27

评审对象：

- `src/infertwin/cache/`
- `src/infertwin/streaming/`
- `src/infertwin/report/`
- `tests/`
- `docs/archive/step7/`

评审范围：Step7 单实例 HBM + DDR/CPU KV pooling。

阶段类型：核心仿真器工程收口。

## 1. 结论

Step7 已完成单实例 DDR/CPU pooling 的核心仿真骨架：

```text
fixed-routing multi-instance isolated replay
+ HBM LRU
+ DDR/CPU LRU tier
+ tier-aware hit accounting
+ tier-aware cache events
+ streaming runner integration
+ report / metrics / E2E validation
```

当前实现可以作为 Step8：KV load latency 的工程基础。

正式结论：

```text
具备进入 Step8 的条件。
```

但 Step8 必须遵守以下边界：

- Step8 先实现 KV load latency accounting，不做 DDR hit promotion。
- Step8 不改变 `ddr_hit_tokens` 的计算方式。
- Step8 不改变 `batch_aware_hbm_ddr_lru` 的 cache hit semantics。
- 如果 Step8 需要改变 cache hit 或 materialization semantics，必须新增 replay/cache mode。

## 2. Step7 完成内容

### 2.1 Config / Schema Guard

已完成：

- Model-owned `ddr_capacity_blocks`。
- Model-owned pooling flags。
- single-instance DDR/CPU pooling validation。
- V1 不支持的 multi-instance / remote / SSD / KV transfer pooling fail-fast。

相关文件：

```text
src/infertwin/config/model_runtime.py
src/infertwin/config/model_binding.py
configs/models/registry_step7_pooling.yaml
configs/deployments/glm-v5.1-vllm-ascend-prefill-pooling.yaml
```

### 2.2 CacheEvent Tier Schema

已完成：

- `CacheEvent` 支持 DDR tier。
- 新增 `STORE`。
- 新增 source / target tier。
- 新增 load / store token 字段。
- `CacheEventStats` 支持 store events 和 DDR resident stats。

相关文件：

```text
src/infertwin/cache/events.py
src/infertwin/cache/event_sink.py
src/infertwin/report/cache_events.py
```

### 2.3 DDR LRU Tier

已完成：

- 独立 `DDRLRUCache`。
- hash-only metadata store。
- contiguous prefix lookup。
- store / eviction / event emission。
- LRU recency update。

相关文件：

```text
src/infertwin/cache/ddr_lru.py
tests/unit/cache/test_ddr_lru_cache.py
```

### 2.4 TieredPrefixCache

已完成：

```text
HBM contiguous hit -> DDR contiguous hit -> final miss
```

已确认语义：

- HBM 优先。
- DDR 只补 HBM miss tail 的连续 prefix。
- 不跳过中间 miss。
- finish-time materialization 同时写 HBM 和 DDR。
- DDR hit 不自动 promote 到 HBM。
- HBM eviction 不解释为 DDR offload。
- raw tier events 保留；request-level accounting 以 `PrefixLookupResult` 为准。

相关文件：

```text
src/infertwin/cache/tiered.py
tests/unit/cache/test_tiered_prefix_cache.py
```

### 2.5 Streaming Runner Integration

已完成：

- 新增 `batch_aware_hbm_ddr_lru` cache mode。
- `sweep-streaming` 可按 instance model defaults 构造 `TieredPrefixCache`。
- HBM capacity 仍由 sweep candidate 覆盖。
- DDR capacity 从 model default cache 读取。
- legacy `simulate` / non-streaming `sweep` 保持 HBM-only。

相关文件：

```text
src/infertwin/streaming/cache_factory.py
src/infertwin/streaming/sweep.py
tests/unit/streaming/test_cache_factory.py
tests/integration/test_step7_streaming_hbm_ddr_integration.py
```

### 2.6 Report / Metrics / E2E

已完成：

- `capacity_sweep.csv` 输出 DDR hit tokens / rates。
- `summary.md` 区分 HBM-only 与 HBM+DDR mode。
- event dump 可观察 DDR store / lookup_hit。
- package CLI `sweep-streaming` E2E 已覆盖 Step7 DDR mode。
- trace row / instance row token invariant 已覆盖。
- cache event dump row count 与 trace row `cache_event_count` 一致。

相关文件：

```text
src/infertwin/report/sweep.py
tests/integration/test_step7_report_metrics_e2e.py
```

## 3. 功能完善度

### 3.1 已满足 Step7 目标

Step7 目标是：

```text
单个实例在 DDR/CPU 侧额外 KV cache 存储中命中。
```

当前已满足：

- 单实例 DDR tier。
- 按 instance 隔离。
- DDR capacity 从 model default cache 读取。
- DDR LRU。
- DDR hit accounting。
- DDR event observability。
- streaming runner 主路径可用。

### 3.2 明确不满足的能力

以下不是 Step7 目标：

- KV load latency。
- DDR hit promotion。
- async KV load completion。
- async DDR store completion。
- cross-instance pooling。
- remote / SSD tier。
- progressive block visibility。
- Decode / TPOT。
- physical KV slots / pinned / refcount。

这些能力不应被解释为 Step7 缺陷，但必须作为后续阶段边界继续追踪。

## 4. 代码结构评审

### 4.1 优点

- `DDRLRUCache` 独立于 HBM，职责清晰。
- `TieredPrefixCache` 实现 `PrefixCache` 协议，replay 不需要知道 tier 细节。
- `streaming/cache_factory.py` 集中处理 cache mode 和 fail-fast guard。
- `streaming/sweep.py` 只替换 cache construction，不修改 streaming replay state machine。
- `report/sweep.py` 只渲染 typed result，不重算 replay metrics。

### 4.2 风险

- `TieredPrefixCache` 当前将 materialization 同时写 HBM 和 DDR；这与真实 offload / KV transfer 不是同一语义。
- raw tier events 中 HBM miss 后可能出现 DDR hit，使用者必须区分 raw tier events 与 request-level token accounting。
- summary 文案在 Step8 / Step9 后需要更新，避免继续说 KV load latency 未建模。

### 4.3 结论

当前代码结构符合核心仿真器与外围 report 分离原则。

Step8 可以在 latency/profile 层消费 DDR hit tokens，而不需要改动 cache lookup 结构。

## 5. 测试覆盖评审

### 5.1 覆盖内容

当前测试覆盖：

- DDR LRU lookup / store / eviction / event。
- Tiered lookup HBM 优先、DDR contiguous hit、non-skip miss。
- no promotion。
- materialize to both tiers。
- streaming DDR mode positive / negative path。
- multi-instance DDR cache isolation。
- report metrics invariant。
- cache event dump consistency。
- package CLI E2E。
- HBM-only backward compatibility。

### 5.2 已运行结果

S7-G closure 已运行：

```text
PYTHONPATH=src .venv/bin/python -m pytest
307 passed in 18.38s

PYTHONPATH=src .venv/bin/python -m ruff check src tests
All checks passed!

git diff --check
passed
```

closure 检查通过，Step7 review 结论生效。

## 6. 性能评审

### 6.1 已控制风险

- DDR tier 只保存 hash key 和 metadata，不保存 KV tensor。
- streaming path 不构造全量 accepted request list。
- cache event dump 已是 streaming writer。
- cache event 默认可关闭。
- capacity sweep 主路径可以使用 stats-only sink。

### 6.2 仍需关注

- DDR tier 增加 resident metadata 数量；超大 capacity 下仍需 benchmark。
- JSONL shard 持有 prefix block hash chain，长 prompt 下磁盘体积可能较大。
- exact percentile 仍保存 TTFT list，百万级 request 需要 quantile policy。
- 多实例 replay 仍是串行。

这些问题不是 Step8 blocker，但在 V1 准出或后续大规模 benchmark 时需要继续评估。

## 7. 与真实 vLLM / vLLM-Ascend / Mooncake 的差异

### 7.1 与 vLLM / vLLM-Ascend

当前 InferTwin 对齐点：

- full-block prefix cache hit。
- contiguous prefix semantics。
- vLLM-like cached_tokens accounting。
- runtime block size / effective block size / speculative drop blocks 的 accounting foundation。
- chunked prefill / continuous batching 的离线近似 replay。

当前差异：

- 不建 physical block table。
- 不建 pinned/refcount。
- 不建 async block transfer。
- 不建真实 KV tensor。
- 不建 progressive block visibility。
- 不建 decode / TPOT。

风险：

- 对长 prefill、高复用场景，finish-time materialization 可能低估 hit。
- 对真实 KV load，Step7 只记录 DDR hit，不增加 load time。

处理：

- progressive visibility 放到 Step9。
- KV load latency 放到 Step8。
- physical KV manager / Hybrid cache group 放到 V2。

### 7.2 与 Mooncake Store

当前 InferTwin 对齐点：

- 已有 local DDR/CPU tier 的概念。
- 已有 tier-aware event。
- 已有 model config 中 pooling flags。

当前差异：

- 不做 global KV pool。
- 不做跨实例 hit。
- 不做 remote transfer。
- 不做 network / RDMA / storage latency。
- 不做 Mooncake object lifecycle。

处理：

- Step7 只实现 single-instance pooling。
- cross-instance pooling / Mooncake global store 属于 V2。
- Step8 只建议接本地 DDR/CPU KV load latency，不应提前混入 remote pooling。

## 8. Step8 Readiness

### 8.1 判断依据

InferTwin 已具备进入 Step8 的工程基础，依据如下：

1. Step7 已能产生 request-level `ddr_hit_tokens`。
2. Step7 已能产生 trace / instance level `ddr_hit_rate`。
3. Step7 已能输出 DDR tier raw cache events。
4. `InstanceLatencyProfile` / model default latency 中已有 `kv_load` 超参数 schema。
5. `ServingLatencyProfile` 已有 replay-facing latency composition interface。
6. `summary.md` 已明确 DDR hit accounting 与 KV load latency 未建模的区别。
7. HBM-only mode 仍通过回归测试。
8. 多实例 DDR cache isolation 已通过测试。
9. S7-F 全量 pytest、ruff、diff check 已通过。

### 8.2 风险

Step8 风险：

- 只给 DDR hit tokens 加 latency，仍不等于真实异步 KV load。
- DDR hit promotion 到 HBM 未建模。
- finish-time materialization 仍可能低估长 prefill 期间的 block reuse。
- raw tier events 与 request-level token accounting 不完全等价。
- 如果 Step8 混入 promotion / transfer completion，会扩大范围并污染 Step7 semantics。

### 8.3 注意事项

Step8 应注意：

- 先实现 latency accounting，不做 promotion。
- 不改变 `ddr_hit_tokens` 计算。
- 不改变 `batch_aware_hbm_ddr_lru` 的 cache hit semantics。
- 明确 `kv_load_ms` 来源：profile constant、fitted function、external simulator adapter 或 production log calibration。
- 保持 HBM-only mode `kv_load_ms = 0`。
- report/export 只展示 typed result，不参与 latency 计算。

### 8.4 遗留问题

进入 Step8 时必须带着以下遗留问题：

- progressive block visibility：Step9。
- Decode / TPOT：V2 pending。
- cross-instance pooling：V2。
- remote / SSD tier：后续多级 cache 扩展。
- Hybrid physical cache group：V2。
- instance-level event count：后续 report schema 扩展。
- large trace quantile policy：后续大规模 benchmark / V1 准出优化。

### 8.5 风险控制

Step8 风险控制建议：

- 新增或扩展 latency result 字段表达 `kv_load_ms`。
- Step8 第一版只消费 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`。
- DDR hit latency 与 promotion 分开设计。
- 如果需要改变 cache semantics，新增 mode，而不是改写 `batch_aware_hbm_ddr_lru`。
- Step8 测试至少覆盖：
  - DDR hit tokens 增加时 TTFT 增加。
  - HBM hit 不产生 DDR load latency。
  - HBM-only mode `kv_load_ms = 0`。
  - trace / instance 聚合口径不变。

### 8.6 Step8 准入结论

结论：

```text
具备进入 Step8：KV load latency 的条件。
```

理由：

- Step7 已完成 DDR hit accounting 和 event observability。
- Step7 已保持实例隔离和 HBM-only 兼容。
- latency/profile 层已有可扩展基础。
- 当前未建模内容与 Step8 目标边界清晰。

准入状态：

```text
S7-G closure 测试已通过，该结论已生效。
```

## 9. 总体风险等级

当前 Step7 收口风险等级：中低。

原因：

- 核心 replay state machine 未在 Step7 被大幅改写。
- Tiered cache 封装在 `PrefixCache` backend 内。
- Streaming runner 集成点集中。
- 已有单元、集成、CLI、report、全量测试覆盖。

主要残余风险集中在模型精度，而不是工程稳定性：

- finish-time materialization。
- KV load latency 尚未建模。
- 无真实物理 KV storage。
- 无 decode / TPOT。

这些残余风险已有后续阶段承接。
