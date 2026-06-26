# Step1-Step5 Core Simulator Review

评审时间：2026-06-25

评审对象：

- `src/infertwin/`
- `tests/`
- `scripts/`
- `configs/experiments/default.yaml`
- `configs/experiments/step5_hbm_lru.yaml`
- Step1-Step5 相关文档

本次评审目标：

1. 基于 `ruff` 和测试结果，评估 Step1-Step5 搭建出的核心仿真器质量。
2. 从功能完善度、代码结构、测试覆盖、函数质量、性能、可维护性、可扩展性等方面给出审查意见。
3. 明确当前骨架是否可以作为后续 InferTwin 扩展基础，以及进入下一阶段前建议优先处理的问题。

## 0. Engineering Close-Out Resolution

更新时间：2026-06-25

评审后已完成 Batch F1-F5 工程收口：

- P1 package CLI 占位问题已修复：`src/infertwin/cli/main.py` 已调用真实 runner / trace reader，scripts 已退为 wrapper。
- P1 hit floor search 未实现问题已转为明确边界：本轮不实现 search，future template 已移动到 `configs/experiments/future_hit_floor_template.yaml`。
- P1 ruff / format 基线已建立：`ruff check src tests scripts` 和 `ruff format --check src tests scripts` 均通过。
- P2 parser schema guard 已收紧。
- P2 同时间戳请求排序已增加 `(service_start_time, instance_uuid, request_id)` tie-break。
- P2 `cache_event_stats` 已改为 result snapshot。
- P2 scaffold / stub 模块已补充状态说明，并新增 external adapter boundary tests。
- P3 summary 阶段名残留已清理。
- P3 waiting queue `pop(0)` 暂不修，后续在大 trace benchmark 前单独设计 queue abstraction。

最新验证结果：

```text
ruff format --check src tests scripts: passed
ruff check src tests scripts: passed
pytest: 93 passed
python -m infertwin.cli.main simulate --config configs/experiments/step5_hbm_lru.yaml: passed
```

## 1. 结论摘要

Step1-Step5 已经形成了一个可用的核心 replay 仿真骨架：

- 可以读取现网风格 CSV。
- 可以解析 OpenAI-style request。
- 可以按模型选择 tokenizer profile。
- 可以生成 hash-only prefix blocks。
- 可以进行固定路由、多实例隔离 replay。
- 可以模拟 vLLM-like continuous batching / chunked prefill。
- 可以使用 fitted TTFT backend 估算 iteration duration 和 TTFT。
- 可以使用无限 HBM 或有限 HBM LRU cache。
- 可以输出 `request_metrics.csv`、`iteration_metrics.csv`、`cache_events.csv` 和 `summary.md`。
- Step5 已补齐 streaming cache events、stateful eviction policy、finish-time materialization 边界文档。

核心链路的工程质量整体良好，尤其是 replay / scheduler / cache / latency 的职责边界比较清晰，测试覆盖也集中在关键语义上。

但它还不是完整的 InferTwin 产品形态。当前主要是“核心仿真骨架”，还缺：

- hit floor search / P90 TTFT target sweep。
- DDR / SSD / multi-tier cache。
- 外部 latency simulator 的真实 adapter。
- packaging CLI 的真实运行入口。
- 更严格的输入 schema guard。
- 项目级 lint / format 基线。

建议结论：

```text
可以作为后续 Step6+ 的稳定基础继续开发；
但在面向同事使用或进入更大规模 trace 前，应优先处理 P1/P2 评审项。
```

## 2. 客观检查结果

### 2.1 Ruff Check

命令：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff check src tests scripts
```

结果：未通过，发现 4 个问题。

```text
scripts/run_simulation.py:15: E402 Module level import not at top of file
scripts/run_simulation.py:16: E402 Module level import not at top of file
scripts/validate_trace.py:15: E402 Module level import not at top of file
src/infertwin/request/chat_template.py:7: F401 typing.Any imported but unused
```

说明：

- `E402` 来自脚本中手动插入 `src` 到 `sys.path` 后再 import InferTwin 模块。
- `F401` 是真实无用 import，可直接清理。
- 如果后续要把 `ruff check` 放进 CI，当前必须先修复或配置合理忽略。

### 2.2 Ruff Format

命令：

```bash
TMPDIR=/tmp PYTHONPATH=src .venv/bin/ruff format --check src tests scripts
```

结果：未通过。

```text
65 files would be reformatted, 27 files already formatted
```

说明：

- 当前项目没有统一应用 `ruff format`。
- 这不一定影响仿真正确性，但会影响后续 review 成本和 CI 建设。
- 建议单独开一次 formatting-only 变更，不与功能修改混在一起。

### 2.3 Pytest

命令：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest
```

结果：

```text
74 passed
```

### 2.4 Coverage

命令：

```bash
TMPDIR=/tmp HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=src .venv/bin/python -m pytest --cov=infertwin --cov-report=term-missing
```

结果：

```text
74 passed
TOTAL 1715 statements, 330 missed, 81% coverage
```

核心模块覆盖情况：

| 模块 | 覆盖率 | 评价 |
| --- | ---: | --- |
| `cache/hbm_lru.py` | 100% | 关键有限 HBM cache 覆盖充分 |
| `cache/event_sink.py` | 100% | streaming event 基础件覆盖充分 |
| `cache/eviction.py` | 92% | stateful LRU policy 覆盖较好 |
| `replay/event_loop.py` | 96% | 核心 replay 状态机覆盖较好 |
| `scheduler/vllm_like.py` | 91% | scheduler 关键路径覆盖较好 |
| `latency/fitted_ttft.py` | 88% | fitted backend 覆盖可接受 |
| `experiment/runner.py` | 94% | runner 主路径覆盖较好 |
| `request/tokenizer_registry.py` | 87% | registry 主路径覆盖较好 |
| `request/chat_template.py` | 56% | 模板渲染边界覆盖不足 |
| `request/tokenizer.py` | 43% | simple tokenizer 覆盖不足 |
| `cli/main.py` | 0% | CLI 仍是占位实现 |
| `external/*` | 多数 0% | 外部 adapter 仍是 stub |
| `cache/policy.py` / `cache/simulator.py` | 0% | 旧 scaffold 或未接入模块 |

## 3. 主要发现

### P1. 包安装后的 `infertwin` CLI 仍是占位实现

证据：

- `pyproject.toml` 注册了 `infertwin = "infertwin.cli.main:main"`。
- `src/infertwin/cli/main.py` 的 `simulate` 分支只打印 `"Simulation entry is ready"`，没有调用 `load_yaml`、`ExperimentRunner` 或写报告。
- 当前真实可用入口是 `scripts/run_simulation.py`。

影响：

- 同事如果按 Python package 方式安装并执行 `infertwin simulate --config ...`，不会真正跑仿真。
- 这会造成“项目可运行”与“包命令可运行”的体验不一致。

建议：

- 将 `scripts/run_simulation.py` 的真实逻辑迁入 `infertwin.cli.main`。
- `scripts/run_simulation.py` 可以保留为薄 wrapper。
- 为 `infertwin simulate` 和 `infertwin validate-trace` 增加 integration tests。

### P1. 产品级 hit floor search 尚未实现

证据：

- `configs/experiments/default.yaml` 已出现 `targets.p90_ttft_ms`、`cache.hbm_capacity_gb`、`cache.ddr_capacity_gb`、`output.hit_floor_table`。
- `ExperimentRunner` 当前只按一个 mode 做单次 replay。
- `src/infertwin/experiment/search.py` 只有简单 grid helper，没有接入 runner/report。

影响：

- 当前输出是 replay metrics，不是“不同 P90 TTFT 对应底线 KV cache hit”的最终产品表。
- Step1-Step5 完成的是仿真骨架，不是完整 InferTwin 产品闭环。

建议：

- 下一阶段单独设计 hit floor search：
  - 输入 target P90 TTFT list。
  - 输入可搜索的 capacity 或 hit-rate control 参数。
  - 多次调用 replay runner。
  - 输出 `hit_floor.csv` 和 summary。
- 不建议直接在现有 `ExperimentRunner._run_batch_aware_hbm_lru()` 中堆搜索逻辑，应新增 `InferTwinSearchRunner` 或 `experiment/search_runner.py`。

### P1. Ruff / format 基线尚未建立

证据：

- `ruff check` 当前失败 4 项。
- `ruff format --check` 当前显示 65 个文件需要格式化。

影响：

- 现在无法把 `ruff check` / `ruff format --check` 作为 CI gate。
- 后续多人协作时，样式 diff 可能淹没真正的逻辑改动。

建议：

- 先做一次 formatting-only 变更。
- 修复 unused import。
- 对脚本 E402 二选一：
  - 推荐：让 CLI 真正可用，脚本只调用 package API，去掉 `sys.path` hack。
  - 或者：在 `pyproject.toml` 针对 `scripts/*.py` 配置 per-file ignore `E402`。

### P2. 输入 schema guard 仍偏宽

证据：

- `parse_request_params()` 中 `model=str(payload.get("model", ""))`。
- `messages` 和 `tools` 只验证是 list，没有验证 list item schema。
- 缺失 `model` 时不会在 parser 层失败，而可能落到 `default_profile`。

影响：

- 如果现网数据出现缺失 model、message item 非 dict、role/content 结构异常，InferTwin 可能继续运行并给出看似正常的结果。
- 这与“只兼容 documented schema，不做过度兼容”的开发原则有偏差。

建议：

- parser 层显式要求：
  - `model` 必须是 non-empty string。
  - `messages` 必须是 list of mapping。
  - 每条 message 至少包含可解释的 `role` / `content`。
  - `tools` 必须是 list of mapping。
- 解析失败时直接抛出清晰错误或返回 parse_error 类型，不要静默 default。

### P2. 同时间戳请求排序缺少稳定 tie-break

证据：

- `build_simulation_requests()` 返回 `sorted(requests, key=lambda request: request.service_start_time)`。
- replay 内部有更稳定的 `(start_time_ms, request_id)` 排序，但 build 阶段的返回顺序本身没有 request-level tie-break。

影响：

- 如果 trace 中多条请求具有相同 `service_start_time`，Python sort 会保留 CSV 原始顺序。
- 这通常是 deterministic 的，但输出依赖输入行顺序，不够显式。
- 对“核心结果必须可测试、可复现；排序有确定性 tie-break”的项目原则来说，可以进一步加固。

建议：

- 改为：

```python
return sorted(
    requests,
    key=lambda request: (
        request.service_start_time,
        request.instance_uuid,
        request.request_id,
    ),
)
```

### P2. `BatchAwareReplayResult.cache_event_stats` 返回可变 stats 引用

证据：

- `BatchAwareReplayEngine.run()` 返回 `cache_event_stats=sink.stats`。
- `CacheEventStats` 是 mutable dataclass。
- `InMemoryCacheEventSink.stats` 返回内部 `_stats` 对象。

影响：

- 如果调用方复用同一个 sink 多次运行 replay，旧 `BatchAwareReplayResult.cache_event_stats` 可能随 sink 后续写入而变化。
- 当前 runner 每次创建新的 `CsvCacheEventWriter`，所以主路径没有问题。
- 但这是一个 API 边界风险。

建议：

- 提供 `CacheEventStats.snapshot()` 或将 `CacheEventStats` 改为 frozen + sink 内部替换。
- `BatchAwareReplayResult` 应持有不可变快照。

### P2. 一批 scaffold / stub 模块未接入主链路，容易增加认知负担

证据：

- coverage 显示 `external/*` 多数 0%。
- `cache/policy.py`、`cache/simulator.py`、`latency/lookup.py`、`cli/main.py` 等未接入主链路或仍是历史骨架。

影响：

- 新同事阅读代码时不容易判断哪些是当前有效路径、哪些是未来占位。
- 后续修改可能误改未接入模块，造成“看似实现了，其实 runner 没用”的问题。

建议：

- 给 stub 模块加 `README` 或 module docstring 标明状态。
- 对不再计划使用的 scaffold，移入 `docs/archive` 或删除。
- 对计划保留的 future adapters，补最小 contract test 或显式 `NotImplementedError` 测试。

### P3. 报告文案存在阶段名残留

证据：

- `write_batch_aware_summary()` 中仍写 `"- HBM / DDR KV load time is not modeled in Batch D."`

影响：

- Step5 之后报告仍显示 Batch D，可能让同事误解当前模式。

建议：

- 改为不带阶段名的稳定语句，例如：

```text
HBM / DDR KV load time is not modeled in this replay mode.
```

### P3. 大规模 waiting queue 下存在 list `pop(0)` 的潜在性能问题

证据：

- `VllmLikeBatchScheduler.schedule()` 对 waiting queue 使用 `waiting.pop(0)`。

影响：

- Python list 头部 pop 是 O(n)。
- 当前 2 小时高峰 trace 未必会触发明显问题，但当 waiting queue 很大时会有性能风险。

建议：

- 保持现有语义不变的前提下，后续可评估 `deque` 或显式 queue abstraction。
- 由于 `_prepare_waiting_frontier()` 当前需要按 index 扫描，若切换数据结构，应先设计 queue API，不建议局部替换。

## 4. 功能完善度评审

### 已完成能力

Step1-Step5 已完成“核心仿真骨架”：

- trace CSV reader。
- request parser。
- tokenizer registry。
- GLM-5 tokenizer profile 接入。
- hash-only prefix block hasher。
- instance-local infinite HBM replay。
- fixed-routing, multi-instance isolated replay。
- vLLM-like scheduler。
- chunked prefill。
- bounded waiting lookup。
- zero-miss fast finish。
- fitted TTFT latency backend。
- finite HBM LRU cache。
- cache events。
- streaming `cache_events.csv`。
- stateful eviction policy。
- runner/report integration。
- synthetic E2E tests。

### 尚未完成能力

这些能力不应被误认为已经完成：

- hit floor search。
- DDR LRU。
- SSD tier。
- HBM/DDR/SSD KV load latency。
- gateway routing simulation。
- instance-side queueing policy simulation。
- external AIConfigurator / MkSim production adapter。
- cross-instance KV pooling。
- sparse-attention cache manager。
- progressive block materialization。

当前项目定位应表述为：

```text
InferTwin Step1-Step5 provides a maintainable offline replay skeleton,
not the final hit-floor search product.
```

## 5. 代码结构评审

整体结构是健康的：

```text
trace       -> CSV input schema
request     -> parser, tokenizer, chat template, block hasher
scheduler   -> vLLM-like batch planner
replay      -> event loop and replay metrics
latency     -> formula / fitted backend
cache       -> prefix cache protocol, infinite HBM, finite HBM LRU
experiment  -> runner
report      -> csv and markdown writers
external    -> future adapter boundaries
```

优点：

- CLI / runner / report 没有混入核心 replay 逻辑。
- cache 通过 `PrefixCache` protocol 与 replay 解耦。
- latency backend 通过 `BatchLatencyBackend` 解耦。
- stateful eviction policy 已经为后续策略扩展留好接口。
- streaming writer 保持在 report 层，没有污染 cache 层。

主要结构风险：

- `ExperimentRunner` 已经承担了较多 mode dispatch、config validation、request building、report writing。当前 324 行可接受，但下一阶段如果加入 hit floor search，不应继续往里面堆。
- `external/*`、`cache/policy.py`、`cache/simulator.py` 等未来/旧模块状态不够显式。
- package CLI 和 scripts 入口分裂。

## 6. 测试评审

测试优点：

- 关键语义有针对性测试：
  - first-schedule-time lookup。
  - finish-time materialization。
  - zero-miss fast finish。
  - finite HBM eviction。
  - multi-instance isolation。
  - streaming cache events。
  - stateful LRU policy。
  - runner/report E2E。
- 全量 `pytest` 通过。
- coverage 对核心路径较高。

测试不足：

- CLI 没有真实测试，因为 CLI 仍是占位实现。
- parser 错误路径不足。
- chat template / tokenizer 边界覆盖不足。
- report summary 的一些错误路径和空输入路径覆盖不足。
- external adapter 只有边界 skeleton，无 contract test。
- 默认 config 中 targets/cache GB 字段未被测试，因为 hit floor search 尚未实现。

建议下一阶段新增：

- `tests/unit/request/test_parser_errors.py`
- `tests/unit/cli/test_main.py`
- `tests/integration/test_infertwin_cli_simulate.py`
- `tests/integration/test_hit_floor_search_runner.py`
- `tests/unit/report/test_summary.py`
- external adapter contract tests，只测试 schema conversion，不要求真实外部工具。

## 7. 函数和代码质量评审

整体评价：

- 函数命名直接，schema dataclass 清楚。
- replay 状态机复杂但仍可读。
- Step5 后 `event_loop.py` 441 行，符合当前调整后的“核心状态机不强拆”原则。
- cache 和 scheduler 的函数边界比较干净。

比较好的实现：

- `planned_prefill_tokens()` 被 replay 和 scheduler 复用，避免 lookup frontier 与 scheduler 选择逻辑分叉。
- `ShapeMemo` 对 latency result 做 copy/replace，避免 memoized 标记污染原对象。
- `CsvCacheEventWriter` streaming 写入并同步 stats，职责明确。
- `LRUEvictionPolicy` 使用 hooks 维护 queue，便于后续策略扩展。

需要关注的实现：

- `parse_request_params()` 应更严格。
- `BatchAwareReplayResult.cache_event_stats` 应改为快照。
- `ExperimentRunner` 下一阶段要避免继续膨胀。
- scripts 的 `sys.path` hack 与 package CLI 分裂。

## 8. 性能评审

当前性能设计优点：

- cache 只保存 hash-only metadata，不保存全量 tokens 或 KV tensor。
- `cache_events.csv` 已 streaming，避免完整 events 常驻内存。
- `ShapeMemo` 避免重复调用 latency backend。
- fixed-routing multi-instance replay 是按 instance 分组，语义简单清楚。

潜在性能风险：

- `waiting.pop(0)` 在大 waiting queue 下是 O(n)。
- lookup miss / materialize 可能对每个 block 写 event，大 trace 下文件 IO 可能很大。
- tokenizer 阶段对所有 records 一次性构建 `SimulationRequest`，没有 streaming request build。
- 多实例 replay 当前是串行执行；这符合确定性优先，但未来大规模 trace 可考虑并行按 instance replay，再合并 metrics。

建议：

- 当前不急着优化，先保持语义正确。
- 在进入大 trace 压测前，增加 benchmark：
  - 10k / 100k requests。
  - 不同 prompt length。
  - 不同 capacity pressure。
  - cache event output on/off。
- 如发现瓶颈，再引入 queue abstraction、per-instance parallel replay 或 event sampling/config。

## 9. 可维护性评审

维护性优点：

- 文档组织非常充分，阶段边界清楚。
- README 已冻结核心语义。
- docs/global_memory 和 docs/development_status 能帮助后续 agent 不跑偏。
- 测试命名对应功能模块，定位方便。

维护性风险：

- 状态文档较长，后续应在阶段收口后 archive，保持活跃文档轻量。
- 部分旧 scaffold 未标注状态。
- 当前 git index 状态显示项目文件整体 untracked，后续需要一次规范提交。
- 没有 CI 配置，无法自动执行 pytest/ruff。

建议：

- Step5 最终归档时，把过程文档移入 archive，只保留 README / status / memory 的简明状态。
- 建立 CI：

```bash
ruff check src tests scripts
ruff format --check src tests scripts
pytest
```

- 先做一次 formatting-only PR。

## 10. 可扩展性评审

扩展性总体较好：

- gateway 可作为 replay 前置层。
- queueing policy 可作为 instance arrival 与 scheduler admission 之间的新层。
- eviction policy 已可替换。
- latency backend 已可替换。
- cache backend 可扩展到 DDR / SSD / pooling。
- external simulator adapter 已有目录边界。

下一阶段建议新增的扩展点：

- `experiment/search_runner.py`：hit floor search。
- `cache/factory.py`：根据 config 创建 cache backend / eviction policy。
- `latency/external_adapter.py`：统一外部 simulator adapter schema。
- `report/hit_floor.py`：专门写 `hit_floor.csv`。
- `cli/main.py`：正式对外入口。

## 11. 建议优先级

### 近期必须处理

1. 修复或配置 `ruff check`。
2. 做一次 `ruff format` 统一格式。
3. 接通 package CLI。
4. 明确 default config 中尚未实现的 fields，避免误导。
5. 收紧 parser schema guard。

### 下一阶段核心开发

1. hit floor search runner。
2. hit floor CSV report。
3. config schema / mode guard。
4. CLI integration tests。

### 后续扩展

1. DDR LRU。
2. external latency simulator adapter。
3. large-trace benchmark。
4. multi-instance parallel replay。
5. progressive materialization 独立模式。

## 12. 最终评审结论

Step1-Step5 的核心仿真器已经具备继续演进的基础。当前最强的部分是 replay 语义、finite HBM cache 生命周期、streaming events、stateful eviction policy 和测试驱动的阶段推进。

当前最大短板不是核心 replay 逻辑，而是产品闭环和工程化入口：

- 还没有 hit floor search。
- CLI 入口未真正运行。
- ruff/format 尚未成为可通过的质量门禁。
- 部分 scaffold 未清理或未标注。

建议在进入 DDR / hit floor search 之前，先做一个短的工程收口批次：

```text
format + ruff clean + CLI real entry + parser guard + config guard
```

完成后，InferTwin 会更适合交给同事接入外部 simulator、跑更大 trace、继续开发 hit floor search。
