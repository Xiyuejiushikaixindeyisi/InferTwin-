# InferTwin Step7：单实例 KV Pooling / DDR-CPU Cache Backend

状态：S7-A 到 S7-G 已完成。本目录已归档。

本目录用于承接 Step7 的学习笔记、技术路线和代码结构方案。Step7 完成后，本目录应移动到：

```text
docs/archive/step7/
```

## 阶段定位

Step7 是核心仿真器开发，不是外围能力开发。

Step7 的主题是：在 fixed-routing、multi-instance isolated replay 基础上，为单个实例增加 HBM 外的 DDR/CPU KV cache tier，使请求可以在同一实例内产生 HBM hit、DDR hit 和 miss。

这不是简单增加一个 cache 存储空间。Step7 必须为后续能力打基础：

- 多级 cache backend。
- KV load latency。
- cache tier event。
- tier-aware hit accounting。
- 更精细的 cache 管理策略。
- sparse / hybrid model cache。
- 未来 block 级 TTFT 仿真。

## 本阶段明确不做

- 不做跨实例 KV hit。
- 不做 gateway routing。
- 不做实例侧真实排队。
- 不做 Decode / TPOT。
- 不接 Ramulator2，KV load latency 仍在 Step8 接入。
- 不做 progressive chunk visibility，仍在 Step9 通过新 replay/cache mode 实现。
- 不做复杂 Hybrid 模型完整 cache group 语义，保留为 V2。

## 文档

```text
docs/archive/step7/01_vllm_vllm_ascend_multilevel_cache_study.md
docs/archive/step7/02_mooncake_store_study.md
docs/archive/step7/03_step7_technical_route.md
docs/archive/step7/04_gap_analysis_and_refined_batches.md
docs/archive/step7/05_s7_a_config_schema_guard_plan.md
docs/archive/step7/06_s7_b_cache_event_tier_schema_plan.md
docs/archive/step7/07_s7_c_ddr_lru_tier_plan.md
docs/archive/step7/08_s7_d_tiered_prefix_cache_plan.md
docs/archive/step7/09_s7_e_streaming_runner_integration_plan.md
docs/archive/step7/10_s7_f_report_metrics_e2e_plan.md
docs/archive/step7/11_s7_g_review_docs_archive_plan.md
```

其中：

- `01` / `02` 是 vLLM、vLLM-Ascend、Mooncake Store 调研。
- `03` 是 Step7 初版技术路线。
- `04` 是初审后的差异对比、修改意见和细化 Batch 顺序；后续开发顺序以 `04` 为准。
- `05` 是 S7-A Config / Schema Guard 的代码开发方案和执行记录。
- `06` 是 S7-B CacheEvent Tier Schema 的代码开发方案和执行记录。
- `07` 是 S7-C DDR LRU Tier 的代码开发方案和执行记录。
- `08` 是 S7-D TieredPrefixCache 的代码开发方案和执行记录。
- `09` 是 S7-E Streaming Runner Integration 的代码开发方案和执行记录。
- `10` 是 S7-F Report / Metrics / E2E 的代码开发方案和执行记录。
- `11` 是 S7-G Review / Docs / Archive 的代码开发方案和执行记录。

## 已确认设计决策

- Step7 v1 使用 finish-time materialization，同时写 HBM 和 DDR。
- DDR hit 不自动 promote 到 HBM；promotion 放到 Step8 之后。
- Step7 只做 tier hit accounting，`kv_load_ms = 0`；Step8 接 KV load latency。
- Step7 扩展现有 `CacheEvent`，并更新 CSV writer / golden / tests。
- 大 trace 主路径优先接 `sweep-streaming`；legacy `simulate` / non-streaming `sweep` 可保持 HBM-only 或后续再接。

## 评审门禁

进入 Step7 代码开发前，需要确认：

- 单实例池化的产品范围。
- DDR/CPU tier 的 hit / store / evict / materialize 语义。
- HBM 与 DDR 的关系：互斥 resident、复制 resident，还是 HBM load target。
- 是否在 Step7 记录 `kv_load_tokens` 但保持 `kv_load_ms = 0`。
- cache event schema 是否允许新增字段，或是否新增 v2 event schema。
- capacity sweep 是否继续 sweep HBM capacity，DDR capacity 从 model default runtime 读取。
