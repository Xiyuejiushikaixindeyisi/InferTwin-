# InferTwin 项目评审意见

评审日期：2026-06-27

评审对象：InferTwin 当前 `main` 分支核心仿真器与外围能力。

评审范围：

- package import 与重命名一致性。
- 核心 replay、scheduler、cache、latency、streaming、config、CLI 的方法实现。
- 注释 / docstring / 文档口径。
- 潜在 bug 与大 trace 风险。
- 与当前产品需求的符合度。
- 测试覆盖、可维护性、可扩展性。

## 1. 总体结论

InferTwin 当前已经具备作为后续 TOB 大型推理服务集群离线仿真平台的基础骨架：

- 固定路由、多实例隔离 replay 已经成立。
- streaming shard 架构已经避免将大型 trace 的所有 request 留在内存中。
- HBM LRU、vLLM-like batch-aware replay、chunked prefill、fitted TTFT、instance latency profile、model registry default fallback 均已接入主链路。
- 核心仿真器和外围 report / CLI 能力边界总体清晰。
- `ruff`、format、全量测试、coverage 均为绿色。

但进入下一阶段前，建议优先处理几个工程边界问题：核心 trace reader 对空 `instance_uuid` 没有 fail-fast、`streaming.require_sorted_trace=false` 可能导致 shard 内请求乱序、`validate-trace` 对大 trace 仍然全量读内存、model registry 相对路径语义依赖当前工作目录。这些问题不破坏当前合成数据和默认配置，但会影响同事在真实 trace 和不同运行目录下的使用可靠性。

结论：可以继续作为后续扩展基础，但建议先做一轮小型工程修正，再进入更复杂的 Step7 语义开发。

## 2. 质量门禁结果

本次评审执行：

```bash
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
PYTHONPATH=src .venv/bin/python -m pytest
PYTHONPATH=src .venv/bin/python -m pytest --cov=src/infertwin --cov-report=term-missing
```

结果：

- `ruff check`：通过。
- `ruff format --check`：通过，150 个文件已格式化。
- 全量 pytest：235 passed。
- 覆盖率：总覆盖率 93%。

覆盖率较低但可接受的模块：

- `src/infertwin/request/tokenizer.py`：67%，简单 tokenizer 辅助模块。
- `src/infertwin/request/chat_template.py`：81%，建议后续补工具调用 / 空 content / template 分支测试。
- `src/infertwin/scheduler/batch_shape.py`：80%，建议补异常路径测试。
- `src/infertwin/scheduler/config.py`：81%，建议补直接 dataclass 构造的非法值测试。
- `src/infertwin/latency/profile.py`：88%，建议补 ServingLatencyProfile 非法配置和边界值测试。

## 3. 重点发现

### P1. 核心 trace reader 未拒绝空 `instance_uuid`

位置：

- `src/infertwin/trace/reader.py:13`
- `src/infertwin/trace/reader.py:18`
- `src/infertwin/trace/reader.py:22`

现状：

`read_trace_csv()` 只校验必需列存在，没有校验 `request_id`、`tenant_id`、`instance_uuid`、`request_params`、`service_start_time` 是否为空。按当前产品边界，核心仿真器应消费 routed trace，`instance_uuid` 必须真实存在。无实例 id trace 应由外围 `normalize-trace` 明确补齐，而不是进入核心 replay。

风险：

- 非 streaming replay 可能把空 `instance_uuid` 当作一个真实实例 replay。
- streaming replay 后续 metric aggregation 会在空 instance 上失败，错误暴露较晚。
- 用户可能误以为“无实例 id”触发了 gateway routing simulation。

建议：

- 在 `read_trace_csv()` 或单独 `TraceRecord.from_row()` 中 fail-fast 校验关键字段非空。
- 对空 `instance_uuid` 输出明确错误：核心仿真器需要 routed trace；如果明确不做 gateway routing，请先使用 `infertwin normalize-trace`。
- 增加单测覆盖空 `instance_uuid`、空 `request_id`、非法 timestamp。

### P1. `streaming.require_sorted_trace=false` 可能产生乱序 shard

位置：

- `src/infertwin/streaming/build.py:77`
- `src/infertwin/streaming/build.py:79`
- `src/infertwin/streaming/build.py:85`
- `src/infertwin/streaming/replay.py:57`
- `src/infertwin/streaming/replay.py:69`

现状：

streaming builder 默认要求 trace 按 `(service_start_time, instance_uuid, request_id)` 排序。若用户设置 `require_sorted_trace=false`，builder 会按输入顺序写 per-instance shard，但 replay engine 仍假设 `RequestSource.peek()` 给出的下一条请求就是当前实例时间序列中的下一条请求。

风险：

- 如果输入 trace 没有按每个实例的时间排序，streaming replay 会以错误顺序推进时间。
- TTFT、cache hit、materialization、eviction event 都可能偏离预期。
- 这是静默逻辑风险，测试默认 sorted trace 不容易覆盖。

建议：

- 第一选择：保留 `require_sorted_trace=true` 作为唯一支持模式，`false` 直接 fail-fast，直到实现 shard-level sort。
- 第二选择：即使 `require_sorted_trace=false`，也对每个 instance shard 维护 last key，发现同一 instance 内乱序立即报错。
- 第三选择：实现外部排序 / shard sort，但这是大 trace 专项能力，不建议顺手做。

### P1. “多实例不同配置回放”目前只完整覆盖 latency，不完整覆盖 scheduler/cache/deployment

位置：

- `src/infertwin/streaming/sweep.py:55`
- `src/infertwin/streaming/sweep.py:106`
- `src/infertwin/streaming/sweep.py:116`
- `src/infertwin/experiment/request_builder.py:77`
- `src/infertwin/request/build_context.py:123`

现状：

streaming runner 会按 `instance_uuid` 选择 TTFT backend，但 scheduler config、HBM capacity、cache policy、block-size conversion、deployment profile 仍是一次 run 的全局配置。request build 可以按 request model 选择 tokenizer profile，但 profile-aware RunSpec 仍更偏单模型 / 单 deployment 路径。

这意味着当前支持：

- 单实例 replay。
- 多个相同配置实例 replay。
- 多个实例使用不同 TTFT 超参数 replay。

当前尚不完整支持：

- 每个 instance 独立 scheduler 参数。
- 每个 instance 独立 HBM capacity。
- 每个 instance 独立 deployment profile / runtime block size / CP / MTP。
- 一个 trace 内多模型、多硬件、多部署形态的完全异构 replay。

风险：

如果文档或 CLI 让用户以为 InferTwin 已支持完整异构集群 replay，结果会被高估。

建议：

- 在 CLI summary 和主文档中继续明确：当前 heterogeneous instance 只覆盖 latency backend。
- 后续新增 `InstanceRuntimeProfile` 或 `InstanceExecutionProfile`，显式绑定 scheduler/cache/deployment/block-size。
- 不要把 per-instance scheduler/cache 参数塞进现有 latency table；这是不同职责。

## 4. 其他问题与建议

### P2. `validate-trace` 会全量读取大 trace

位置：

- `src/infertwin/cli/main.py:129`
- `src/infertwin/cli/main.py:130`

现状：

`validate_trace()` 使用 `records = list(read_trace_csv(trace_path))`，对 11G trace 会产生明显内存压力。

建议：

- 改成 streaming 统计：逐行计数、维护 instance / tenant 集合、维护 min/max timestamp。
- 如果 instance / tenant 基数未来也可能很大，进一步支持只统计 count、采样或 HyperLogLog 类近似；第一版用 set 足够。

### P2. model registry 相对路径依赖当前工作目录

位置：

- `src/infertwin/config/model_binding.py:33`
- `src/infertwin/config/model_binding.py:41`
- `src/infertwin/config/model_binding.py:44`
- `src/infertwin/latency/instance_resolver.py:252`
- `src/infertwin/latency/instance_resolver.py:257`
- `configs/models/registry.yaml:3`

现状：

`validate_model_registry()` 支持 `base_dir`，但 `build_instance_latency_backend_resolver()` 调用 `_load_model_registry()` 时没有传入 registry 文件所在目录。因此 registry 中的相对路径是 cwd-relative，而不是 registry-file-relative。

风险：

用户从仓库根目录运行没有问题；同事从其他工作目录或通过安装后的 CLI 运行时，可能找不到 `configs/models/glm-v5.1.yaml`。

建议：

- `_load_model_registry(profile_path)` 调用 `validate_model_registry(model_registry, base_dir=profile_path.parent)`。
- 示例配置中保留仓库相对路径也可以，但代码层应支持 registry-relative。

### P2. streaming percentile 仍保存全部 TTFT 值

位置：

- `src/infertwin/streaming/metrics.py:141`
- `src/infertwin/streaming/metrics.py:149`
- `src/infertwin/streaming/metrics.py:179`

现状：

streaming aggregator 不保存完整 request metrics，但为了计算 p50 / p90 / p99，仍保存每个 scope 的全部 TTFT 数组。

风险：

对“几万条请求”的公司 trace 完全可接受；如果未来扩展到百万级 / 多 capacity / 多 instance，TTFT 列表会成为新的内存增长点。

建议：

- 当前可保留 exact percentile。
- 后续大规模 benchmark 若发现内存压力，再引入可插拔 percentile accumulator。
- 如果产品要求精确分位数，可考虑外部排序或分片 percentile；如果接受近似，可考虑 t-digest / histogram。

### P2. streaming replay 依赖 `replay.event_loop` 的私有 helper

位置：

- `src/infertwin/streaming/replay.py:7`
- `src/infertwin/streaming/replay.py:9`
- `src/infertwin/streaming/replay.py:10`

现状：

`StreamingBatchAwareReplayEngine` 继承 `BatchAwareReplayEngine`，并 import `_drain_cache_events`、`_state_from_request` 这类下划线 helper。

风险：

这是可维护性风险，不是当前 bug。后续修改 batch replay internals 时，streaming replay 可能被间接破坏。

建议：

- 将共享 helper 提升为公开模块，例如 `replay/state_factory.py` 或 `replay/helpers.py`。
- 或者让 `BatchAwareReplayEngine` 提供受控的 protected method，避免跨模块 import 私有函数。

### P2. tokenizer manifest 中 `include_tools` 类型校验偏宽松

位置：

- `src/infertwin/request/tokenizer_registry.py:150`
- `src/infertwin/request/tokenizer_registry.py:172`

现状：

`include_tools=bool(tokenizer.get("include_tools", True))` 会把字符串 `"false"` 解析成 `True`。

风险：

YAML 配置写错类型时不会 fail-fast，可能影响 prompt render 和 token 数。

建议：

- 显式要求 `include_tools` 为 bool。
- 非 bool 直接报错，并补单测。

### P3. 部分注释 / docstring 仍带阶段性表达

位置：

- `src/infertwin/scheduler/vllm_like.py:1`
- `src/infertwin/scheduler/config.py:13`
- `src/infertwin/scheduler/config.py:29`
- `src/infertwin/experiment/sweep.py:176`
- `src/infertwin/experiment/runner.py:1`

现状：

部分 docstring 仍写着 Step4 / Step6 / skeleton。作为历史阶段说明可以理解，但当前 InferTwin 已经从阶段项目演进为长期仿真平台。

建议：

- 将阶段性描述改成能力描述，例如 “vLLM-like FCFS prefill scheduler”。
- `ExperimentRunner` docstring 从 “skeleton” 改成 “small / non-streaming experiment runner”。

## 5. 导入与命名评审

结果：

- 活跃代码、测试、脚本、配置中的 import 已统一为 `infertwin`。
- 未发现活跃源码中残留 `from hitfloor` / `import hitfloor`。
- `pyproject.toml` 的 package name 和 console script 已是 `infertwin`。
- `docs/global_memory.md` 中保留旧名只用于说明“HitFloor 重命名为 InferTwin”，符合用户要求。

建议：

- 归档目录无需改名，当前处理正确。
- 当前工作目录仍是 `/home/zhangxiyue/HitFloor`，不影响 package，但若后续同事从路径识别项目，建议在合适时机迁移目录或在 README 中说明历史原因。

## 6. 方法实现评审

### 6.1 Replay event loop

优点：

- `BatchAwareReplayEngine` 职责清晰：按实例分组、推进 arrival / waiting / running、调用 scheduler、调用 latency backend、materialize cache、输出 request / iteration metrics。
- lookup 保守发生在 scheduler frontier，符合“不提前 lookup 整个 waiting 队列”的冻结语义。
- `ShapeMemo` 让相同 batch shape 复用 latency 估算，适合 fitted backend 和未来外部 backend 缓存。
- `FinishTimeMaterializationPolicy` 作为默认绑定，语义明确。

风险：

- `event_loop.py` 约 456 行，当前仍可接受，但它是核心状态机，后续如果叠加 progressive visibility、decode、queue simulation，会快速变复杂。
- 后续新增 progressive mode 时，建议新增 policy / mode，不要修改现有 `batch_aware_hbm_lru` 的 finish-time 语义。

### 6.2 Scheduler

优点：

- `VllmLikeBatchScheduler` 简洁，FCFS、token budget、seq budget、chunked prefill 的边界清楚。
- `planned_prefill_tokens()` 被 replay 和 scheduler 共用，避免 waiting lookup 与 scheduler admission 逻辑漂移。

差异：

- 当前只建模 prefill，不建模 decode / TPOT / decode KV growth。
- 不建模 vLLM 的真实 KV slot allocator、preemption、priority scheduler。

建议：

- 保持当前 scheduler 简洁。
- Step7 后如果新增 decode 或真实 queue simulation，应新增 scheduler mode 或 execution profile。

### 6.3 HBM cache

优点：

- `HBMCache` 只保存 hash key 和 metadata，不保存真实 KV tensor，适合大 trace。
- cache event sink 已有 `StatsOnlyCacheEventSink` 和 CSV writer，避免默认内存爆炸。
- eviction policy 已 stateful，后续可替换 LRU。

差异：

- 不建 physical slot、pinned/refcount、fragmentation。
- finish-time materialization 可能低估长 prefill 期间的 block reuse。

建议：

- physical KV slot 不应成为默认能力。
- progressive block visibility 已被确认为必须补齐，但应作为新 replay/cache mode。

### 6.4 Latency

优点：

- `FittedTTFTLatencyBackend` 直接服务当前产品目标，避免过早接入大型外部仿真器。
- `InstanceLatencyBackendResolver` 已支持 instance profile -> model default -> legacy global backend 的解析路径。
- `kv_load` schema 已预留 DDR / remote KV load 参数。

边界：

- queue waiting time 仍为 0。
- kv load latency 仍为 0。
- dynamic per-500-request calibration counter 尚未实现。

建议：

- 不要在现有 fitted backend 中硬塞 queue / kv load / TPOT。
- 后续设计 `ServingLatencyProfile` 的运行态组合对象，分别维护 prefill compute、kv load、queue wait、decode/TPOT。

### 6.5 Streaming architecture

优点：

- request build -> JSONL shard -> per-instance replay -> streaming aggregation 的路径清晰。
- request metrics 已 streaming emit，避免容量 sweep 时保存所有 request metrics。
- shard manifest 和 codec 有测试覆盖，适合作为大 trace 基础。

风险：

- 目前仍单线程按 capacity、按 shard 顺序 replay。
- percentile 精确计算仍保存 TTFT 列表。
- sorted trace 约束需要更强 fail-fast。

建议：

- 单线程确定性优先是正确选择。
- parallel instance / capacity sweep 后续应显式引入 execution backend，不要让 runner 隐式并行。

## 7. 注释与文档评审

总体文档已经比早期阶段更稳定：

- `docs/infertwin_product_design.md` 负责产品形态。
- `docs/core_simulator_technical_plan.md` 负责核心技术路线。
- `docs/development_governance.md` 负责治理与真实 vLLM 差异。
- `docs/global_memory.md` 记录长期约束。

建议调整：

- 删除 active code docstring 中 Step4 / Step6 等阶段口径。
- 在 README 的快速路径中强调：大 trace 使用 `sweep-streaming`，`sweep` / `simulate` 是小 trace 或开发入口。
- 在 `validate-trace` 说明中标注将改为 streaming validation，避免同事拿 11G trace 直接 OOM。

## 8. 需求符合度评审

当前已满足：

- 单实例 replay。
- 多个相同配置实例 replay。
- 多实例固定路由、cache 隔离 replay。
- instance-specific TTFT backend。
- model registry default TTFT fallback。
- tokenizer-stage 长请求拒绝。
- HBM capacity sweep 外围报告。
- true streaming request shard build、streaming replay、streaming metrics。

当前部分满足：

- 多个不同配置实例 replay：目前主要是不同 TTFT 超参数；scheduler/cache/deployment/block-size 仍全局。
- 大 trace 支撑：核心 streaming path 成立；`validate-trace` 和 exact percentile 仍有后续优化空间。
- vLLM 对齐：cached token accounting、batch-aware replay、chunked prefill 已做核心近似；progressive block visibility 尚未实现。

当前未满足，且文档已列为未来能力：

- gateway routing simulation。
- machine-side queue simulation。
- DDR / SSD / remote KV 多级 cache。
- KV load latency。
- cross-instance KV pooling。
- decode / TPOT。
- dynamic per-instance calibration refit。
- per-instance scheduler/cache/deployment 完整异构集群 replay。

## 9. 建议优先级

进入下一阶段前建议先处理：

1. 核心 trace reader 对空关键字段 fail-fast，尤其是 `instance_uuid`。
2. `streaming.require_sorted_trace=false` 改为 fail-fast，或增加 per-instance shard monotonic guard。
3. `validate-trace` 改成 streaming validation。
4. model registry 相对路径改成 registry-file-relative。
5. 在 README / CLI summary 中继续收紧“异构实例”口径：当前是 per-instance latency，不是完整 per-instance deployment。

可延后到后续工程优化：

1. streaming percentile accumulator 插件化。
2. streaming replay 与 batch replay 共享 helper 公共化。
3. tokenizer manifest bool schema 收紧。
4. active docstring 去阶段化。
5. per-instance scheduler/cache/deployment 的 `InstanceExecutionProfile` 设计。

语义类能力，不建议顺手修改：

1. progressive block visibility。
2. decode / TPOT。
3. DDR / remote KV load latency。
4. gateway routing。
5. cross-instance pooling。

这些能力都应该进入正式阶段设计，声明是核心仿真器开发，并新增 mode / backend / policy / schema。

## 10. 最终判断

InferTwin 当前可以被视为“面向大型 LLM 推理服务集群的离线仿真平台骨架”，并且足以承载后续 Step7 扩展。

不过，在接真实公司大 trace 或给同事试用前，建议先完成一轮小型可靠性修正：

- trace schema fail-fast。
- streaming sorted guard 收紧。
- streaming validate-trace。
- registry-relative config path。

完成这些后，InferTwin 的“可运行、可解释、可维护、可扩展”基础会更稳，后续再进入 progressive visibility、多级 cache、KV load latency 或 gateway routing 会更从容。
