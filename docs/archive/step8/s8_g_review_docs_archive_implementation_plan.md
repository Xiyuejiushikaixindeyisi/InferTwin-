# S8-G 实施方案：Review / Docs / Archive

状态：已完成执行，待用户 review。

所属 Step：Step8 KV Load Latency。

本 Batch 名称：S8-G：Review / Docs / Archive。

前置条件：

- S8-A 已完成 KV load shape / shape key。
- S8-B 已完成 `KVLoadLatencyComponent` 与 `KVLoadLatencyProfile` mode schema。
- S8-C 已完成 instance/model resolver 到 `ServingLatencyProfile` 的接入。
- S8-D 已完成 replay 中 DDR hit -> KV load latency 的主链路。
- S8-E 已完成 request / iteration / streaming typed metrics 中的 KV load 字段。
- S8-F 已完成 Ramulator2 / Mooncake calibration boundary。

## 1. 类型与改动等级

本 Batch 属于核心仿真器阶段收口。

改动等级：L0。

原因：

- S8-G 只做 review、主文档同步、记忆更新、归档和收口验证。
- S8-G 不修改 replay、scheduler、cache、latency backend、streaming runner 或 report schema。
- 如果 review 发现必须修改核心 replay，不能在 S8-G 中顺手修，应新增独立 repair batch 并重新审批。

## 2. 本 Batch 做什么

S8-G 做 Step8 工程收口：

1. 新增 Step8 专项 review 文档，评估 Step8 后核心仿真器质量。
2. 审查 Step8 对核心 replay 链路的影响：
   - trace schema guard。
   - request build。
   - tokenizer / chat template。
   - prefix block hash。
   - scheduler replay。
   - cache lookup / materialization / eviction。
   - latency backend。
   - per-instance isolation。
   - typed metrics / typed result。
   - external calibration boundary。
3. 更新主文档：
   - 产品形态文档。
   - 核心仿真器技术路线文档。
   - agent development context。
   - global memory。
4. 明确 Step8 完成内容、验收结果、遗留问题和风险控制。
5. 判断是否具备进入 Step9 的条件。
6. 将 `docs/step8/` 移入 `docs/archive/step8/`。

## 3. 本 Batch 不做什么

S8-G 不做：

- 不修改业务代码。
- 不修改 `KVLoadLatencyProfile` schema。
- 不修改 `BatchShape` / `ShapeKey` / `LatencyResult`。
- 不修改 scheduler、waiting queue、chunked prefill planning。
- 不修改 cache lookup / materialization / eviction。
- 不修改 `BatchAwareReplayEngine` 或 streaming replay engine。
- 不新增 report/export 字段。
- 不新增 CLI。
- 不运行或接入 Ramulator2 / Mooncake。
- 不实现 compute/load overlap。
- 不实现 DDR hit promotion。
- 不实现 progressive visibility。
- 不修复 Step9 范围内的 finish-time materialization 低估问题。

如果收口 review 发现必须修改上述内容，应暂停 S8-G 归档，新增 Step8 repair batch 或进入 Step9 方案设计。

## 4. 计划新增/修改的文件

### 4.1 `docs/reviews/step8_core_simulator_review.md`

职责：

- 记录 Step8 后核心仿真器 review 结果。
- 对功能完善度、代码结构、测试覆盖、函数质量、性能、可维护性、可扩展性给出审查意见。
- 明确 Step8 是否具备进入 Step9 的条件。

计划内容：

```text
1. Review scope
2. Step8 capability summary
3. Core replay chain impact review
4. vLLM / vLLM-Ascend / Mooncake difference review
5. Test and ruff results
6. Remaining risks
7. Step9 readiness judgment
8. Recommended next actions
```

### 4.2 `docs/infertwin_product_design.md`

职责：

- 维护 InferTwin 产品形态主文档。

计划修改：

- 将 Step8 能力写入核心仿真器能力边界：
  - DDR/CPU hit 会产生 KV load latency。
  - KV load latency 由 `KVLoadLatencyProfile` 控制。
  - Ramulator2 / Mooncake 是 calibration source，不是默认 online replay dependency。
- 更新仍未实现能力：
  - progressive visibility 属于 Step9。
  - overlap / queue / promotion / remote pooling / decode 属于后续。

### 4.3 `docs/core_simulator_technical_plan.md`

职责：

- 维护核心仿真器技术路线。

计划修改：

- 将 Step8 从“待开发”更新为“已完成 / 待用户收口 review”。
- 记录 Step8 对 replay timeline 的实际改变：

```text
iteration_duration = queue_ms + prefill_compute_ms + kv_load_ms
```

- 记录 Step8 不改变的语义：
  - cached token accounting。
  - HBM / DDR / miss token accounting。
  - finish-time materialization。
  - DDR hit 不 promotion。
  - fixed-routing instance isolation。
- 将 Step9 progressive visibility 标记为下一阶段核心任务。

### 4.4 `docs/agent_development_context.md`

职责：

- 维护 coding agent 最小开发上下文。

计划修改：

- 将 Step8 状态从“下一阶段”更新为“已完成 / 收口中”。
- 更新当前 V1 范围：
  - Step7 已完成。
  - Step8 已完成 KV load latency。
  - Step9 下一步是 progressive chunk/block visibility。
- 更新稳定语义：
  - Step8 v1 默认 `overlap_mode=none_v1`。
  - `kv_load_tokens` / `kv_load_bytes` / `kv_load_ms` 是显式语义。
  - Ramulator2 / Mooncake 只作为 calibration source。

### 4.5 `docs/global_memory.md`

职责：

- 维护项目全局记忆。

计划修改：

- 记录 Step8 完成内容。
- 记录进入 Step9 前的主要遗留问题。
- 记录 `docs/step8/` 已归档到 `docs/archive/step8/`。

### 4.6 `docs/step8/s8_g_review_docs_archive_implementation_plan.md`

职责：

- 本 Batch 的方案与执行记录。

开发完成后更新：

- 已做内容。
- 未做内容。
- 验证命令。
- 是否具备进入 Step9 的判断。

### 4.7 `docs/archive/step8/`

职责：

- 保存 Step8 临时技术路线、学习笔记和 batch 方案。

计划动作：

- 将整个 `docs/step8/` 移动到 `docs/archive/step8/`。
- 归档后主文档只保留轻量索引，不要求 agent 后续默认读取 archive。

边界：

- 不删除 Step8 文档内容。
- 不修改 archive 内历史文档口径，除非存在明显错误引用影响当前主文档。

## 5. 新增或修改的数据结构 / schema / interface

S8-G 不新增或修改任何 Python 数据结构、schema、backend、policy、adapter 或 interface。

仅文档层面更新：

- Step8 review 文档。
- 主文档中 Step8 状态和能力边界。
- agent context / global memory。
- archive 目录结构。

## 6. 核心算法逻辑

S8-G 没有业务算法。

收口逻辑是确定性的 review checklist：

1. 列出 Step8 涉及的核心源码与测试。
2. 对每条 replay 保护链路给出结论：
   - 改了什么。
   - 没改什么。
   - 是否存在风险。
   - 是否影响 Step9。
3. 运行收口验证命令。
4. 将结果写入 review 文档。
5. 更新主文档和记忆。
6. 归档 `docs/step8/`。

## 7. 对核心 replay 语义的影响

| 问题 | S8-G 影响 |
| --- | --- |
| 是否改变 `cached_tokens` | 不改变 |
| 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens` | 不改变 |
| 是否改变 `finish_time` / `ttft_ms` | S8-G 不改变；review 中会记录 Step8 已使 DDR hit KV load 进入 finish_time / TTFT |
| 是否改变 cache event 顺序 | 不改变 |
| 是否改变 materialization timing | 不改变，仍为 finish-time materialization |
| 是否改变实例隔离 | 不改变 |
| 是否影响 true streaming 大 trace | 不改变 streaming 主路径；只更新文档和归档 |

## 8. 测试计划

### 8.1 单测

S8-G 不新增单测。

收口时计划运行 Step8 targeted unit tests：

```text
tests/unit/scheduler/test_batch_shape_kv_load.py
tests/unit/scheduler/test_request_state_kv_load.py
tests/unit/latency/test_shape_key_kv_load.py
tests/unit/latency/test_kv_load_latency.py
tests/unit/latency/test_serving_latency_profile.py
tests/unit/latency/test_instance_resolver.py
tests/unit/latency/test_instance_resolver_model_defaults.py
tests/unit/replay/test_step8_kv_load_replay.py
tests/unit/replay/test_step8_latency_contribution_metrics.py
tests/unit/streaming/test_metrics.py
tests/unit/experiment/test_sweep_metrics.py
tests/unit/external/test_kv_load_calibration.py
tests/unit/external/test_adapter_boundaries.py
```

### 8.2 集成测试

计划运行：

```text
tests/integration/test_step8_streaming_kv_load_e2e.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
tests/integration/test_step7_report_metrics_e2e.py
tests/integration/test_batch_d_runner.py
```

目的：

- 验证 Step8 KV load latency 在 streaming replay 中可观察。
- 验证 capacity sweep / report 仍只消费 typed result。
- 验证 Step7 HBM + DDR accounting report 不被 Step8 文案和 schema 破坏。
- 验证 legacy batch-aware runner 仍兼容。

### 8.3 小 E2E

小 E2E 使用现有 synthetic streaming KV load 测试，不新增新数据集。

验收关注：

- DDR hit request 的 `ttft_ms` 随 `kv_load_ms` 增加。
- HBM-only zero-miss 仍 immediate finish。
- `kv_load_tokens` / `kv_load_bytes` / `kv_load_ms` 出现在 typed metrics。
- trace row 与 instance row 能聚合 KV load 指标。

### 8.4 Golden 更新

S8-G 不需要 golden 更新。

原因：

- 不改变代码行为。
- 不改变输出 schema。
- 不更新 existing golden expected values。

### 8.5 格式与质量检查

计划运行：

```text
ruff check <Step8 touched src/tests>
git diff --check
```

阶段收口可选：

```text
PYTHONPATH=src .venv/bin/python -m pytest
```

是否运行全量 pytest 取决于本轮时间；如果未运行，review 文档必须明确说明。

## 9. 风险与回滚边界

风险 1：归档导致主文档引用失效。

控制：

- 归档前用 `rg "docs/step8|step8/" docs README.md` 查找引用。
- 主文档改为引用 `docs/archive/step8/` 或保留轻量索引。

风险 2：S8-G 中顺手修代码，破坏已验收 replay。

控制：

- S8-G 禁止业务代码修改。
- 如果发现问题，新增 repair batch。

风险 3：review 结论过度乐观。

控制：

- review 必须明确列出剩余差异：
  - no overlap。
  - no load queue / backpressure。
  - no promotion。
  - no load completion event。
  - no progressive visibility。
  - no online Ramulator2 / Mooncake replay。

风险 4：Step9 准入判断不清晰。

控制：

- review 文档必须给出明确结论：
  - 是否具备进入 Step9。
  - 判断依据。
  - 进入 Step9 前是否需要 repair。

回滚边界：

- 可回滚主文档更新、review 文档和 archive 移动。
- 不涉及 Python 业务代码回滚。

## 10. 完成后如何判断可以进入 Step9

S8-G 完成条件：

1. Step8 review 文档已写入 `docs/reviews/`。
2. 主文档已同步 Step8 能力、边界、遗留问题。
3. `docs/agent_development_context.md` 与 `docs/global_memory.md` 已更新。
4. `docs/step8/` 已归档到 `docs/archive/step8/`。
5. Step8 targeted tests 通过，或明确记录未运行项及原因。
6. review 给出是否具备进入 Step9 的明确结论。

建议 Step9 准入结论预期：

```text
具备进入 Step9 技术路线设计条件。
```

判断依据：

- Step8 已经把 DDR/CPU hit 的 KV load latency 纳入 iteration duration 和 TTFT。
- typed metrics 已能解释 `kv_load_tokens` / `kv_load_bytes` / `kv_load_ms`。
- external calibration boundary 已独立于 replay 主路径。
- Step9 的核心问题 progressive visibility 不需要再改 Step8 默认 mode，而应新增新 replay/cache mode。

## 11. 需要用户审批的决定

请用户评审以下决定后再进入 S8-G 执行：

1. 是否接受 S8-G 属于核心仿真器阶段收口，改动等级 L0。
2. 是否接受 S8-G 不修改任何业务代码。
3. 是否接受新增 `docs/reviews/step8_core_simulator_review.md`。
4. 是否接受更新 `docs/infertwin_product_design.md`、`docs/core_simulator_technical_plan.md`、`docs/agent_development_context.md`、`docs/global_memory.md`。
5. 是否接受将 `docs/step8/` 整体移动到 `docs/archive/step8/`。
6. 是否接受 S8-G 不新增测试，只运行 Step8 targeted tests / ruff / diff check 并记录结果。
7. 是否接受若 review 发现业务代码问题，新增 repair batch，而不是在 S8-G 中直接修改。
8. 是否接受 S8-G review 必须明确给出能否进入 Step9 的结论。

## 12. 执行记录

执行状态：已完成。

实际完成内容：

- 新增 `docs/reviews/step8_core_simulator_review.md`。
- 更新 `docs/infertwin_product_design.md`，将 Step8 KV load latency accounting 写入当前能力边界。
- 更新 `docs/core_simulator_technical_plan.md`，将 Step8 从待开发改为已完成，并将 Step9 标记为下一阶段。
- 更新 `docs/agent_development_context.md`，把 coding agent 最小上下文切换到 Step9 入口。
- 更新 `docs/global_memory.md`，记录 Step8 完成内容、归档位置、验证结果和遗留边界。
- 更新 `README.md` 的 Core Semantics 和 archive index。

未做内容：

- 未修改 Python 业务代码。
- 未修改 replay / scheduler / cache / latency backend 默认语义。
- 未新增 CLI、report schema 或 golden。
- 未接入在线 Ramulator2 / Mooncake。
- 未实现 compute/load overlap、DDR promotion、load queue/backpressure 或 progressive visibility。

验证命令：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest \
  tests/unit/scheduler/test_batch_shape_kv_load.py \
  tests/unit/scheduler/test_request_state_kv_load.py \
  tests/unit/latency/test_shape_key_kv_load.py \
  tests/unit/latency/test_kv_load_latency.py \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/unit/replay/test_step8_kv_load_replay.py \
  tests/unit/replay/test_step8_latency_contribution_metrics.py \
  tests/unit/streaming/test_metrics.py \
  tests/unit/experiment/test_sweep_metrics.py \
  tests/unit/external/test_kv_load_calibration.py \
  tests/unit/external/test_adapter_boundaries.py \
  tests/integration/test_step8_streaming_kv_load_e2e.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/integration/test_step7_report_metrics_e2e.py \
  tests/integration/test_batch_d_runner.py
```

结果：

```text
87 passed in 7.28s
```

质量检查：

```text
TMPDIR=/tmp .venv/bin/ruff check <Step8 touched src/tests>
All checks passed!

git diff --check
passed
```

收口判断：

- Step8 已完成 DDR/CPU hit KV load latency accounting。
- Step8 未破坏 cached token accounting、HBM/DDR/miss token accounting、finish-time materialization、eviction 或 per-instance isolation。
- Step8 review 已明确记录 vLLM / vLLM-Ascend / Mooncake 差异和遗留问题。
- 具备进入 Step9：progressive chunk/block visibility 技术路线设计的条件。

归档动作：

- 本文件将随 `docs/step8/` 一起移动到 `docs/archive/step8/`。
