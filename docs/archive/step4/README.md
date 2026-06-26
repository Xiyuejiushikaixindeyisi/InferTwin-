# Step4 Workspace

Step4 目标是把 HitFloor 从“无限 HBM prefix cache 命中统计”推进到“可生成 batch-aware TTFT 输入，并用 latency backend 物化请求完成时间”的 replay。

本目录是 Step4 的归档工作区，保存本阶段的产品形态、学习笔记、技术路线、代码编写方案和评审清单。Step4 的历史开发状态已随 `docs/archive/development_status.md` 归档；当前状态以 `docs/global_memory.md` 和 `docs/core_simulator_technical_plan.md` 为准。

## 当前状态

- Step1-Step3 已完成：实例内、无限 HBM、hash-only prefix cache 命中计算。
- Step4 未进入代码开发：当前阶段只做技术路线和代码规划。
- 关键缺口：现有 replay 没有 vLLM/vLLM-Ascend 风格的组 batch 过程，因此无法向 AIConfigurator 或 Markov-Infer-sim 提供可信的 batch size / batch shape。

## 本目录文件

- `01_product_shape.md`：Step4 的产品边界、输入输出、显式不做什么。
- `02_vllm_batching_study.md`：本地 vLLM / vLLM-Ascend scheduler 学习摘要。
- `03_latency_simulator_selection.md`：AIConfigurator 与 Markov-Infer-sim 的选择标准、接口问题清单。
- `04_technical_route_and_code_plan.md`：Step4 详细技术路线、算法、模块结构、测试计划。
- `05_review_checklist.md`：进入代码开发前和完成代码开发后的评审清单。
- `06_code_development_discussion.md`：代码开发批次、改动文件、测试验收和最小闭环建议。
