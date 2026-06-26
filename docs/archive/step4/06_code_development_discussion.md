# Step4 Code Development Discussion

本文件用于 Step4 进入代码开发前的最终讨论。它承接已审批的产品形态和技术路线，不引入新的产品范围。

## 已审批决策

- Step4 只做固定路由、多实例隔离 replay；每个实例内无限 HBM、vLLM-like batch-aware replay；不做请求路由仿真。
- `ttft_ms = finish_time - arrival_time`。
- 单独输出 `scheduler_wait_ms`。
- Step4 第一版不建模 decode TPOT 对 prefill 的干扰。
- `batch_size` 在 HitFloor 内部定义为 iteration 内 request slice 数。
- Batch C 使用 `FormulaLatencyBackend` 完成开发和测试闭环。
- Batch D 默认 backend 调整为拟合型 TTFT 函数 backend：`FittedTTFTLatencyBackend` / `fitted_ttft`。
- `token_linear_v1` 函数固定为：`duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens`。
- 外部 simulator adapter 延后。

## 代码开发目标

Step4 完成后，HitFloor 应具备：

1. 按实例 replay trace。
2. 将请求放入 waiting/running 状态机。
3. 使用 vLLM-like scheduler 产生 iteration-level batch shape。
4. 使用 fitted TTFT backend 估算每个 iteration duration。
5. 使用 duration 推进 finish time。
6. 只有请求 prefill 完成后才 materialize miss blocks。
7. 输出 request metrics、iteration metrics 和 summary。

## 推荐开发批次

### Batch A: Schema + Latency Foundation

新增：

```text
src/hitfloor/scheduler/
  __init__.py
  config.py
  state.py
  batch_shape.py

src/hitfloor/latency/
  __init__.py
  schema.py
  backend.py
  formula.py
  fitted_ttft.py
  memo.py

tests/unit/latency/
  test_formula_backend.py
  test_shape_memo.py
```

开发内容：

- `SchedulerConfig`
- `RequestState`
- `ScheduledSlice`
- `BatchShape`
- `LatencyResult`
- `LatencyBackend` protocol
- `FormulaLatencyBackend`
- `FittedTTFTLatencyBackend`
- `ShapeMemo`

验收：

- formula latency 随 scheduled prefill tokens 增加。
- formula latency 随 batch size 增加。
- fitted TTFT latency 随 scheduled prefill tokens 增加。
- shape memo 相同 shape 命中，不同 model/hardware 隔离。
- 所有 schema 可直接在单测中构造。

### Batch B: VllmLikeBatchScheduler

新增：

```text
src/hitfloor/scheduler/vllm_like.py

tests/unit/scheduler/
  test_vllm_like_scheduler.py
  test_chunked_prefill.py
```

开发内容：

- FCFS waiting queue。
- running-first scheduling。
- `max_num_batched_tokens` 约束。
- `max_num_seqs` 约束。
- chunked prefill。
- no-chunked 模式下预算不足时不调度超长请求。

验收：

- token budget 不被突破。
- seq budget 不被突破。
- 长 prompt 可跨 iteration 调度。
- 同一输入重复运行结果一致。

### Batch C: BatchAwareReplayEngine

新增：

```text
src/hitfloor/replay/
  __init__.py
  event_loop.py
  metrics.py

tests/unit/scheduler/
  test_prefix_lookup_timing.py

tests/integration/
  test_step4_batch_aware_replay.py
```

开发内容：

- per-instance event loop。
- 请求 arrival -> waiting。
- 首次调度前进行 prefix cache lookup。
- scheduler 产生 `BatchShape`。
- latency backend 推进 iteration finish time。
- prefill 完成后 materialize miss blocks。
- 生成 request metrics 和 iteration metrics。

验收：

- cache block 在 finish_time 前不可见。
- cache block 在 finish_time 后可见。
- `ttft_ms = finish_time - arrival_time`。
- `scheduler_wait_ms = first_scheduled_time - arrival_time`。
- `sum(scheduled_prefill_tokens per request) == miss_tokens`。
- 两个实例之间不共享 cache。

### Batch D: Runner + Report Integration

修改：

```text
src/hitfloor/experiment/runner.py
scripts/run_simulation.py
configs/experiments/default.yaml
```

可能新增：

```text
src/hitfloor/report/
  iteration_csv.py
```

开发内容：

- config 增加 scheduler 和 latency 配置。
- runner 可选择 Step4 batch-aware replay。
- 输出 `iteration_metrics.csv`。
- 扩展 `request_metrics.csv`。
- 扩展 `summary.md`。

验收：

- CLI 仍只负责参数解析和调用 runner。
- report writer 只消费 replay 结果，不重算核心逻辑。
- 现有 Step1-Step3 流程不被破坏，或以明确 config 切换。
- synthetic E2E 通过。

## 配置草案

```yaml
simulation:
  mode: batch_aware_infinite_hbm

scheduler:
  policy: fcfs
  max_num_batched_tokens: 8192
  max_num_seqs: 32
  enable_chunked_prefill: true
  long_prefill_token_threshold: 4096

latency:
  backend: fitted_ttft
  model_name: glm-v5
  hardware_name: local-dev
  fitted_ttft:
    profile: glm-v5_ascend910c_default
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 0.02
    calibrated_from: manual_default
```

## 开发时需要保护的既有行为

- tokenizer registry 不应被 scheduler 逻辑污染。
- block hasher 不保存全量 token ids。
- infinite HBM cache 继续只保存 hash key 和 metadata。
- Step1-Step3 的无限 HBM replay 作为兼容路径保留，除非用户明确同意替换。
- 外部 simulator adapter 不在 Step4 第一批代码中实现。

## 建议先开发的最小闭环

第一轮代码开发建议只做 Batch A + Batch B：

```text
schema + formula latency + shape memo + vLLM-like scheduler
```

原因：

- 这部分不改 runner，不影响现有端到端链路。
- 可以先用单测把 batch shape 的口径锁死。
- replay event loop 依赖这些类型，先把边界固定后再接主流程更稳。

第一轮完成并评审通过后，再进入 Batch C + Batch D。
