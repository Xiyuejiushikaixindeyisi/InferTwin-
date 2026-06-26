# HitFloor 核心仿真器技术路线与代码实现方案

## 1. 文档定位

本文是 HitFloor 核心仿真器的主技术路线文档。

旧 `docs/implementation_plan.md` 已归档到：

```text
docs/archive/implementation_plan.md
```

原因：

- 旧文档以“输出目标 P90 TTFT 对应的 hit floor”为主线。
- hit floor search 是外围能力，不是核心仿真器。
- HitFloor 当前核心定位已经升级为大型推理服务集群离线仿真平台。

## 2. 开发阶段划分

核心仿真器后续开发分为两类阶段：

1. 工程优化阶段。
2. 实际开发阶段。

### 2.1 工程优化阶段

目标：

- 提升仿真器与真实 vLLM 推理框架的一致性。
- 提升大 trace 下的稳定性和性能。
- 明确哪些近似是为了性能，哪些是能力边界。
- 保证各仿真模块之间有序工作。
- 保证实例之间 replay 互不影响。

工程优化阶段可以不引入新产品能力，但必须输出：

- 当前仿真行为。
- 与真实推理服务的差异。
- 误差风险。
- 是否需要新 replay mode / backend / schema。
- benchmark 和 E2E 验证。

### 2.2 实际开发阶段

目标：

- 开发新的核心仿真器能力。
- 例如 multi-tier cache、KV load latency、queue simulation、gateway simulation。

实际开发阶段必须先声明：

```text
本阶段开发的是核心仿真器能力。
```

如果开发的是 CSV、summary、dashboard、search、CLI wrapper，则应声明为外围能力。

## 3. 当前核心仿真器代码结构

核心代码路径：

```text
src/hitfloor/
  trace/                 # trace schema and CSV reader
  request/               # request parser, tokenizer registry, chat template, block hash
  instance/              # SimulationRequest and early replay utilities
  scheduler/             # vLLM-like scheduling schema and policy
  cache/                 # prefix cache backend, events, eviction policy
  latency/               # fitted TTFT backend and latency schema
  replay/                # batch-aware replay engine and metrics
  experiment/            # request builder, runner, sweep orchestration
  report/                # outer report/export
  cli/                   # package CLI
```

核心模块：

| 模块 | 职责 |
| --- | --- |
| `experiment/request_builder.py` | 从 config 构造 `SimulationRequest` |
| `replay/event_loop.py` | fixed-routing, multi-instance isolated replay |
| `scheduler/vllm_like.py` | iteration-level request slice 选择 |
| `scheduler/planning.py` | chunked prefill token selection helper |
| `scheduler/queue.py` | waiting queue abstraction |
| `cache/hbm_lru.py` | finite HBM prefix cache |
| `cache/eviction.py` | stateful eviction policy |
| `cache/event_sink.py` | event sink and stats |
| `latency/fitted_ttft.py` | token-linear fitted TTFT backend |
| `experiment/sweep.py` | HBM capacity sweep runner and aggregation |

外围模块：

| 模块 | 职责 |
| --- | --- |
| `report/sweep.py` | 导出 `capacity_sweep.csv` / `summary.md` |
| `report/summary.py` | 单次 replay summary |
| `report/tables.py` | CSV writer |
| `cli/main.py` | 解析 CLI，调用 runner/report |
| `scripts/` | local wrapper |

## 4. 当前 Replay 工作流

当前主工作流：

```text
CSV trace
-> TraceRecord
-> parse request_params
-> tokenizer + chat template
-> prompt token ids
-> prefix block hash
-> SimulationRequest
-> BatchAwareReplayEngine
-> per-instance replay
-> cache lookup
-> scheduler iteration
-> latency estimate
-> finish-time materialization
-> metrics
```

实例内状态机：

```text
pending -> waiting -> running -> finished
```

每个 instance 独立：

- independent `WaitingQueue`。
- independent `HBMCache`。
- independent iteration clock。
- independent request states。

全局输出最后按时间 / instance / request deterministic sort。

## 5. 当前仿真内容

当前核心仿真器已经模拟：

- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching。
- chunked prefill。
- first-schedule-time prefix cache lookup。
- bounded waiting lookup frontier。
- zero-miss fast-finish。
- finish-time materialization。
- HBM LRU block lifecycle。
- lookup hit / lookup miss / materialize / evict events。
- fitted TTFT prefill latency。
- HBM capacity sweep。

## 6. 当前不建模内容

当前不建模：

- 真实模型推理。
- 真实物理 KV tensor。
- physical KV slots。
- pinned/refcount。
- progressive block visibility。
- DDR / SSD / remote cache。
- KV load latency。
- TPOT。
- decode batch。
- gateway routing。
- 真实机器侧排队。
- heterogeneous instance profiles。
- cross-instance KV pooling。

这些内容不是外围能力，而是未来核心仿真器能力。

## 7. 与真实 vLLM 推理服务的核心差异

当前主要差异有两类。

### 7.1 没有真实推理

HitFloor 不部署真实模型，不执行真实 attention / MLP / decode kernel。

当前使用：

```text
FittedTTFTLatencyBackend
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

风险：

- 无法表达不同 batch shape 的非线性。
- 无法表达 cached prefix 对 attention shape 的影响。
- 无法表达 decode 对 prefill 的干扰。
- 无法表达硬件占用、kernel overlap、host/device transfer 等细节。

当前缓解：

- `BatchShape` 和 `ShapeKey` 已存在。
- `ShapeMemo` 已存在。
- 后续可以用外部 TTFT simulator / production logs 拟合更复杂 profile。

### 7.2 没有真实物理存储

HitFloor cache 当前只保存：

```text
block_key + metadata
```

不保存：

- token ids。
- KV tensor。
- physical block table。
- refcount。
- pinned state。
- device memory fragmentation。

风险：

- 无法评估真实 memory pressure。
- 无法表达 physical block allocation failure。
- 无法表达 shared block refcount / copy-on-write。

当前缓解：

- 用 `hbm_capacity_blocks` 控制逻辑容量。
- 用 event stats 检查 lookup/materialize/evict。
- 后续新增 physical slot / refcount mode，而不是改变当前 HBM-only mode。

## 8. 工程优化重点

### 8.1 Progressive Block Visibility

当前 HitFloor：

```text
request prefill finish 后，miss blocks 才 materialize。
```

这叫 finish-time materialization。

真实 vLLM 的 prefix caching 更细：

- vLLM 文档说明 prefix cache 使用 full block。
- scheduler 会调用 `get_computed_blocks()` 查已计算 blocks。
- `allocate_slots()` 中满 block 可以进入 cache block map。
- running request 追加 token 后，如果 block full，会加入 cache。

参考：

- vLLM docs: <https://docs.vllm.ai/en/latest/design/prefix_caching/>
- vLLM source: <https://github.com/vllm-project/vllm/blob/main/vllm/v1/core/kv_cache_manager.py>
- vLLM source: <https://github.com/vllm-project/vllm/blob/main/vllm/v1/core/block_pool.py>

初步判断：

```text
vLLM 只缓存 full blocks；
full block 可能在 request 运行过程中进入 prefix cache；
不一定需要等整个 request prefill 完成。
```

为什么重要：

- 现网中 P80 KV block reuse 间隔可能在 1 min 左右。
- 128K 长请求 prefill 可达 30s 左右，更长请求会更久。
- 如果 HitFloor 等整个 TTFT 完成才让 blocks 可见，长请求场景可能低估 KV hit。

可行性评估：

- 可以新增 chunk/block-level materialization mode。
- 在每个 scheduled chunk finish 后，将 newly completed full blocks materialize。
- partial block 仍不能 cache。
- 需要重新定义 event timing 和 lookup frontier。
- 不应改变 `batch_aware_hbm_lru` 的 frozen finish-time 语义，应新增 replay/cache mode。

建议：

```text
Step7 前工程优化阶段做专项调研和设计。
不要直接修改现有 finish-time materialization。
```

### 8.2 Decode / TPOT 建模

当前 HitFloor 只做 prefill TTFT。

真实服务中：

- decode batch 会占用 iteration。
- TPOT 会影响用户体验。
- PD 混部模型中 decode 和 prefill 会互相干扰。
- decode KV cache 会持续增长。

风险：

- 不考虑 decode 会高估 prefill 可用资源。
- 不考虑 TPOT 会低估混部服务尾延迟。
- 不考虑 decode cache growth 会低估 cache pressure。

建议：

- 当前工程优化阶段先不实现 TPOT。
- 先设计接口位置和 metrics。
- 后续新增 decode-aware scheduler / replay mode。

### 8.3 Latency Profile 管理

当前 fitted TTFT 公式过于简单。

建议新增核心类：

```text
ServingLatencyProfile
```

职责：

- 管理 TTFT 计算。
- 管理 KV load time。
- 管理 queue time。
- 管理 TPOT。
- 管理 decode token count。
- 管理部署形态和启动参数。
- 管理外部 simulator 校准结果。

建议数据组成：

```text
ServingLatencyProfile:
  model_name
  hardware_name
  deployment_shape
  launch_args
  ttft_model
  kv_load_model
  queue_model
  tpot_model
  calibration_policy
```

TTFT 未来建议：

```text
request miss tokens
-> split into chunks
-> estimate each chunk duration
-> compose request TTFT
```

KV load time 未来建议：

```text
kv_load_time ~= f(cache_tier, hit_tokens, kv_bytes, hardware)
```

动态拟合建议：

```text
每 N 条请求重新校准 profile，默认 N=500，可配置。
```

外部来源：

- AIConfigurator / MkSim 用于 TTFT / prefill 计算标定。
- Ramulator2 用于 DDR / memory access latency 标定。
- production logs 用于拟合和校验。

接入说明索引：

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
```

设计原则：

- `ServingLatencyProfile` 是核心仿真器能力。
- 外部 simulator adapter 是依赖，不应污染 replay 数据结构。
- report 只展示 profile 信息，不参与计算。

### 8.4 大 Trace 性能

当前：

- request build 一次性构造全部 `SimulationRequest`。
- capacity sweep 复用 requests。
- 多实例 replay 串行。
- cache event 明细默认关闭。

优化方向：

- request streaming build。
- per-instance sharding。
- per-instance parallel replay。
- event sampling。
- stats-only event path。
- shape memoization 复用策略。

约束：

- 不牺牲 deterministic output。
- 不牺牲实例隔离。
- 不让 report/export 参与 replay。

## 9. 实际开发阶段路线

多级能力暂不展开完整技术方案，但核心开发顺序已确认：

1. 多级 cache backend。
2. KV load latency。
3. instance queue simulation。
4. gateway simulation。
5. 实例集群仿真。
6. cache 管理。

每个阶段必须先写产品形态，再写技术路线，再进入代码开发。

## 10. 开发状态

### 10.1 已完成

Step1-Step6 已完成：

- trace reader。
- request parser。
- tokenizer / chat template registry。
- GLM-5 profile。
- hash-only prefix block。
- fixed-routing multi-instance isolated replay。
- vLLM-like batch-aware replay。
- chunked prefill。
- fitted TTFT backend。
- infinite HBM prefix cache。
- finite HBM LRU。
- stateful eviction policy。
- event sinks。
- HBM capacity sweep。
- `capacity_sweep.csv` / `summary.md` 外围导出。

### 10.2 最近验收

Step6 功能验收：

```text
docs/archive/step6/03_acceptance_e2e.md
```

验收结果：

| hbm_capacity_blocks | kv_hit_rate | p90_ttft_ms |
| ---: | ---: | ---: |
| 3 | 0.000000 | 15.0 |
| 4 | 0.876667 | 11.0 |
| 8 | 0.913333 | 0.0 |

验证基线：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 115 passed
python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml: passed
python scripts/benchmark_replay.py --requests 10000 --instances 4: passed
python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml: passed
```

### 10.3 遗留问题

- request build 仍是一次性构造。
- 多实例 replay 当前串行。
- event 明细大文件需要继续控制。
- progressive block visibility 未实现。
- decode / TPOT 未实现。
- multi-tier cache 未实现。
- KV load latency 未实现。
- gateway / queue simulation 未实现。
- heterogeneous instance cluster 未实现。

## 11. Step7 前工程优化目标

正式进入 Step7 前，建议先做工程优化阶段。

目标：

- 明确当前仿真器与真实 vLLM 的差异。
- 评估 progressive block visibility 是否必要。
- 设计 `ServingLatencyProfile`。
- 梳理性能瓶颈。
- 确认实例隔离和 replay deterministic。
- 输出工程优化代码方案，经审批后再修改代码。
