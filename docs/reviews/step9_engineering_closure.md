# Step9 Engineering Closure

日期：2026-06-29

任务类型：核心仿真器工程收口。

本轮不新增功能，不修改业务代码。收口只覆盖文档、记忆、review、归档和一致性检查。

## 1. Step9 完成内容

Step9 已完成 progressive chunk timeline 能力，并通过新 mode 承载，不修改 legacy mode：

```text
batch_aware_hbm_ddr_lru_progressive_timeline
```

核心完成项：

- 技术路线：`docs/archive/step9/02_technical_route.md`。
- 源码对齐与误差分析：`docs/archive/step9/01_source_alignment_and_error_analysis.md`。
- Timeline schema / typed result。
- Compute wait accounting。
- KV load timing state。
- Instance-local deterministic `SharedLinkFIFOTransferQueue`。
- Chunk-level TTFT composer。
- Progressive full-block materialization。
- Streaming `sweep-streaming` integration。
- Capacity sweep / summary report typed fields。
- CLI 级 synthetic E2E。

Step9 稳定 replay 语义：

- legacy `batch_aware_hbm_lru` / `batch_aware_hbm_ddr_lru` 仍保持 finish-time materialization。
- progressive mode 下 scheduled chunk finish 后 newly completed full miss blocks 可见。
- partial block 仍不可见。
- progressive mode 下 `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms`。
- progressive request TTFT 由以下字段闭合：

```text
ttft_ms
  = compute_wait_ms
  + kv_load_wait_ms
  + uncached_prefill_compute_ms
  + unattributed_ttft_ms
```

- `unattributed_ttft_ms` 是 replay 粒度残差，不是物理建模结果。
- `SharedLinkFIFOTransferQueue` 是 deterministic accounting abstraction，不是真实 Mooncake / TransferEngine。

## 2. 验收结果

Step9 review 期间已重新验证：

```text
ruff check src tests: passed
Step9 targeted tests: 51 passed
full pytest: 439 passed
git diff --check: passed
```

Step9 E2E 覆盖：

- package CLI `sweep-streaming`。
- routed CSV trace。
- model registry / instance runtime / instance latency。
- `batch_aware_hbm_ddr_lru_progressive_timeline`。
- HBM + DDR tiered cache。
- progressive materialization events。
- DDR lookup hit events。
- KV load wait metrics。
- per-instance isolation。
- `capacity_sweep.csv` Step9 timeline fields。
- `summary.md` Timeline Results。

## 3. 遗留问题

Step9 后仍未实现的核心能力：

- 真实 Mooncake / HCCL / RDMA / DMA transfer backpressure、priority 和 completion event。
- DDR hit promotion 到 HBM。
- physical KV slot / refcount / pin / fragmentation。
- partial-block prefix hit。
- layer / page / chunk 级 KV load split。
- same-request layerwise compute/load overlap。
- per-chunk timeline 明细 dump。
- Decode / TPOT。
- gateway routing。
- instance admission queue。
- remote / SSD tier。
- cross-instance pooling。
- complex Hybrid cache group。

外围或工程能力遗留：

- target-based hit floor solver / P90 target matching。
- GB / GiB 到 block 数转换工具。
- Deployment script -> profile config。
- legacy in-memory `capacity_sweep` 的大 trace 风险。
- exact percentile in-memory list。
- external sort / shard sort。
- 多实例并行 replay。
- shard / event 文件体积控制。

## 4. 是否具备进入 StepY 的条件

结论：具备进入 StepY 产品形态和技术路线讨论的条件。

判断依据：

- Step9 核心能力已经通过新 mode 落地，不破坏 legacy mode。
- `batch_aware_hbm_ddr_lru_progressive_timeline` 已通过 CLI E2E。
- typed result 已能表达 compute wait、KV load wait、chunk count、load event count 和 progressive materialized tokens。
- report/export 只消费 typed result，没有反向修改 replay 语义。
- true streaming 主路径未退化为全量 request list。
- full pytest、ruff 和 diff check 均通过。

StepY 注意事项：

- StepY 范围尚未定义，不应默认进入 V2 或外围能力开发。
- 如果 StepY 要改变 replay 语义，必须新增 mode、backend、policy、adapter 或 schema。
- 不允许在 legacy mode 或 Step9 progressive mode 上静默改变字段含义。
- 如果 StepY 是外围能力，只能消费 Step9 后的 typed result。

## 5. 风险与风险控制

风险：

- `SharedLinkFIFOTransferQueue` 可能被误解为真实 Mooncake transfer simulation。
- `unattributed_ttft_ms` 可能被误解为物理串行时间。
- progressive mode 的更早 materialization 会改变 hit/miss 和 eviction timing，这是预期行为，但只应发生在新 mode。
- 大 trace 下 exact percentile 和 shard/event 文件体积仍有工程压力。

风险控制：

- 主文档明确 shared-link FIFO 是 accounting abstraction。
- 主文档明确 `unattributed_ttft_ms` 是 replay 粒度残差。
- 保留 legacy mode regression。
- 大 trace 默认继续走 `sweep-streaming`。
- per-chunk 明细和更真实 transfer backend 后续必须 opt-in。

## 6. 收口变更

本次收口更新：

- `docs/core_simulator_technical_plan.md`
- `docs/global_memory.md`
- `docs/agent_development_context.md`
- `docs/infertwin_product_design.md`
- `docs/archive/step9/s9_g_progressive_full_block_materialization_implementation_plan.md`
- `docs/reviews/step9_engineering_closure.md`
- `docs/archive/step9/`

本次收口不修改：

- `src/**`
- `tests/**`
- `configs/**`
- `scripts/**`
