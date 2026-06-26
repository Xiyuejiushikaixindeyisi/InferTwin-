# HitFloor 仿真器骨架评审与外围 Unrouted Trace Normalizer 方案

执行时间：2026-06-26

任务类型：核心仿真器能力评审 + 外围能力代码方案。

状态：仿真器骨架评审已记录；外围能力方案已审批并按 IL-E1 到 IL-E5 实现。

执行记录：

```text
docs/archive/instance_latency_profiles/08_il_e_execution.md
```

本文包含两个独立部分：

- 第 1-4 节：评审当前核心仿真器骨架、处理链路和回放能力。
- 第 5-6 节：设计外围 Unrouted Trace Normalizer，用于在 replay 前把无 `instance_uuid` 的 trace 补成 routed trace。

Batch IL-E 是外围能力，不属于核心仿真器设计。

## 1. 当前代码结构

核心仿真器主路径：

```text
src/hitfloor/
  trace/       CSV trace schema and reader
  request/     request parser, tokenizer registry, chat template, prefix block hash
  instance/    SimulationRequest build
  scheduler/   vLLM-like scheduler, chunked prefill planning, waiting queue
  cache/       HBM cache, block accounting, materialization, eviction, events
  latency/     fitted TTFT, ServingLatencyProfile, instance latency resolver
  replay/      in-memory batch-aware replay engine
  streaming/   request sharding, streaming replay, streaming capacity sweep
  experiment/  request build and capacity sweep orchestration
  report/      CSV / Markdown report/export
  cli/         package CLI
```

核心与外围边界：

- `trace/request/instance/scheduler/cache/latency/replay/streaming/experiment` 承载核心 replay 语义。
- `report/cli/scripts` 消费 typed result，不重算 replay 语义。

## 2. 当前处理逻辑

普通 in-memory path：

```text
CSV trace
-> TraceRecord
-> parse request_params
-> tokenizer + chat template
-> prompt token ids
-> block size / cached_tokens accounting context
-> prefix block hash
-> SimulationRequest
-> BatchAwareReplayEngine.run()
-> group by instance_uuid
-> per-instance replay
-> cache lookup
-> scheduler iteration
-> latency estimate
-> finish-time materialization
-> metrics
```

true streaming path：

```text
CSV trace
-> StreamingRequestShardBuilder
-> per-instance JSONL request shards
-> JsonlRequestSource
-> StreamingBatchAwareReplayEngine.run_instance_stream()
-> CapacitySweepStreamingMetricAggregator
-> StreamingCapacitySweepRunner
-> CapacitySweepResult
```

如果配置了实例级 latency profile：

```text
shard.instance_uuid
-> InstanceLatencyBackendResolver.backend_for(instance_uuid)
-> instance fitted TTFT backend
-> StreamingBatchAwareReplayEngine
```

## 3. 当前调用链

普通 capacity sweep：

```text
CapacitySweepRunner.run()
-> build_request_build_result_from_config()
-> read_trace_csv()
-> build_simulation_request()
-> BatchAwareReplayEngine.run()
-> build_capacity_rows()
-> CapacitySweepResult
```

streaming capacity sweep：

```text
StreamingCapacitySweepRunner.run()
-> StreamingRequestShardBuilder.build()
-> read_trace_csv()
-> build_simulation_request()
-> write per-instance shards
-> for each capacity
   -> for each shard
      -> latency_resolver.backend_for(shard.instance_uuid)
      -> StreamingBatchAwareReplayEngine.run_instance_stream()
      -> HBMCache(capacity_blocks)
      -> CapacitySweepStreamingMetricAggregator
-> CapacitySweepResult
```

## 4. 回放能力评审

### 4.1 单实例回放

已具备。

当前前提：

```text
trace 中存在 instance_uuid，并且所有请求的 instance_uuid 相同
```

此时 HitFloor 会按一个实例 replay，实例有独立：

- waiting queue。
- running list。
- HBM cache。
- iteration clock。
- request states。

当前不足：

```text
没有 instance_uuid 列时，当前 read_trace_csv() 会 fail-fast。
```

如果用户不想做路由仿真，但 trace 又没有实例 id，正确做法不应是在核心 reader 中隐式补实例 id。

更合理的边界是：

```text
外围 trace normalization
-> 补充统一 instance_uuid
-> 输出 routed trace
-> 核心仿真器继续读取 routed trace
```

这样不会让用户误以为“无实例 id”已经触发 gateway routing。

### 4.2 多个相同配置实例回放

已具备。

当前语义：

```text
same global scheduler/cache/latency config
different instance_uuid
-> isolated per-instance replay state
```

普通 replay 通过 `BatchAwareReplayEngine.run()` 按 `instance_uuid` 分组。

streaming replay 通过 per-instance JSONL shard 隔离。

### 4.3 多个不同配置实例回放

部分具备。

当前已支持：

```text
true streaming path:
  instance_uuid -> fitted TTFT backend
```

当前不支持：

- per-instance scheduler config。
- per-instance cache capacity。
- per-instance block size / deployment profile。
- dynamic per-500-request TTFT refit。
- DDR / remote KV-load latency materialization。

因此当前应称为：

```text
heterogeneous fitted TTFT replay
```

不应称为完整 heterogeneous instance cluster replay。

## 5. 特殊情况：没有实例 ID

外围能力目标语义：

```text
如果用户明确不想做路由仿真，且 trace 没有实例 id，
可以先由外围能力把所有请求补成同一个 instance_uuid。
```

当前代码现状：

```text
read_trace_csv() requires instance_uuid column
```

这是合理的核心边界：核心仿真器消费 routed trace，不在 reader 内隐式创造路由信息。

建议将该需求作为外围 Batch IL-E，但 Batch IL-E 必须定位为外围能力，而不是核心仿真器设计。

## 6. 外围 Batch IL-E：Unrouted Trace Normalizer

### 6.1 阶段定位

本批是外围能力，不是核心仿真器能力。

目标：

```text
将没有 instance_uuid 的 trace 预处理成带统一 instance_uuid 的 routed trace
```

核心仿真器仍保持：

```text
read_trace_csv() requires instance_uuid column
```

非目标：

- 不实现 gateway routing。
- 不实现路由策略仿真。
- 不修改现有 routed trace 语义。
- 不修改 cache/scheduler/latency/replay 行为。
- 不在核心 reader 中引入 `trace.default_instance_uuid`。

### 6.2 配置语义

新增外围命令或脚本参数：

```bash
hitfloor normalize-trace \
  --input data/raw/unrouted_trace.csv \
  --output data/processed/routed_single_instance_trace.csv \
  --instance-uuid default-instance
```

规则：

| 输入情况 | 行为 |
| --- | --- |
| 输入 CSV 无 `instance_uuid` | 输出 CSV 增加 `instance_uuid` 列，所有行填统一实例 id |
| 输入 CSV 有 `instance_uuid` | fail-fast，避免误覆盖真实路由 |
| `--instance-uuid` 为空 | fail-fast |

Batch IL-E v1 不实现 `--overwrite` 或 `--fill-empty-only`。如果后续确实需要修补部分空实例 id，应新增独立外围能力或显式新参数，并重新评审风险。

如果同时启用：

```yaml
instance_latency.profile_path
```

则实例表必须包含这个统一实例 id，否则继续由 `InstanceLatencyBackendResolver` fail-fast。

归一化后的 trace 对核心仿真器来说就是普通 routed trace。

### 6.3 建议代码修改

建议新增：

```text
src/hitfloor/trace/normalizer.py
tests/unit/trace/test_normalizer.py
```

建议修改：

```text
src/hitfloor/cli/main.py
scripts/normalize_unrouted_trace.py
```

不修改：

```text
src/hitfloor/trace/reader.py
src/hitfloor/trace/schema.py
src/hitfloor/experiment/request_builder.py
src/hitfloor/streaming/build.py
src/hitfloor/replay/
```

原因：核心仿真器输入仍然是 routed trace。无实例 id 的处理属于 replay 前的数据准备，不属于 core replay 语义。

### 6.4 接口设计

新增 pure function：

```python
def normalize_unrouted_trace(
    input_path: str | Path,
    output_path: str | Path,
    *,
    instance_uuid: str,
) -> TraceNormalizeResult:
    ...
```

`TraceNormalizeResult` 建议包含：

```text
input_path
output_path
row_count
added_instance_uuid_column
filled_empty_instance_uuid_count
instance_uuid
```

Batch IL-E v1 中 `filled_empty_instance_uuid_count` 固定为 0；保留该字段是为了让结果 schema 能表达“没有做部分填充”，不是为了启用部分填充行为。

CLI：

```text
hitfloor normalize-trace --input ... --output ... --instance-uuid ...
```

script wrapper：

```text
scripts/normalize_unrouted_trace.py
```

### 6.5 与核心仿真器的关系

归一化前：

```text
unrouted trace
  request_id,tenant_id,request_params,service_start_time
```

归一化后：

```text
routed trace
  request_id,tenant_id,instance_uuid,request_params,service_start_time
```

核心仿真器只消费归一化后的 routed trace：

```text
hitfloor sweep-streaming --config config_that_points_to_routed_trace.yaml
```

该外围能力不能：

- 修改 request params。
- 修改时间戳。
- 修改 tenant。
- 修改 cache/scheduler/latency 语义。
- 根据负载选择实例。
- 做 gateway routing。

### 6.6 测试计划

新增 / 修改：

```text
tests/unit/trace/test_normalizer.py
tests/integration/test_trace_normalizer_cli.py
```

覆盖：

- 无 `instance_uuid` 列 -> 输出增加该列。
- 输出所有行填同一个 `instance_uuid`。
- `--instance-uuid` 为空 -> fail-fast。
- 输入已有 `instance_uuid` -> fail-fast。
- 输入缺少基础 trace 字段 -> fail-fast。
- output path 已存在 -> fail-fast，避免覆盖已有 trace。
- 空数据 trace -> 保留 header，`row_count = 0`。
- 输出 CSV 保留原字段和行数。
- CLI 能生成 routed trace。

### 6.7 验收标准

定向测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/trace/test_normalizer.py \
  tests/integration/test_trace_normalizer_cli.py
```

全量回归：

```text
PYTHONPATH=src .venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
git diff --check
```

### 6.8 外围能力风险与边界

核心边界：

- `normalize-trace` 不属于核心仿真器 replay 能力。
- `normalize-trace` 只生成核心仿真器可消费的 routed trace。
- 核心仿真器仍要求输入 trace 中存在 `instance_uuid`。
- 核心仿真器不会因为输入缺少 `instance_uuid` 而自动补默认实例。

外围能力风险：

- 如果用户以为 normalize trace 代表做了 gateway routing，会误解。
- 如果用户把真实 routed trace 误覆盖成统一实例 id，会破坏现网路由信息。
- 如果用户使用统一实例 id 跑单实例 baseline，结果只能解释为“无路由仿真的单实例 replay”，不能解释为“集群路由效果”。
- 如果启用 instance latency profile，归一化时填入的统一实例 id 必须存在于实例表中，否则应由后续 resolver fail-fast。

禁止行为：

- 不根据请求负载、租户、时间或 cache 状态选择实例。
- 不生成或修改 scheduler/cache/latency/replay 信号。
- 不改变 request params、到达时间、tenant、tokenizer、block hash 或 cache policy。
- 不在 report 中把 normalizer 结果描述为 gateway simulation。

文档口径：

```text
normalize-trace 只是把无实例 id trace 转为单实例 routed trace。
它不是 gateway routing simulation。
它是 replay 前的数据准备外围能力，不是核心仿真器能力。
```

如果要模拟路由，应进入 future gateway simulation 阶段，并新增 gateway layer。gateway layer 可以消费无 `instance_uuid` trace，并根据明确的路由策略生成 routed trace 或直接驱动核心 replay；这属于未来核心仿真器扩展，不属于外围 normalizer。

### 6.9 Batch 开发顺序

命名约定：

- `IL-A`、`IL-B`、`IL-C`、`IL-D` 是已经完成的 Instance Latency Profiles 核心仿真器专项批次。
- `IL-E1` 到 `IL-E5` 是外围 Batch IL-E 内部子批次，只属于 Unrouted Trace Normalizer 外围能力。
- 后续文档和代码评审不得把 `IL-E1` 写成 `IL-A` 的同级核心仿真器批次。

#### IL-E1：Normalizer Core

开发目标：

```text
新增一个 row-by-row CSV normalizer，只负责把 unrouted trace 转成 routed trace。
```

新增文件：

```text
src/hitfloor/trace/normalizer.py
tests/unit/trace/test_normalizer.py
```

核心类型：

```python
@dataclass(frozen=True, slots=True)
class TraceNormalizeResult:
    input_path: Path
    output_path: Path
    row_count: int
    added_instance_uuid_column: bool
    filled_empty_instance_uuid_count: int
    instance_uuid: str
```

核心函数：

```python
def normalize_unrouted_trace(
    input_path: str | Path,
    output_path: str | Path,
    *,
    instance_uuid: str,
) -> TraceNormalizeResult:
    ...
```

实现要求：

- 使用 `csv.DictReader` / `csv.DictWriter`，逐行读写，不把 CSV 全量加载进内存。
- 输入必须包含基础字段：`request_id`、`tenant_id`、`request_params`、`service_start_time`。
- 输入不能包含 `instance_uuid`；如果包含则 fail-fast。
- 输出新增 `instance_uuid` 列，建议插入在 `tenant_id` 后面，保证列顺序稳定。
- 额外输入列必须原样保留，且列顺序稳定。
- `instance_uuid` 必须是非空字符串，且不能包含换行。
- 输出路径已存在时 fail-fast，避免覆盖真实 trace。
- 写入建议使用临时文件，成功后原子替换到目标路径；失败时不留下半成品输出。
- 不解析 `request_params`，不解析时间戳，不调用 tokenizer，不调用 `read_trace_csv()`。

本批不做：

- 不接 CLI。
- 不接 script wrapper。
- 不接 replay / sweep。
- 不修改 `TraceRecord`。
- 不修改 `read_trace_csv()`。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/trace/test_normalizer.py
git diff --check
```

#### IL-E2：Package CLI + Script Wrapper

开发目标：

```text
把 normalizer 暴露成用户可直接调用的外围命令。
```

修改文件：

```text
src/hitfloor/cli/main.py
scripts/normalize_unrouted_trace.py
tests/integration/test_trace_normalizer_cli.py
```

CLI 入口：

```bash
PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main normalize-trace \
  --input data/raw/unrouted_trace.csv \
  --output data/processed/routed_single_instance_trace.csv \
  --instance-uuid default-instance
```

script wrapper：

```bash
.venv/bin/python scripts/normalize_unrouted_trace.py \
  --input data/raw/unrouted_trace.csv \
  --output data/processed/routed_single_instance_trace.csv \
  --instance-uuid default-instance
```

实现要求：

- `src/hitfloor/cli/main.py` 新增 argparse subcommand：`normalize-trace`。
- CLI 只调用 `normalize_unrouted_trace()`，不承载 normalizer 业务逻辑。
- 成功后打印结构化摘要：输入、输出、行数、实例 id。
- wrapper 只负责加入 `src` 到 `sys.path` 并转发到 package CLI。
- CLI fail-fast 时返回非 0，由异常暴露具体错误信息。

本批不做：

- 不新增 config schema。
- 不在 `sweep` 或 `sweep-streaming` 中自动调用 normalizer。
- 不把 normalizer 结果写入 report 目录。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/trace/test_normalizer.py \
  tests/integration/test_trace_normalizer_cli.py
git diff --check
```

#### IL-E3：Replay Boundary E2E

开发目标：

```text
证明 normalizer 只是 replay 前的数据准备，核心仿真器仍只消费 routed trace。
```

新增测试建议：

```text
tests/integration/test_unrouted_trace_normalizer_e2e.py
```

测试链路：

```text
synthetic unrouted trace
-> normalize-trace
-> validate-trace on normalized output
-> sweep-streaming with normalized output
-> capacity_sweep.csv contains trace row + instance row
```

边界测试：

- 直接对 raw unrouted trace 调 `validate-trace` 应 fail-fast。
- normalized trace 可被 `read_trace_csv()` 正常读取。
- sweep / streaming replay 不感知 normalizer 的存在。
- 输出结果中只出现统一 `instance_uuid` 对应的单实例 scope。

本批不做：

- 不改变 replay metrics。
- 不给 report 增加 normalizer 字段。
- 不新增 gateway routing 行为。

验收：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_unrouted_trace_normalizer_e2e.py
git diff --check
```

#### IL-E4：Docs / Examples / Memory

开发目标：

```text
把外围能力使用方式和边界写进长期文档，避免后续把它误认为 gateway routing。
```

修改范围：

```text
docs/archive/instance_latency_profiles/
docs/development_governance.md
docs/global_memory.md
README.md
```

建议新增执行记录：

```text
docs/archive/instance_latency_profiles/08_il_e_execution.md
```

文档必须说明：

- `normalize-trace` 是外围能力。
- 核心仿真器仍要求 routed trace。
- 该能力只适用于“用户明确不想做路由仿真，但原始 trace 没有实例 id”的场景。
- 该能力不能解释集群 routing 效果。
- 如果未来要做 gateway routing，应新增 gateway layer。

验收：

```text
rg -n "default_instance_uuid|trace.default_instance_uuid" docs src tests configs
git diff --check
```

`default_instance_uuid` 只能出现在“禁止引入该核心配置”的说明里，不能出现在 config schema 或核心代码里。

#### IL-E5：工程收口

开发目标：

```text
确认外围 normalizer 已完成，且没有破坏核心 replay 能力。
```

必须检查：

- `src/hitfloor/trace/reader.py` 未修改核心 required columns 语义。
- `src/hitfloor/trace/schema.py` 未把 `instance_uuid` 变成 optional。
- `src/hitfloor/experiment/request_builder.py` 未加入默认实例逻辑。
- `src/hitfloor/streaming/` 未自动调用 normalizer。
- `src/hitfloor/replay/` 未感知 normalizer。
- 所有 normalizer 逻辑只在 `trace/normalizer.py`、CLI、script wrapper 和测试中出现。

完整验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/trace/test_normalizer.py \
  tests/integration/test_trace_normalizer_cli.py \
  tests/integration/test_unrouted_trace_normalizer_e2e.py

PYTHONPATH=src .venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
git diff --check
```

收口文档：

```text
docs/archive/instance_latency_profiles/08_il_e_execution.md
```

必须记录：

- 完成内容。
- 没有修改的核心模块。
- 定向测试和全量测试结果。
- 合成 unrouted trace -> normalized routed trace -> streaming sweep 的验收结果。
- 遗留问题：真正 gateway routing simulation 仍未实现。

### 6.10 工程质量要求

可维护性要求：

- Normalizer 是独立外围模块，函数职责单一。
- CLI 是薄入口，不写 CSV 业务逻辑。
- Script 是 wrapper，不写 CSV 业务逻辑。
- 测试覆盖正常路径、错误路径、边界路径和 replay 边界。

性能要求：

- normalizer 必须 row-by-row streaming。
- 内存复杂度应约等于单行大小和 CSV fieldnames，不能随 request 数线性增长。
- 不解析 request JSON，避免对 11G trace 造成额外 CPU 和内存压力。
- 不 tokenize，不构造 `SimulationRequest`。

安全要求：

- 不覆盖已有输出。
- 输入已有 `instance_uuid` 时 fail-fast。
- 失败时不留下半成品输出。
- 不静默丢弃未知列。

版本管理要求：

- 代码开发完成后应能单独形成一个清晰变更集。
- 变更说明必须写明：这是外围能力，不是核心仿真器能力。
- 若需要提交，commit message 建议：

```text
Add unrouted trace normalizer outer capability
```
