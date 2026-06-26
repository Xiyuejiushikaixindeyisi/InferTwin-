# HitFloor Simulator Integration Guide

本文档面向需要了解 HitFloor、或准备把 AIConfigurator / Markov-Infer-Sim 接入 HitFloor 的同事。

核心结论：

- HitFloor 可以在不下载大型 TTFT 仿真器的情况下继续开发和测试。
- Batch D 默认使用拟合型 TTFT 函数 backend，即 `FittedTTFTLatencyBackend` / `fitted_ttft`。
- AIConfigurator / Markov-Infer-Sim 后续优先作为拟合参数的标定来源，也可以作为高精度 latency backend 接入。
- 外部仿真器不应改写 replay、scheduler、cache materialization 逻辑。

## HitFloor 当前做什么

HitFloor 是离线 KV cache hit-floor 仿真器。

它负责：

1. 读取现网 trace。
2. 按请求中的 `model` 选择 tokenizer / chat template。
3. 生成 prompt token 和 prefix block hash。
4. 按 trace 中的 `instance_uuid` 分实例 replay。
5. 在实例内模拟 prefix KV cache lookup、hit、miss、materialization。
6. 使用 vLLM-like scheduler 模拟 continuous batching 和 chunked prefill。
7. 将每个 scheduler iteration 转成 batch shape。
8. 调 latency backend 得到本轮 duration。
9. 在 finish time 之后让新 cache block 可见。
10. 输出 request 级 TTFT、scheduler wait、cache hit/miss 和 iteration 级 batch metrics。

HitFloor 第一阶段不负责：

- 网关路由策略。
- 实例侧真实排队策略。
- decode TPOT 对 prefill 的干扰。
- 真实 GPU/NPU kernel profiling。
- 真实 KV tensor 存储。
- 跨实例 KV pooling。

## 不接大型 TTFT 仿真器时如何继续开发

在没有 AIConfigurator / Markov-Infer-Sim 的情况下，HitFloor 仍然可以完整推进 Batch D 以及后续有限 HBM / DDR 前置代码。

原因是 Step4 的核心不是某个外部 latency 数值，而是 replay 语义是否正确：

```text
trace arrival
-> tokenizer/chat template
-> prefix lookup
-> scheduler batch admission
-> iteration latency
-> finish-time materialization
-> per-request TTFT
```

latency backend 只替换其中这一段：

```text
BatchShape -> duration_ms
```

### 推荐开发方式

1. 默认使用 `FittedTTFTLatencyBackend`。

   拟合型 TTFT backend 是确定性的轻量后端，适合单测、集成测试、E2E 验证和常规离线分析。它不运行真实算子级仿真，而是用一个由模型 / 硬件 / 部署方式标定出的函数快速给出 iteration duration。

2. 先把 HitFloor 内部语义测准。

   重点测试：

   - first-schedule-time lookup。
   - bounded waiting lookup。
   - zero-miss fast-finish。
   - chunked prefill 多 iteration 完成。
   - finish-time materialization 只在完成后可见。
   - 实例间 cache 不共享。
   - `sum(iteration.scheduled_prefill_tokens) == sum(request.miss_tokens)`。
   - `ttft_ms = finish_time_ms - arrival_time_ms`。
   - `scheduler_wait_ms = first_scheduled_time_ms - arrival_time_ms`。

3. 用合成 trace 覆盖边界，用小型真实 trace 做 smoke test。

   合成 trace 应覆盖：

   - 单请求无命中。
   - 单请求 100% 命中。
   - 两请求同实例命中。
   - 两请求不同实例不命中。
   - 同一 iteration 内 materialization 不可见。
   - prompt 超过 `max_num_batched_tokens` 且未开启 chunked prefill 时失败。

4. 用 fitted TTFT 参数做趋势测试，不做未经标定的绝对性能结论。

   可以验证：

   - miss tokens 增加时 TTFT 不下降。
   - batch size 增加时 iteration duration 不下降，除非配置明确允许。
   - scheduler token budget 减小时，chunk 数量增加。

   不应宣称：

   - 某硬件真实 P90 TTFT。
   - 某模型真实吞吐。
   - cache 命中率提升一定带来固定百分比收益。

5. Batch D 可以先做 runner/report。

   Runner/report 只消费 HitFloor 内部 metrics，不依赖外部仿真器。后续把 fitted TTFT backend 替换成 AIConfigurator / MkSim backend，或用 AIConfigurator / MkSim 重新标定 fitted profile 时，报告字段和 replay 结果口径应保持不变。

## 拟合型 TTFT 函数 backend

Batch D 的默认 backend 是拟合型 TTFT 函数 backend。

推荐名称：

```text
FittedTTFTLatencyBackend
```

推荐配置名：

```text
fitted_ttft
```

第一版函数口径固定为 `token_linear_v1`：

```text
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

其中：

- `duration_ms`: 一个 scheduler iteration 的 prefill compute duration。
- `scheduled_prefill_tokens`: 本 iteration 内所有 request slice 的 uncached prefill token 总数。
- `intercept_ms`: 固定启动 / 框架开销，可先设为 0。
- `ms_per_uncached_token`: 核心超参数，由模型、硬件、并行策略、推理框架、batch/chunk 配置共同决定。

注意：

- `token_linear_v1` 不建模 queue time。
- `token_linear_v1` 不建模 HBM / DDR KV load time。
- `token_linear_v1` 不建模 decode TPOT 对 prefill 的干扰。
- scheduler wait 仍由 HitFloor replay 产生，不由 fitted backend 伪造。
- 100% prefix hit 请求仍走 zero-miss fast-finish，不产生新的 `ScheduledSlice`。

默认配置示例：

```yaml
latency:
  backend: fitted_ttft
  fitted_ttft:
    profile: glm-v5_ascend910c_default
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 0.02
    calibrated_from: manual_default
```

如果未来发现 batch size、context length、并行策略对延迟有显著非线性影响，不应修改 `token_linear_v1` 的含义。应新增函数版本或新 backend，例如：

```text
token_linear_with_batch_v2
piecewise_token_linear_v1
lookup_table_ttft_v1
external_simulator_v1
```

## 两种 TTFT 使用模式

HitFloor 后续支持两种 latency 使用模式。

### 快速拟合模式

```text
trace
-> HitFloor replay
-> FittedTTFTLatencyBackend
-> request_metrics.csv / iteration_metrics.csv / summary.md
```

特点：

- 快。
- 可复现。
- 适合反复 sweep cache 容量、token budget、scheduler 参数。
- 精度取决于 fitted profile 的标定质量。

### 真实仿真器重放模式

```text
trace
-> HitFloor replay
-> AIConfigurator / MkSim backend
-> request_metrics.csv / iteration_metrics.csv / summary.md
```

特点：

- 慢。
- 接入成本更高。
- 适合抽样校验、高精度分析、或为 fitted profile 生成标定数据。
- 不应成为 HitFloor 基础开发和单测的硬依赖。

### 推荐标定流程

```text
AIConfigurator / MkSim / profiling
-> 采样不同 scheduled_prefill_tokens / batch_size / context
-> 拟合 intercept_ms 与 ms_per_uncached_token
-> 生成 fitted_ttft profile yaml
-> HitFloor 快速 replay
```

第一版只要求得到 `token_linear_v1` 的两个参数：

```text
intercept_ms
ms_per_uncached_token
```

## 核心语义

以下语义已经在 `README.md` 的 `Core Semantics (Frozen)` 中冻结，外部 adapter 必须遵守。

- `batch_size` 是一个 scheduler iteration 内 request slice 数，不是 token 数。
- `max_num_batched_tokens` 是 token budget，不是 batch size。
- `BatchShape` 是 HitFloor scheduler output，不是 AIConfigurator / MkSim 的直接输入。
- `ScheduledSlice` 表示一个请求在一个 iteration 中被调度的 prefill token slice。
- `cached_prefix_tokens` 来自 prefix cache hit。
- `previous_chunk_tokens` 来自同一请求此前 chunk 已完成的 token。
- `computed_tokens_before = cached_prefix_tokens + previous_chunk_tokens`。
- cache lookup 发生在请求第一次被 scheduler 考虑时，不发生在 trace arrival 时。
- cache materialization 只在请求 prefill finish time 之后可见。

如果后续外部仿真器需要不同语义，应新增 adapter 输入类型或新 backend，不要改变这些字段含义。

## 外部仿真器接入总原则

外部仿真器只接入 latency backend 层：

```text
BatchAwareReplayEngine
-> BatchShape
-> BatchShapeConverter
-> SimulatorPrefillInput(s)
-> SimulatorLatencyBackend
-> LatencyResult(duration_ms)
-> replay advances finish_time
```

禁止：

- 在 replay core 中 import AIConfigurator / MkSim。
- 让外部仿真器重新决定 cache hit/miss。
- 让外部仿真器重新决定 scheduler admission。
- 把 `BatchShape` 直接当作 simulator input。
- 在 adapter 中静默猜测 heterogeneous batch 的转换策略。

推荐新增代码边界：

```text
src/hitfloor/latency/fitted_ttft.py           # default Batch D backend
src/hitfloor/latency/simulator_schema.py      # simulator-neutral input schema
src/hitfloor/latency/converter.py             # BatchShape -> SimulatorPrefillInput
src/hitfloor/latency/aiconfigurator_backend.py
src/hitfloor/latency/mksim_backend.py
tests/unit/latency/test_simulator_converter.py
tests/unit/latency/test_aiconfigurator_backend.py
tests/unit/latency/test_mksim_backend.py
```

现有 `src/hitfloor/external/` 可以保留为低层进程/API runner 边界。真正参与 Step4 replay 的 backend 应实现 `BatchLatencyBackend`。

## 建议的通用输入类型

后续接入外部 simulator 时，建议先定义 simulator-neutral schema：

```text
SimulatorPrefillInput:
  model_name
  hardware_name
  batch_size
  isl
  prefix_tokens
  scheduled_prefill_tokens
  ctx_tokens
  osl
  request_ids
  source_shape_key
  conversion_strategy
```

字段含义：

- `batch_size`: 请求数。
- `isl`: simulator 看到的 input sequence length。
- `prefix_tokens`: 每个请求已缓存或已完成、无需本轮 compute 的 context token 数。
- `scheduled_prefill_tokens`: 每个请求本轮需要计算的 prefill token 数。
- `ctx_tokens`: context token budget。AIConfigurator 可使用，MkSim 可忽略。
- `osl`: output sequence length。TTFT-only 场景也要显式配置，通常先设为 1。
- `request_ids`: 当前 simulator input 覆盖的请求。
- `conversion_strategy`: heterogeneous batch 转换策略。

基本约束：

```text
batch_size > 0
scheduled_prefill_tokens > 0
isl = prefix_tokens + scheduled_prefill_tokens
```

## heterogeneous batch 转换策略

HitFloor scheduler 可以产生 heterogeneous batch，例如同一 iteration 中不同请求的 `scheduled_prefill_tokens` 或 `computed_tokens_before` 不同。

AIConfigurator / MkSim 更偏向 uniform workload shape。因此 adapter 必须显式选择策略。

### strict_uniform

要求同一个 `BatchShape` 内所有 slice 的以下字段完全一致：

```text
scheduled_prefill_tokens
computed_tokens_before
cached_prefix_tokens
previous_chunk_tokens
```

不满足则失败。

这是第一版外部 adapter 的推荐策略，因为语义最干净，不隐藏估算偏差。

### group_by_shape

按 shape 分组，多次调用 simulator：

```text
(scheduled_prefill_tokens, computed_tokens_before, cached_prefix_tokens, previous_chunk_tokens)
```

同一个 scheduler iteration 的多个 simulator duration 需要显式 merge：

- `max`: 假设硬件并行处理多个组。
- `sum`: 假设组间串行。

第一版不建议默认启用，因为 merge 口径会明显影响 TTFT。

### max_shape_padding

用 batch 内最大 shape 填充成一个 uniform workload。

该策略实现简单，但可能系统性高估。只有在产品评审确认后才能使用。

## AIConfigurator 接入说明

AIConfigurator 是公开的解析式 / data-driven LLM serving 性能模拟器。对 HitFloor 来说，它优先作为 fitted TTFT 参数的标定来源；在需要高精度重放时，也可以作为 compute latency backend 使用。

推荐 backend 名称：

```text
AIConfiguratorLatencyBackend
```

推荐配置形态：

```yaml
latency:
  backend: aiconfigurator
  mode: iteration_controlled
  model_path: zai-org/GLM-5
  system_name: ascend_910c
  backend_name: sglang
  backend_version: ascend_v1
  database_mode: SILICON
  tp_size: 1
  pp_size: 1
  attention_dp_size: 1
  osl_for_ttft_backend: 1
  heterogeneous_strategy: strict_uniform
```

推荐转换：

```text
batch_size = SimulatorPrefillInput.batch_size
isl = SimulatorPrefillInput.isl
prefix = SimulatorPrefillInput.prefix_tokens
ctx_tokens = batch_size * scheduled_prefill_tokens
osl = osl_for_ttft_backend
```

注意事项：

- HitFloor 已经逐 iteration 控制 chunked prefill，AIConfigurator adapter 第一版应采用 `iteration_controlled`。
- 不要同时让 AIConfigurator 内部 IFB/chunking 再拆同一段 prefill，否则会双重建模。
- 如果 AIConfigurator CLI 不能表达 `prefix`，优先使用其 Python API 或 lower-level runtime config。
- 输出优先映射到 `LatencyResult.duration_ms`，细节写入 `LatencyResult.details`。
- AIConfigurator 不负责 HitFloor 的 DDR KV load、cache policy、cache materialization。

最低测试要求：

- strict uniform shape 可以成功估算。
- heterogeneous shape 在 strict mode 下显式失败。
- 同 input 重复调用输出确定。
- adapter 不 import replay core。
- replay core 不 import AIConfigurator。

## Markov-Infer-Sim 接入说明

Markov-Infer-Sim，简称 MkSim，是公司内算子级 roofline 性能仿真器。对 HitFloor 来说，它也优先作为 fitted TTFT 参数的标定来源；在需要高精度重放时，也可以作为 compute latency backend 使用。

推荐 backend 名称：

```text
MkSimLatencyBackend
```

推荐配置形态：

```yaml
latency:
  backend: mksim
  mode: iteration_controlled
  mksim_root: /path/to/Markov-Infer-Sim
  task_template: configs/task/pd_fusion.yaml
  model_path: configs/models/glm_5.json
  deploy_strategy: pd_fusion
  deploy_config_path: configs/deployment/pd_fusion.yaml
  hardware_config_path: configs/hardware/ascend_910c.json
  osl_for_ttft_backend: 1
  heterogeneous_strategy: strict_uniform
```

推荐转换：

```text
batch_size = SimulatorPrefillInput.batch_size
seq_len = SimulatorPrefillInput.isl
prefix_cache = SimulatorPrefillInput.prefix_tokens
out_len = osl_for_ttft_backend
```

注意事项：

- MkSim 核心不内建 HitFloor 所需的 chunked prefill loop。
- HitFloor 每个 scheduler iteration 应转换为一次或多次 MkSim prefill workload。
- MkSim 单次调用更适合 uniform workload shape。
- MkSim 不单独建模 DDR KV load；DDR 命中加载时间应由 HitFloor 后续 DDR/Ramulator2 路径建模。
- 如果使用 CLI adapter，临时配置文件必须写在 adapter 工作目录中，并在日志中保留输入摘要。

最低测试要求：

- strict uniform shape 可以生成 MkSim workload config。
- heterogeneous shape 在 strict mode 下显式失败。
- CLI/API 调用失败时暴露 stderr、config path 和 workload summary。
- 不把 MkSim 的 `prefix_cache` 同时用来表达 DDR 命中。

## 什么情况下 HitFloor 才算真正接入仿真器

接入完成不是指能调用外部命令，而是满足以下条件：

1. `BatchAwareReplayEngine` 不需要修改。
2. 同一份 trace 可以在 fitted TTFT / AIConfigurator / MkSim backend 间切换。
3. request metrics 字段不变。
4. iteration metrics 中记录 backend、shape key、duration 和 conversion strategy。
5. heterogeneous batch 处理策略显式写在 config 和输出中。
6. 外部 simulator 失败时，HitFloor 明确失败或进入 config guard，不伪造 latency。
7. 至少有一个小型 E2E fixture 能跑通：

```text
trace.csv
-> HitFloor replay
-> external latency backend
-> request_metrics.csv
-> summary.md
```

## 当前推荐路线

短期：

1. Batch D 先基于 `FittedTTFTLatencyBackend` 接 runner/report。
2. 报告中输出 backend name、shape key、iteration metrics 和 P90 TTFT。
3. 保持所有外部 simulator adapter 延后。

中期：

1. 新增 simulator-neutral schema 和 converter。
2. 先实现 strict_uniform converter。
3. 为 AIConfigurator / MkSim 各写 dry-run backend，先只生成输入，不调用重型仿真器。

后期：

1. 接 AIConfigurator Python API 或 CLI。
2. 接 MkSim internal API 或 CLI。
3. 用小型 trace 对齐 Formula 和外部 backend 的趋势。
4. 再讨论 DDR KV load、Ramulator2、HBM LRU、DDR LRU 的组合口径。
