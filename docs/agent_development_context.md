# InferTwin Agent Development Context

本文档是面向 coding agent 的最小开发上下文。进入代码开发、代码评审或阶段方案编写时，优先读取本文档；只有当本文件无法回答问题时，才按索引读取产品设计、技术路线、当前 step 文档或 archive。

目标不是复述全部历史，而是让 agent 在不翻完整 project 的情况下保持接口、边界和 review 口径一致。

本文档也是开发上下文治理后的第一入口。后续开发默认减少历史聊天和 archive 依赖，但不减少相关源码阅读、核心 replay 保护和测试验收。

## 1. 当前定位

InferTwin 是面向 TOB 大型推理服务集群的离线仿真器。

当前重点是核心仿真器，而不是外围报表产品。V1 准出前，不新增新的外围能力；外围能力只能消费核心仿真器 typed result。

当前 V1 范围：

- Step7：单实例 DDR/CPU pooling hit accounting，已完成。
- Step8：KV load latency，已完成。
- Step9：progressive chunk/block visibility / chunk-level TTFT timeline，已完成。

V2 之后再处理 gateway、实例侧排队、多实例池化跨实例命中、Decode / TPOT、复杂 Hybrid 模型和新一轮大规模工程优化。

## 2. 必须先声明的边界

每个新阶段、batch、代码修改或 review 开始前，必须先说明：

```text
本次开发的是核心仿真器，还是外围能力。
```

核心仿真器负责 replay 语义：

- trace -> `SimulationRequest`。
- tokenizer / chat template。
- prefix block hash。
- scheduler replay。
- cache lookup、materialization、eviction、event stats。
- latency backend。
- deterministic request / iteration / sweep metrics。

外围能力只消费 typed result：

- `capacity_sweep.csv`。
- `summary.md`。
- CLI / scripts wrapper。
- dashboard、notebook、batch job。
- 未来 hit floor search、容量规划、策略对比报告。

外围能力不能重算或修改 replay 语义。需要新语义时，新增 replay mode、cache backend、policy、adapter 或 result schema。

## 3. 主调用链

大 trace 主路径：

```text
CSV trace
  -> streaming shard builder
  -> per-instance request source
  -> SimulationRequest
  -> BatchAwareReplayEngine
  -> scheduler planning
  -> cache lookup
  -> latency backend
  -> finish/materialization
  -> metrics / event sink
  -> report exporter
```

大 trace 必须优先使用：

```bash
PYTHONPATH=src python -m infertwin.cli.main sweep-streaming --config <config.yaml>
```

`simulate` 和非 streaming `sweep` 只用于小 trace、开发调试和回归测试。

## 4. 核心目录

- `src/infertwin/trace/`：CSV schema、reader、trace guard。
- `src/infertwin/request/`：request parser、tokenizer registry、chat template、prefix hash。
- `src/infertwin/scheduler/`：vLLM-like scheduler、chunked prefill planning、waiting queue、batch shape。
- `src/infertwin/cache/`：HBM / DDR cache backend、block metadata、event、eviction policy。
- `src/infertwin/latency/`：fitted TTFT、ServingLatencyProfile、KV load latency component。
- `src/infertwin/replay/`：batch-aware replay event loop。
- `src/infertwin/streaming/`：true streaming shard、source、metrics、runner。
- `src/infertwin/experiment/`：实验级 orchestration。
- `src/infertwin/report/`：CSV / Markdown report/export 外围能力。
- `src/infertwin/cli/`：package CLI 正式入口。
- `src/infertwin/config/`：profile、registry、binding、guard。
- `src/infertwin/external/`：AIConfigurator、MkSim、Ramulator2 adapter 边界。

## 5. 当前稳定语义

这些语义不要静默修改：

- 输入 trace 默认是 routed trace，核心 reader 应拒绝空 `instance_uuid`。
- 无实例 id 的 trace 如需单实例 baseline，由外围 `normalize-trace` 先补统一 `instance_uuid`；这不是 gateway routing。
- `batch_size` 是单个 scheduler iteration 内 request slice 数，不是 token batch。
- `max_num_batched_tokens` 是 iteration token budget，不是 batch size。
- `batch_aware_hbm_lru` / `batch_aware_hbm_ddr_lru` 默认使用 finish-time materialization。
- cache 只保存 hash key 和 metadata，不保存真实 KV tensor。
- HBM / DDR capacity 与模型 runtime defaults 绑定；capacity sweep 可以用 sweep candidate 覆盖 HBM capacity。
- model registry 相对路径默认相对 registry 文件所在目录。
- `streaming.require_sorted_trace=false` 在 V1 禁用；外部排序 / shard sort 是 V2 工程能力。
- per-instance TTFT 已支持；完整 per-instance scheduler/cache/deployment 异构 replay 仍按能力逐步补齐。

## 6. 与真实 vLLM / vLLM-Ascend 的关键差异

当前需要记住的差异：

- 不部署真实模型；TTFT 由 fitted profile / future latency component 估算。
- 不保存真实 KV tensor；只保存 block hash 和 metadata。
- 不建模 physical KV slot、refcount、pin、fragmentation。
- legacy `batch_aware_hbm_lru` / `batch_aware_hbm_ddr_lru` 仍采用 finish-time materialization。
- Step9 已新增 `batch_aware_hbm_ddr_lru_progressive_timeline`，chunk finish 后 newly completed full blocks 可见。
- Step8 已补 DDR/CPU hit 的 KV load latency；Step9 已补 deterministic shared-link FIFO wait accounting，但仍不是真实 Mooncake / TransferEngine backpressure。
- DDR hit 当前不做 promotion。
- Decode / TPOT 未建模，V2 pending。
- 不做 gateway routing；fixed-routing trace 内的多实例互相隔离 replay。
- 不做实例外 queue waiting；当前 `queue_waiting_ms` 语义仍是 0 或待后续排队层扩展。

## 7. 开发规则

默认流程：

1. 先写本 batch 方案和执行记录。
2. 用户评审通过后再改业务代码。
3. 只修改方案中列出的文件。
4. 如果发现必须越界修改，暂停并重新评审。
5. 开发后运行对应测试并记录结果。

默认测试等级：

- 文档-only：`smoke`，至少运行 `git diff --check`。
- 普通功能：`targeted`，新增测试 + 相关测试 + `ruff` + `git diff --check`。
- 阶段收口：`closure`，targeted + 全量 `pytest`。

代码风格：

- 优先清晰代码，不写聪明代码。
- parser / scheduler / cache / latency / replay / report 职责分离。
- CLI 和 scripts 只做参数解析、调用 package、写输出。
- report/export 不得重新分析或重算 replay 语义。
- 遇到 unknown schema、未支持配置、缺失实例绑定时 fail-fast 或 config guard，不要静默 fallback。

技术路线和代码编写方案必须单独列出“需要用户审批的内容”。在用户明确审批通过前，不允许进入业务代码开发。

审批清单至少包括：

- 本阶段 / 本 batch 属于核心仿真器还是外围能力。
- 改动等级是 L0 / L1 / L2 / L3。
- 做什么。
- 不做什么。
- 是否修改核心 replay 语义。
- 是否新增或修改 schema / mode / backend / policy / adapter / interface。
- 是否修改默认配置或默认行为。
- 允许修改的文件范围。
- 禁止修改的文件范围。
- 测试范围和验收方式。
- 是否允许进入代码开发。

### 7.1 改动分级

后续开发默认按改动风险分级：

| 等级 | 类型 | 示例 | 默认要求 |
| --- | --- | --- | --- |
| L0 | 文档治理 | 文档、索引、记忆 | `git diff --check` |
| L1 | 外围能力 | report、benchmark、normalizer、capacity sweep wrapper | 方案审批 + 相关单测或小 E2E |
| L2 | 核心非 replay | config guard、schema、registry、profile resolver | 方案审批 + 相关单测 + 小 E2E |
| L3 | 核心 replay | scheduler、cache lookup、materialization、latency shape、streaming replay | 单独方案审批 + 相关单测 + 小 E2E + 必要时 closure |

一旦 L0 / L1 / L2 任务发现必须修改 L3 核心 replay，应暂停并重新提交方案。

### 7.2 核心 Replay 保护清单

以下内容属于核心 replay 保护区，修改时默认视为 L3：

- trace 到 `SimulationRequest` 的构造。
- tokenizer / chat template / prefix hash。
- scheduler planning、waiting queue、running set。
- chunked prefill selection。
- block conversion / cached token accounting。
- HBM / DDR lookup。
- materialization policy。
- eviction policy 状态转移。
- cache event 顺序和语义。
- latency shape、finish time、TTFT。
- streaming replay 的 instance isolation。

L3 改动必须显式说明是否影响：

```text
cached_tokens
hbm_hit_tokens / ddr_hit_tokens / miss_tokens
finish_time / ttft_ms
cache event 顺序
materialization timing
实例隔离
capacity sweep 输出
true streaming 大 trace
```

### 7.3 默认读取策略

方案阶段默认只读：

- 本文档。
- 当前阶段文档。
- 必要的主技术路线或产品文档。

代码开发阶段默认读：

- 本文档。
- 当前 batch 文档。
- 计划修改的源码和直接依赖。
- 相关测试。

默认不要读取整个 project、整个 `docs/archive/` 或全部 review 文档。需要历史依据时，只读取指定 archive / review 文件。

## 8. 当前 StepY 开发入口

Step8 已完成 KV load latency 并归档：

```text
docs/archive/step8/
docs/reviews/step8_core_simulator_review.md
docs/reviews/step8_review.md
docs/reviews/step8_engineering_closure.md
```

Step8 稳定语义：

- 不改变 Step7 的 HBM / DDR hit 统计。
- 不改变 `batch_aware_hbm_ddr_lru` 的 cache hit semantics。
- DDR/CPU hit 可以通过 `KVLoadLatencyProfile` 进入 `kv_load_ms`。
- `kv_load_tokens`、`kv_load_bytes`、`kv_load_ms` 是显式 typed metrics。
- 默认 `overlap_mode=none_v1`，即 `iteration_duration = queue_ms + prefill_compute_ms + kv_load_ms`。
- Ramulator2 / Mooncake 只作为 calibration source / adapter 边界，不作为默认在线 replay 依赖。
- 不做 DDR hit promotion、load queue/backpressure、load completion event 或 online external replay。

Step9 已完成 progressive chunk/block visibility / chunk-level TTFT timeline，并通过核心仿真器 review 与工程收口：

```text
docs/archive/step9/
docs/reviews/step9_core_simulator_review.md
docs/reviews/step9_engineering_closure.md
```

Step9 稳定语义：

- 不修改默认 `batch_aware_hbm_lru` / `batch_aware_hbm_ddr_lru` 的 finish-time materialization 语义。
- 新增 `batch_aware_hbm_ddr_lru_progressive_timeline` mode。
- progressive mode 下，scheduled chunk finish 后 newly completed full miss blocks 可以进入 cache。
- progressive mode 下，TTFT 由 `compute_wait_ms + kv_load_wait_ms + uncached_prefill_compute_ms + unattributed_ttft_ms` 组成。
- `unattributed_ttft_ms` 是 replay 粒度残差，不是物理建模结果。
- shared-link FIFO 是 deterministic accounting abstraction，不是真实 Mooncake / TransferEngine。

当前下一阶段暂称 StepY。StepY 的产品形态和技术路线尚未定义，进入前必须重新声明：

```text
本阶段开发的是核心仿真器，还是外围能力。
```

V2 约束：

- gateway、instance queue、cross-instance pooling、Decode / TPOT、Hybrid / sparse attention cache 都必须作为独立模块或新 mode 接入。
- V2 实验能力不得反向修改 V1 默认 replay 语义。

## 9. 什么时候读取更多文档

优先读取本文档。只有以下情况才继续读取更多文档：

- 产品边界不清楚：读 `docs/infertwin_product_design.md`。
- 技术路线不清楚：读 `docs/core_simulator_technical_plan.md`。
- 开发治理不清楚：读 `docs/development_governance.md`。
- 代码规范不清楚：读 `docs/code_development_requirements.md`。
- 当前阶段细节不清楚：读当前 step 文件夹。
- 需要历史决策依据：只读相关 archive 文件，不扫描整个 `docs/archive/`。

默认不要读取整份 project、整份 archive 或所有 review 文档。
