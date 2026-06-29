# Step9 Core Simulator Review

日期：2026-06-29

范围：Step9 Chunk Timeline / Compute Wait / KV Load Timing / Progressive Full-Block Materialization，覆盖 S9-A 到 S9-I 的核心仿真器改动与当前测试结果。

结论：Step9 的核心开发目标已完成，建议进入工程收口。当前实现通过新 replay/cache mode `batch_aware_hbm_ddr_lru_progressive_timeline` 承载 chunk-level TTFT、compute wait、KV load wait、shared-link FIFO accounting 和 progressive full-block visibility；旧 `batch_aware_hbm_lru` / `batch_aware_hbm_ddr_lru` 仍保持 legacy iteration / finish-time 语义。Review 未发现必须立即修改业务代码的阻塞 bug，但存在若干明确遗留问题和一个文档状态一致性问题，应在工程收口或后续阶段处理。

## 1. Review Scope

本次 review 只评估 Step9 相关核心仿真器能力：

- Step9 技术路线与 vLLM / vLLM-Ascend / Mooncake 对齐分析。
- replay timeline schema 和 typed metrics。
- compute wait accounting。
- KV load timing state。
- shared-link FIFO KV transfer accounting。
- chunk-level TTFT composer。
- progressive full-block materialization。
- streaming `sweep-streaming` 主路径接入。
- report/export 对 Step9 typed result 的消费。
- Step9 targeted tests、CLI E2E、全量 pytest 和 ruff 结果。

本次 review 不新增功能，不修改业务代码，不做 Step9 归档或主文档收口。

## 2. Step9 实际完成内容

Step9 已完成以下内容：

1. 技术路线收口。
   - `docs/archive/step9/02_technical_route.md` 已成为正式路线。
   - 明确 Step9 是核心仿真器 L3 改动。
   - 明确旧 mode 不变，新 mode 承载 progressive timeline。

2. Timeline schema / typed result。
   - 新增 `src/infertwin/replay/timeline.py`。
   - 新增 `RequestTimelineState`、`ChunkTimelineEntry`、`KVLoadTimelineEntry`、`RequestTimelineSummary`。
   - `BatchAwareRequestMetrics` 和 `IterationMetrics` 增加 timeline fields。

3. Compute wait accounting。
   - `RequestState` 增加 `compute_wait_ms` 与 timeline state。
   - progressive mode 下，已到达但本轮未被 scheduler 选中的 active request 累计 compute wait。
   - legacy mode 下 compute wait 保持 0。

4. KV load timing state。
   - progressive mode 下，DDR hit request 记录 `kv_load_wait_ms` 和 `load_event_count`。
   - HBM-only zero-miss 继续 immediate finish。
   - DDR-only zero-miss 不再无代价 finish，而是产生 load-only iteration。

5. Shared-link FIFO v1。
   - 新增 `src/infertwin/replay/kv_transfer.py`。
   - 每个 instance 独立维护 `SharedLinkFIFOTransferQueue`。
   - 输出 `kv_transfer_queue_depth_max`，并将 queue wait + service time 计入 `kv_load_wait_ms`。
   - 该队列是 deterministic accounting abstraction，不是真实 Mooncake TransferEngine。

6. Chunk-level TTFT composer。
   - 新增 `src/infertwin/replay/ttft.py`。
   - progressive mode 下 request TTFT 由以下字段闭合：

```text
ttft_ms
  = compute_wait_ms
  + kv_load_wait_ms
  + uncached_prefill_compute_ms
  + unattributed_ttft_ms
```

   - `unattributed_ttft_ms` 是 replay 粒度残差，不是物理建模结果。
   - negative residual fail-fast。

7. Progressive full-block materialization。
   - 新增 `ProgressiveFullBlockMaterializationPolicy`。
   - progressive mode 下 scheduled chunk finish 后 materialize newly completed full miss blocks。
   - partial block 仍不可见。
   - HBM event reason 使用 `progressive_chunk_materialization`。
   - DDR store event reason 使用 `progressive_chunk_store`。
   - legacy mode 仍使用 finish-time materialization。

8. Streaming integration / report fields。
   - streaming cache mode 新增 `batch_aware_hbm_ddr_lru_progressive_timeline`。
   - `StreamingCapacitySweepRunner` 将该 mode 映射为 progressive timeline replay。
   - `CapacitySweepRow`、streaming aggregator 和 summary report 输出 Step9 timeline aggregate fields。
   - report/export 只消费 typed result，不重算 replay 语义。

9. E2E 验收。
   - 新增 CLI 级 synthetic E2E：`tests/integration/test_step9_streaming_cli_e2e.py`。
   - 覆盖 routed CSV、model registry、instance runtime、progressive mode、HBM+DDR tier、DDR hit、KV load wait、cache events、capacity_sweep.csv 和 summary.md。

## 3. Step9 没有完成什么

Step9 按技术路线没有完成以下能力：

- 不支持 legacy `simulate` 和 non-streaming `sweep` 的 progressive mode。
- 不输出 per-chunk timeline 明细文件；当前只输出 request / iteration / sweep aggregate。
- 不实现 DDR hit promotion 到 HBM。
- 不建模 physical KV slot、refcount、pin、fragmentation。
- 不实现 partial-block prefix hit。
- 不实现真实 async load completion event。
- 不接 Ramulator2 / Mooncake online replay。
- 不模拟真实 Mooncake TransferEngine 的 protocol、thread、priority、replica placement 或 retry。
- 不建模 same-request layerwise compute/load overlap。
- 不建模 gateway routing、instance admission queue、Decode / TPOT、cross-instance pooling、SSD tier 或复杂 Hybrid cache group。
- 不实现千万级 trace 的 approximate percentile accumulator。

这些是明确边界或后续阶段任务，不应在当前 Step9 中写成已完成能力。

## 4. 核心链路影响评审

| 核心链路 | Step9 影响 | Review 结论 |
| --- | --- | --- |
| trace schema guard | 不改变 trace schema；核心 reader 仍应拒绝空 `instance_uuid` | 安全 |
| request build | 不改变 request build；不预生成 chunk timeline | 安全 |
| tokenizer / chat template | 不改变 tokenizer、chat template 和长请求拒绝策略 | 安全 |
| prefix block hash | 不改变 hash-only block chain；只改变 progressive mode 下 full block 可见时间 | 安全 |
| scheduler replay | progressive mode 增加 compute wait、KV load wait、chunk counters、transfer queue accounting | Step9 核心改动，符合路线 |
| cache lookup | 不改变 vLLM-like cached token accounting；progressive mode 后续 lookup 可能看到更早 materialized 的 full blocks | 符合路线 |
| materialization | legacy mode finish-time 不变；progressive mode chunk finish 后 materialize full blocks | Step9 核心改动 |
| eviction | 不换 LRU policy；progressive mode 因 materialization 更早，eviction 也可能更早发生 | 预期行为 |
| latency backend | 不新增真实外部 simulator；通过 composer 和 typed fields 表达 compute/load/wait 组成 | 符合路线 |
| per-instance isolation | 每个 instance 独立 scheduler、cache、transfer queue、latency backend 和 metric aggregation | 通过测试覆盖 |
| typed metrics / typed result | 新增 timeline fields；capacity sweep/report 消费 typed result | 安全 |

## 5. 是否改变核心 replay 语义

Step9 改变核心 replay 语义，但只在新 mode 中改变。

旧 mode：

- `batch_aware_hbm_lru`：保持 legacy iteration / finish-time materialization。
- `batch_aware_hbm_ddr_lru`：保持 Step8 legacy iteration / finish-time materialization。
- old-mode golden 未因 Step9 更新。

新 mode：

```text
batch_aware_hbm_ddr_lru_progressive_timeline
```

启用以下新语义：

- request-level `compute_wait_ms`。
- request-level `kv_load_wait_ms`。
- `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms`。
- request-level `chunk_count` / `load_event_count`。
- instance-local `shared_link_fifo_v1` accounting。
- full miss block 在 scheduled chunk finish 后可见。
- cache event timestamp / reason 反映 progressive chunk materialization。
- `ttft_granularity=chunk`。

字段影响：

- `cached_tokens` accounting 规则未改变。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens` 在 progressive mode 下可能因更早可见的 full blocks 改变；这是 Step9 目标行为。
- `finish_time_ms` / `ttft_ms` 在 progressive mode 下可因 chunk visibility、KV load wait 和 compute wait 组合变化；旧 mode 不变。
- cache event 顺序在 progressive mode 下会产生更早的 materialize/store 事件；旧 mode 不变。
- materialization timing 只在 progressive mode 下改变。
- 实例隔离保持不变。
- true streaming 主路径未退化为全量 request list。

## 6. 与技术路线一致性

Step9 实现与 `docs/archive/step9/02_technical_route.md` 总体一致：

- 已新增 explicit compute wait。
- 已新增 KV load wait。
- 已新增 shared-link FIFO v1。
- 已新增 chunk-level TTFT composer。
- 已新增 progressive full-block materialization。
- 已通过新 mode 启用，不静默修改旧 mode。
- 已接入 streaming 主路径和 typed aggregate report。

存在一个需要在收口时澄清的表达边界：

- `SharedLinkFIFOTransferQueue` 当前是 KV load wait accounting abstraction；它输出 queue wait / elapsed wait，但不模拟真实 TransferEngine，也不产生真实 load completion event 或协议级 backpressure。当前 replay clock 仍以 latency backend 返回的 iteration duration 前进，`unattributed_ttft_ms` 用于吸收 replay 粒度残差。这一点符合“V1 不做真实 Mooncake/Ramulator2 online replay”的边界，但后续如果要研究真实带宽竞争，需要新增更严格的 transfer timeline/backend，而不是在 report 层修正。

## 7. 测试结果

本次 review 期间已重新运行：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m ruff check src tests

All checks passed!
```

Step9 targeted tests：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest \
  tests/unit/replay/test_timeline_schema.py \
  tests/unit/replay/test_compute_wait_accounting.py \
  tests/unit/replay/test_kv_load_timing_state.py \
  tests/unit/replay/test_kv_transfer_queue.py \
  tests/unit/replay/test_kv_transfer_queue_replay.py \
  tests/unit/replay/test_chunk_level_ttft_composer.py \
  tests/unit/replay/test_progressive_full_block_materialization.py \
  tests/integration/test_step9_streaming_cli_e2e.py \
  tests/integration/test_step9_streaming_progressive_timeline_e2e.py

51 passed in 0.17s
```

全量测试：

```text
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src \
  .venv/bin/python -m pytest

439 passed in 17.43s
```

未运行：

- 未运行真实 Ramulator2 / Mooncake / AIConfigurator 校准，因为 Step9 v1 不接 online replay。
- 未运行 11G 级真实公司 trace，因为本次 review 目标是核心语义和合成 E2E，不是大 trace benchmark。

## 8. 代码质量评审

功能完善度：

- Step9 v1 的核心目标已完成：progressive mode 可以表达 chunk-level TTFT、compute wait、KV load wait、DDR tier hit、progressive full-block visibility 和 streaming capacity sweep aggregate。
- E2E 已覆盖多实例隔离、DDR hit、KV load wait、cache events 和 report fields。

代码结构：

- timeline schema 位于 `replay/timeline.py`，是纯数据结构。
- transfer queue 位于 `replay/kv_transfer.py`，没有污染 cache backend。
- TTFT composition 位于 `replay/ttft.py`，没有散落到 report/export。
- materialization policy 位于 `cache/materialization.py`，legacy 和 progressive policy 分离。
- streaming runner 只把 cache mode 映射到 timeline mode，不在 report 层改变 replay 语义。

schema 稳定性：

- 新增字段默认值保持 legacy 兼容。
- progressive mode 使用显式 `timeline_mode` / `ttft_granularity`。
- aggregator 对 mixed timeline/granularity fail-fast，避免 report 混合口径。

可测试性：

- 核心状态、policy、queue、composer 都有独立单测。
- list replay 与 streaming replay parity 有覆盖。
- old-mode regression 有覆盖。
- CLI 级 E2E 覆盖主路径。

可维护性：

- 新能力通过新 mode 接入，避免隐式改变旧结果。
- report/export 只消费 typed result，符合核心仿真器与外围能力分离原则。
- 后续若接更真实 transfer backend，可以新增 queue/backend/policy，不需要重写 cache lookup。

性能风险：

- cache 仍保存 hash 和 metadata，不保存真实 KV tensor，主内存风险可控。
- progressive materialization 只在 full block boundary 产生 cache events，不默认输出 per-chunk 明细。
- streaming 主路径仍按 shard streaming replay，不构造全量 request list。
- capacity sweep percentile 仍保存 request-level lists；公司 V1 几万请求规模可接受，千万级 trace 应新增 approximate percentile accumulator。
- `progressive_materialized_block_keys` 是 active request 局部集合，超长 active request 会增加少量内存，但完成后会释放。

## 9. 是否存在外围能力污染核心仿真器

未发现外围能力污染核心 replay 的问题。

理由：

- `report/sweep.py` 只渲染 `CapacitySweepRow`，没有重算 hit、wait、TTFT 或 materialization。
- `streaming/metrics.py` 只聚合 typed request / iteration metrics，并维护 token invariant。
- `streaming/cache_factory.py` 只做 mode -> cache/timeline 映射，不把 report 配置反向注入 replay 逻辑。
- `sweep-streaming` 是外围运行入口，但核心 replay 仍由 `StreamingBatchAwareReplayEngine` 和 cache/latency/scheduler 模块负责。

## 10. Review 发现的问题

### P2. S9-G 文档顶部状态与执行记录不一致

证据：

- review 时 S9-G implementation plan 的归档前文件顶部仍写“状态：待审批，未开发”。
- 同一文件第 13 节执行记录写“状态：已开发，待用户 review”，且 README 已标记 S9-G 完成。

影响：

- 不影响业务代码或 replay 正确性。
- 会影响后续 agent / 同事快速判断 Step9 文档状态。

建议：

- 已在 Step9 工程收口时修正，并归档到 `docs/archive/step9/`。

### P2. Shared-link FIFO 仍是 accounting abstraction，不是真实 transfer completion / backpressure

证据：

- `SharedLinkFIFOTransferQueue` 维护 FIFO queue depth 和 elapsed wait，但不模拟真实 Mooncake protocol、thread、priority、replica placement 或 retry。
- replay 仍不产生真实 load completion event。

影响：

- 当前字段可用于解释 V1 KV load wait 和队列趋势。
- 对真实高并发 DDR/Mooncake transfer 的精度仍有限。

建议：

- 保持当前为 V1 边界。
- 后续如果要做真实链路竞争，应新增 `KVTransferTimelineBackend` 或更细粒度 transfer policy，并引入 load completion event，而不是在 report 中补算。

### P3. Capacity sweep percentile 仍是 in-memory list

证据：

- streaming aggregator 为 P50/P90/P99 保留 request-level values。

影响：

- 对 V1 几万请求规模可接受。
- 对千万级 trace 或更多 sweep capacities 时会增加内存压力。

建议：

- 后续大规模工程优化中新增 approximate percentile accumulator 或分片 percentile merge。

## 11. 遗留问题

Step9 后仍应保留以下遗留问题：

- per-chunk timeline dump 未实现；当前只输出 aggregate。
- KV transfer queue 不是真实 Mooncake / HCCL / RDMA / DMA 仿真。
- load completion event 未实现。
- DDR hit promotion 未实现。
- physical KV slot / refcount / pin / fragmentation 未实现。
- partial-block prefix hit 未实现。
- layer/page/request 内更细粒度 KV load split 未实现。
- same-request compute/load overlap 未实现。
- Decode / TPOT 未建模。
- gateway routing、instance admission queue、多实例池化跨实例命中、SSD tier、复杂 Hybrid cache group 未实现。
- legacy `simulate` / non-streaming `sweep` 暂不支持 progressive mode。
- 11G 真实 trace benchmark 和 approximate percentile 仍需后续专项。

## 12. 是否建议进入工程收口

建议进入 Step9 工程收口。

判断依据：

- Step9 技术路线中的核心能力已按新 mode 实现。
- 旧 mode 兼容边界清晰，测试覆盖旧 mode regression。
- streaming 主路径已经通过 CLI E2E。
- full pytest 通过：439 passed。
- ruff check 通过。
- report/export 未污染核心 replay。
- 遗留问题均可作为后续阶段或工程优化处理，不阻塞 Step9 收口。

工程收口建议动作：

- 更新主文档和全局记忆，明确 Step9 已完成 progressive timeline mode。
- 修正 S9-G 文档顶部状态。
- 将 Step9 遗留问题同步到主技术路线“后续阶段 / 仍未实现”部分。
- Step9 活跃文档已按用户要求归档到 `docs/archive/step9/`。
- 再次运行 `ruff check src tests`、全量 `pytest` 和 `git diff --check`。
