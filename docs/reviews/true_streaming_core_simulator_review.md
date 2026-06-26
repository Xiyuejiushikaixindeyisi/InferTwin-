# True Streaming Core Simulator Review

评审时间：2026-06-26

评审对象：

- `src/infertwin/streaming/`
- `scripts/benchmark_streaming_replay.py`
- `tests/unit/streaming/`
- `tests/integration/test_true_streaming_capacity_sweep_runner.py`
- `tests/integration/test_benchmark_streaming_replay_script.py`
- `docs/archive/true_streaming/`

本次评审目标：

1. 基于 `ruff` 和测试结果，评估 true streaming 专项后的核心仿真器质量。
2. 从功能完善度、代码结构、测试覆盖、性能、安全边界、可维护性、可扩展性等方面给出审查意见。
3. 明确 true streaming 是否解决进入 Step7 前的大 trace 架构 blocker。
4. 表明未完成方案和下一阶段建议。

## 1. 结论

true streaming 专项已完成，当前骨架可以作为后续大 trace 仿真和 Step7 扩展基础。

本专项补齐了工程优化 review 中的 P2 问题：

```text
request build 仍非 true streaming，大 trace 下内存压力仍需专项处理
```

当前已形成 opt-in streaming path：

```text
CSV trace
-> StreamingRequestShardBuilder
-> per-instance JSONL request shards
-> JsonlRequestSource
-> StreamingBatchAwareReplayEngine
-> CapacitySweepStreamingMetricAggregator
-> StreamingCapacitySweepRunner
-> CapacitySweepResult
-> report/export
```

旧 path 仍保留：

```text
CapacitySweepRunner
-> build accepted SimulationRequest list
-> BatchAwareReplayEngine.run(list[SimulationRequest])
```

这两个 path 并存是合理的：

- 小 trace / debug path 保持简单。
- 大 trace / 11G CSV path 使用 `capacity_sweep_streaming`。
- 旧 `infertwin sweep` 不被静默改语义。
- 新 `infertwin sweep-streaming` 显式 opt-in。

## 2. 功能完善度

已完成：

- streaming request schema / codec。
- per-instance JSONL shard manifest。
- CSV 逐行 request build。
- tokenizer-stage long request rejection sidecar。
- sorted trace guard。
- `RequestSource` abstraction。
- JSONL-backed request source。
- per-instance streaming replay。
- request finish 后释放 active state。
- streaming request / iteration metric sink。
- streaming capacity sweep metric aggregator。
- streaming capacity sweep runner。
- package CLI opt-in：`infertwin sweep-streaming`。
- selected capacity raw cache event dump。
- streaming benchmark harness。

验收重点：

- `StreamingCapacitySweepRunner` 与旧 `CapacitySweepRunner` 在同一 synthetic trace 上输出完全相同的 `CapacitySweepRow`。
- streaming replay 与旧 list replay 在单实例 synthetic trace 上 request / iteration metrics 等价。
- old `capacity_sweep` 不受影响。

结论：

- true streaming v1 功能闭环完整。
- 可以覆盖当前 11G trace、几万条长请求的架构目标，但仍要求 trace 已排序。

## 3. 代码结构

新增模块行数：

| 文件 | 行数 | 评价 |
| --- | ---: | --- |
| `streaming/build.py` | 172 | 清晰，专注 shard build |
| `streaming/manifest.py` | 57 | 简洁 |
| `streaming/request_codec.py` | 196 | schema / codec 边界清楚 |
| `streaming/shard_store.py` | 98 | 清晰 |
| `streaming/source.py` | 160 | 清晰 |
| `streaming/replay.py` | 213 | 复用旧 replay helper，职责可接受 |
| `streaming/metrics.py` | 205 | aggregator 与 sink 放在一起，当前可接受 |
| `streaming/sweep.py` | 220 | streaming orchestration 清晰 |

设计评价：

- `streaming/` 是核心仿真器目录，不是外围 report 能力。
- CSV / Markdown 输出仍在 `report/`，没有进入 streaming replay core。
- `scripts/benchmark_streaming_replay.py` 是外围 benchmark harness，但它压测的是核心 streaming path。
- `experiment/sweep.py` 只暴露 helper，没有把旧 runner 改成 streaming runner。
- `cli/main.py` 新增 opt-in 子命令，没有改变旧 CLI。

结论：

- 模块边界清楚。
- 当前没有需要立刻拆分的超大文件。
- 后续如果支持 parallel instance replay，不应继续扩张 `streaming/sweep.py`，应新增 execution backend 或 parallel runner。

## 4. 测试覆盖

新增测试覆盖：

- schema / manifest validation。
- request codec roundtrip。
- streaming request shard build。
- prompt too long rejection sidecar。
- sorted trace guard。
- list source / JSONL source。
- streaming replay 与旧 replay 等价。
- zero-miss fast finish。
- cache event sink。
- instance mismatch fail-fast。
- streaming metric aggregator 与 `build_capacity_rows()` 等价。
- streaming capacity sweep runner 与旧 runner 等价。
- selected capacity cache event dump。
- package CLI opt-in。
- benchmark smoke。

客观结果：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
135 files already formatted

PYTHONPATH=src .venv/bin/python -m pytest
182 passed

PYTHONPATH=src .venv/bin/python -m pytest --cov=infertwin --cov-report=term-missing
182 passed
TOTAL 3581 statements, 254 missed, 93% coverage

git diff --check
passed
```

streaming 模块覆盖率：

| 模块 | 覆盖率 |
| --- | ---: |
| `streaming/build.py` | 99% |
| `streaming/manifest.py` | 89% |
| `streaming/metrics.py` | 97% |
| `streaming/replay.py` | 92% |
| `streaming/request_codec.py` | 87% |
| `streaming/shard_store.py` | 97% |
| `streaming/source.py` | 97% |
| `streaming/sweep.py` | 92% |

结论：

- 测试覆盖足够支撑后续开发。
- 大规模 benchmark 不进入默认 pytest 是正确选择。

## 5. 性能与大 Trace 安全

已改善：

- request build 不再需要保留全量 accepted `SimulationRequest` list。
- replay 不再需要 per-instance pending list。
- replay 只保留当前 active state、cache metadata、metric accumulator。
- cache event raw dump 默认关闭。
- raw cache event 只对 selected capacity 打开。
- benchmark 能观察 requests/s、iterations/s、cache_events/s、peak traced memory、RSS 和总耗时。

仍需注意：

- JSONL shard 仍保存 prefix block hash chain；长 prompt 下磁盘体积会变大。
- tokenizer 本身仍是单请求粒度 tokenization，不是 token-level streaming。
- exact percentile 仍保存 TTFT list；几万请求可接受，百万级 request 需要 quantile policy。
- 第一版要求 trace sorted；无序 11G trace 需要 external sort 或 shard merge 设计。
- 多实例 replay 当前仍串行。

结论：

- true streaming 已解决进入 Step7 前的大 trace 内存架构 blocker。
- 下一步性能优化不应阻塞 Step7，但后续应专项处理 parallel replay、approximate quantile、compressed/binary shard 或 external sort。

## 6. 与真实 vLLM / vLLM-Ascend 差异

true streaming 不改变 replay 语义。它只改变 request build / replay 输入方式：

```text
内存 list
-> 磁盘 shard + streaming source
```

因此仍保留当前核心差异：

- 默认 finish-time materialization，尚未实现 progressive block visibility。
- prefill-only，不建模 Decode / TPOT。
- cache 只保存 hash key 和 metadata，不保存真实 KV tensor。
- HBM-only，不建模 DDR / SSD / remote KV load latency。
- 多实例隔离，不做 gateway routing 或 cross-instance pooling。

其中 progressive block visibility 仍是 Step7 后应优先补齐的核心能力。

## 7. 未完成方案

未完成但不阻塞当前收口：

- progressive block visibility。
- parallel instance replay。
- external sort / unsorted trace spooling。
- approximate percentile / quantile sketch。
- binary or compressed shard codec。
- shard manifest persistence / resumable replay。
- multi-tier cache backend。
- KV load latency。
- Decode / TPOT。

这些应作为后续独立核心能力或外围能力处理，不能在现有字段上静默改语义。

## 8. 进入下一阶段建议

当前可以进入 Step7 或下一个明确阶段。

建议优先级：

1. 如果 Step7 继续核心仿真器能力，优先设计 progressive block visibility，新建 replay/cache mode。
2. 如果 Step7 是外围能力，应明确只消费 typed result，不修改 streaming/replay core。
3. 如果要进一步强化大 trace 生产能力，优先做 external sort 或 parallel instance replay，但必须保持 deterministic output。

进入下一阶段时仍必须先声明：

```text
本阶段开发的是核心仿真器，还是外围能力。
```
