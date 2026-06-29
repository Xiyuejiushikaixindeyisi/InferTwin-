# S9-C Implementation Plan: Compute Wait Accounting

状态：已审批通过，已执行完成。

本文件同时作为 S9-C 的代码编写方案和执行记录。

## 1. Batch 定位

本 Batch 属于核心仿真器开发。

改动等级：L3。

原因：

- S9-C 会修改 replay event loop 对 request 等待时间的记录方式。
- 新增的 `compute_wait_ms` 会进入 request / iteration typed result。
- 本 Batch 不改变 cache hit 结果和 cache materialization，但会影响新 timeline mode 下的 `scheduler_wait_ms` 拆分口径。
- 旧 mode 必须保持完全兼容，不能改变现有 `batch_aware_hbm_lru` / `batch_aware_hbm_ddr_lru` 的结果。

## 2. 本 Batch 做什么

S9-C 只实现 `compute_wait_ms` accounting。

具体做：

1. 在 replay engine 中新增 timeline mode 开关。
   - 默认 `legacy_iteration_v1`。
   - 显式传入 `batch_aware_hbm_ddr_lru_progressive_timeline` 时，启用 compute wait 统计。
   - 本 Batch 不接 CLI / runner / config，先让核心 engine 和测试可用。

2. 在 request state 中记录 compute wait。
   - request 到达 engine 后，如果没有被当前 scheduler iteration 选中，则该 iteration duration 计入 `compute_wait_ms`。
   - request 在前一个 iteration 执行期间到达，直到下一个 scheduling boundary 才被 engine 消费，这段时间也计入 `compute_wait_ms`。
   - request 已完成后不再计时。

3. 在 request metrics 中输出 compute wait。
   - 新 timeline mode 下：

     ```text
     scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
     ```

   - S9-C 暂不实现真实 `kv_load_wait_ms`，因此 `kv_load_wait_ms=0`。
   - `ttft_ms` 仍保持 `finish_time_ms - arrival_time_ms`。

4. 在 iteration metrics 中输出本轮 compute wait 聚合。
   - 统计本轮已处于 active state、但没有被 schedule 的 request 数量。
   - 统计这些 request 在本轮 iteration duration 上累计的 compute wait。

5. 增加 list replay 与 streaming replay 的一致性测试。
   - progressive timeline mode 下 request-level compute wait 结果一致。
   - legacy mode 下旧结果不变。

## 3. 本 Batch 不做什么

S9-C 不做：

- 不实现 KV load wait。
- 不实现 KV transfer queue。
- 不实现 progressive materialization。
- 不改变 HBM / DDR lookup。
- 不改变 cached token accounting。
- 不改变 eviction policy。
- 不改变 materialization timing。
- 不修改 scheduler token selection 策略。
- 不接入 report/export。
- 不接入 CLI / runner / config。
- 不默认输出 per-chunk 明细。
- 不把 `ttft_granularity` 切到完整 `chunk` 口径。

说明：

- S9-C 只是 Step9 timeline 的第一个行为接入点。
- 完整 chunk-level TTFT composition 由 S9-F 完成。
- S9-C 可以在新 timeline mode 下输出真实 `compute_wait_ms`，但暂不宣称完整 chunk timeline 已完成。

如果实现时发现必须修改 `src/infertwin/cache/**`、`src/infertwin/latency/**`、
`src/infertwin/report/**`、`src/infertwin/cli/**` 或配置解析链路，应暂停并重新评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `tests/unit/replay/test_compute_wait_accounting.py` | 覆盖 progressive timeline mode 下 compute wait 统计、legacy mode 兼容和边界条件。 |

### 4.2 修改文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/scheduler/state.py` | 在 `RequestState` 中保存 timeline mode、timeline-facing state 和 compute wait 累计值；提供非负累加方法。 |
| `src/infertwin/replay/event_loop.py` | 在 batch-aware replay 中启用可选 compute wait accounting；保持 legacy mode no-op。 |
| `src/infertwin/streaming/replay.py` | 让 streaming replay 使用与 list replay 相同的 state 创建和 compute wait accounting helper。 |
| `src/infertwin/replay/metrics.py` | 在 builder 中读取 `RequestState` 的 compute wait 字段，并支持 iteration metrics 的 compute wait 参数。 |
| `tests/unit/streaming/test_streaming_replay.py` | 增加 progressive timeline mode 的 list / streaming parity 测试。 |
| `docs/step9/s9_c_compute_wait_accounting_implementation_plan.md` | 本文件；开发后补充执行记录、测试结果和进入 S9-D 的判断。 |

### 4.3 禁止修改文件

S9-C 禁止修改：

- `src/infertwin/cache/**`
- `src/infertwin/latency/**`
- `src/infertwin/report/**`
- `src/infertwin/cli/**`
- `src/infertwin/config/**`
- `scripts/**`
- `configs/**`

## 5. 每个文件的职责

### 5.1 `src/infertwin/scheduler/state.py`

新增 request-local timeline accounting 字段，但不替换 scheduler-visible `RequestStatus`。

建议新增字段：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
timeline_state: RequestTimelineState = RequestTimelineState.WAITING_FOR_COMPUTE
compute_wait_ms: float = 0.0
kv_load_wait_ms: float = 0.0
modeled_serialization_ms: float = 0.0
```

建议新增方法：

```python
def record_compute_wait(self, duration_ms: float) -> None:
    ...
```

职责边界：

- `RequestStatus` 继续只表达 scheduler 队列生命周期：`WAITING` / `RUNNING` / `FINISHED`。
- `timeline_state` 只用于 Step9 timeline accounting，不参与 S9-C 的 scheduler selection。
- `kv_load_wait_ms` 先作为 schema placeholder，S9-D 再接入真实 KV load wait。
- 所有新增时间字段必须非负。

### 5.2 `src/infertwin/replay/event_loop.py`

新增 `timeline_mode` 构造参数：

```python
BatchAwareReplayEngine(
    ...,
    timeline_mode: str = LEGACY_TIMELINE_MODE,
)
```

校验策略：

- 只接受：
  - `LEGACY_TIMELINE_MODE`
  - `PROGRESSIVE_TIMELINE_MODE`
- 其他值 fail-fast。

核心 helper 建议：

```python
def _is_progressive_timeline_enabled(self) -> bool:
    ...
```

```python
def _record_initial_compute_wait(
    *,
    state: RequestState,
    now_ms: float,
) -> None:
    ...
```

```python
def _record_iteration_compute_wait(
    *,
    waiting: WaitingQueue,
    running: list[RequestState],
    scheduled_request_ids: set[str],
    duration_ms: float,
) -> ComputeWaitAccounting:
    ...
```

`ComputeWaitAccounting` 可以是 replay 内部小 dataclass，或先用局部 tuple：

```python
@dataclass(frozen=True, slots=True)
class ComputeWaitAccounting:
    waiting_for_compute_count: int
    compute_wait_ms: float
```

职责边界：

- 只在 progressive timeline mode 下记录 compute wait。
- legacy mode 下 helper 返回 0，不修改 state。
- 不把 pending list 全量预取到内存。
- 不改变 scheduler schedule 输入和输出。
- 不改变 `_prepare_scheduler_frontier()` 的 lookup / zero-miss 行为。

### 5.3 `src/infertwin/streaming/replay.py`

修改 `_move_arrivals_from_source()`，让其支持：

- 传入 `timeline_mode`。
- 创建 state 时携带 timeline mode。
- request 在前一个 iteration 期间到达、但到当前 scheduling boundary 才被消费时，记录初始 compute wait。

要求：

- 不破坏 streaming source 的单向读取语义。
- 不为了统计 mid-iteration arrival wait 而预读整个 shard。
- streaming replay 与 list replay 对 request-level `compute_wait_ms` 保持一致。

### 5.4 `src/infertwin/replay/metrics.py`

修改 `build_request_metrics()`：

- legacy mode 保持：

  ```text
  scheduler_wait_ms = first_scheduled_time_ms - arrival_time_ms
  compute_wait_ms = 0
  kv_load_wait_ms = 0
  timeline_mode = legacy_iteration_v1
  ```

- progressive timeline mode 使用：

  ```text
  scheduler_wait_ms = state.compute_wait_ms + state.kv_load_wait_ms
  compute_wait_ms = state.compute_wait_ms
  kv_load_wait_ms = state.kv_load_wait_ms
  timeline_mode = state.timeline_mode
  ```

- `ttft_ms` 继续保持：

  ```text
  finish_time_ms - arrival_time_ms
  ```

修改 `build_iteration_metrics()`：

- 增加默认参数：

  ```python
  timeline_mode: str = LEGACY_TIMELINE_MODE
  waiting_for_compute_count: int = 0
  compute_wait_ms: float = 0.0
  ```

- 默认值保持旧测试兼容。
- S9-C 不填 `kv_load_wait_ms`，保持 0。

### 5.5 测试文件

新增 `tests/unit/replay/test_compute_wait_accounting.py`：

- 只测试核心 replay 行为。
- 不依赖 CLI / config / report。
- 使用现有 `FormulaLatencyBackend` 和合成 `SimulationRequest`。

修改 `tests/unit/streaming/test_streaming_replay.py`：

- 增加一个 progressive timeline mode parity 测试。
- 确认 streaming request metrics 与 list replay request metrics 一致。

## 6. 新增或修改的数据结构 / schema / interface

### 6.1 Replay engine interface

新增可选参数：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
```

默认值保证现有调用点不需要修改。

### 6.2 RequestState

新增 timeline accounting 字段：

```python
timeline_mode: str
timeline_state: RequestTimelineState
compute_wait_ms: float
kv_load_wait_ms: float
modeled_serialization_ms: float
```

S9-C 只真实更新：

```python
compute_wait_ms
```

### 6.3 Metrics builder interface

`build_iteration_metrics()` 增加可选 keyword-only 参数，全部有默认值。

`build_request_metrics()` 不改函数签名，只从 `RequestState` 读取新字段。

## 7. 核心算法逻辑

### 7.1 初始 compute wait

当 pending request 被移动到 waiting queue 时：

```text
if progressive mode and now_ms > request.arrival_time_ms:
    state.compute_wait_ms += now_ms - request.arrival_time_ms
```

含义：

- replay clock 只在 scheduling boundary 推进。
- 如果 request 在上一轮 iteration 执行期间到达，它虽然尚未进入 `WaitingQueue`，但从 trace arrival 到当前 boundary 的时间属于 engine 内部等待。
- 这段时间计入 request-level `compute_wait_ms`。

空闲跳时不计 compute wait：

```text
if no waiting and no running:
    now_ms = next_request.arrival_time_ms
```

因为此时 engine 没有正在执行的 iteration，请求一到达就可被处理。

### 7.2 iteration compute wait

一次 scheduler iteration 的流程：

```text
active states before scheduling
  -> scheduler selects request slices
  -> latency backend returns iteration duration
  -> scheduled requests consume chunk/load-only slice
  -> unscheduled active requests accumulate compute_wait_ms += duration
```

active unscheduled request 定义：

```text
request.status != FINISHED
and request.arrival_time_ms <= iteration_start_ms
and request_id not in scheduled_request_ids
and request is in waiting queue or running list
```

这些 request 已经被 engine 接收，但本轮没有进入 compute batch，因此等待本轮 iteration duration。

### 7.3 避免双重计费

同一时间段只能进入一个字段：

- request 尚未到达：不计入。
- request 到达但本轮未 schedule：计入 `compute_wait_ms`。
- request 本轮被 schedule：不计入 `compute_wait_ms`，其时间由 prefill compute / KV load component 承担。
- request 等待 KV load：S9-C 暂不拆分，S9-D 再从 compute wait 中分离到 `kv_load_wait_ms`。
- request 完成：不再计入任何 wait。

### 7.4 request-level 与 iteration-level 的关系

S9-C 以 request-level `compute_wait_ms` 为 TTFT source of truth。

iteration-level `compute_wait_ms` 是辅助聚合字段：

- 统计本轮已在 active state 中、但没有被选中的 request wait。
- 对于 iteration 运行期间新到达的 request，request-level 初始 wait 会在下一次 `_move_arrivals()` 时补齐。
- streaming path 不为了 iteration-level 归因而预读未来 request。

这样可以保持 true streaming 安全，同时保证 request TTFT 分解不丢失。

## 8. 对核心 replay 语义的影响

### 8.1 是否改变 `cached_tokens`

不改变。

S9-C 不修改 lookup、block conversion、CP / DCP / PCP / MTP accounting。

### 8.2 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`

不改变。

HBM / DDR hit 统计仍由现有 cache lookup 与 `LookupMetrics.from_result()` 决定。

### 8.3 是否改变 `finish_time` / `ttft_ms`

不改变 `finish_time_ms`。

不改变 `ttft_ms = finish_time_ms - arrival_time_ms`。

会改变新 progressive timeline mode 下的拆分字段：

```text
compute_wait_ms
scheduler_wait_ms
```

legacy mode 下 `scheduler_wait_ms` 保持旧口径。

### 8.4 是否改变 cache event 顺序

不改变。

S9-C 不修改 cache lookup、materialization、eviction 和 event sink。

### 8.5 是否改变 materialization timing

不改变。

仍然使用现有 finish-time materialization。

### 8.6 是否改变实例隔离

不改变。

compute wait 在每个 instance replay 内独立统计。不同实例的 waiting/running state 不共享。

### 8.7 是否影响 true streaming 大 trace

有轻微状态字段增加，但不改变 streaming 主路径：

- 不全量读取 request。
- 不保存 per-chunk 明细。
- 每个 active request 增加少量 float / string / enum 字段。

大 trace 内存风险不应显著增加。

## 9. 测试计划

### 9.1 单测

新增 `tests/unit/replay/test_compute_wait_accounting.py`，建议覆盖：

1. legacy mode no-op。
   - 不传 `timeline_mode`。
   - `compute_wait_ms == 0`。
   - `scheduler_wait_ms == first_scheduled_time_ms - arrival_time_ms`。
   - 现有 request finish time 不变。

2. 同时到达但 token budget 只能调度一个 request。
   - r1、r2 同时到达。
   - 第一轮只 schedule r1。
   - r2 `compute_wait_ms` 等于第一轮 duration。
   - r2 `scheduler_wait_ms == compute_wait_ms`。

3. request 在 iteration 执行期间到达。
   - r1 在 0ms 到达，第一轮 duration 为 4ms。
   - r2 在 1ms 到达。
   - r2 被移动到 waiting 时记录初始 compute wait 3ms。

4. running request 的 chunk 间等待。
   - 构造多个 running request 与较小 token budget。
   - 某个 running request 在一轮未被选中。
   - 该 request 累加本轮 duration 到 `compute_wait_ms`。

5. zero-miss fast finish。
   - HBM-only zero-miss 不产生 iteration。
   - 如果到达时 engine 正在忙，仍可记录 initial compute wait。

6. 负时间防护。
   - `record_compute_wait(-1)` fail-fast。

### 9.2 集成测试

S9-C 不新增大型集成测试。

建议运行现有相关集成：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step4_batch_aware_replay.py tests/integration/test_step8_streaming_kv_load_e2e.py
```

目的：

- 证明 legacy replay 和 Step8 KV load E2E 没被 S9-C 破坏。

### 9.3 小 E2E

通过 unit-level list / streaming parity 构造一个小 E2E：

```text
same synthetic requests
same scheduler config
same latency backend
list replay result == streaming replay result
```

 progressive timeline mode 下至少比较：

- request_id。
- finish_time_ms。
- ttft_ms。
- compute_wait_ms。
- scheduler_wait_ms。
- hit / miss tokens。

### 9.4 是否需要 golden 更新

不需要更新 CSV golden。

理由：

- S9-C 不接 report/export。
- 旧 mode typed metrics 默认值不变。
- 新 progressive timeline mode 尚未成为 runner/report 默认入口。

### 9.5 建议运行命令

开发完成后建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_compute_wait_accounting.py tests/unit/streaming/test_streaming_replay.py tests/unit/replay/test_timeline_schema.py tests/unit/replay/test_step8_latency_contribution_metrics.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step4_batch_aware_replay.py tests/integration/test_step8_streaming_kv_load_e2e.py
```

```bash
.venv/bin/ruff check src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/replay tests/unit/streaming
```

```bash
git diff --check
```

## 10. 风险与回滚边界

### 10.1 主要风险

1. wait 口径混淆。
   - `compute_wait_ms` 是 engine 内部 chunked prefill batching wait。
   - 它不是 gateway queue，也不是实例入口 admission queue。

2. 双重计费。
   - scheduled request 不能同时计入 compute wait。
   - finished request 不能继续计 wait。

3. streaming parity。
   - streaming path 不能为了更细 iteration attribution 而预读大量 request。

4. old mode regression。
   - 默认构造必须仍然是 legacy mode。
   - legacy request metrics 的 `scheduler_wait_ms` 旧口径不能变。

### 10.2 回滚边界

如果 S9-C 出现问题，可以回滚以下改动而不影响 S9-B schema：

- `BatchAwareReplayEngine.timeline_mode` 参数。
- `RequestState.compute_wait_ms` 的实际累加。
- `build_request_metrics()` 对 progressive mode 的 scheduler wait 拆分。
- 新增 compute wait 测试。

S9-B 已新增的 typed fields 不需要回滚。

## 11. 完成后如何判断可以进入 S9-D

满足以下条件后，可以进入 S9-D：

1. legacy mode 回归通过。
2. progressive timeline mode 下 request-level `compute_wait_ms` 可解释且 deterministic。
3. list replay 与 streaming replay 在小 E2E 上一致。
4. `cached_tokens`、HBM/DDR hit、miss tokens、cache events、finish-time materialization 均未变化。
5. 新增和相关测试通过。
6. `ruff check` 和 `git diff --check` 通过。
7. 本文档补充执行记录：
   - 做了什么。
   - 没有做什么。
   - 测试结果。
   - 风险和进入 S9-D 的判断。

## 12. 需要用户审批的内容

以下设计点已审批通过：

1. 接受 S9-C 属于核心仿真器，改动等级 L3。
2. 接受 S9-C 只实现 `compute_wait_ms`，不实现 KV load wait / transfer queue / progressive materialization。
3. 接受新增 `BatchAwareReplayEngine(..., timeline_mode=...)`，默认 legacy，不接 CLI / runner / config。
4. 接受 `RequestStatus` 不替换为 Step9 timeline state；S9-C 只新增 timeline-facing 字段，避免破坏 scheduler。
5. 接受 progressive timeline mode 下 `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms`，其中 S9-C 的 `kv_load_wait_ms=0`。
6. 接受 S9-C 暂不把完整 `ttft_granularity` 切成 chunk composer 口径，S9-F 再完成 chunk-level TTFT composition。
7. 接受 request-level `compute_wait_ms` 作为 TTFT source of truth；iteration-level compute wait 只做 active-state 聚合，不为了 streaming 归因预读未来 request。
8. 接受本 Batch 只修改计划列出的文件；如需越界修改，暂停并重新评审。
9. 接受测试范围：新增 compute wait 单测、streaming parity、legacy 回归、Step8 KV load E2E、ruff、`git diff --check`。

## 13. 执行记录

执行状态：已完成。

### 13.1 实际完成内容

S9-C 完成了以下代码开发：

1. `RequestState` 增加 timeline accounting 字段。
   - `timeline_mode`
   - `timeline_state`
   - `compute_wait_ms`
   - `kv_load_wait_ms`
   - `modeled_serialization_ms`
   - `record_compute_wait()`

2. `BatchAwareReplayEngine` 增加 `timeline_mode` 参数。
   - 默认 `legacy_iteration_v1`。
   - 显式使用 `batch_aware_hbm_ddr_lru_progressive_timeline` 时启用 compute wait accounting。
   - 未知 mode fail-fast。

3. list replay 接入 compute wait accounting。
   - request 在上一轮 iteration 执行期间到达时，进入 waiting queue 时补记 initial compute wait。
   - active 但本轮没有被 scheduler 选中的 request，按本轮 iteration duration 累加 compute wait。

4. streaming replay 接入同一套 compute wait accounting helper。
   - 不预读未来 request。
   - request-level 结果与 list replay 保持一致。

5. typed metrics 接入 compute wait 字段。
   - legacy mode 下 `compute_wait_ms=0`，`scheduler_wait_ms` 保持旧口径。
   - progressive timeline mode 下：

     ```text
     scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
     ```

   - S9-C 中 `kv_load_wait_ms` 仍为 0。

6. 新增和扩展测试。
   - 新增 `tests/unit/replay/test_compute_wait_accounting.py`。
   - 扩展 `tests/unit/streaming/test_streaming_replay.py`，增加 progressive compute wait parity。

### 13.2 本 Batch 没有完成什么

S9-C 未实现：

- KV load wait。
- KV transfer queue。
- progressive materialization。
- chunk-level TTFT composer。
- report/export 字段接入。
- CLI / runner / config 接入。
- per-chunk timeline 明细输出。

这些能力继续留给 S9-D 之后的 batch。

### 13.3 对核心 replay 语义的影响

- `cached_tokens`：未改变。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`：未改变。
- `finish_time_ms` / `ttft_ms`：未改变。
- `cache event` 顺序：未改变。
- materialization timing：未改变，仍是 finish-time materialization。
- 实例隔离：未改变，compute wait 在每个 instance replay 内独立统计。
- true streaming：未改变 streaming 读取方式，不全量加载 request。

变化仅限新 progressive timeline mode 下的 timeline typed metrics：

- `compute_wait_ms`
- `scheduler_wait_ms`
- iteration-level `waiting_for_compute_count`
- iteration-level `compute_wait_ms`

legacy mode 维持旧结果。

### 13.4 测试结果

已运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_compute_wait_accounting.py tests/unit/streaming/test_streaming_replay.py
```

结果：12 passed。

已运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_timeline_schema.py tests/unit/replay/test_step8_latency_contribution_metrics.py tests/integration/test_step4_batch_aware_replay.py tests/integration/test_step8_streaming_kv_load_e2e.py
```

结果：20 passed。

已运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_chunked_prefill.py
```

结果：18 passed。

已运行：

```bash
.venv/bin/ruff check src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/replay tests/unit/streaming
```

结果：All checks passed。

已运行：

```bash
git diff --check
```

结果：通过。

### 13.5 风险与注意事项

1. progressive timeline mode 目前只接入 engine 构造参数，尚未接入 CLI / runner / config。
2. S9-C 仍未把 `ttft_granularity` 切为完整 chunk composer 口径，避免提前宣称 chunk-level TTFT 已完成。
3. DDR/CPU hit 的等待时间仍未从 compute wait 中分离；S9-D 需要接入 `WAITING_FOR_KV_LOAD`。
4. iteration-level compute wait 是 active-state 聚合；request 在 iteration 执行期间到达的 wait 会在下一次 arrival move 时进入 request-level source of truth。

### 13.6 是否可以进入 S9-D

可以进入 S9-D。

判断依据：

- legacy mode 回归通过。
- progressive timeline mode 下 `compute_wait_ms` deterministic 且可解释。
- list replay 与 streaming replay 小 E2E 结果一致。
- Step8 KV load E2E 未被破坏。
- S9-C 未改变 cache hit、cache event、materialization、finish time 和 TTFT 总口径。
