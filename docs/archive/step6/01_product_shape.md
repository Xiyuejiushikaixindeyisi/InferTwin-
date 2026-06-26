# Step6 Product Shape

Step6 主题：

```text
HBM Cache Capacity Sweep Report
```

本文定义 Step6 产品形态。具体技术路线和代码方案见 `docs/archive/step6/02_technical_route_and_code_plan.md`。

## 1. 产品目标

Step1-Step5 已完成单次 replay 骨架：

```text
trace + tokenizer + scheduler + latency backend + cache config
-> request_metrics.csv / iteration_metrics.csv / cache_events.csv / summary.md
```

Step6 的目标是在此基础上形成最小可用的 cache capacity sweep report：

```text
给定一个 trace
给定一组 HBM cache capacity candidates
对每个 cache capacity 进行 batch_aware_hbm_lru replay
统计该 capacity 下的 KV cache hit 和 P90 TTFT
输出总 trace 级别和每实例级别的 sweep 表
```

最终用户直接使用这张表：

```text
cache capacity
总 trace KV cache hit
总 trace P90 TTFT
每个实例 KV cache hit
每个实例 P90 TTFT
```

Step6 第一版不根据 P90 TTFT target 自动求“最低 hit rate”，也不做 binary search。用户可以基于 sweep 表自行判断目标 TTFT 下需要的 cache 容量和 hit 水位。

## 2. 产品定位

Step6 是 HitFloor 从“单次 replay 仿真骨架”走向“容量敏感性分析输出”的第一步。

它仍然是离线仿真：

- 不部署模型。
- 不调用真实 vLLM / vLLM-Ascend。
- 不下载或强制接入 AIConfigurator / MkSim。
- 不仿真 gateway routing。
- 不仿真实例侧复杂排队策略。
- 不仿真 DDR / SSD 多级缓存。

它使用 Step5 已冻结的 replay 语义：

- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching / chunked prefill approximation。
- fitted TTFT backend。
- finite HBM LRU cache。
- finish-time materialization。
- hash-only prefix block metadata。

## 3. 使用方式

用户提供一个 capacity sweep config：

```bash
PYTHONPATH=src python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml
```

或本地 wrapper：

```bash
python scripts/run_capacity_sweep.py --config configs/experiments/step6_capacity_sweep.yaml
```

说明：

- `hitfloor sweep` 是 Step6 建议新增的正式 CLI 子命令。
- `scripts/run_capacity_sweep.py` 只是 wrapper，不承载核心业务逻辑。
- 如果本轮暂不新增 wrapper，也必须保证 package CLI 可用。

## 4. 输入

### 4.1 Trace 输入

沿用 Step1-Step5 trace schema：

```text
request_id
tenant_id
instance_uuid
request_params
service_start_time
```

约束：

- `instance_uuid` 必须存在。
- Step6 不做 gateway routing simulation。
- 请求已经在 trace 中固定路由到实例。

### 4.2 Request Params

沿用当前 strict parser：

- `model`: non-empty string。
- `messages`: list of object。
- `messages[*].role`: non-empty string。
- `messages[*].content`: required。
- `tools`: list of object，缺省为空 list。

未知或不符合 schema 的 request 不做启发式兼容，应显式失败。

### 4.3 Tokenizer / Chat Template

沿用 tokenizer registry：

```yaml
tokenizers:
  root: tokenizers
  default_profile: glm-v5
  cache_scope: tenant_isolated
```

Step6 不改变 tokenizer / chat template 管理方式。

### 4.4 Capacity Candidates

Step6 第一版只 sweep HBM capacity blocks：

```yaml
sweep:
  hbm_capacity_blocks: [512, 1024, 2048, 4096, 8192]
```

语义：

- 每个 candidate 都会触发一次完整 replay。
- capacity 单位是 Step5 已冻结的 `hbm_capacity_blocks`。
- eviction policy 固定为 LRU，或显式配置为 `lru`。

不使用 HBM GB：

- GB 到 blocks 需要模型 KV bytes、block size、dtype、layers 等更完整的内存换算。
- Step5 已冻结 capacity 第一版为 blocks。
- 如需 GB 输入，后续新增 converter，不改变 Step6 第一版 schema。

### 4.5 Scheduler / Latency

沿用 Step4/Step5 配置：

```yaml
scheduler:
  policy: fcfs
  max_num_batched_tokens: 8192
  max_num_seqs: 32
  enable_chunked_prefill: true
  long_prefill_token_threshold: 4096

latency:
  backend: fitted_ttft
  model_name: glm-v5
  hardware_name: ascend910c
  fitted_ttft:
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 0.02
```

Step6 不改变 `batch_size`、`BatchShape` 或 fitted TTFT 语义。

## 5. 配置形态

建议新增示例配置：

```text
configs/experiments/step6_capacity_sweep.yaml
```

示例：

```yaml
experiment:
  name: step6_capacity_sweep
  description: Replay one trace over multiple HBM cache capacities.

simulation:
  mode: capacity_sweep

trace:
  path: data/samples/sample_trace.csv

tokenizers:
  root: tokenizers
  default_profile: glm-v5
  cache_scope: tenant_isolated

cache:
  block_size_tokens: 16
  eviction_policy: lru

scheduler:
  policy: fcfs
  max_num_batched_tokens: 8192
  max_num_seqs: 32
  enable_chunked_prefill: true
  long_prefill_token_threshold: 4096

latency:
  backend: fitted_ttft
  model_name: glm-v5
  hardware_name: ascend910c
  fitted_ttft:
    profile: glm-v5_ascend910c_default
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 0.02
    calibrated_from: manual_default

sweep:
  hbm_capacity_blocks: [512, 1024, 2048, 4096, 8192]
  parallel_instances: false

output:
  directory: reports/step6_capacity_sweep
  cache_events: false
```

## 6. 输出

Step6 输出目录：

```text
reports/step6_capacity_sweep/
```

### 6.1 `capacity_sweep.csv`

核心产品输出。

Step6 第一版建议使用 long table，而不是为每个实例动态生成列。这样 schema 稳定，实例数量变化时不改变表结构。

建议 schema：

```text
hbm_capacity_blocks
scope
instance_uuid
request_count
iteration_count
total_prompt_tokens
hbm_hit_tokens
ddr_hit_tokens
miss_tokens
total_hit_tokens
kv_hit_rate
hbm_hit_rate
ddr_hit_rate
p50_ttft_ms
p90_ttft_ms
p99_ttft_ms
cache_event_count
```

语义：

- `scope`: `trace` 或 `instance`。
- `instance_uuid`: 当 `scope=trace` 时为空；当 `scope=instance` 时为实例 uuid。
- 每个 `hbm_capacity_blocks` 至少输出一行 `scope=trace`。
- 每个 `hbm_capacity_blocks` 同时输出该 capacity 下每个实例的一行 `scope=instance`。
- 总 trace 级别 `kv_hit_rate = total_hit_tokens / total_prompt_tokens`。
- 每实例级别 `kv_hit_rate` 使用该实例内的 hit tokens 和 prompt tokens 计算。
- `ddr_hit_tokens` / `ddr_hit_rate` 在 Step6 中恒为 0，因为 Step6 不实现 DDR；保留字段是为了后续多级缓存扩展时不重新定义主表口径。

示意：

```text
hbm_capacity_blocks,scope,instance_uuid,kv_hit_rate,p90_ttft_ms
512,trace,,0.42,850.0
512,instance,instance-a,0.51,720.0
512,instance,instance-b,0.33,980.0
1024,trace,,0.57,690.0
1024,instance,instance-a,0.62,610.0
1024,instance,instance-b,0.50,770.0
```

### 6.2 `summary.md`

面向用户阅读的总结。

内容包括：

- trace path。
- mode。
- capacity candidates。
- latency backend。
- scheduler config。
- 总 trace 在不同 capacity 下的 KV hit / P90 TTFT 摘要。
- 每实例在不同 capacity 下的 KV hit / P90 TTFT 摘要。
- 未实现项说明。

### 6.3 单次 replay 明细

Step6 第一版默认不保存每个 capacity 的完整 request / iteration 明细。

原因：

- sweep 会多次 replay。
- 如果每个 candidate 都写全量 `request_metrics.csv`、`iteration_metrics.csv`、`cache_events.csv`，输出会快速膨胀。

可选后续配置：

```yaml
output:
  save_replay_details: false
```

第一版可以暂不实现该配置，只输出 sweep summary 表。

## 7. Sweep 算法

Step6 第一版采用用户给定 capacity candidates 的 grid sweep：

```text
build SimulationRequest list once

for capacity in hbm_capacity_blocks:
    run batch_aware_hbm_lru replay
    collect request metrics and cache stats
    aggregate trace-level KV hit and TTFT
    aggregate per-instance KV hit and TTFT
    append rows to structured sweep result

report/export layer writes capacity_sweep.csv
```

不做：

- P90 TTFT target matching。
- binary search。
- 自动外推容量。
- 自动给“最低命中率”结论。

原因：

- 用户需要的是容量、KV hit、P90 TTFT 的完整关系表。
- capacity 增大时，P90 TTFT 通常下降，但不承诺严格单调。
- replay 中存在 batching、chunked prefill、finish-time materialization、eviction 等离散行为。
- grid sweep 的所有中间点都能输出到 `capacity_sweep.csv`。

## 8. 统计口径

### 8.1 KV Cache Hit

总 trace 级别：

```text
kv_hit_rate =
    sum(hbm_hit_tokens + ddr_hit_tokens) / sum(total_prompt_tokens)
```

Step6 中：

```text
ddr_hit_tokens = 0
kv_hit_rate = hbm_hit_tokens / total_prompt_tokens
```

每实例级别：

```text
instance_kv_hit_rate =
    sum(instance hbm_hit_tokens + instance ddr_hit_tokens)
    / sum(instance total_prompt_tokens)
```

### 8.2 P90 TTFT

总 trace 级别：

```text
p90_ttft_ms = percentile(all request ttft_ms, 90)
```

每实例级别：

```text
instance_p90_ttft_ms =
    percentile(request ttft_ms where request.instance_uuid == instance_uuid, 90)
```

### 8.3 Token Counts

字段说明：

- `total_prompt_tokens`: 当前 scope 的 prompt token 总数。
- `hbm_hit_tokens`: 当前 scope 的 HBM hit token 总数。
- `ddr_hit_tokens`: 当前 scope 的 DDR hit token 总数，Step6 为 0。
- `miss_tokens`: 当前 scope 的 miss token 总数。
- `total_hit_tokens = hbm_hit_tokens + ddr_hit_tokens`。

不变量：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == total_prompt_tokens
```

该不变量应在测试中覆盖。

## 9. Cache Events 控制

Step6 sweep 默认关闭 cache event 明细：

```yaml
output:
  cache_events: false
```

原因：

- sweep 会运行多个 capacity candidates。
- Step5 已确认 `cache_events.csv` 是单次 replay 标准输出，但 sweep 场景默认写完整 event 明细会导致输出体积过大。
- sweep 只需要 aggregate `CacheEventStats`。

语义：

- `cache_events: false`: 使用 stats-only path，不写 `cache_events.csv`。
- `cache_events: true`: 只允许对指定 capacity 开启 event dump，不支持对所有 capacity 默认输出明细。

第一版建议：

- sweep 默认不写 cache events 明细。
- 保留每个 capacity 的 `cache_event_count` aggregate。
- 如需调试，只对 `cache_event_capacities` 中列出的 capacity 输出 `capacity_<N>/cache_events.csv`。
- 不改变 Step5 单次 `batch_aware_hbm_lru` runner 的默认行为。

## 10. 多实例并行 Replay

Step6 配置预留：

```yaml
sweep:
  parallel_instances: false
```

第一版建议默认 false。

原因：

- 当前多实例 replay 是 deterministic serial replay。
- 并行 replay 需要稳定合并 request metrics、iteration metrics 和 cache stats。
- sweep 第一版更需要可复现性和简单审查。

Step6 第一版不实现可选并行；`parallel_instances: true` 应直接 config guard 失败。

单线程 sweep 稳定后，再新增 `ParallelCapacitySweepRunner` 或显式 execution backend，并保证输出 deterministic merge。

## 11. Request Streaming Build

Step6 第一版不做真正 streaming build。

原因：

- sweep 需要对同一批 requests 多次 replay。
- 如果 streaming build 每个 capacity candidate 都重新 parse/tokenize/hash，会显著增加总耗时。
- 当前更合理的第一版是：

```text
build SimulationRequest list once
reuse immutable requests across capacity sweep
```

后续如 trace 超大导致内存压力，再设计：

- per-instance request shard。
- on-disk intermediate representation。
- streaming parse + reusable encoded blocks。

这些属于后续性能架构阶段，不进入 Step6 第一版。

## 12. 不做什么

Step6 第一版明确不做：

- P90 TTFT target matching。
- hit floor 自动求解。
- binary search / 自动外推 capacity。
- DDR LRU。
- SSD tier。
- HBM/DDR/SSD KV load latency。
- gateway routing simulation。
- instance-side queueing policy simulation。
- cross-instance KV pooling。
- Mooncake pooling。
- sparse attention cache manager。
- progressive block materialization。
- physical KV slot allocation。
- pinned / refcount。
- true streaming request build。
- external AIConfigurator / MkSim production adapter。

## 13. 成功标准

Step6 产品完成后，用户应能：

1. 用一个 sweep config 跑完整 HBM capacity sweep。
2. 得到 `capacity_sweep.csv`。
3. 得到 `summary.md`。
4. 对每个 capacity 看到总 trace 的 KV hit rate 和 P90 TTFT。
5. 对每个 capacity 看到每个实例的 KV hit rate 和 P90 TTFT。
6. 看到 HBM hit tokens、miss tokens、total prompt tokens 等细节统计。
7. 确认 sweep 没有写爆 cache event 明细。

## 14. 审批点

请重点 review：

1. Step6 是否确认以 `HBM Cache Capacity Sweep Report` 为主题。
2. 是否接受第一版只 sweep `hbm_capacity_blocks`，不接受 GB 输入。
3. 是否接受第一版只输出 capacity 与指标关系表，不做 P90 target matching。
4. 是否接受标准用户导出为一张 long-format `capacity_sweep.csv`，用 `scope=trace/instance` 表达总 trace 和每实例指标。
5. 是否接受 Step6 输出中保留 `ddr_hit_tokens` / `ddr_hit_rate` 字段但恒为 0，用于未来多级缓存扩展。
6. 是否接受 sweep 默认关闭 cache event 明细。
7. 是否接受 request build once、capacity sweep reuse requests，不做 true streaming build。
8. 是否接受多实例并行 replay 先作为可选/后续项，不影响第一版 sweep。

审批通过后，进入 Step6 技术路线和代码方案。
