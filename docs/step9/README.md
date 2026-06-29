# Step9 文档索引

状态：索引文档。

注意：早期 `README.md` 中的 Step9 技术路线已作废。正式技术路线以以下文件为准：

```text
docs/step9/02_technical_route.md
```

当前 Step9 文档：

- `01_source_alignment_and_error_analysis.md`：vLLM / vLLM-Ascend / Mooncake 源码对齐与误差分析。
- `02_technical_route.md`：Step9 正式技术路线，包含 chunk-level TTFT、compute wait state、KV load timing、KV transfer queue 和 progressive full-block visibility。
- `s9_a_route_finalization_implementation_plan.md`：S9-A Route Finalization 方案与执行记录。
- `s9_b_timeline_schema_typed_result_implementation_plan.md`：S9-B Timeline Schema / Typed Result 方案与执行记录。

本阶段属于核心仿真器开发，不是外围能力开发。

## 当前执行状态

- S9-A：Route Finalization，已完成。
- S9-B：Timeline Schema / Typed Result，已完成。
- 下一步：进入 S9-C：Compute Wait Accounting 代码编写方案设计。
