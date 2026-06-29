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
- `s9_c_compute_wait_accounting_implementation_plan.md`：S9-C Compute Wait Accounting 方案与执行记录。
- `s9_d_kv_load_timing_state_implementation_plan.md`：S9-D KV Load Timing State 方案与执行记录。
- `s9_e_kv_transfer_queue_shared_link_v1_implementation_plan.md`：S9-E KV Transfer Queue / Shared Link v1 方案与执行记录。
- `s9_f_chunk_level_ttft_composer_implementation_plan.md`：S9-F Chunk-Level TTFT Composer 方案与执行记录。
- `s9_g_progressive_full_block_materialization_implementation_plan.md`：S9-G Progressive Full-Block Materialization 方案与执行记录。
- `s9_h_streaming_integration_report_fields_implementation_plan.md`：S9-H Streaming Integration / Report Fields 方案与执行记录。
- `s9_i_e2e_execution_record.md`：S9-I E2E 执行记录；不做归档、不做工程收口。

本阶段属于核心仿真器开发，不是外围能力开发。

## 当前执行状态

- S9-A：Route Finalization，已完成。
- S9-B：Timeline Schema / Typed Result，已完成。
- S9-C：Compute Wait Accounting，已完成。
- S9-D：KV Load Timing State，已完成。
- S9-E：KV Transfer Queue / Shared Link v1，已完成。
- S9-F：Chunk-Level TTFT Composer，已完成。
- S9-G：Progressive Full-Block Materialization，已完成。
- S9-H：Streaming Integration / Report Fields，已完成。
- S9-I：E2E，已完成。

说明：S9-I 只完成 E2E 验收，不包含 Step9 review、主文档更新、全局记忆更新、归档或工程收口。
