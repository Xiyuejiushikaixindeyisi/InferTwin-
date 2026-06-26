# InferTwin 全局记忆

## 项目定位

InferTwin 是面向 TOB 大型推理服务集群的离线仿真器。

命名变更：2026-06-26 起，项目由 **HitFloor** 正式重命名为 **InferTwin**。归档文档和历史阶段记忆可以保留旧名；当前活跃代码、配置、CLI、主文档和后续开发应统一使用 InferTwin / `infertwin`。

当前已完成固定 trace、固定实例路由、固定 cache/latency 配置下的 HBM capacity sweep，能够按不同 `hbm_capacity_blocks` 输出 cache 容量、KV cache hit rate 与 P90 TTFT 的关系表。target-based hit floor solver / P90 target matching 属于外围能力，不是核心仿真器本身。

Step1-Step6 已完成核心仿真骨架。后续 gateway、实例侧排队、chunk 调度、淘汰算法、多级缓存、稀疏注意力 cache 管理和 Mooncake 多实例池化，都应作为独立仿真层、策略类、adapter 或 cache backend 接入。

核心仿真器主技术路线记录在：

```text
docs/core_simulator_technical_plan.md
```

## 核心仿真器与外围能力边界

InferTwin 必须区分核心仿真器和外围能力。

核心仿真器负责：

- trace 到 `SimulationRequest` 的构造。
- tokenizer / chat template 选择。
- prefix block hash。
- scheduler replay。
- cache lookup、materialization、eviction 和 event stats。
- latency backend 调用。
- deterministic request / iteration / sweep metrics。

外围能力负责消费核心仿真器的结构化结果：

- InferTwin 表，例如 `capacity_sweep.csv`。
- `summary.md`。
- CLI / scripts wrapper。
- dashboard / notebook / batch job。
- 未来 P90 target matching / hit floor search。
- 未来策略对比报告、容量规划报告、SLO search report。

外围能力不能改变核心 replay 语义。任何新产品形态如果需要不同语义，必须新增 replay mode、cache backend、policy、adapter 或 result schema，不能在原有字段和模式上静默改语义。

每个新阶段、每个新步骤、每个代码开发批次都必须先声明：

```text
本次开发的是核心仿真器，还是外围能力。
```

如果答案不清楚，应先回到产品形态讨论，不进入代码开发。

## 目录职责记忆

顶层目录边界：

- `src/infertwin/`：主 Python package，承载核心仿真器、runner、report、CLI 和外部 adapter 边界。
- `configs/`：模型、硬件、backend、实验配置；参数不硬编码进 Python。
- `tokenizers/`：模型 tokenizer profile 和 chat template，例如 `glm-v5/`。
- `tests/`：单元测试和集成测试，覆盖核心语义和外围输出。
- `scripts/`：本地 wrapper，只调用 package 逻辑，不承载核心业务。
- `docs/`：产品设计、技术路线、开发治理、notes、archive、review。
- `data/`：样例和本地 trace 数据；真实 raw/processed 数据默认不入库。
- `reports/`：仿真输出生成物；默认不入库，只保留 `.gitkeep`。
- `notebooks/`：探索性分析，不进入核心仿真器逻辑。
- `.git/`：版本管理元数据，不手动修改。
- `.venv/`：本地运行环境，不入库。

`src/infertwin/` 子目录边界：

- `trace/`：CSV trace schema 和 reader。
- `request/`：request parser、model resolver、tokenizer registry、chat template、prefix block hash。
- `instance/`：`SimulationRequest` 和实例侧基础结构。
- `scheduler/`：vLLM-like scheduler、chunked prefill planning、waiting queue、batch shape。
- `cache/`：cache backend、block metadata、event sink、eviction policy。
- `latency/`：fitted TTFT / formula backend、latency schema、memo。
- `replay/`：batch-aware replay event loop 和 replay metrics。
- `experiment/`：request build、single run、capacity sweep orchestration、实验级聚合。
- `report/`：CSV / Markdown report/export 外围能力。
- `cli/`：package CLI 正式入口。
- `external/`：AIConfigurator、MkSim、Ramulator2 等 adapter 边界。
- `config/`：配置加载。
- `utils/`：通用工具预留。

整理原则：核心 replay 语义只能放在核心模块中；外围 report、CLI、scripts、notebooks 只能消费 typed result，不能重算或改写 request、scheduler、cache、latency、replay 语义。

## 当前阶段

工程优化阶段、true streaming 专项、Pre-Step7 Model Registry & Instance Model Binding 专项均已完成。

当前没有活动专项；下一阶段进入前仍必须声明本阶段是核心仿真器能力还是外围能力。

最近完成专项：

```text
docs/archive/pre_step7_model_registry/
```

专项类型：核心仿真器开发，工程优化 / 配置治理 / 兜底能力。

当前状态：

- MR-1 Schema / Parser 已完成。
- MR-2 Registry Validation / Consistency Guard 已完成。
- MR-3 InstanceLatencyBackendResolver Default Fallback 已完成。
- MR-4 Streaming Runner Metadata Integration 已完成。
- MR-5 Calibration Failure Fallback Schema 已完成。
- MR-6 Docs / Examples / Memory 已完成。
- MR-7 工程收口与归档已完成。

MR-1 完成内容：

- 新增 `ModelRegistryEntry` / `ModelRegistry` schema。
- 新增 `configs/models/registry.yaml` 示例。
- 新增 `load_model_registry()`。
- `InstanceDeployment` 新增可选 `model_name` 字段。
- 更新 instance 示例配置，为实例补充 `model_name`。
- legacy instance profile 没有 `model_name` 时仍可解析。

MR-1 验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_instance_latency_profiles.py \
  tests/unit/config/test_profiles_and_guard.py

21 passed
```

MR-2 完成内容：

- 新增 `src/infertwin/config/model_binding.py`。
- 新增 model registry 与 `ModelProfile` 的一致性校验。
- 新增 instance model binding 校验。
- `InstanceProfile` parser 允许 instance 缺少 `latency_profile`，为 MR-3 model default fallback 留出语义空间。
- MR-2 仍未接入 resolver，不改变 replay。

MR-2 验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/config/test_instance_latency_profiles.py \
  tests/unit/config/test_profiles_and_guard.py

29 passed
```

MR-3 完成内容：

- `InstanceLatencyBackendResolver` 支持可选 `ModelRegistry`。
- 新增 `ModelRegistryConfig` 和 `LatencyResolutionMetadata`。
- `backend_for()` 保持返回 `BatchLatencyBackend`，不改变 replay engine 接口。
- latency backend 解析优先级：instance 专属 profile -> model registry default latency -> legacy global backend。
- 新增 `metadata_for(instance_uuid)` 和 `latency_source_by_instance`。
- `model_registry.profile_path` 一旦配置就会加载并校验 registry 本身。
- 没有 model registry 且 instance 缺少 `latency_profile` 时仍 fail-fast。
- MR-3 仍未修改 streaming runner 的 `config_details`，未修改 replay。

MR-3 验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_instance_latency_profiles.py

35 passed
```

MR-4 完成内容：

- `StreamingCapacitySweepRunner.config_details` 输出 resolver metadata。
- 新增 `model_registry_enabled`、`model_registry_profile_path`、`latency_source_by_instance`。
- `latency_source_by_instance` 只进入 `config_details` / summary，不进入 `capacity_sweep.csv`。
- `CapacitySweepRow` 不新增字段，CSV 保持纯指标表。
- summary 渲染 latency resolution metadata，但不重新计算 TTFT source。
- 集成测试覆盖 instance-a 使用 instance 专属 profile、instance-b 使用 model registry default latency。
- MR-4 未修改 replay core、cache、scheduler、tokenizer、request build 语义。

MR-4 验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py

17 passed
```

MR-5 完成内容：

- 新增 `src/infertwin/latency/fallback.py`。
- 新增 `LatencyFallbackConfig`。
- 新增 `CalibrationFailurePolicy` / `CalibrationStatus`。
- 新增 `build_latency_fallback_config()`。
- `latency_fallback.on_calibration_failure` 默认值为 `fail`。
- 显式支持 `use_model_default`。
- 未知 fallback policy fail-fast。
- 本批只定义 schema / policy object，不接入真实 calibration harness。
- 本批不捕获 fitted TTFT backend、request build、trace schema、tokenizer、scheduler、cache、replay 错误。

MR-5 验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_latency_fallback.py \
  tests/unit/latency/test_instance_resolver.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py

16 passed
```

MR-6 完成内容：

- `configs/experiments/streaming_capacity_sweep_instance_latency.yaml` 新增 `model_registry.profile_path`。
- 示例 config 新增显式 `latency_fallback.on_calibration_failure`。
- README 写入 model registry、instance binding、calibration fallback 使用边界。
- `docs/core_simulator_technical_plan.md` 写入 streaming runner latency backend 解析优先级。
- `docs/infertwin_product_design.md` 新增 `ModelRegistry` 章节并更新 `InstanceProfile` 语义。
- `docs/archive/pre_step7_model_registry/02_execution.md` 更新 MR-6 执行记录。

MR-6 核心语义：

- `model_registry` 是 `model_name -> ModelProfile / tokenizer profile / default_latency` 索引。
- `instance_latency` 是 `instance_uuid -> model/deployment/optional latency_profile` 绑定表。
- latency backend 解析优先级是 instance profile -> model default -> legacy global backend。
- `latency_fallback` 只用于未来 external calibration failure，且必须显式配置。
- request build / tokenizer / scheduler / cache / replay 错误不能 fallback。
- 动态每 500 条请求重新拟合 TTFT 尚未实现。

MR-6 验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_latency_fallback.py \
  tests/unit/config/test_model_registry.py \
  tests/unit/config/test_model_binding.py \
  tests/unit/latency/test_instance_resolver_model_defaults.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py

30 passed

PYTHONPATH=src .venv/bin/python -m infertwin.cli.main sweep-streaming \
  --config configs/experiments/streaming_capacity_sweep_instance_latency.yaml

passed
```

MR-7 完成内容：

- 完整验证该专项没有破坏现有 replay 能力。
- 确认 replay / cache / scheduler / request / streaming replay 内没有 model registry 或 latency fallback 逻辑。
- 确认 model registry 只影响 config validation、request build context 和 latency backend resolution。
- 确认旧 config 没有 `model_registry` 时，现有测试仍通过。
- 确认 streaming sweep 中 `instance_latency.profile_path` 旧语义保持兼容。
- 专项文档归档到 `docs/archive/pre_step7_model_registry/`。

MR-7 验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest

235 passed

.venv/bin/python -m ruff check src tests scripts
passed

.venv/bin/python -m ruff format --check src tests scripts
150 files already formatted

boundary rg: no matches
git diff --check: passed
```

最近归档专项：

```text
docs/archive/instance_latency_profiles/
```

专项类型：核心仿真器能力设计 + 外围能力。

归档状态：已完成并收口。

完成能力：

- `InstanceProfile / InstanceLatencyProfile` schema / parser。
- `FittedTTFTProfile` 和 `KVLoadLatencyProfile`。
- `InstanceLatencyBackendResolver`。
- streaming runner 按 `instance_uuid` 选择 fitted TTFT backend。
- 缺省无 `instance_latency` 时 fallback 到全局 backend。
- 配置了 instance latency 表但 trace instance 缺失时 fail-fast。
- 外围 `normalize-trace` / Unrouted Trace Normalizer，把无 `instance_uuid` trace 转为单实例 routed trace。

关键语义：

- 多个实例可以共享同一套 deployment / scheduler / cache 配置。
- 即使共享同一套配置参数，不同实例仍允许拥有不同 TTFT 超参数。
- TTFT 拟合窗口默认按每 500 条请求重新拟合一次，但该请求计数器属于实例侧。
- 不允许把同 deployment 实例的请求合并成一个全局或 deployment-level 拟合计数器。
- 请求级 TTFT 长期语义是 `queue_waiting_ms + uncached_prefill_compute_ms + kv_load_ms`。
- 当前 `queue_waiting_ms = 0`，且 queue waiting time 不进入实例 latency profile。
- 当前 `kv_load_ms = 0`，因为只实现 HBM 命中；未来 DDR / remote 命中后由 `kv_load` 超参数控制。
- 核心仿真器输入仍是 routed trace；无实例 id trace 如需单实例 replay，应先通过外围 normalize-trace 生成统一 `instance_uuid`。
- `normalize-trace` v1 只支持输入完全没有 `instance_uuid` 列；如果输入已有 `instance_uuid` 列则 fail-fast，不做 overwrite / fill-empty。
- `normalize-trace` 不是 gateway routing simulation。

归档验证：

```text
PYTHONPATH=src .venv/bin/python -m pytest: 209 passed
.venv/bin/python -m ruff check src tests scripts: passed
.venv/bin/python -m ruff format --check src tests scripts: passed
git diff --check: passed
```

True streaming 专项类型：核心仿真器架构任务。

True streaming 已归档到：

```text
docs/archive/true_streaming/
docs/reviews/true_streaming_core_simulator_review.md
```

True streaming 当前能力：

- `capacity_sweep_streaming` 是显式 opt-in，不改变旧 `capacity_sweep`。
- 新 package CLI：`infertwin sweep-streaming --config <config.yaml>`。
- CSV trace 逐行 request build。
- per-instance JSONL request shard。
- tokenizer-stage long request rejection sidecar。
- `RequestSource` / `JsonlRequestSource`。
- `StreamingBatchAwareReplayEngine.run_instance_stream()`。
- request finish 后释放 active state。
- `CapacitySweepStreamingMetricAggregator`。
- `StreamingCapacitySweepRunner`。
- selected capacity raw cache event dump。
- `scripts/benchmark_streaming_replay.py`，可观察 requests/s、iterations/s、cache_events/s、peak traced memory、RSS 和总耗时。

True streaming 不改变：

- `BatchAwareReplayEngine.run(list[SimulationRequest])`。
- 旧 `CapacitySweepRunner`。
- `batch_aware_hbm_lru` finish-time materialization 语义。
- vLLM-like cached_tokens accounting 口径。
- report/export 只消费 typed result 的边界。

True streaming 当前限制：

- 第一版要求 trace sorted；external sort / unsorted spooling 未实现。
- JSONL shard 保存 prefix block hash chain，长 prompt 下磁盘体积会变大。
- exact percentile 仍保存 TTFT list，百万级 request 需要显式 quantile policy。
- 多实例 replay 当前串行。
- 不解决 progressive block visibility、Decode / TPOT、multi-tier cache 或 cross-instance pooling。

工程优化已归档到：

```text
docs/archive/engineering_optimization/
```

关键收口结论：

- 当前核心仿真器可作为 `fixed-routing, multi-instance isolated, prefill-only, finite HBM LRU` baseline。
- vLLM cached_tokens usage 语义已通过 EO-H 贯穿 replay lookup metrics。
- cache event raw hit 与 report usage cached_tokens 是两个口径。
- 旧 `capacity_sweep` path 仍保留 in-memory accepted request list；true streaming path 已通过 `capacity_sweep_streaming` 提供 opt-in 大 trace 入口。
- finish-time materialization 可能低估长 prefill 期间的 block reuse，这是必须补齐的核心能力，但用户评审确认可放到 Step7 之后通过新 replay/cache mode 实现。
- Decode / TPOT 建模当前保持 pending；只有在存在明确 Decode 建模需求，且目标部署形态是 PD 混部时开启。
- 2026-06-26 已清理未接入主链路、coverage 为 0 的 scaffold / legacy 源码模块；清理不改变 `batch_aware_hbm_lru` replay 能力。未来若需要 lookup table latency、generic cache simulator、hit floor search 等能力，应按新 schema / 新入口重新引入。

工程优化索引：

```text
docs/archive/engineering_optimization/08_core_simulator_closeout_review.md
docs/archive/engineering_optimization/09_eo_h_execution.md
```

Step4-Step6 过程文档已归档：

```text
docs/archive/step4/
docs/archive/step5/
docs/archive/step6/
```

活跃文档只保留主索引和当前状态：

```text
README.md
docs/global_memory.md
docs/code_development_requirements.md
docs/infertwin_product_design.md
docs/core_simulator_technical_plan.md
```

notes 索引：

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
docs/notes/internal_model_deployment_method.md
docs/notes/cached_tokens_calculation_logic.md
```

历史开发状态已归档：

```text
docs/archive/development_status.md
```

## 输入与 Profile 目标形态

用户侧配置目标是轻量化：

- 用户选择 `model_name`，例如 `glm-v5.1`。
- 用户选择 `requested_block_size`。
- trace 路径、输出路径、capacity sweep 候选等仍属于实验入口。

目标输入分层：

- `RunSpec`：一次仿真实验的入口，包含 trace path、output dir、mode、model_name、requested_block_size、capacity sweep 候选和 profile 引用。
- `ModelProfile`：模型固有信息，位于 `configs/models/<model_name>.yaml`。
- `HardwareProfile`：硬件信息，位于 `configs/hardware/<hardware_name>.yaml`。
- `DeploymentProfile`：部署形态、并行策略、启动参数、高级特性，位于 `configs/deployments/<deployment_name>.yaml`。
- `InstanceProfile`：`instance_uuid` 到 deployment profile 的映射，位于 `configs/instances/<cluster_name>.yaml`。

`request_params.model` 是 trace 中真实请求模型。`RunSpec.model_name` 是用户声明的本次仿真模型。单模型 replay 中二者必须相同或命中 `ModelProfile.aliases`；不一致时必须显式失败或进入 `config_guard`，不能静默覆盖。

profile 应覆盖：

- tokenizer、chat template、tool calling 等模型相关设置。
- 硬件信息。
- 是否 PD 分离、PD 配比、并行策略。
- vLLM / vLLM-Ascend 启动参数，例如 `max_num_seqs`、`max_model_len`、`max_num_batched_tokens`、`gpu_memory_utilization`。
- HCCL / 通信缓冲参数，例如 `HCCL_BUFFSIZE`。
- 多级缓存、池化、稀疏注意力等高级特性开关。
- KV cache size 到存储 GB 的换算信息，未来用于 GB 到 block 数转换。

GB / GiB 到 block 数转换是未来外围能力，产品名可叫 `KV Capacity Planner`。它根据 model / deployment / hardware profile、显式 KV cache 容量和 `requested_block_size` 输出 `hbm_capacity_blocks`。第一版只转换显式 KV cache 容量，不自动从整卡 HBM 扣除模型权重、runtime buffer 和碎片。Step6 v1 仍只接受 `hbm_capacity_blocks`。

建议基础公式：

```text
bytes_per_token_per_rank =
  2 * num_layers * num_kv_heads_per_rank * head_dim * kv_dtype_bytes

bytes_per_block =
  requested_block_size * bytes_per_token_per_rank

hbm_capacity_blocks =
  floor(kv_cache_bytes / bytes_per_block)
```

后续技术路线和代码接口应区分三层 block size：

- `requested_block_size`：用户输入或启动参数中的 block size。
- `runtime_block_size`：真实运行时生效值，可能被模型或平台代码覆盖。
- `effective_block_size`：用于 `cached_tokens` 统计的最终值，可能包含 PCP / DCP 倍数和 hybrid cache group LCM 对齐。

Speculative decoding 相关参数属于 deployment profile。`mtp` / `eagle` / `eagle3` 当前按 `speculative_drop_blocks = 1` 理解。后续应按 `cached_blocks = max(matched_blocks - speculative_drop_blocks, 0)` 设计新 replay/cache 语义。在该语义实现前，`speculative.enabled = true` 且 `speculative_drop_blocks > 0` 应被拒绝或进入 `config_guard`。

CP / PCP / DCP、MTP、EAGLE、EAGLE3、runtime block size override、hybrid cache group LCM 对齐都会改变 `cached_tokens`。工程优化已完成第一版 block size / cache block conversion module 和 replay-facing cached_tokens accounting；GB 到 block 转换仍是外围容量转换工具。

`cached_tokens` 计算逻辑笔记：

```text
docs/notes/cached_tokens_calculation_logic.md
```

部署脚本生成 profile config 属于未来外围能力。它可以从单体部署脚本，或 PD 分离部署下的 P 节点脚本、D 节点脚本和 PD 配比中提取配置。该工具只能生成配置，不能改变核心 replay 语义。

Cache 管理和稀疏注意力后续可以参考 Omini cache；具体资料待补充后再进入技术方案。若稀疏注意力改变 block/token 可复用定义，必须新增 cache manager 或 replay mode。

## 已审批边界

- 请求已通过 `instance_uuid` 路由到实例，InferTwin 当前不做路由策略。
- 当前是固定路由、多实例隔离 replay；实例之间 cache 不共享。
- `batch_admission_delay = 0`。
- 当前只建模 prefill TTFT，不建模 TPOT 和 decode KV。Decode / TPOT 建模保持 pending，仅在明确 Decode 建模需求且部署为 PD 混部时开启。
- cache 内部只保存 hash key 和 metadata，不保存全量 token ids，不保存真实 KV tensor。
- tokenizer / chat template 根据请求中的 `model` 字段选择。
- 当前有限 cache 只实现 HBM LRU；DDR LRU 和更多淘汰算法属于后续扩展。
- 报告输出以 CSV + `summary.md` 为主。
- Step6 第一版只 sweep `hbm_capacity_blocks`，不接受 GB 输入。
- Step6 第一版输出 capacity 与指标关系表，不做 P90 target matching。
- Step6 核心 runner 返回结构化 sweep result；InferTwin 表和 `capacity_sweep.csv` 是 report/export 外围能力，不属于 replay core。
- Step6 原始 `capacity_sweep` path 仍是 request build once 并复用 requests；大 trace 使用后续完成的 `capacity_sweep_streaming` opt-in path。
- Step6 cache events 默认不落明细；只允许对指定 capacity dump `cache_events.csv`。
- Step6 `capacity_sweep.csv` 的 trace row 记录 replay-level `cache_event_count`，instance row 固定为 0，表示 v1 不提供 instance-level event count。
- Step6 多实例并行 replay 是后续项；单线程稳定后再新增 `ParallelCapacitySweepRunner` 或显式 execution backend。
- 旧 `implementation_plan.md` 已归档；“输出目标 P90 TTFT 对应的 hit floor” 是外围能力。
- 旧 `future_simulation_extensions.md` 已压缩并入 `infertwin_product_design.md` 的核心仿真器扩展路线。

## Step1-Step6 完成能力

- CSV trace reader。
- strict OpenAI-style request parser。
- tokenizer / chat template registry。
- GLM-5 tokenizer profile。
- hash-only prefix block hasher。
- `SimulationRequest` 构造。
- 无限 HBM prefix cache replay。
- fixed-routing, multi-instance isolated replay。
- vLLM-like continuous batching / chunked prefill replay。
- scheduler schema：`SchedulerConfig`、`RequestState`、`ScheduledSlice`、`BatchShape`。
- latency schema：`ShapeKey`、`LatencyResult`、`BatchLatencyBackend`。
- `FittedTTFTLatencyBackend` / `fitted_ttft`。
- `ShapeMemo`。
- `BatchAwareReplayEngine.run()`。
- first-schedule-time prefix cache lookup。
- bounded waiting lookup frontier。
- zero-miss fast-finish。
- finish-time materialization。
- finite HBM LRU cache with `hbm_capacity_blocks`。
- streaming `cache_events.csv`。
- stateful eviction policy。
- package CLI as formal entrypoint。
- `scripts/` as local wrappers。
- `WaitingQueue` abstraction for scheduler/replay waiting state。
- `scripts/benchmark_replay.py` synthetic replay benchmark harness。
- `CapacitySweepRunner`。
- `CapacitySweepRow` / `CapacitySweepResult`。
- `StatsOnlyCacheEventSink`。
- `capacity_sweep.csv` / `summary.md` report/export。
- `infertwin sweep` package CLI。
- `scripts/run_capacity_sweep.py` wrapper。
- `RunSpec` / profile schema / `ConfigGuard` foundation。
- block size / cache block conversion pure module。
- profile-aware request build path。
- tokenizer-stage long request rejection and `rejected_requests.csv` sidecar。
- `MaterializationPolicy` interface with default `FinishTimeMaterializationPolicy`。
- `ServingLatencyProfile` replay-facing latency composition interface。
- `account_prefix_lookup()` / `AccountedLookupResult`。
- vLLM-like cached_tokens accounting across batch-aware and infinite replay.

最新验证基线：

```text
PYTHONPATH=src .venv/bin/python -m pytest: 199 passed
.venv/bin/python -m ruff check src tests scripts: passed
.venv/bin/python -m ruff format --check src tests scripts: passed
git diff --check: passed
```

Step6 功能验收：

```text
docs/archive/step6/03_acceptance_e2e.md
```

验收结果：

- `hbm_capacity_blocks=3`: kv_hit_rate=0.000000, p90_ttft_ms=15.0。
- `hbm_capacity_blocks=4`: kv_hit_rate=0.876667, p90_ttft_ms=11.0。
- `hbm_capacity_blocks=8`: kv_hit_rate=0.913333, p90_ttft_ms=0.0。
- `run_capacity_sweep elapsed_ms = 659.9 ms`。
- cache event 信号符合预期。
- 未发现外围 InferTwin 表影响核心 replay 语义的风险。

## Frozen Semantics

核心语义以 `README.md` 的 `Core Semantics (Frozen)` 为准。后续如需改变语义，必须新增 py 类型、数据结构、adapter、cache backend 或 replay mode，不能静默修改旧字段含义。

特别注意：

- `batch_size` 是单个 scheduler iteration 内 request slice 数，不是 token batch。
- `max_num_batched_tokens` 是 iteration token budget，不是 batch size。
- `BatchShape` 是 InferTwin scheduler output，不是 AIConfigurator / MkSim 直接输入。
- Cache lookup 发生在 request 第一次进入 scheduler 可考虑范围时，不是 trace arrival 时。
- `batch_aware_hbm_lru` 固定采用 finish-time materialization。
- miss blocks 在 request prefill finish 前不对其他 request 可见。
- `HBMCache` 表示 request finish 后可复用的 prefix cache resident metadata，不是真实 runtime HBM physical block table。

## 仍未实现

- target-based hit floor solver / P90 target matching。
- DDR / SSD / multi-tier cache。
- KV load latency。
- gateway routing simulation。
- instance-side queueing policy simulation。
- external AIConfigurator / MkSim production adapter。
- cross-instance KV pooling。
- progressive block materialization。该项必须补齐，但放到 Step7 后通过新 replay/cache mode 实现。
- physical KV slot allocation、pinned/refcount。
- GB / GiB 到 block 数转换外围工具。

## 工程优化候选

- 旧 `capacity_sweep` path 仍会构造全部 accepted `SimulationRequest`；大 trace 应使用 `capacity_sweep_streaming`。
- 多实例 replay 当前串行执行；后续可设计 parallel execution backend。
- 高 cache pressure 下 raw `cache_events.csv` 可能较大；当前只允许对指定 capacity 开启 event dump，默认使用 stats-only 事件计数。
- `.venv/bin/infertwin` 当前不存在，说明项目尚未在 venv 中 editable/install；已验证 `PYTHONPATH=src python -m infertwin.cli.main ...` 可用。
- finish-time materialization 可能低估长 prefill 请求中的 block reuse。该判断已完成：必须修改，但放到 Step7 后实现。
- runtime block size override、CP、MTP、EAGLE、hybrid cache group 会改变 `cached_tokens`；当前已有 pure conversion module，并已在 EO-H 贯穿 replay lookup metrics。
- 只建模 prefill，不建模 decode / TPOT。该项 pending，仅在明确 Decode 建模需求且部署为 PD 混部时补齐。
- `ServingLatencyProfile` 已提供 replay-facing latency 组合接口；queue / KV-load / decode 的真实 backend 仍待接入。

已清扫：

- `waiting.pop(0)` 性能风险：已新增 `WaitingQueue`，scheduler/replay 内部统一使用该 abstraction。
- benchmark 缺口：已新增 `scripts/benchmark_replay.py`，默认压测 InferTwin replay state machine，不模拟真实硬件。
- 0% coverage scaffold / legacy 源码模块：已删除未接入主链路的旧 cache simulator、旧 instance boundary、旧 lookup latency、旧 search / metrics helper，清理后没有剩余“有语句但 0% coverage”的现行源码模块。
- true streaming 缺口：已新增 request sharding、streaming replay、streaming sweep 和 benchmark harness。

处理这些遗留问题前，需要先明确产品范围、benchmark 规模、语义不变量和接口变化边界。

## 开发约束

- Step3 之后，每个阶段都必须先讨论产品形态，再讨论技术路线，最后才进行代码开发。
- 代码修改前必须先向用户确认。
- 可维护、可测试是第一准则。
- 每阶段必须包含测试和端到端验证。
- 代码开发必须遵守 `docs/code_development_requirements.md`。
