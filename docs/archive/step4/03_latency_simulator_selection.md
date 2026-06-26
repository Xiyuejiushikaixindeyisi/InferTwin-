# Latency Simulator Selection

Step4 不能直接绑定某一个重型仿真器。正确做法是先定义 HitFloor 内部稳定的 latency input/output schema，然后为 AIConfigurator 和 Markov-Infer-sim 分别实现 adapter。

在用户提供两个仿真器的信息前，Step4 代码应先实现：

- `FormulaLatencyBackend`: 轻量、确定性、用于单测和 E2E。
- `LatencyBackend` 抽象接口。
- `BatchShape` 与 `LatencyResult` 稳定 schema。

等仿真器信息明确后，再选择默认 backend，并实现对应 adapter。

## 初始选择倾向

当前不下载巨型仿真器时，默认选择：

```text
FormulaLatencyBackend for Step4 development and tests
```

AIConfigurator 与 Markov-Infer-sim 的最终选择依据：

1. 是否能接收 HitFloor 生成的 batch shape，而不是只接收过度简化的 batch size。
2. 是否能区分 prompt/prefill token 数、batch size、context length。
3. 是否支持或可近似 chunked prefill。
4. 是否能离线批量调用，且运行成本适合 2 小时现网 trace。
5. 是否输出稳定、可复现、单位明确的 latency。
6. 是否容易通过 adapter 包装，不把仿真器细节泄漏到 replay 核心逻辑。

如果 Markov-Infer-sim 能更准确表达公司内硬件和模型 profile，但安装重、不可开源，建议作为 private adapter；HitFloor 公开路径保留 AIConfigurator 或 formula backend。

## HitFloor 内部 Latency Schema

### LatencyInput

```text
model_name
hardware_name
iteration_id
batch_size
scheduled_prefill_tokens
scheduled_decode_tokens
max_query_len
total_context_tokens
request_shapes[]
```

`request_shapes[]` 建议包含：

```text
request_id
scheduled_prefill_tokens
cached_tokens
computed_tokens_before_iteration
prompt_tokens
```

### LatencyResult

```text
duration_ms
backend
shape_key
memoized
details
```

`shape_key` 用于 shape memoization：相同模型、硬件和 batch shape 多次出现时，可以复用 latency result。

## 需要用户提供的信息

请为 AIConfigurator 和 Markov-Infer-sim 分别提供以下信息。可以先提供最小可运行样例，不需要下载完整工程。

### 1. 调用方式

- 是 Python API、CLI、HTTP 还是配置文件驱动？
- 最小调用命令或函数签名是什么？
- 输入文件格式是什么？
- 输出文件格式是什么？
- 是否支持批量输入多个 shape？

### 2. batch size 定义

- `batch size` 指请求数，还是 token batch，还是 kernel/算子维度？
- 对 prefill 来说，是否还需要 `seq_len` / `context_len` / `prompt_len`？
- 混合 prefill + decode 时，batch size 如何定义？
- chunked prefill 的一个 chunk 是否被视为独立 prefill 请求？

### 3. 模型与硬件 profile

- 如何指定模型，例如 GLM-5？
- 如何指定 dtype、TP、PP、DP、attention backend？
- 如何指定硬件，例如 Ascend/GPU 型号、HBM、DDR、带宽？
- 是否需要模型 hidden size、num layers、num heads、kv heads、head dim 等参数？

### 4. cache 命中表达

- simulator 是否理解 cached tokens / miss tokens？
- 如果不理解，是否只需要传入实际需要计算的 `miss_tokens`？
- kv load time 是否在该 simulator 内建模，还是需要 HitFloor 另接 Ramulator2？

### 5. 输出与单位

- 输出 latency 是 ms/us/ns？
- 输出的是 prefill compute time、end-to-end iteration time，还是 kernel time 之和？
- 是否包含排队、调度、通信、host overhead？
- 是否有随机性？如何固定 seed？

### 6. 工程约束

- 安装大小和依赖。
- 是否需要 GPU/NPU。
- 是否能在离线环境运行。
- 是否有 license 或公司内保密边界。
- 典型一次调用耗时。

## Adapter 选择规则

Step4 进入代码开发时使用如下规则：

1. 如果两个 simulator 都没有可稳定调用接口，先只实现 `FormulaLatencyBackend`。
2. 如果 AIConfigurator 有轻量 CLI/API 且 batch shape 足够，优先实现 `AIConfiguratorBackend`。
3. 如果 Markov-Infer-sim 的模型/硬件口径明显更接近现网，但接口较重，先定义 `MarkovInferSimBackend` 空壳和 schema 校验，不阻塞 Step4 主流程。
4. replay 核心代码不得 import 任一外部仿真器；只能依赖 `LatencyBackend` 协议。

