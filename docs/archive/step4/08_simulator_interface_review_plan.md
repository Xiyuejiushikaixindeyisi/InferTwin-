# Simulator Interface Review Plan

## 背景

Batch A + Batch B 已完成并通过代码审核。Batch C 的整体路线已认可：

```text
arrival -> waiting/running -> scheduler -> BatchShape -> latency backend -> finish_time -> materialization
```

但在进入 Batch C 代码开发前，需要重新审核数据结构和接口设计，尤其是：

- AIConfigurator / Markov-Infer-sim 对输入 shape 的真实要求。
- `batch size` 在仿真器中的含义。
- chunked prefill 在仿真器输入中如何表达。
- HitFloor 内部 `BatchShape` 是否需要拆分为 scheduler shape 和 simulator input shape。

因此 Batch C 代码开发暂停，先进入 simulator manual review。

## 已确认的 Batch C 决策

- Batch C 暂不接入 runner/report，只提供 `BatchAwareReplayEngine.run()` 给测试和 Batch D 使用。
- empty schedule 直接失败，不跳过请求，也不自动打开 chunked prefill。
- waiting lookup 采用保守策略：只对 scheduler 本轮可能考虑的队首请求 lookup，不提前 lookup 整个 waiting 队列。

## 手册沉淀方式

用户将先后提供：

1. AIConfigurator 使用手册。
2. Markov-Infer-sim 使用手册。

收到后分别沉淀为：

```text
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
```

当前状态：

- Markov-Infer-Sim 手册已收到，并沉淀为 `docs/notes/markov_infer_sim_manual.md`。
- AIConfigurator 手册已收到，并沉淀为 `docs/notes/aiconfigurator_manual.md`。

如果原始手册很长，notes 文档应包含：

- 原始信息整理。
- 最小调用方式。
- 输入 schema。
- 输出 schema。
- batch size 定义。
- chunked prefill 表达方式。
- 与 HitFloor Step4 的接口影响。
- 尚不明确的问题。

## Review 输出

阅读两份手册后，需要新增或更新：

```text
docs/step4/09_latency_backend_interface_design.md
docs/step4/07_batch_c_execution_plan.md
```

其中 `09_latency_backend_interface_design.md` 应回答：

1. HitFloor 内部是否继续使用当前 `BatchShape`。
2. 是否新增 simulator-specific input，例如 `SimulatorBatchInput`。
3. `batch_size` 是否继续等于 request slice 数，还是只作为 HitFloor 内部字段。
4. chunked prefill 应作为多个 iteration 输入，还是在 simulator input 中显式标记 chunk。
5. AIConfigurator adapter 的接口设计。
6. Markov-Infer-sim adapter 的接口设计。
7. Formula backend 是否需要调整以保持同一 schema。

## 暂停规则

在完成两份 simulator manual review 前，不进入 Batch C 代码开发。

两份 simulator manual 已完成沉淀。下一步需要输出统一 latency backend interface redesign，并据此重审 Batch C 数据结构。

可以继续做：

- 文档整理。
- 接口设计。
- 数据结构重审。
- Batch A/B 已有代码的解释与评审。

不做：

- 新增 `BatchAwareReplayEngine`。
- 改动 runner。
- 改动 report。
- 改动 scheduler/latency schema，除非接口设计先审批通过。
