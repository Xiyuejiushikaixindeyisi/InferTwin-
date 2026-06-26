# HitFloor 全局记忆

## 项目定位

HitFloor 是面向 TOB 大型推理服务集群的离线仿真器。

当前已完成固定 trace、固定实例路由、固定 cache/latency 配置下的 HBM capacity sweep，能够按不同 `hbm_capacity_blocks` 输出 cache 容量、KV cache hit rate 与 P90 TTFT 的关系表。target-based hit floor solver / P90 target matching 属于外围能力，不是核心仿真器本身。

Step1-Step6 已完成核心仿真骨架。后续 gateway、实例侧排队、chunk 调度、淘汰算法、多级缓存、稀疏注意力 cache 管理和 Mooncake 多实例池化，都应作为独立仿真层、策略类、adapter 或 cache backend 接入。

核心仿真器主技术路线记录在：

```text
docs/core_simulator_technical_plan.md
```

## 核心仿真器与外围能力边界

HitFloor 必须区分核心仿真器和外围能力。

核心仿真器负责：

- trace 到 `SimulationRequest` 的构造。
- tokenizer / chat template 选择。
- prefix block hash。
- scheduler replay。
- cache lookup、materialization、eviction 和 event stats。
- latency backend 调用。
- deterministic request / iteration / sweep metrics。

外围能力负责消费核心仿真器的结构化结果：

- HitFloor 表，例如 `capacity_sweep.csv`。
- `summary.md`。
- CLI / scripts wrapper。
- dashboard / notebook / batch job。
- 未来 P90 target matching / hit floor search。
- 未来策略对比报告、容量规划报告、SLO search report。

外围能力不能改变核心 replay 语义。任何新产品形态如果需要不同语义，必须新增 replay mode、cache backend、policy、adapter 或 result schema，不能在原有字段和模式上静默改语义。

每个新阶段、每个新步骤、每个代码开发批次都必须先声明：

```text
本次开发的是核心仿真器，还是外围能力。
```

如果答案不清楚，应先回到产品形态讨论，不进入代码开发。

## 当前阶段

当前暂不进入 Step7，先进行核心仿真器工程优化阶段。Step6 v1 功能验收已通过，主题是 `HBM Cache Capacity Sweep Report`。Pre-Step6 P1/P2 已完成：waiting queue 性能清扫和 replay benchmark harness。

Step4-Step6 过程文档已归档：

```text
docs/archive/step4/
docs/archive/step5/
docs/archive/step6/
```

活跃文档只保留主索引和当前状态：

```text
README.md
docs/global_memory.md
docs/code_development_requirements.md
docs/hitfloor_product_design.md
docs/core_simulator_technical_plan.md
```

notes 索引：

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
docs/notes/internal_model_deployment_method.md
```

历史开发状态已归档：

```text
docs/archive/development_status.md
```

## 已审批边界

- 请求已通过 `instance_uuid` 路由到实例，HitFloor 当前不做路由策略。
- 当前是固定路由、多实例隔离 replay；实例之间 cache 不共享。
- `batch_admission_delay = 0`。
- 当前只建模 prefill TTFT，不建模 TPOT 和 decode KV。
- cache 内部只保存 hash key 和 metadata，不保存全量 token ids，不保存真实 KV tensor。
- tokenizer / chat template 根据请求中的 `model` 字段选择。
- 当前有限 cache 只实现 HBM LRU；DDR LRU 和更多淘汰算法属于后续扩展。
- 报告输出以 CSV + `summary.md` 为主。
- Step6 第一版只 sweep `hbm_capacity_blocks`，不接受 GB 输入。
- Step6 第一版输出 capacity 与指标关系表，不做 P90 target matching。
- Step6 核心 runner 返回结构化 sweep result；HitFloor 表和 `capacity_sweep.csv` 是 report/export 外围能力，不属于 replay core。
- Step6 request build once，capacity sweep 复用 requests，不做 true streaming build。
- Step6 cache events 默认不落明细；只允许对指定 capacity dump `cache_events.csv`。
- Step6 `capacity_sweep.csv` 的 trace row 记录 replay-level `cache_event_count`，instance row 固定为 0，表示 v1 不提供 instance-level event count。
- Step6 多实例并行 replay 是后续项；单线程稳定后再新增 `ParallelCapacitySweepRunner` 或显式 execution backend。
- 旧 `implementation_plan.md` 已归档；“输出目标 P90 TTFT 对应的 hit floor” 是外围能力。
- 旧 `future_simulation_extensions.md` 已压缩并入 `hitfloor_product_design.md` 的核心仿真器扩展路线。

## Step1-Step6 完成能力

- CSV trace reader。
- strict OpenAI-style request parser。
- tokenizer / chat template registry。
- GLM-5 tokenizer profile。
- hash-only prefix block hasher。
- `SimulationRequest` 构造。
- 无限 HBM prefix cache replay。
- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching / chunked prefill replay。
- scheduler schema：`SchedulerConfig`、`RequestState`、`ScheduledSlice`、`BatchShape`。
- latency schema：`ShapeKey`、`LatencyResult`、`BatchLatencyBackend`。
- `FittedTTFTLatencyBackend` / `fitted_ttft`。
- `ShapeMemo`。
- `BatchAwareReplayEngine.run()`。
- first-schedule-time prefix cache lookup。
- bounded waiting lookup frontier。
- zero-miss / full-prefix-hit fast-finish。
- finish-time materialization。
- finite HBM LRU cache with `hbm_capacity_blocks`。
- streaming `cache_events.csv`。
- stateful eviction policy。
- package CLI as formal entrypoint。
- `scripts/` as local wrappers。
- `WaitingQueue` abstraction for scheduler/replay waiting state。
- `scripts/benchmark_replay.py` synthetic replay benchmark harness。
- `CapacitySweepRunner`。
- `CapacitySweepRow` / `CapacitySweepResult`。
- `StatsOnlyCacheEventSink`。
- `capacity_sweep.csv` / `summary.md` report/export。
- `hitfloor sweep` package CLI。
- `scripts/run_capacity_sweep.py` wrapper。

最新验证基线：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 115 passed
python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml: passed
python scripts/benchmark_replay.py --requests 10000 --instances 4: passed
python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml: passed
```

Step6 功能验收：

```text
docs/archive/step6/03_acceptance_e2e.md
```

验收结果：

- `hbm_capacity_blocks=3`: kv_hit_rate=0.000000, p90_ttft_ms=15.0。
- `hbm_capacity_blocks=4`: kv_hit_rate=0.876667, p90_ttft_ms=11.0。
- `hbm_capacity_blocks=8`: kv_hit_rate=0.913333, p90_ttft_ms=0.0。
- `run_capacity_sweep elapsed_ms = 659.9 ms`。
- cache event 信号符合预期。
- 未发现外围 HitFloor 表影响核心 replay 语义的风险。

## Frozen Semantics

核心语义以 `README.md` 的 `Core Semantics (Frozen)` 为准。后续如需改变语义，必须新增 py 类型、数据结构、adapter、cache backend 或 replay mode，不能静默修改旧字段含义。

特别注意：

- `batch_size` 是单个 scheduler iteration 内 request slice 数，不是 token batch。
- `max_num_batched_tokens` 是 iteration token budget，不是 batch size。
- `BatchShape` 是 HitFloor scheduler output，不是 AIConfigurator / MkSim 直接输入。
- Cache lookup 发生在 request 第一次进入 scheduler 可考虑范围时，不是 trace arrival 时。
- `batch_aware_hbm_lru` 固定采用 finish-time materialization。
- miss blocks 在 request prefill finish 前不对其他 request 可见。
- `HBMCache` 表示 request finish 后可复用的 prefix cache resident metadata，不是真实 runtime HBM physical block table。

## 仍未实现

- target-based hit floor solver / P90 target matching。
- DDR / SSD / multi-tier cache。
- KV load latency。
- gateway routing simulation。
- instance-side queueing policy simulation。
- external AIConfigurator / MkSim production adapter。
- cross-instance KV pooling。
- progressive block materialization。
- physical KV slot allocation、pinned/refcount。

## 工程优化候选

- request build 当前一次性构造全部 `SimulationRequest`；Step6 选择 build once 并在 capacity sweep 中复用。
- 多实例 replay 当前串行执行；Step6 第一版继续单线程，后续再设计并行 execution backend。
- 高 cache pressure 下 `cache_events.csv` 可能较大；Step6 只允许对指定 capacity 开启 event dump，默认使用 stats-only 事件计数。
- `.venv/bin/hitfloor` 当前不存在，说明项目尚未在 venv 中 editable/install；已验证 `PYTHONPATH=src python -m hitfloor.cli.main ...` 可用。
- finish-time materialization 可能低估长 prefill 请求中的 block reuse，需要评估 progressive block visibility。
- 只建模 prefill，不建模 decode / TPOT，未来 PD 混部场景需要补齐。
- 简单 fitted TTFT 公式需要演进为 latency profile 管理。

已清扫：

- `waiting.pop(0)` 性能风险：已新增 `WaitingQueue`，scheduler/replay 内部统一使用该 abstraction。
- benchmark 缺口：已新增 `scripts/benchmark_replay.py`，默认压测 HitFloor replay state machine，不模拟真实硬件。

处理这些遗留问题前，需要先明确产品范围、benchmark 规模、语义不变量和接口变化边界。

## 开发约束

- Step3 之后，每个阶段都必须先讨论产品形态，再讨论技术路线，最后才进行代码开发。
- 代码修改前必须先向用户确认。
- 可维护、可测试是第一准则。
- 每阶段必须包含测试和端到端验证。
- 代码开发必须遵守 `docs/code_development_requirements.md`。
