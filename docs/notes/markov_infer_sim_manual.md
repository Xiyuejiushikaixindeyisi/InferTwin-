# Markov-Infer-Sim Manual Notes

## 来源与定位

本文档根据用户提供的 Markov-Infer-Sim 调用说明整理。

Markov-Infer-Sim，简称 MkSim，是面向 LLM / 多模态推理的算子级 roofline 性能仿真器。输入模型结构、硬件 profile、部署/并行策略和 workload 后，输出 TTFT、TPOT、吞吐和显存等指标。

对 HitFloor Step4 来说，MkSim 的定位是：

```text
batch-aware prefill compute latency backend
```

它可以提供 compute latency，包含 HBM 访存和通信建模，但不负责：

- HitFloor 的请求 replay。
- continuous batching 调度。
- DDR KV load。
- cache 命中生成和淘汰。

## 最小可运行样例

单任务：

```bash
python run.py -c configs/task/pd_fusion.yaml
python run.py -c configs/task/pd_split.yaml -o output/my_run
```

批量寻优：

```bash
python run_optimal.py -c configs/optimal/multi_strategy_pd_sep.yaml
python run_optimal.py -c configs/optimal/multi_strategy_pd_sep.yaml --task Dsv3_Hetero_Benchmark --verbose
```

## 调用方式

MkSim 是配置文件驱动的 Python CLI。

| 入口 | 用途 | 命令 |
| :--- | :--- | :--- |
| `run.py` | 单任务仿真 | `python run.py -c <task.yaml> [-o <out_dir>]` |
| `run_optimal.py` | 批量寻优 / 搜索空间遍历 | `python run_optimal.py -c <optimal.yaml> [-t <task_name>...] [-o <out>] [-w <workers>] [-v]` |

没有 HTTP 服务。官方稳定入口是 CLI。内部可程序化调用：

```python
from src.configs import load_task_config
from src.configs.task_converter import convert_task_config_to_workload_and_plan
from src.optimal.task import Task

task_config = load_task_config("configs/task/pd_fusion.yaml")
workload, plan = convert_task_config_to_workload_and_plan(task_config)
result = Task(workload=workload, plan=plan).execute()

print(
    result.throughput,
    result.execution_time_ms,
    result.metrics.ttft_ms,
    result.metrics.tpot_ms,
)
```

HitFloor adapter 可以选择：

- CLI 方式：生成 task yaml，调用 `run.py`，读取 metrics csv。
- 内部 API 方式：直接构造 config 并调用 `Task(...).execute()`。

第一版 adapter 建议优先做内部 API 或临时目录 CLI 封装，但必须把 MkSim 细节隔离在 adapter 内，replay core 不直接 import MkSim。

## 输入格式

单任务由 YAML 文件拼装：

```yaml
model_path: "configs/models/deepseek_v4_pro.yaml"
deploy_strategy: "pd_fusion"
deploy_config_path: "configs/deployment/pd_fusion.yaml"
chip_path: ["configs/chips/h100-sxm.yaml"]
output_path: "output/task/pd_fusion"
op_config: "configs/op_config/op_config.yaml"
workload: "configs/workload/workload_pd_fusion.yaml"
```

模型也支持 JSON，例如 `configs/models/glm_5.json`。

task 的核心组成：

- `model_path`: 模型结构。
- `deploy_strategy`: 部署策略，例如 `pd_fusion` / `pd_split`。
- `deploy_config_path`: 卡数和并行策略。
- `chip_path`: 硬件 profile。PD 分离时可以给两个。
- `op_config`: 算子后端 / 利用率配置。
- `workload`: workload shape。

## 输出格式

MkSim 输出：

- `prefill.csv`
- `decode.csv`
- `*_plan_metrics.csv`
- 寻优模式额外输出 `*_best_plan_metrics.csv`

`prefill.csv` / `decode.csv` 是逐算子明细，典型字段：

```text
operator
op_type
module
layer_idx
execution_time_ms
kernel_launch_ms
compute_ms
memory_ms
network_ms
cube_compute_ms
vector_compute_ms
memory_traffic_bytes
weight
activation
kv_cache
flops
```

`*_plan_metrics.csv` 是单行汇总，典型字段：

```text
ttft_ms
tpot_ms
prefill_time_ms
decode_time_ms
prefill_kv_cache_bytes
decode_kv_cache_bytes
prefill_peak_memory_bytes
decode_peak_memory_bytes
tps
rps
execution_time_ms
throughput
request_per_second
is_valid
fail_reason
```

## 多 shape 支持

`run.py` 单任务不支持多 shape。`workload.yaml` 中：

- `seq_len` 是单值。
- `out_len` 是单值。
- `batch_size` 是单值。
- PD 分离时 `batch_size` 可以是 `[prefill_bs, decode_bs]`，但仍是一组 shape。

多 shape 有两条路径：

1. `run_optimal.py` 通过 `workload_library` 做搜索。
2. HitFloor adapter 自己循环调用单 shape，例如循环 `Task(...).execute()`。

对 HitFloor 更适合的是：

```text
ShapeMemo + 单 shape programmatic call / CLI call
```

不建议让 replay core 直接使用 `run_optimal.py`。

## batch size 定义

MkSim 中：

```text
batch_size = 并发请求数 / 同时在 batch 内的请求数
```

它不是：

- token 数。
- operator 维度。
- `max_num_batched_tokens`。

代码口径：

```text
batch_size -> StageConfig.concurrency -> ModelMetaData.global_batch_size
```

每卡 / 每 DP 组实际 batch 会按并行策略切分：

```text
per_dp_batch = ceil(global_batch_size / DP)
moe_batch_size = ceil(global_batch_size / (MOE_DP * EP))
```

### 对 HitFloor 的影响

当前 HitFloor `BatchShape.batch_size = request slice 数` 与 MkSim 的 batch size 在语义上基本一致，前提是：

```text
一个 ScheduledSlice 表示一个请求在一次 iteration 中的一段 prefill work
```

但 MkSim 的 workload shape 是单一 `seq_len/prefix_cache/batch_size`，这要求同一个 MkSim 调用中的 batch 请求具有统一 shape。HitFloor scheduler 可能生成 heterogeneous batch：

- 不同 request 的 `scheduled_prefill_tokens` 不同。
- 不同 request 的 `computed_tokens_before` 不同。
- 不同 request 的 `cached_tokens` 不同。

因此 HitFloor 不能简单把任意 `BatchShape` 直接传给 MkSim。需要在 adapter 层显式设计转换策略。

## prefill shape

MkSim prefill 需要：

```yaml
workload:
  batch_size: 4096
  seq_len: 8192
  out_len: 1024
  prefix_cache: 0
```

其中：

- `batch_size`: 并发请求数。
- `seq_len`: 输入 prompt 长度，包含已缓存部分。
- `prefix_cache`: 命中的前缀长度。
- `out_len`: 输出 token 数。

Prefill 实际计算 token 数：

```text
miss_tokens_per_request = seq_len - prefix_cache
total_prefill_compute_tokens = (seq_len - prefix_cache) * batch_size
```

约束：

```text
seq_len > prefix_cache
```

## PD Fusion 与混合 prefill/decode

PD Fusion 下 prefill 与 decode 必须使用相同 batch size。

MkSim 会分别构建 prefill 和 decode metadata，并串行计算：

```text
execution_time = ttft + tpot * out_len
```

MkSim 不建模真实 continuous batching 中同一个 iteration 内 prefill + decode 混跑。

对 HitFloor Step4：

- 第一版不建模 decode TPOT 对 prefill 的干扰。
- 可以只关注 MkSim prefill TTFT 相关输出。
- `scheduled_decode_tokens` 在 HitFloor 里仍为 0。

## chunked prefill

MkSim 核心仿真器不内建 chunked prefill loop，也不会把 chunk 自动当成独立请求。

用户提供的说明中提到，现有外层脚本通过多次 prefill 调用近似 chunked prefill：

```text
simulate_chunked_prefill:
  按 num_batched_tokens // batch_size 切 step
  对 cache=0 / 中段 / 近末三点探针
  线性插值累加各 chunk 延迟
```

对 HitFloor 来说，一个 chunk 应表达为一次 prefill shape：

```text
prefix_cache = chunk_start_context_tokens
seq_len = chunk_start_context_tokens + chunk_tokens
batch_size = chunk request count
```

这样：

```text
seq_len - prefix_cache = chunk_tokens
```

如果只传：

```text
seq_len = chunk_tokens
prefix_cache = 0
```

则会低估或改变命中前缀带来的 KV/cache memory 行为。

## 模型 profile

模型通过 `configs/models/*` yaml/json 指定。GLM-5 已有内置配置。

MkSim 需要模型结构参数作为 FLOPs 和访存公式输入，典型包括：

- hidden size
- vocab size
- num layers
- attention block type: MHA / GQA / MGA / MLA / SWA
- local heads
- qk head dim
- qk_nope head dim
- qk_rope head dim
- v head dim
- q lora rank
- kv lora rank
- sparse attention 配置
- dense MLP intermediate size
- MoE expert num
- top experts activation
- MoE intermediate dim
- shared expert 配置

这些不是可选项，是算子级公式的输入。

## 部署与并行策略

dtype 在 workload 的 quant config 中指定：

- compute dtype，例如 fp8 / bf16。
- weight storage dtype。

并行策略在 deploy config 中指定：

- TP
- DP
- EP
- SP
- CP
- MOE_DP
- MLP_TP
- OPROJ_TP

PP 当前存在枚举，但未真正支持，恒为 1。

attention backend 不是统一开关，而由 block type 决定。

## 硬件 profile

硬件通过 `configs/chips/*.yaml` 指定。字段包括：

```yaml
compute:
  cube:
    fp8: 989
    fp16: 494
    kernel_launch_us: 1.2
  vector:
    fp8: 120
    fp16: 200
    kernel_launch_us: 1.2
memory:
  hbm:
    capacity_gb: 80
    bandwidth_gb_s: 3350
    latency_ns: 600
  l2_cache:
    size_mb: 50
    bandwidth_gb_s: 10000
topo:
  intra_node:
    type: nvlink
    bandwidth_gb_s: 900
  inter_node:
    type: roce_v2
    bandwidth_gb_s: 50
```

MkSim 显式建模：

- cube/vector 算力。
- HBM capacity / bandwidth。
- L2 cache。
- 节点内/节点间互联。
- AllReduce / AllGather / ReduceScatter / All2All 通信。

MkSim 核心不建模 DDR KV load。

## cache 命中表达

MkSim 通过 `prefix_cache` 表达 prefix cache 命中：

```text
prefix_cache = 命中的前缀长度
miss_tokens = seq_len - prefix_cache
```

如果只想传实际计算 tokens：

```text
seq_len = miss_tokens
prefix_cache = 0
```

如果希望命中前缀也影响 KV/cache memory：

```text
seq_len = cached_tokens + miss_tokens
prefix_cache = cached_tokens
```

对 HitFloor 更合适的是后者，因为 HitFloor 需要 TTFT 中的 compute 部分尽量反映已缓存前缀对 attention/KV memory 的影响。

## KV load 与 DDR 边界

MkSim 能通过 roofline memory time 间接体现 HBM 内 KV 访存。

MkSim 不单独建模：

- DDR -> HBM 的 KV load。
- 跨层级 KV 加载/传输。
- prefill -> decode KV transfer time。

HitFloor 分工：

```text
compute latency: MkSim / AIConfigurator
DDR KV load latency: Ramulator2 或后续独立 backend
cache hit/miss generation: HitFloor
```

因此 MkSim adapter 不应宣称覆盖 DDR 命中加载时间。

## 输出单位与确定性

延迟单位是毫秒。

常见字段：

```text
execution_time_ms
ttft_ms
tpot_ms
compute_ms
kernel_launch_ms
memory_ms
network_ms
```

阶段时间是逐算子 `execution_time_ms` 求和。算子 device time 近似为 roofline 结果：

```text
operator_time = max(compute_time, memory_time) + kernel_launch
```

包含：

- 通信时间。
- kernel launch overhead。

不包含：

- 请求排队。
- service scheduler/host overhead。
- continuous batching timeline overlap。
- prefill->decode KV transfer。

MkSim 是确定性解析式仿真器，同输入输出一致，无需 seed。

## 工程约束

| 项 | 情况 |
| :--- | :--- |
| 安装大小 | 源码较小，纯 Python |
| 依赖 | `pyyaml`, `pydantic>=2`, `tqdm`, `colorama` |
| Python | 需要 Python 3.11+ |
| GPU/NPU | 不需要 |
| 离线运行 | 可以 |
| License | MIT，但内置公司硬件 profile 可能有保密边界 |
| 单次调用耗时 | 单任务秒级；寻优随搜索空间线性增长 |

## HitFloor 接口影响

### 1. 当前 BatchShape 不应直接等同于 MkSim input

HitFloor `BatchShape` 是 scheduler output，包含每个 request slice。

MkSim input 是 uniform workload shape：

```text
batch_size
seq_len
prefix_cache
out_len
model/deploy/chip/op config
```

因此需要区分：

```text
SchedulerBatchShape
SimulatorPrefillInput
```

或者保留 `BatchShape` 作为 scheduler output，再新增 adapter 层转换模型。

### 2. Heterogeneous batch 需要明确转换策略

HitFloor scheduler 可能产生 heterogeneous batch，MkSim 单次调用不直接支持。

可选策略：

1. **strict uniform**
   - 只有所有 request slice 的 `scheduled_prefill_tokens` 和 `computed_tokens_before` 相同，才允许调用 MkSim。
   - 优点：语义干净。
   - 缺点：真实 trace 下可能频繁失败。

2. **max-shape padding**
   - 用 batch 内最大 `seq_len` 和最大 `prefix_cache` 近似整批。
   - 优点：一个 iteration 一个 MkSim call。
   - 缺点：可能高估，且 prefix/miss token 关系可能失真。

3. **group-by-shape**
   - 按 `(seq_len, prefix_cache, scheduled_prefill_tokens)` 分组，多次调用 MkSim。
   - 优点：比 max-shape 更精确。
   - 缺点：如何合并多个 group duration 需要定义；求和可能高估，取 max 可能低估或遗漏串行开销。

4. **formula-only Step4, MkSim adapter later**
   - Batch C 继续使用 formula backend。
   - MkSim adapter 在统一 simulator interface 审批后再接入。
   - 优点：不阻塞 replay engine。
   - 缺点：Batch C 数据结构仍要提前保留 per-slice 信息。

当前建议：在没有 AIConfigurator 口径前，不最终选择转换策略。

### 3. chunked prefill 表达应保留 context

HitFloor chunked prefill 的一个 slice 应转换为：

```text
seq_len = computed_tokens_before + scheduled_prefill_tokens
prefix_cache = computed_tokens_before
```

如果该 slice 还包含 HBM prefix cache hit，则需要进一步区分：

```text
cached_tokens_from_prefix_cache
computed_tokens_from_previous_chunks
scheduled_prefill_tokens
```

当前 `ScheduledSlice` 只有 `computed_tokens_before` 和 `cached_tokens`，后续接口设计需要检查是否足以区分这两类 context。

### 4. out_len 仍需决策

MkSim workload 需要 `out_len`。HitFloor Step4 不建模 decode TPOT。

可选策略：

- adapter 中设置固定 `out_len=1`，只读取 prefill/ttft。
- 使用请求参数中的 `max_tokens`。
- 在 model/hardware profile 配置里显式设置 `out_len_for_prefill_backend`。

需要结合 AIConfigurator 手册后统一决定。

## 待确认问题

1. HitFloor 是否为 MkSim adapter 采用 internal API，而不是 CLI。
2. MkSim adapter 是否允许 heterogeneous batch；如果允许，采用哪种转换策略。
3. `out_len` 在 TTFT-only 估算中如何设定。
4. 是否需要把 `computed_tokens_before` 拆成：
   - `cached_prefix_tokens`
   - `previous_chunk_tokens`
5. MkSim 的 `prefix_cache` 应只表达 prefix cache hit，还是也表达 chunk carry-over context。
6. AIConfigurator 是否支持 per-request heterogeneous batch；如果支持，HitFloor 公共接口应向 AIConfigurator 靠拢还是向 MkSim uniform shape 靠拢。

