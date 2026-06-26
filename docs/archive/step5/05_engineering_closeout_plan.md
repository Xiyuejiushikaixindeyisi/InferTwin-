# Step5 Engineering Close-Out Code Development Plan

本文根据 `docs/reviews/step1_step5_core_simulator_review.md` 的评审结果，给出 Step5 工程收口代码开发方案。

当前阶段只沉淀方案，不进入代码实现。经用户 review 通过后，再按本方案分批修改代码。

## 1. 收口目标

Step1-Step5 已经完成 HitFloor 核心仿真骨架：

- 现网风格 CSV 输入。
- request parser。
- tokenizer / chat template registry。
- hash-only prefix blocks。
- 固定路由、多实例隔离 replay。
- vLLM-like continuous batching / chunked prefill。
- fitted TTFT backend。
- 无限 HBM prefix cache。
- 有限 HBM LRU cache。
- streaming `cache_events.csv`。
- stateful eviction policy。
- runner / report / synthetic E2E。

本轮工程收口目标是让这套骨架更适合交给同事使用和继续扩展：

- package CLI 真正可用。
- lint / format 基线可作为后续 CI gate。
- 输入 schema guard 更严格。
- 结果对象不暴露可变内部状态。
- 当前未完成能力不再被配置或文案误导为已实现。
- stub / scaffold 模块状态更清楚。

## 2. 不在本轮实现

本轮不是 Step6，也不扩展产品能力。

明确不做：

- hit floor search / P90 target sweep。
- DDR / SSD / multi-tier cache。
- KV load latency。
- gateway routing simulation。
- instance-side queueing policy simulation。
- external AIConfigurator / MkSim production adapter。
- cross-instance KV pooling。
- progressive block materialization。
- physical KV slot allocation。
- pinned / refcount。
- 大规模性能重构。

这些能力应在后续阶段单独做产品形态、技术路线和代码开发讨论。

## 3. 设计原则

本轮修改遵循以下原则：

- 不改变 `batch_aware_hbm_lru` 的 frozen semantics。
- 不把 hit floor search 硬塞进现有 `ExperimentRunner`。
- 不把 scripts 作为唯一正式入口，package CLI 必须可运行。
- 不为了兼容未知输入而增加隐式 fallback。
- 不把 formatting-only diff 和逻辑修改混在同一个 batch。
- 不强行拆分当前核心状态机文件；只处理明确评审问题。

## 4. 待处理评审项

### 4.1 P1: package CLI 仍是占位实现

现状：

- `pyproject.toml` 注册了 `hitfloor = "hitfloor.cli.main:main"`。
- `src/hitfloor/cli/main.py` 的 `simulate` / `validate-trace` 仍是占位输出。
- 当前真实入口是 `scripts/run_simulation.py` 和 `scripts/validate_trace.py`。

影响：

- 同事通过 package 命令运行 `hitfloor simulate --config ...` 时不会真正执行仿真。
- CLI 与 scripts 行为分裂，后续维护成本增加。

修改方向：

- 将真实逻辑沉到 `src/hitfloor/cli/main.py`。
- `scripts/*.py` 保留为薄 wrapper，调用 package CLI 或 package-level function。
- 给 CLI 增加 unit / integration tests。

### 4.2 P1: hit floor search 尚未实现，但配置中出现未来字段

现状：

- `configs/experiments/default.yaml` 中存在 `targets.p90_ttft_ms`、`cache.hbm_capacity_gb`、`cache.ddr_capacity_gb`、`output.hit_floor_table` 等未来产品字段。
- 当前 runner 只做单次 replay，不做 target sweep。

影响：

- 用户可能误以为当前版本已经输出 hit floor table。

修改方向：

- 当前 runnable config 只保留已实现字段。
- 将未来字段移入 future template 或文档说明。
- 不在本轮实现 hit floor search。

### 4.3 P1: ruff / format 基线未建立

现状：

```text
ruff check: 4 issues
ruff format --check: 65 files would be reformatted
pytest: 74 passed
```

已知 `ruff check` 问题：

- `scripts/run_simulation.py`: `E402`
- `scripts/validate_trace.py`: `E402`
- `src/hitfloor/request/chat_template.py`: `F401`

修改方向：

- 先做 formatting-only。
- 再修复 `ruff check`。
- 后续把 `ruff check`、`ruff format --check`、`pytest` 作为基本验证命令。

### 4.4 P2: request parser schema guard 偏宽

现状：

- `model` 缺失时会被转换为 `""`。
- `messages` / `tools` 只校验 list，不校验 list item schema。
- 不符合 documented schema 的输入可能继续进入 tokenizer / replay。

修改方向：

- `model` 必须是 non-empty string。
- `messages` 必须是 list of mapping。
- 每条 message 至少包含可解释的 `role` 和 `content` 字段。
- `tools` 必须是 list of mapping。
- 解析失败时抛出清晰错误，不 silent fallback。

### 4.5 P2: 同时间戳请求排序缺少显式 tie-break

现状：

- `build_simulation_requests()` 只按 `service_start_time` 排序。

修改方向：

排序 key 固定为：

```python
(
    request.service_start_time,
    request.instance_uuid,
    request.request_id,
)
```

这样同时间戳请求在不同输入行顺序下仍有显式、可解释的顺序。

### 4.6 P2: `cache_event_stats` 暴露可变引用

现状：

- `BatchAwareReplayEngine.run()` 返回 `cache_event_stats=sink.stats`。
- `CacheEventStats` 是 mutable dataclass。
- 如果调用方复用 sink，旧 result 可能被后续写入影响。

修改方向：

- 给 `CacheEventStats` 增加 `snapshot()` 或 `copy()`。
- `BatchAwareReplayResult` 持有 stats 快照。
- 测试覆盖 result stats 不随 sink 后续变化。

### 4.7 P2: scaffold / stub 模块状态不清楚

现状：

- `external/*`、`cache/policy.py`、`cache/simulator.py`、`latency/lookup.py` 等模块未接入主链路或属于未来接口。

影响：

- 新同事可能误以为这些模块已经被 runner 使用。

修改方向：

- 给保留的 future modules 增加 module docstring 或 README，说明状态和边界。
- 对 external adapters 增加最小 contract tests，只验证未接真实外部工具时显式 `NotImplementedError` 或等价 guard。
- 不在本轮接入真实外部仿真器。

### 4.8 P3: summary 文案仍残留阶段名

现状：

- summary 中有类似 `HBM / DDR KV load time is not modeled in Batch D.` 的阶段名残留。

修改方向：

- 改为稳定产品语义：

```text
HBM / DDR KV load time is not modeled in this replay mode.
```

### 4.9 P3: waiting queue `pop(0)` 潜在性能问题

现状：

- scheduler 内部 list `pop(0)` 是 O(n)。

本轮决策：

- 不在本轮重构。
- 原因是 `_prepare_waiting_frontier()` 当前需要按 index 扫描 waiting queue；直接换成 `deque` 会影响接口和可读性。
- 进入大 trace 压测前，再单独设计 queue abstraction 和 benchmark。

## 5. 代码开发批次

### Batch F1: Formatting-only baseline

目的：

- 建立统一格式基线。
- 避免格式 diff 混入逻辑改动。

修改：

- 执行 `ruff format src tests scripts`。
- 不修改业务逻辑。
- 不手写功能补丁。

验证：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff format --check src tests scripts
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
```

验收：

- format check 通过。
- pytest 仍通过。

### Batch F2: Package CLI becomes the real entrypoint

目的：

- 让 `hitfloor simulate` 和 `hitfloor validate-trace` 真正可用。
- 消除 scripts 与 package CLI 的行为分裂。
- 修复 scripts `E402`。

建议修改文件：

```text
src/hitfloor/cli/main.py
scripts/run_simulation.py
scripts/validate_trace.py
tests/unit/cli/test_main.py
tests/integration/test_hitfloor_cli_simulate.py
tests/integration/test_hitfloor_cli_validate_trace.py
```

接口建议：

```text
hitfloor simulate --config configs/experiments/step5_hbm_lru.yaml
hitfloor validate-trace --input data/sample.csv
```

实现要求：

- CLI 只做参数解析、调用 lib、打印输出路径。
- CLI 不承载核心 replay 逻辑。
- `simulate` 调用 `load_yaml` 和 `ExperimentRunner.run()`。
- `validate-trace` 调用 trace reader，输出请求条数、实例数、时间范围等基础校验信息。
- scripts 只作为薄 wrapper，避免 module-level `sys.path` 后 import。

测试：

- CLI simulate 使用合成 config 跑出：
  - `request_metrics.csv`
  - `iteration_metrics.csv`
  - `summary.md`
  - `cache_events.csv`，当 mode 是 `batch_aware_hbm_lru`
- CLI validate-trace 对合法 CSV 返回成功。
- CLI validate-trace 对缺字段 CSV 返回非 0 或清晰异常。
- scripts wrapper 与 package CLI 主路径一致。

验收：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff check src tests scripts
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
```

### Batch F3: Config scope and parser guard

目的：

- 避免当前 config 暗示未完成能力。
- 加固 documented schema。
- 固化同时间戳排序 tie-break。

建议修改文件：

```text
configs/experiments/default.yaml
configs/experiments/future_hit_floor_template.yaml
src/hitfloor/request/parser.py
src/hitfloor/experiment/runner.py
tests/unit/request/test_parser_errors.py
tests/unit/test_simulation_request.py
tests/integration/test_phase1_runner.py
tests/integration/test_batch_d_runner.py
tests/integration/test_step5_hbm_lru_runner.py
```

config 修改：

- `default.yaml` 只保留当前 runner 已实现的 runnable replay 字段。
- 未来 hit floor search 字段移动到 `future_hit_floor_template.yaml` 或 docs。
- 文档写明：future template 不是当前可运行产品能力。

parser 修改：

- `model` 缺失、非 string、空 string：失败。
- `messages` 缺失或非 list：失败。
- `messages[*]` 非 mapping：失败。
- `messages[*].role` 缺失或非 string：失败。
- `messages[*].content` 缺失：失败。
- `tools` 存在时必须是 list of mapping。

排序修改：

- `build_simulation_requests()` 使用 `(service_start_time, instance_uuid, request_id)` 排序。

测试：

- 合法 OpenAI-style request 仍通过。
- 缺失 model 失败。
- 空 model 失败。
- messages 非 list 失败。
- message item 非 dict 失败。
- message 缺 role / content 失败。
- tools 非 list 失败。
- tools item 非 dict 失败。
- 同时间戳请求排序有稳定 tie-break。

验收：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/unit/request tests/unit/test_simulation_request.py
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
```

### Batch F4: Result immutability, stale wording, and stub boundaries

目的：

- 收紧 public result API。
- 清理阶段名残留。
- 降低 scaffold 误读成本。

建议修改文件：

```text
src/hitfloor/cache/event_sink.py
src/hitfloor/replay/event_loop.py
src/hitfloor/replay/metrics.py
src/hitfloor/report/summary.py
src/hitfloor/external/*.py
src/hitfloor/cache/policy.py
src/hitfloor/cache/simulator.py
src/hitfloor/latency/lookup.py
tests/unit/cache/test_cache_event_sink.py
tests/unit/replay/test_batch_aware_replay_hbm_lru.py
tests/unit/report/test_summary.py
tests/unit/external/test_adapter_boundaries.py
```

实现要求：

- `CacheEventStats` 提供不可变快照。
- `BatchAwareReplayResult.cache_event_stats` 存放快照，不引用 sink 内部状态。
- summary 文案不再出现 `Batch D` 等阶段名。
- future / stub modules 的 docstring 必须说明：
  - 当前是否接入 runner。
  - 当前是否执行真实外部工具。
  - 如果未实现，失败方式是什么。
  - 后续应该在哪里接入。

测试：

- 复用同一个 sink 进行第二次 emit 后，第一次 replay result 的 stats 不变化。
- summary 文案不包含 `Batch D`。
- external adapters 未接真实工具时显式失败，不静默返回虚假 latency。

验收：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/unit/cache tests/unit/replay tests/unit/report tests/unit/external
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
```

### Batch F5: Final verification and Step5 close-out docs

目的：

- 确认 Step1-Step5 核心仿真器可作为后续基础。
- 更新阶段状态和记忆。
- 准备 Step5 归档。

建议修改文件：

```text
docs/development_status.md
docs/global_memory.md
docs/step5/README.md
docs/reviews/step1_step5_core_simulator_review.md
README.md
```

验证命令：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff format --check src tests scripts
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff check src tests scripts
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m hitfloor.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml
```

如果 package 安装入口可用，还应验证：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/hitfloor simulate --config configs/experiments/step5_hbm_lru.yaml
```

文档更新：

- `development_status.md` 标注 Step5 工程收口完成。
- `global_memory.md` 写明当前基础能力和 frozen semantics。
- Step5 README 更新最终状态。
- 如果用户确认归档，将 Step5 过程文档移入 `docs/archive/step5/`，只保留必要索引。

验收：

- ruff format check 通过。
- ruff check 通过。
- full pytest 通过。
- package CLI 和 script wrapper 都能跑通合成数据 E2E。
- 文档明确 Step1-Step5 是核心仿真骨架，不是完整 hit floor search 产品。

## 6. 最终验收标准

功能验收：

- `hitfloor simulate --config ...` 会真实运行 replay 并写报告。
- `hitfloor validate-trace --input ...` 会真实读取并校验 trace。
- `batch_aware_hbm_lru` 语义不变。
- `cache_events.csv` 仍是 streaming writer 输出。
- parser 对 documented schema 以外输入显式失败。
- result stats 不暴露可变 sink 状态。

质量验收：

- `ruff format --check src tests scripts` 通过。
- `ruff check src tests scripts` 通过。
- full `pytest` 通过。
- 新增和修改逻辑都有对应测试。
- 未实现能力在 config、README、stub module 中不再产生误导。

文档验收：

- README 保留 frozen core semantics。
- Step5 README 与 development status 一致。
- review 文档中的 P1/P2/P3 是否已处理有明确状态。
- Step5 归档前，活跃文档保持轻量。

## 7. 风险与处理

### 7.1 Formatting-only diff 较大

风险：

- `ruff format` 会影响大量文件，review 可能被格式 diff 淹没。

处理：

- Batch F1 单独提交 / 单独 review。
- 不和功能修改混在一起。

### 7.2 Parser 变严格后旧合成数据失败

风险：

- 测试 fixture 或合成 CSV 中存在不完整 request dict。

处理：

- 修正 fixture，使其符合 documented schema。
- 不新增宽松 fallback。

### 7.3 CLI 引入路径问题

风险：

- 未安装 package 时，`scripts/*.py` 与 `python -m hitfloor.cli.main` 行为不同。

处理：

- CLI 核心逻辑放在 package 内。
- scripts 只负责开发环境下找到 `src` 并调用 package CLI。
- integration tests 同时覆盖 package CLI 和 scripts wrapper。

### 7.4 Stub contract test 过度绑定未来接口

风险：

- future adapter 还没定型，测试写太细会阻碍后续变化。

处理：

- 只测试当前边界：未接真实外部工具时显式失败。
- 不测试未来具体参数转换细节。

## 8. 用户审批点

请重点 review 以下决策：

1. 本轮是否只做工程收口，不实现 hit floor search。
2. 是否接受先做 formatting-only Batch F1。
3. 是否接受 package CLI 成为正式入口，scripts 退为 wrapper。
4. 是否接受 parser 对 request schema 变严格。
5. 是否接受 default config 移除未实现的 hit floor / DDR 字段，转入 future template 或文档。
6. 是否接受 waiting queue 性能问题暂不修，只作为后续 benchmark / queue abstraction 任务。

审批通过后，按 Batch F1 -> F5 顺序进入代码开发。
