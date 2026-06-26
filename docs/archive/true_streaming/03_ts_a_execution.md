# Batch TS-A 执行记录：Schema / Codec

执行时间：2026-06-26

任务类型：核心仿真器架构任务。

状态：已完成。

## 1. 本批目标

Batch TS-A 只完成 true streaming 的基础 schema 和 codec：

- 新增 streaming package。
- 定义 streaming shard manifest schema。
- 定义 `SimulationRequest` 的 JSON-compatible codec。
- 增加单元测试覆盖 roundtrip、schema guard 和错误路径。

本批不接入：

- request shard writer。
- streaming request builder。
- streaming replay engine。
- capacity sweep runner。
- CLI / report。

因此本批不会改变现有 `capacity_sweep`、`BatchAwareReplayEngine.run(list[SimulationRequest])` 或 `batch_aware_hbm_lru` replay 语义。

## 2. 新增代码

```text
src/hitfloor/streaming/__init__.py
src/hitfloor/streaming/manifest.py
src/hitfloor/streaming/request_codec.py
```

### 2.1 `manifest.py`

新增：

- `STREAMING_MANIFEST_SCHEMA_VERSION`
- `RequestShard`
- `StreamingBuildManifest`

职责：

- 描述一次 streaming request build 的 shard 输出。
- 校验 manifest schema version。
- 校验 shard request count 与 accepted count 一致。
- 校验 shard 时间范围合法。

不负责：

- 写 manifest 文件。
- 读 manifest 文件。
- 构造 shard。
- replay。

这些能力留给 Batch TS-B。

### 2.2 `request_codec.py`

新增：

- `STREAMING_REQUEST_SCHEMA_VERSION`
- `encode_simulation_request()`
- `decode_simulation_request()`
- `encode_simulation_request_line()`
- `decode_simulation_request_line()`

职责：

- 将 `SimulationRequest` 编码为稳定 JSON-compatible mapping。
- 将 mapping / JSONL line 解码回 `SimulationRequest`。
- 保留 replay 所需字段：
  - request identity。
  - instance routing。
  - model / tokenizer profile。
  - prompt token count。
  - hash-only `PrefixBlock` chain。
  - block size / cached-token accounting metadata。

明确不保存：

- raw request JSON。
- messages。
- token ids。
- 真实 KV tensor。

## 3. 新增测试

```text
tests/unit/streaming/test_manifest.py
tests/unit/streaming/test_request_codec.py
```

覆盖：

- manifest count validation。
- manifest schema mismatch。
- shard time bound validation。
- `SimulationRequest` dict roundtrip。
- JSONL line roundtrip。
- `block_conversion_result=None` roundtrip。
- request schema version mismatch。
- 缺少 required field。
- invalid JSON line。
- invalid prompt block。

## 4. 验证结果

定向测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/streaming/test_manifest.py tests/unit/streaming/test_request_codec.py
11 passed
```

静态检查：

```text
.venv/bin/python -m ruff check src tests scripts
All checks passed!

.venv/bin/python -m ruff format --check src tests scripts
122 files already formatted
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
163 passed
```

## 5. 收口结论

Batch TS-A 已完成。

当前 true streaming 仍未接入主链路，但 schema / codec 已具备给 Batch TS-B 使用的基础：

```text
CSV row -> SimulationRequest -> JSONL record -> SimulationRequest
```

下一批建议进入 Batch TS-B：

```text
Streaming Request Shard Builder
```

Batch TS-B 的核心目标是：

- 逐行读取 CSV。
- 逐条 build `SimulationRequest`。
- 按 instance 写入 JSONL shard。
- 流式写 rejected request sidecar。
- 生成 `StreamingBuildManifest`。
- 加入 sorted trace guard。

