# S9-E Implementation Plan: KV Transfer Queue / Shared Link v1

状态：已审批通过，已开发完成。

本文件是 S9-E 的代码编写方案和执行记录。

## 1. Batch 定位

本 Batch 属于核心仿真器开发。

改动等级：L3。

原因：

- S9-E 会在 progressive timeline mode 下引入 instance-local KV transfer queue state。
- KV transfer queue 会影响 `kv_load_wait_ms`、`scheduler_wait_ms` 和 iteration-level
  queue depth typed metrics。
- 本 Batch 不改变 cache lookup、hit/miss accounting、materialization 或 scheduler token
  selection，但会修改 replay event loop 的 latency timeline accounting。
- legacy mode 必须保持 Step8 / S9-D 行为不变。

## 2. 本 Batch 做什么

S9-E 实现一个最小、确定性的 `shared_link_fifo_v1`。

具体做：

1. 新增 instance-local FIFO transfer queue。
   - 每个 instance replay 拥有独立 `SharedLinkFIFOTransferQueue`。
   - transfer request 按 replay 中的 deterministic scheduled slice 顺序入队。
   - 同一 ready time 下，按 scheduler slice order 排队。

2. 将 S9-D 的 `kv_load_wait_ms = attributed kv_load_ms` 升级为：

   ```text
   kv_load_wait_ms = queue_wait_ms + transfer_ms
   ```

   其中：

   - `transfer_ms` 来自 Step8 / S9-D 已有 `split_iteration_latency_contributions()`。
   - `queue_wait_ms` 由 `SharedLinkFIFOTransferQueue` 根据 ready time 和 shared-link
     availability 计算。

3. 输出 iteration-level transfer queue metrics。
   - `kv_transfer_queue_depth_max`
   - `waiting_for_kv_load_count`
   - `kv_load_wait_ms`

4. 保持 request-level `kv_load_ms` 兼容字段。
   - `kv_load_ms` 仍表示本 request 被归因到的 transfer service time。
   - `kv_load_wait_ms` 表示 progressive timeline 下 request 在 KV transfer 上的 elapsed wait：

     ```text
     finish_time_of_transfer - ready_time
     ```

5. 保持 list replay 与 streaming replay 行为一致。
   - list replay 和 streaming replay 都在 instance scope 内创建 transfer queue。
   - 不预读 future request。

## 3. 本 Batch 不做什么

S9-E 不做：

- 不模拟真实 RDMA / DMA / HCCL / HIXL / CPU copy 协议。
- 不模拟 Mooncake TransferEngine 的线程池、priority、retry、placement 或 replica。
- 不模拟跨实例 pooling。
- 不实现 load completion event dump。
- 不把 request 从 running set 拆成真正异步 waiting-for-load set。
- 不实现 compute 与 KV load 的真实 overlap。
- 不实现 backpressure。
- 不改变 scheduler token selection。
- 不改变 cache lookup。
- 不改变 HBM / DDR hit tokens。
- 不改变 materialization timing。
- 不实现 progressive materialization。
- 不实现 chunk-level TTFT composer。
- 不接入 CLI / runner / config。
- 不修改 report/export。
- 不接入 online Ramulator2 / Mooncake replay。

边界说明：

- S9-E v1 的 queue 是 deterministic accounting model，不是真实 TransferEngine simulator。
- S9-E v1 仍保持 current blocking iteration replay：不把 load pending request 暂停后让其他
  request 继续 compute。
- S9-E v1 不改变 `finish_time_ms` / `ttft_ms` 总口径；完整 TTFT composer 和 residual
  accounting 留给 S9-F。

如果实现时发现必须修改 `src/infertwin/cache/**`、`src/infertwin/latency/**`、
`src/infertwin/report/**`、`src/infertwin/cli/**`、`src/infertwin/config/**` 或
`src/infertwin/external/**`，应暂停并重新评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/replay/kv_transfer.py` | 定义 `SharedLinkFIFOTransferQueue`、transfer request/result schema 和 deterministic FIFO queue 算法。 |
| `tests/unit/replay/test_kv_transfer_queue.py` | 单测 FIFO queue 的时间计算、queue depth、非负校验和 deterministic tie-break。 |
| `tests/unit/replay/test_kv_transfer_queue_replay.py` | 覆盖 replay 中 progressive mode 的 shared-link queue accounting、legacy 兼容和 list/streaming parity。 |

### 4.2 修改文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/scheduler/state.py` | 如需记录 request-level `load_event_count`，新增轻量字段和方法；否则只复用现有 `kv_load_wait_ms`。 |
| `src/infertwin/replay/event_loop.py` | 在 instance scope 创建 transfer queue；将 S9-D 的 KV load timing helper 改为通过 queue submit 计算 wait/depth。 |
| `src/infertwin/replay/metrics.py` | 在 `build_iteration_metrics()` 中接收并填充 `kv_transfer_queue_depth_max`；如新增 request `load_event_count`，同步填充 request metrics。 |
| `src/infertwin/streaming/replay.py` | 在 streaming instance scope 创建同类型 transfer queue，并传入 inherited event loop helper。 |
| `tests/unit/replay/test_kv_load_timing_state.py` | 调整或新增断言，确认 S9-D 语义在单 load request 下仍等价。 |
| `tests/unit/streaming/test_streaming_replay.py` | 可增加或保留小规模 parity；若 replay 专测已覆盖，可不改。 |
| `docs/step9/s9_e_kv_transfer_queue_shared_link_v1_implementation_plan.md` | 本文件；开发后补充执行记录、测试结果和进入 S9-F 的判断。 |

### 4.3 禁止修改文件

S9-E 禁止修改：

- `src/infertwin/cache/**`
- `src/infertwin/latency/**`
- `src/infertwin/report/**`
- `src/infertwin/cli/**`
- `src/infertwin/config/**`
- `src/infertwin/external/**`
- `scripts/**`
- `configs/**`

例外：

- 如果实现发现不需要修改 `tests/unit/streaming/test_streaming_replay.py`，则不改。
- 如果不新增 request-level `load_event_count` state，则 `scheduler/state.py` 只保留已有
  S9-D 字段，不做额外修改。

## 5. 每个文件的职责

### 5.1 `src/infertwin/replay/kv_transfer.py`

只负责 KV transfer queue 纯算法。

不负责：

- scheduler selection。
- cache lookup。
- latency backend 估算。
- materialization。
- report/export。

建议数据结构：

```python
@dataclass(frozen=True, slots=True)
class KVTransferRequest:
    request_id: str
    instance_uuid: str
    ready_time_ms: float
    transfer_ms: float
    kv_load_tokens: int = 0
    kv_load_bytes: int = 0
```

```python
@dataclass(frozen=True, slots=True)
class KVTransferResult:
    request_id: str
    instance_uuid: str
    ready_time_ms: float
    start_time_ms: float
    finish_time_ms: float
    transfer_ms: float
    queue_wait_ms: float
    elapsed_ms: float
    queue_depth_before: int
    queue_depth_after: int
```

```python
class SharedLinkFIFOTransferQueue:
    def submit(self, request: KVTransferRequest) -> KVTransferResult:
        ...
```

字段语义：

- `ready_time_ms`：request 已确认需要 KV load 且可以提交 transfer 的时间。S9-E v1 使用
  scheduler iteration start time。
- `transfer_ms`：该 request 被归因到的 KV load service time。
- `queue_wait_ms = start_time_ms - ready_time_ms`。
- `elapsed_ms = finish_time_ms - ready_time_ms`。
- `queue_depth_before`：submit 前已有多少未完成 transfer 排在该 request 前面。
- `queue_depth_after`：submit 后 shared link 中未完成 transfer 数量。

算法要求：

- 所有时间和 token/byte 字段非负。
- request id / instance uuid 非空。
- 每次 submit 前，先 prune `finish_time_ms <= ready_time_ms` 的已完成 transfer。
- `start_time_ms = max(ready_time_ms, next_available_time_ms)`。
- `finish_time_ms = start_time_ms + transfer_ms`。
- `next_available_time_ms = finish_time_ms`。
- 同 ready time 的顺序由 caller 的 submit order 决定。

### 5.2 `src/infertwin/replay/event_loop.py`

S9-E 在 instance replay scope 创建 transfer queue。

建议 `_run_instance()`：

```python
transfer_queue = SharedLinkFIFOTransferQueue(instance_uuid=instance_uuid)
```

然后传入 `_apply_schedule_result()`：

```python
self._apply_schedule_result(
    ...,
    transfer_queue=transfer_queue,
)
```

S9-E 修改 S9-D helper：

```python
def _record_scheduled_kv_load_timing(
    *,
    state: RequestState,
    scheduled_slice: ScheduledSlice,
    kv_load_ms: float,
    timeline_mode: str,
    transfer_queue: SharedLinkFIFOTransferQueue,
    ready_time_ms: float,
) -> KVLoadTimingAccounting:
    ...
```

progressive mode 行为：

1. 如果 scheduled slice 没有 KV load，返回 zero accounting。
2. 构造 `KVTransferRequest`：

   ```text
   ready_time_ms = schedule_result.shape.start_time_ms
   transfer_ms = contribution.kv_load_ms
   kv_load_tokens = scheduled_slice.kv_load_tokens
   kv_load_bytes = scheduled_slice.kv_load_bytes
   ```

3. submit 到 instance-local queue。
4. `state.record_kv_load_wait(result.elapsed_ms)`。
5. iteration accounting：

   ```text
   waiting_for_kv_load_count += 1
   kv_load_wait_ms += result.elapsed_ms
   kv_transfer_queue_depth_max = max(depth_max, result.queue_depth_after)
   ```

legacy mode 行为：

- 不 submit queue。
- 不修改 `kv_load_wait_ms`。
- 不修改 `kv_transfer_queue_depth_max`。

### 5.3 `src/infertwin/replay/metrics.py`

修改 `build_iteration_metrics()`，增加默认参数：

```python
kv_transfer_queue_depth_max: int = 0
```

填入 `IterationMetrics.kv_transfer_queue_depth_max`。

如果 S9-E 新增 request-level `load_event_count`：

- 在 `RequestState` 中记录。
- 在 `build_request_metrics()` 中填入 `load_event_count=state.load_event_count`。

若暂不新增 `load_event_count` state，则保留默认 `0`，后续 S9-F timeline composer 再统一接入。

推荐选择：

- S9-E 可以新增 `load_event_count`，因为 transfer queue submit 就是一个明确 load event。
- 但不新增 per-load 明细，避免大 trace 内存压力。

### 5.4 `src/infertwin/streaming/replay.py`

streaming replay 需要与 list replay 创建同样的 instance-local queue。

建议：

```python
transfer_queue = SharedLinkFIFOTransferQueue(instance_uuid=instance_uuid)
```

并传入 inherited `_apply_schedule_result()`。

要求：

- 不预读 future request。
- 不保存 per-transfer detail。
- list replay 和 streaming replay 的 request / iteration metrics 一致。

### 5.5 测试文件

新增 `tests/unit/replay/test_kv_transfer_queue.py`，只测试 queue 纯算法。

新增 `tests/unit/replay/test_kv_transfer_queue_replay.py`，测试 replay 接入。

## 6. 新增或修改的数据结构 / schema / interface

### 6.1 新增 internal queue schema

新增：

- `KVTransferRequest`
- `KVTransferResult`
- `SharedLinkFIFOTransferQueue`

这些类型属于 replay internal helper，不作为 public report schema。

### 6.2 Event loop internal accounting

扩展 S9-D 已有 `KVLoadTimingAccounting`：

```python
@dataclass(frozen=True, slots=True)
class KVLoadTimingAccounting:
    waiting_for_kv_load_count: int = 0
    kv_load_wait_ms: float = 0.0
    kv_transfer_queue_depth_max: int = 0
```

### 6.3 IterationMetrics builder

`build_iteration_metrics()` 增加可选 keyword-only 参数：

```python
kv_transfer_queue_depth_max: int = 0
```

`IterationMetrics` 字段已存在：

```python
kv_transfer_queue_depth_max: int = 0
```

### 6.4 Replay engine public interface

不新增 public engine 参数。

继续复用：

```python
timeline_mode: str = LEGACY_TIMELINE_MODE
```

S9-E 不接 config，因此不新增 `transfer_queue_mode` public parameter。

## 7. 核心算法逻辑

### 7.1 transfer queue submit

对 progressive mode 下每个 scheduled KV load slice：

```text
transfer_request.ready_time_ms = iteration_start_ms
transfer_request.transfer_ms = per_request_kv_load_ms
transfer_request.kv_load_tokens = scheduled_slice.kv_load_tokens
transfer_request.kv_load_bytes = scheduled_slice.kv_load_bytes

result = shared_link_fifo.submit(transfer_request)
```

queue 计算：

```text
queue_depth_before = unfinished_transfers_at_ready_time
start_time_ms = max(ready_time_ms, next_available_time_ms)
finish_time_ms = start_time_ms + transfer_ms
queue_wait_ms = start_time_ms - ready_time_ms
elapsed_ms = finish_time_ms - ready_time_ms
queue_depth_after = queue_depth_before + 1
```

request accounting：

```text
state.kv_load_wait_ms += result.elapsed_ms
```

iteration accounting：

```text
iteration.kv_load_wait_ms += result.elapsed_ms
iteration.kv_transfer_queue_depth_max = max(queue_depth_after)
```

### 7.2 与 Step8 / S9-D latency 的关系

`transfer_ms` 的来源不变：

```text
transfer_ms = split_iteration_latency_contributions(...)[request_id].kv_load_ms
```

因此：

- `kv_load_ms` 仍表示 service time。
- `kv_load_wait_ms` 表示 service time + queue wait。
- 对同一 iteration 多个 DDR load，后面的 request 可能拥有更大的 `kv_load_wait_ms`。

### 7.3 是否改变 iteration duration

S9-E v1 不改变 `latency.duration_ms`、`finish_ms` 或 `ttft_ms` 总口径。

原因：

- 当前 event loop 仍是 blocking iteration replay。
- 当前 Step8 latency 已经把同一 iteration 内 shared-link service time 加到 duration 中。
- S9-E 先把 queue wait 作为 request-level timeline accounting 暴露出来。
- 完整 request-level TTFT composer、residual accounting、load/compute overlap 留给 S9-F。

约束：

- S9-E 不应让 `transfer_queue` 的 `finish_time_ms` 超出当前 iteration `finish_ms` 之后再静默忽略。
- 如果出现 `transfer_finish_time_ms > iteration_finish_ms + epsilon`，应 fail-fast 或在方案内明确
  暂不支持；按当前 blocking semantics 和 `shared_link_sum`，正常不会发生。

### 7.4 同一 iteration 多个 load 的例子

两个 DDR-only request 同时进入 load-only iteration：

```text
r1 transfer_ms = 2
r2 transfer_ms = 2
ready_time = 0
```

FIFO result：

```text
r1: start=0, finish=2, queue_wait=0, elapsed=2, depth_after=1
r2: start=2, finish=4, queue_wait=2, elapsed=4, depth_after=2
```

Iteration metrics：

```text
kv_load_ms = 4              # service time total, Step8 compatible
kv_load_wait_ms = 6         # sum of request elapsed waits
kv_transfer_queue_depth_max = 2
```

注意：

- `kv_load_wait_ms` 是 request wait aggregate，允许大于 iteration service time。
- S9-F 会负责完整 TTFT composition / residual 表达。

### 7.5 多实例隔离

每个 instance 有自己的 transfer queue：

```text
instance-a queue
instance-b queue
```

不同 instance 的 transfer 不互相排队。

## 8. 对核心 replay 语义的影响

### 8.1 是否改变 `cached_tokens`

不改变。

S9-E 不修改 prefix lookup、block conversion、CP / DCP / PCP / MTP accounting。

### 8.2 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`

不改变。

HBM / DDR hit 统计仍由 cache lookup 和 `LookupMetrics.from_result()` 决定。

### 8.3 是否改变 `finish_time` / `ttft_ms`

legacy mode：不改变。

progressive mode：S9-E v1 不改变 `finish_time_ms` 和 `ttft_ms` 总口径。

会改变 progressive mode 下的拆分字段：

```text
kv_load_wait_ms
scheduler_wait_ms
kv_transfer_queue_depth_max
```

### 8.4 是否改变 cache event 顺序

不改变。

S9-E 不修改 cache lookup、materialization、eviction 或 event sink。

### 8.5 是否改变 materialization timing

不改变。

仍然使用现有 finish-time materialization。

### 8.6 是否改变实例隔离

增强实例隔离表达，但不改变实例隔离边界。

每个 instance 独立维护 transfer queue。不同实例之间不共享 link state。

### 8.7 是否影响 true streaming 大 trace

有轻微状态增加，但不改变 streaming 主路径。

- 每个 active instance 增加一个 transfer queue。
- Queue 只保留未完成 transfer finish times，按 ready time prune。
- 不保存 per-transfer 明细到 result。
- 不预读 future request。

## 9. 测试计划

### 9.1 单测

新增 `tests/unit/replay/test_kv_transfer_queue.py`：

1. 单个 transfer 无排队。
   - ready=0, transfer=2 -> start=0, finish=2, elapsed=2, depth_after=1。

2. 同 ready time FIFO。
   - 两个 request ready=0。
   - 第二个 request queue_wait 等于第一个 transfer_ms。
   - depth max deterministic。

3. 已完成 transfer 会被 prune。
   - r1 finish=2。
   - r2 ready=10。
   - r2 queue_wait=0。

4. 非负校验。
   - negative ready / transfer / tokens / bytes fail-fast。

5. deterministic tie-break。
   - 同一 ready time 下 submit order 决定结果。

新增 `tests/unit/replay/test_kv_transfer_queue_replay.py`：

1. legacy mode no-op。
   - `kv_transfer_queue_depth_max == 0`。
   - S9-D / Step8 行为不变。

2. progressive mode 下单 DDR hit。
   - `kv_load_wait_ms == kv_load_ms`。
   - `kv_transfer_queue_depth_max == 1`。

3. progressive mode 下同 iteration 两个 DDR load。
   - 后一个 request 的 `kv_load_wait_ms` 大于自身 `kv_load_ms`。
   - iteration `kv_transfer_queue_depth_max == 2`。
   - hit / miss tokens 不变。
   - `finish_time_ms` / `ttft_ms` 总口径不变。

4. 多实例隔离。
   - instance-a 和 instance-b 同时发生 DDR load。
   - 各自 depth max 为 1，不互相排队。

5. streaming parity。
   - 相同 synthetic DDR-hit requests。
   - list replay metrics == streaming replay metrics。

### 9.2 集成测试

建议运行现有相关集成：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

目的：

- 确认 Step8 legacy KV load E2E 不被 S9-E 破坏。
- 确认基础 batch-aware replay 不回退。

### 9.3 小 E2E

S9-E 小 E2E 放在 unit/integration 边界均可。建议作为 unit-level replay E2E：

```text
two DDR-only zero-miss requests
same ready time
same instance
same ServingLatencyProfile
progressive timeline mode
```

验证：

- request-level `kv_load_wait_ms` FIFO 排队。
- iteration-level `kv_transfer_queue_depth_max`。
- list / streaming parity。

### 9.4 是否需要 golden 更新

不需要更新 CSV golden。

理由：

- S9-E 不接 report/export。
- legacy mode 默认输出不变。
- progressive timeline mode 尚未成为 runner/report 默认入口。

### 9.5 建议运行命令

开发完成后建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_kv_transfer_queue.py tests/unit/replay/test_kv_transfer_queue_replay.py tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_compute_wait_accounting.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py
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

1. queue 被误解为真实 Mooncake / TransferEngine。
   - S9-E 是 deterministic abstraction。
   - 不模拟 protocol、priority、thread pool、placement、replica。

2. `kv_load_ms` 与 `kv_load_wait_ms` 语义混淆。
   - `kv_load_ms` 是 service time。
   - `kv_load_wait_ms` 是 request elapsed wait，包括 queue wait。

3. request-level wait aggregate 可能大于 iteration service time。
   - 这是 queue wait aggregate 的正常现象。
   - 需要在测试和文档中明确。

4. TTFT 分解仍未闭合。
   - `ttft_ms` 仍来自 `finish_time_ms - arrival_time_ms`。
   - S9-E 不做 final composer。
   - S9-F 需要处理 residual / modeled serialization。

5. old mode regression。
   - legacy mode 不应 submit transfer queue。
   - Step8 KV load tests 必须继续通过。

### 10.2 回滚边界

如果 S9-E 出现问题，可以回滚：

- `src/infertwin/replay/kv_transfer.py`。
- event loop 中传入/使用 transfer queue 的逻辑。
- `build_iteration_metrics()` 的 `kv_transfer_queue_depth_max` 参数。
- 新增 S9-E 测试。

S9-B/S9-C/S9-D 已新增的 schema、compute wait 和 KV load timing accounting 不需要回滚。

## 11. 完成后如何判断可以进入 S9-F

满足以下条件后，可以进入 S9-F：

1. legacy mode 回归通过。
2. progressive mode 下单 DDR load 与 S9-D 结果等价。
3. progressive mode 下同 iteration 多个 DDR load 能体现 FIFO queue wait。
4. iteration metrics 输出 `kv_transfer_queue_depth_max`。
5. 多实例 transfer queue 互相隔离。
6. list replay 与 streaming replay 小 E2E 一致。
7. `cached_tokens`、HBM/DDR hit、miss tokens、cache events、materialization 均未变化。
8. 新增和相关测试通过。
9. `ruff check` 和 `git diff --check` 通过。
10. 本文档补充执行记录：
    - 做了什么。
    - 没有做什么。
    - 测试结果。
    - 风险和进入 S9-F 的判断。

## 12. 已审批的内容

以下设计点已由用户审批通过：

1. 接受 S9-E 属于核心仿真器，改动等级 L3。
2. 接受 S9-E 新增 replay internal `SharedLinkFIFOTransferQueue`，不作为 public report schema。
3. 接受 S9-E 的 transfer queue 是 deterministic accounting abstraction，不模拟真实 Mooncake / TransferEngine。
4. 接受 S9-E v1 不改变 scheduler token selection，不拆出真正异步 waiting-for-load set。
5. 接受 S9-E v1 不改变 `finish_time_ms` / `ttft_ms` 总口径，只改变 progressive mode 下的 timeline wait/depth 字段。
6. 接受 `kv_load_ms` 表示 service time，`kv_load_wait_ms` 表示 queue wait + service time。
7. 接受 iteration-level `kv_load_wait_ms` 是 request wait aggregate，允许大于 iteration `kv_load_ms`。
8. 接受每个 instance 独立维护 transfer queue，不做跨实例共享链路。
9. 接受 S9-E 不接 CLI / runner / config / report/export。
10. 接受本 Batch 只修改计划列出的文件；如需越界修改，暂停并重新评审。
11. 接受测试范围：新增 queue 单测、replay queue 单测、S9-D/S9-C/Step8 回归、scheduler/request-state 回归、streaming parity、相关集成、ruff、`git diff --check`。

## 13. 执行记录

### 13.1 已完成内容

S9-E 已完成 `shared_link_fifo_v1` 的最小实现：

1. 新增 `src/infertwin/replay/kv_transfer.py`。
   - 定义 `KVTransferRequest`、`KVTransferResult` 和
     `SharedLinkFIFOTransferQueue`。
   - Queue 是 instance-local FIFO accounting model。
   - 同一 ready time 下按 caller submit order 排队。
   - submit 前 prune 已完成 transfer。
   - 只保存未完成 transfer 的 finish time，不保存 per-transfer 明细。

2. 接入 list replay。
   - `BatchAwareReplayEngine._run_instance()` 在 instance scope 创建独立
     transfer queue。
   - progressive mode 下，scheduled KV load slice 会 submit 到该 instance queue。
   - `state.kv_load_wait_ms` 从 S9-D 的 `kv_load_ms` 升级为
     `queue_wait_ms + transfer_ms`。
   - iteration metric 填充 `kv_transfer_queue_depth_max`。

3. 接入 streaming replay。
   - `StreamingBatchAwareReplayEngine.run_instance_stream()` 同样在 instance scope
     创建 `SharedLinkFIFOTransferQueue`。
   - list replay 和 streaming replay 使用同一 `_apply_schedule_result()` 逻辑。

4. 扩展 typed metrics builder。
   - `build_iteration_metrics()` 新增 keyword-only 参数
     `kv_transfer_queue_depth_max`，默认值为 `0`。
   - 未新增 public config、CLI、runner 或 report/export 入口。

5. 补充测试。
   - 新增 `tests/unit/replay/test_kv_transfer_queue.py`。
   - 新增 `tests/unit/replay/test_kv_transfer_queue_replay.py`。

### 13.2 没有完成的内容

S9-E 按计划没有实现以下能力：

- 不模拟真实 Mooncake / TransferEngine 的协议、线程、优先级、placement、replica
  或 retry。
- 不接 Ramulator2、Mooncake online replay 或外部 calibration harness。
- 不引入 public `transfer_queue_mode` 配置。
- 不改变 scheduler token selection。
- 不拆出真正异步 waiting-for-load set。
- 不改变 cache lookup、materialization、eviction 或 cache event 顺序。
- 不改变 `finish_time_ms` / `ttft_ms` 总口径。
- 不做 load completion event dump。
- 不接 report/export。

### 13.3 对核心 replay 语义的影响

- `cached_tokens`：不改变。
- `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`：不改变。
- `cache event` 顺序：不改变。
- `materialization timing`：不改变，仍是 finish-time materialization。
- `finish_time_ms` / `ttft_ms`：不改变。
- `timeline metrics`：progressive mode 下改变
  `kv_load_wait_ms`、`scheduler_wait_ms` 和 `kv_transfer_queue_depth_max`。
- `per-instance isolation`：保持并增强表达，每个 instance 独立维护 transfer queue。
- `true streaming`：保持 streaming 主路径，不预读 future request。

### 13.4 测试结果

已运行并通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_kv_transfer_queue.py tests/unit/replay/test_kv_transfer_queue_replay.py tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_compute_wait_accounting.py
```

结果：24 passed。

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

结果：32 passed。

```bash
.venv/bin/ruff check src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/replay tests/unit/scheduler tests/unit/streaming tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

结果：All checks passed。

```bash
git diff --check
```

结果：通过，无输出。

### 13.5 风险与边界

1. `SharedLinkFIFOTransferQueue` 仍是 deterministic accounting abstraction。
   它不是 Mooncake TransferEngine，也不表达真实 RDMA / HCCL / HIXL 行为。

2. `kv_load_ms` 与 `kv_load_wait_ms` 的语义必须继续区分。
   - `kv_load_ms` 是 request 被归因到的 transfer service time。
   - `kv_load_wait_ms` 是 queue wait + service time。

3. iteration-level `kv_load_wait_ms` 是 request elapsed wait aggregate。
   因此它可以大于 iteration-level `kv_load_ms`。

4. S9-E 仍未完成 Step9 的 TTFT composer 闭合。
   这部分留给 S9-F，用于处理 residual / modeled serialization / chunk-level
   timeline 的最终表达。

### 13.6 是否可以进入 S9-F

可以进入 S9-F。

判断依据：

- legacy mode 保持 no-op。
- progressive mode 下单 DDR load 与 S9-D 等价。
- progressive mode 下同 iteration 多 DDR load 能体现 FIFO queue wait。
- list replay 与 streaming replay parity 通过。
- 多实例 transfer queue 隔离通过。
- Step8、S9-C、S9-D 相关回归通过。
- `ruff check` 和 `git diff --check` 通过。
