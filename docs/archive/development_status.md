# HitFloor 开发状态

归档说明：本文件已归档，仅保留 Step1-Step6 完成后的历史状态快照。当前状态以 `docs/global_memory.md` 和 `docs/core_simulator_technical_plan.md` 为准。

## 当前阶段

Step1-Step6 已完成，当前暂不进入 Step7，先进入核心仿真器工程优化阶段。

工程优化目标：

- 让仿真器更加贴近真实基于 vLLM 的大模型服务推理过程。
- 保证仿真器在大 trace 下稳定运行。
- 保证 scheduler、cache、latency、event、report 等模块有序工作。
- 保证多实例 replay 互不影响。
- 明确每项改动属于核心仿真器还是外围能力。

主技术路线文档：

```text
docs/core_simulator_technical_plan.md
```

产品设计文档：

```text
docs/hitfloor_product_design.md
```

## 已完成能力

核心仿真器已完成：

- trace reader。
- strict request parser。
- tokenizer / chat template registry。
- GLM-5 tokenizer profile。
- hash-only prefix block。
- fixed-routing, multi-instance isolated replay。
- vLLM-like batch-aware replay。
- chunked prefill。
- fitted TTFT backend。
- infinite HBM prefix cache。
- finite HBM LRU cache。
- stateful eviction policy。
- event sinks。
- HBM capacity sweep runner。

外围能力已完成：

- single replay CSV / summary。
- HBM Cache Capacity Sweep Report。
- `capacity_sweep.csv`。
- `summary.md`。
- package CLI。
- scripts wrapper。

## 最近验收

Step6 功能验收文档：

```text
docs/archive/step6/03_acceptance_e2e.md
```

验收结果：

| hbm_capacity_blocks | kv_hit_rate | p90_ttft_ms |
| ---: | ---: | ---: |
| 3 | 0.000000 | 15.0 |
| 4 | 0.876667 | 11.0 |
| 8 | 0.913333 | 0.0 |

## 当前验证基线

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 115 passed
python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml: passed
python scripts/benchmark_replay.py --requests 10000 --instances 4: passed
python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml: passed
```

## 当前遗留问题

工程优化阶段重点处理或设计：

- request build 一次性构造全部 `SimulationRequest`。
- 多实例 replay 串行执行。
- cache event 大文件控制。
- finish-time materialization 与真实 vLLM progressive block visibility 的差异。
- prefill-only replay 与真实 decode / TPOT 的差异。
- 简单 fitted TTFT 公式与真实服务 latency profile 的差异。

仍未实现的核心仿真器能力：

- multi-tier cache backend。
- KV load latency。
- instance queue simulation。
- gateway simulation。
- heterogeneous instance cluster simulation。
- sparse-attention-aware cache management。
- Mooncake-style cross-instance pooling。

仍未实现的外围能力：

- P90 target matching。
- hit floor search。
- dashboard / Web UI。

## 归档索引

```text
docs/archive/implementation_plan.md
docs/archive/future_simulation_extensions.md
docs/archive/pre_step6_cleanup_plan.md
docs/archive/step4/
docs/archive/step5/
docs/archive/step6/
```
