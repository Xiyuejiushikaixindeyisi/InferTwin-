# InferTwin 开发上下文治理方案

本文档是一次临时治理阶段的方案文档，目标是在不牺牲工程质量的前提下，降低长会话、长文档和大范围代码扫描带来的 TTFT、TPOT 与 Thinking 时间成本。

本阶段只治理开发协作方式、文档入口和编码约束，不修改业务代码，不修改核心 replay 语义。

## 1. 背景

InferTwin 已从 HitFloor 的 KV cache hit 仿真器，演化为面向 TOB 大型推理服务集群的离线仿真平台。当前已经完成 V1 replay 骨架、true streaming、实例配置绑定、HBM/DDR tier hit accounting，准备进入 Step8 KV load latency。

随着历史阶段增多，开发上下文出现几个问题：

- 聊天上下文包含大量历史设计、评审、归档和阶段结论，每轮推理成本变高。
- `docs/archive/`、`docs/reviews/`、`docs/notes/` 中存在大量历史材料，若每轮默认读取，容易引入过期口径。
- 当前核心 replay 语义很重要，不能为了减少上下文而让 agent 靠记忆或猜测改代码。
- Step8、Step9、V2 会继续扩展 latency、progressive visibility、gateway、queue、hybrid model，如果没有清晰治理，后续容易出现外围能力污染核心仿真器。

治理目标不是减少约束，而是把约束变成更短、更权威、更可执行的入口。

## 2. 治理目标

### 2.1 必须达成

- 降低每轮开发需要读取的文档量。
- 明确哪些文档是权威入口，哪些文档只是历史证据。
- 明确不同类型改动的评审和测试要求。
- 保护核心 replay 能力不被外围能力反向污染。
- 让 Step8、Step9、V2 通过新接口、新 mode、新 backend 扩展，而不是修改旧语义。
- 让 coding agent 能够在短上下文下稳定开发。

### 2.2 明确不做

- 不删除历史文档。
- 不修改业务代码。
- 不调整已有 replay 行为。
- 不简化测试要求。
- 不把未实现能力写成已实现能力。
- 不把 archive 中的历史设计重新提升为当前语义。

## 3. 风险判断

短上下文本身不会降低代码质量。真正的风险来自以下情况：

- 只读文档，不读相关源码。
- 只凭聊天记忆修改核心 replay。
- 把历史 archive 当成当前设计。
- 外围 report / CLI / script 偷偷重算 cache hit、TTFT 或 event。
- 修改核心 replay 后没有跑针对性测试。
- 为了兼容未知输入格式加入隐式 fallback。

因此治理策略是：

```text
减少历史噪声，不减少权威约束。
减少无关文件读取，不减少相关源码阅读。
减少大范围扫描，不减少核心路径测试。
```

## 4. 文档权威层级

后续开发默认按以下优先级读取文档。

| 层级 | 文档 | 作用 | 默认读取 |
| --- | --- | --- | --- |
| L1 | `docs/agent_development_context.md` | coding agent 最小开发上下文 | 是 |
| L1 | 当前阶段文档，例如 `docs/step8/` | 当前阶段范围、技术路线、batch 计划 | 是 |
| L2 | `docs/code_development_requirements.md` | 编码要求、测试原则、规模阈值 | 需要代码开发时读取 |
| L2 | `docs/development_governance.md` | 阶段流程、文档治理、目录职责 | 需要治理或阶段收口时读取 |
| L2 | `docs/core_simulator_technical_plan.md` | 核心仿真器长期技术路线 | 技术路线不清楚时读取 |
| L2 | `docs/infertwin_product_design.md` | 产品形态和核心/外围关系 | 产品边界不清楚时读取 |
| L3 | `docs/reviews/` | 评审证据 | review / closure 时按需读取 |
| L3 | `docs/notes/` | 学习资料 | 调研外部系统时按需读取 |
| L4 | `docs/archive/` | 历史阶段记录 | 默认不读取；只在追溯历史决策时读取指定文件 |

禁止默认扫描整个 `docs/archive/`、整个 `docs/reviews/` 或整个 project。

## 5. 默认读取策略

### 5.1 方案阶段

默认读取：

- `docs/agent_development_context.md`
- 当前阶段文档
- 必要时读取主技术路线或产品文档

默认不读取：

- archive
- 全量源码
- 全量测试

### 5.2 代码开发阶段

默认读取：

- `docs/agent_development_context.md`
- 当前 batch 文档
- 计划修改的源码
- 计划修改模块的直接依赖
- 相关测试

如果改动涉及核心 replay，还必须读取对应核心模块和测试。

### 5.3 Review / 收口阶段

默认读取：

- 当前阶段执行记录
- 相关源码
- 相关测试
- 必要的 review 文档

阶段收口时才更新主文档和全局记忆。

## 6. 改动分级

不同改动使用不同治理强度。

| 等级 | 类型 | 示例 | 审批要求 | 测试要求 |
| --- | --- | --- | --- | --- |
| L0 | 文档治理 | 更新说明、索引、记忆 | 可直接按用户要求改 | `git diff --check` |
| L1 | 外围能力 | report、benchmark、normalizer、capacity sweep wrapper | 方案审批后改 | 相关单测或小 E2E |
| L2 | 核心非 replay | config guard、schema、registry、profile resolver | 方案审批后改 | 相关单测 + 小 E2E |
| L3 | 核心 replay | scheduler、cache lookup、materialization、latency shape、streaming replay | 必须单独方案审批 | 新增/相关单测 + 小 E2E + 必要时阶段 closure |

一旦 L1 / L2 改动发现需要修改 L3 模块，应暂停开发并重新提交方案。

## 7. 核心 replay 保护清单

以下模块或语义属于核心 replay 保护区。修改时默认升级为 L3：

- trace 到 `SimulationRequest` 的构造。
- tokenizer / chat template / prefix hash。
- scheduler planning。
- waiting queue 与 running set 状态推进。
- chunked prefill selection。
- block conversion / cached token accounting。
- HBM / DDR lookup。
- materialization policy。
- eviction policy 状态转移。
- cache event 顺序和语义。
- latency shape、finish time、TTFT。
- streaming replay 的 instance isolation。

L3 改动必须回答：

```text
是否改变 cached_tokens？
是否改变 hbm_hit_tokens / ddr_hit_tokens / miss_tokens？
是否改变 finish_time / ttft_ms？
是否改变 cache event 顺序？
是否改变 materialization timing？
是否改变实例隔离？
是否改变 capacity sweep 输出？
是否影响 true streaming 大 trace？
```

## 8. 核心仿真器与外围能力边界

### 8.1 核心仿真器

核心仿真器负责：

- trace schema guard。
- request build。
- tokenizer / chat template。
- prefix block hash。
- scheduler replay。
- cache lookup / materialization / eviction。
- latency backend。
- per-instance isolation。
- typed metrics / typed result。

### 8.2 外围能力

外围能力负责：

- CLI wrapper。
- report/export。
- capacity sweep 表。
- benchmark。
- trace normalizer。
- dashboard / notebook。
- future hit floor search。

外围能力只能消费核心仿真器输出，不得重算：

- cache hit。
- cached tokens。
- miss tokens。
- TTFT。
- cache event。
- instance replay ordering。

如果外围能力需要新语义，应回到核心仿真器新增：

- replay mode。
- cache backend。
- materialization policy。
- latency component。
- result schema。
- adapter boundary。

## 9. Step8 / Step9 / V2 扩展约束

### 9.1 Step8：KV Load Latency

Step8 属于核心仿真器。

必须遵守：

- 只增加非 HBM hit 的 KV load latency accounting。
- 不改变 Step7 的 HBM / DDR hit 判定。
- 不改变默认 materialization mode。
- 不实现 DDR promotion。
- 不把 Ramulator2 作为默认在线 replay 依赖。
- 必须让 `kv_load_tokens`、`kv_load_bytes`、`kv_load_ms` 成为显式语义。

如果 Step8 需要调整 `ShapeKey` 或 memoization，必须说明是否影响已有 fitted TTFT 结果。

### 9.2 Step9：Progressive Visibility

Step9 属于核心仿真器。

必须遵守：

- 不修改默认 `batch_aware_hbm_ddr_lru` 的 finish-time materialization 语义。
- 新增独立 mode，例如 `batch_aware_hbm_ddr_lru_progressive`。
- progressive visibility 必须有独立 materialization policy。
- 必须明确 chunk 完成、block 可见、后续 lookup 的时间关系。
- 必须用测试证明长 prefill 场景下 hit 不再被低估。

### 9.3 V2

V2 能力必须以独立模块或模式接入：

- gateway simulation。
- instance-side queue simulation。
- cross-instance pooling。
- Decode / TPOT。
- Hybrid / Mamba / sparse attention cache。
- 更细粒度 physical KV storage。

V2 不允许通过修改 V1 默认 replay 语义来实现实验能力。

## 10. 任务模板

以后新任务建议使用以下模板。

### 10.1 方案任务

```text
任务：
类型：核心仿真器 / 外围能力
改动等级：L0 / L1 / L2 / L3
允许读取：
不允许读取：
是否允许修改代码：否
输出要求：
验收方式：
```

### 10.2 开发任务

```text
任务：
类型：核心仿真器 / 外围能力
改动等级：L0 / L1 / L2 / L3
允许修改：
禁止修改：
必须测试：
必须记录：
发现越界时：
```

### 10.3 Review 任务

```text
任务：
评审范围：
读取范围：
测试依据：
重点风险：
输出位置：
```

## 11. 执行批次

本治理阶段建议按以下批次执行。每个批次都先说明范围，再修改文档。

### DCG-A：现状小审计

目标：

- 检查 `agent_development_context.md`、`development_governance.md`、`code_development_requirements.md` 的当前口径。
- 不扫描 archive。
- 输出需要同步的条目。

不做：

- 不改业务代码。
- 不改核心 replay。

### DCG-B：更新最小开发上下文

修改：

- `docs/agent_development_context.md`

新增或强化：

- 默认读取策略。
- L0-L3 改动分级索引。
- 核心 replay 保护清单。
- Step8 / Step9 / V2 扩展约束。

### DCG-C：更新开发治理

修改：

- `docs/development_governance.md`

新增或强化：

- 文档权威层级。
- archive 读取规则。
- 任务模板。
- 阶段收口规则。

### DCG-D：更新编码要求

修改：

- `docs/code_development_requirements.md`

新增或强化：

- L3 核心 replay 修改检查项。
- 外围能力不得重算核心指标。
- 新接口优先于修改旧语义。
- unknown schema fail-fast。

### DCG-E：更新全局记忆

修改：

- `docs/global_memory.md`

写入：

- InferTwin 后续默认使用开发上下文治理协议。
- `agent_development_context.md` 是 coding agent 第一入口。
- Step8 之后开发必须声明核心仿真器 / 外围能力。

### DCG-F：一致性检查

执行：

- `git diff --check`
- 检查新增文档是否把未实现能力写成已实现。
- 检查是否把外围能力写成核心 replay。
- 检查是否错误要求默认读取 archive。

不执行：

- 不跑全量 pytest。
- 不修改 Python 业务代码。

## 12. 准出标准

本治理阶段完成后，应满足：

- agent 有一个明确、短小、权威的开发入口。
- 后续任务可以按 L0-L3 分级执行。
- 核心 replay 修改有明确保护清单。
- 外围能力不能污染核心仿真器。
- Step8、Step9、V2 的扩展方式更清楚。
- 主文档、全局记忆、编码要求之间没有明显冲突。

只有满足上述条件，才进入 Step8 代码方案和开发。

## 13. 执行记录

2026-06-29，DCG-A 到 DCG-F 已执行。

已同步：

- `docs/agent_development_context.md`：补充改动分级、核心 replay 保护清单、默认读取策略、Step8 / Step9 / V2 扩展约束。
- `docs/development_governance.md`：补充文档权威层级、archive 读取规则、L0-L3 改动分级和任务模板。
- `docs/code_development_requirements.md`：补充核心 replay 自检要求、外围能力不得重算核心指标、新语义优先使用新接口。
- `docs/global_memory.md`：补充开发上下文治理已生效、当前阶段、Step8 入口和长期开发约束。

检查结果：

- 未修改业务代码。
- 未修改核心 replay 语义。
- 未把未实现能力写成已实现能力。
- 未把外围能力写成核心 replay。
- `git diff --check` 通过。
