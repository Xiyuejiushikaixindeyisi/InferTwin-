# S9-G Implementation Plan: Progressive Full-Block Materialization

状态：已审批通过，已开发完成。

本文件是 S9-G 的代码编写方案与执行记录。

## 1. Batch 定位

本 Batch 属于核心仿真器开发。

改动等级：L3。

原因：

- S9-G 会改变 progressive timeline mode 下 miss blocks 的可见时间。
- 旧 `batch_aware_hbm_lru` / `batch_aware_hbm_ddr_lru` 必须继续保持 finish-time
  materialization。
- progressive mode 下，chunk finish 后 newly completed full blocks 可以进入 cache，
  后续 request lookup 可能产生更高 prefix hit。
- 本 Batch 会影响 progressive mode 下的 cache event timestamp、materialization event reason、
  eviction timing、hit/miss metrics 和 typed materialization counters。

## 2. 本 Batch 做什么

S9-G 实现 progressive full-block materialization。

核心目标：

```text
finish-time mode:
  request finish -> materialize all miss blocks

progressive timeline mode:
  each scheduled prefill chunk finish
    -> materialize newly completed full miss blocks
    -> later scheduler-boundary lookup can hit these blocks
```

具体做：

1. 新增 `ProgressiveFullBlockMaterializationPolicy`。
   - 只在 progressive timeline mode 默认启用。
   - chunk finish 后 materialize newly completed full blocks。
   - partial block 不 materialize，不可见。
   - final chunk 仍走同一 progressive chunk materialization path。

2. 保持 finish-time policy 稳定。
   - legacy mode 默认仍使用 `FinishTimeMaterializationPolicy`。
   - legacy mode 不调用 progressive chunk materialization。
   - 旧测试和 golden 默认不变。

3. 扩展 materialization policy 接口。
   - 增加 scheduled chunk materialization 方法。
   - 增加 result schema，返回本轮实际 progressive materialized blocks/tokens。
   - progressive mode 如果用户显式传入不支持 progressive chunk 的 policy，应 fail-fast。

4. 扩展 cache materialize / store reason。
   - HBM materialize event reason 区分：

     ```text
     finish_time_materialization
     progressive_chunk_materialization
     ```

   - DDR store event reason 区分：

     ```text
     finish_time_store
     progressive_chunk_store
     ```

   - 不新增 `CacheEvent` 字段，只复用既有 `reason` 字段。

5. 在 request / iteration typed metrics 中填充 progressive materialization counters。
   - request:
     - `progressive_materialized_blocks`
     - `progressive_materialized_tokens`
   - iteration:
     - `progressive_materialized_blocks`
     - `progressive_materialized_tokens`

6. 保持 list replay 与 streaming replay 行为一致。
   - streaming path 使用同一 replay event loop。
   - 不保存 per-chunk timeline 明细。
   - 不预读 future request。

## 3. 本 Batch 不做什么

S9-G 不做：

- 不实现 partial-block hit。
- 不实现 physical KV slot / refcount / pin。
- 不实现 DDR hit promotion。
- 不实现 async store completion event。
- 不实现 load completion event dump。
- 不改变 KV load timing state。
- 不改变 KV transfer queue。
- 不改变 scheduler token selection。
- 不改变 latency backend。
- 不改变 request build / tokenizer / prefix hash。
- 不接 CLI / runner / config。
- 不接 report/export。
- 不接 Ramulator2 / Mooncake online replay。
- 不实现 cross-instance pooling。
- 不实现 Decode / TPOT。

边界说明：

- S9-G 只改变 progressive timeline mode 的 materialization timing。
- S9-G 不改变 old mode 默认行为。
- Progressive materialization 的可见时间是 scheduler iteration boundary：chunk 在 iteration
  finish 时 materialize，下一轮 lookup 可以看到；同一 iteration 内已经完成 lookup 的 request 不会
  retroactively 变成 hit。
- request 在某个 iteration 中途到达时，当前 event loop 仍在下一个 scheduler boundary 才消费它；
  因此它看到的是该 boundary 前已经 materialized 的 blocks。

如果实现时发现必须修改 `src/infertwin/latency/**`、`src/infertwin/report/**`、
`src/infertwin/cli/**`、`src/infertwin/config/**`、`src/infertwin/external/**` 或
`scripts/**`，应暂停并重新评审。

## 4. 计划新增/修改的文件

### 4.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `tests/unit/cache/test_progressive_materialization_policy.py` | 单测 progressive policy 的 full-block selection、partial block 不可见、duplicate guard 和 result counters。 |
| `tests/unit/replay/test_progressive_full_block_materialization.py` | 覆盖 replay 中 chunk finish 后 block 可见、old mode 不变、cache event reason、metrics counters、list/streaming parity。 |
| `docs/step9/s9_g_progressive_full_block_materialization_implementation_plan.md` | 本文件；开发后补充执行记录、测试结果和进入 S9-H 的判断。 |

### 4.2 修改文件

| 文件 | 职责 |
| --- | --- |
| `src/infertwin/cache/materialization.py` | 新增 `ProgressiveFullBlockMaterializationPolicy`、`MaterializationResult`、scheduled chunk materialization API；保留 finish-time policy。 |
| `src/infertwin/cache/base.py` | 给 `PrefixCache.materialize()` 增加可选 `reason` 参数，默认保持 `finish_time_materialization`。 |
| `src/infertwin/cache/hbm_lru.py` | 支持 materialize reason；materialize event reason 可区分 finish-time 和 progressive。 |
| `src/infertwin/cache/infinite_hbm.py` | 同步 `materialize()` 签名，忽略 reason。 |
| `src/infertwin/cache/ddr_lru.py` | 给 `store()` 增加可选 `reason` 参数，默认保持 `finish_time_store`。 |
| `src/infertwin/cache/tiered.py` | `materialize()` 将 reason 传给 HBM，并把 progressive reason 映射为 DDR store reason；保持 stable event order。 |
| `src/infertwin/scheduler/state.py` | 新增 effective block size、progressive materialized counters、已 materialized block key set；不参与 scheduler selection。 |
| `src/infertwin/replay/event_loop.py` | 在 scheduled prefill chunk finish 后调用 progressive materialization；finish branch 避免重复 materialize；iteration metrics 汇总 counters。 |
| `src/infertwin/replay/metrics.py` | 在 request / iteration metrics 中填充 progressive materialized blocks/tokens。 |
| `tests/unit/replay/test_batch_aware_replay_hbm_lru.py` | 保持 finish-time materialization old-mode 回归；更新 recording policy 以兼容扩展接口。 |
| `tests/unit/cache/test_hbm_lru_cache.py` | 增加或调整 materialize reason 断言。 |
| `tests/unit/cache/test_tiered_prefix_cache.py` | 增加 progressive materialize 写 HBM + DDR 且 event reason 稳定的断言。 |
| `tests/unit/replay/test_chunk_level_ttft_composer.py` | 如 S9-G 默认 progressive policy 影响已有 progressive tests，补充 effective block size 或断言 counters。 |
| `tests/unit/replay/test_kv_load_timing_state.py` | 如 S9-G 默认 progressive policy 影响已有 progressive tests，补充 effective block size 或保持旧断言稳定。 |
| `tests/unit/replay/test_kv_transfer_queue_replay.py` | 如 S9-G 默认 progressive policy 影响已有 progressive tests，补充 effective block size 或保持旧断言稳定。 |
| `tests/unit/streaming/test_streaming_replay.py` | 如专门 replay parity 未覆盖 streaming path，则增加小规模 progressive parity。 |

### 4.3 禁止修改文件

S9-G 禁止修改：

- `src/infertwin/latency/**`
- `src/infertwin/report/**`
- `src/infertwin/cli/**`
- `src/infertwin/config/**`
- `src/infertwin/external/**`
- `scripts/**`
- `configs/**`

例外：

- 如发现 report/export 因字段重命名失败，应暂停并重新评审，不在 S9-G 顺手修。
- 如发现 runner/config 需要暴露 progressive mode，应留给 S9-H。

## 5. 每个文件的职责

### 5.1 `src/infertwin/cache/materialization.py`

只负责决定“哪些 computed miss blocks 在什么时候进入 cache 可见状态”。

不负责：

- cache backend 内部存储。
- eviction victim selection。
- scheduler selection。
- latency estimation。
- report/export。

建议新增：

```python
@dataclass(frozen=True, slots=True)
class MaterializationResult:
    materialized_blocks: tuple[PrefixBlock, ...] = ()

    @property
    def block_count(self) -> int: ...

    @property
    def token_count(self) -> int: ...
```

扩展 policy interface：

```python
class MaterializationPolicy(Protocol):
    name: str
    supports_progressive_chunks: bool

    def materialize_scheduled_chunk(...) -> MaterializationResult: ...

    def materialize_finished_request(...) -> MaterializationResult: ...
```

`FinishTimeMaterializationPolicy`：

- `supports_progressive_chunks = False`。
- `materialize_scheduled_chunk()` 返回 empty result。
- `materialize_finished_request()` 保持现有行为，materialize 全部 blocks。

`ProgressiveFullBlockMaterializationPolicy`：

- `supports_progressive_chunks = True`。
- `materialize_scheduled_chunk()` 选择 newly completed full blocks。
- `materialize_finished_request()` 不重复 materialize 已 progressive materialized blocks。
  第一版可以只 materialize remaining full blocks 或 no-op；推荐实现为“materialize remaining full
  blocks”，保证如果某个 request 因边界条件跳过 chunk hook，finish 时仍能补齐 full blocks。
- partial blocks 永远不 materialize。

### 5.2 Full-block selection algorithm

输入：

```text
materialization_blocks: tuple[PrefixBlock, ...]  # lookup miss blocks
prompt_blocks: tuple[PrefixBlock, ...]           # full prompt block chain
effective_block_size: int
computed_tokens_before: int
computed_tokens_after: int
already_materialized_block_keys: frozenset[str]
```

选择规则：

```text
for block in materialization_blocks:
  block_end_token = cumulative end token in prompt_blocks

  if block.block_key in already_materialized_block_keys:
      skip
  if block.token_count != effective_block_size:
      skip  # partial block is not visible
  if computed_tokens_before < block_end_token <= computed_tokens_after:
      materialize
```

finish 补齐规则：

```text
for block in materialization_blocks:
  if block already materialized:
      skip
  if block.token_count != effective_block_size:
      skip
  if block_end_token <= prompt_tokens:
      materialize
```

说明：

- `effective_block_size` 必须来自 request build / block conversion / model runtime profile。
- 如果 progressive mode 下无法得到 positive `effective_block_size`，应 fail-fast。
- 不根据 `block_index * block_size` 推导 span；应基于 `prompt_blocks` 的 token_count 累积计算，
  避免 partial block 和 future variable group block 产生错误。

### 5.3 `src/infertwin/cache/base.py`

扩展协议：

```python
def materialize(
    self,
    blocks: tuple[PrefixBlock, ...],
    now_ms: float,
    request_id: str = "",
    instance_uuid: str = "",
    reason: str = "finish_time_materialization",
) -> None: ...
```

默认值保持旧行为。

### 5.4 Cache backends

#### HBM

`HBMCache.materialize()` 增加 `reason` 参数并写入 event。

默认：

```text
finish_time_materialization
```

Progressive：

```text
progressive_chunk_materialization
```

#### DDR

`DDRLRUCache.store()` 增加 `reason` 参数并写入 event。

默认：

```text
finish_time_store
```

Progressive：

```text
progressive_chunk_store
```

#### TieredPrefixCache

`TieredPrefixCache.materialize()`：

1. 先写 HBM。
2. drain HBM events。
3. 再写 DDR。
4. drain DDR events。

事件顺序保持：

```text
HBM materialize events
DDR store events
```

### 5.5 `src/infertwin/scheduler/state.py`

新增 fields：

```python
effective_block_size: int = 0
progressive_materialized_blocks: int = 0
progressive_materialized_tokens: int = 0
progressive_materialized_block_keys: set[str] = field(default_factory=set)
```

建议方法：

```python
def record_progressive_materialization(
    self,
    blocks: tuple[PrefixBlock, ...],
) -> None:
    ...
```

职责边界：

- 这些字段只用于 materialization tracking 和 typed metrics。
- 不参与 scheduler selection。
- 不改变 `num_computed_tokens`。
- 不改变 hit/miss accounting。

### 5.6 `src/infertwin/replay/event_loop.py`

构造默认 policy：

```text
timeline_mode == legacy:
  default policy = FinishTimeMaterializationPolicy

timeline_mode == progressive:
  default policy = ProgressiveFullBlockMaterializationPolicy
```

如果用户显式传入 materialization policy：

- legacy mode：接受旧 policy。
- progressive mode：policy 必须 `supports_progressive_chunks=True`，否则 fail-fast。

scheduled slice 应用阶段建议流程：

```text
for scheduled_slice in schedule_result.shape.request_slices:
  state = states_by_id[request_id]
  lookup_state = lookup_by_id[request_id]

  record kv load timing
  record latency contribution

  if scheduled_prefill_tokens > 0:
      state.timeline_state = RUNNING_CHUNK
      state.apply_scheduled_tokens(...)
      if progressive mode:
          result = materialization_policy.materialize_scheduled_chunk(
              cache=cache,
              materialization_blocks=lookup_state.materialization_blocks,
              prompt_blocks=state.prompt_blocks,
              effective_block_size=state.effective_block_size,
              computed_tokens_before=scheduled_slice.computed_tokens_before,
              computed_tokens_after=scheduled_slice.computed_tokens_after,
              chunk_finish_time_ms=finish_ms,
              request_id=state.request_id,
              instance_uuid=state.instance_uuid,
              already_materialized_block_keys=frozenset(
                  state.progressive_materialized_block_keys
              ),
          )
          state.record_progressive_materialization(result.materialized_blocks)

  else:
      state.apply_load_only_iteration(...)

  if state.status == FINISHED:
      finish_result = materialization_policy.materialize_finished_request(...)
      state.record_progressive_materialization(finish_result.materialized_blocks)
      build request metrics
```

重要约束：

- old mode 不调用 scheduled chunk materialization。
- progressive mode 下 final chunk 先走 chunk materialization，再进入 finish branch。
- progressive policy 的 finish branch 不应重复 materialize chunk 已 materialized blocks。
- event sink drain timing 保持现有结构：iteration apply 后 drain，下一轮 lookup 前 cache 已可见。

### 5.7 `src/infertwin/replay/metrics.py`

`build_request_metrics()` 填入：

```python
progressive_materialized_blocks=state.progressive_materialized_blocks
progressive_materialized_tokens=state.progressive_materialized_tokens
```

`build_iteration_metrics()` 增加参数：

```python
progressive_materialized_blocks: int = 0
progressive_materialized_tokens: int = 0
```

并填入 `IterationMetrics`。

### 5.8 Test fixture updates

S9-G 会让 progressive mode 默认启用 progressive policy。因此已有 progressive tests 如果手工构造
`SimulationRequest`，需要保证 effective block size 可用。

推荐：

- 对测试中需要 progressive materialization 的 request，显式设置：
  - `effective_block_size`
  - `block_conversion_result`
- 对只关注 wait / TTFT composition 的 tests，如果 cache 是 `_LookupMapCache` 且不支持 reason 参数，
  可以更新 mock `materialize(..., reason: str = "finish_time_materialization")`。

## 6. 新增或修改的数据结构 / schema / interface

### 6.1 Materialization result

新增：

```python
MaterializationResult
```

这是 core replay internal result，不是 report schema。

### 6.2 Materialization policy interface

新增：

```python
supports_progressive_chunks: bool
materialize_scheduled_chunk(...)
```

这是 core replay policy interface。

### 6.3 Cache materialize reason

`PrefixCache.materialize()` 增加可选 `reason` 参数。

该参数只影响 `CacheEvent.reason`，不改变 cache storage semantics。

### 6.4 RequestState

新增：

```python
effective_block_size
progressive_materialized_blocks
progressive_materialized_tokens
progressive_materialized_block_keys
```

### 6.5 Typed metrics

开始填充既有字段：

- `BatchAwareRequestMetrics.progressive_materialized_blocks`
- `BatchAwareRequestMetrics.progressive_materialized_tokens`
- `IterationMetrics.progressive_materialized_blocks`
- `IterationMetrics.progressive_materialized_tokens`

不新增 report/export 字段。

## 7. 核心算法逻辑

### 7.1 Boundary visibility

对于一个 request：

```text
chunk_0 start=0, finish=4, computed_tokens_after=4
chunk_1 start=4, finish=8, computed_tokens_after=8
```

当 `effective_block_size=4`：

```text
finish=4:
  block_0 visible

finish=8:
  block_1 visible
```

后续 request 如果在 `t=4` 的 scheduler boundary 之后 lookup，可以 hit block_0。

同一 iteration 内已经 lookup 过的 request 不回溯修改 hit/miss。

### 7.2 Partial block

Prompt 6 tokens，block size 4：

```text
block_0 token_count=4
block_1 token_count=2
```

Progressive materialization:

```text
block_0 visible after computed_tokens_after >= 4
block_1 never materialized
```

原因：

- vLLM prefix cache 不计 partial-block prefix hit。
- Materializing partial block would pollute logical capacity and distort eviction.

### 7.3 Duplicate guard

同一个 request 的某个 block 一旦通过 progressive policy materialized：

```text
block_key in state.progressive_materialized_block_keys
```

后续 chunks / finish branch 不再重复 materialize。

如果 block 后续被 cache eviction，S9-G v1 不重新 materialize 该 block。

### 7.4 Event reason

Finish-time:

```text
HBM: materialize, reason=finish_time_materialization
DDR: store, reason=finish_time_store
```

Progressive:

```text
HBM: materialize, reason=progressive_chunk_materialization
DDR: store, reason=progressive_chunk_store
```

### 7.5 Interaction with capacity / eviction

Progressive materialization happens earlier, so eviction can also happen earlier.

This is intended for the progressive mode.

Old mode capacity / eviction timing remains unchanged.

## 8. 对核心 replay 语义的影响

### 8.1 是否改变 `cached_tokens`

Old mode：不改变。

Progressive mode：可能改变后续 request 的 effective cached tokens。

原因：

- lookup accounting 规则不变。
- 但 cache 中可见 blocks 的时间提前。
- 后续 request lookup 可能从 miss 变为 HBM/DDR hit。

### 8.2 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens`

Old mode：不改变。

Progressive mode：可能改变。

典型场景：

```text
r1 long prompt chunk_0 finish at t=4
r2 same prompt arrives at t=4
r1 not finished until t=8

finish-time mode:
  r2 misses all

progressive mode:
  r2 hits block_0
```

### 8.3 是否改变 `finish_time` / `ttft_ms`

Old mode：不改变。

Progressive mode：可能改变后续 requests 的 `finish_time_ms` / `ttft_ms`。

原因：

- 后续 request 的 miss tokens 可能减少。
- Scheduler shape 和 latency backend input may change.

### 8.4 是否改变 cache event 顺序

Old mode：不改变。

Progressive mode：改变。

变化：

- materialize/store events may appear at chunk finish time.
- eviction may happen earlier.
- reason 区分 progressive 与 finish-time。

### 8.5 是否改变 materialization timing

Old mode：不改变。

Progressive mode：改变，这是本 Batch 的核心目标。

### 8.6 是否改变实例隔离

不改变。

每个 instance 仍有独立 cache、scheduler、transfer queue 和 materialization state。

### 8.7 是否影响 true streaming 大 trace

轻微增加 active request state：

- materialized block key set。
- progressive materialization counters。

不增加 per-chunk timeline dump。

不预读 future request。

Streaming path 通过同一 event loop 保持一致。

## 9. 测试计划

### 9.1 单测

新增 `tests/unit/cache/test_progressive_materialization_policy.py`：

1. chunk finish materializes newly completed full block。
2. chunk finish 不 materialize partial block。
3. final finish 不重复 materialize already progressive materialized block。
4. duplicate guard 防止同 request 重复 materialize。
5. missing / non-positive effective block size fail-fast。
6. policy name 和 `supports_progressive_chunks` 稳定。

新增 `tests/unit/replay/test_progressive_full_block_materialization.py`：

1. old mode keeps finish-time behavior。
   - 同一个长 request 未 finish 前，后续 request 不 hit。

2. progressive mode makes full block visible after chunk finish。
   - r1 长 prompt，chunk size 1 block。
   - r2 在 r1 chunk_0 finish boundary 到达。
   - r2 hit block_0。

3. partial block remains invisible。
   - prompt length 6, block size 4。
   - second request only sees block_0; partial block 不计 hit。

4. progressive event reason。
   - HBM reason includes `progressive_chunk_materialization`。
   - Tiered cache DDR reason includes `progressive_chunk_store`。

5. request / iteration metrics counters。
   - `progressive_materialized_blocks`。
   - `progressive_materialized_tokens`。

6. list replay / streaming replay parity。

7. multi-instance isolation。
   - instance-a progressive materialization 不影响 instance-b。

### 9.2 Existing regression

建议运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/cache/test_progressive_materialization_policy.py tests/unit/replay/test_progressive_full_block_materialization.py tests/unit/cache/test_materialization_policy.py tests/unit/cache/test_hbm_lru_cache.py tests/unit/cache/test_tiered_prefix_cache.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_ttft_composer.py tests/unit/replay/test_chunk_level_ttft_composer.py tests/unit/replay/test_compute_wait_accounting.py tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_kv_transfer_queue.py tests/unit/replay/test_kv_transfer_queue_replay.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py tests/unit/streaming/test_metrics.py
```

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

### 9.3 是否需要 golden 更新

Old-mode golden 不应更新。

理由：

- S9-G 不改变 legacy default。
- S9-G 不接 runner/report。
- Progressive mode 仍未成为 report/export 默认入口。

如果新增 progressive golden，应作为新测试文件，不修改 old-mode golden expectation。

### 9.4 质量检查

建议运行：

```bash
.venv/bin/ruff check src/infertwin/cache src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/cache tests/unit/replay tests/unit/scheduler tests/unit/streaming
```

```bash
git diff --check
```

## 10. 风险与回滚边界

### 10.1 主要风险

1. Progressive mode accidentally changes old mode。
   - 通过 default policy selection 和 old-mode regression 控制。

2. Partial block 被错误 materialize。
   - 通过 effective block size 和 full-block selection tests 控制。

3. Duplicate materialization distorts capacity / eviction。
   - 通过 request-local materialized block key set 控制。

4. Cache event reason 不清晰。
   - 通过 reason 字段区分 progressive / finish-time。

5. Effective block size 缺失。
   - Progressive mode 应 fail-fast，提示 request build / block conversion 缺少 effective
     block size。

6. Existing test mocks signature mismatch。
   - 更新 mock `materialize()` 签名接受 reason 默认参数。

7. Progressive materialization may increase memory of active request state。
   - 只保存 active request 的 materialized block keys。
   - request finished 后 streaming path 会释放 state。

### 10.2 回滚边界

如果 S9-G 出现问题，可以回滚：

- `ProgressiveFullBlockMaterializationPolicy`。
- Materialization policy interface extensions。
- cache materialize/store reason optional params。
- `RequestState` progressive materialization fields。
- event loop chunk materialization hook。
- metrics counters。
- S9-G tests。

S9-B/S9-C/S9-D/S9-E/S9-F 已完成的 timeline schema、wait accounting、transfer queue 和
TTFT composer 不需要回滚。

## 11. 完成后如何判断可以进入 S9-H

满足以下条件后，可以进入 S9-H：

1. old mode finish-time materialization 回归通过。
2. progressive mode 下 chunk finish 后 full block 可见。
3. partial block 仍不可见。
4. duplicate materialization 不发生。
5. event reason 能区分 finish-time 与 progressive。
6. request / iteration metrics 正确填充 progressive materialization counters。
7. list replay 与 streaming replay parity 通过。
8. 多实例 progressive materialization 互相隔离。
9. `cached_tokens` accounting 规则本身未变，只改变 progressive mode 下的 cache visibility timing。
10. 新增和相关测试通过。
11. `ruff check` 和 `git diff --check` 通过。
12. 本文档补充执行记录：
    - 做了什么。
    - 没有做什么。
    - 测试结果。
    - 风险和进入 S9-H 的判断。

## 12. 需要用户审批的内容

请审批以下设计点：

1. 接受 S9-G 属于核心仿真器，改动等级 L3。
2. 接受 S9-G 新增 `ProgressiveFullBlockMaterializationPolicy`。
3. 接受 legacy mode 默认继续使用 `FinishTimeMaterializationPolicy`。
4. 接受 progressive timeline mode 默认使用 `ProgressiveFullBlockMaterializationPolicy`。
5. 接受 progressive mode 下，chunk finish 后只 materialize newly completed full miss blocks。
6. 接受 partial blocks 在 S9-G progressive mode 下不 materialize、不可见。
7. 接受 progressive mode 缺少 positive effective block size 时 fail-fast。
8. 接受 `PrefixCache.materialize()` 增加可选 `reason` 参数，默认保持旧行为。
9. 接受 HBM / DDR event reason 区分 finish-time 与 progressive。
10. 接受 progressive mode 下 hit/miss、finish_time、ttft、eviction timing 可能变化。
11. 接受 old mode hit/miss、finish_time、ttft、cache event 顺序必须不变。
12. 接受 S9-G 不接 CLI / runner / config / report/export。
13. 接受本 Batch 只修改计划列出的文件；如需越界修改，暂停并重新评审。
14. 接受测试范围：progressive policy 单测、progressive replay 单测、cache backend 回归、
    S9-C/D/E/F 回归、Step8/scheduler/streaming 回归、相关集成、ruff、`git diff --check`。

## 13. 执行记录

状态：已开发，待用户 review。

### 13.1 已完成

1. 新增 `ProgressiveFullBlockMaterializationPolicy`。
   - progressive timeline mode 默认使用该 policy。
   - legacy mode 默认仍使用 `FinishTimeMaterializationPolicy`。
   - progressive mode 显式传入不支持 chunk materialization 的 policy 时 fail-fast。

2. 扩展 materialization policy interface。
   - 新增 `MaterializationResult`。
   - 新增 `supports_progressive_chunks`。
   - 新增 `materialize_scheduled_chunk()`。
   - `materialize_finished_request()` 保持旧参数可用，并支持 progressive finish fallback
     需要的可选参数。

3. 扩展 cache materialize/store reason。
   - `PrefixCache.materialize()` 新增可选 `reason`，默认 `finish_time_materialization`。
   - HBM progressive reason 为 `progressive_chunk_materialization`。
   - DDR progressive store reason 为 `progressive_chunk_store`。
   - `TieredPrefixCache` 保持 HBM events 在前、DDR events 在后的稳定顺序。

4. 接入 replay event loop。
   - progressive mode 在 scheduled prefill chunk finish 后 materialize newly completed
     full miss blocks。
   - final chunk 仍走 chunk materialization path。
   - finish branch 通过 request-local block key set 避免重复计数。
   - old mode 不调用 scheduled chunk materialization。

5. 接入 typed metrics。
   - `BatchAwareRequestMetrics.progressive_materialized_blocks/tokens` 开始填充真实值。
   - `IterationMetrics.progressive_materialized_blocks/tokens` 开始填充真实值。

6. 补充测试。
   - 新增 progressive materialization policy 单测。
   - 新增 progressive replay 小 E2E。
   - 补充 HBM / DDR / Tiered cache reason 回归。
   - 更新受影响的 progressive 手工 request fixtures，补齐 `effective_block_size`。

7. 修复开发中暴露的一个 scheduler guard。
   - 当 running request 已耗尽 token budget，且队首 waiting request 尚未 lookup 时，
     scheduler 不再读取该 waiting request 的 load-only 状态。
   - 该请求本轮本来不会被调度；修复只避免未 lookup 状态被误访问，不改变有效
     token selection。

### 13.2 未完成

S9-G 未实现以下能力：

- partial-block materialization / hit。
- physical KV slot、refcount、pin。
- DDR hit promotion。
- async store completion / load completion event。
- KV load timing / transfer queue 的新语义。
- CLI / runner / config / report/export 接入。
- Ramulator2 / Mooncake online replay。
- Decode / TPOT。

### 13.3 对 replay 语义的实际影响

- legacy mode：保持 finish-time materialization；old-mode golden 未更新。
- progressive mode：full miss block 在 chunk finish 后可见；后续 scheduler-boundary lookup
  可能命中更早 materialized 的 HBM/DDR block。
- `cached_tokens` accounting 规则未改变；改变的是 progressive mode 下 cache visibility timing。
- materialization timing 改变只发生在 progressive timeline mode。
- per-instance cache、scheduler、transfer queue、materialization state 仍隔离。
- true streaming 复用同一 event loop；没有新增 per-chunk dump，也没有预读 future request。

### 13.4 测试结果

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/cache/test_progressive_materialization_policy.py tests/unit/replay/test_progressive_full_block_materialization.py tests/unit/cache/test_materialization_policy.py tests/unit/cache/test_hbm_lru_cache.py tests/unit/cache/test_ddr_lru_cache.py tests/unit/cache/test_tiered_prefix_cache.py
```

结果：44 passed。

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/replay/test_ttft_composer.py tests/unit/replay/test_chunk_level_ttft_composer.py tests/unit/replay/test_compute_wait_accounting.py tests/unit/replay/test_kv_load_timing_state.py tests/unit/replay/test_kv_transfer_queue.py tests/unit/replay/test_kv_transfer_queue_replay.py tests/unit/replay/test_step8_kv_load_replay.py tests/unit/replay/test_step8_latency_contribution_metrics.py tests/unit/scheduler/test_request_state_kv_load.py tests/unit/scheduler/test_vllm_like_scheduler.py tests/unit/streaming/test_streaming_replay.py tests/unit/streaming/test_metrics.py
```

结果：68 passed。

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py tests/integration/test_streaming_runtime_integration.py tests/integration/test_step7_streaming_hbm_ddr_integration.py
```

结果：6 passed。

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/golden/test_batch_aware_hbm_lru_golden.py tests/unit/report/test_cache_event_writer.py tests/unit/report/test_sweep_summary.py tests/unit/experiment/test_sweep_metrics.py tests/integration/test_true_streaming_capacity_sweep_runner.py
```

结果：19 passed。

```bash
.venv/bin/ruff check src/infertwin/cache src/infertwin/replay src/infertwin/scheduler src/infertwin/streaming tests/unit/cache tests/unit/replay tests/unit/scheduler tests/unit/streaming tests/integration/test_step8_streaming_kv_load_e2e.py tests/integration/test_step4_batch_aware_replay.py
```

结果：All checks passed。

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

结果：431 passed。

```bash
git diff --check
```

结果：通过，无输出。

### 13.5 风险与进入下一 Batch 判断

当前主要风险：

- progressive mode 下 cache visibility timing 变早，命中率和 TTFT 变化是预期行为；
  后续 review 需要重点确认测试覆盖的边界是否足够。
- request-local materialized block key set 会随 active request 持有；streaming 完成 request 后会释放，
  但超长 active request 仍会有少量额外内存。
- S9-G 仍然不建 partial-block hit，不建真实 KV slot/refcount/pin，与真实 vLLM 的物理存储仍有差异。

判断：

- S9-G 已满足进入下一 Batch 的工程条件。
- 建议进入下一步前先进行一次代码 review，重点看 progressive visibility boundary、
  partial block 不可见、event reason、old-mode golden 未变化、以及 scheduler guard 是否符合预期。
