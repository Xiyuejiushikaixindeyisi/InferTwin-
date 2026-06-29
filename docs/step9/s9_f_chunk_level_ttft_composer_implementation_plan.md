# S9-F Implementation Plan: Chunk-Level TTFT Composer

状态：已审批通过，已开发完成。

本文件是 S9-F 的代码编写方案和执行记录。

## 1. Batch 定位

本 Batch 属于核心仿真器开发。

改动等级：L3。

原因：

- S9-F 会把 S9-C / S9-D / S9-E 已接入的 timeline accounting 收敛成 request-level
  TTFT composition。
- S9-F 会修改 progressive timeline mode 下的 request typed result 字段：
  `ttft_granularity`、`chunk_count`、`load_event_count`、`unattributed_ttft_ms`。
- S9-F 不改变 scheduler 选 batch、cache lookup、hit/miss accounting、materialization 或
  eviction，但会明确 `ttft_ms` 的组成不变量。
- legacy mode 必须保持 Step8 / S9-E 以前的总口径不变。

## 2. 本 Batch 做什么

S9-F 实现一个轻量、可测试的 `RequestTTFTComposer`。

核心目标：

```text
progressive request ttft_ms
  = compute_wait_ms
  + kv_load_wait_ms
  + uncached_prefill_compute_ms
  + unattributed_ttft_ms
```

其中：

- `compute_wait_ms` 来自 S9-C。
- `kv_load_wait_ms` 来自 S9-D / S9-E。
- `uncached_prefill_compute_ms` 来自 Step8/S9-D 已有 per-request prefill attribution。
- `unattributed_ttft_ms` 是当前 replay 粒度下尚未被 compute wait、KV load wait、
  prefill compute 解释的 TTFT 残差，使 composed TTFT 与
  `finish_time_ms - arrival_time_ms` 闭合。
- `unattributed_ttft_ms` 不是物理建模结果，不代表真实硬件序列化、通信、DMA、
  Mooncake 或 HCCL 耗时；它只用于暴露当前 replay 粒度不足造成的未归因时间。

具体做：

1. 新增 request-level TTFT composition schema/helper。
   - 把组成字段集中到一个 composer，而不是让 report/export 或测试自行拼接。
   - 对 progressive mode 做不变量校验。
   - 对 legacy mode 只做兼容 pass-through。

2. 在 request state 中记录 chunk/load event 计数。
   - `chunk_count` 只统计实际执行 prefill compute 的 chunk。
   - load-only iteration 不计入 `chunk_count`。
   - `load_event_count` 统计 request-level KV load event。S9-F v1 中每条 request 最多一次，
     未来 Step9 后续或 V2 可以扩展为多次 chunk/layer/page load。

3. 在 `build_request_metrics()` 中使用 composer。
   - legacy mode：

     ```text
     ttft_ms = finish_time_ms - arrival_time_ms
     scheduler_wait_ms = first_scheduled_time_ms - arrival_time_ms
     ttft_granularity = iteration
     chunk_count = 0
     load_event_count = 0
     unattributed_ttft_ms = 0
     ```

   - progressive mode：

     ```text
     observed_ttft_ms = finish_time_ms - arrival_time_ms
     base_ms = compute_wait_ms + kv_load_wait_ms + uncached_prefill_compute_ms
     unattributed_ttft_ms = observed_ttft_ms - base_ms
     ttft_ms = base_ms + unattributed_ttft_ms
     scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
     ttft_granularity = chunk
     ```

4. 在 `build_iteration_metrics()` 中填充 chunk 计数。
   - progressive mode 下，`scheduled_chunk_count` 统计本轮 scheduled prefill slices 数量。
   - load-only slices 不计入 `scheduled_chunk_count`。
   - legacy mode 保持默认 `0`，避免旧 mode 新字段被误解为已启用 chunk timeline。

5. 保持 list replay 与 streaming replay 一致。
   - streaming replay 继续消费同一 `build_request_metrics()` / `build_iteration_metrics()`。
   - 不为 streaming 增加专用 TTFT 拼接逻辑。

## 3. 本 Batch 不做什么

S9-F 不做：

- 不实现 progressive full-block materialization。
- 不改变 cache lookup。
- 不改变 HBM / DDR hit tokens。
- 不改变 miss tokens。
- 不改变 materialization timing。
- 不改变 eviction policy。
- 不改变 cache event 顺序。
- 不改变 scheduler token selection。
- 不实现真实 async load completion event。
- 不实现 same-request layerwise compute/load overlap。
- 不实现 DDR hit promotion。
- 不实现 decode / TPOT。
- 不实现 per-chunk timeline 明细输出。
- 不接 CLI / runner / config。
- 不接 report/export。
- 不接 Ramulator2 / Mooncake online replay。

边界说明：

- S9-F v1 是 composition layer，不是新的 latency backend。
- S9-F v1 不改变 `finish_time_ms`。
- S9-F v1 会让 progressive mode 的 `ttft_ms` 明确由组成字段求和得到；通过
  `unattributed_ttft_ms` 与当前 blocking iteration finish time 对齐。
- `unattributed_ttft_ms` 是诊断字段。后续如果引入更细粒度的 load completion、
  per-request chunk completion 或真正异步 timeline，它应该逐步减少，而不是被解释成某类
  真实物理耗时。
- 如果 composition 出现负 residual，说明前置 wait/compute/load 字段发生双重计费或时间线不一致，
  应 fail-fast，而不是静默截断。

如果实现时发现必须修改 `src/infertwin/cache/**`、`src/infertwin/latency/**`、
`src/infertwin/report/**`、`src/infertwin/cli/**`、`src/infertwin/config/**` 或
`src/infertwin/external/**`，应暂停并重新评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/replay/ttft.py` | 定义 `RequestTTFTComposition` 和 `RequestTTFTComposer`，集中处理 request-level TTFT composition 和不变量校验。 |
| `tests/unit/replay/test_ttft_composer.py` | 单测 composer 的 legacy/progressive 行为、residual 闭合、负 residual fail-fast 和字段非负约束。 |
| `tests/unit/replay/test_chunk_level_ttft_composer.py` | 覆盖 replay 中 progressive mode 的 chunk/load count、unattributed residual、list/streaming parity 和 legacy 兼容。 |
| `docs/step9/s9_f_chunk_level_ttft_composer_implementation_plan.md` | 本文件；开发后补充执行记录、测试结果和进入 S9-G 的判断。 |

### 4.2 修改文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/scheduler/state.py` | 在 `RequestState` 中新增 `chunk_count`、`load_event_count`；将既有 `modeled_serialization_ms` placeholder 迁移为 `unattributed_ttft_ms`，并在 prefill chunk / KV load event 发生时累加。 |
| `src/infertwin/replay/event_loop.py` | 在 scheduled slice 应用阶段记录 chunk count / load event count；继续复用 S9-E transfer queue；不改变 scheduler selection。 |
| `src/infertwin/replay/metrics.py` | 使用 `RequestTTFTComposer` 构造 request metrics；将 request metric 的既有 `modeled_serialization_ms` placeholder 迁移为 `unattributed_ttft_ms`；在 progressive mode 下填充 `ttft_granularity`、`chunk_count`、`load_event_count`、`unattributed_ttft_ms` 和 iteration `scheduled_chunk_count`。 |
| `src/infertwin/replay/timeline.py` | 可选：给 `RequestTimelineSummary` 增加 `composed_ttft_ms` property；若 `ttft.py` 已能表达不变量，可不改。 |
| `tests/unit/replay/test_kv_load_timing_state.py` | 增加或调整少量断言，确认 progressive mode 下 `ttft_ms` composition 与旧期望一致，且 `unattributed_ttft_ms` 默认合理。 |
| `tests/unit/replay/test_kv_transfer_queue_replay.py` | 增加同 iteration 多 DDR load 的 residual / `unattributed_ttft_ms` 断言。 |
| `tests/unit/streaming/test_streaming_replay.py` | 如新增专门 replay parity 测试已覆盖，可不改；否则增加 streaming parity 小测试。 |

### 4.3 禁止修改文件

S9-F 禁止修改：

- `src/infertwin/cache/**`
- `src/infertwin/latency/**`
- `src/infertwin/report/**`
- `src/infertwin/cli/**`
- `src/infertwin/config/**`
- `src/infertwin/external/**`
- `scripts/**`
- `configs/**`

例外：

- 如果实现证明 `src/infertwin/replay/timeline.py` 不需要修改，则不改。
- 如果新增 `tests/unit/replay/test_chunk_level_ttft_composer.py` 已完整覆盖 streaming parity，
  则不修改 `tests/unit/streaming/test_streaming_replay.py`。

## 5. 每个文件的职责

### 5.1 `src/infertwin/replay/ttft.py`

只负责 request-level TTFT composition。

不负责：

- scheduler selection。
- cache lookup。
- latency backend 估算。
- materialization。
- report/export。

建议数据结构：

```python
@dataclass(frozen=True, slots=True)
class RequestTTFTComposition:
    timeline_mode: str
    ttft_granularity: str
    observed_ttft_ms: float
    ttft_ms: float
    scheduler_wait_ms: float
    compute_wait_ms: float
    kv_load_wait_ms: float
    uncached_prefill_compute_ms: float
    unattributed_ttft_ms: float
    chunk_count: int
    load_event_count: int
```

建议 composer：

```python
class RequestTTFTComposer:
    def compose(
        self,
        *,
        request: SimulationRequest,
        state: RequestState,
        finish_time_ms: float,
        first_scheduled_time_ms: float,
    ) -> RequestTTFTComposition:
        ...
```

legacy mode 逻辑：

```text
observed_ttft_ms = finish_time_ms - request.start_time_ms
ttft_ms = observed_ttft_ms
scheduler_wait_ms = first_scheduled_time_ms - request.start_time_ms
ttft_granularity = iteration
compute_wait_ms = 0
kv_load_wait_ms = 0
uncached_prefill_compute_ms = state.prefill_compute_ms
unattributed_ttft_ms = 0
chunk_count = 0
load_event_count = 0
```

progressive mode 逻辑：

```text
observed_ttft_ms = finish_time_ms - request.start_time_ms
base_ms =
  state.compute_wait_ms
  + state.kv_load_wait_ms
  + state.prefill_compute_ms
  + state.unattributed_ttft_ms

residual_ms = observed_ttft_ms - base_ms
if residual_ms < -epsilon:
    fail-fast
if abs(residual_ms) <= epsilon:
    residual_ms = 0

unattributed_ttft_ms = state.unattributed_ttft_ms + residual_ms
ttft_ms =
  state.compute_wait_ms
  + state.kv_load_wait_ms
  + state.prefill_compute_ms
  + unattributed_ttft_ms
```

为什么需要 residual：

- 当前 S9-E 仍是 blocking iteration replay。
- 同一 iteration 内，request 的 transfer 可能早于 batch finish 完成。
- `finish_time_ms - arrival_time_ms` 仍包含 batch barrier / replay 粒度未拆分时间 /
  其他未归因时间。
- S9-F v1 用 `unattributed_ttft_ms` 显式承接这段时间，避免 TTFT 分解不闭合。
- 该字段只说明“当前 replay 无法进一步解释这段时间”，不说明这段时间在真实系统中由哪种
  硬件、通信协议或 runtime 行为产生。

不变量：

```text
ttft_ms == scheduler_wait_ms + uncached_prefill_compute_ms + unattributed_ttft_ms
scheduler_wait_ms == compute_wait_ms + kv_load_wait_ms
ttft_ms == observed_ttft_ms
```

浮点误差允许一个小 epsilon，例如 `1e-9`。

### 5.2 `src/infertwin/scheduler/state.py`

新增 request-local counters：

```python
chunk_count: int = 0
load_event_count: int = 0
```

建议方法：

```python
def record_prefill_chunk(self) -> None:
    ...

def record_kv_load_event(self, duration_ms: float) -> None:
    ...
```

职责边界：

- `chunk_count` 只统计 `scheduled_prefill_tokens > 0` 的 chunk。
- `load_event_count` 只统计实际需要 KV load 的 scheduled slice。
- 不把 load-only iteration 计入 chunk。
- 不把 HBM-only zero-miss 计入 load。
- 所有计数必须非负。

### 5.3 `src/infertwin/replay/event_loop.py`

S9-F 对 event loop 的修改应很小。

建议：

1. scheduled slice 有 KV load 时，调用 `state.record_kv_load_event(...)` 或等价 helper。
   - 可以把 S9-E 中 `state.record_kv_load_wait(transfer.elapsed_ms)` 替换为更明确的
     `state.record_kv_load_event(transfer.elapsed_ms)`。
   - 该方法同时累加 `kv_load_wait_ms` 和 `load_event_count`。

2. scheduled slice 有 prefill tokens 时，调用 `state.record_prefill_chunk()`。
   - 可以放在 `state.apply_scheduled_tokens()` 内部，避免 event loop 重复记数。
   - load-only iteration 不调用。

3. 不修改 `_prepare_scheduler_frontier()`。
4. 不修改 `_ensure_lookup()`。
5. 不修改 materialization policy 调用。
6. 不修改 transfer queue submit 顺序。

### 5.4 `src/infertwin/replay/metrics.py`

`build_request_metrics()` 改为：

```text
required first_scheduled_time_ms
required finish_time_ms
composition = RequestTTFTComposer().compose(...)
return BatchAwareRequestMetrics(
    scheduler_wait_ms=composition.scheduler_wait_ms,
    ttft_ms=composition.ttft_ms,
    timeline_mode=composition.timeline_mode,
    ttft_granularity=composition.ttft_granularity,
    compute_wait_ms=composition.compute_wait_ms,
    kv_load_wait_ms=composition.kv_load_wait_ms,
    uncached_prefill_compute_ms=composition.uncached_prefill_compute_ms,
    unattributed_ttft_ms=composition.unattributed_ttft_ms,
    chunk_count=composition.chunk_count,
    load_event_count=composition.load_event_count,
    ...
)
```

`build_iteration_metrics()` 改为：

```text
if timeline_mode == PROGRESSIVE_TIMELINE_MODE:
    scheduled_chunk_count = count(slice.scheduled_prefill_tokens > 0)
else:
    scheduled_chunk_count = 0
```

职责边界：

- `metrics.py` 只调用 composer，不自己重写公式。
- `report/export` 后续只能消费这些 typed fields。

### 5.5 `src/infertwin/replay/timeline.py`

可选修改：

```python
@property
def composed_ttft_ms(self) -> float:
    return (
        self.compute_wait_ms
        + self.kv_load_wait_ms
        + self.uncached_prefill_compute_ms
        + self.unattributed_ttft_ms
    )
```

如果 `ttft.py` 已完整覆盖 composition schema，本文件可以不改，避免把 timeline schema 变成业务逻辑模块。

## 6. 新增或修改的数据结构 / schema / interface

### 6.1 新增 internal composer schema

新增：

- `RequestTTFTComposition`
- `RequestTTFTComposer`

这些类型属于 replay internal helper，不作为 public report schema。

### 6.2 RequestState 新增 counters 和 residual 字段

新增：

```python
chunk_count: int = 0
load_event_count: int = 0
unattributed_ttft_ms: float = 0.0
```

这些字段只用于 typed metrics，不参与 scheduler selection。

同时将 S9-B/S9-C 中预留的 `modeled_serialization_ms` 语义迁移为
`unattributed_ttft_ms`。S9-F 之后不应继续新增或输出
`modeled_serialization_ms`，避免把 replay residual 误解为物理序列化建模结果。

### 6.3 Request metrics 填充变化

progressive mode 下开始填充：

- `ttft_granularity = "chunk"`。
- `chunk_count = state.chunk_count`。
- `load_event_count = state.load_event_count`。
- `unattributed_ttft_ms = residual`。

legacy mode 保持：

- `ttft_granularity = "iteration"`。
- `chunk_count = 0`。
- `load_event_count = 0`。
- `unattributed_ttft_ms = 0`。

### 6.5 统计口径

`unattributed_ttft_ms` 可以在后续 report/export 中作为诊断指标统计，但 S9-F 不接
report/export。

推荐统计口径：

```text
total_unattributed_ttft_ms = sum(request.unattributed_ttft_ms)
avg_unattributed_ttft_ms = total_unattributed_ttft_ms / request_count
p50/p90/p99_unattributed_ttft_ms
unattributed_ttft_ratio = total_unattributed_ttft_ms / total_ttft_ms
```

这些统计只能说明当前 replay 粒度下未归因 TTFT 的大小，不能解释成硬件通信耗时或真实系统
serialization cost。

### 6.4 Iteration metrics 填充变化

progressive mode 下开始填充：

- `scheduled_chunk_count`。

legacy mode 保持默认 `0`。

## 7. 核心算法逻辑

### 7.1 TTFT composition

对完成的 request：

```text
observed_ttft_ms = finish_time_ms - arrival_time_ms
```

legacy mode：

```text
ttft_ms = observed_ttft_ms
scheduler_wait_ms = first_scheduled_time_ms - arrival_time_ms
```

progressive mode：

```text
scheduler_wait_ms = compute_wait_ms + kv_load_wait_ms
base_ms = scheduler_wait_ms + uncached_prefill_compute_ms + state.unattributed_ttft_ms
residual_ms = observed_ttft_ms - base_ms
unattributed_ttft_ms = state.unattributed_ttft_ms + residual_ms
ttft_ms = scheduler_wait_ms + uncached_prefill_compute_ms + unattributed_ttft_ms
```

### 7.2 Negative residual fail-fast

如果：

```text
observed_ttft_ms < base_ms - epsilon
```

则说明前置 timeline accounting 出现了双重计费或 event loop 时间不一致。

S9-F 应直接 `ValueError`，错误信息包含：

- request id。
- observed ttft。
- compute wait。
- kv load wait。
- prefill compute。
- unattributed TTFT。

不要静默把 residual 截断为 0。

### 7.3 Chunk count

规则：

```text
chunk_count += 1
```

仅当：

```text
scheduled_slice.scheduled_prefill_tokens > 0
```

不计入：

- DDR-only load-only iteration。
- HBM-only zero-miss immediate finish。
- future decode token。

### 7.4 Load event count

规则：

```text
load_event_count += 1
```

仅当 scheduled slice 发生真实 KV load：

```text
scheduled_slice.kv_load_tokens > 0 or scheduled_slice.kv_load_bytes > 0
```

S9-F v1 中每个 request 由于 `consume_pending_kv_load()` 语义，最多一次。

未来如果 Step9 或 V2 引入 chunk/layer/page load split，应新增 mode 或扩展 schema，不静默改变当前计数语义。

### 7.5 示例

两个 DDR-only request 同一 iteration 进入 shared-link FIFO：

```text
iteration duration = 4
r1 kv_load_wait_ms = 2
r2 kv_load_wait_ms = 4
```

S9-F composition：

```text
r1:
  observed_ttft = 4
  compute_wait = 0
  kv_load_wait = 2
  prefill_compute = 0
  unattributed_ttft = 2
  ttft = 4

r2:
  observed_ttft = 4
  compute_wait = 0
  kv_load_wait = 4
  prefill_compute = 0
  unattributed_ttft = 0
  ttft = 4
```

这能表达当前 blocking iteration replay 中的 batch barrier residual，同时不把 residual 混入
KV load service time。

## 8. 对核心 replay 语义的影响

### 8.1 是否改变 `cached_tokens`

不改变。

S9-F 不修改 prefix lookup、block conversion 或 cached-token accounting。

### 8.2 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`

不改变。

HBM / DDR hit 仍由 cache lookup 和 `LookupMetrics.from_result()` 决定。

### 8.3 是否改变 `finish_time` / `ttft_ms`

legacy mode：不改变。

progressive mode：

- 不改变 `finish_time_ms`。
- `ttft_ms` 总值应继续等于 `finish_time_ms - arrival_time_ms`。
- 但 `ttft_ms` 的组成字段会变得闭合且可测试。
- `unattributed_ttft_ms` 可能从 0 变为 residual。
- `ttft_granularity` 会从默认 `iteration` 变为 `chunk`。

### 8.4 是否改变 cache event 顺序

不改变。

S9-F 不修改 cache lookup、materialization、eviction 或 event sink。

### 8.5 是否改变 materialization timing

不改变。

仍然使用现有 finish-time materialization。Progressive full-block materialization 留给 S9-G。

### 8.6 是否改变实例隔离

不改变。

每个 instance 仍独立 replay、独立 cache、独立 transfer queue、独立 state。

### 8.7 是否影响 true streaming 大 trace

轻微增加每个 active request 的两个 integer counters。

不新增 per-chunk timeline 明细存储。

不预读 future request。

streaming 主路径继续通过 typed metrics 输出 aggregate。

## 9. 测试计划

### 9.1 单测

新增 `tests/unit/replay/test_ttft_composer.py`：

1. legacy composition 保持旧口径。
   - `ttft_ms = finish - arrival`。
   - `scheduler_wait_ms = first_scheduled - arrival`。
   - `ttft_granularity = iteration`。

2. progressive composition 闭合。
   - compute wait + kv load wait + prefill compute + residual = observed TTFT。
   - scheduler wait = compute wait + kv load wait。
   - `ttft_granularity = chunk`。

3. residual 为 0 时不引入浮点噪声。

4. residual 为正时填入 `unattributed_ttft_ms`。

5. residual 为负时 fail-fast。

6. chunk/load counts 从 state 透传。

新增 `tests/unit/replay/test_chunk_level_ttft_composer.py`：

1. progressive multi-chunk request。
   - token budget 小于 miss tokens。
   - `chunk_count` 等于 prefill chunks 数。
   - `uncached_prefill_compute_ms` 等于 chunk compute contribution 之和。
   - `ttft_ms` 由 composition 闭合。

2. DDR-only load-only request。
   - `chunk_count == 0`。
   - `load_event_count == 1`。
   - `kv_load_wait_ms > 0`。

3. 同 iteration 两个 DDR-only request。
   - 第一个 request 出现 positive residual。
   - 第二个 request residual 为 0。
   - `ttft_ms` 总值仍等于 `finish - arrival`。

4. compute wait + KV load wait + prefill compute 组合。
   - 使用 S9-D 既有场景。
   - 验证 composition 字段和 `scheduler_wait_ms`。

5. list replay 与 streaming replay parity。
   - 相同 synthetic requests。
   - request metrics 和 iteration metrics 一致。

### 9.2 现有测试回归

建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_ttft_composer.py tests/unit/replay/test_chunk_level_ttft_composer.py tests/unit/replay/test_compute_wait_accounting.py tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_kv_transfer_queue.py tests/unit/replay/test_kv_transfer_queue_replay.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

### 9.3 是否需要 golden 更新

不需要更新 CSV golden。

理由：

- S9-F 不接 report/export。
- legacy mode 默认输出不变。
- progressive timeline mode 尚未成为 runner/report 默认入口。

### 9.4 质量检查

建议运行：

```bash
.venv/bin/ruff check src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/replay tests/unit/scheduler tests/unit/streaming
```

```bash
git diff --check
```

## 10. 风险与回滚边界

### 10.1 主要风险

1. TTFT 双重计费。
   - 通过 negative residual fail-fast 暴露问题。
   - 不允许 silently clamp。

2. `unattributed_ttft_ms` 被误解。
   - S9-F v1 中它是 blocking iteration replay 下的 residual。
   - 它是 replay 粒度残差和诊断项。
   - 不是真实硬件序列化时间，不是通信时间，也不是新的 latency backend。

3. `chunk_count` 与 `scheduled_iteration_count` 混淆。
   - `scheduled_iteration_count` 包含 load-only iteration。
   - `chunk_count` 只统计 prefill compute chunks。

4. `load_event_count` 被误解为真实 Mooncake transfer descriptor 数。
   - S9-F v1 是 request-level load event count。
   - 不是 block/page/layer/object 传输数量。

5. progressive mode 新字段影响旧测试。
   - old mode 必须保持 legacy defaults。
   - 相关回归必须覆盖。

### 10.2 回滚边界

如果 S9-F 出现问题，可以回滚：

- `src/infertwin/replay/ttft.py`。
- `RequestState` 新增 counters。
- `build_request_metrics()` 使用 composer 的改动。
- `build_iteration_metrics()` 的 `scheduled_chunk_count` 填充。
- 新增 S9-F 测试。

S9-B/S9-C/S9-D/S9-E 已完成的 schema、compute wait、KV load wait、transfer queue 不需要回滚。

## 11. 完成后如何判断可以进入 S9-G

满足以下条件后，可以进入 S9-G：

1. legacy mode 回归通过。
2. progressive mode request-level TTFT composition 闭合：

   ```text
   ttft_ms
   == compute_wait_ms
    + kv_load_wait_ms
    + uncached_prefill_compute_ms
    + unattributed_ttft_ms
   ```

3. progressive mode `scheduler_wait_ms == compute_wait_ms + kv_load_wait_ms`。
4. `ttft_ms == finish_time_ms - arrival_time_ms`。
5. `chunk_count` 只统计 prefill compute chunks。
6. `load_event_count` 只统计 request-level KV load events。
7. 同 iteration 多 DDR load 的 positive residual 被显式记录。
8. list replay 与 streaming replay 小 E2E 一致。
9. `cached_tokens`、HBM/DDR hit、miss tokens、cache events、materialization 均未变化。
10. 新增和相关测试通过。
11. `ruff check` 和 `git diff --check` 通过。
12. 本文档补充执行记录：
    - 做了什么。
    - 没有做什么。
    - 测试结果。
    - 风险和进入 S9-G 的判断。

## 12. 已审批的内容

以下设计点已由用户审批通过：

1. 接受 S9-F 属于核心仿真器，改动等级 L3。
2. 接受 S9-F 新增 replay internal `RequestTTFTComposer`，不作为 public report schema。
3. 接受 progressive mode 下 `ttft_ms` 由 composition 字段闭合，但总值仍等于
   `finish_time_ms - arrival_time_ms`。
4. 接受 S9-F v1 使用 `unattributed_ttft_ms` 承接当前 blocking iteration replay 下的
   positive residual；该字段是 replay 粒度残差和诊断项，不是物理建模结果。
5. 接受 negative residual fail-fast，不静默截断。
6. 接受 `chunk_count` 只统计 `scheduled_prefill_tokens > 0` 的 prefill compute chunks。
7. 接受 `load_event_count` 只统计 request-level KV load events，S9-F v1 不表达 block/page/layer
   transfer count。
8. 接受 progressive mode 下 `ttft_granularity = chunk`；legacy mode 保持
   `ttft_granularity = iteration`。
9. 接受 S9-F 将既有 `modeled_serialization_ms` placeholder 迁移为
   `unattributed_ttft_ms`，后续不再使用旧名表达 replay residual。
10. 接受 S9-F 不改变 scheduler token selection、cache lookup、hit/miss accounting、cache event
   顺序和 materialization timing。
11. 接受 S9-F 不接 CLI / runner / config / report/export。
12. 接受本 Batch 只修改计划列出的文件；如需越界修改，暂停并重新评审。
13. 接受测试范围：新增 composer 单测、replay composer 单测、S9-C/D/E 回归、Step8 回归、
    scheduler/request-state 回归、streaming parity、相关集成、ruff、`git diff --check`。

## 13. 执行记录

### 13.1 已完成内容

S9-F 已完成 Chunk-Level TTFT Composer 的最小实现：

1. 新增 `src/infertwin/replay/ttft.py`。
   - 定义 `RequestTTFTComposition`。
   - 定义 `RequestTTFTComposer`。
   - legacy mode 保持 iteration TTFT 口径。
   - progressive mode 使用 composition 闭合：

     ```text
     ttft_ms
       = compute_wait_ms
       + kv_load_wait_ms
       + uncached_prefill_compute_ms
       + unattributed_ttft_ms
     ```

   - negative residual fail-fast，不静默截断。

2. 将旧 placeholder `modeled_serialization_ms` 迁移为 `unattributed_ttft_ms`。
   - `RequestState`、`RequestTimelineSummary`、`BatchAwareRequestMetrics`、
     `IterationMetrics` 均使用新字段。
   - 活跃源码和测试中不再使用 `modeled_serialization_ms`。
   - `unattributed_ttft_ms` 明确为 replay 粒度残差和诊断项，不是物理建模结果。

3. 在 `RequestState` 中新增 request-local counters。
   - `chunk_count`：只统计 `scheduled_prefill_tokens > 0` 的 prefill compute chunks。
   - `load_event_count`：只统计 request-level KV load events。
   - load-only iteration 不计入 chunk。

4. 接入 request metrics builder。
   - `build_request_metrics()` 统一调用 `RequestTTFTComposer`。
   - progressive mode 下填充：
     - `ttft_granularity="chunk"`。
     - `chunk_count`。
     - `load_event_count`。
     - `unattributed_ttft_ms`。
   - legacy mode 保持：
     - `ttft_granularity="iteration"`。
     - `chunk_count=0`。
     - `load_event_count=0`。
     - `unattributed_ttft_ms=0`。

5. 接入 iteration metrics builder。
   - progressive mode 下填充 `scheduled_chunk_count`。
   - load-only slice 不计入 `scheduled_chunk_count`。
   - legacy mode 保持默认 `0`。

6. 补充测试。
   - 新增 `tests/unit/replay/test_ttft_composer.py`。
   - 新增 `tests/unit/replay/test_chunk_level_ttft_composer.py`。
   - 更新 timeline / S9-D / S9-E 相关断言。

### 13.2 没有完成的内容

S9-F 按计划没有实现以下能力：

- 不实现 progressive full-block materialization。
- 不改变 cache lookup。
- 不改变 HBM / DDR hit tokens。
- 不改变 miss tokens。
- 不改变 materialization timing。
- 不改变 eviction policy。
- 不改变 cache event 顺序。
- 不改变 scheduler token selection。
- 不实现真实 async load completion event。
- 不实现 same-request layerwise compute/load overlap。
- 不实现 DDR hit promotion。
- 不实现 decode / TPOT。
- 不实现 per-chunk timeline 明细输出。
- 不接 CLI / runner / config。
- 不接 report/export。
- 不接 Ramulator2 / Mooncake online replay。

### 13.3 对核心 replay 语义的影响

- `cached_tokens`：不改变。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`：不改变。
- `cache event` 顺序：不改变。
- `materialization timing`：不改变，仍是 finish-time materialization。
- `finish_time_ms`：不改变。
- `ttft_ms`：legacy mode 不改变；progressive mode 下总值仍等于
  `finish_time_ms - arrival_time_ms`，但组成字段现在闭合。
- `timeline metrics`：progressive mode 下新增真实 `chunk_count`、`load_event_count`、
  `scheduled_chunk_count` 和 `unattributed_ttft_ms`。
- `per-instance isolation`：不改变。
- `true streaming`：不预读 future request，不保存 per-chunk 明细。

### 13.4 测试结果

已运行并通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_ttft_composer.py tests/unit/replay/test_chunk_level_ttft_composer.py tests/unit/replay/test_timeline_schema.py tests/unit/replay/test_compute_wait_accounting.py tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_kv_transfer_queue.py tests/unit/replay/test_kv_transfer_queue_replay.py
```

结果：47 passed。

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py tests/unit/streaming/test_metrics.py tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

结果：35 passed。

```bash
.venv/bin/ruff check src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/replay tests/unit/scheduler tests/unit/streaming tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

结果：All checks passed。

```bash
git diff --check
```

结果：通过，无输出。

### 13.5 风险与边界

1. `unattributed_ttft_ms` 仍可能被误解。
   - 当前文档和测试已明确它是 replay 粒度残差和诊断项。
   - 它不是硬件、通信、DMA、Mooncake 或 HCCL 的真实耗时。

2. `chunk_count` 不是 `scheduled_iteration_count`。
   - `chunk_count` 只统计 prefill compute chunks。
   - DDR-only load-only iteration 不计入 chunk。

3. `load_event_count` 不是真实 transfer descriptor 数。
   - S9-F v1 是 request-level load event。
   - 不表达 block/page/layer/object 级传输数量。

4. S9-F 仍未实现 progressive full-block visibility。
   - 这部分留给 S9-G。

### 13.6 是否可以进入 S9-G

可以进入 S9-G。

判断依据：

- legacy mode 回归通过。
- progressive mode TTFT composition 闭合。
- `scheduler_wait_ms == compute_wait_ms + kv_load_wait_ms`。
- `ttft_ms == finish_time_ms - arrival_time_ms`。
- `chunk_count` / `load_event_count` 语义被测试覆盖。
- 同 iteration 多 DDR load 的 positive residual 被显式记录到
  `unattributed_ttft_ms`。
- list replay 与 streaming replay parity 通过。
- `cached_tokens`、HBM/DDR hit、miss tokens、cache events、materialization 未改变。
- `ruff check` 和 `git diff --check` 通过。
