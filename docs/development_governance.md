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
- Step 3 之后不直接跳到代码实现。
- 代码修改前必须先向用户确认。
- 测试和端到端验证必须成为每阶段交付的一部分。
- 每次进入新阶段或新开发批次前，必须说明本次开发属于核心仿真器还是外围能力。
- 核心仿真器输出 typed result；外围能力只消费 typed result，不重算 replay 语义。
- 如果外围能力需要新语义，应回到核心仿真器设计，新增 replay mode、cache backend、policy、adapter 或 result schema。

具体代码开发要求已写入独立文档，后续如有新增要求，应继续更新该文档。

当前代码开发要求文档：

```text
docs/code_development_requirements.md
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
| materialization timing | 默认 finish-time materialization | 保守、简单、deterministic | 需要修改 | 长 prompt prefill 时间接近 block reuse 间隔时，新增 progressive mode |
| progressive block visibility | 当前未启用，full blocks 不在 prefill 中途可见 | 当前 mode 冻结为 finish-time 语义 | 必须补齐 | Step7 后通过 `batch_aware_hbm_lru_progressive` 等新 mode 实现 |
| scheduler | vLLM-like continuous batching / chunked prefill 近似 | 离线复刻核心行为，未复制全部 vLLM 内部状态 | 持续校准 | 与真实 vLLM / vLLM-Ascend 行为偏差影响指标时 |
| decode / TPOT | 未建模 decode batch、TPOT、decode KV growth | 当前聚焦 prefill TTFT 和 prefix cache | pending | 明确 PD 混部且有 decode 建模需求时 |
| queue waiting | `queue_waiting_ms = 0` | 当前不做实例外 admission / machine-side queue | 需要新增层 | 需要机器侧排队、tenant fairness、admission control 仿真 |
| KV load latency | `kv_load_ms = 0`，只保留 schema knobs | 当前只实现 HBM 命中 | 需要实现 | DDR / SSD / remote KV hit 接入后 |
| 多级 cache | 仅 HBM LRU | 先搭核心 replay 骨架 | 需要实现 | 做 Mooncake、DDR、SSD、remote store 命中时 |
| gateway routing | 不做路由，使用 trace 中 `instance_uuid` | 当前 fixed-routing replay | 后续新增 | trace 无实例 id 且需要路由策略比较时 |
| 无实例 id trace | 核心仿真器仍要求 routed trace / `instance_uuid` | 避免把单实例 baseline 误认为 gateway routing | 不修改核心 reader；通过外围 normalize-trace 处理 | 用户没有实例 id 且明确不做路由策略仿真时 |
| 异构实例 | 当前 true streaming 只支持 per-instance fitted TTFT backend | 先拆 latency profile | 需要扩展 | 需要 per-instance scheduler/cache/deployment/block-size 时 |
| 跨实例 KV pooling | 未实现 | 当前实例 cache 隔离 | 需要新增 | Mooncake pooling / remote KV transfer 仿真阶段 |

已完成的外围能力：

- 外围 Batch IL-E：Unrouted Trace Normalizer，将无 `instance_uuid` 的 trace 补成单实例 routed trace；不修改核心 reader。

当前已确认的遗留问题：

- Progressive block visibility：必须补齐，但应新增 replay/cache mode，不能改变 `batch_aware_hbm_lru` frozen finish-time 语义。
- DDR / remote KV load latency：等待多级 cache hit tokens 接入后实现。
- Decode / TPOT：pending，仅在明确 PD 混部需求时开启。
- 完整 heterogeneous instance cluster replay：需要 per-instance scheduler/cache/deployment 能力。
- gateway routing simulation：未来当 trace 不含实例 id 且需要策略对比时实现。

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

- `global_memory.md`：长期约束和当前阶段。
- `development_governance.md`：开发流程和文档治理。
- `infertwin_product_design.md`：产品形态。
- `core_simulator_technical_plan.md`：核心仿真器技术路线、代码设计和当前开发状态。
- `code_development_requirements.md`：已审批的代码开发要求。
- `notes/`：学习笔记或调研记录。
- `reviews/`：阶段收口和核心仿真器评审。
- `archive/`：阶段完成后的历史材料，包括已归档的 `development_status.md`。
