# Pre-Step7 Model Registry & Instance Model Binding

状态：MR-1、MR-2、MR-3、MR-4、MR-5、MR-6、MR-7 已完成；专项已收口并归档。

任务类型：核心仿真器开发，Step7 前工程优化 / 兜底能力。

目标：在进入新的核心 replay 能力开发前，补齐模型注册表、实例到模型绑定和默认 latency fallback 语义，让后续集群仿真能够通过 `instance_uuid` 稳定解析出：

- model 配置。
- tokenizer / chat template profile。
- 默认 TTFT 超参数。
- 未来可扩展的 KV load 默认超参数。

本专项属于核心仿真器开发，但当前阶段只做工程优化和兜底能力：不改变当前 replay 语义，不实现 gateway routing，不实现新的 cache / scheduler / latency 物理模型。

## 文档索引

- `01_code_plan.md`：详细代码结构、schema、批次开发顺序和工程收口方案。
- `02_execution.md`：执行记录。

## 归档规则

本专项完成后，移动到：

```text
docs/archive/pre_step7_model_registry/
```

归档前必须更新：

- `README.md`
- `docs/global_memory.md`
- `docs/core_simulator_technical_plan.md`
- `docs/hitfloor_product_design.md`
