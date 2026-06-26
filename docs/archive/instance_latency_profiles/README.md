# Instance Latency Profiles Task

归档位置：

```text
docs/archive/instance_latency_profiles/
```

状态：Batch IL-A、Batch IL-B、Batch IL-B2、Batch IL-C、Batch IL-D 已完成；外围 Batch IL-E 已完成并收口。

任务类型：核心仿真器能力设计与外围能力方案。

目标：让 fixed-routing, multi-instance isolated replay 支持按 `instance_uuid` 选择 TTFT backend，使 HitFloor 从 homogeneous multi-instance replay 升级到第一版 heterogeneous latency cluster replay。

归档文档：

- `01_code_plan.md`：InstanceProfile / InstanceLatencyProfile 表设计与代码开发方案。
- `02_il_a_execution.md`：Batch IL-A schema / parser 执行记录。
- `03_il_b_execution.md`：Batch IL-B InstanceLatencyBackendResolver 执行记录。
- `04_il_b2_execution.md`：Batch IL-B2 kv_load schema extension 执行记录。
- `05_il_c_execution.md`：Batch IL-C streaming runner integration 执行记录。
- `06_il_d_acceptance.md`：Batch IL-D 主文档、示例和完整验收收口。
- `07_replay_capability_review_and_il_e_plan.md`：核心仿真器骨架评审，以及外围 Batch IL-E Unrouted Trace Normalizer 方案。
- `08_il_e_execution.md`：外围 Batch IL-E Unrouted Trace Normalizer 执行记录。

当前专项主体能力已完成并归档。外围 Batch IL-E 已实现 Unrouted Trace Normalizer：把无 `instance_uuid` 的 trace 预处理成统一实例 id 的 routed trace。核心仿真器仍要求 routed trace。
