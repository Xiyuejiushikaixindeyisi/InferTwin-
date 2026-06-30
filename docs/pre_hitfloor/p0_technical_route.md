# HitFloor 前置 P0 技术路线

## 1. 文档定位

本文是 `docs/pre_hitfloor/technical_route.md` 审批通过后的 P0 级技术路线拆解。

本轮仍然不进入代码开发，也不输出具体编码方案。本文只回答：

```text
HitFloor 外围能力开发前，InferTwin 核心仿真器必须先补齐或确认哪些 P0 能力？
为什么这些能力会影响 HitFloor 准确性？
每个 P0 能力需要先和用户讨论确认哪些设计问题？
```

HitFloor 是 InferTwin 之上的外围能力。它应消费核心仿真器 typed result，生成容量、HBM hit、DDR hit、miss、TTFT、P90 TTFT 等关系表。HitFloor 不应重算 prefix cache hit、TTFT 或 cache lifecycle。

因此，P0 技术路线的核心目标是让 InferTwin 在进入 HitFloor 前，能够更可信地回答：

```text
同一条真实 trace，在不同 cache 容量下：
  1. 哪些 prefix blocks 能在 HBM 命中？
  2. 哪些 prefix blocks 能在 DDR / CPU pooling 命中？
  3. 哪些 tokens 需要重新 prefill compute？
  4. DDR hit 引入了多少 KV load wait / service？
  5. 这些因素如何共同形成 request TTFT 和 P90 TTFT？
```

## 2. P0 总体目标

P0 阶段服务于两个准出主线。

第一条主线是 PD prefill-only replay 准出：

```text
PD 分离部署
P 实例只做 prefill
batch 只包含 prefill chunk
active KV 只包含 prefill 过程中生成和持有的 KV blocks
TTFT = scheduler_compute_wait_ms
     + sum(chunk_prefill_compute_ms)
     + kv_load_wait_ms
     + kv_load_service_ms
```

第二条主线是 LCP / 热前缀准出：

```text
tokenizer + chat template + runtime/effective block size 后
基于 prefix block hash chain 统计共享前缀、热前缀、生命周期、命中 tier 和复用间隔
```

进入 HitFloor 外围能力开发前，P0 阶段应优先保证：

- prefix cache hit 的 token / block 口径正确。
- HBM / DDR tier hit 的生命周期解释可信。
- TTFT 四项组成字段不混淆。
- active KV 对可用 HBM prefix capacity 的挤压能被表达。
- batch 对 scheduler wait、active KV、visibility 的间接影响能被表达。
- report/export 仍只消费 typed result，不污染核心 replay。

## 3. P0 范围与边界

### 3.1 本阶段类型

P0 主要属于核心仿真器前置能力。

其中：

| 能力 | 类型 | 是否阻塞 HitFloor |
| --- | --- | --- |
| PD prefill-only replay profile | 核心仿真器 | 是 |
| TTFT 四项组成字段修正 | 核心仿真器 | 是 |
| Active KV occupancy-aware HBM capacity | 核心仿真器 | 待讨论：可能作为一级风险后移到 V2 |
| Pooling mode / DDR visibility schema | 核心仿真器 | 待讨论：可能先确认接口口径，具体 mode 后续实现 |
| Prefix block visibility / lifecycle 边界确认 | 核心仿真器 | 是 |
| Block-chain LCP / hot prefix telemetry | 核心 telemetry | 是 |
| Active KV / batch 对 TTFT 的间接影响口径 | 核心仿真器讨论与验收 | 是 |

Hot prefix report、可视化、HitFloor 表、capacity search 属于外围能力，不在 P0 编码范围内。

### 3.2 本阶段不做

P0 不做：

- 不开发 HitFloor 表。
- 不开发 hit floor search。
- 不新增 capacity sweep 外围 report。
- 不在 CLI / report 中重算 replay 语义。
- 不实现真实 Mooncake / TransferEngine online replay。
- 不实现真实 offload-on-evict 数据路径。
- 不实现 DDR hit promotion。
- 不实现 calibrated compute / transfer overlap backend。
- 不实现 Decode / TPOT。
- 不实现 gateway routing。
- 不实现实例侧真实排队。
- 不实现 sparse / hybrid cache manager。
- 不实现完整 vLLM physical BlockPool、ref_cnt、pin、fragmentation。

## 4. P0-1：PD Prefill-Only Replay Profile

### 4.1 目标

明确 HitFloor 第一版的目标部署形态是 PD 分离下的 P 实例 prefill replay：

```text
P 实例只处理 prefill
batch 只包含 prefill chunk
active KV 只来自 prefill
decode / TPOT 不进入本阶段
```

### 4.2 为什么是 P0

HitFloor 主要分析 prefix cache 对 TTFT 的影响。对 PD 分离部署来说，TTFT 主要由 P 实例 prefill 侧决定。如果 replay 语义中混入 decode batch、decode KV growth 或 TPOT，就会把 active KV pressure、batch contention 和 TTFT 组成混在一起，导致 HitFloor 表难以解释。

### 4.3 与当前 InferTwin 的关系

当前 InferTwin 已经是 prefill-focused replay，并且 Step9 已实现 chunk-level timeline 和 progressive full-block visibility。P0-1 不是要求重写 replay，而是要求把部署语义收紧为显式 profile：

```text
deployment.mode = pd_prefill_only
```

该 profile 应明确它不是完整服务端到端 TTFT，也不是 PD 混部 / decode-heavy replay。

### 4.4 对核心链路的影响

- trace schema guard：不改变 trace schema，继续要求 routed trace 有非空 `instance_uuid`。
- request build：不改变 tokenizer / chat template / prefix hash。
- scheduler replay：需要确认只调度 prefill chunk。
- cache lookup / materialization：不改变 prefix lookup 规则。
- latency backend：TTFT 只组合 prefill compute 与 KV load。
- per-instance isolation：继续每实例独立 replay。
- typed result：需要带上 deployment profile / replay profile 标识。

### 4.5 风险

如果该语义不收紧，HitFloor 可能把 decode 混部压力、TPOT 或实例侧其他等待误认为 prefix cache 容量问题。

### 4.6 需要讨论确认的问题

1. 是否确认 HitFloor 第一版只面向 PD 分离下的 P 实例 prefill replay？
2. 是否确认 `deployment.mode=pd_prefill_only` 是 P0 准出 profile，而不是普通说明文字？
3. 是否确认该 profile 下不建模 decode / TPOT，也不把 decode KV 纳入 active KV？
4. 如果输入 trace 来自非 PD 分离部署，是否要求用户显式声明低置信，还是直接 fail-fast？

### 4.7 用户评审判断

已确认：

- HitFloor 第一版只面向 PD 分离下的 P 实例 prefill replay。
- 该 profile 下不建模 Decode / TPOT，也不把 decode KV 纳入 active KV。
- 如果输入 trace 来自非 PD 分离部署，直接 fail-fast。

解释：

`deployment.mode=pd_prefill_only` 是 P0 准出 profile，而不是普通说明文字，意思是它应成为配置和结果中的显式语义开关：

```text
配置声明 deployment.mode=pd_prefill_only
-> config guard 校验输入和运行模式是否匹配
-> replay / latency / metrics 按 P 实例 prefill-only 口径解释
-> typed result 输出该 profile，供 HitFloor 表解释
```

如果它只是文档说明，代码仍可能在不清楚部署形态的情况下运行，导致 decode 混部 trace、P/D 混合 trace、prefill-only trace 被同一种 TTFT 和 active KV 口径解释。作为准出 profile，它的作用是把“这次 replay 到底代表什么部署形态”固定下来，并在不匹配时 fail-fast。

## 5. P0-2：TTFT 四项组成字段修正

### 5.1 目标

统一 HitFloor 第一版 TTFT 公式：

```text
ttft_ms =
  scheduler_compute_wait_ms
  + sum(chunk_prefill_compute_ms)
  + kv_load_wait_ms
  + kv_load_service_ms
```

兼容口径：

```text
kv_load_total_ms = kv_load_wait_ms + kv_load_service_ms
kv_load_ms = kv_load_service_ms
first_token_overhead_ms = 0
```

### 5.2 为什么是 P0

HitFloor 需要比较 HBM hit、DDR hit、miss 对 TTFT 的影响。若 `kv_load_wait_ms` 与 `kv_load_service_ms` 混在一起，DDR-heavy 场景会出现重复计算、漏算或解释错误。

### 5.3 与当前 InferTwin 的关系

当前 InferTwin 已经有 chunk-level TTFT timeline、compute wait、prefill compute、KV load 和 transfer queue 相关字段。但进入 HitFloor 前需要把命名和语义收紧：

- `scheduler_compute_wait_ms`：等待被 scheduler 选中做 prefill compute。
- `chunk_prefill_compute_ms`：单个 chunk 的 uncached prefill compute 服务时间。
- `uncached_prefill_compute_ms`：request 级 chunk compute 之和。
- `kv_load_wait_ms`：transfer queue / link / stream 等待。
- `kv_load_service_ms`：真正执行 KV load / copy / transfer 的服务时间。
- `kv_load_total_ms`：wait + service。

### 5.4 对核心链路的影响

- cached_tokens / hit tokens：不应改变。
- finish_time / ttft_ms：如果当前字段统计混淆，修正后可能改变。
- cache event 顺序：不应改变。
- materialization timing：不应改变。
- true streaming：必须继续可聚合，不允许为了字段拆分退回内存 list。

### 5.5 风险

最大风险是字段名兼容：历史 `kv_load_ms` 已存在，应保持 `kv_load_ms == kv_load_service_ms`，避免外围工具把 service 和 total 混用。

### 5.6 需要讨论确认的问题

1. 是否确认 `kv_load_ms` 未来只作为 `kv_load_service_ms` 的兼容 alias？
2. 是否确认 HitFloor 表中必须同时输出 wait、service、total，而不只输出总 KV load？
3. 对 P90 `kv_load_total_ms`，是否采用“先逐 request 求 total，再算 P90”的口径？
4. 如果当前实现的 `kv_load_wait_ms` 已经包含 service，是否允许 P0 修正 typed result 语义并更新相关 golden？

### 5.7 用户评审判断

已确认：

- HitFloor 表必须同时输出 `kv_load_wait_ms`、`kv_load_service_ms`、`kv_load_total_ms`。
- 如果当前实现的 `kv_load_wait_ms` 已经包含 service，允许修正 typed result 语义并更新相关 golden。

解释：

`kv_load_ms` 是历史兼容字段，未来应等同于 `kv_load_service_ms`，只表示真正执行 KV load / copy / transfer 的服务时间。它不应表示 wait + service 的总时间。

推荐口径：

```text
kv_load_wait_ms:
  transfer queue / link / stream 等待时间

kv_load_service_ms:
  真正搬运 KV 的服务时间

kv_load_total_ms:
  kv_load_wait_ms + kv_load_service_ms

kv_load_ms:
  兼容 alias，等同于 kv_load_service_ms
```

需要统计 `kv_load_total_ms` 的原因是：DDR hit 是否有收益取决于它节省的 prefill compute 是否大于完整 load 成本。

```text
DDR hit 是否值得
= saved_prefill_compute_ms > kv_load_wait_ms + kv_load_service_ms
```

只看 service 会漏掉共享链路/队列竞争；只看 wait 会漏掉真实数据搬运。HitFloor 做 P90 TTFT 解释时，需要 request 级 total：

```text
request_kv_load_total_ms
= request_kv_load_wait_ms + request_kv_load_service_ms
```

## 6. P0-3：Active KV Occupancy-Aware HBM Capacity

### 6.1 目标

让 HBM prefix cache capacity 从静态容量升级为可选动态有效容量：

```text
effective_hbm_prefix_capacity(t)
= max(0, configured_hbm_prefix_capacity_blocks
          - active_prefill_blocks(t)
          - reserved_blocks)
```

P0 第一版只面向 PD prefill-only：

```text
active_prefill_blocks(t)
= running prefill chunks 已生成或持有的 KV blocks 估算
```

### 6.2 为什么是 P0

真实 vLLM 中 active KV / running request blocks 优先占用 HBM。容量紧张时，cached prefix blocks 会被 active allocation 挤出。高并发长 prompt 下，如果完全不建模 active KV，HitFloor 会倾向于：

- 高估 HBM hit。
- 低估 DDR hit 或 miss。
- 低估高并发下 TTFT 抬升。

### 6.3 与当前 InferTwin 的关系

当前 InferTwin 已有有限 HBM / DDR LRU、progressive visibility 和 per-instance replay，但 HBM prefix capacity 仍更接近静态配置。P0 需要引入 active KV occupancy 对可用 prefix cache capacity 的影响。

第一版建议是 estimator，而不是完整 vLLM BlockPool：

- 不建真实 physical slot。
- 不建 ref_cnt / pin。
- 不建 fragmentation。
- 不建 decode KV。

### 6.4 对核心链路的影响

启用该能力后可能改变：

- HBM hit tokens。
- DDR hit tokens。
- miss tokens。
- eviction timing。
- TTFT。

不应改变：

- tokenizer / chat template。
- prefix block hash。
- arrival order。
- instance isolation。

### 6.5 风险

风险在于 active KV estimator 过粗或过强：

- 过粗：无法解释高并发 HBM hit 下降。
- 过强：可能误伤低并发场景，制造虚假的 eviction。

因此该能力必须是显式 mode，不应静默改变现有 replay 默认结果。

### 6.6 需要讨论确认的问题

1. 是否确认第一版只统计 PD prefill active KV，不统计 decode KV？
2. active KV 估算应按“已完成 full blocks”统计，还是按“本 chunk 运行期间预占用 blocks”统计？
3. active KV 挤压 HBM prefix capacity 时，是动态降低 capacity，还是在 allocation 时触发 eviction？
4. 如果 active KV 超过 configured HBM capacity，P0 是 fail-fast、截断、还是记录 capacity pressure 并继续？
5. 是否接受该能力必须显式开启，例如 `active_occupancy_aware_v1`，避免改变旧 replay mode？

### 6.7 用户评审判断

已确认：

- 第一版只统计 PD prefill active KV，不统计 decode KV。
- 倾向于按“本 chunk 运行期间预占用 blocks”统计 active KV。

源码核对结论：

vLLM scheduler 在确定本轮 `num_new_tokens` 后，会在真正进入 running 前调用 `kv_cache_manager.allocate_slots(...)`。`allocate_slots()` 会按照 `num_new_tokens` 为 “to be computed” 的 tokens 分配 KV slots / blocks；如果 free blocks 不足，本轮 request 不能被调度。因此，“本 chunk 运行期间预占用 blocks”比“只统计已完成 full blocks”更接近真实调度期的 HBM 占用压力。

待讨论 / 一级风险：

- active KV 挤压 HBM prefix capacity 时，是动态降低 effective capacity，还是在 allocation 时触发 eviction。
- 如果 active KV 超过 configured HBM capacity，是 fail-fast、截断到 0，还是记录 capacity pressure 并继续。
- 是否必须通过显式 mode 开启，例如 `active_occupancy_aware_v1`。

用户评审补充：

对于一个固定 model，尤其是 PD 分离中的 P 实例，在流量高峰时刻，active KV occupancy 可能接近一个相对固定的容量占用。因此 P0-3 可以先不实现动态 active-aware capacity，而是作为一级风险或 V2 遗留问题；第一版 HitFloor 可以先通过模型配置中预留/扣减固定 blocks 的方式近似表达。

## 7. P0-4：Pooling Mode / DDR Visibility Schema

### 7.1 目标

显式区分 DDR / CPU pooling 的可见性语义，避免把所有 DDR hit 都解释成同一种真实系统行为。

第一版至少明确：

```text
write_through_on_materialization:
  prefix block 在 materialize / visible 后同时写入 HBM 和 DDR。

hbm_evict_offload_ddr:
  prefix block 先在 HBM，只有 HBM eviction / offload 时才进入 DDR。

remote_store:
  DDR / remote tier 由外部 store metadata 和 transfer 完成状态决定。
```

P0 可以只支持 `write_through_on_materialization`，但必须对未支持 mode fail-fast 或输出低置信状态。

### 7.2 为什么是 P0

HitFloor 的核心难点之一是区分 HBM hit 和 DDR hit。不同 pooling mode 会给出不同的 DDR 可见时刻和 tier residency，因此 P0 的重点是把 mode 和置信状态显式暴露出来，避免把不同真实部署机制混成同一种 DDR hit 解释。

### 7.3 与当前 InferTwin 的关系

当前 TieredPrefixCache 更接近 write-through：prefix block materialize 后进入 HBM 和 DDR tier accounting。这个模式适合做第一版 baseline，但必须在配置和输出中显式标明。

### 7.4 对核心链路的影响

- cached_tokens：支持 mode 不同可能改变 tier attribution。
- hbm_hit_tokens / ddr_hit_tokens / miss_tokens：不同 pooling mode 会改变。
- finish_time / ttft_ms：DDR hit 改变后会改变。
- cache event 顺序：未来 offload mode 会新增/改变 store/offload 事件。
- materialization timing：P0 不改变 local HBM progressive visibility，但必须说明 DDR visibility 是否同步。

### 7.5 风险

如果只保留 write-through 但不暴露 mode，用户容易把 DDR hit 结果误解为其他 pooling / offload / remote store 结果。

### 7.6 需要讨论确认的问题

1. HitFloor 第一版是否只接受 `write_through_on_materialization` 作为可运行 pooling mode？
2. 对 `hbm_evict_offload_ddr`，P0 是 fail-fast，还是允许低置信 dry-run？
3. DDR visibility 是否默认与 HBM progressive materialization 同步，还是必须等待未来 store completion event？
4. HitFloor 表是否必须带 `pooling_mode` 和 `pooling_confidence` 字段？

### 7.7 用户评审判断

待讨论：

- 是否需要同时设置两种接口：`write_through_on_materialization` 和 `hbm_evict_offload_ddr`。
- DDR visibility 是否默认与 HBM progressive materialization 同步，还是等待未来 store completion event。

用户评审补充：

- 是否优先实现哪种 DDR visibility，可能取决于真实部署的 reuse time CDF 和第一版 HitFloor 表的观测结果。
- P0 文档不对具体偏差方向做强判断；当前只保留 mode mismatch 会影响解释置信度的判断。

解释：

`pooling_mode` 表示 DDR / CPU tier 中 block 何时被写入、何时可见、何时可被 lookup 命中。例如：

```text
write_through_on_materialization:
  block 在 local materialization 后同步进入 DDR tier accounting。

hbm_evict_offload_ddr:
  block 先在 HBM，只有被 HBM eviction / offload 时进入 DDR tier accounting。
```

`pooling_confidence` 表示本次仿真的 pooling mode 与真实部署机制的贴近程度，不是 hit 结果本身。例如：

```text
high:
  已确认真实部署与当前 mode 一致，或已通过实验校准。

medium:
  mode 大体合理，但 visibility / store completion / offload timing 有近似。

low:
  mode 只是占位或假设，不能把 DDR hit 解释为高置信真实命中。
```

## 8. P0-5：Prefix Block Visibility / Lifecycle 边界确认

### 8.1 目标

确认 Step9 progressive full-block visibility 作为 HitFloor 第一版 local HBM prefix visibility baseline：

```text
chunk / scheduler progress 完成
-> newly completed full blocks materialize
-> 后续 scheduler lookup 可见
```

同时明确：

- 同一个 scheduler iteration 已完成 selection 的其他 request 不回头命中本 iteration 刚生成的 block。
- DDR / remote store visibility 不等同于 local HBM visibility。

### 8.2 为什么是 P0

长 prompt / 长 prefill 场景中，若等整个 request finish 后才让 blocks 可见，会低估真实 prefix hit。progressive visibility 是 HitFloor 能解释长 prefill 期间 prefix reuse 的关键。

### 8.3 与当前 InferTwin 的关系

Step9 已实现 progressive full-block materialization。P0 主要是确认其边界，并防止 HitFloor 外围能力误读：

- progressive visibility 只代表 full block 完成后的 local visibility。
- 不代表 layer-wise KV load 完成。
- 不代表 remote store / DDR offload 完成。
- 不代表同 iteration 内即时回看。

### 8.4 对核心链路的影响

如果保持现有 progressive mode，本项主要是验收与文档收紧。若发现默认 mode 不正确，后续编码方案再单独评审。

### 8.5 风险

风险主要来自两个方向：

- 过晚可见：低估长 prefill 中的 hit。
- 过早可见：高估同一 iteration 或未完成 store 的 hit。

### 8.6 需要讨论确认的问题

1. 是否确认 HitFloor 第一版基于 Step9 progressive full-block visibility，而不是 finish-time materialization？
2. 是否确认 progressive visibility 仅代表 local HBM full-block 可见？
3. DDR / CPU tier 是否需要单独 `visible_after_store_completion` 状态，还是第一版接受 write-through 同步可见？
4. 是否需要在 typed result 中输出 visibility mode，方便 HitFloor 表解释？

### 8.7 用户评审判断

已确认：

- HitFloor 第一版基于 Step9 progressive full-block visibility，而不是 finish-time materialization。
- 每个 chunk 处理结束后，newly completed full blocks 可以对后续 scheduler lookup 可见。
- 第一版可以接受 write-through 同步可见。
- typed result 需要输出 visibility mode。

解释：

“progressive visibility 仅代表 local HBM full-block 可见”与“第一版接受 write-through 同步可见”并不必然矛盾，但它们属于两个不同层次：

```text
progressive visibility:
  定义 local full block 何时完成并进入可 lookup 状态。

write_through_on_materialization:
  定义 DDR tier 是否在同一个 materialization 时刻同步获得该 block。
```

也就是说，第一版可以采用：

```text
chunk 完成
-> local HBM full block visible
-> write-through mode 下 DDR mirror 同步 visible
```

但这只适用于 `write_through_on_materialization`。如果未来实现 `hbm_evict_offload_ddr` 或 remote store，则 DDR visibility 应该有独立状态，例如 `visible_after_store_completion`。

## 9. P0-6：Block-Chain LCP / Hot Prefix Telemetry

### 9.1 目标

在 replay 过程中记录可解释 HitFloor 的 block-chain LCP / hot prefix telemetry。

LCP 口径必须发生在：

```text
request_params
-> parse messages/tools/model
-> apply chat template
-> tokenizer
-> runtime/effective block size
-> build prefix block hash chain
-> block-chain LCP / hot prefix telemetry
```

### 9.2 为什么是 P0

HitFloor 不应只输出 hit rate。它还需要解释：

- 该 trace 是否本来就有足够共享前缀。
- 热前缀数量是否集中。
- 热前缀 reuse 间隔是否超过 HBM residency。
- DDR hit 高于 HBM hit 是容量、reuse 间隔还是 pooling mode 导致。
- 某个 capacity 下 P90 TTFT 变化来自哪类 hot prefix。

### 9.3 与当前 InferTwin 的关系

当前 InferTwin 已经用 `PrefixBlock.block_key` 做 replay lookup，但还没有独立的 LCP / hot prefix analytics。P0 需要基于已有 block hash chain 产出 telemetry，而不是从 raw text 重算 LCP。

### 9.4 建议 telemetry 语义

技术路线层面建议记录：

```text
prefix_chain_id
tenant_id
model
instance_uuid
prefix_length_blocks
prefix_length_tokens
first_seen_time
last_seen_time
visible_start_time
visible_end_time
lookup_count
hit_count
miss_count
hbm_hit_count
ddr_hit_count
evicted_count
reuse_interval_ms
length_growth_or_shrink
tier_residency_history
```

具体字段命名和落盘方式留到编码方案阶段审批。

### 9.5 对核心链路的影响

Telemetry 不应改变：

- cached_tokens。
- HBM / DDR / miss tokens。
- cache event 顺序。
- materialization timing。
- finish_time / TTFT。

它应只观察 replay state 和 typed events。

### 9.6 风险

风险是 telemetry 变成第二套 replay 逻辑。P0 必须坚持：

```text
hot prefix telemetry 只消费核心 replay 的 block hash chain / event / typed result，
不得重新决定 hit / miss。
```

### 9.7 需要讨论确认的问题

1. LCP telemetry 是在核心 replay 内实时统计，还是从 typed events 离线派生？
2. 热前缀定义按 block chain exact prefix，还是按某个最小长度阈值聚合？
3. 是否需要按 tenant / model / instance 三个维度都支持聚合？
4. large trace 下 telemetry 是否默认采样、topK，还是完整 streaming aggregation？
5. `length_growth_or_shrink` 如何定义：按 request 间 LCP 变化，还是按同一 prefix family 演化？

### 9.8 用户评审判断

已确认：

- LCP telemetry 从 typed events 离线派生。
- 暂时只要求按 model / instance 维度聚合，不要求 tenant 维度作为第一版必选项。
- large trace 下 telemetry 倾向于完整 streaming aggregation。

待讨论：

- `length_growth_or_shrink` 暂时 pending，用户更倾向于按同一 prefix family 演化。

解释：

`block chain exact prefix` 是指按完整的 block hash chain 前缀精确分组。例如两个请求前 N 个 `block_key` 完全相同，就认为它们共享长度为 N 的 exact block prefix。它的优点是语义最接近 vLLM prefix cache lookup；缺点是前缀基数可能很大。

`按某个最小长度阈值聚合` 是指只统计长度达到阈值的共享前缀，例如只保留 `prefix_length_blocks >= 4` 或 `prefix_length_tokens >= 512` 的前缀。它的优点是降低大 trace 下的内存和输出压力；缺点是会丢掉短前缀复用信息。

tenant / model / instance 三个维度的意义：

- model：不同模型 tokenizer、chat template、block size、cache config 不同，必须区分。
- instance：fixed-routing replay 下 cache 是实例隔离的，必须区分。
- tenant：有助于解释多租户热前缀来源，但第一版可先不作为必选聚合维度。

完整 streaming aggregation 的风险：

- 热前缀基数可能很大，尤其是长 trace、长 prompt、agent workflow 分叉多时。
- 若每个 prefix family 都保留完整生命周期和 tier history，内存可能明显上涨。
- 第一版编码方案需要设计 bounded state，例如只保留必要计数、分位统计、topK 可选输出，避免把 LCP telemetry 变成新的大 trace 内存瓶颈。

## 10. P0-7：Active KV / Batch 对 TTFT 的间接影响口径

### 10.1 目标

确认 HitFloor 第一版不建模真实 kernel shape 的复杂 batch latency，但必须保留 batch 对 TTFT 的三条间接影响路径：

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

### 10.2 为什么是 P0

HitFloor 输出 P90 TTFT。即使 compute backend 不建模真实 batch shape 非线性，batch 仍会通过 scheduler wait、active KV capacity 和 visibility 改变 TTFT。

### 10.3 与当前 InferTwin 的关系

InferTwin 已有 vLLM-like chunked prefill scheduler 和 iteration timeline。P0 需要把 “batch 影响 TTFT 的哪些路径被纳入、哪些没有纳入” 写成准出边界。

### 10.4 对核心链路的影响

本项本身是口径确认。后续如果落地 active capacity estimator，才会改变 hit tokens、eviction 和 TTFT。

### 10.5 风险

如果忽略 batch/active KV 的间接影响，HitFloor 会在高并发场景下难以解释 HBM hit 下降。如果过早引入 kernel shape 非线性，则 P0 可能膨胀成完整 serving performance simulator。

### 10.6 需要讨论确认的问题

1. 是否确认第一版不建模真实 kernel shape 非线性 batch latency？
2. 是否确认 batch 对 TTFT 的 P0 关注点是 scheduler wait、active capacity、visibility，而不是 kernel shape？
3. `batch_size` 是否继续保持 InferTwin 已冻结语义：一次 scheduler iteration 内 request slice 数？
4. 是否需要输出 `active_running_blocks`、`effective_hbm_prefix_capacity_blocks`、`scheduled_prefill_tokens` 作为解释字段？

### 10.7 用户评审判断

已确认：

- 第一版不建模真实 kernel shape 非线性 batch latency。
- batch 对 TTFT 的 P0 关注点是 scheduler wait、active capacity、visibility，而不是 kernel shape。
- `batch_size` 继续保持 InferTwin 已冻结语义：一次 scheduler iteration 内 request slice 数。
- 需要输出 `active_running_blocks`、`effective_hbm_prefix_capacity_blocks`、`scheduled_prefill_tokens` 作为解释字段。

## 11. P0 之间的依赖关系

建议按以下逻辑关系理解 P0，不等同于编码顺序：

```text
PD prefill-only profile
  -> 决定 active KV 只统计 prefill
  -> 决定 TTFT 只组合 prefill compute + KV load

TTFT 四项字段
  -> 决定 HitFloor 如何解释 P90 TTFT
  -> 依赖 KV load wait/service 拆分

Active KV occupancy
  -> 改变 effective HBM prefix capacity
  -> 改变 HBM/DDR/miss 分布

Pooling mode
  -> 决定 DDR visibility 语义
  -> 决定 DDR hit 是否可信

Progressive visibility
  -> 决定长 prefill 中 block 何时可被后续 lookup 命中

Block-chain LCP telemetry
  -> 解释 trace 复用机会与实际 hit 之间的差距

Batch indirect impact
  -> 解释 scheduler wait、active capacity、visibility 如何共同影响 TTFT
```

## 12. P0 风险总表

| 风险 | 误差方向 | 是否阻塞 HitFloor | 风险控制 |
| --- | --- | --- | --- |
| 未区分 PD prefill-only 与 decode 混部 | TTFT 和 active KV 混淆 | 是 | 显式 deployment profile |
| KV load wait/service 混淆 | DDR-heavy TTFT 可能重复或漏算 | 是 | 拆分 wait/service/total |
| 不建模 active KV occupancy | 可能高估 HBM hit，低估 DDR/miss | 待讨论：可能作为一级风险后移 | 固定扣减或 active-aware capacity mode |
| pooling mode 与真实部署不一致 | DDR hit 解释置信度下降 | 待讨论 | pooling mode + confidence |
| visibility 过晚 | 低估长 prefill hit | 是 | progressive full-block baseline |
| visibility 过早 | 高估同 iteration / 未完成 store hit | 是 | 明确 visibility boundary |
| raw text LCP 代替 block-chain LCP | prefix opportunity 统计错误 | 是 | post-template block-chain telemetry |
| 忽略 batch 间接影响 | 无法解释高并发 TTFT | 是 | scheduler wait / active capacity / visibility 三路径 |
| report 重算 replay | 核心语义污染 | 是 | report 只消费 typed result |

## 13. 当前审批状态与下一轮待讨论项

下一轮只需要围绕本节的“待讨论 / pending”项继续判断。若待讨论项无法通过，可以先将 `pre_hitfloor` 整体 pending，直接进入 HitFloor 外围能力方案设计；完成 HitFloor 方案后，再回头处理 `pre_hitfloor`。

### 13.1 已确认项

已确认：

- HitFloor 第一版只面向 PD 分离 P 实例 prefill replay。
- `deployment.mode=pd_prefill_only` 应作为 P0 准出 profile / guard，而不是普通说明文字。
- 非 PD 分离部署 trace 直接 fail-fast。
- 不建模 Decode / TPOT，不把 decode KV 纳入 active KV。
- HitFloor 表必须同时输出 `kv_load_wait_ms`、`kv_load_service_ms`、`kv_load_total_ms`。
- 如当前 `kv_load_wait_ms` 包含 service，允许修正 typed result 语义并更新 golden。
- Active KV 第一版只统计 PD prefill active KV，不统计 decode KV。
- Active KV 统计倾向按“本 chunk 运行期间预占用 blocks”。
- HitFloor 第一版基于 Step9 progressive full-block visibility，而不是 finish-time materialization。
- chunk 处理结束后，newly completed full blocks 对后续 scheduler lookup 可见。
- 第一版可以接受 write-through 同步可见。
- typed result 需要输出 visibility mode。
- LCP telemetry 从 typed events 离线派生。
- LCP 第一版暂按 model / instance 维度聚合。
- large trace 下 LCP telemetry 倾向于完整 streaming aggregation。
- 第一版不建模真实 kernel shape 非线性 batch latency。
- batch 对 TTFT 的 P0 关注点是 scheduler wait、active capacity、visibility，而不是 kernel shape。
- `batch_size` 继续保持 InferTwin 已冻结语义：一次 scheduler iteration 内 request slice 数。
- 需要输出 `active_running_blocks`、`effective_hbm_prefix_capacity_blocks`、`scheduled_prefill_tokens` 作为解释字段。

### 13.2 待讨论 / Pending 项

待讨论：

1. P0-3 是否先不实现动态 active-aware capacity，转为 V2 遗留问题；第一版 HitFloor 是否只通过模型配置固定扣减 active KV blocks。
2. active KV 挤压 HBM prefix capacity 时，若未来实现，是动态降低 effective capacity，还是在 allocation 时触发 eviction。
3. active KV 超过 configured HBM capacity 时，未来应 fail-fast、截断到 0，还是输出 capacity pressure signal 并继续。
4. active-aware capacity 若未来实现，是否必须通过显式 mode 开启，例如 `active_occupancy_aware_v1`。
5. Pooling 是否需要同时提供 `write_through_on_materialization` 和 `hbm_evict_offload_ddr` 两种接口。
6. DDR visibility 是否默认与 HBM progressive materialization 同步，还是必须等待未来 store completion event。
7. HitFloor 输出是否必须带 `pooling_mode` 和 `pooling_confidence`。
8. P90 `kv_load_total_ms` 是否采用 request-level total 后再 percentile。
9. 热前缀定义采用 block chain exact prefix，还是按最小长度阈值聚合。
10. `length_growth_or_shrink` 是否按同一 prefix family 演化定义。

### 13.3 下一轮判断分支

若待讨论项通过：

```text
进入 P0 具体编码方案阶段
-> 每个 P0 项单独提交代码方案
-> 用户审批后再开发
```

若待讨论项不通过：

```text
pre_hitfloor 整体 pending
-> 先进入 HitFloor 外围能力方案设计
-> 完成 HitFloor 方案后再回头处理 pre_hitfloor
```

## 14. P0 通过后的下一步

只有当以上问题完成确认后，才能进入具体编码方案阶段。

编码方案阶段应逐项给出：

- 本项属于核心仿真器还是外围能力。
- 改动等级 L2 / L3。
- 允许修改的文件范围。
- 不允许修改的文件范围。
- 是否改变 replay 语义。
- 是否改变 hit tokens / TTFT / event 顺序 / materialization timing。
- 测试与 E2E 验收范围。

在编码方案审批前，不进入业务代码开发。
