# Step4 Product Shape

## 目标

Step4 的目标不是直接求最终 hit floor，而是让 HitFloor 具备可信的 batch-aware replay 能力：

1. 按 trace 中的 `instance_uuid` 做固定路由、多实例隔离 replay；每个实例内部模拟请求到达、prefix cache lookup、vLLM-like 组 batch、chunked prefill、iteration 推进。
2. 为 TTFT latency backend 生成稳定的 batch shape 输入。
3. 用 latency backend 返回的 iteration duration 推进时间。
4. 只有当请求 prefill 完成后，才 materialize 该请求产生的 KV block，使其对后续请求可见。
5. 输出 request 级 TTFT 指标、iteration 级 batch 指标和 summary。

## Step4 输入

沿用 Step1-Step3 的输入：

- 现网 trace CSV。
- tokenizer profile，例如 `tokenizers/glm-v5/manifest.yaml`。
- 实例 UUID，按 trace 中已有路由结果分实例 replay。

Step4 新增配置：

- scheduler 配置：
  - `max_num_batched_tokens`
  - `max_num_seqs`
  - `enable_chunked_prefill`
  - `long_prefill_token_threshold`
  - `policy`: 第一版只支持 `fcfs`
- latency backend 配置：
  - `backend`: `formula` / `aiconfigurator` / `markov_infer_sim`
  - `model_profile`
  - `hardware_profile`
  - backend-specific options

## Step4 输出

第一版报告仍以 CSV + Markdown 为主：

- `request_metrics.csv`
  - `request_id`
  - `tenant_id`
  - `instance_uuid`
  - `arrival_time`
  - `prompt_tokens`
  - `cached_tokens`
  - `miss_tokens`
  - `hit_rate`
  - `first_scheduled_time_ms`
  - `finish_time_ms`
  - `scheduler_wait_ms`
  - `ttft_ms`
  - `iterations`
- `iteration_metrics.csv`
  - `instance_uuid`
  - `iteration_id`
  - `start_time_ms`
  - `duration_ms`
  - `batch_size`
  - `scheduled_prefill_tokens`
  - `scheduled_decode_tokens`
  - `active_request_count`
  - `backend`
  - `shape_key`
- `summary.md`
  - request count
  - total prompt / cached / miss tokens
  - hit rate
  - P50/P90/P99 TTFT
  - average and P90 batch size
  - average and P90 scheduled prefill tokens
  - latency backend used

## 显式不做

Step4 不做：

- 跨实例 KV cache 命中。
- 有限 HBM LRU / DDR LRU 淘汰。
- 路由策略仿真。
- 多租户隔离策略变更。
- decode TPOT 完整仿真。
- 真实 vLLM block manager 完整复刻。

Step4 可以记录 decode 相关字段，但第一版 batch replay 只对 TTFT 相关 prefill 完成时间负责。

## 关键产品口径

### batch_admission_delay

此前已经接受 `batch_admission_delay = 0`，含义是“不额外引入人为攒批等待窗口”。也就是说，只要 scheduler 当前 iteration 有预算、请求已经到达、资源约束允许，请求可以立即被纳入下一次调度。

这和 continuous batching 不冲突：

- `batch_admission_delay = 0` 是 admission policy：不为了凑 batch 主动等待。
- continuous batching 是 execution policy：每个 iteration 都可以从 waiting/running 中选择请求组成 batch，而不是等整批请求全部完成后再换下一批。

Step4 引入 batch replay 后，仍可能产生 `scheduler_wait_ms`，来源不是人为 admission delay，而是：

- 当前 iteration 已经开始，请求只能等待下一次 schedule。
- token budget / seq budget 已满。
- 长 prefill 被 chunked，需要跨多个 iteration。
- FCFS 队列前方请求占用预算。

因此 Step4 输出中应区分：

- `batch_admission_delay_ms`: 第一版固定为 0。
- `scheduler_wait_ms`: replay 自然产生的等待。
- `ttft_ms`: 默认按 `finish_time_ms - arrival_time_ms` 计算；如果后续产品口径要求排除 scheduler wait，应在报告中显式输出另一列，不要混淆。
