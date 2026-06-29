# Step8 技术路线：KV Load Latency

状态：待用户评审。

本文是 Step8 的高优先级技术路线文档。`03_step8_technical_route.md` 仅作为旧版参考；如两者冲突，以本文为准。

本轮任务类型：核心仿真器。

改动等级：L3 核心 replay / latency 改动。Step8 会改变 DDR/CPU hit 请求的 `finish_time`、`ttft_ms` 和 iteration duration，但不改变 Step7 已确认的 HBM / DDR / miss token accounting。

## 1. Step8 目标

Step7 已经让 InferTwin 能在单实例内区分：

```text
hbm_hit_tokens
ddr_hit_tokens
miss_tokens
```

但 Step7 只做 tier hit accounting。DDR/CPU 命中的 KV 仍需要加载到可计算位置，真实服务中这部分会影响 TTFT。Step8 的目标是把非 HBM 命中的 KV load latency 显式纳入 replay timeline：

```text
ttft_ms =
  scheduler_wait_ms
  + prefill_compute_ms(miss_tokens)
  + kv_load_ms(ddr_hit_tokens / ddr_load_bytes)
```

核心目标：

- 让 DDR/CPU hit 继续减少 prefill compute tokens。
- 让 DDR/CPU hit 产生显式 `kv_load_ms`。
- 让 `kv_load_ms` 进入 iteration duration，进而影响 request finish time、TTFT 和 P90。
- 保持 HBM hit 近似 0 load latency。
- 为 Step9 progressive chunk/block visibility 预留 chunk/iteration 级 latency 结构。

## 2. 核心仿真器还是外围能力

Step8 属于核心仿真器。

原因：

- 它修改 replay-facing latency shape。
- 它修改 request finish time 和 TTFT。
- 它要求 typed metrics 增加 KV load 字段。
- 它影响 `sweep-streaming` 这类外围能力消费到的结果。

外围能力只需要展示新增 typed result，例如 `kv_load_ms`、`kv_load_tokens`、`kv_load_bytes`，不得在 report/export 中重算 KV load latency。

## 3. 输入变化

### 3.1 Trace 输入

Step8 不改变 trace CSV schema。

继续沿用 routed trace：

```text
request_id
tenant_id
instance_uuid
request_params
service_start_time
```

要求：

- 核心 trace reader 继续 fail-fast 拒绝空 `instance_uuid`。
- 无实例 ID 的 trace 仍由外围 normalizer 先补统一实例 ID；这不是 gateway routing。
- `streaming.require_sorted_trace=false` 仍在 V1 禁用。

### 3.2 Request build 输入

Step8 不改变 tokenizer / chat template / prefix hash 输入。

Step8 需要复用 request build 阶段已有或未来可补充的模型信息来计算 `kv_load_bytes`：

```text
kv_bytes_per_token
bytes_per_block
runtime_block_size
effective_block_size
```

若当前 request/block 只可靠提供 token 数，Step8 v1 可以先支持 token-linear KV load；byte-linear KV load 必须在 bytes 信息缺失时 fail-fast，不能静默猜测。

### 3.3 Config / Profile 输入

Step8 应正式化 `kv_load` profile。当前 `InstanceLatencyProfile.kv_load` 已有占位字段：

```yaml
kv_load:
  ddr_ms_per_cached_token: 0.0
  remote_ms_per_cached_token: 0.0
```

Step8 v1 建议升级为显式模式 schema，保持默认 0 兼容：

```yaml
kv_load:
  mode: zero                 # zero | token_linear_v1 | byte_linear_v1
  aggregation: shared_link_sum
  ddr_fixed_overhead_ms: 0.0
  ddr_ms_per_cached_token: 0.0
  ddr_ms_per_byte: 0.0
  calibrated_from: manual_default
  overlap_mode: none_v1
  transfer_path: local_ddr_cpu
```

语义：

- `mode=zero`：保持当前行为，DDR hit 不增加 load latency，用于兼容和基线。
- `token_linear_v1`：按 `kv_load_tokens` 线性估算。
- `byte_linear_v1`：按 `kv_load_bytes` 线性估算。
- `aggregation=shared_link_sum`：同一 iteration 内按实例汇总 load bytes/tokens 后估算一次 load latency。
- `overlap_mode=none_v1`：Step8 v1 不建模 compute/load overlap。
- `transfer_path` 仅记录 profile 来源和解释口径，不在 v1 中切换真实传输实现。

配置来源优先级延续现有规则：

```text
instance latency profile -> model default latency -> legacy global backend
```

但 Step8 只允许全局 fallback 用于未配置 instance profile 的兼容路径。只要显式启用 instance/model profile，缺失实例绑定仍应 fail-fast。

## 4. 输出变化

### 4.1 Request metrics

建议新增：

```text
kv_load_tokens
kv_load_bytes
kv_load_ms
prefill_compute_ms
queue_ms
ttft_ms
```

其中：

- `kv_load_tokens` v1 等于 accounted `ddr_hit_tokens`。
- `kv_load_bytes` 来自 DDR hit blocks / bytes-per-token；若不可得且使用 token-linear，可为 0 并标注 unavailable。
- `kv_load_ms` 是非 HBM KV load 对 request TTFT 的贡献。
- `prefill_compute_ms` 来自 fitted TTFT component，便于解释 TTFT 分解。
- `queue_ms` 仍为当前 replay scheduler waiting 语义，不是实例入口真实排队。

### 4.2 Iteration metrics

建议新增：

```text
kv_load_tokens
kv_load_bytes
kv_load_request_count
kv_load_ms
prefill_compute_ms
queue_ms
```

这些字段应来自 `LatencyResult.details` 和 `BatchShape`，而不是 report 层重算。

### 4.3 Capacity sweep typed result

`sweep-streaming` 是 Step8 的主要验收入口，但它是外围报告能力。核心 typed result 需要暴露足够字段，让 report/export 只做展示。

建议 `CapacitySweepRow` 增加：

```text
total_kv_load_ms
avg_kv_load_ms
p50_kv_load_ms
p90_kv_load_ms
p99_kv_load_ms
```

是否把这些字段直接写入 `capacity_sweep.csv` 属于外围展示改动，但建议跟随 Step8 验收一起做，避免新增核心能力不可观察。

## 5. 对核心链路的影响评估

### 5.1 trace schema guard

影响：无 schema 新字段。

保持：

- routed trace 必须有非空 `instance_uuid`。
- 大 trace 主路径仍是 `sweep-streaming`。
- sorted trace guard 不变。

Step8 不应在 trace guard 中引入 KV load 逻辑。

### 5.2 request build

影响：轻微扩展。

request build 需要保证后续能拿到 KV load bytes 所需信息：

- token count 已有。
- block hash 已有。
- block token_count 已有。
- bytes-per-token / bytes-per-block 可能需要来自 model profile 或 block metadata。

Step8 v1 可以先以 token-linear 模式落地；byte-linear 模式必须要求 bytes schema 明确存在。

### 5.3 tokenizer / chat template

影响：无。

Step8 不修改 tokenizer registry、chat template、long request rejection 和 request parser。

### 5.4 prefix block hash

影响：无。

Step8 不修改 block key、block boundary、runtime/effective block size conversion 和 cached token accounting。`ddr_hit_tokens` 的产生仍由 Step7 的 tiered cache + vLLM-like accounting 决定。

### 5.5 scheduler replay

影响：中等，属于 L3。

Step8 不改变 admission / chunk selection 规则，但需要把首次可执行 iteration 的 DDR hit 变成 `kv_load_tokens`：

```text
first scheduled slice for request:
  kv_load_tokens = lookup.ddr_hit_tokens
  kv_load_bytes = bytes(ddr_hit_blocks)

later slices:
  kv_load_tokens = 0
  kv_load_bytes = 0
```

这样可以避免同一请求多 chunk 重复收取 KV load latency。

### 5.6 cache lookup / materialization / eviction

影响：不改变现有语义。

保持：

- HBM lookup first。
- DDR lookup second。
- miss blocks finish-time materialization。
- materialize 同时写 HBM 和 DDR。
- LRU eviction policy 不变。
- 不做 DDR hit promotion。

Step8 只消费 lookup 结果，不改变 lookup / materialization / eviction 的状态转移。

### 5.7 latency backend

影响：核心改动。

当前 `ServingLatencyProfile` 已预留：

```text
ttft_backend
queue_component
kv_load_component
```

Step8 应新增真正的 `KVLoadLatencyComponent` 实现，让 replay duration 变成：

```text
iteration_duration_ms =
  queue_ms
  + prefill_compute_ms
  + kv_load_ms
```

同时 `ShapeKey` 必须加入：

```text
kv_load_tokens
kv_load_bytes
kv_load_request_count
```

否则相同 prefill shape、不同 DDR load shape 会错误复用 memoized latency。

### 5.8 per-instance isolation

影响：必须保持。

KV load component 由 `InstanceLatencyBackendResolver` 按 `instance_uuid` 解析。不同实例可以共享模型配置，但拥有不同 TTFT / KV load 超参数。

Step8 不引入跨实例带宽共享，不引入跨实例 KV hit，不引入 remote pooling。每个实例 replay 和 KV load latency 仍互不干扰。

### 5.9 typed metrics / typed result

影响：中等。

新增 KV load 字段应贯穿：

```text
BatchShape
ShapeKey
LatencyResult.details
IterationMetrics
BatchAwareRequestMetrics
StreamingMetricAggregator
CapacitySweepRow
report/export
```

report/export 只读取 typed fields，不自行重算。

## 6. 与现有 V1 replay 语义的关系

Step8 保持这些 V1 语义不变：

- fixed-routing、多实例隔离 replay。
- batch size = iteration 内 request slice 数。
- `max_num_batched_tokens` = iteration token budget。
- first-schedule-time prefix lookup。
- vLLM-like cached_tokens accounting。
- HBM / DDR / miss token 不变量。
- finish-time materialization。
- stateful LRU eviction。
- true streaming request source 和 streaming metric aggregation。
- HBM-only zero-miss immediate finish。

Step8 修改这些语义：

- DDR hit 不再等价于 0 latency。
- `miss_tokens == 0 and ddr_hit_tokens > 0` 不再 immediate finish，而是 load-only finish。
- latency memoization key 包含 KV load shape。
- request / iteration / sweep metrics 显式输出 KV load 分量。

Step8 不解决 Step9 的 progressive visibility。也就是说：

```text
finish-time materialization 仍可能低估长 prefill 期间的 block reuse。
```

这个问题必须在 Step9 通过新 replay/cache mode 解决，而不是在 Step8 偷偷改变 materialization timing。

## 7. 不做什么

Step8 v1 不做：

- 不做 Ramulator2 online replay。
- 不把 Mooncake / Ramulator2 放进默认 replay 主路径。
- 不做 memory request / cacheline / DRAM address 级仿真。
- 不做 KV load queue / backpressure。
- 不做 compute/load overlap。
- 不做 layerwise KV load pipeline。
- 不做 DDR hit promotion 到 HBM。
- 不做 load completion event。
- 不做 SSD tier。
- 不做 remote cache tier。
- 不做跨实例 KV hit / 多实例池化。
- 不做 gateway routing。
- 不做实例入口真实排队。
- 不做 Decode / TPOT。
- 不做 Hybrid/Mamba/MLA physical cache group 精确 layout。

这些不是否定方向，而是避免 Step8 v1 过大。需要时应进入 Step9、V2 或独立存储/通信专项。

## 8. 风险与边界

### 8.1 保守加和可能高估 TTFT

真实 vLLM connector 支持 async load，KV load 可能与 prefill compute overlap。但当前 InferTwin 仍没有 layer-level compute timeline，强行扣 overlap 容易制造虚假精度。

Step8 v1 建议：

```text
overlap_mode = none_v1
duration = compute + kv_load
```

后续可新增：

```text
max_compute_or_load_v1
layerwise_pipeline_v2
measured_profile_v2
```

### 8.2 request-level 一次性 load 是近似

源码显示真实 load 可能是：

```text
request/block keys
-> multi-buffer slices
-> TransferRequest list
-> transport-specific slice / fragment / queue
```

Step8 v1 用 iteration-level shape 聚合，是为了和当前 replay 时间单位对齐。它不会表达对象级 pin/lease、replica placement、offload/promotion 等 Mooncake 行为。

### 8.3 byte-linear 需要模型 KV bytes 口径

同样 token 数在不同模型上 KV bytes 不同。若 bytes 口径缺失，byte-linear 不应猜测。

### 8.4 per-instance latency 不等于完整异构 replay

Step8 让不同实例拥有不同 TTFT / KV load 超参数，但不表示已经支持完整 per-instance scheduler/cache/deployment 异构 replay。调度参数、cache capacity、block size 等仍应通过模型/部署 profile 的既有机制逐步贯穿。

### 8.5 大 trace 事件安全

Step8 不应恢复全量事件内存持有。cache events 继续走 stats-only 或 streaming writer。

## 9. 与 vLLM / vLLM-Ascend / Mooncake 的对比要求

需要对比，而且已经有 Step8 调研基础：

- vLLM CPU offload：按 block id 批量 copy，使用 DMA / stream，可能与 compute overlap。
- vLLM MooncakeConnector：按 request/block region 生成 transfer descriptors，默认 RDMA 类路径。
- vLLM-Ascend AscendStore：`batch_get_into_multi_buffers`，可 sync / async / layerwise。
- Mooncake Store：per-key replica selection，local/remote/disk/NoF 路径不同，TransferEngine 无统一 request 级公平调度语义。
- Ramulator2：适合作为 DRAM 标定来源，不适合作为默认在线 replay 依赖。

Step8 代码开发前不需要继续 clone 或 vendor 外部项目。只有当用户要求做 calibration harness 或更细 transfer model 时，才需要进入新的调研/adapter batch。

## 10. 建议 Batch 开发顺序

每个 batch 都必须先提交独立代码开发方案，经用户审批后再写业务代码。

### S8-A：KV-load Shape Schema

目标：

- 扩展 `ScheduledSlice` / `BatchShape`。
- 扩展 `ShapeKey`。
- 增加非负校验。
- 默认值为 0，保证 HBM-only 旧路径兼容。

验收：

- 不同 `kv_load_tokens` / `kv_load_bytes` 产生不同 shape key。
- HBM-only shape 仍为 0 load。
- 旧 HBM-only replay 测试不受影响。

### S8-B：KVLoadLatencyComponent

目标：

- 新增 `latency/kv_load.py`。
- 实现 zero / token-linear / byte-linear component。
- 将 `KVLoadLatencyProfile` schema 升级为显式 mode。
- `ServingLatencyProfile` 接入真实 KV load component。

验收：

- `mode=zero` 返回 0。
- token-linear 单调随 tokens 增加。
- byte-linear 单调随 bytes 增加。
- byte-linear 缺少 bytes 时 fail-fast。

### S8-C：Instance / Model Resolver Integration

目标：

- `InstanceLatencyBackendResolver` 返回包含 TTFT + KV load component 的 serving profile。
- model default latency 能作为实例缺省。
- 保持 instance profile -> model default -> global backend 优先级。

验收：

- 同一模型不同实例可以有不同 KV load 超参数。
- 缺失显式实例绑定仍 fail-fast。
- 未启用 instance/model profile 时 legacy global backend 兼容。

### S8-D：Replay Integration

目标：

- lookup tier split 进入 first scheduled slice。
- 同一请求只收一次 DDR KV load。
- iteration duration 包含 `kv_load_ms`。
- 修正 `miss_tokens == 0 and ddr_hit_tokens > 0` 的 load-only path。

验收：

- HBM-only zero-miss immediate finish 保持不变。
- DDR-only zero-miss request 的 TTFT > 0。
- DDR hit tokens 增加时 TTFT 随 KV load 参数增加。
- HBM/DDR/miss token accounting 不变。

### S8-E：Streaming Metrics / Typed Result

目标：

- streaming replay 输出 request / iteration KV load metrics。
- capacity sweep typed result 聚合 KV load 分量。
- report/export 只展示 typed result。

验收：

- 合成 trace 三种 capacity 下，`kv_load_ms` 与 DDR hit 变化一致。
- trace row 和 instance row 都能解释 TTFT 中 KV load 分量。
- 大 trace event sink 不退化为全量内存事件。

### S8-F：Ramulator2 / Mooncake Calibration Boundary

目标：

- 不接默认 replay。
- 在 `external/` 或 docs 中明确 calibration 输入/输出边界。
- 说明如何把 Ramulator2、Mooncake 压测或实机观测拟合成 `kv_load` profile。

验收：

- 不安装外部 simulator 时，InferTwin 测试不受影响。
- 文档说明 `calibrated_from` 的可用取值和含义。
- adapter/harness 若实现，必须 opt-in。

### S8-G：Review / Docs / Archive

目标：

- 做 Step8 专项 review。
- 更新产品设计、核心技术路线、agent context、global memory。
- 评估是否具备进入 Step9 的条件。
- 将 `docs/step8/` 移入 archive。

验收：

- 明确 Step8 对 replay 处理逻辑做了哪些改变。
- 明确与真实 vLLM / vLLM-Ascend / Mooncake 的剩余差异。
- 给出 Step9 progressive visibility 的准入判断。

## 11. 测试策略

### 11.1 单元测试

建议新增：

```text
tests/unit/scheduler/test_batch_shape_kv_load.py
tests/unit/latency/test_kv_load_latency.py
tests/unit/latency/test_serving_latency_profile_kv_load.py
tests/unit/latency/test_instance_resolver_kv_load.py
tests/unit/replay/test_zero_miss_kv_load.py
```

重点覆盖：

- shape schema 非负校验。
- shape key memoization。
- token-linear / byte-linear 单调性。
- missing bytes fail-fast。
- zero-miss DDR load-only path。

### 11.2 集成测试

建议新增：

```text
tests/integration/test_step8_streaming_kv_load_e2e.py
tests/integration/test_step8_capacity_sweep_kv_load_report.py
```

合成数据应覆盖：

- HBM-only hit：`kv_load_ms == 0`。
- DDR hit：`kv_load_ms > 0`。
- miss：prefill compute 增加。
- 同一请求多 chunk：KV load 只收一次。
- 多实例隔离：实例 A/B 使用不同 KV load 超参数。
- capacity sweep：容量变化导致 HBM/DDR/miss 和 P90 TTFT 可解释变化。

### 11.3 回归测试

必须确认：

- Step7 HBM/DDR hit accounting 不变。
- cache event stats 不变或只增加明确的新 latency metric，不改变 event 顺序。
- `sweep-streaming` 主路径可跑通。
- legacy small-trace `simulate` / `sweep` 不被无意破坏；如果暂不接 Step8 字段，应明确保持 HBM-only / zero KV load 兼容。

### 11.4 阶段收口测试

Step8 收口前建议运行：

```text
ruff
targeted pytest
streaming E2E synthetic trace
capacity sweep synthetic trace
git diff --check
```

若时间允许，再运行全量 pytest。

## 12. 需要用户批准的决定

进入 Step8 代码开发前，请用户明确批准或修改以下决定：

1. Step8 属于核心仿真器，改动等级为 L3。
2. Step8 v1 不接 Ramulator2 / Mooncake online replay，只使用 fitted/static KV load component。
3. Step8 v1 默认 `overlap_mode=none_v1`，即 `iteration_duration = compute + kv_load`。
4. Step8 v1 默认 `aggregation=shared_link_sum`，同一 iteration 内按实例汇总 DDR load tokens/bytes。
5. Step8 v1 的 KV load 收费发生在 request 第一次被 scheduler 选中时，后续 chunk 不重复收费。
6. `miss_tokens == 0 and ddr_hit_tokens > 0` 不再 immediate finish，改为 load-only finish。
7. HBM-only zero-miss 继续 immediate finish。
8. Step8 不做 DDR hit promotion、load completion event、load queue/backpressure。
9. Step8 不改变 cache lookup / materialization / eviction 语义，不改变 finish-time materialization。
10. `KVLoadLatencyProfile` 可以升级为显式 mode schema，并保持 `mode=zero` 兼容。
11. byte-linear 模式缺少 bytes 信息时 fail-fast；token-linear 模式可以不依赖 bytes。
12. report/export 可以跟随新增 KV load 字段，但只消费 typed result，不重算 replay 语义。

如果以上任一决定不被接受，应先修订本文，再进入 S8-A 代码开发方案。
