# Batch TS-B 执行记录：Streaming Request Shard Builder

执行时间：2026-06-26

任务类型：核心仿真器架构任务。

状态：已完成。

## 1. 本批目标

Batch TS-B 实现 true streaming 的 request build 阶段：

```text
CSV trace row
-> tokenizer / chat template / prefix hash
-> SimulationRequest
-> per-instance JSONL shard
```

本批目标是去掉 request build 阶段对全量 accepted request list 的依赖，为后续 streaming replay 提供磁盘 shard 输入。

本批不接入：

- streaming replay engine。
- streaming capacity sweep runner。
- CLI / report。
- external sort。

因此本批不会改变现有 `capacity_sweep`、`BatchAwareReplayEngine.run(list[SimulationRequest])` 或 `batch_aware_hbm_lru` replay 语义。

## 2. 新增代码

```text
src/hitfloor/streaming/shard_store.py
src/hitfloor/streaming/build.py
```

同时对现有 request builder 做了小型复用性重构：

```text
src/hitfloor/experiment/request_builder.py
```

新增：

- `RequestBuildSettings`
- `build_request_build_settings_from_config()`
- `build_prompt_too_long_rejection()`

目的：

- 旧全量 builder 和新 streaming builder 复用同一套 trace/tokenizer/cache/profile 配置解析。
- 避免 streaming path 复制一份私有配置逻辑，导致后续 profile 语义分叉。

## 3. `shard_store.py`

新增：

- `StreamingRequestShardStore`
- `shard_path_for_instance()`

职责：

- 按 `instance_uuid` 写一个 JSONL shard。
- 每条 accepted request 使用 TS-A 的 `encode_simulation_request_line()`。
- 只保存 hash-only replay metadata。
- 统计每个 shard 的 request count、min/max start time。

不负责：

- 读取 CSV。
- tokenizer。
- rejected request。
- replay。

## 4. `build.py`

新增：

- `StreamingRequestShardBuilder`
- `StreamingBuildResult`
- `CsvRejectedTraceRecordWriter`
- `UnsortedTraceError`

职责：

- 逐行读取 CSV。
- 逐条 build `SimulationRequest`。
- 对 accepted request 写入 per-instance JSONL shard。
- 对 `PromptTooLongError` 写入 `rejected_requests.csv` sidecar。
- 构造 `StreamingBuildManifest`。
- 在 `require_sorted_trace=True` 时检查 trace sort key：

```text
(service_start_time, instance_uuid, request_id)
```

失败行为：

- unsorted trace 直接抛 `UnsortedTraceError`，错误包含 line number、previous key、current key。
- prompt too long 不写 shard，只写 rejected sidecar。
- parse error / config error 继续 fail-fast，不静默跳过。

## 5. 新增测试

```text
tests/unit/streaming/test_build.py
```

覆盖：

- sorted trace 生成 per-instance shards。
- shard JSONL 可 decode 回 `SimulationRequest`。
- prompt too long 写 rejected sidecar。
- unsorted trace fail-fast。
- 关闭 sorted guard 后仍可 build，并正确统计 shard min/max time。

## 6. 验证结果

定向测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/streaming/test_manifest.py \
  tests/unit/streaming/test_request_codec.py \
  tests/unit/streaming/test_build.py \
  tests/unit/experiment/test_request_builder.py

21 passed
```

静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
125 files already formatted
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
167 passed
```

覆盖率：

```text
PYTHONPATH=src .venv/bin/python -m pytest --cov=hitfloor --cov-report=term-missing
167 passed
TOTAL 3188 statements, 229 missed, 93% coverage
```

新增 streaming 模块覆盖情况：

| 模块 | 覆盖率 |
| --- | ---: |
| `streaming/build.py` | 99% |
| `streaming/manifest.py` | 89% |
| `streaming/request_codec.py` | 87% |
| `streaming/shard_store.py` | 97% |

## 7. 收口结论

Batch TS-B 已完成。

当前 true streaming 已具备：

```text
CSV row -> SimulationRequest -> per-instance JSONL shard
```

但还没有：

```text
per-instance JSONL shard -> streaming replay
```

下一批建议进入 Batch TS-C：

```text
RequestSource 与 Streaming Replay Engine
```

Batch TS-C 的核心目标是：

- 增加 `JsonlRequestSource` / `ListRequestSource`。
- 增加 streaming replay engine。
- 将 pending list + pending_index 替换为 source.peek()/pop()。
- request finish 后释放 active state。
- 与现有 list replay 在 synthetic trace 上做指标等价测试。
