# S7-G：Review / Docs / Archive 开发方案与执行记录

状态：方案待评审。

阶段类型：工程收口。

测试等级：`closure`。

## 1. Batch 目标

S7-G 是 Step7 的最终工程收口 batch。

它的目标不是新增功能，而是对 Step7 单实例 HBM + DDR/CPU pooling 能力进行完整 review、文档收口、风险判断和归档。

S7-G 必须输出一个明确结论：

```text
InferTwin 是否具备进入 Step8：KV load latency 的条件。
```

该结论必须包含：

- 判断依据。
- 风险。
- 注意事项。
- 遗留问题。
- 风险控制。
- 最终结论。

## 2. 为什么需要 S7-G

Step7 已经完成 S7-A 到 S7-F：

- S7-A：配置层支持 single-instance DDR/CPU pooling。
- S7-B：cache event schema 支持 tier 信息和 DDR fields。
- S7-C：实现 `DDRLRUCache`。
- S7-D：实现 `TieredPrefixCache`。
- S7-E：`sweep-streaming` 接入 `batch_aware_hbm_ddr_lru`。
- S7-F：CSV / summary / event dump / CLI E2E 完成验收。

但这些 batch 仍是阶段内材料。进入 Step8 前，需要把它们收敛成长期可维护的状态：

- 主文档必须反映 Step7 后的真实能力。
- `docs/step7/` 必须归档。
- reviews 中必须有 Step7 核心仿真器 review。
- 全局记忆必须更新到 Step8 前状态。
- 与 vLLM / vLLM-Ascend / Mooncake 的差异必须说明清楚。
- Step8 准入结论必须明确，避免把 Step8 建在含糊边界上。

## 3. S7-G 不做什么

S7-G 不做：

- 不实现 Step8。
- 不实现 KV load latency。
- 不修改 replay event loop。
- 不修改 cache backend。
- 不修改 scheduler。
- 不修改 latency backend。
- 不新增 report 产品能力。
- 不新增 replay mode。
- 不改变 `batch_aware_hbm_ddr_lru` 语义。

如果 S7-G review 中发现必须修改业务代码，应先判断是否为：

```text
1. 文档/测试口径修正；
2. Step7 blocker；
3. Step8 设计输入；
4. V2 遗留问题。
```

只有 Step7 blocker 才应暂停归档并另开修复 batch。否则应记录在 review 中，不在 S7-G 中扩大范围。

## 4. 当前 Step7 后能力基线

S7-G review 应以当前已完成能力为基线。

### 4.1 已实现

当前 Step7 后核心能力：

- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching / chunked prefill。
- vLLM-like cached_tokens accounting。
- finite HBM LRU prefix cache。
- single-instance DDR/CPU LRU prefix cache tier。
- `TieredPrefixCache`：HBM contiguous hit -> DDR contiguous hit -> miss。
- finish-time materialization 同时写 HBM 和 DDR。
- DDR hit 不 promotion 到 HBM。
- `CacheEvent` 支持 HBM / DDR tier event。
- `cache_events.csv` 可观察 DDR `store` / `lookup_hit`。
- `CapacitySweepRow` 支持 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`。
- `summary.md` 区分 HBM-only mode 和 HBM+DDR mode。
- `sweep-streaming` 支持显式：

```text
cache.mode: batch_aware_hbm_ddr_lru
```

### 4.2 仍不实现

Step7 后仍不实现：

- KV load latency，放到 Step8。
- progressive block visibility，放到 Step9。
- cross-instance pooling，V2。
- remote / SSD tier，V2 或后续多级 cache 扩展。
- DDR hit promotion 到 HBM，Step8 之后再设计。
- Decode / TPOT，V2 pending。
- gateway routing，V2。
- 实例侧真实排队，V2。
- Hybrid cache group 的完整 physical cache 行为，V2。

## 5. Batch 开发顺序

### S7-G1：Review Scope / Evidence Inventory

职责：

- 汇总 S7-A 到 S7-F 的已完成内容。
- 记录当前测试结果。
- 列出核心代码路径。
- 列出主文档中需要更新的 stale statements。

预计检查文件：

```text
docs/step7/
docs/infertwin_product_design.md
docs/core_simulator_technical_plan.md
docs/development_governance.md
docs/global_memory.md
src/infertwin/cache/
src/infertwin/streaming/
src/infertwin/report/
tests/
```

不修改业务代码。

### S7-G2：Step7 Core Simulator Review

职责：

新增：

```text
docs/reviews/step7_core_simulator_review.md
```

review 必须覆盖：

- 功能完善度。
- 代码结构。
- 测试覆盖。
- 函数质量。
- 性能。
- 可维护性。
- 可扩展性。
- 与真实 vLLM / vLLM-Ascend / Mooncake 的差异。
- 当前骨架是否可以作为 Step8 基础。
- 进入 Step8 前建议优先关注的问题。

review 必须明确：

```text
是否具备进入 Step8。
```

### S7-G3：Step8 Readiness Gate

职责：

在 Step7 review 中单独写一个准出章节：

```text
Step8 Readiness
```

该章节必须包含：

#### 5.3.1 判断依据

建议依据：

- Step7 已能产生 request-level `ddr_hit_tokens`。
- Step7 已能产生 trace / instance level `ddr_hit_rate`。
- Step7 已能输出 DDR tier raw cache events。
- `InstanceLatencyProfile` / model default latency 中已有 `kv_load` 超参数 schema。
- `ServingLatencyProfile` 已有 replay-facing latency composition interface。
- `summary.md` 已明确 DDR hit accounting 与 KV load latency 未建模的区别。
- 全量 pytest / ruff / diff check 通过。

#### 5.3.2 风险

必须记录：

- Step8 如果只给 DDR hit tokens 加 latency，仍不等于真实异步 KV load。
- DDR hit promotion 到 HBM 未建模。
- finish-time materialization 仍可能低估长 prefill 期间的复用，该问题放到 Step9。
- raw tier events 与 request-level token accounting 不完全等价，review 必须提醒使用者看 request metrics。
- Step8 不应静默改变 `batch_aware_hbm_ddr_lru` 的 cache hit 语义。

#### 5.3.3 注意事项

必须记录：

- Step8 应新增或扩展 latency profile / backend，而不是改 cache backend 统计口径。
- Step8 必须保持 HBM-only mode 兼容。
- Step8 应继续区分核心仿真器和 report/export。
- Step8 应明确 `kv_load_ms` 的来源：fitted function、constant per token、external simulator adapter，还是 profile default。
- Step8 的测试应至少覆盖：
  - DDR hit tokens 增加时 TTFT 增加。
  - HBM hit 不产生 DDR load latency。
  - HBM-only mode `kv_load_ms = 0`。
  - trace / instance 聚合口径不变。

#### 5.3.4 遗留问题

必须记录：

- progressive block visibility：Step9。
- Decode / TPOT：V2 pending。
- cross-instance pooling：V2。
- remote / SSD tier：后续多级 cache 扩展。
- Hybrid physical cache group：V2。
- instance-level event count：后续 report schema 扩展。

#### 5.3.5 风险控制

建议风险控制：

- Step8 先只实现 latency accounting，不做 promotion。
- Step8 用新 typed latency result 字段表达 `kv_load_ms`。
- Step8 不改变 `ddr_hit_tokens` 计算。
- Step8 保持 `cache.mode=batch_aware_hbm_ddr_lru` 的 hit semantics。
- 如果 Step8 需要改变 semantics，应新增 mode，例如：

```text
batch_aware_hbm_ddr_lru_with_kv_load
```

但如果只是使用现有 DDR hit tokens 增加 latency，可保留 cache mode，扩展 latency backend。

#### 5.3.6 初步结论

S7-G 方案阶段的初步判断：

```text
基于 S7-F 的 307 个全量测试通过、S7-E/S7-F 的 DDR hit E2E 验收通过，以及现有 latency/profile schema foundation，InferTwin 已基本具备进入 Step8：KV load latency 的工程条件。
```

但正式结论必须以 S7-G 执行后的 Step7 review 和 closure 测试为准。

### S7-G4：主文档更新

职责：

更新长期主文档，确保 Step7 后能力不再被描述为未实现。

预计修改：

```text
docs/infertwin_product_design.md
docs/core_simulator_technical_plan.md
docs/development_governance.md
docs/global_memory.md
```

重点修正：

- 当前能力中加入 single-instance DDR/CPU pooling。
- 当前不建模内容中移除“DDR cache 未实现”的旧口径，改为：

```text
已实现 single-instance DDR/CPU pooling hit accounting；
KV load latency 未实现；
SSD / remote / cross-instance pooling 未实现。
```

- Step8 标记为下一阶段。
- Step9 progressive visibility 仍为 V1 后续能力。
- Step7 与真实 vLLM / vLLM-Ascend / Mooncake 差异写清楚。

### S7-G5：最终测试与版本健康检查

测试等级：`closure`。

建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest
PYTHONPATH=src .venv/bin/python -m ruff check src tests
git diff --check
```

如全量 pytest 失败：

- 若是文档 / golden / report 口径问题，可以在 S7-G 修正并记录。
- 若是 replay / cache / latency 核心行为问题，应暂停归档，进入修复评审。

### S7-G6：Archive

职责：

将 Step7 活动目录归档：

```text
docs/step7/
-> docs/archive/step7/
```

归档后必须更新：

```text
docs/global_memory.md
docs/core_simulator_technical_plan.md
```

使当前阶段指向 Step8，而不是继续指向 active `docs/step7/`。

注意：

- 归档前必须完成 review 和主文档更新。
- 归档后 active docs 根目录应保持轻量。
- 历史阶段文档中的旧口径可保留为历史记录，但主文档和全局记忆必须使用最新口径。

### S7-G7：执行记录收口

职责：

更新本文件执行记录：

- 做了什么。
- 没有做什么。
- 影响。
- 边界。
- 风险。
- 测试结果。
- 是否建议进入 Step8。

由于本文件会随 `docs/step7/` 一起归档，执行记录最终路径将变为：

```text
docs/archive/step7/11_s7_g_review_docs_archive_plan.md
```

## 6. 预计文件改动

预计新增：

```text
docs/reviews/step7_core_simulator_review.md
```

预计修改：

```text
docs/infertwin_product_design.md
docs/core_simulator_technical_plan.md
docs/development_governance.md
docs/global_memory.md
docs/step7/README.md
docs/step7/11_s7_g_review_docs_archive_plan.md
```

预计移动：

```text
docs/step7/
-> docs/archive/step7/
```

预计不修改：

```text
src/infertwin/
tests/
configs/
scripts/
```

如果执行期间发现必须修改 `src/` 或 `tests/`，应先说明原因并重新评审。

## 7. 验收标准

S7-G 通过条件：

1. 新增 Step7 core simulator review。
2. review 明确给出是否具备进入 Step8 的结论。
3. review 包含判断依据、风险、注意事项、遗留问题、风险控制、结论。
4. 产品设计文档更新到 Step7 后能力。
5. 核心技术路线文档更新到 Step7 后能力。
6. 全局记忆更新到 Step7 已完成、下一阶段 Step8。
7. `docs/step7/` 已归档到 `docs/archive/step7/`。
8. 全量 pytest、ruff、`git diff --check` 通过，或明确记录未运行/失败原因。
9. 没有修改核心 replay/cache/latency 语义。

## 8. Step8 准入判断框架

S7-G 最终必须回答：

```text
是否具备进入 Step8：KV load latency。
```

判断维度：

| 维度 | 准入要求 | 当前初步状态 |
| --- | --- | --- |
| DDR hit accounting | request / trace / instance metrics 能输出 DDR hit tokens | 已具备 |
| DDR event observability | cache event dump 能观察 DDR store / lookup_hit | 已具备 |
| latency schema foundation | 有 kv_load 超参数 schema 或可扩展点 | 已具备 |
| latency composition boundary | 有 replay-facing latency composition interface | 已具备 |
| HBM-only compatibility | HBM-only mode 不受 Step7 影响 | 已通过测试 |
| 实例隔离 | DDR cache 不跨实例共享 | 已通过测试 |
| report 口径 | summary 明确 DDR hit 与 KV load latency 区别 | 已完成 |
| 测试健康 | 全量 pytest / ruff / diff check | S7-G closure 已通过 |

初步结论：

```text
具备进入 Step8 的工程基础；S7-G closure review 和 closure 测试已通过，正式结论生效。
```

## 9. S7-G 执行记录

状态：已完成并归档。

### 9.1 做了什么

- 新增 `docs/reviews/step7_core_simulator_review.md`，完成 Step7 core simulator review。
- review 覆盖功能完善度、代码结构、测试覆盖、函数质量、性能、可维护性、可扩展性、与真实 vLLM / vLLM-Ascend / Mooncake 差异、Step8 准入判断、风险和遗留问题。
- 更新 `docs/infertwin_product_design.md`，把 Step7 DDR/CPU pooling hit accounting 从未实现能力调整为已完成核心能力，并保留 Step8 KV load latency 边界。
- 更新 `docs/core_simulator_technical_plan.md`，补齐 Step7 后核心模块、当前模拟内容、归档材料和 Step8 进入条件。
- 更新 `docs/development_governance.md`，把 DDR/CPU pooling 差异、KV load latency 差异和遗留问题口径写清楚。
- 更新 `docs/global_memory.md`，记录 Step7 已完成、归档路径、Step8 准入边界和后续协作模式。
- 更新 `docs/step7/README.md`，将 Step7 状态调整为 S7-A 到 S7-G 已完成。

### 9.2 没有做什么

- 没有新增 Step8 KV load latency 代码。
- 没有修改 replay / scheduler / cache / latency 的业务语义。
- 没有实现 DDR hit promotion。
- 没有实现 SSD / remote cache tier。
- 没有实现 cross-instance pooling。
- 没有实现 progressive block visibility。
- 没有实现 Decode / TPOT 建模。

### 9.3 影响

- 文档口径从 “Step7 待实现” 收敛到 “Step7 已完成 single-instance HBM + DDR/CPU pooling hit accounting”。
- 主文档明确 Step8 的输入基础：request-level `ddr_hit_tokens`、trace / instance level `ddr_hit_rate`、DDR tier cache events。
- 主文档明确 Step8 不应混入 promotion、remote tier、cross-instance pooling 或 progressive visibility。
- 当前代码路径不受 S7-G 文档收口影响。

### 9.4 边界

- S7-G 是工程收口 batch，不是功能开发 batch。
- Step7 默认仍采用 finish-time materialization。
- `batch_aware_hbm_ddr_lru` 的 cache hit semantics 不在 S7-G 修改。
- Step8 如果需要改变 cache hit 或 materialization semantics，必须新增 replay/cache mode。

### 9.5 风险

- 文档风险：如果仍有旧文档把 DDR/CPU pooling 描述为未实现，会误导 Step8 设计；S7-G 已通过主文档更新降低该风险。
- 语义风险：Step8 容易把 KV load latency、promotion、transfer completion 混在一起；S7-G 已将 Step8 边界写入 review 和主技术路线。
- 性能风险：DDR raw cache event 在大 trace 下可能很大；当前仍应默认关闭 event dump，只在指定 capacity 打开。

### 9.6 测试结果

S7-G closure 测试已通过：

```text
PYTHONPATH=src .venv/bin/python -m pytest
307 passed in 18.38s

PYTHONPATH=src .venv/bin/python -m ruff check src tests
All checks passed!

git diff --check
passed
```

### 9.7 是否具备进入 Step8

结论：具备。

判断依据：

- Step7 已能产生 request-level `ddr_hit_tokens`。
- Step7 已能产生 trace / instance level `ddr_hit_rate`。
- Step7 已能输出 DDR tier cache events。
- HBM-only mode 兼容性已在测试中覆盖。
- 多实例 isolation 已在 Step7 DDR mode E2E 中覆盖。
- S7-G closure 测试已通过。
- Step8 可以在不改变 cache hit semantics 的前提下，为 DDR hit 增加 KV load latency。

### 9.8 是否建议进入下一阶段

建议进入 Step8：KV load latency。

注意事项：

- Step8 先做 latency accounting，不做 promotion。
- Step8 不改变 `ddr_hit_tokens` 口径。
- Step8 不改变 `batch_aware_hbm_ddr_lru` materialization policy。
- 如需引入更真实的 KV transfer completion 或 progressive visibility，应另开 replay/cache mode。
