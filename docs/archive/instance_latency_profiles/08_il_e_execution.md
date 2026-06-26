# 外围 Batch IL-E 执行记录：Unrouted Trace Normalizer

执行时间：2026-06-26

任务类型：外围能力开发。

状态：已完成并收口。

## 1. 完成内容

本批实现了 `normalize-trace` 外围能力，用于在 replay 前把无 `instance_uuid`
的 trace 转成单实例 routed trace。

新增：

```text
src/hitfloor/trace/normalizer.py
scripts/normalize_unrouted_trace.py
tests/unit/trace/test_normalizer.py
tests/integration/test_trace_normalizer_cli.py
tests/integration/test_unrouted_trace_normalizer_e2e.py
```

修改：

```text
src/hitfloor/cli/main.py
README.md
docs/archive/instance_latency_profiles/README.md
docs/development_governance.md
docs/global_memory.md
```

## 2. 用户入口

Package CLI：

```bash
PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main normalize-trace \
  --input data/raw/unrouted_trace.csv \
  --output data/processed/routed_single_instance_trace.csv \
  --instance-uuid single-instance
```

Script wrapper：

```bash
.venv/bin/python scripts/normalize_unrouted_trace.py \
  --input data/raw/unrouted_trace.csv \
  --output data/processed/routed_single_instance_trace.csv \
  --instance-uuid single-instance
```

## 3. 语义边界

`normalize-trace` 是外围数据准备能力，不属于核心仿真器 replay 能力。

核心仿真器仍要求：

```text
trace 中存在 instance_uuid
```

Normalizer 只做：

- 逐行读取 unrouted trace。
- 给每一行写入同一个 `instance_uuid`。
- 输出新的 routed trace。

Normalizer 不做：

- 不做 gateway routing simulation。
- 不根据负载、租户、时间或 cache 状态选择实例。
- 不解析 request JSON。
- 不 tokenize。
- 不构造 `SimulationRequest`。
- 不修改 scheduler/cache/latency/replay 行为。

## 4. v1 输入规则

支持：

```text
输入 CSV 完全没有 instance_uuid 列
```

拒绝：

```text
输入 CSV 已经有 instance_uuid 列
```

原因：避免把真实 routed trace 误覆盖成统一实例 id。v1 不提供
`--overwrite` 或 `--fill-empty-only`。

## 5. 未修改的核心模块

以下核心模块未引入默认实例逻辑：

```text
src/hitfloor/trace/reader.py
src/hitfloor/trace/schema.py
src/hitfloor/experiment/request_builder.py
src/hitfloor/streaming/
src/hitfloor/replay/
```

## 6. 定向验证

已通过：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/trace/test_normalizer.py \
  tests/integration/test_trace_normalizer_cli.py \
  tests/integration/test_unrouted_trace_normalizer_e2e.py
```

结果：

```text
10 passed
```

覆盖：

- 无 `instance_uuid` trace 可生成 routed trace。
- 有 `instance_uuid` trace fail-fast。
- 缺基础列 fail-fast。
- 输出路径存在 fail-fast。
- 空 trace 保留 header。
- package CLI 可用。
- script wrapper 可用。
- raw unrouted trace 直接 `validate-trace` 仍 fail-fast。
- normalized routed trace 可进入 `validate-trace` 和 `sweep-streaming`。

## 7. 工程收口验证

已完成：

```text
PYTHONPATH=src .venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
git diff --check
```

结果：

```text
PYTHONPATH=src .venv/bin/python -m pytest: 209 passed
.venv/bin/python -m ruff check src tests scripts: passed
.venv/bin/python -m ruff format --check src tests scripts: passed
git diff --check: passed
```

收口检查：

- `src/hitfloor/trace/reader.py` 未修改核心 required columns 语义。
- `src/hitfloor/trace/schema.py` 未把 `instance_uuid` 变成 optional。
- `src/hitfloor/experiment/request_builder.py` 未加入默认实例逻辑。
- `src/hitfloor/streaming/` 未自动调用 normalizer。
- `src/hitfloor/replay/` 未感知 normalizer。
- normalizer 逻辑只在 `trace/normalizer.py`、CLI、script wrapper 和测试中出现。

## 8. 遗留问题

真正的 gateway routing simulation 仍未实现。未来如果要消费无
`instance_uuid` trace 并按策略分配实例，应新增 gateway layer，而不是扩展
`normalize-trace` 的语义。
