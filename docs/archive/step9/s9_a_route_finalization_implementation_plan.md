# S9-A Implementation Plan: Route Finalization

状态：已审批通过，已执行完成。

本 Batch 只做文档收口，不进行业务代码开发。

## 1. Batch 定位

本 Batch 属于核心仿真器开发阶段中的文档收口 batch。

改动等级：L0。

原因：

- S9-A 不修改 `src/infertwin/` 业务代码。
- S9-A 不修改 replay、scheduler、cache、latency 或 streaming 行为。
- S9-A 的目标是把 Step9 已审批技术路线固化为后续 L3 代码开发的执行边界。

后续从 S9-B 开始才进入核心 replay schema / state / event-loop 修改，默认属于 L3。

## 2. 本 Batch 做什么

S9-A 做四件事：

1. 固化 Step9 正式路线。
   - 以 `docs/step9/02_technical_route.md` 为 Step9 正式技术路线。
   - `docs/step9/README.md` 只作为索引，不再承载技术路线。

2. 固化核心术语。
   - `compute_wait_ms`：request 已进入 vLLM / InferTwin replay，但等待 chunked prefill 组 batch 的时间。
   - `kv_load_wait_ms`：request 等待 DDR/CPU KV load 完成的时间。
   - `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms`：兼容聚合字段。
   - `queue_waiting_ms = 0`：实例入口 admission queue wait 仍不建模。

3. 固化新 mode 和后续状态名。
   - 新 mode：`batch_aware_hbm_ddr_lru_progressive_timeline`。
   - 新状态：`WAITING_FOR_COMPUTE`、`WAITING_FOR_KV_LOAD`、`RUNNING_CHUNK`。
   - old mode：`batch_aware_hbm_ddr_lru` 必须保持 Step8 行为。

4. 明确后续 batch 的代码边界。
   - S9-B：timeline schema / typed result。
   - S9-C：compute wait accounting。
   - S9-D：KV load timing state。
   - S9-E：KV transfer queue。
   - S9-F：chunk-level TTFT composer。
   - S9-G：progressive full-block materialization。
   - S9-H：streaming integration / report fields。
   - S9-I：E2E / review / archive。

## 3. 本 Batch 不做什么

S9-A 不做：

- 不改 `src/infertwin/`。
- 不新增 Python 数据结构。
- 不新增 replay mode。
- 不新增 config schema。
- 不修改 scheduler。
- 不修改 request state。
- 不修改 latency backend。
- 不修改 cache lookup / materialization / eviction。
- 不修改 streaming runner。
- 不更新 report/export。
- 不更新 golden。
- 不运行 pytest。

如果在 S9-A 中发现必须修改业务代码，应暂停并重新提交评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `docs/step9/s9_a_route_finalization_implementation_plan.md` | 本文件。记录 S9-A 方案、边界、测试策略和进入 S9-B 的条件。 |

### 4.2 可修改文件

若用户审批后进入 S9-A 执行，可允许修改：

| 文件 | 职责 |
| --- | --- |
| `docs/step9/02_technical_route.md` | 将状态从“待评审”更新为“已评审通过”；补充最终审批结论。 |
| `docs/step9/README.md` | 保持索引职责；必要时增加 S9-A 方案索引。 |

### 4.3 禁止修改文件

S9-A 禁止修改：

- `src/infertwin/**`
- `tests/**`
- `configs/**`
- `scripts/**`
- `docs/core_simulator_technical_plan.md`
- `docs/global_memory.md`

主文档和记忆应在 Step9 阶段收口 S9-I 更新，不在 S9-A 更新。

## 5. 当前源码入口理解

本方案基于当前源码入口，不改变它们。

| 领域 | 当前文件 | S9-A 结论 |
| --- | --- | --- |
| replay loop | `src/infertwin/replay/event_loop.py` | 后续 S9-C/D/F/G 的主要修改点；S9-A 不改。 |
| streaming replay | `src/infertwin/streaming/replay.py` | 后续 S9-H 必须保持与 list replay 等价；S9-A 不改。 |
| scheduler | `src/infertwin/scheduler/vllm_like.py` | 当前按 running 优先、waiting 补充形成 `BatchShape`；后续 compute wait 统计不能破坏 token selection。 |
| request state | `src/infertwin/scheduler/state.py` | 当前只有 `WAITING/RUNNING/FINISHED`，已有 pending KV load 字段；后续 S9-B/C/D 再设计新状态。 |
| batch shape | `src/infertwin/scheduler/batch_shape.py` | 当前已有 per-slice `kv_load_tokens/bytes`；后续 timeline schema 应复用 slice 信息。 |
| replay metrics | `src/infertwin/replay/metrics.py` | 当前 `scheduler_wait_ms = first_scheduled - arrival`；新 mode 后续需拆出 `compute_wait_ms/kv_load_wait_ms`。 |
| latency profile | `src/infertwin/latency/profile.py` | 当前 composition 是 `queue + ttft + kv_load`；后续不应把 all timeline state 塞入 backend。 |
| KV load component | `src/infertwin/latency/kv_load.py` | 当前是 iteration-level token/byte-linear component；后续 queue/timeline 应在 replay-facing policy 层处理。 |
| materialization | `src/infertwin/cache/materialization.py` | 当前只有 finish-time policy；后续 S9-G 新增 progressive policy。 |
| streaming metrics | `src/infertwin/streaming/metrics.py` | 当前聚合 request/iteration metrics；后续 S9-H 只消费 typed fields。 |

## 6. 新增或修改的数据结构 / schema / interface

S9-A 不新增或修改任何 Python 数据结构、schema 或 interface。

但 S9-A 固化后续待实现命名，供 S9-B 评审使用：

### 6.1 后续建议数据结构

待 S9-B 设计，不在 S9-A 实现：

- `RequestTimelineState`
- `ChunkTimelineEntry`
- `KVLoadTimelineEntry`
- `RequestTimelineSummary`

### 6.2 后续建议 interface

待 S9-B/S9-D/S9-E 设计，不在 S9-A 实现：

- `RequestTTFTComposer`
- `KVLoadTimingPolicy`
- `KVTransferTimelinePolicy`
- `ProgressiveFullBlockMaterializationPolicy`

### 6.3 后续建议 schema 字段

待 S9-B/S9-H 设计，不在 S9-A 实现：

- `timeline_mode`
- `ttft_granularity`
- `compute_wait_ms`
- `kv_load_wait_ms`
- `scheduler_wait_ms`
- `uncached_prefill_compute_ms`
- `modeled_serialization_ms`
- `chunk_count`
- `load_event_count`
- `progressive_materialized_blocks`
- `progressive_materialized_tokens`

## 7. 核心算法逻辑

S9-A 无业务算法。

S9-A 的文档收口流程是：

1. 对照 `01_source_alignment_and_error_analysis.md`，确认 Step9 正式路线覆盖：
   - compute wait。
   - KV load wait。
   - transfer queue。
   - chunk-level TTFT。
   - progressive materialization。

2. 对照当前源码入口，确认后续代码改动不会把职责混入错误模块：
   - scheduler 负责选 slice。
   - replay loop 负责状态推进和时间线应用。
   - latency backend 负责 chunk/load duration 估计。
   - materialization policy 负责 block 何时可见。
   - metrics 负责 typed result，不反推 replay。

3. 对照测试入口，确认后续测试必须覆盖：
   - old mode regression。
   - list replay 与 streaming replay 等价。
   - DDR-only load-only finish。
   - HBM-only zero-miss immediate finish。
   - compute wait 与 KV load wait 不重复计费。

## 8. 对核心 replay 语义的影响

S9-A 是文档-only，不改变核心 replay 语义。

| 问题 | S9-A 是否改变 | 说明 |
| --- | --- | --- |
| 是否改变 `cached_tokens` | 否 | 不改 lookup/accounting。 |
| 是否改变 `hbm_hit_tokens / ddr_hit_tokens / miss_tokens` | 否 | 不改 cache 或 metrics。 |
| 是否改变 `finish_time / ttft_ms` | 否 | 不改 replay；仅固化后续新 mode 的 TTFT 拆分方案。 |
| 是否改变 cache event 顺序 | 否 | 不改 materialization。 |
| 是否改变 materialization timing | 否 | 不改 finish-time policy。 |
| 是否改变实例隔离 | 否 | 不改 per-instance replay。 |
| 是否影响 true streaming 大 trace | 否 | 不改 streaming path。 |

后续 S9-B 到 S9-H 会逐步改变新 mode 的 replay 语义；每个 batch 必须单独评审。

## 9. 测试计划

### 9.1 单测

S9-A 不新增单测。

原因：本 batch 不修改 Python 业务代码。

### 9.2 集成测试

S9-A 不运行集成测试。

原因：无业务行为变化。

### 9.3 小 E2E

S9-A 不运行小 E2E。

小 E2E 将从 S9-C/S9-D 后开始有意义，因为那时才会出现新的 wait state 或 timeline fields。

### 9.4 Golden 更新

S9-A 不更新 golden。

原因：不改变输出 schema 或 replay result。

### 9.5 文档检查

S9-A 需要运行：

```bash
git diff --check
rg -n "[[:blank:]]$" docs/step9
```

## 10. 风险与回滚边界

### 10.1 风险

1. 后续 batch 范围膨胀。
   - S9-A 必须把 S9-B 到 S9-I 拆清楚，避免一个 batch 同时改 schema、state、latency、materialization 和 streaming。

2. wait 语义混淆。
   - `compute_wait_ms` 不是实例入口排队。
   - `kv_load_wait_ms` 不是 compute wait。
   - `scheduler_wait_ms` 只是兼容聚合字段。

3. old mode 被误改。
   - 后续任何代码 batch 都必须保留 old mode regression。

4. timeline state 被塞进 latency backend。
   - latency backend 只能估计 duration；replay-facing wait state 和 critical path composition 应由 timeline/replay 层负责。

### 10.2 回滚边界

S9-A 只新增/修改文档。

回滚方式：

- 删除或回退 `docs/step9/s9_a_route_finalization_implementation_plan.md`。
- 若执行阶段修改了 `README.md` 或 `02_technical_route.md`，回退对应文档即可。

不涉及数据库、cache、shard、配置或业务代码回滚。

## 11. 完成后如何判断可以进入下一个 Batch

S9-A 评审通过并执行后，可以进入 S9-B 的条件：

1. 用户确认 `02_technical_route.md` 是 Step9 正式技术路线。
2. 用户确认 S9-A 不进入业务代码开发。
3. 用户确认后续新增 mode 名称：
   `batch_aware_hbm_ddr_lru_progressive_timeline`。
4. 用户确认后续 TTFT 拆分口径：
   `ttft_ms = compute_wait_ms + kv_load_wait_ms + uncached_prefill_compute_ms + modeled_serialization_ms`。
5. 用户确认 S9-B 只做 timeline schema / typed result 设计和实现，不提前改 replay event loop。
6. 文档检查通过：
   - `git diff --check`
   - `rg -n "[[:blank:]]$" docs/step9` 无输出。

## 12. 需要用户审批的内容

审批结果：已通过。

已接受以下内容：

1. 是否接受 S9-A 属于核心仿真器阶段的 L0 文档收口 batch。
2. 是否接受 S9-A 不修改任何业务代码。
3. 是否接受 S9-A 只允许修改：
   - `docs/step9/s9_a_route_finalization_implementation_plan.md`
   - `docs/step9/02_technical_route.md`
   - `docs/step9/README.md`
4. 是否接受主文档和全局记忆等到 S9-I 阶段收口再更新。
5. 是否接受 S9-B 从 timeline schema / typed result 开始，不直接修改 replay event loop。

## 13. S9-A 执行记录

执行日期：2026-06-29。

实际修改：

- `docs/step9/02_technical_route.md`
  - 将状态更新为“正式技术路线，已通过评审”。
  - 增加评审结论，固化 Step9 的 scope、mode、wait 语义和旧 mode 保护要求。
- `docs/step9/README.md`
  - 保持索引职责。
  - 增加 S9-A 方案索引。
  - 标记 S9-A 已完成，并指向下一步 S9-B。
- `docs/step9/s9_a_route_finalization_implementation_plan.md`
  - 将状态更新为“已审批通过，已执行完成”。
  - 记录审批结果和执行结果。

未修改：

- 未修改 `src/infertwin/**`。
- 未修改 `tests/**`。
- 未修改 `configs/**`。
- 未修改 `scripts/**`。
- 未更新主文档和全局记忆，按计划留到 S9-I 阶段收口。

验证：

- `git diff --check` 通过。
- `rg -n "[[:blank:]]$" docs/step9` 无输出。
- 未运行 pytest，因为 S9-A 是文档-only。

进入下一 Batch 条件：

- S9-A 已完成。
- 可以进入 S9-B：Timeline Schema / Typed Result 代码编写方案设计。
