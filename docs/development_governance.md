# HitFloor 开发治理与文档组织

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

## 3. 记忆管理

### 3.1 全局记忆

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

### 3.2 Docs 内部记忆

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

## 4. 建议 Docs 结构

```text
docs/
  global_memory.md
  development_governance.md
  hitfloor_product_design.md
  core_simulator_technical_plan.md
  code_development_requirements.md
  notes/
  archive/
```

说明：

- `global_memory.md`：长期约束和当前阶段。
- `development_governance.md`：开发流程和文档治理。
- `hitfloor_product_design.md`：产品形态。
- `core_simulator_technical_plan.md`：核心仿真器技术路线、代码设计和当前开发状态。
- `code_development_requirements.md`：已审批的代码开发要求。
- `notes/`：学习笔记或调研记录。
- `archive/`：阶段完成后的历史材料，包括已归档的 `development_status.md`。
