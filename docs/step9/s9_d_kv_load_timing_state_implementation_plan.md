# S9-D Implementation Plan: KV Load Timing State

状态：已审批通过，已执行完成。

本文件是 S9-D 的代码编写方案和执行记录。

## 1. Batch 定位

本 Batch 属于核心仿真器开发。

改动等级：L3。

原因：

- S9-D 会把 DDR/CPU hit 的 KV load 时间从 Step8 的 scalar latency contribution，显式接入
  Step9 progressive timeline。
- 新增的 `kv_load_wait_ms` 会影响 progressive timeline mode 下的 `scheduler_wait_ms`
  拆分口径。
- 本 Batch 不改变 cache lookup 和 materialization，但会修改 request / iteration typed metrics
  的 timeline 字段。
- 旧 `batch_aware_hbm_ddr_lru` / legacy timeline mode 必须保持 Step8 行为不变。

## 2. 本 Batch 做什么

S9-D 只实现 KV load timing state 和 accounting。

具体做：

1. 在 `RequestState` 中补齐 KV load wait 记录方法。
   - 新增 `record_kv_load_wait(duration_ms)`。
   - 保持非负校验。
   - `kv_load_wait_ms` 作为 request-level TTFT timeline 字段。

2. 在 progressive timeline mode 下，把 scheduled slice 中的 KV load latency 记入
   `kv_load_wait_ms`。
   - 复用 Step8 已有 `ServingLatencyProfile` / `KVLoadLatencyComponent` 计算出的
     `kv_load_ms`。
   - 复用现有 `split_iteration_latency_contributions()` 的 per-request KV load attribution。
   - 不引入新的 latency backend。

3. 在 progressive timeline mode 下显式标记 request timeline state。
   - 有 KV load 的 scheduled slice：

     ```text
     WAITING_FOR_KV_LOAD -> RUNNING_CHUNK -> WAITING_FOR_COMPUTE / FINISHED
     ```

   - DDR-only zero-miss：

     ```text
     WAITING_FOR_KV_LOAD -> FINISHED
     ```

   - HBM-only zero-miss 仍保持 immediate finish。

4. 在 typed metrics 中输出 KV load wait。
   - request metrics：

     ```text
     kv_load_wait_ms = state.kv_load_wait_ms
     scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
     ```

   - iteration metrics：

     ```text
     waiting_for_kv_load_count = shape.kv_load_request_count
     kv_load_wait_ms = iteration kv_load_ms
     ```

   - 以上只在 progressive timeline mode 下填真实值；legacy mode 默认仍为 0。

5. 保持 Step8 的 `kv_load_ms` 兼容字段。
   - `kv_load_ms` 仍表示 request/iteration 被归因到的 KV load duration。
   - `kv_load_wait_ms` 是 Step9 timeline 语义字段。

## 3. 本 Batch 不做什么

S9-D 不做：

- 不新增 shared-link queue。
- 不新增 KV transfer queue。
- 不模拟跨请求 bandwidth sharing / queue priority / backpressure。
- 不实现 load completion event dump。
- 不实现 DDR hit promotion。
- 不改变 scheduler token selection。
- 不改变 cache lookup。
- 不改变 HBM / DDR hit tokens。
- 不改变 eviction policy。
- 不改变 finish-time materialization。
- 不实现 progressive materialization。
- 不实现 chunk-level TTFT composer。
- 不接入 CLI / runner / config。
- 不修改 report/export。
- 不接入 Ramulator2 / Mooncake online replay。

边界说明：

- S9-D 是 timeline accounting split，不是完整 KV transfer simulator。
- S9-D 的 KV load wait 来自当前 iteration latency breakdown，不新增真实异步 transfer
  队列。
- 更接近真实系统的 instance-local `shared_link_fifo_v1`、queue depth、load queue wait
  留给 S9-E。

如果实现时发现必须修改 `src/infertwin/cache/**`、`src/infertwin/latency/**`、
`src/infertwin/report/**`、`src/infertwin/cli/**`、`src/infertwin/config/**` 或
`src/infertwin/external/**`，应暂停并重新评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `tests/unit/replay/test_kv_load_timing_state.py` | 覆盖 progressive timeline mode 下 KV load wait accounting、DDR-only zero-miss、legacy mode 兼容和 compute wait + KV load wait 组合。 |

### 4.2 修改文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/scheduler/state.py` | 为 `RequestState` 增加 `record_kv_load_wait()`；补齐 timeline state 转移所需的小方法或直接字段更新。 |
| `src/infertwin/replay/event_loop.py` | 在 scheduled slice 应用阶段，将 per-request KV load contribution 记入 `kv_load_wait_ms`，并标记 `WAITING_FOR_KV_LOAD`。 |
| `src/infertwin/replay/metrics.py` | 在 iteration metrics builder 中支持 `waiting_for_kv_load_count` 和 `kv_load_wait_ms` 参数；request metrics 已有字段，需确认 progressive mode 正确填充。 |
| `src/infertwin/streaming/replay.py` | 如 event loop helper 签名变化，保持 streaming path 使用相同 helper；不引入 streaming 专用逻辑。 |
| `tests/unit/replay/test_step8_kv_load_replay.py` | 保留 Step8 legacy 回归；可增加一条断言确认未传 progressive mode 时 `kv_load_wait_ms == 0`。 |
| `tests/unit/streaming/test_streaming_replay.py` | 可增加小规模 progressive list / streaming parity，验证 DDR hit 的 `kv_load_wait_ms` 一致。 |
| `docs/step9/s9_d_kv_load_timing_state_implementation_plan.md` | 本文件；开发后补充执行记录、测试结果和进入 S9-E 的判断。 |

### 4.3 禁止修改文件

S9-D 禁止修改：

- `src/infertwin/cache/**`
- `src/infertwin/latency/**`
- `src/infertwin/report/**`
- `src/infertwin/cli/**`
- `src/infertwin/config/**`
- `src/infertwin/external/**`
- `scripts/**`
- `configs/**`

例外：如果开发中发现 `streaming/replay.py` 不需要修改，则不改。

## 5. 每个文件的职责

### 5.1 `src/infertwin/scheduler/state.py`

只负责 request-local timeline counters 和状态字段，不负责 latency 估算。

建议新增：

```python
def record_kv_load_wait(self, duration_ms: float) -> None:
    ...
```

语义：

- `duration_ms` 必须非负。
- 累加到 `kv_load_wait_ms`。
- 不修改 `kv_load_ms`；`kv_load_ms` 继续由 `record_latency_contribution()` 维护，作为
  Step8 兼容字段。

建议 timeline state 更新规则：

- request 开始承担 KV load 时：

  ```text
  timeline_state = WAITING_FOR_KV_LOAD
  ```

- 同一 scheduled slice 后续有 prefill tokens 时：

  ```text
  timeline_state = RUNNING_CHUNK
  ```

- slice 应用后 request 未完成：

  ```text
  timeline_state = WAITING_FOR_COMPUTE
  ```

- request 完成：

  ```text
  timeline_state = FINISHED
  ```

说明：

- `RequestStatus` 仍保持 scheduler-visible 生命周期：`WAITING` / `RUNNING` / `FINISHED`。
- `timeline_state` 不参与 S9-D 的 scheduler selection。

### 5.2 `src/infertwin/replay/event_loop.py`

S9-D 的核心改动在 `_apply_schedule_result()`。

当前流程：

```text
schedule_result
  -> latency estimate
  -> split_iteration_latency_contributions()
  -> state.record_latency_contribution(prefill, kv_load, queue)
  -> apply scheduled tokens / load-only finish
```

S9-D 建议流程：

```text
schedule_result
  -> latency estimate
  -> split_iteration_latency_contributions()
  -> for each scheduled slice:
       if progressive mode and slice has kv_load:
           state.timeline_state = WAITING_FOR_KV_LOAD
           state.record_kv_load_wait(contribution.kv_load_ms)
       state.record_latency_contribution(...)
       if slice has prefill tokens:
           state.timeline_state = RUNNING_CHUNK
           state.apply_scheduled_tokens(...)
       else:
           state.apply_load_only_iteration(...)
       if not finished:
           state.timeline_state = WAITING_FOR_COMPUTE
```

建议新增 helper：

```python
def _record_kv_load_timing(
    *,
    state: RequestState,
    scheduled_slice: ScheduledSlice,
    kv_load_ms: float,
    timeline_mode: str,
) -> KVLoadTimingAccounting:
    ...
```

内部小 dataclass：

```python
@dataclass(frozen=True, slots=True)
class KVLoadTimingAccounting:
    waiting_for_kv_load_count: int = 0
    kv_load_wait_ms: float = 0.0
```

职责边界：

- 只在 progressive timeline mode 下记录真实 KV load wait。
- legacy mode 返回 0，不修改 `kv_load_wait_ms`。
- 不改变 `latency.duration_ms`。
- 不改变 `finish_ms`。
- 不改变 materialization。

### 5.3 `src/infertwin/replay/metrics.py`

修改 `build_iteration_metrics()`，增加默认参数：

```python
waiting_for_kv_load_count: int = 0
kv_load_wait_ms: float = 0.0
```

默认值保持旧测试兼容。

request metrics 已经在 S9-C 中从 `RequestState` 读取：

```python
kv_load_wait_ms=state.kv_load_wait_ms
```

S9-D 需要确认：

- legacy mode 下 `state.kv_load_wait_ms` 仍为 0。
- progressive mode 下 DDR hit request 输出真实 `kv_load_wait_ms`。
- `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms` 已由 S9-C 接入，无需重写。

### 5.4 `src/infertwin/streaming/replay.py`

原则：

- streaming replay 仍调用 `BatchAwareReplayEngine._apply_schedule_result()`。
- 如果 S9-D 的新 helper 完全封装在 event loop 内，streaming 文件只需要传递新增 accounting
  参数，或不需要修改。
- 不做 streaming 专用 KV load 逻辑。
- 不预读未来 request。

### 5.5 测试文件

新增 `tests/unit/replay/test_kv_load_timing_state.py`。

建议复用 `tests/unit/replay/test_step8_kv_load_replay.py` 中的 `_LookupMapCache` / synthetic
request 方式，或者局部复制小 helper，避免把测试依赖做成生产接口。

重点覆盖 progressive mode。

## 6. 新增或修改的数据结构 / schema / interface

### 6.1 RequestState

新增方法：

```python
record_kv_load_wait(duration_ms: float) -> None
```

已存在字段继续使用：

```python
kv_load_wait_ms: float
timeline_state: RequestTimelineState
```

### 6.2 Event loop internal accounting

新增内部 accounting dataclass：

```python
KVLoadTimingAccounting(
    waiting_for_kv_load_count: int = 0,
    kv_load_wait_ms: float = 0.0,
)
```

该类型只在 replay event loop 内部使用，不作为 public result schema。

### 6.3 IterationMetrics builder

`build_iteration_metrics()` 增加可选 keyword-only 参数：

```python
waiting_for_kv_load_count: int = 0
kv_load_wait_ms: float = 0.0
```

`IterationMetrics` 字段已存在：

```python
waiting_for_kv_load_count: int = 0
kv_load_wait_ms: float = 0.0
```

### 6.4 Replay engine public interface

不新增 public engine 参数。

继续复用 S9-C 已新增的：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
```

## 7. 核心算法逻辑

### 7.1 判断一个 scheduled slice 是否承担 KV load

使用 `ScheduledSlice` 已有字段：

```text
kv_load_tokens > 0 or kv_load_bytes > 0
```

如果为 true，说明该 request 第一次 scheduled slice 消费了 pending KV load shape。

### 7.2 计算 request-level KV load wait

继续使用 Step8 已有 attribution：

```text
contribution = split_iteration_latency_contributions(...)[request_id]
kv_load_wait_ms = contribution.kv_load_ms
```

原因：

- `ServingLatencyProfile` 已经把 iteration-level `kv_load_ms` 写入 `LatencyResult.details`。
- `split_iteration_latency_contributions()` 已经按 bytes 优先、tokens fallback 分摊到 request。
- S9-D 不新增新的 KV load 估算器。

### 7.3 request 状态推进

对每个 scheduled slice：

```text
if progressive mode and slice has kv load:
    state.timeline_state = WAITING_FOR_KV_LOAD
    state.record_kv_load_wait(contribution.kv_load_ms)

state.record_latency_contribution(...)

if scheduled_prefill_tokens == 0:
    state.apply_load_only_iteration(finish_time_ms)
else:
    state.timeline_state = RUNNING_CHUNK
    state.apply_scheduled_tokens(scheduled_tokens, finish_time_ms)

if state.status != FINISHED:
    state.timeline_state = WAITING_FOR_COMPUTE
```

注意：

- 当前 event loop 仍以 iteration finish time 应用结果。
- S9-D 不新增独立 load completion timestamp。
- S9-D 不拆分同一 iteration 的 public start/finish events。
- 真实的 load completion event 和 transfer queue 留给 S9-E。

### 7.4 iteration-level KV load wait

在 progressive mode 下：

```text
waiting_for_kv_load_count = shape.kv_load_request_count
kv_load_wait_ms = latency_breakdown.kv_load_ms
```

legacy mode 下保持：

```text
waiting_for_kv_load_count = 0
kv_load_wait_ms = 0
```

### 7.5 HBM-only zero-miss

保持现状：

```text
HBM hit tokens == prompt tokens
DDR hit tokens == 0
miss_tokens == 0
kv_load_wait_ms == 0
scheduled_iteration_count == 0
finish_time == now
```

不产生 iteration。

### 7.6 DDR-only zero-miss

保持 Step8 的 load-only iteration，但在 progressive mode 下新增 timeline fields：

```text
scheduled_prefill_tokens == 0
kv_load_tokens > 0 or kv_load_bytes > 0
kv_load_wait_ms == kv_load_ms
prefill_compute_ms == 0
ttft_ms == kv_load_wait_ms
scheduler_wait_ms == compute_wait_ms + kv_load_wait_ms
```

### 7.7 DDR hit + miss tokens

保持 Step8 的 total TTFT：

```text
ttft_ms = finish_time_ms - arrival_time_ms
```

在 progressive mode 下拆分：

```text
kv_load_wait_ms = request attributed kv_load_ms
uncached_prefill_compute_ms = request attributed prefill_compute_ms
scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
```

说明：

- S9-D 不改变总 duration。
- S9-D 只是把 KV load latency 放入 timeline wait 字段。
- 完整 request-level TTFT composer 和 batch residual reconciliation 留给 S9-F。

## 8. 对核心 replay 语义的影响

### 8.1 是否改变 `cached_tokens`

不改变。

S9-D 不修改 prefix lookup、block conversion、CP / DCP / PCP / MTP accounting。

### 8.2 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`

不改变。

HBM / DDR hit 统计仍由 cache lookup 和 `LookupMetrics.from_result()` 决定。

### 8.3 是否改变 `finish_time` / `ttft_ms`

legacy mode：不改变。

progressive mode：S9-D 计划不改变 `finish_time_ms` 和 `ttft_ms` 总口径。

会改变 progressive mode 下的拆分字段：

```text
kv_load_wait_ms
scheduler_wait_ms
waiting_for_kv_load_count
iteration.kv_load_wait_ms
```

### 8.4 是否改变 cache event 顺序

不改变。

S9-D 不修改 cache lookup、materialization、eviction 或 event sink。

### 8.5 是否改变 materialization timing

不改变。

仍然使用现有 finish-time materialization。

### 8.6 是否改变实例隔离

不改变。

KV load wait 在每个 instance replay 内独立记录。不同实例之间不共享 transfer state。

### 8.7 是否影响 true streaming 大 trace

不应显著影响。

- 不保存 per-load 明细。
- 每个 active request 只增加已存在字段的累加值。
- 不预读 future request。
- 不改变 shard/source 读取方式。

## 9. 测试计划

### 9.1 单测

新增 `tests/unit/replay/test_kv_load_timing_state.py`，建议覆盖：

1. legacy mode 下 Step8 行为不变。
   - DDR hit request `kv_load_ms > 0`。
   - `kv_load_wait_ms == 0`。
   - `scheduler_wait_ms` 仍为旧口径。

2. progressive mode 下 DDR hit + miss request。
   - `kv_load_wait_ms == kv_load_ms`。
   - `prefill_compute_ms` 不变。
   - `ttft_ms` 不变。
   - `scheduler_wait_ms == compute_wait_ms + kv_load_wait_ms`。
   - hit / miss tokens 不变。

3. progressive mode 下 DDR-only zero-miss。
   - `scheduled_prefill_tokens == 0`。
   - `kv_load_wait_ms == kv_load_ms`。
   - `prefill_compute_ms == 0`。
   - `ttft_ms == kv_load_wait_ms`。

4. progressive mode 下 HBM-only zero-miss。
   - immediate finish。
   - `kv_load_wait_ms == 0`。
   - 不产生 iteration。

5. compute wait + KV load wait 组合。
   - request 先因为 token budget 等待一轮，产生 `compute_wait_ms`。
   - 随后第一次 scheduled slice 有 DDR hit，产生 `kv_load_wait_ms`。
   - `scheduler_wait_ms == compute_wait_ms + kv_load_wait_ms`。

6. iteration metrics。
   - progressive mode 下 `waiting_for_kv_load_count == shape.kv_load_request_count`。
   - `iteration.kv_load_wait_ms == iteration.kv_load_ms`。
   - legacy mode 下两个字段为 0。

7. 负时间防护。
   - `record_kv_load_wait(-1)` fail-fast。

### 9.2 集成测试

建议运行现有相关集成：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

目的：

- 确认 Step8 legacy KV load E2E 不被 S9-D 破坏。
- 确认基础 batch-aware replay 不回退。

### 9.3 小 E2E

建议增加或扩展 streaming parity：

```text
same synthetic DDR-hit requests
same cache lookup map
same ServingLatencyProfile
list replay result == streaming replay result
```

至少比较：

- request_id。
- finish_time_ms。
- ttft_ms。
- hbm_hit_tokens / ddr_hit_tokens / miss_tokens。
- kv_load_ms。
- kv_load_wait_ms。
- scheduler_wait_ms。

### 9.4 是否需要 golden 更新

不需要更新 CSV golden。

理由：

- S9-D 不接 report/export。
- legacy mode 默认输出不变。
- progressive timeline mode 尚未成为 runner/report 默认入口。

### 9.5 建议运行命令

开发完成后建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_compute_wait_accounting.py tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

```bash
.venv/bin/ruff check src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/replay tests/unit/scheduler tests/unit/streaming
```

```bash
git diff --check
```

## 10. 风险与回滚边界

### 10.1 主要风险

1. 与 Step8 `kv_load_ms` 语义混淆。
   - `kv_load_ms` 保留兼容字段。
   - `kv_load_wait_ms` 是 Step9 timeline 字段。

2. timeline 过度承诺。
   - S9-D 不创建真实 load completion event。
   - S9-D 不模拟 queue / bandwidth sharing。
   - S9-D 只把当前 iteration 内的 KV load contribution 显式归入 wait。

3. TTFT 分解暂不完全闭合。
   - `ttft_ms` 仍来自 `finish_time_ms - arrival_time_ms`。
   - 同一 batch 中非 loading request 可能仍受 batch-level load duration 影响。
   - 完整 composer 和 residual 字段留给 S9-F。

4. old mode regression。
   - 默认 legacy mode 下 `kv_load_wait_ms` 必须保持 0。
   - Step8 KV load tests 必须继续通过。

### 10.2 回滚边界

如果 S9-D 出现问题，可以回滚：

- `RequestState.record_kv_load_wait()`。
- `_apply_schedule_result()` 中对 `kv_load_wait_ms` 的记录。
- `build_iteration_metrics()` 的新增可选参数。
- 新增 S9-D 单测。

S9-B/S9-C 已新增的 schema 和 compute wait accounting 不需要回滚。

## 11. 完成后如何判断可以进入 S9-E

满足以下条件后，可以进入 S9-E：

1. legacy mode 回归通过。
2. progressive mode 下 DDR hit request 能输出真实 `kv_load_wait_ms`。
3. DDR-only zero-miss 在 progressive mode 下仍通过 load-only iteration finish。
4. HBM-only zero-miss 仍 immediate finish。
5. `cached_tokens`、HBM/DDR hit、miss tokens、cache events、materialization 均未变化。
6. list replay 与 streaming replay 小 E2E 一致。
7. 新增和相关测试通过。
8. `ruff check` 和 `git diff --check` 通过。
9. 本文档补充执行记录：
   - 做了什么。
   - 没有做什么。
   - 测试结果。
   - 风险和进入 S9-E 的判断。

## 12. 需要用户审批的内容

以下设计点已审批通过：

1. 接受 S9-D 属于核心仿真器，改动等级 L3。
2. 接受 S9-D 只实现 `kv_load_wait_ms` timing/accounting，不实现 shared-link queue。
3. 接受 S9-D 复用 Step8 的 `ServingLatencyProfile` / `KVLoadLatencyComponent`，不新增 latency backend。
4. 接受 S9-D 不改变 scheduler token selection，不把 DDR load 拆成新的 public scheduler iteration。
5. 接受 S9-D 不改变 `finish_time_ms` / `ttft_ms` 总口径，只改变 progressive mode 下的 timeline 拆分字段。
6. 接受 legacy mode 下 `kv_load_wait_ms` 保持 0，Step8 行为不变。
7. 接受 `kv_load_ms` 继续作为兼容字段，`kv_load_wait_ms` 作为 Step9 timeline 字段。
8. 接受 S9-D 不接 CLI / runner / config / report/export。
9. 接受本 Batch 只修改计划列出的文件；如需越界修改，暂停并重新评审。
10. 接受测试范围：新增 S9-D 单测、compute wait 回归、Step8 KV load 回归、scheduler/request-state 回归、streaming parity、相关集成、ruff、`git diff --check`。

## 13. 执行记录

执行状态：已完成。

### 13.1 实际完成内容

S9-D 完成了以下代码开发：

1. `RequestState` 增加 `record_kv_load_wait()`。
   - 只负责累加 request-level `kv_load_wait_ms`。
   - 保持非负校验。
   - 不改变 Step8 兼容字段 `kv_load_ms`。

2. replay event loop 接入 progressive KV load timing accounting。
   - 对 scheduled slice 中有 `kv_load_tokens` 或 `kv_load_bytes` 的 request，在 progressive
     timeline mode 下记录 `WAITING_FOR_KV_LOAD`。
   - 复用 `split_iteration_latency_contributions()` 得到的 per-request `kv_load_ms`，累加到
     `kv_load_wait_ms`。
   - scheduled prefill slice 后继续进入 `RUNNING_CHUNK`；未完成 request 回到
     `WAITING_FOR_COMPUTE`；完成 request 进入 `FINISHED`。
   - legacy mode 下 helper 为 no-op。

3. iteration typed metrics 接入 KV load wait 聚合。
   - `waiting_for_kv_load_count`
   - `kv_load_wait_ms`
   - legacy mode 默认仍为 0。

4. 新增 `tests/unit/replay/test_kv_load_timing_state.py`。
   - 覆盖 legacy mode 兼容。
   - 覆盖 DDR hit + miss request。
   - 覆盖 DDR-only zero-miss。
   - 覆盖 HBM-only zero-miss immediate finish。
   - 覆盖 compute wait + KV load wait 组合。
   - 覆盖 list replay 与 streaming replay parity。

### 13.2 本 Batch 没有完成什么

S9-D 未实现：

- shared-link queue。
- KV transfer queue。
- queue depth / priority / backpressure。
- load completion event。
- DDR hit promotion。
- progressive materialization。
- chunk-level TTFT composer。
- CLI / runner / config 接入。
- report/export 字段接入。
- online Ramulator2 / Mooncake replay。

这些能力继续留给 S9-E 之后的 batch。

### 13.3 对核心 replay 语义的影响

- `cached_tokens`：未改变。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`：未改变。
- `finish_time_ms` / `ttft_ms`：未改变。
- `cache event` 顺序：未改变。
- materialization timing：未改变，仍是 finish-time materialization。
- scheduler token selection：未改变。
- 实例隔离：未改变，KV load wait 在每个 instance replay 内独立统计。
- true streaming：未改变读取方式，不全量加载 request。

变化仅限 progressive timeline mode 下的 timeline typed metrics：

- request-level `kv_load_wait_ms`
- progressive mode 下的 `scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms`
- iteration-level `waiting_for_kv_load_count`
- iteration-level `kv_load_wait_ms`

legacy mode 维持 Step8 结果。

### 13.4 测试结果

已运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_compute_wait_accounting.py tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py
```

结果：24 passed。

已运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py
```

结果：20 passed。

已运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

结果：2 passed。

已运行：

```bash
.venv/bin/ruff check src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/replay tests/unit/scheduler tests/unit/streaming
```

结果：All checks passed。

已运行：

```bash
git diff --check
```

结果：通过。

### 13.5 风险与注意事项

1. S9-D 仍不是完整 KV transfer simulator。
   - 没有 load queue。
   - 没有带宽共享。
   - 没有 load completion event。

2. `kv_load_ms` 与 `kv_load_wait_ms` 需要继续区分。
   - `kv_load_ms` 是 Step8 兼容字段。
   - `kv_load_wait_ms` 是 Step9 progressive timeline 字段。

3. 同一 batch 内非 loading request 仍可能受 iteration duration 影响。
   - S9-D 不重构 batch-level duration。
   - 完整 TTFT composer 和 residual accounting 留给 S9-F。

4. progressive timeline mode 仍只接入 engine 构造参数。
   - CLI / runner / config / report 接入留给后续 batch。

### 13.6 是否可以进入 S9-E

可以进入 S9-E。

判断依据：

- legacy mode 回归通过。
- progressive mode 下 DDR hit request 能输出真实 `kv_load_wait_ms`。
- DDR-only zero-miss 在 progressive mode 下通过 load-only iteration finish。
- HBM-only zero-miss 仍 immediate finish。
- list replay 与 streaming replay 小 E2E 结果一致。
- Step8 streaming KV load E2E 未被破坏。
- S9-D 未改变 cache hit、cache event、materialization、scheduler selection、finish time 和 TTFT 总口径。
