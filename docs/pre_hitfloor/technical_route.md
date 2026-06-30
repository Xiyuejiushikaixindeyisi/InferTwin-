# HitFloor 前置条件技术路线

## 1. 文档定位

本文是进入 HitFloor 外围能力开发前的前置条件技术路线。

HitFloor 是 InferTwin 之上的外围能力。它的目标不是重新实现 replay，而是消费核心仿真器输出的 typed result，得到：

```text
cache 容量
-> HBM hit / DDR hit / miss
-> TTFT 组成
-> P90 TTFT
-> HitFloor 表
```

因此，在开发 HitFloor 表之前，必须先确认核心仿真器对 prefix cache hit 和 TTFT 的关键机制足够可信。否则 HitFloor 表即使格式正确，也可能因为 HBM / DDR hit 分布或 TTFT 组成不准而失去解释价值。

本轮只给出技术路线、可行性判断、开发优先级和风险边界，不给出 batch 级代码开发路线，不进行业务代码开发。

## 2. 总体判断

基于 `prefix_cache_hit_factors_and_priorities.md` 和 `ttft_modeling.md`，HitFloor 开发前最重要的不是报表字段，而是两类前置能力：

1. InferTwin 必须能可靠离线 replay 目标部署形态。
2. InferTwin 必须能在 replay 中解释 prefix reuse 链。

第一类对应 PD prefill-only replay 的准确性：

```text
PD 分离部署
P 实例只做 prefill
batch 只包含 prefill chunk
active KV 只包含 prefill 生成的 KV cache
TTFT = scheduler_compute_wait_ms
     + sum(chunk_prefill_compute_ms)
     + kv_load_wait_ms
     + kv_load_service_ms
```

第二类对应 LCP / 热前缀能力：

```text
tokenizer + chat template + block conversion 后
记录 block-chain LCP / hot prefix
统计 HBM / DDR 中热前缀数量、生命周期、长度变化、命中次数
```

HitFloor 外围能力本质上是在不同 cache capacity 下观察这些机制如何改变 HBM hit、DDR hit、miss 和 TTFT。因此，前置技术路线应围绕这两件事排序。

## 3. 本阶段属于核心仿真器还是外围能力

本阶段是混合阶段，但以核心仿真器为主。

| 能力 | 类型 | 是否阻塞 HitFloor |
| --- | --- | --- |
| PD prefill-only replay profile | 核心仿真器 | 是 |
| TTFT 四项组成口径修正 | 核心仿真器 | 是 |
| active KV occupancy-aware HBM capacity | 核心仿真器 | 是 |
| active KV / batch 对 TTFT 的间接影响口径 | 核心仿真器讨论与验收 | 是 |
| prefix block visibility / lifecycle 对齐 | 核心仿真器 | 是 |
| pooling mode / DDR visibility 语义 | 核心仿真器 | 是 |
| compute / transfer overlap boundary | 核心仿真器讨论与后续 backend 设计 | 否，但需要在 HitFloor 前明确边界 |
| block-chain LCP telemetry | 核心 telemetry | 是 |
| hot prefix analytics report | 外围分析能力 | 是，作为 HitFloor 解释前置 |
| HitFloor capacity sweep / table | 外围能力 | 本阶段不做 |

原则：

```text
核心仿真器负责产生可信 replay result 和 telemetry。
外围能力负责汇总、筛选、排序、导出，不重新计算 replay 语义。
```

## 4. HitFloor 前置准出目标

进入 HitFloor 外围能力开发前，InferTwin 应满足以下准出目标。

### 4.1 PD Prefill-Only Replay 准出

目标部署形态：

```text
PD 分离
P 实例只处理 prefill
D 实例和 decode / TPOT 暂不建模
```

核心 replay 语义：

- fixed-routing，多实例隔离。
- 每个实例独立 scheduler replay。
- batch 只包含 prefill chunk。
- active KV 只统计 prefill 过程中已生成但尚未释放的 KV blocks。
- 不把 decode KV、TPOT、decode batch 混入本阶段。

这使 HitFloor 第一版可以聚焦：

```text
输入前缀复用
-> prefill compute 减少
-> DDR/CPU KV load 成本
-> P90 TTFT
```

### 4.2 TTFT 组成准出

统一 TTFT 口径：

```text
ttft_ms =
  scheduler_compute_wait_ms
  + sum(chunk_prefill_compute_ms)
  + kv_load_wait_ms
  + kv_load_service_ms
```

要求：

- `scheduler_compute_wait_ms` 只表示请求进入实例 replay 后，等待 chunked prefill compute 的时间。
- `chunk_prefill_compute_ms` 只表示 uncached tokens 的 prefill compute 服务时间。
- `kv_load_wait_ms` 只表示 transfer queue / link / stream 等待时间。
- `kv_load_service_ms` 只表示真正执行 KV load / copy / transfer 的服务时间。
- `kv_load_total_ms = kv_load_wait_ms + kv_load_service_ms`。
- `kv_load_ms` 只作为兼容字段，语义等同于 `kv_load_service_ms`。
- `first_token_overhead_ms = 0`，不进入第一版 HitFloor 主公式。

若当前实现中 `kv_load_wait_ms` 包含 service，应在 HitFloor 前修正，否则 DDR-heavy 场景会重复计算 KV load。

### 4.3 Prefix Hit 机制准出

必须明确并实现或校验：

- prefix block 在 tokenizer、chat template、runtime block size、CP/MTP accounting 后生成。
- lookup 基于 block-chain full block 连续匹配。
- progressive full-block visibility 用于 local HBM prefix timing。
- newly completed full block 在 chunk / scheduler progress 完成后才对后续 lookup 可见。
- 同一 scheduler iteration 已完成 selection 的其他 request 不回头命中本 iteration 刚生成的 block。
- HBM / DDR hit / miss 由核心 replay 输出，HitFloor 不重算。

### 4.4 Active KV Capacity 准出

真实 vLLM 中 active KV 优先占用 HBM，cached prefix blocks 在容量紧张时会被 active allocation 挤出。因此 HitFloor 前需要至少具备轻量 active KV occupancy-aware capacity：

```text
effective_hbm_prefix_capacity(t)
= max(0, configured_hbm_prefix_capacity_blocks
          - active_prefill_blocks(t)
          - reserved_blocks)
```

第一版只需要面向 PD prefill-only：

```text
active_prefill_blocks(t)
= sum(ceil(active_prefill_tokens(request, t) / effective_block_size))
```

暂不建模：

- decode KV growth。
- physical KV tensor。
- full vLLM ref_cnt / free queue。
- preemption。
- fragmentation。

但如果完全不做 active KV occupancy，HitFloor 在高并发长 prompt 下会倾向于高估 HBM hit，并低估 DDR hit 或 miss。

### 4.5 Pooling / DDR Visibility 准出

当前 InferTwin `TieredPrefixCache` 更接近：

```text
write_through_on_materialization
```

真实系统可能是：

```text
write_through_on_materialization
hbm_evict_offload_ddr
metadata_driven_remote_store
P/D KV transfer
```

如果真实系统 DDR hit 主要来自 HBM eviction / offload，那么只做 write-through 会明显高估 DDR 可见性。

因此，HitFloor 前必须显式声明 pooling mode：

- 已支持 mode 才能运行。
- 未支持 mode 必须 fail-fast 或明确进入低置信结果。
- 输出中必须带 `pooling_mode`。
- DDR hit 解释必须绑定 mode。

### 4.6 LCP / Hot Prefix 准出

HitFloor 需要解释为什么某些容量下 HBM hit / DDR hit / miss 会变化。因此，开发 HitFloor 前应具备 block-chain LCP / hot prefix 能力。

记录口径必须发生在：

```text
request_params
-> parse messages/tools/model
-> apply chat template
-> tokenizer
-> runtime/effective block size
-> build prefix block hash chain
-> block-chain LCP / hot prefix telemetry
```

建议至少统计：

```text
prefix_chain_id
block_hash_prefix
tenant_id
model
instance_uuid
first_seen_time
last_seen_time
visible_start_time
visible_end_time
reuse_count
lookup_count
hit_count
miss_count
hbm_hit_count
ddr_hit_count
evicted_count
prefix_length_blocks
prefix_length_tokens
reuse_interval_ms
length_growth_or_shrink
tier_residency_history
```

其中：

- telemetry 应由核心 replay 产生或从核心 event 派生。
- hot prefix report 属于外围分析能力。
- report 不得反向修改 replay hit 结果。

### 4.7 Active KV / Batch 对 TTFT 的间接影响准出

Active KV 和 batch 不只是 prefix hit 的容量问题，也会间接影响 TTFT。

在 PD prefill-only replay 中，batch 至少通过三条路径影响 TTFT：

```text
路径 A：batch / token budget / running set
-> request 被切成几个 prefill chunk
-> scheduler_compute_wait_ms 改变

路径 B：batch 中正在运行的 prefill chunks
-> active_prefill_blocks 增加
-> effective_hbm_prefix_capacity 下降
-> HBM hit / DDR hit / miss 分布改变
-> chunk_prefill_compute_ms 和 kv_load_* 改变

路径 C：chunk 完成时刻
-> newly completed full blocks 何时 visible
-> 后续 request 的 prefix hit 改变
-> 后续 TTFT 改变
```

因此，HitFloor 前必须明确：

- batch size 在 InferTwin 中仍表示一次 scheduler iteration 内的 request slice 数。
- HitFloor 第一版不追求真实 kernel shape 的 batch latency 非线性。
- 但必须保留 batch 对 scheduler wait、active KV occupancy、prefix visibility 的间接影响。
- active KV 第一版只统计 prefill active KV，不混入 decode KV。

### 4.8 Compute / Transfer Overlap Boundary 准出

真实系统中，DDR / CPU / remote KV load 可能与 prefill compute 部分 overlap。当前 InferTwin 默认：

```text
overlap_mode = none_v1
iteration_duration = compute + kv_load
```

这会让 DDR-heavy 场景下 TTFT 偏保守，即可能高估真实 TTFT。但如果真实系统中 KV load 无法 overlap，例如：

- load 必须在 compute 前完成。
- transfer stream 与 compute stream 存在强同步。
- HBM slot 尚未 ready。
- load queue/backpressure 严重。

则 `none_v1` 可能反而更接近真实系统。

HitFloor 前不要求实现完整 overlap backend，但必须在文档和输出中明确：

- 当前是否启用 overlap。
- 默认 `none_v1` 的误差方向。
- DDR-heavy 场景下 HitFloor 结论是否是 conservative。
- 后续如果实现，应新增 `ComputeTransferOverlapBackend` 或 latency backend mode，而不是在 report 层修正。

## 5. 优先级排序

### P0：HitFloor 开发前必须完成

#### P0-1：PD Prefill-Only Replay Profile

原因：

HitFloor 第一版关注 prefix cache 对 TTFT 的影响，而公司主要部署趋势是 PD 分离。P 实例 replay 若混入 decode / TPOT，会把 active KV、batch 和 TTFT 组成都搞混。

必须明确：

- 当前 replay mode 是否代表 PD P 实例。
- batch 是否只包含 prefill chunk。
- active KV 是否只来自 prefill。
- instance-level TTFT profile 是否对应 P 实例 prefill profile。

风险：

- 若语义不清，HitFloor 表会把 decode 混部压力误认为 prefix cache 容量问题。

#### P0-2：TTFT 四项组成字段修正

原因：

HitFloor 最终输出 P90 TTFT。若 `scheduler_compute_wait_ms`、`chunk_prefill_compute_ms`、`kv_load_wait_ms`、`kv_load_service_ms` 口径不清，DDR hit 是否收益无法判断。

必须完成：

```text
kv_load_wait_ms = queue/link/stream wait
kv_load_service_ms = transfer/copy service
kv_load_total_ms = wait + service
kv_load_ms = service compatibility alias
scheduler_compute_wait_ms = compute scheduling wait
```

风险：

- DDR-heavy 场景可能重复计算或漏算 load 时间。
- HitFloor 可能错误判断 DDR hit 有收益或无收益。

#### P0-3：Active KV Occupancy-Aware HBM Capacity

原因：

真实系统中 active KV 优先占用 HBM。高并发长 prompt 下，active KV 会挤压 prefix cache，从而改变 HBM hit、DDR hit 和 miss 分布。

第一版目标：

```text
PD prefill-only active blocks estimator
dynamic effective_hbm_prefix_capacity
active_running_blocks / effective capacity typed metrics
```

风险：

- 不做会高估 HBM hit。
- 做得过重会提前进入 full block manager 复杂度。

边界：

- 第一版只做 estimator，不建真实 ref_cnt。
- 必须显式 mode，不改变默认 replay 结果。

#### P0-4：Pooling Mode / DDR Visibility Schema

原因：

HitFloor 难点在调节 HBM hit 和 DDR hit。DDR hit 来源不同，解释完全不同。

必须完成：

- 显式 `pooling_mode`。
- 当前支持 `write_through_on_materialization`。
- 对 `hbm_evict_offload_ddr`、`remote_store` 等未实现模式 fail-fast 或低置信标注。
- 输出中保留 mode 与 calibration status。

风险：

- 如果真实系统主要是 offload-on-evict，而仿真器按 write-through 跑，会明显高估 DDR hit。

#### P0-5：Prefix Block Visibility / Lifecycle 边界确认

原因：

长 prompt prefill 期间，block 何时可见直接影响后续请求能否提前 hit。

当前判断：

- Step9 progressive full-block visibility 对 local HBM prefix timing 已足够接近第一版 baseline。
- 但对 DDR/store/offload completion 不充分。

必须完成：

- 明确 progressive visibility 是 HitFloor baseline。
- 明确它只表示 local full-block 可见性。
- 对 DDR store completion 未建模给出 mode/status 标注。

风险：

- 若退回 finish-time materialization，会低估长 prefill 期间的 prefix hit。
- 若把 DDR 也当成立即可见，会高估 DDR hit。

#### P0-6：Block-Chain LCP / Hot Prefix Telemetry

原因：

HitFloor 不只是输出一张 capacity 表，还需要解释为什么某些容量下 hit 变化。热前缀数量、生命周期、复用间隔和 tier residency 是解释 HitFloor 表的核心证据。

必须完成：

- replay 过程中统计 HBM / DDR 热前缀数量。
- 统计热前缀生命周期。
- 统计热前缀长度变化。
- 统计 lookup / hit / miss / HBM hit / DDR hit 次数。
- 支持按 instance、tenant、model 聚合。

风险：

- 只输出 hit rate，无法判断是 trace 复用机会不足，还是 cache lifecycle / capacity 不足。
- 无法解释 DDR hit 高于 HBM hit 的现象。

#### P0-7：Active KV / Batch 对 TTFT 的间接影响口径

原因：

HitFloor 输出的是 P90 TTFT，而 batch 和 active KV 虽然不一定直接进入 fitted compute 公式，却会通过 scheduler wait、active capacity、chunk visibility 间接改变 TTFT。

必须完成：

- 明确 PD prefill-only 下 batch 只包含 prefill chunk。
- 明确 active KV 只统计 prefill active KV。
- 明确 batch 对 TTFT 的三条间接路径：
  - scheduler_compute_wait_ms。
  - effective_hbm_prefix_capacity。
  - progressive full-block visibility。
- 明确第一版不建模真实 kernel shape 非线性 batch latency。

风险：

- 如果只看 prefix hit，不看 active/batch 间接影响，HitFloor 可能无法解释高并发下 HBM hit 下降和 TTFT 抬升。
- 如果过早引入真实 batch latency，会把 HitFloor 前置任务扩展成完整 serving performance simulator。

### P1：强烈建议在 HitFloor 前完成

#### P1-1：Runtime Block / Tokenizer / Chat Template Guard

这些是 correctness guard。配置错误会导致 prefix block chain 从根上错误。

建议校验：

- model profile 绑定 tokenizer。
- chat template 与线上一致。
- runtime block size 来源明确。
- CP / DCP / PCP / MTP / EAGLE accounting 明确。

若这些能力已有，应在 pre_hitfloor 验收中确认，而不一定重新开发。

#### P1-2：DDR Load Profile Status

DDR hit 对 TTFT 的收益依赖 KV load 参数。若没有校准，应输出：

```text
calibrated
uncalibrated
conservative_default
disabled
```

没有真实校准不阻塞 HitFloor 第一版，但必须避免把 DDR-heavy TTFT 包装成高置信结论。

#### P1-3：Active Block / Cached Block / Free Queue 轻量状态解释

不要求实现完整 vLLM BlockPool，但应在文档和 metrics 中解释：

```text
active blocks
cached prefix blocks
free / evictable blocks
```

这有助于后续从 estimator 过渡到更真实 block manager。

#### P1-4：Compute / Transfer Overlap Boundary

第一版 HitFloor 可以不实现 overlap backend，但必须明确默认口径和误差方向。

建议讨论并沉淀：

```text
none_v1:
  compute 和 kv_load 串行相加，偏保守，可能高估 DDR-heavy TTFT。

full_or_partial_overlap:
  未来 backend，根据真实 profiling / calibration 判断可 overlap 比例。
```

建议输出或配置中至少能标注：

```text
overlap_mode = none_v1
overlap_backend_status = not_enabled
```

不建议在 HitFloor report 中临时减去 overlap 时间。若需要 overlap，应回到核心 latency backend 新增 mode。

### P2：不阻塞 HitFloor 第一版

以下能力重要，但不应阻塞第一版 HitFloor：

- calibrated compute / transfer overlap backend。
- layer-wise KV load timing。
- 真实 Mooncake / TransferEngine adapter。
- 真实 `hbm_evict_offload_ddr` 实现。
- DDR load completion event / promotion。
- Decode / TPOT。
- gateway routing。
- 实例侧真实排队。
- multi-instance pooling / remote hit。
- sparse / hybrid cache manager。
- full physical KV slot / ref_cnt / fragmentation。

## 6. 输入、输出、配置变化

### 6.1 输入

不建议改变 trace CSV schema。

继续要求：

- routed trace 必须有非空 `instance_uuid`。
- 无实例 id 的 trace 由外围 normalizer 补统一实例 id，不属于核心 replay 行为。
- request params 继续由 parser / tokenizer / chat template 构建 `SimulationRequest`。

### 6.2 配置

建议新增或确认：

```yaml
deployment:
  mode: pd_prefill_only

cache:
  hbm_capacity_policy:
    mode: static_prefix_capacity | active_occupancy_aware_v1
    reserved_blocks: 0

default_cache:
  pooling:
    enabled: true
    mode: write_through_on_materialization

kv_load:
  calibration_status: calibrated | uncalibrated | conservative_default | disabled
  overlap_mode: none_v1
```

具体字段名需要在代码方案中再次评审。

### 6.3 输出

HitFloor 前置阶段应让核心 replay / streaming metrics 能输出：

```text
scheduler_compute_wait_ms
chunk_prefill_compute_ms
uncached_prefill_compute_ms
kv_load_wait_ms
kv_load_service_ms
kv_load_total_ms
pooling_mode
kv_load_profile_status
active_running_blocks
effective_hbm_prefix_capacity_blocks
overlap_mode
prefix_chain_id
hot_prefix_lifecycle metrics
hot_prefix_hit counters
```

HitFloor 外围能力只消费这些字段，不重新计算。

## 7. 对核心链路的影响评估

| 核心链路 | 影响 |
| --- | --- |
| trace schema guard | 不改变 CSV schema；继续 fail-fast 拒绝空 `instance_uuid`。 |
| request build | 不改变 parser / tokenizer / chat template 主流程；LCP 必须使用 build 后的 block chain。 |
| tokenizer / chat template | 不改变行为；需要作为 P1 guard 确认线上一致性。 |
| prefix block hash | 不改变 hash 算法；新增 LCP / hot prefix telemetry 消费 hash chain。 |
| scheduler replay | PD prefill-only profile 需要明确 batch 只包含 prefill chunk；active capacity estimator 需要读取 running state；batch 对 compute wait 和 visibility 的间接影响需要进入验收。 |
| cache lookup / materialization / eviction | progressive visibility 作为 local HBM baseline；active-aware capacity 会影响 HBM residency；pooling mode 决定 DDR 可见性解释。 |
| latency backend | 不改变 fitted/static backend 本质；必须修正 wait/service/total 字段语义；overlap 第一版保持 `none_v1`，后续如实现需新增 backend / mode。 |
| per-instance isolation | 继续每实例独立 cache、scheduler、latency profile、active occupancy。 |
| typed metrics / typed result | 增加 TTFT component、active capacity、pooling mode、hot prefix telemetry。 |

## 8. 与现有 V1 replay 语义的关系

默认 V1 replay 不应被静默改变。

可以保持：

- fixed-routing、多实例隔离。
- HBM / DDR LRU。
- progressive full-block visibility。
- fitted/static TTFT and KV load profile。
- no decode / TPOT。

需要新增的能力应采用显式 mode / schema：

```text
deployment.mode = pd_prefill_only
hbm_capacity_policy.mode = active_occupancy_aware_v1
pooling.mode = write_through_on_materialization
lcp_telemetry.enabled = true
```

如果启用 active-aware capacity，允许改变：

- HBM hit tokens。
- DDR hit tokens。
- miss tokens。
- eviction event 时机。
- TTFT。

但不得改变：

- tokenizer / chat template 输出。
- prefix block hash。
- arrival order。
- instance isolation。
- report 不重算 replay 的原则。

## 9. 不做什么

本阶段不做：

- 不开发 HitFloor 表。
- 不开发 hit floor search。
- 不开发 capacity sweep 新 report。
- 不在 CLI / report 里重算 cache lookup 或 TTFT。
- 不实现真实 Mooncake / TransferEngine online replay。
- 不实现真实 offload-on-evict。
- 不实现 DDR promotion。
- 不实现 calibrated compute / transfer overlap backend，但必须明确默认 overlap boundary。
- 不实现 Decode / TPOT。
- 不实现 gateway routing。
- 不实现实例侧排队。
- 不实现 sparse / hybrid cache manager。
- 不实现 full vLLM physical BlockPool。

## 10. 风险与可行性评估

### 10.1 技术可行性

整体可行。

原因：

- PD prefill-only replay 与现有 InferTwin prefill-focused replay 方向一致。
- Step9 已实现 chunk-level timeline 和 progressive full-block visibility。
- active KV occupancy 第一版可以先用 estimator，不需要完整 physical block manager。
- LCP / hot prefix 可以基于已有 `PrefixBlock.block_key` 和 replay event 实现。

### 10.2 最大风险

最大风险不是代码复杂度，而是语义混淆：

- 把 write-through DDR 当成真实 offload DDR。
- 把 `kv_load_wait_ms` 当成全部 load time。
- 把 `none_v1` 串行 KV load 误认为真实系统一定不能 overlap。
- 把静态 `hbm_capacity_blocks` 当成真实 prefix cache capacity。
- 只讨论 active KV 对 hit 的影响，忽略 batch / active KV 对 TTFT 的间接影响。
- 把 raw text LCP 当成 block-chain LCP。
- 把 HitFloor 外围汇总写成核心 replay 逻辑。

### 10.3 风险控制

风险控制方式：

- 所有新语义用显式 mode / schema。
- 不改变默认 replay 结果。
- 未实现 mode fail-fast 或低置信输出。
- LCP 只基于 post-template block chain。
- HitFloor 外围能力只消费 typed result。
- 每个核心改动都需要单独代码方案和 E2E 验收。

## 11. 是否需要继续对比 vLLM / vLLM-Ascend / Mooncake

需要，但范围应服务于 P0。

优先对比：

1. vLLM full block 何时 cache / visible。
2. active KV 与 cached prefix block 在 HBM block pool 中的优先级。
3. vLLM-Ascend CPU offload 的 lookup / load / store / layer-wise 行为。
4. Mooncake / Mooncake Store 中 write-through、offload-on-evict、remote store 的区别。
5. 真实部署中 compute / transfer 是否可能 overlap，以及当前 `none_v1` 的误差方向。

暂不深挖：

- full TransferEngine 调度细节。
- replica placement 精细策略。
- sparse / hybrid 模型 cache group。
- decode-side KV growth。

## 12. 需要用户审批的技术路线问题

进入具体代码方案前，需要先审批以下判断：

1. 是否接受 HitFloor 前置阶段以“PD prefill-only replay 准出 + LCP 热前缀准出”为主线。
2. 是否接受 P0 优先级：

```text
P0-1 PD Prefill-Only Replay Profile
P0-2 TTFT 四项组成字段修正
P0-3 Active KV Occupancy-Aware HBM Capacity
P0-4 Pooling Mode / DDR Visibility Schema
P0-5 Prefix Block Visibility / Lifecycle 边界确认
P0-6 Block-Chain LCP / Hot Prefix Telemetry
P0-7 Active KV / Batch 对 TTFT 的间接影响口径
```

3. 是否接受 active KV 第一版只做 PD prefill-only estimator，不建完整 vLLM BlockPool。
4. 是否接受 compute / transfer overlap 第一版只明确 `none_v1` 边界和误差方向，不实现 calibrated overlap backend。
5. 是否接受 HitFloor 前必须具备 LCP / hot prefix telemetry，但 hot prefix report 作为外围分析能力。
6. 是否接受本阶段不输出 batch 级开发路线；待技术路线通过后，再逐项提交代码编写方案。
