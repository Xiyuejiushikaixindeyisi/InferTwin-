# EO-G 执行记录：大 Trace 性能与事件安全

## 开发对象

```text
核心仿真器
```

EO-G 不新增外围报表能力，不改变 `batch_aware_hbm_lru` 的 cache / scheduler / latency 语义。

## 背景

公司内 trace 可能达到：

- CSV 文件约 11G。
- 请求数量几万级。
- 单请求 prompt token 数约 32K 到 200K，未来可能更长。

因此 EO-G 重点处理两类风险：

- request build 阶段不必要地持有大对象。
- cache event 明细在大 trace 下堆内存。

本轮新增一条硬边界：

```text
超过 tokenizer / profile 处理上限的长请求，必须在 tokenizer 阶段拒绝处理，不进入 replay。
```

## 本轮实现

### 1. Request Build 不再先持有全量 TraceRecord

修改：

```text
src/hitfloor/experiment/request_builder.py
```

旧路径：

```text
records = list(read_trace_csv(trace_path))
-> build_simulation_requests(records, ...)
```

新路径：

```text
for record in read_trace_csv(trace_path):
    build one SimulationRequest
    append accepted request
```

效果：

- 不再同时持有全量 `TraceRecord` 列表和 `SimulationRequest` 列表。
- 仍然会持有已接受的 `SimulationRequest`，因为当前 replay 仍然需要按实例和时间 deterministic sort。
- 仍不实现 true streaming request build；这是后续更大工程，不在 EO-G 本轮范围。

### 2. Tokenizer 阶段长请求拒绝

修改：

```text
src/hitfloor/request/tokenizer_registry.py
src/hitfloor/request/build_context.py
src/hitfloor/instance/request.py
src/hitfloor/experiment/request_builder.py
```

新增：

```text
PromptTooLongError
RejectedTraceRecord
RequestBuildResult
build_request_build_result_from_config()
```

处理顺序：

```text
TraceRecord
-> parse request_params
-> validate request model
-> tokenizer + chat template
-> count prompt tokens
-> if prompt_tokens > max_prompt_tokens: reject
-> build prefix blocks
-> SimulationRequest
```

`PromptTooLongError` 只在 tokenization 已得到真实 prompt token 数后触发。被拒绝请求不会：

- 生成 `SimulationRequest`。
- 进入 scheduler waiting queue。
- 进行 cache lookup。
- 产生 cache event。
- 进入 TTFT / hit-rate 指标分母。

### 3. max_prompt_tokens 来源

legacy config：

```yaml
tokenizers:
  max_prompt_tokens: 131072
```

如果 legacy config 未设置 `tokenizers.max_prompt_tokens`，保持旧行为，不启用长度拒绝。

profile-aware config：

```text
effective_max_prompt_tokens =
  min(
    tokenizers.max_prompt_tokens if provided,
    ModelProfile.max_model_len if provided,
    DeploymentProfile.startup_args.max_model_len if provided
  )
```

取最小值的原因：

- model profile 表示模型/tokenizer 可接受上限。
- deployment startup args 表示实际服务启动上限。
- 显式 tokenizer config 可能用于本次实验更保守地拒绝请求。

如果这些字段都不存在，则不启用长度拒绝。

### 4. Request Build 拒绝结果

兼容入口仍保留：

```text
build_requests_from_config(config) -> list[SimulationRequest]
```

新增可审计入口：

```text
build_request_build_result_from_config(config) -> RequestBuildResult
```

`RequestBuildResult` 包含：

```text
requests
rejected_records
accepted_count
rejected_count
```

`RejectedTraceRecord` 包含：

```text
request_id
tenant_id
instance_uuid
reason
detail
prompt_tokens
max_prompt_tokens
tokenizer_profile
```

当前只捕获明确的 `PromptTooLongError`。以下错误仍然直接失败，不会被当作“可丢弃请求”吞掉：

- request JSON/schema 错误。
- model mismatch。
- tokenizer profile 缺失。
- config guard 失败。
- block conversion 不支持。

单次 `ExperimentRunner` 会在存在拒绝请求时写出：

```text
rejected_requests.csv
```

并在 `ExperimentResult.metrics` 中记录：

```text
request_build_accepted_count
request_build_rejected_count
rejected_requests_path
```

`CapacitySweepRunner` 会在 `config_details` 中记录 accepted / rejected 计数，但不为每个 capacity 重复写 rejected 明细。

### 5. Cache Event 安全

修改：

```text
src/hitfloor/cache/event_sink.py
src/hitfloor/replay/event_loop.py
```

变化：

- `BatchAwareReplayEngine.run()` 默认使用 `StatsOnlyCacheEventSink`，不再使用 `NullCacheEventSink`。
- 默认 replay 不保存 `cache_events` payload，但会保留 `cache_event_stats`。
- `InMemoryCacheEventSink` 新增默认 `max_events = 100000`。
- 如果超过上限，直接抛出 `MemoryError`，提示改用 `StatsOnlyCacheEventSink` 或 `CsvCacheEventWriter`。

这让小测试仍然可以拿 event payload，大 trace 默认只保留统计，不会在内存中堆大量事件明细。

## 暂不做

本轮不实现：

- true streaming request build。
- 多进程 / 多线程 per-instance replay。
- request streaming tokenizer。
- token id 列表完全零拷贝。
- 大规模 benchmark 进入默认 pytest。
- 根据 rejected records 自动改写 trace。
- 把拒绝请求计入 hit-rate / TTFT 指标。

## 测试

新增/更新：

```text
tests/unit/experiment/test_request_builder.py
tests/unit/cache/test_cache_event_sink.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
tests/integration/test_step5_hbm_lru_runner.py
```

覆盖：

- legacy config 下 `tokenizers.max_prompt_tokens` 会拒绝超长请求。
- profile-aware path 使用 `ModelProfile.max_model_len` 作为默认拒绝上限。
- 拒绝请求不会进入 accepted requests。
- `InMemoryCacheEventSink` 超过 `max_events` 后失败。
- 默认 finite HBM replay 不保存 event payload，但保留 event stats。
- `ExperimentRunner` 对被拒绝请求写出 `rejected_requests.csv` 并暴露 rejected count。

聚焦测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/experiment/test_request_builder.py \
  tests/unit/cache/test_cache_event_sink.py \
  tests/unit/replay/test_batch_aware_replay_hbm_lru.py
```

结果：

```text
19 passed
```

## 工程优化收口 Review 结论

EO-G 完成的是大 trace 下的第一层安全改造：

- 去掉 request build 中不必要的全量 `TraceRecord` 持有。
- 明确超长请求在 tokenizer 阶段拒绝。
- 默认 cache event path 只保留 stats，不保留 payload。
- 小测试用 in-memory event sink 有事件数上限。

仍需后续继续评估：

- `SimulationRequest` 仍按全量 accepted requests 持有。
- 每个 accepted request 仍会临时持有 `prompt_token_ids` 直到 prefix blocks 构造完成。
- prefix blocks 会按 block 粒度持有 hash 链，200K tokens / 128 block size 约 1563 blocks/request。
- 如果未来单请求 token 数继续增长，需要评估 streaming tokenizer、rolling block hash 和 per-instance request shard。
