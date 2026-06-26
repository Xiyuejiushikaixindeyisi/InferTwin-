# AIConfigurator Manual Notes

## 来源与定位

本文档根据用户提供的 AIConfigurator 接入信息说明整理。

AIConfigurator 是离线解析式 / data-driven LLM serving 性能模拟器和配置搜索器。它不跑真实推理，不需要 GPU/NPU，主要依赖预采集 kernel 性能表做插值和外推，输出 TTFT、TPOT、request latency、吞吐和功耗等指标。

对 HitFloor Step4 来说，AIConfigurator 的定位是：

```text
fast prefill/decode compute latency backend with IFB/chunked-prefill awareness
```

它可以作为 HitFloor 的 compute latency backend，但不负责：

- HitFloor 请求 replay。
- KV block 命中生成、保活、淘汰。
- DDR / host KV load。
- prefix cache 物化时机。

## 最小可运行样例

单点估算：

```bash
aiconfigurator cli estimate \
  --model-path Qwen/Qwen3-32B-FP8 \
  --system h200_sxm \
  --isl 4000 \
  --osl 1000 \
  --batch-size 64 \
  --tp-size 4 \
  --pp-size 1
```

配置搜索：

```bash
aiconfigurator cli default \
  --model-path Qwen/Qwen3-32B-FP8 \
  --total-gpus 32 \
  --system h200_sxm \
  --isl 4000 \
  --osl 1000 \
  --ttft 300 \
  --tpot 10
```

朴素配置生成：

```bash
aiconfigurator cli generate \
  --model-path Qwen/Qwen3-32B-FP8 \
  --total-gpus 8 \
  --system h200_sxm
```

Python API：

```python
from aiconfigurator.cli.api import cli_estimate

result = cli_estimate(
    model_path="Qwen/Qwen3-32B-FP8",
    system_name="h200_sxm",
    mode="agg",
    isl=4000,
    osl=1000,
    batch_size=64,
    tp_size=4,
    pp_size=1,
    backend_name="trtllm",
)

print(
    result.ttft,
    result.tpot,
    result.request_latency,
    result.tokens_per_second_per_gpu,
)
```

如果 HitFloor 只需要“给一个 shape，要一个延迟数字”，`cli_estimate` 是最接近的入口。

## 调用方式

AIConfigurator 支持：

| 方式 | 状态 | 说明 |
| :--- | :--- | :--- |
| CLI | 一等入口 | `aiconfigurator cli estimate/default/exp/generate/support` |
| Python API | 一等入口 | `aiconfigurator.cli.api` 下的 `cli_estimate` 等 |
| 配置文件 | 支持 | `exp` 模式吃 YAML |
| HTTP | 非内置稳定 API | 可选 service extra，但不是本体默认接口 |

HitFloor adapter 推荐优先使用 Python API，避免 shell/argparse/临时文件开销。replay core 不应 import AIConfigurator，只能依赖 HitFloor 自己定义的 latency backend protocol。

## Python API 关键信息

单点估算：

```python
cli_estimate(
    model_path: str,
    system_name: str,
    mode: str = "agg",
    backend_name: str = "trtllm",
    backend_version: str | None = None,
    database_mode: str = "SILICON",
    isl: int = 1024,
    osl: int = 1024,
    batch_size: int = 128,
    ctx_tokens: int | None = None,
    tp_size: int = 1,
    pp_size: int = 1,
    attention_dp_size: int = 1,
    moe_tp_size: int | None = None,
    moe_ep_size: int | None = None,
    free_gpu_memory_fraction: float | None = None,
    max_seq_len: int | None = None,
    ...
) -> EstimateResult
```

重要输出：

```text
ttft
tpot
request_latency
tokens_per_second_per_gpu
raw
```

更底层 SDK 可通过 `InferenceSession.run_static()` 调用，支持：

- `static_ctx`: 纯 prefill。
- `static_gen`: 纯 decode。
- `static`: prefill + decode。

对 HitFloor Step4，优先考虑：

```text
cli_estimate or InferenceSession.run_static(static_ctx)
```

具体选哪个，需要在 adapter 设计时根据 prefix/cache 参数暴露程度确认。

## 输入文件与 profile

模型：

- 支持 HF model id。
- 支持本地 config 目录。
- 仓库内置常用模型 config，例如 `zai-org--GLM-5_config.json`。

硬件：

- `src/aiconfigurator/systems/<system>.yaml`
- 对应 `systems/data/<system>/<backend>/<version>/*.txt` 性能表。

实验定义：

- `exp` 模式使用 YAML。

生成器配置：

- `--generator-config`
- `--generator-set`

## 输出格式

`cli_estimate` / `cli_default` / `cli_exp` 返回 Python dataclass，核心数据是 pandas DataFrame。

落盘产物包括：

- `best_config_topn.csv`
- `pareto.csv`
- `config.yaml`
- `pareto_frontier.png`
- Dynamo 部署文件，例如 `agg_config.yaml`、`prefill_config.yaml`、`decode_config.yaml` 等。

HitFloor 单点 latency adapter 不应依赖 deployment 产物，只需要读取 `EstimateResult.ttft` 或 lower-level summary 中的 context latency。

## 多 shape 支持

`cli_estimate` 一次一个 shape。

`exp` 模式可以在 YAML 内定义多个实验，但每个实验仍是一个 `(isl, osl)` 点。

没有原生“一次传入一批 shape list”的接口。

HitFloor 建议：

```text
ShapeMemo + 外层循环 cli_estimate / lower-level API
```

因为单点调用通常亚秒级到秒级，外层循环可接受。

## batch size 定义

AIConfigurator 中：

```text
RuntimeConfig.batch_size = 并发请求数 / in-flight sequences
```

它不是：

- token batch。
- `ctx_tokens`。
- `max_num_batched_tokens`。

Prefill token 维度由：

```text
effective_isl = isl - prefix
x = batch_size * effective_isl
```

Decode 每步处理约 `batch_size` 个 token。

### 对 HitFloor 的影响

HitFloor 当前 `BatchShape.batch_size = request slice 数` 与 AIConfigurator 的 request-level batch size 一致。

但 AIConfigurator 假设 batch 内共享同一个：

```text
isl
prefix
osl
ctx_tokens
```

因此它和 MkSim 一样，不能直接表达任意 heterogeneous batch 中每个 request slice 都有不同 `scheduled_prefill_tokens` 和 `computed_tokens_before` 的情况。

## prefill shape

AIConfigurator prefill 主要参数：

```text
isl
osl
prefix
batch_size
ctx_tokens
```

含义：

- `isl`: input sequence length。
- `prefix`: `isl` 中已缓存、无需重新计算 activation 的 token 数。
- `effective_isl = isl - prefix`。
- `batch_size`: 请求数。
- `ctx_tokens`: IFB 调度的 context token 预算，默认等于 `isl`。
- `osl`: output sequence length。

约束：

```text
effective_isl = isl - prefix > 0
```

AIConfigurator 不支持每个请求不同 `isl` 的 batch。它使用一个代表值 / 平均值进行估算。

## IFB 与 chunked prefill

AIConfigurator 对 aggregated / IFB 有解析建模：

```text
steps_to_finish_ctx = ceil(isl * batch_size / ctx_tokens)
```

每个混合步包含：

- 一部分 context / prefill tokens。
- 一部分 generation / decode tokens。

`ctx_tokens` 表示每步 context token 预算。长 prompt 被切成多个 step 完成。

与 MkSim 的差异：

- MkSim 核心不内建 chunk loop，chunked prefill 需要外层多次 prefill 调用近似。
- AIConfigurator 内部可以通过 `ctx_tokens` 建模 chunked prefill / IFB。

对 HitFloor Step4 的影响：

- 如果使用 AIConfigurator adapter，可以把 HitFloor scheduler 的 token budget 映射为 `ctx_tokens`。
- 但如果 HitFloor 已经逐 iteration 做 scheduler replay，再让 AIConfigurator 内部再拆 IFB，可能会双重建模 chunking。
- 因此需要决定 adapter 模式：
  - `iteration_mode`: HitFloor 每个 iteration 调一次 simulator，`ctx_tokens` 设为本轮 total scheduled prefill tokens 或关闭内部分块。
  - `request_batch_mode`: AIConfigurator 自己建模 IFB/chunking，HitFloor 只提供代表 batch workload。

Step4 Batch C 更适合 `iteration_mode`，因为 replay 已由 HitFloor 控制。

## 模型 profile

模型通过 `model_path` 指定：

- HF id。
- 本地 config 目录。
- 内置 model config。

GLM-5 已内建：

```text
zai-org--GLM-5_config.json
architecture = GlmMoeDsaForCausalLM
mapped family = DEEPSEEKV32
```

支持模型族包括：

- GPT
- LLAMA
- MOE
- HYBRIDMOE
- DEEPSEEK
- DEEPSEEKV32
- KIMIK25
- NEMOTRONNAS
- NEMOTRONH
- QWEN35

模型结构参数从 HF `config.json` 读取，不需要在 HitFloor 中手填 hidden size、layer count、head dim 等。

## 并行、dtype 与 backend

通过 `ModelConfig` 或 CLI 参数指定：

- TP: `tp_size`
- PP: `pp_size`
- Attention DP: `attention_dp_size`
- MoE TP / EP: `moe_tp_size` / `moe_ep_size`
- GEMM dtype: `gemm_quant_mode`
- MoE dtype: `moe_quant_mode`
- KV cache dtype: `kvcache_quant_mode`
- FMHA dtype: `fmha_quant_mode`
- communication dtype: `comm_quant_mode`
- attention backend: `attention_backend`
- speculative/MTP: `nextn`, `nextn_accept_rates`

`backend_name` 支持：

- `trtllm`
- `vllm`
- `sglang`

dtype 不传时从 HF config 和 quant config 推断。显式配置会覆盖自动推断。

## 硬件 profile

通过 `--system <name>` 选择预置系统。

内置系统包括：

```text
a100_sxm
h100_sxm
h200_sxm
b200_sxm
b300_sxm
gb200
gb300
l40s
b60
ascend_910c
```

`ascend_910c` 有 system yaml 和 sglang 数据。

系统 YAML 建模：

- HBM 带宽。
- HBM capacity。
- tensor core FLOPS。
- int8 / fp8 FLOPS。
- power。
- 每节点 GPU 数。
- inter/intra node bandwidth。
- pcie bandwidth。
- P2P latency。
- NCCL memory。

不建模独立 DDR / host memory 层级。

自定义硬件需要：

- system YAML。
- 对应 kernel 性能表数据。

## cache 命中表达

AIConfigurator 通过 `prefix` 表达 cache 命中：

```text
prefix = isl 中已缓存、无需重新计算 activation 的 token 数
effective_isl = isl - prefix
```

它理解 cached vs miss，但只有一个粗粒度参数：

```text
prefix
```

它不是 token/block 级 cache simulator。

如果只想传 miss tokens：

```text
prefix = isl - miss_tokens
```

约束：

```text
isl - prefix > 0
```

prefix 也会影响 KV cache 显存占用建模。

## KV load 与 DDR 边界

AIConfigurator 不建模：

- DDR -> HBM KV load。
- host memory 层级。
- KV transfer delay。
- Ramulator 级内存子系统。

cache 命中在 AIConfigurator 中只体现为：

```text
减少 prefill activation compute
```

不体现：

```text
从 HBM/DDR/远端读回 KV 的独立时间
```

因此 HitFloor 仍需外接 Ramulator2 或独立 backend 来建模 DDR KV load。

## 输出与单位

延迟单位是毫秒。

关键指标：

```text
context_latency / ttft
generation_latency
tpot
request_latency
tokens/s
tokens/s/gpu
tokens/s/user
```

`ttft` 是 prefill 阶段解析合成时间，单点估算不含排队。

AIConfigurator 包含：

- communication。
- IFB 混合步 / disagg rate matching 调度解析。
- 部分经验修正 host overhead。

不包含：

- queueing delay。
- HitFloor 的 instance-level replay。
- KV load / transfer 独立时间。

## 确定性

AIConfigurator 是确定性的。同输入同输出。

建议集成时固定：

```text
database_mode = "SILICON"
```

`HYBRID` / `EMPIRICAL` / `SOL` 也确定性，但建模假设不同。

## 工程约束

| 项 | 情况 |
| :--- | :--- |
| 运行依赖 | 纯 Python，无 GPU/编译依赖 |
| 核心依赖 | numpy, pandas, scipy, jinja2, pyyaml, matplotlib, plotly, bokeh, prettytable, plotext, tqdm, munch, nvidia-ml-py, packaging |
| 数据大小 | systems data 约 644 MB，整包约 650 MB，需要 git-lfs |
| Python | 3.10+ |
| GPU/NPU | 运行 estimator 不需要 |
| 离线运行 | 可以，前提是模型 config 和性能数据本地可用 |
| License | Apache-2.0 |
| 单点耗时 | 亚秒级到秒级 |

## 与 HitFloor 的需求对照

| HitFloor 关注点 | AIConfigurator 状态 | 影响 |
| :--- | :--- | :--- |
| 单 shape 延迟 | 支持 `cli_estimate` | 可作为 backend |
| 批量多 shape | 无原生 list 接口 | 外层循环 + memo |
| batch size | 请求数 | 与 HitFloor slice count 口径接近 |
| miss tokens / prefix hit | 用 `prefix` 表达 | 需要 adapter 转换 |
| chunked prefill | 通过 `ctx_tokens` / IFB 建模 | 需避免和 HitFloor scheduler 双重建模 |
| heterogeneous batch | 不直接支持 per-request shape | 需要转换策略 |
| GLM-5 | 内建支持 | 可用 |
| Ascend 910C | 有 profile | 需 support check |
| DDR KV load | 不支持 | HitFloor/Ramulator2 外接 |
| 排队延迟 | 不支持 | HitFloor replay 外接 |

## HitFloor 接口影响

### 1. 不能把 BatchShape 直接视为 AIConfigurator input

AIConfigurator 需要 uniform workload：

```text
batch_size
isl
prefix
osl
ctx_tokens
```

HitFloor `BatchShape` 是 per-request-slice shape，可能 heterogeneous。

因此需要 adapter-specific conversion。

### 2. chunked prefill 有两种建模路径

路径 A：HitFloor 控制 chunk。

```text
HitFloor scheduler emits one iteration
AIConfigurator estimates that iteration as one prefill step
```

路径 B：AIConfigurator 控制 chunk。

```text
HitFloor provides full workload
AIConfigurator uses ctx_tokens to model IFB/chunking
```

Step4 Batch C 已经选择由 HitFloor replay 控制 scheduler 和 chunked prefill，因此更适合路径 A。

### 3. prefix 与 previous chunk context 需要区分

AIConfigurator 的 `prefix` 表示“已缓存、无需算 activation”的 tokens。

HitFloor 的 `computed_tokens_before` 可能包含两类：

- 原本从 prefix cache 命中的 tokens。
- 同一请求前面 chunk 已经算完的 tokens。

这两类在 simulator 中都表现为本轮无需计算 activation，但对 cache/memory 和指标解释不同。

后续接口设计应明确保留：

```text
cached_prefix_tokens
previous_chunk_tokens
scheduled_prefill_tokens
```

而不是只保留一个 `computed_tokens_before`。

### 4. out_len 仍需配置

AIConfigurator 需要 `osl`。HitFloor Step4 不建模 decode TPOT。

建议 adapter 配置显式提供：

```text
osl_for_ttft_backend
```

默认可为 1，但必须写入 config 和报告口径，不能隐式硬编码。

## 待确认问题

1. `cli_estimate` 是否直接暴露 `prefix` 参数；若不暴露，是否使用 lower-level `RuntimeConfig`。
2. `iteration_mode` 下如何关闭或约束 AIConfigurator 内部 IFB chunking，避免双重建模。
3. heterogeneous batch 是否采用 strict uniform、max-shape padding、group-by-shape 或 formula-only。
4. `osl_for_ttft_backend` 的默认值与配置位置。
5. 是否优先支持 `backend_name="sglang"` + `system_name="ascend_910c"`，以贴近 GLM-5 现网。

