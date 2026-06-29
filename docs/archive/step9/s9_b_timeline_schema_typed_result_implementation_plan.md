# S9-B Implementation Plan: Timeline Schema / Typed Result

状态：已审批通过，已执行完成。

本 Batch 已完成代码开发和 targeted 验证。

## 1. Batch 定位

本 Batch 属于核心仿真器开发。

改动等级：L3。

原因：

- S9-B 不改变 replay 行为，但会新增 replay-facing timeline schema，并扩展 request /
  iteration typed result。
- typed result 是核心仿真器边界，外围能力只能消费它，不能重算 replay 语义。
- S9-B 是后续 S9-C 到 S9-H 的 schema 地基，因此按 L3 审批和测试要求处理。

本 Batch 是 schema-only / behavior-neutral L3：只增加类型、字段和验证，不接入 event loop
状态推进。

## 2. 本 Batch 做什么

S9-B 做四件事：

1. 新增 timeline schema。
   - 定义 request timeline state。
   - 定义 chunk timeline entry。
   - 定义 KV load timeline entry。
   - 定义 request timeline summary。

2. 扩展 replay typed result。
   - 在 `BatchAwareRequestMetrics` 上追加 Step9 timeline aggregate 字段，全部提供默认值。
   - 在 `IterationMetrics` 上追加 Step9 timeline aggregate 字段，全部提供默认值。
   - 保持现有字段含义不变。

3. 提供 legacy compatibility defaults。
   - old mode 默认 `timeline_mode="legacy_iteration_v1"`。
   - old mode 默认 `ttft_granularity="iteration"`。
   - old mode 不把现有 `scheduler_wait_ms` 重新解释为 `compute_wait_ms + kv_load_wait_ms`。

4. 新增 schema 单测。
   - 验证新 dataclass 的非负约束。
   - 验证 request / iteration metrics 的默认字段不会破坏旧构造方式。
   - 验证 streaming aggregator 忽略新增字段后仍保持 token invariant。

## 3. 本 Batch 不做什么

S9-B 不做：

- 不新增 replay mode。
- 不修改 scheduler token selection。
- 不修改 `RequestStatus`。
- 不修改 event loop 状态推进。
- 不统计真实 `compute_wait_ms`。
- 不统计真实 `kv_load_wait_ms`。
- 不引入 KV transfer queue。
- 不改变 latency backend。
- 不改变 cache lookup / materialization / eviction。
- 不改变 cache event 顺序。
- 不接入 streaming runner 的新状态逻辑。
- 不修改 report/export 输出。
- 不默认保存 per-chunk 明细。

如果实现时发现必须修改 `replay/event_loop.py` 的行为、`scheduler/vllm_like.py` 的调度逻辑、
`cache/materialization.py` 的策略或 report/export 字段，应暂停并重新评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/replay/timeline.py` | 定义 Step9 timeline schema、state enum、summary validation 和 legacy defaults。 |
| `tests/unit/replay/test_timeline_schema.py` | 测试 timeline schema 的构造、非负约束、时间区间约束和 summary 不变量。 |
| `docs/step9/s9_b_timeline_schema_typed_result_implementation_plan.md` | 本文件。记录 S9-B 方案、边界、测试策略和进入 S9-C 的条件。 |

### 4.2 修改文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/replay/metrics.py` | 给 `BatchAwareRequestMetrics` / `IterationMetrics` 追加 timeline aggregate 字段；在 builder 中填入 legacy defaults。 |
| `src/infertwin/replay/__init__.py` | 如当前导出核心 replay 类型，则补充导出 timeline schema；如果该文件不导出类似类型，可不改。 |
| `tests/unit/replay/test_step8_latency_contribution_metrics.py` | 可增加一条兼容测试，确认 legacy latency contribution 仍不变且新字段为默认值。 |
| `tests/unit/streaming/test_metrics.py` | 可增加一条兼容测试，确认 streaming aggregator 不因新增 metrics 字段改变 capacity rows。 |

### 4.3 禁止修改文件

S9-B 禁止修改：

- `src/infertwin/replay/event_loop.py`
- `src/infertwin/streaming/replay.py`
- `src/infertwin/scheduler/vllm_like.py`
- `src/infertwin/scheduler/state.py`
- `src/infertwin/cache/**`
- `src/infertwin/latency/**`
- `src/infertwin/report/**`
- `configs/**`
- `scripts/**`

例外：如果用户审批后发现 `src/infertwin/replay/__init__.py` 不需要导出 timeline 类型，则不改。

## 5. 每个文件的职责

### 5.1 `src/infertwin/replay/timeline.py`

只负责定义 timeline 相关纯数据结构和轻量验证。

不负责：

- scheduler 选 batch。
- request 状态推进。
- latency 估算。
- materialization。
- report/export。

建议定义：

```python
LEGACY_TIMELINE_MODE = "legacy_iteration_v1"
PROGRESSIVE_TIMELINE_MODE = "batch_aware_hbm_ddr_lru_progressive_timeline"

ITERATION_TTFT_GRANULARITY = "iteration"
CHUNK_TTFT_GRANULARITY = "chunk"
```

建议数据结构：

```python
class RequestTimelineState(str, Enum):
    PENDING = "pending"
    WAITING_FOR_COMPUTE = "waiting_for_compute"
    WAITING_FOR_KV_LOAD = "waiting_for_kv_load"
    RUNNING_CHUNK = "running_chunk"
    FINISHED = "finished"
```

```python
@dataclass(frozen=True, slots=True)
class ChunkTimelineEntry:
    request_id: str
    instance_uuid: str
    iteration_id: int
    start_time_ms: float
    finish_time_ms: float
    scheduled_prefill_tokens: int
    computed_tokens_before: int
    computed_tokens_after: int
    prefill_compute_ms: float = 0.0
```

```python
@dataclass(frozen=True, slots=True)
class KVLoadTimelineEntry:
    request_id: str
    instance_uuid: str
    ready_time_ms: float
    start_time_ms: float
    finish_time_ms: float
    kv_load_tokens: int = 0
    kv_load_bytes: int = 0
    kv_load_ms: float = 0.0
    kv_load_wait_ms: float = 0.0
    source_tier: str = "ddr"
```

```python
@dataclass(frozen=True, slots=True)
class RequestTimelineSummary:
    timeline_mode: str = LEGACY_TIMELINE_MODE
    ttft_granularity: str = ITERATION_TTFT_GRANULARITY
    compute_wait_ms: float = 0.0
    kv_load_wait_ms: float = 0.0
    uncached_prefill_compute_ms: float = 0.0
    modeled_serialization_ms: float = 0.0
    chunk_count: int = 0
    load_event_count: int = 0
    progressive_materialized_blocks: int = 0
    progressive_materialized_tokens: int = 0

    @property
    def scheduler_wait_ms(self) -> float:
        return self.compute_wait_ms + self.kv_load_wait_ms
```

Validation:

- all time values must be non-negative。
- finish time must be `>= start_time_ms`。
- computed token counts must be non-negative。
- `computed_tokens_after >= computed_tokens_before`。
- token / byte / count fields must be non-negative。
- `timeline_mode` and `ttft_granularity` must be non-empty。

### 5.2 `src/infertwin/replay/metrics.py`

只追加轻量 aggregate fields，不存储 timeline entry tuples。

建议追加到 `BatchAwareRequestMetrics`：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
ttft_granularity: str = ITERATION_TTFT_GRANULARITY
compute_wait_ms: float = 0.0
kv_load_wait_ms: float = 0.0
uncached_prefill_compute_ms: float = 0.0
modeled_serialization_ms: float = 0.0
chunk_count: int = 0
load_event_count: int = 0
progressive_materialized_blocks: int = 0
progressive_materialized_tokens: int = 0
```

建议追加到 `IterationMetrics`：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
ttft_granularity: str = ITERATION_TTFT_GRANULARITY
waiting_for_compute_count: int = 0
waiting_for_kv_load_count: int = 0
scheduled_chunk_count: int = 0
kv_transfer_queue_depth_max: int = 0
compute_wait_ms: float = 0.0
kv_load_wait_ms: float = 0.0
modeled_serialization_ms: float = 0.0
progressive_materialized_blocks: int = 0
progressive_materialized_tokens: int = 0
```

Builder 兼容策略：

- `build_request_metrics()` 继续按旧逻辑计算 `scheduler_wait_ms` 和 `ttft_ms`。
- 新字段默认 legacy mode。
- `uncached_prefill_compute_ms` 可填入 `state.prefill_compute_ms`，作为旧 `prefill_compute_ms`
  的同义新字段。
- `compute_wait_ms` / `kv_load_wait_ms` 保持 0，直到 S9-C/S9-D 实现真实 accounting。
- `chunk_count` 可保持 0，直到 S9-F 实现 chunk composer；也可以填 `state.scheduled_iteration_count`
  作为 legacy coarse count。为避免语义混淆，S9-B 建议保持 0。

### 5.3 `tests/unit/replay/test_timeline_schema.py`

覆盖：

- `RequestTimelineState` 值稳定。
- `ChunkTimelineEntry` 接受合法区间，拒绝负 token / 负时间 / finish < start。
- `KVLoadTimelineEntry` 接受 token-linear / byte-linear 场景，拒绝负值。
- `RequestTimelineSummary.scheduler_wait_ms` 等于 `compute_wait_ms + kv_load_wait_ms`。
- `RequestTimelineSummary` legacy default 为 `legacy_iteration_v1` / `iteration`。

### 5.4 现有测试文件的小修改

只做兼容断言，不改变旧测试期望：

- request metric 默认 `timeline_mode == "legacy_iteration_v1"`。
- request metric 默认 `compute_wait_ms == 0.0`。
- request metric 默认 `kv_load_wait_ms == 0.0`。
- streaming aggregator capacity rows 与旧结果一致。

## 6. 新增或修改的数据结构 / schema / interface

### 6.1 新增数据结构

新增：

- `RequestTimelineState`
- `ChunkTimelineEntry`
- `KVLoadTimelineEntry`
- `RequestTimelineSummary`

### 6.2 修改 typed result schema

修改：

- `BatchAwareRequestMetrics`
- `IterationMetrics`

追加字段必须全部有默认值，保证现有测试 helper 和外部调用不需要立即更新。

### 6.3 不新增 interface

S9-B 不新增：

- `RequestTTFTComposer`
- `KVLoadTimingPolicy`
- `KVTransferTimelinePolicy`
- `ProgressiveFullBlockMaterializationPolicy`

这些 interface 留到 S9-D/S9-E/S9-F/S9-G 分批设计。

## 7. 核心算法逻辑

S9-B 没有 replay 算法。

核心逻辑只有 schema validation：

1. 时间区间校验：

```text
finish_time_ms >= start_time_ms
ready_time_ms >= 0
```

2. 非负校验：

```text
tokens >= 0
bytes >= 0
counts >= 0
durations >= 0
```

3. computed token 校验：

```text
computed_tokens_after >= computed_tokens_before
```

4. legacy 默认：

```text
timeline_mode = legacy_iteration_v1
ttft_granularity = iteration
compute_wait_ms = 0
kv_load_wait_ms = 0
```

5. request summary 派生：

```text
scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
```

注意：S9-B 不用 `RequestTimelineSummary` 覆盖现有 `BatchAwareRequestMetrics.scheduler_wait_ms`。
旧 mode 下，该字段仍维持当前 `first_scheduled_time_ms - arrival_time_ms` 语义。新 mode 的
兼容聚合字段将在 S9-C/S9-D/S9-F 逐步接入。

## 8. 对核心 replay 语义的影响

S9-B 是 schema-only，不改变 replay 行为。

| 问题 | S9-B 是否改变 | 说明 |
| --- | --- | --- |
| 是否改变 `cached_tokens` | 否 | 不改 lookup/accounting。 |
| 是否改变 `hbm_hit_tokens / ddr_hit_tokens / miss_tokens` | 否 | 不改 cache result 或 metric invariant。 |
| 是否改变 `finish_time / ttft_ms` | 否 | 不改 event loop 和 latency application。 |
| 是否改变 cache event 顺序 | 否 | 不改 cache/event sink/materialization。 |
| 是否改变 materialization timing | 否 | 不改 finish-time policy。 |
| 是否改变实例隔离 | 否 | 不改 per-instance replay。 |
| 是否影响 true streaming 大 trace | 轻微 | 仅 request/iteration metric 对象增加少量标量字段；不默认保存 per-chunk 明细。 |

大 trace 风险控制：

- 不把 `ChunkTimelineEntry` tuple 放进 `BatchAwareRequestMetrics` 默认字段。
- 不把 `KVLoadTimelineEntry` tuple 放进 streaming aggregator。
- per-chunk 明细未来必须通过显式 sink / debug output / selected capacity dump 控制。

## 9. 测试计划

### 9.1 单测

新增：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_timeline_schema.py
```

建议同时运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/replay/test_timeline_schema.py \
  tests/unit/replay/test_step8_latency_contribution_metrics.py \
  tests/unit/streaming/test_metrics.py
```

覆盖点：

- timeline schema validation。
- request metrics 新字段默认值。
- iteration metrics 新字段默认值。
- streaming aggregator 不受新增字段影响。

### 9.2 集成测试

S9-B 不要求新增集成测试。

原因：

- 不改变 replay event loop。
- 不改变 streaming runner。
- 不改变 report/export。

可选 targeted check：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py
```

该测试用于确认新增 typed fields 不破坏 Step8 streaming KV load E2E。

### 9.3 小 E2E

S9-B 不新增小 E2E。

小 E2E 从 S9-C 或 S9-D 开始更有意义，因为那时会首次出现真实 wait accounting。

### 9.4 Golden 更新

S9-B 不更新 golden。

原因：

- 不修改 report/export CSV。
- 不修改 existing capacity sweep row schema。
- 不修改 cache event schema。

如果实现时发现某个 golden 直接比较 `BatchAwareRequestMetrics` 全字段 repr，应暂停说明；优先更新测试 helper，而不是扩大 report schema。

## 10. 风险与回滚边界

### 10.1 风险

1. schema 字段过早污染 report/export。
   - S9-B 不改 report/export。
   - S9-H 再决定哪些字段进入 CSV / summary。

2. per-chunk 明细导致大 trace 内存膨胀。
   - S9-B 只定义 entry schema，不默认挂到 request metrics。

3. legacy `scheduler_wait_ms` 语义混淆。
   - S9-B 不改旧字段计算。
   - 新 mode 的兼容聚合字段在 S9-C/S9-D/F 接入。

4. timeline schema 和后续状态实现不匹配。
   - S9-B schema 必须保持最小但可扩展；不要把 transfer queue、materialization policy
     细节硬编码进去。

5. 修改范围滑向 replay behavior。
   - 如果需要改 `event_loop.py`、`vllm_like.py`、`state.py` 行为，暂停并重新评审。

### 10.2 回滚边界

S9-B 回滚简单：

- 删除 `src/infertwin/replay/timeline.py`。
- 回退 `src/infertwin/replay/metrics.py` 中追加字段和 import。
- 回退 `src/infertwin/replay/__init__.py` 导出。
- 删除 `tests/unit/replay/test_timeline_schema.py`。
- 回退现有测试中的兼容断言。

由于不修改 replay 状态机和数据文件，回滚不涉及 cache、shard、event 或 report 迁移。

## 11. 完成后如何判断可以进入下一个 Batch

S9-B 完成后，可以进入 S9-C 的条件：

1. timeline schema 已合入，且只包含纯数据结构和 validation。
2. request / iteration typed metrics 已追加默认字段，旧 helper 构造仍可用。
3. `build_request_metrics()` 和 `build_iteration_metrics()` 不改变旧字段值。
4. streaming aggregator 不消费新字段也不被破坏。
5. 不默认持有 per-chunk / per-load entry tuple，避免大 trace 内存风险。
6. targeted tests 通过：
   - `tests/unit/replay/test_timeline_schema.py`
   - `tests/unit/replay/test_step8_latency_contribution_metrics.py`
   - `tests/unit/streaming/test_metrics.py`
7. `git diff --check` 通过。
8. 没有修改方案外文件。

## 12. 需要用户审批的内容

审批结果：已通过。

已接受以下内容：

1. 是否接受 S9-B 属于核心仿真器，改动等级 L3，但本批次为 schema-only / behavior-neutral。
2. 是否接受新增 `src/infertwin/replay/timeline.py`。
3. 是否接受 S9-B 只追加 `BatchAwareRequestMetrics` / `IterationMetrics` 的轻量 aggregate 字段，不默认保存 per-chunk 明细。
4. 是否接受 old mode 默认：
   - `timeline_mode="legacy_iteration_v1"`
   - `ttft_granularity="iteration"`
   - `compute_wait_ms=0.0`
   - `kv_load_wait_ms=0.0`
5. 是否接受 S9-B 不修改 `event_loop.py`、`scheduler/state.py`、`scheduler/vllm_like.py` 和 `cache/materialization.py`。
6. 是否接受 S9-B 不更新 report/export 和 golden。
7. 是否接受上述 targeted tests 作为 S9-B 验收范围。

## 13. S9-B 执行记录

执行日期：2026-06-29。

实际修改：

- `src/infertwin/replay/timeline.py`
  - 新增 Step9 timeline schema。
  - 新增 `RequestTimelineState`。
  - 新增 `ChunkTimelineEntry`。
  - 新增 `KVLoadTimelineEntry`。
  - 新增 `RequestTimelineSummary`。
  - 新增 legacy / progressive timeline mode constants。
- `src/infertwin/replay/metrics.py`
  - 给 `BatchAwareRequestMetrics` 追加 legacy timeline aggregate 字段。
  - 给 `IterationMetrics` 追加 legacy timeline aggregate 字段。
  - `build_request_metrics()` 填充 `uncached_prefill_compute_ms=state.prefill_compute_ms`，保持旧字段不变。
- `tests/unit/replay/test_timeline_schema.py`
  - 新增 timeline schema 单测。
- `tests/unit/replay/test_step8_latency_contribution_metrics.py`
  - 增加 legacy timeline defaults 兼容断言。
- `tests/unit/streaming/test_metrics.py`
  - 增加 streaming metrics 与 legacy timeline defaults 兼容断言。
- `docs/step9/README.md`
  - 增加 S9-B 文档索引和当前执行状态。
- `docs/step9/s9_b_timeline_schema_typed_result_implementation_plan.md`
  - 更新审批和执行记录。

未修改：

- 未修改 `src/infertwin/replay/event_loop.py`。
- 未修改 `src/infertwin/streaming/replay.py`。
- 未修改 `src/infertwin/scheduler/vllm_like.py`。
- 未修改 `src/infertwin/scheduler/state.py`。
- 未修改 `src/infertwin/cache/**`。
- 未修改 `src/infertwin/latency/**`。
- 未修改 `src/infertwin/report/**`。
- 未修改 report/export 和 golden。

验证：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/replay/test_timeline_schema.py \
  tests/unit/replay/test_step8_latency_contribution_metrics.py \
  tests/unit/streaming/test_metrics.py
```

结果：21 passed。

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py
```

结果：1 passed。

```bash
.venv/bin/ruff check \
  src/infertwin/replay/timeline.py \
  src/infertwin/replay/metrics.py \
  tests/unit/replay/test_timeline_schema.py \
  tests/unit/replay/test_step8_latency_contribution_metrics.py \
  tests/unit/streaming/test_metrics.py
```

结果：All checks passed。

```bash
git diff --check
rg -n "[[:blank:]]$" docs/step9 src/infertwin/replay/timeline.py tests/unit/replay/test_timeline_schema.py
```

结果：通过；尾随空白检查无输出。

核心 replay 影响：

- 未改变 `cached_tokens`。
- 未改变 `hbm_hit_tokens / ddr_hit_tokens / miss_tokens`。
- 未改变 `finish_time / ttft_ms`。
- 未改变 cache event 顺序。
- 未改变 materialization timing。
- 未改变实例隔离。
- 未改变 true streaming replay 行为。

进入下一 Batch 条件：

- S9-B 已完成。
- 可以进入 S9-C：Compute Wait Accounting 代码编写方案设计。
