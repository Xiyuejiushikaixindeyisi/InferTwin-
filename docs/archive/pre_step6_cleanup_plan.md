# Pre-Step6 Cleanup Code Plan

本文定义进入 Step6 前的遗留问题清扫方案。当前阶段只沉淀 P1/P2 代码方案，不进入代码修改。

本轮定位：

```text
Pre-Step6 cleanup
```

它不是 Step6 新功能，不实现 hit floor search，不引入 DDR/SSD，不改变 `batch_aware_hbm_lru` 的 frozen semantics。

## 0. Implementation Status

更新时间：2026-06-25

P1/P2 已完成：

- 新增 `src/hitfloor/scheduler/queue.py`。
- scheduler / replay 内部已统一使用 `WaitingQueue`，不保留 list fallback。
- scheduler 主 admission 路径不再使用 list `pop(0)`。
- 新增 `scripts/benchmark_replay.py`。
- benchmark script 只压测 HitFloor replay state machine，不模拟真实硬件。
- 大规模 benchmark 不进入默认 pytest，只保留小规模 smoke test。

验证结果：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 101 passed
python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml: passed
python scripts/benchmark_replay.py --requests 10000 --instances 4: passed
```

## 1. 背景

Step1-Step5 已完成核心离线 replay 仿真骨架。当前明确遗留问题包括：

- `waiting.pop(0)` 在大 waiting queue 下存在 O(n) 性能风险。
- 缺少轻量 benchmark harness，无法量化 replay 在 10k / 100k synthetic requests 下的表现。
- request build 一次性构造全部 `SimulationRequest`、多实例串行 replay、cache event 明细过大等问题需要 benchmark 后再决定是否进入后续阶段。

本轮只处理前两项：

- P1: waiting queue 性能清扫。
- P2: replay benchmark harness。

## 2. 非目标

本轮不做：

- hit floor search / P90 target sweep。
- DDR / SSD / multi-tier cache。
- KV load latency。
- gateway routing simulation。
- instance-side queueing policy simulation。
- external AIConfigurator / MkSim production adapter。
- cross-instance KV pooling。
- progressive block materialization。
- physical KV slot allocation、pinned/refcount。
- request streaming build。
- 多实例并行 replay。
- 改变 `cache_events.csv` 的默认标准输出地位。

## 3. 当前代码事实

### 3.1 waiting queue 使用点

当前相关文件：

```text
src/hitfloor/scheduler/vllm_like.py
src/hitfloor/replay/event_loop.py
tests/unit/scheduler/test_vllm_like_scheduler.py
tests/unit/replay/test_batch_aware_replay.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
```

当前性能风险：

```python
waiting.pop(0)
```

Python list 头部 pop 是 O(n)。当 waiting queue 很大且 scheduler 每轮只 admit 少量请求时，该成本会被放大。

### 3.2 为什么不能直接换成 `deque`

`event_loop.py` 的 bounded waiting lookup 不是单纯 FIFO pop：

- 需要从 waiting frontier 的逻辑头部开始扫描。
- 只对本轮 scheduler 可能考虑的请求做 cache lookup。
- 遇到 zero-miss request 时，需要从 waiting 中移除并 fast-finish。
- scan frontier 依赖 logical index。

如果直接把 list 改成 `deque`：

- `deque` 不适合按 index 扫描。
- middle remove 成本和语义会变得不透明。
- 很容易破坏 bounded waiting lookup 的保守策略。

因此本轮需要一个小而明确的 queue abstraction，而不是局部替换容器。

## 4. P1: Waiting Queue Abstraction

### 4.1 目标

新增 `WaitingQueue`，用清晰接口表达 replay/scheduler 对 waiting queue 的真实需求：

- FIFO append。
- O(1) logical head pop。
- logical index scan。
- logical index remove。
- deterministic iteration。

目标是消除 scheduler 主路径的 repeated `list.pop(0)`，同时保持现有 replay 语义不变。

### 4.2 新增文件

```text
src/hitfloor/scheduler/queue.py
tests/unit/scheduler/test_waiting_queue.py
```

### 4.3 `WaitingQueue` API

建议接口：

```python
class WaitingQueue:
    def __init__(self, states: Iterable[RequestState] = ()) -> None: ...

    def append(self, state: RequestState) -> None: ...

    def popleft(self) -> RequestState: ...

    def pop(self, index: int = 0) -> RequestState: ...

    def __len__(self) -> int: ...

    def __bool__(self) -> bool: ...

    def __iter__(self) -> Iterator[RequestState]: ...

    def __getitem__(self, index: int) -> RequestState: ...
```

内部实现建议：

```text
_items: list[RequestState]
_head: int
```

行为：

- `append()` 追加到 `_items` 尾部。
- `popleft()` 返回 `_items[_head]`，然后 `_head += 1`，避免 list 头部搬移。
- `pop(0)` 等价于 `popleft()`。
- `pop(index > 0)` 按 logical index 删除，允许 O(n)，因为它不是主性能路径。
- 当 `_head` 足够大时做 compact，释放已弹出前缀。

compact 建议条件：

```python
if self._head >= 64 and self._head * 2 >= len(self._items):
    self._items = self._items[self._head :]
    self._head = 0
```

这样能避免长期 replay 中 `_items` 持有大量已弹出对象。

### 4.4 修改文件

```text
src/hitfloor/scheduler/vllm_like.py
src/hitfloor/replay/event_loop.py
src/hitfloor/scheduler/__init__.py
tests/unit/scheduler/test_vllm_like_scheduler.py
tests/unit/replay/test_batch_aware_replay.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
tests/integration/test_step4_batch_aware_replay.py
tests/integration/test_step5_hbm_lru_e2e.py
```

### 4.5 代码改动方案

#### Scheduler

当前：

```python
request = waiting[0]
...
waiting.pop(0)
```

修改为：

```python
request = waiting[0]
...
waiting.popleft()
```

`schedule()` 签名建议改为：

```python
def schedule(
    *,
    instance_uuid: str,
    iteration_id: int,
    start_time_ms: float,
    waiting: WaitingQueue,
    running: list[RequestState],
) -> ScheduleResult:
    ...
```

不保留 list fallback。原因：

- `VllmLikeBatchScheduler` 是 HitFloor 内部 scheduler。
- 所有生产调用来自 `BatchAwareReplayEngine`。
- 测试可以同步改为 `WaitingQueue`。
- 过度兼容 list 会让 queue abstraction 的边界变模糊。

#### Replay Event Loop

当前：

```python
waiting: list[RequestState] = []
```

修改为：

```python
waiting = WaitingQueue()
```

受影响方法：

```text
_move_arrivals()
_prepare_scheduler_frontier()
_prepare_waiting_frontier()
```

这些方法的类型从 `list[RequestState]` 改为 `WaitingQueue`，逻辑顺序不变。

`_prepare_waiting_frontier()` 可以继续使用：

```python
index = 0
while index < len(waiting):
    state = waiting[index]
    ...
    waiting.pop(index)
```

其中 `waiting.pop(0)` 会走 O(1) `popleft()`。

### 4.6 语义不变量

P1 必须保持：

- FCFS admission 顺序不变。
- running requests 仍优先于 waiting requests。
- `max_num_batched_tokens` 语义不变。
- `max_num_seqs` 语义不变。
- bounded waiting lookup 不提前 lookup 整个 waiting queue。
- zero-miss fast-finish 行为不变。
- finish-time materialization 行为不变。
- request metrics 和 iteration metrics 对同一输入保持一致。

### 4.7 测试计划

新增：

```text
tests/unit/scheduler/test_waiting_queue.py
```

覆盖：

- empty queue `popleft()` / `pop()` 显式失败。
- append 后按 FIFO 迭代。
- `popleft()` 后 logical index 从新 head 开始。
- `pop(0)` 等价于 `popleft()`。
- `pop(index > 0)` 删除 logical middle item。
- 多次 `popleft()` 后 append 新元素仍保持顺序。

更新：

```text
tests/unit/scheduler/test_vllm_like_scheduler.py
```

覆盖：

- scheduler 使用 `WaitingQueue`。
- waiting 被 admit 后 queue 中只剩未 admit 请求。
- running 请求仍优先。
- token budget 和 seq budget 不变。

回归：

```text
tests/unit/replay/test_batch_aware_replay.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
tests/integration/test_step4_batch_aware_replay.py
tests/integration/test_step5_hbm_lru_e2e.py
```

重点确认：

- zero-miss fast-finish。
- lookup first schedule timing。
- materialization not visible in same iteration。
- finite HBM eviction。
- multi-instance isolation。

## 5. P2: Replay Benchmark Harness

### 5.1 目标

新增轻量 benchmark 脚本，用合成请求测量 replay 骨架在较大请求数下的表现。

它不是产品输出，不进入默认大规模 pytest，不依赖外部 simulator。

### 5.2 新增文件

```text
scripts/benchmark_replay.py
tests/integration/test_benchmark_replay_script.py
```

### 5.3 CLI 设计

建议命令：

```bash
PYTHONPATH=src python scripts/benchmark_replay.py \
  --requests 10000 \
  --instances 4 \
  --prompt-tokens 128 \
  --reuse-period 32 \
  --mode batch_aware_hbm_lru \
  --hbm-capacity-blocks 4096 \
  --max-num-batched-tokens 8192 \
  --max-num-seqs 32 \
  --cache-events off
```

参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--requests` | `10000` | synthetic request 数 |
| `--instances` | `1` | synthetic instance 数 |
| `--prompt-tokens` | `128` | 每条请求 prompt token 数 |
| `--reuse-period` | `32` | prompt pattern 周期，用于制造 prefix reuse |
| `--arrival-interval-ms` | `0.0` | 相邻请求到达间隔 |
| `--mode` | `batch_aware_infinite_hbm` | 支持 `batch_aware_infinite_hbm` / `batch_aware_hbm_lru` |
| `--hbm-capacity-blocks` | `4096` | finite HBM 模式容量 |
| `--block-size-tokens` | `16` | prefix block size |
| `--max-num-batched-tokens` | `8192` | scheduler token budget |
| `--max-num-seqs` | `32` | scheduler seq budget |
| `--cache-events` | `off` | `off` / `memory`，大规模默认 off |
| `--output-json` | optional | 可选写 JSON summary |

### 5.4 Synthetic Request 生成

生成逻辑建议放在脚本内的纯 helper：

```python
def build_synthetic_requests(config: BenchmarkConfig) -> list[SimulationRequest]:
    ...
```

请求字段：

- `request_id`: deterministic zero-padded string。
- `tenant_id`: `"tenant-a"`。
- `instance_uuid`: `f"instance-{index % instances}"`。
- `model`: `"glm-v5"`。
- `start_time_ms`: `index * arrival_interval_ms`。
- `prompt_blocks`: 由 `build_prefix_blocks()` 生成。

prompt token pattern：

```python
pattern_id = index % reuse_period
token_ids = [pattern_id * 1_000_000 + offset for offset in range(prompt_tokens)]
```

这样：

- `reuse_period = 1` 表示所有请求同 prompt。
- `reuse_period = requests` 接近全 unique prompt。
- 输出可复现。

### 5.5 Benchmark Metrics

输出 stdout summary：

```text
requests: 10000
instances: 4
mode: batch_aware_hbm_lru
build_ms: ...
replay_ms: ...
total_ms: ...
requests_per_second: ...
iterations: ...
p90_ttft_ms: ...
effective_hit_rate: ...
cache_events: ...
```

如果传入 `--output-json`，写稳定 JSON schema：

```json
{
  "request_count": 10000,
  "instance_count": 4,
  "mode": "batch_aware_hbm_lru",
  "build_ms": 123.0,
  "replay_ms": 456.0,
  "total_ms": 579.0,
  "requests_per_second": 17271.1,
  "iteration_count": 313,
  "p90_ttft_ms": 0.0,
  "effective_hit_rate": 0.75,
  "cache_event_count": 0
}
```

### 5.6 Latency Backend

使用 internal `FormulaLatencyBackend`，避免引入真实外部 simulator：

```text
iteration_fixed_overhead_ms = 0.0
iteration_prefill_token_ms = 0.01
iteration_batch_overhead_ms = 0.0
iteration_context_token_ms = 0.0
```

目的不是模拟真实硬件，只是压测 replay/scheduler/cache 状态机。

### 5.7 Cache Events

默认：

```text
--cache-events off
```

对应 `NullCacheEventSink`。

可选：

```text
--cache-events memory
```

对应 `InMemoryCacheEventSink`，只建议小规模使用，用于对比 event tracking overhead。

本轮不做 CSV benchmark writer，因为 Step5 runner 已覆盖 `CsvCacheEventWriter`，而 P2 目标是 replay 核心压测，不是文件 IO 压测。

### 5.8 测试计划

新增轻量 smoke test：

```text
tests/integration/test_benchmark_replay_script.py
```

测试内容：

- 以 `--requests 16` 跑通 infinite HBM benchmark。
- 以 `--requests 16 --mode batch_aware_hbm_lru` 跑通 finite HBM benchmark。
- 使用 `--output-json` 写入 JSON，并断言 schema 字段存在。

不在 pytest 中运行 10k / 100k benchmark。

大规模 benchmark 由人工命令触发：

```bash
PYTHONPATH=src python scripts/benchmark_replay.py --requests 10000 --instances 4
PYTHONPATH=src python scripts/benchmark_replay.py --requests 100000 --instances 8
```

## 6. 执行批次

### Batch P1-A: WaitingQueue schema and unit tests

实现：

- 新增 `src/hitfloor/scheduler/queue.py`。
- 新增 `tests/unit/scheduler/test_waiting_queue.py`。

验证：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff check src tests scripts
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/unit/scheduler/test_waiting_queue.py
```

### Batch P1-B: Scheduler and replay integration

实现：

- `VllmLikeBatchScheduler.schedule()` 使用 `WaitingQueue`。
- `BatchAwareReplayEngine` 内部 waiting 改为 `WaitingQueue`。
- 更新 scheduler / replay 测试。

验证：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/unit/scheduler tests/unit/replay
```

### Batch P2-A: Benchmark script

实现：

- 新增 `scripts/benchmark_replay.py`。
- 支持 stdout summary 和 optional JSON output。

验证：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/python scripts/benchmark_replay.py --requests 1000 --instances 4
```

### Batch P2-B: Benchmark smoke tests and final validation

实现：

- 新增 `tests/integration/test_benchmark_replay_script.py`。
- 更新 `docs/development_status.md` 和 `docs/global_memory.md` 的遗留清扫状态。

验证：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff format --check src tests scripts
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff check src tests scripts
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml
TMPDIR=/tmp PYTHONPATH=src .venv/bin/python scripts/benchmark_replay.py --requests 10000 --instances 4
```

## 7. 风险与应对

### 7.1 WaitingQueue 改动影响 replay 语义

风险：

- bounded waiting lookup、zero-miss fast-finish 和 scheduler admission 顺序可能被无意改变。

应对：

- 保持 `_prepare_waiting_frontier()` 的 logical index scan。
- 使用现有 replay tests 作为语义回归。
- 新增 queue unit tests 锁定 logical index 行为。

### 7.2 Queue abstraction 过度设计

风险：

- 为一个 `pop(0)` 引入过复杂的数据结构。

应对：

- `WaitingQueue` 只提供当前 replay/scheduler 需要的最小 API。
- 不引入 priority、tenant、policy、routing 等未来概念。
- 不让 `WaitingQueue` 承担 cache lookup 或 scheduler decision。

### 7.3 Benchmark 被误解为性能承诺

风险：

- synthetic benchmark 结果被误读为真实线上性能。

应对：

- 脚本文档和输出说明：这是 replay state-machine benchmark，不是硬件性能仿真。
- 使用 FormulaLatencyBackend，只用于压测 HitFloor 内部调度/cache逻辑。

### 7.4 大规模 benchmark 写太多 cache events

风险：

- memory sink 在大规模下消耗内存。

应对：

- 默认 `--cache-events off`。
- `--cache-events memory` 文档标记为小规模对比用途。

## 8. 验收标准

代码验收：

- Scheduler 不再在主 admission 路径使用 list `pop(0)`。
- `WaitingQueue` API 小而明确。
- `batch_aware_infinite_hbm` 与 `batch_aware_hbm_lru` 现有语义不变。
- Benchmark script 可生成 deterministic synthetic workload。
- Benchmark script 可输出 stable JSON summary。

测试验收：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: passed
package CLI E2E: passed
benchmark 10k synthetic requests: passed
```

文档验收：

- `docs/development_status.md` 更新 Pre-Step6 清扫状态。
- `docs/global_memory.md` 更新 WaitingQueue 和 benchmark harness 状态。
- 遗留问题列表中标明哪些已清扫、哪些仍需后续 benchmark / 产品决策。

## 9. 审批点

请重点 review：

1. 是否接受新增 `src/hitfloor/scheduler/queue.py`。
2. 是否接受 scheduler/replay 内部统一使用 `WaitingQueue`，不保留 list fallback。
3. 是否接受 `pop(index > 0)` 保持 O(n)，因为主性能路径是 `popleft()`。
4. 是否接受 benchmark script 只压测 HitFloor replay state machine，不模拟真实硬件。
5. 是否接受大规模 benchmark 不进入默认 pytest，只保留小规模 smoke test。

审批通过后，再进入 P1/P2 代码修改。
