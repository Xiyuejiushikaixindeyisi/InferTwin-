# Step8 技术路线与代码结构方案

状态：待用户评审。

阶段类型：核心仿真器。

## 1. Step8 产品目标

Step8 在 Step7 的 tier-aware prefix cache 基础上，为非 HBM KV cache hit 增加 latency accounting。

核心目标：

```text
DDR/CPU hit tokens should reduce prefill compute tokens,
but should add explicit KV load latency.
```

Step8 完成后，一条请求的 TTFT 口径应为：

```text
ttft_ms =
  scheduler_wait_ms
  + prefill_compute_ms(miss_tokens)
  + kv_load_ms(ddr_hit_tokens / ddr_load_bytes)
```

其中：

- HBM hit 不产生 KV load latency。
- DDR/CPU hit 产生 KV load latency。
- miss tokens 仍进入 fitted TTFT / prefill compute backend。
- `kv_load_ms` 必须进入 iteration finish time，进而影响 request finish time 和 P90 TTFT。

## 2. Step8 做什么

Step8 v1 做：

1. 显式建模 iteration-level KV-load input。
2. 让 `BatchShape` / `ScheduledSlice` 能表达：
   - `hbm_hit_tokens`
   - `ddr_hit_tokens`
   - `kv_load_tokens`
   - `kv_load_bytes`
3. 扩展 latency memoization key，避免不同 KV load shape 误复用同一 latency。
4. 新增 KV-load latency component：
   - 默认 zero。
   - token-linear。
   - byte-linear。
5. 从 `InstanceLatencyProfile.kv_load` / model default latency 构建 KV-load component。
6. 将 DDR load latency 接入 streaming replay。
7. 修正 zero-miss DDR request 不能 immediate finish 的问题。
8. 在 request / iteration metrics、capacity sweep CSV、summary 中输出 `kv_load_ms`。
9. 保持 HBM-only mode 兼容。
10. 保留 Ramulator2 adapter/calibration 边界，但不让默认 replay 依赖 Ramulator2。

## 3. Step8 不做什么

Step8 v1 不做：

- 不做真实 Ramulator2 online replay。
- 不做 memory request trace 级别 replay。
- 不做 KV load queue/backpressure。
- 不做 load 与 compute overlap。
- 不做 DDR hit promotion 到 HBM。
- 不做跨实例 remote KV hit/load。
- 不做 SSD tier。
- 不做 gateway routing。
- 不做实例入口排队。
- 不做 Decode / TPOT。
- 不做 progressive block visibility。
- 不做 Hybrid/Mamba/MLA physical cache group 精确 layout。

如果评审要求以上能力，应拆成新阶段或专项，不能塞进 Step8 v1。

## 4. 为什么不建议 Step8 直接接 Ramulator2 在线 replay

Ramulator2 是 DRAM simulator，不是 LLM serving simulator。把它直接接到 InferTwin replay 主路径，需要额外定义：

- KV block/page 到 memory address 的映射。
- 一次 KV load 拆成多少 DRAM requests。
- read/write 比例。
- memory request queue full 时如何等待。
- Ramulator2 cycle 与 InferTwin ms 的同步。
- load callback 如何生成 replay event。
- load 与 prefill compute 是否 overlap。

同时，Ramulator2 的 `LoadStoreTrace` standalone frontend 会把 trace 加载到内存。这不适合 InferTwin 11G trace 主路径。

因此 Step8 推荐：

```text
Ramulator2 -> calibration source
InferTwin -> fitted KVLoadLatencyComponent
```

未来如需更细粒度，可以新增 opt-in calibration harness 或 online wrapper，不影响默认 replay。

## 5. 关键数据口径

### 5.1 Token 口径

Step7 已经输出：

```text
hbm_hit_tokens
ddr_hit_tokens
miss_tokens
```

Step8 不能改变这些不变量：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == prompt_tokens
```

Step8 只新增 latency 解释：

```text
kv_load_tokens = ddr_hit_tokens
```

暂不对 HBM hit 收费。

### 5.2 Bytes 口径

优先使用 request/block 已有字段：

```text
SimulationRequest.kv_bytes_per_token
PrefixBlock.size_bytes
```

如果 `kv_bytes_per_token` 缺失：

- HBM-only mode 可以继续运行。
- DDR mode 如果启用了 byte-linear KV load，应 fail-fast。
- DDR mode 如果使用 token-linear KV load，可以继续运行，但必须在 result details 中标注 `kv_load_bytes_available=false`。

### 5.3 Batch / iteration 口径

请求的 DDR KV 只在第一次进入可执行阶段时加载一次。

建议：

```text
first scheduled slice:
  kv_load_tokens = lookup.ddr_hit_tokens
  kv_load_bytes = sum(DDR hit block bytes after accounting)

later slices:
  kv_load_tokens = 0
  kv_load_bytes = 0
```

batch-level：

```text
shape.kv_load_tokens = sum(slice.kv_load_tokens)
shape.kv_load_bytes = sum(slice.kv_load_bytes)
```

默认聚合：

```text
kv_load_ms =
  ddr_fixed_overhead_ms
  + shape.kv_load_bytes * ddr_ms_per_byte
```

如果使用 token-linear：

```text
kv_load_ms =
  ddr_fixed_overhead_ms
  + shape.kv_load_tokens * ddr_ms_per_cached_token
```

## 6. 需要小心的 replay 行为

### 6.1 Shape memoization

当前 `ShapeKey` 只包含：

- backend。
- model/hardware。
- batch size。
- scheduled prefill tokens。
- scheduled decode tokens。
- max query len。
- total context tokens。

Step8 必须加入：

- `kv_load_tokens`
- `kv_load_bytes`
- 可选 `kv_load_request_count`

否则两个 prefill shape 相同但 DDR load 不同的 iteration 会错误命中 memo。

### 6.2 zero-miss DDR request

Step7 的 zero-miss fast-finish 在 HBM-only 情况下成立。

Step8 必须区分：

```text
miss_tokens == 0 and ddr_hit_tokens == 0 -> immediate finish
miss_tokens == 0 and ddr_hit_tokens > 0  -> load-only finish after kv_load_ms
```

这一点是 Step8 的核心验收项。

### 6.3 scheduler semantics

Step8 不应该改变 scheduler 的 batch admission 规则。

KV load latency 应影响：

- iteration duration。
- request finish time。
- TTFT。
- capacity sweep P90。

但不应影响：

- prefix cache lookup result。
- HBM/DDR/miss token accounting。
- request route。
- cache materialization semantics。

## 7. 建议代码结构

建议新增或修改：

```text
src/infertwin/latency/
  kv_load.py                 # KVLoadLatencyComponent implementations
  profile.py                 # ServingLatencyProfile 接入 KV load component
  instance_resolver.py       # 从 instance/model profile 构建 kv_load component
  schema.py                  # ShapeKey 增加 kv_load fields

src/infertwin/scheduler/
  batch_shape.py             # ScheduledSlice / BatchShape 增加 kv_load fields

src/infertwin/replay/
  event_loop.py              # lookup tier details -> first slice / zero-miss load
  metrics.py                 # request/iteration metrics 增加 kv_load_ms

src/infertwin/streaming/
  replay.py                  # streaming path 同步 Step8 replay 行为
  metrics.py                 # 聚合 kv_load_ms / p90 TTFT 不变量

src/infertwin/config/
  profiles.py                # KVLoadLatencyProfile 扩展 mode / bytes 参数
  model_registry.py          # model default latency 中解析 kv_load

src/infertwin/report/
  sweep.py                   # capacity_sweep.csv / summary.md 输出 kv_load 字段

src/infertwin/external/
  ramulator2.py              # 保持 adapter boundary；Step8 v1 不进默认路径
```

建议测试结构：

```text
tests/unit/latency/test_kv_load_latency.py
tests/unit/latency/test_serving_latency_profile.py
tests/unit/scheduler/test_batch_shape_kv_load.py
tests/unit/replay/test_zero_miss_kv_load.py
tests/integration/test_step8_streaming_kv_load_e2e.py
tests/integration/test_step8_capacity_sweep_kv_load_report.py
```

## 8. Batch 开发顺序

每个 batch 开始前仍应先生成独立开发文档，用户评审通过后再写代码。

### S8-A：KV-load shape schema

目标：

- 扩展 `ScheduledSlice` / `BatchShape`，显式表达 KV-load tokens/bytes。
- 扩展 `ShapeKey`。
- 保持默认值为 0，确保 HBM-only 旧测试不受影响。

原因：

Step8 后 latency memoization 必须区分 prefill compute shape 和 KV-load shape。

验收：

- HBM-only shape key 与历史行为兼容。
- 不同 `kv_load_tokens` / `kv_load_bytes` 产生不同 shape key。
- 非负校验完整。

### S8-B：KVLoadLatencyComponent

目标：

- 新增 `latency/kv_load.py`。
- 实现：
  - `ZeroKVLoadLatencyComponent`
  - `TokenLinearKVLoadLatencyComponent`
  - `ByteLinearKVLoadLatencyComponent`
- 扩展 `KVLoadLatencyProfile` schema。

建议 schema：

```yaml
kv_load:
  mode: byte_linear_v1        # zero | token_linear_v1 | byte_linear_v1
  ddr_fixed_overhead_ms: 0.0
  ddr_ms_per_cached_token: 0.0
  ddr_ms_per_byte: 0.0
  aggregation: shared_link_sum
  calibrated_from: manual_default
```

原因：

未来可以从 Ramulator2 得到 byte coefficient，但默认仍可手动给 token coefficient。

验收：

- `mode=zero` 返回 0。
- token-linear 随 `kv_load_tokens` 单调增加。
- byte-linear 随 `kv_load_bytes` 单调增加。
- 缺失 bytes 时 byte-linear fail-fast。

### S8-C：Replay scheduled iteration integration

目标：

- lookup 完成后保留 tier split。
- 请求第一次被 scheduler 选中时，把 `ddr_hit_tokens/bytes` 写入 slice。
- 后续 chunk 不重复收费。
- `ServingLatencyProfile` 将 TTFT compute 和 KV load 组合为 iteration duration。

原因：

KV load 应该影响 batch iteration finish time，而不是只改 request metric。

验收：

- DDR hit tokens 增加时 iteration duration 增加。
- HBM hit 不增加 `kv_load_ms`。
- 同一请求多 chunk 只收一次 KV load。
- HBM/DDR/miss token accounting 不变。

### S8-D：zero-miss DDR load path

目标：

- 修正 `miss_tokens == 0 and ddr_hit_tokens > 0` 的 immediate finish。
- 设计 load-only finish path。
- 输出 request-level `kv_load_ms`。

建议先用最小方案：

```text
zero-miss DDR request:
  finish_time = now + kv_load_component.estimate(...)
```

是否生成 load-only iteration metric，由本 batch 代码方案进一步评审。

原因：

全部命中 DDR 的请求仍需要 restore KV，不能在 lookup 时刻完成。

验收：

- HBM-only zero-miss 仍 immediate finish。
- DDR zero-miss TTFT > 0。
- 多个 DDR zero-miss 请求的排序 deterministic。

### S8-E：Streaming runner / report integration

目标：

- `sweep-streaming` 输出 KV-load metrics。
- capacity sweep row 增加：
  - `total_kv_load_ms`
  - `avg_kv_load_ms`
  - 可选 `p90_kv_load_ms`
- summary 明确：
  - prefill compute latency。
  - KV load latency。
  - total TTFT。

原因：

Step8 是核心仿真器能力，但 capacity sweep 是主要验收入口，需要把新增 latency 可观察化。

验收：

- 合成 trace 三种 capacity 下，DDR hit 越多时 KV-load 分量越高。
- TTFT 同时受 miss tokens 和 DDR load tokens 影响。
- HBM-only mode `kv_load_ms == 0`。

### S8-F：Ramulator2 calibration boundary

目标：

- 不接默认 replay。
- 完善 `external/ramulator2.py` 的 adapter boundary 文档和接口。
- 增加 opt-in calibration harness 设计或轻量测试桩。

原因：

Ramulator2 更适合作为参数标定工具，而不是 Step8 默认在线依赖。

验收：

- 不安装/运行 Ramulator2 时，InferTwin 测试不受影响。
- 文档清楚说明如何把 Ramulator2 结果写入 `kv_load` profile。
- 如果未来接真实 adapter，入口是显式 opt-in。

### S8-G：E2E / Review / Archive

目标：

- 全量测试。
- ruff。
- Step8 review 文档。
- 更新主文档和全局记忆。
- 将 `docs/step8/` 移入 `docs/archive/step8/`。

准出问题：

```text
InferTwin 是否具备进入 Step9 progressive block visibility 的条件？
```

## 9. Step8 准入条件

进入代码开发前必须确认：

1. 接受 Step8 v1 不做 Ramulator2 online replay。
2. 接受 Step8 v1 默认使用 fitted/static KV-load function。
3. 接受 DDR load 默认按 first-scheduled-iteration 收费。
4. 接受 zero-miss DDR request 不再 immediate finish。
5. 接受 HBM hit 不产生 KV load latency。
6. 接受 Step8 不做 promotion / load completion event。

如果以上任一项被否决，应暂停 Step8，重新定义产品形态。

## 10. 是否建议 pending

不建议整个 Step8 pending。

原因：

- Step7 已经有 DDR hit tokens，若不加 KV load latency，P90 TTFT 会系统性低估。
- Step9 progressive visibility 也需要更细的 latency composition 基础。
- 当前代码已有 `ServingLatencyProfile` 和 `kv_load` schema 预留，适合小步推进。

建议 pending 的部分是：

- Ramulator2 online wrapper。
- block/page/memory-request-level simulation。
- load queue/backpressure。
- communication-level detailed simulation。

也就是说，Step8 应先做可维护的 KV-load accounting，再把底层存储仿真作为 calibration 或后续专项。
