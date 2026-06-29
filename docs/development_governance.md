# InferTwin 开发治理与文档组织

## 1. 阶段开发流程

Step 3 之后，每个阶段必须按以下顺序推进。

### 1.1 产品形态讨论

目标：确认本阶段到底要交付什么。

必须完成：

- 声明本阶段开发的是核心仿真器还是外围能力。
- 讨论本阶段需求边界。
- 明确输入、输出、用户可见行为。
- 明确不做什么。
- 得到用户审批。
- 经用户允许后沉淀为文档。

### 1.2 技术路线讨论

目标：确认本阶段怎么实现。

必须完成：

- 研究当前产品形态对应的实现范围。
- 讨论实现粒度。
- 设计代码骨架。
- 设计核心数据模型。
- 设计核心算法逻辑。
- 明确测试策略。
- 得到用户审批。

### 1.3 代码开发讨论

目标：在用户允许后进行代码实现。

必须完成：

- 说明计划修改的文件和目的。
- 得到用户确认后再修改代码。
- 实现功能代码。
- 实现对应测试代码。
- 进行代码评审。
- 运行测试。
- 根据测试和评审结果迭代修改。
- 完成功能端到端测试。

## 2. 代码开发原则

当前已确认原则：

- 可维护、可测试是第一准则。
- 进入代码开发、代码评审或阶段方案编写时，优先读取轻量开发入口文档 `docs/agent_development_context.md`，不要默认扫描整份 project 或 archive。
- Step 3 之后不直接跳到代码实现。
- 代码修改前必须先向用户确认。
- 测试和端到端验证必须成为每阶段交付的一部分。
- 每次进入新阶段或新开发批次前，必须说明本次开发属于核心仿真器还是外围能力。
- 核心仿真器输出 typed result；外围能力只消费 typed result，不重算 replay 语义。
- 如果外围能力需要新语义，应回到核心仿真器设计，新增 replay mode、cache backend、policy、adapter 或 result schema。
- V1 核心仿真器准出前，不新增新的外围能力；外围能力必须等核心 replay/cache/latency 语义稳定后再消费 typed result。

具体代码开发要求已写入独立文档，后续如有新增要求，应继续更新该文档。

当前代码开发要求文档：

```text
docs/code_development_requirements.md
```

当前 coding agent 最小开发上下文：

```text
docs/agent_development_context.md
```

### 2.1 长期 Batch 协作模式

为降低长上下文、复杂约束和大测试矩阵带来的协作成本，InferTwin 后续开发默认采用更小粒度的 batch 协作模式。

每个 batch 默认分为：

1. 方案阶段：只写本 batch 的开发方案与执行记录文档，不改业务代码。
2. 开发阶段：只修改方案中列出的文件；如果发现必须越界修改，应暂停并重新评审。
3. 验收阶段：按当前 batch 的测试等级运行测试，并把结果写入执行记录。
4. 记忆阶段：小 batch 默认只更新 batch 执行文档；阶段收口或用户明确要求时，再更新 `docs/global_memory.md` 和主文档。

每个 batch 文档必须记录：

- 本 batch 属于核心仿真器还是外围能力。
- 做什么。
- 不做什么。
- 允许修改的文件范围。
- 不允许修改的文件范围。
- 测试等级。
- 测试结果。
- 风险、边界、是否建议进入下一 batch。

### 2.2 测试等级

后续开发默认使用分级测试，而不是每个小 batch 都运行全量测试。

| 等级 | 适用场景 | 默认测试范围 |
| --- | --- | --- |
| `smoke` | 文档、小型 helper、低风险局部改动 | 新增或直接相关测试 |
| `targeted` | 普通功能开发、runner/report/cache 局部集成 | 新增测试 + 相关模块单测/集成测试 + `ruff` + `git diff --check` |
| `closure` | 阶段收口、核心语义改动、进入下一大阶段前 | targeted 测试 + 全量 `pytest` + `ruff` + `git diff --check` |

默认规则：

- 普通 batch 使用 `targeted`。
- 阶段收口使用 `closure`。
- 文档-only batch 可使用 `smoke`，但仍应运行 `git diff --check`。
- 用户可以在指令中显式指定测试等级。
- 如果 targeted 测试暴露出跨模块风险，升级为 `closure`。

### 2.3 文档更新频率

为避免小 batch 花费过多时间反复同步主文档：

- 小 batch：优先只更新本 batch 的执行记录文档。
- 关键语义变化：必须同步更新主文档和全局记忆。
- 阶段收口：统一更新产品设计、核心技术路线、开发治理、全局记忆和 review / archive。
- 如果用户明确要求更新全局记忆或主文档，应立即同步。

### 2.4 文档权威层级与读取规则

InferTwin 后续开发默认减少历史上下文依赖，但不减少工程约束和测试验收。

文档读取优先级：

| 层级 | 文档 | 作用 | 默认读取 |
| --- | --- | --- | --- |
| L1 | `docs/agent_development_context.md` | coding agent 最小开发上下文 | 是 |
| L1 | 当前阶段文档，例如未来 `docs/step9/` | 当前阶段范围、技术路线、batch 计划 | 是 |
| L2 | `docs/code_development_requirements.md` | 编码要求、测试原则、规模阈值 | 代码开发时读取 |
| L2 | `docs/development_governance.md` | 阶段流程、文档治理、目录职责 | 治理或阶段收口时读取 |
| L2 | `docs/core_simulator_technical_plan.md` | 核心仿真器长期技术路线 | 技术路线不清楚时读取 |
| L2 | `docs/infertwin_product_design.md` | 产品形态和核心/外围关系 | 产品边界不清楚时读取 |
| L3 | `docs/reviews/` | 评审证据 | review / closure 时按需读取 |
| L3 | `docs/notes/` | 学习资料 | 调研外部系统时按需读取 |
| L4 | `docs/archive/` | 历史阶段记录 | 默认不读取，只在追溯历史决策时读取指定文件 |

默认禁止无目的扫描整个 `docs/archive/`、整个 `docs/reviews/` 或整个 project。需要历史证据时，应说明要查找的问题，并只读取相关文件。

### 2.5 改动分级

后续开发按改动风险分级执行。

| 等级 | 类型 | 示例 | 审批与测试要求 |
| --- | --- | --- | --- |
| L0 | 文档治理 | 更新说明、索引、记忆 | 按用户要求改，至少 `git diff --check` |
| L1 | 外围能力 | report、benchmark、normalizer、capacity sweep wrapper | 方案审批后改，跑相关单测或小 E2E |
| L2 | 核心非 replay | config guard、schema、registry、profile resolver | 方案审批后改，跑相关单测 + 小 E2E |
| L3 | 核心 replay | scheduler、cache lookup、materialization、latency shape、streaming replay | 必须单独方案审批，跑新增/相关单测 + 小 E2E，必要时阶段 closure |

一旦 L0 / L1 / L2 任务发现必须修改 L3 模块，应暂停并重新提交方案。

### 2.6 任务模板

技术路线和代码编写方案必须包含独立的“需要审批的内容”章节。用户明确审批通过前，不允许进入业务代码开发。

方案任务建议使用：

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

技术路线或代码编写方案结尾必须包含：

```text
## 需要审批的内容

- [ ] 本阶段 / 本 batch 属于核心仿真器还是外围能力。
- [ ] 改动等级是 L0 / L1 / L2 / L3。
- [ ] 本阶段 / 本 batch 做什么。
- [ ] 本阶段 / 本 batch 不做什么。
- [ ] 是否修改核心 replay 语义。
- [ ] 是否影响 cached_tokens / miss_tokens / finish_time / cache event / instance isolation。
- [ ] 是否新增或修改 schema / mode / backend / policy / adapter / interface。
- [ ] 是否修改默认配置或默认行为。
- [ ] 允许修改的文件范围。
- [ ] 禁止修改的文件范围。
- [ ] 必须新增或更新的测试。
- [ ] 验收方式。
- [ ] 是否允许进入代码开发。
```

开发任务建议使用：

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

Review 任务建议使用：

```text
任务：
评审范围：
读取范围：
测试依据：
重点风险：
输出位置：
```

## 3. 项目目录职责与整理逻辑

InferTwin 的目录组织必须服务于两个核心目标：

- 核心仿真器与外围能力分离。
- 代码、配置、文档、数据、报告和测试各归其位。

### 3.1 顶层目录

| 目录 | 存放内容 | 整理逻辑 |
| --- | --- | --- |
| `src/infertwin/` | InferTwin 主 Python package，包含核心仿真器、runner、report、CLI、外部 adapter 边界 | 所有可复用业务逻辑集中在 package 中，方便测试、CLI 调用和未来安装成正式 Python 包 |
| `configs/` | 模型、硬件、backend、实验配置 YAML | 实验参数脱离代码，换模型、换容量、换 latency backend 时不改 Python |
| `tokenizers/` | tokenizer profile，例如 `glm-v5/manifest.yaml`、`tokenizer.json`、chat template | tokenizer 是模型资产，不硬编码进代码；按 profile 管理，方便多模型扩展 |
| `tests/` | 单元测试和集成测试 | 保证 replay、scheduler、cache、latency、report 等核心语义可复现、可回归 |
| `scripts/` | 本地开发 wrapper，例如 simulation、capacity sweep、benchmark | 只做薄封装，正式逻辑仍在 `src/infertwin/`，禁止在脚本中承载核心业务 |
| `docs/` | 产品设计、核心技术路线、开发约束、notes、archive、review 文档 | 保持长期设计和阶段材料可追踪，避免长期开发中口径漂移 |
| `data/` | 样例 trace、raw/processed 本地数据目录 | 样例数据可入库，真实现网数据默认不入库，保护数据并避免仓库膨胀 |
| `reports/` | 仿真输出报告目录 | 生成物默认不入库，只保留 `.gitkeep`，防止结果文件污染代码版本 |
| `notebooks/` | 探索性分析 notebook 预留目录 | 用于临时分析和可视化，不进入核心仿真器逻辑 |
| `.git/` | Git 版本管理元数据 | 保存 commit、index、分支等版本信息，不手动修改 |
| `.venv/` | 本地 Python 虚拟环境 | 运行和测试环境，已被 `.gitignore` 排除，不进入仓库 |

本地工具目录如 `.codex/`、`.agents/`、`.pytest_cache/`、`.ruff_cache/`、`.vscode/` 不属于 InferTwin 产品结构，除非明确需要，否则不纳入项目文档和版本管理。

### 3.2 `src/infertwin/` 子目录

| 子目录 | 职责 |
| --- | --- |
| `trace/` | CSV trace schema 和 reader，把原始 csv 行变成结构化 `TraceRecord` |
| `request/` | request JSON parser、model resolver、tokenizer registry、chat template、prefix block hash |
| `instance/` | `SimulationRequest` 等实例侧基础结构和早期 replay utility |
| `scheduler/` | vLLM-like scheduler、chunked prefill planning、waiting queue、batch shape |
| `cache/` | infinite HBM、finite HBM LRU、block metadata、event sink、eviction policy |
| `latency/` | fitted TTFT backend、formula backend、latency schema、shape memo |
| `replay/` | batch-aware replay event loop 和 replay metrics，是核心仿真流程的主干 |
| `experiment/` | request build、single run、capacity sweep orchestration、实验级聚合 |
| `report/` | CSV / Markdown 输出，是外围 report/export 能力 |
| `cli/` | package CLI 正式入口，例如 `simulate`、`sweep` |
| `external/` | AIConfigurator、MkSim、Ramulator2 等外部工具 adapter 边界 |
| `config/` | 配置加载 |
| `utils/` | 通用工具预留 |

### 3.3 整理原则

- 核心 replay 语义只能放在 `src/infertwin/` 的核心模块中。
- 外围能力只消费核心仿真器 typed result；CSV、Markdown、dashboard、script 不能重算 replay 语义。
- 配置放 `configs/`，模型 tokenizer 资产放 `tokenizers/`，实验输出放 `reports/`，真实数据放 `data/raw/` 或 `data/processed/` 并默认不入库。
- 新能力如果改变 request、scheduler、cache、latency、replay 语义，应新增 replay mode、cache backend、policy、adapter 或 result schema，而不是在外围目录中打补丁。
- 阶段文档完成后归档到 `docs/archive/`；长期有效的产品和技术口径保留在 active docs。

## 4. 仿真器与真实 vLLM / vLLM-Ascend 差异治理

InferTwin 的长期目标是尽量贴近真实基于 vLLM / vLLM-Ascend 的推理服务，但它不是在线推理框架，也不部署真实模型。

差异治理原则：

- 能对齐真实推理框架的 replay 语义，应尽量对齐。
- 因性能或能力边界暂不对齐的地方，必须记录现状、原因、是否需要修改和触发条件。
- 需要修改的差异统一视为遗留问题，后续通过新 replay mode、cache backend、policy、adapter 或 schema 逐项关闭。
- 不允许在 report、CLI、script 中偷偷修正核心 replay 语义。

| 维度 | 当前 InferTwin 现状 | 主要原因 | 是否需要修改 | 修改触发条件 |
| --- | --- | --- | --- | --- |
| TTFT 构造 | 使用 fitted TTFT / ServingLatencyProfile 估算 iteration duration | 离线仿真，不部署真实模型 | 需要增强 | 需要更高精度 TTFT、接 AIConfigurator / MkSim / production logs 校准 |
| 真实计算 kernel | 不执行 attention / MLP / decode kernel | 核心定位是不部署模型的离线 replay | 不作为默认能力 | 只通过外部 simulator adapter 或校准 harness 接入 |
| 真实 KV 存储 | cache 只保存 hash key 和 metadata，不保存真实 KV tensor | 防止内存爆炸，支持大 trace | 默认不改 | 研究 physical slot、refcount、fragmentation 时新增模式 |
| 物理 block table | 不建模 physical KV slot allocation、pinned/refcount | 当前只做逻辑 prefix cache | 需要时新增 | 需要评估真实显存碎片、slot allocation failure、共享 block refcount |
| cache lookup timing | request 第一次可能被 scheduler 考虑时 lookup | 对齐 scheduler admission 语义，避免到达即 lookup | 基本合理 | 后续更精细对齐 vLLM scheduler 时重新校准 |
| materialization timing | 默认 finish-time materialization | 保守、简单、deterministic | 需要修改 | Step9 新增 progressive mode |
| progressive block visibility | 当前未启用，full blocks 不在 prefill 中途可见 | 当前 mode 冻结为 finish-time 语义 | V1 必须补齐 | Step9 通过 `batch_aware_hbm_lru_progressive` 等新 mode 实现 |
| scheduler | vLLM-like continuous batching / chunked prefill 近似 | 离线复刻核心行为，未复制全部 vLLM 内部状态 | 持续校准 | 与真实 vLLM / vLLM-Ascend 行为偏差影响指标时 |
| decode / TPOT | 未建模 decode batch、TPOT、decode KV growth | 当前聚焦 prefill TTFT 和 prefix cache | V2 pending | 明确 PD 混部且有 decode 建模需求时 |
| queue waiting | `queue_waiting_ms = 0` | 当前不做实例外 admission / machine-side queue | V2 新增层 | 需要机器侧排队、tenant fairness、admission control 仿真 |
| KV load latency | Step8 已支持 DDR/CPU hit 的 fitted/static KV load latency；默认 zero mode 兼容旧行为 | 不部署真实存储系统，先做 accounting | 后续增强 | 需要 overlap、load queue/backpressure、promotion 或更细粒度 load 时新增能力 |
| 多级 cache | 已支持单实例 HBM + DDR/CPU hit accounting 和本地 DDR/CPU KV load latency；SSD / remote / cross-instance pooling 未实现 | 先完成本地 tier 语义，再接远端语义 | 继续扩展 | V2 接 remote / cross-instance pooling |
| gateway routing | 不做路由，使用 trace 中 `instance_uuid` | 当前 fixed-routing replay | V2 新增 | trace 无实例 id 且需要路由策略比较时 |
| 无实例 id trace | 核心仿真器要求 routed trace / `instance_uuid`，空值应 fail-fast | 避免把单实例 baseline 误认为 gateway routing | 核心 reader fail-fast；通过外围 normalize-trace 处理 | 用户没有实例 id 且明确不做路由策略仿真时 |
| 异构实例 | streaming path 已支持 per-instance model runtime defaults 和 fitted TTFT backend selection | 仍未实现完整多级 cache / Hybrid cache / gateway / queue 异构 | 后续按核心能力逐步补齐 | 需要单实例池化、KV-load、Hybrid cache group、gateway 或实例侧排队时 |
| 跨实例 KV pooling | 未实现 | 当前实例 cache 隔离 | V2 新增 | Mooncake pooling / remote KV transfer 仿真阶段 |

已完成的外围能力：

- 外围 Batch IL-E：Unrouted Trace Normalizer，将无 `instance_uuid` 的 trace 补成单实例 routed trace；不修改核心 reader。

当前已确认的遗留问题：

- Step7：单实例池化已完成，允许单个实例在 DDR/CPU 侧额外 KV cache 存储中命中。
- Step8：KV load latency 已完成，为非 HBM 命中增加加载时延建模。
- Step9：progressive chunk visibility 是下一阶段；chunk 生成后即可成为后续请求的 cache-hit 候选，TTFT prefill 时间按多个 uncached-token chunk 组合。
- V2：复杂 Hybrid 模型、gateway、实例侧排队、多实例池化跨实例命中、Decode / TPOT 和后续大规模工程优化。

## 5. 记忆管理

### 5.1 全局记忆

全局记忆用于防止 agent 在长期开发中跑偏。

应包含：

- 产品形态。
- 代码开发要求。
- 当前开发阶段。
- 已审批的关键口径。
- 不允许随意改变的边界。

当前全局记忆文件：

```text
docs/global_memory.md
```

### 5.2 Docs 内部记忆

docs 中应保留轻量、可读、可维护的阶段记忆。

应包含：

- 当前文档状态。
- 产品形态。
- 技术路线 + 代码设计。
- 代码开发要求。
- 学习笔记。
- archive 文件夹。

阶段完成后：

- 总结本阶段开发结果。
- 写入 `docs/core_simulator_technical_plan.md` 的开发状态章节和 `docs/global_memory.md`。
- 将阶段性临时文档移动到 archive。
- 更新全局记忆。
- 保持 docs 根目录轻量。

## 6. 建议 Docs 结构

```text
docs/
  agent_development_context.md
  global_memory.md
  development_governance.md
  infertwin_product_design.md
  core_simulator_technical_plan.md
  code_development_requirements.md
  notes/
  reviews/
  archive/
```

说明：

- `agent_development_context.md`：coding agent 最小开发上下文和当前稳定语义入口。
- `global_memory.md`：长期约束和当前阶段。
- `development_governance.md`：开发流程和文档治理。
- `infertwin_product_design.md`：产品形态。
- `core_simulator_technical_plan.md`：核心仿真器技术路线、代码设计和当前开发状态。
- `code_development_requirements.md`：已审批的代码开发要求。
- `notes/`：学习笔记或调研记录。
- `reviews/`：阶段收口和核心仿真器评审。
- `archive/`：阶段完成后的历史材料，包括已归档的 `development_status.md` 和 `development_context_governance/`。
