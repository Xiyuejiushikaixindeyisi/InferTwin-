# Step6 Acceptance E2E

Step6 功能验收主题：

```text
HBM Cache Capacity Sweep Report
```

本次验收目标：

- 使用合成 trace 做一次端到端测试。
- 在三种不同 `hbm_capacity_blocks` 下观察 KV cache hit 和 P90 TTFT。
- 检查 Step1-Step5 核心链路是否仍正常工作。
- 检查 finite HBM LRU 是否发出符合预期的 cache event 信号。
- 检查外围 report/export 能力是否有影响核心仿真器的风险。

## 1. 验收数据

临时验收目录：

```text
/tmp/hitfloor_step6_acceptance/
```

输入 trace：

```text
/tmp/hitfloor_step6_acceptance/synthetic_trace.csv
```

运行配置：

```text
/tmp/hitfloor_step6_acceptance/step6_acceptance.yaml
```

输出：

```text
/tmp/hitfloor_step6_acceptance/reports/capacity_sweep.csv
/tmp/hitfloor_step6_acceptance/reports/summary.md
```

合成 trace 形态：

- 2 个 instance：`instance-a`、`instance-b`。
- 每个 instance 20 条请求。
- 每个 instance 的 prompt 序列：

```text
A * 9, B * 1, A * 10
```

设计目的：

- 连续重复 prompt A 可以验证 prefix cache hit。
- 中间插入 prompt B 可以验证有限 HBM 容量下的 LRU 淘汰。
- 两个 instance 使用相同模式，可以验证固定路由、多实例隔离 replay。

经过真实 GLM-v5 tokenizer / chat template 后：

```text
request_count = 40
instance_count = 2
prompt_tokens_per_request = 15
prompt_blocks_per_request = 4
total_prompt_tokens = 600
```

三档 HBM 容量：

```text
hbm_capacity_blocks = [3, 4, 8]
```

解释：

- `3`: 小于单个 prompt 的 4 个 prefix blocks，无法保留完整 prefix。
- `4`: 刚好能保留一个完整 prompt。
- `8`: 能同时保留两个完整 prompt。

## 2. 验收结果

Trace-level long-format 输出：

| hbm_capacity_blocks | kv_hit_rate | hbm_hit_tokens | ddr_hit_tokens | miss_tokens | p90_ttft_ms | cache_event_count |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 0.000000 | 0 | 0 | 600 | 15.0 | 474 |
| 4 | 0.876667 | 526 | 0 | 74 | 11.0 | 192 |
| 8 | 0.913333 | 548 | 0 | 52 | 0.0 | 174 |

Instance-level 输出对称：

| hbm_capacity_blocks | instance_uuid | kv_hit_rate | hbm_hit_tokens | miss_tokens | p90_ttft_ms |
| ---: | --- | ---: | ---: | ---: | ---: |
| 3 | instance-a | 0.000000 | 0 | 300 | 15.0 |
| 3 | instance-b | 0.000000 | 0 | 300 | 15.0 |
| 4 | instance-a | 0.876667 | 263 | 37 | 11.0 |
| 4 | instance-b | 0.876667 | 263 | 37 | 11.0 |
| 8 | instance-a | 0.913333 | 274 | 26 | 0.0 |
| 8 | instance-b | 0.913333 | 274 | 26 | 0.0 |

`ddr_hit_tokens` / `ddr_hit_rate` 恒为 0，符合 Step6 v1 不建模 DDR / SSD / multi-tier cache 的边界。

## 3. TTFT 口径

本次验收使用 fitted TTFT backend：

```text
intercept_ms = 0.0
ms_per_uncached_token = 1.0
```

因此：

```text
iteration_duration_ms = scheduled_uncached_prefill_tokens * 1.0
ttft_ms = finish_time_ms - arrival_time_ms
```

zero-miss request 走 fast-finish 路径，TTFT 可以为 0。

容量变化解释：

- `capacity=3`: 不能保留完整 4-block prompt，所有请求 miss 15 tokens，所以 P90 TTFT = 15.0 ms。
- `capacity=4`: 能保留一个 prompt，连续 A 命中，但中间 B 会引发部分淘汰，所以 P90 TTFT = 11.0 ms。
- `capacity=8`: A/B 两个 prompt 都能驻留，绝大多数请求 zero-miss fast-finish，所以 P90 TTFT = 0.0 ms。

## 4. Cache Event 信号

本次验收对三个 capacity 都开启了 event dump：

```text
/tmp/hitfloor_step6_acceptance/reports/capacity_3/cache_events.csv
/tmp/hitfloor_step6_acceptance/reports/capacity_4/cache_events.csv
/tmp/hitfloor_step6_acceptance/reports/capacity_8/cache_events.csv
```

事件统计：

| hbm_capacity_blocks | lookup_hit | lookup_miss | materialize | evict | total_events |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 0 | 160 | 160 | 154 | 474 |
| 4 | 140 | 20 | 20 | 12 | 192 |
| 8 | 146 | 14 | 14 | 0 | 174 |

符合预期：

- `capacity=3`: 没有 lookup hit，大量 lookup miss、materialize、evict。
- `capacity=4`: 出现大量 lookup hit，但 A/B 切换仍产生 eviction。
- `capacity=8`: 无 eviction，两个 prompt 都能保留。
- `materialize` 的 reason 为 `finish_time_materialization`。
- `evict` 的 reason 为 `capacity`。

## 5. 端到端链路检查

本次验收覆盖了 Step1-Step6 的实际链路：

```text
synthetic CSV trace
-> trace reader
-> request_params parser
-> GLM-v5 tokenizer / chat template
-> prefix block hash
-> SimulationRequest build
-> CapacitySweepRunner
-> BatchAwareReplayEngine
-> VllmLikeBatchScheduler
-> HBMCache + LRUEvictor
-> fitted TTFT backend
-> CapacitySweepResult
-> capacity_sweep.csv / summary.md report/export
```

检查结论：

- Step1-Step5 核心链路正常。
- fixed-routing、多实例隔离 replay 正常。
- HBM LRU cache 只保存 hash key 和 metadata，没有保存全量 token 或真实 KV tensor。
- finish-time materialization 生效。
- zero-miss fast-finish 生效。
- cache event 信号符合预期。

## 6. 外围能力风险检查

本次验收没有发现外围功能影响核心仿真器的风险。

原因：

- `CapacitySweepRunner` 返回结构化 `CapacitySweepResult`。
- `capacity_sweep.csv` / `summary.md` 由 report/export 层生成。
- report/export 不重算 KV hit、TTFT 或 event 语义。
- CLI / scripts 只调用 runner 和 report/export API。
- `CsvCacheEventWriter` 是 event sink，只消费 cache events，不参与 cache lookup、materialization 或 eviction 决策。

架构边界仍然成立：

```text
core simulator -> typed result -> outer capability
```

HitFloor 表是外围能力之一，不属于核心 replay 语义。

## 7. 耗时

本次验收耗时：

```text
run_capacity_sweep elapsed_ms = 659.9 ms
whole acceptance script wall time ~= 3.31 s
```

`run_capacity_sweep elapsed_ms` 包含：

- 读取合成 trace。
- 构造 `SimulationRequest`。
- 对 3 个 HBM capacity 进行 replay。
- 生成 `capacity_sweep.csv` 和 `summary.md`。

whole script wall time 额外包含：

- 创建临时 trace/config。
- 预探测 prompt block 数。
- 读取输出 CSV / event CSV。
- 汇总 JSON 打印。

## 8. 验收结论

Step6 v1 功能验收通过。

已验证能力：

- 三档 HBM cache capacity sweep。
- trace-level 和 instance-level KV hit / TTFT 输出。
- DDR 预留字段恒为 0。
- stats-only / selected capacity event dump 语义。
- Step1-Step5 核心 replay 链路无明显回归。
- 外围 report/export 能力没有反向污染核心仿真器。
