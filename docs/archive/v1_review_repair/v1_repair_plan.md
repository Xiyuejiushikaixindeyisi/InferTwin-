# InferTwin V1 修复方案

状态：方案已审批，RP-A 到 RP-H 已完成并归档。

来源：

- `docs/reviews/infertwin_project_review.md`
- 用户对 InferTwin 项目评审意见的二次确认

说明：

用户评审意见第 2 点标题写成了 `streaming.require_sorted_trace=false`，但解决方案是 “`registry.yaml` 里的相对路径默认相对 `registry.yaml` 自己所在目录”。本方案按 model registry 相对路径修复处理；`streaming.require_sorted_trace=false` 的风险由第 4 点单独处理。

## 1. 修复目标

本轮属于核心仿真器 V1 可靠性修复，外加模型绑定运行参数兜底设计。

本轮不是大规模重构。核心设计是轻量表驱动：

```text
ModelRuntimeRegistry:
  model_name -> model config / tokenizer / deployment / default cache / default TTFT

InstanceBindingTable:
  instance_uuid -> model_name + optional instance TTFT profile
```

`ModelRuntimeRegistry` 可以由现有 `configs/models/registry.yaml` 扩展得到；`InstanceBindingTable` 复用现有 instance profile。代码只需要把这两张表接入 streaming request build、scheduler/cache factory 和 latency resolver 的边界。

本轮 repair 不新增 gateway routing、不实现 DDR / SSD / remote KV、多实例池化、不新增 progressive block visibility、不实现 decode / TPOT。它的目标是让 V1 后续 Step7-Step9 能在更稳定的 schema 和 replay 边界上开发。

V1 核心仿真器准出范围：

1. Step7：单实例池化。单个实例可以在 DDR/CPU 侧额外 KV cache 存储中命中。
2. Step8：KV load latency。为 DDR/CPU 等非 HBM 命中增加加载时延建模。
3. Step9：progressive chunk visibility。chunk 生成后即可成为后续请求的 KV cache hit 候选，不再等待整个 prompt prefill 完成；TTFT prefill 时间由多个 uncached-token chunk 组合。

V2 之后再处理：

- 复杂 Hybrid 模型，例如 Qwen3.6、DeepSeekV4。
- gateway simulation。
- 实例侧排队。
- 多实例池化 / 跨实例 KV 命中。
- Decode / TPOT。
- V1 准出后的新一轮工程优化。

V1 准出前，不新增新的外围能力。

本轮完成后，InferTwin V1 repair 应满足：

- routed trace 缺失或空 `instance_uuid` 时，核心 reader fail-fast。
- model registry 中的相对路径稳定相对 registry 文件所在目录解析。
- V1 禁止 `streaming.require_sorted_trace=false`，避免 shard 内乱序导致 replay 时间线错误。
- model registry 可以维护每个模型的默认运行参数，包括 default latency、deployment、scheduler、cache、block-size、pooling flags。
- instance 绑定到 model 后，除 instance-specific TTFT 外，可复用该 model 的默认运行参数。
- 如果未接入外部 TTFT 仿真器、显式关闭 TTFT 拟合、外部 TTFT 仿真器失败，或没有实例专属 TTFT profile，则可使用 model default TTFT 继续 replay。

## 2. 明确不做

### 2.1 V1 不修 `validate-trace` 全量读内存

当前 `validate-trace` 会把 trace 读入 list。该问题确认作为 V1 遗留工程问题，在 V2 工程阶段处理。

V2 预期方向：

- 改成 streaming validation。
- 支持大 trace 的低内存统计。
- 根据需要限制高基数 tenant / instance set 的内存。

### 2.2 V1 不支持外部排序 / shard sort

V1 直接禁止：

```yaml
streaming:
  require_sorted_trace: false
```

V2 之后如果需要支持乱序 CSV，应新增外部排序或 per-instance shard sort 能力。

### 2.3 V1 不解决 Hybrid 模型完整 KV cache 语义

Qwen3.6、DeepSeekV4 等 Hybrid 模型会打破 full-attention prefix cache 的 block 假设。该问题作为 V2 之后核心仿真器专项遗留问题，详见：

```text
docs/archive/v1_review_repair/hybrid_model_debt_note.md
```

### 2.4 V1 不改变已冻结 replay mode 语义

`batch_aware_hbm_lru` 仍绑定 finish-time materialization。

如果后续实现 progressive block visibility，应新增 mode，例如：

```text
batch_aware_hbm_lru_progressive
```

## 3. 目标代码结构

计划新增或修改的主要文件：

```text
src/infertwin/trace/reader.py
src/infertwin/trace/schema.py

src/infertwin/config/model_registry.py
src/infertwin/config/model_binding.py
src/infertwin/config/model_runtime.py        # 新增
src/infertwin/config/validation.py
src/infertwin/config/__init__.py

src/infertwin/latency/instance_resolver.py

src/infertwin/experiment/request_builder.py
src/infertwin/request/build_context.py

src/infertwin/streaming/build.py
src/infertwin/streaming/sweep.py

configs/models/registry.yaml
configs/models/glm-v5.1.yaml                 # 如需补充模型默认信息
configs/deployments/glm-v5.1-vllm-ascend-prefill.yaml
configs/experiments/streaming_capacity_sweep_instance_latency.yaml

tests/unit/test_trace_reader.py
tests/unit/config/test_model_registry.py
tests/unit/config/test_model_binding.py
tests/unit/config/test_model_runtime.py       # 新增
tests/unit/streaming/test_build.py
tests/integration/test_true_streaming_capacity_sweep_runner.py
```

说明：

- `config/model_runtime.py` 负责将 model registry、deployment profile、cache defaults、scheduler defaults 整理成可供 runner 使用的 typed runtime profile。
- `latency/instance_resolver.py` 仍只负责 TTFT backend，不承载 scheduler/cache/deployment 逻辑。
- `streaming/sweep.py` 是 V1 模型运行参数兜底的主集成入口。
- legacy `simulate` / non-streaming `sweep` 保持全局配置语义，除非后续单独审批改造。

## 4. Schema 设计

### 4.1 Model Registry Entry

扩展 `configs/models/registry.yaml`。

建议 V1 schema：

```yaml
models:
  glm-v5.1:
    model_profile_path: glm-v5.1.yaml
    deployment_profile_path: ../deployments/glm-v5.1-vllm-ascend-prefill.yaml
    tokenizer_profile: glm-v5
    chat_template_profile: glm-v5

    default_cache:
      hbm_capacity_blocks: 4096
      eviction_policy: lru
      block_size_tokens: 128

    default_latency:
      backend: fitted_ttft
      model_name: glm-v5.1
      hardware_name: ascend-a3-example
      fitted_ttft:
        profile: glm-v5.1_default_ttft
        function: token_linear_v1
        intercept_ms: 0.0
        ms_per_uncached_token: 0.01
        calibrated_from: default_registry
        calibration_window_requests: 500
      kv_load:
        ddr_ms_per_cached_token: 0.0
        remote_ms_per_cached_token: 0.0
```

路径规则：

- 绝对路径按绝对路径解析。
- 相对路径默认相对 `registry.yaml` 所在目录解析。
- 不再依赖运行命令时的 cwd。

迁移注意：

当前示例：

```yaml
model_profile_path: configs/models/glm-v5.1.yaml
```

如果改为 registry-relative，应迁移为：

```yaml
model_profile_path: glm-v5.1.yaml
```

### 4.2 Deployment Profile

已有 `DeploymentProfile` 继续承载：

- scheduler：
  - `max_num_seqs`
  - `max_num_batched_tokens`
  - `enable_chunked_prefill`
  - `long_prefill_token_threshold`
- parallel：
  - TP
  - PP
  - PCP
  - DCP
- speculative：
  - MTP / EAGLE / EAGLE3 drop blocks
- cache_features：
  - `prefix_caching`
  - `runtime_block_size`
  - pooling flags

V1 建议扩展 `cache_features` 的 pooling 表达，但只作为 metadata / guard：

```yaml
cache_features:
  prefix_caching: true
  runtime_block_size: 128
  pooling:
    single_instance: false
    multi_instance: false
```

兼容当前旧字段：

```yaml
pooling: false
```

V1 行为：

- `single_instance=false` 且 `multi_instance=false`：允许 replay。
- `single_instance=true`：在本轮 repair 和 Step7 实现前 fail-fast，提示单实例池化 / DDR cache backend 尚未实现；Step7 完成后打开该能力。
- `multi_instance=true`：fail-fast，提示多实例池化 / cross-instance hit 属于 V2。

### 4.3 Instance Binding

已有 instance profile 继续表达：

```yaml
instances:
  name: local-fixed-route-latency-example
  items:
    instance-a:
      deployment: glm-v5.1-vllm-ascend-prefill
      model_name: glm-v5.1
      latency_profile: instance-a-ttft
    instance-b:
      deployment: glm-v5.1-vllm-ascend-prefill
      model_name: glm-v5.1
```

V1 语义：

- `latency_profile` 存在：使用 instance-specific TTFT。
- `latency_profile` 不存在：使用 `model_name` 对应 model registry 的 `default_latency`。
- scheduler/cache/deployment/block-size 默认取 `model_name` 对应 model registry 和 deployment profile。

### 4.4 Capacity Sweep 与 Model Default Cache 的关系

`capacity_sweep_streaming` 中：

```yaml
sweep:
  hbm_capacity_blocks: [512, 1024, 2048]
```

仍是实验变量，会在本次 replay runtime 中覆盖 model default 中的 `default_cache.hbm_capacity_blocks`。

这里的“覆盖”不是修改 `registry.yaml` 文件，也不是改变模型默认部署配置；它只是在 capacity sweep 这个外围能力运行期间形成 runtime override：

```text
effective_hbm_capacity_blocks = current sweep candidate
```

原因：

- capacity sweep 的产品目标就是观察不同 HBM block 容量下的 hit rate 和 P90 TTFT。
- model default cache size 用于记录生产 baseline 和非 sweep replay 默认值。
- 一般 capacity sweep 只针对相同模型的一组实例，观察该模型 cache capacity 变化对指标的影响，改动范围较小。

V1 规则：

- sweep mode：capacity candidate > model default capacity，且只在本次 replay runtime 生效。
- 非 sweep / 未来 direct streaming replay：使用 model default capacity。
- report / summary 应记录 model default capacity 和本次 sweep candidate，方便区分生产 baseline 与实验变量。

V1 不处理复杂 multi-model sweep 语义，例如一个 trace 中多个 model 的默认 cache capacity 不同，而用户给出同一组 absolute block candidates。该问题如出现，应在后续设计中明确 candidate 是绝对容量、相对倍率，还是按 model 分组 sweep。

## 5. Batch 开发顺序

### RP-A：Trace Schema Guard

类型：核心仿真器修复。

目标：

- 核心 reader fail-fast 拒绝空 `instance_uuid`。
- 同时收紧 documented required fields：`request_id`、`tenant_id`、`request_params`、`service_start_time`。

修改：

- `src/infertwin/trace/reader.py`
  - 增加 `_required_non_empty(row, column, path, line_number)`。
  - `datetime.fromisoformat()` 包装成带文件行号的 ValueError。
  - 空 `instance_uuid` 错误信息明确提示：核心仿真器需要 routed trace；如确认不做 gateway routing，先用 `infertwin normalize-trace`。

测试：

- 空 `instance_uuid` 失败。
- 空 `request_id` / `tenant_id` 失败。
- 空或非法 timestamp 失败。
- 正常 trace 仍通过。

验收：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trace_reader.py tests/integration/test_trace_normalizer_cli.py
```

### RP-B：Registry-Relative Model Paths

类型：核心配置修复。

目标：

- model registry 中的相对路径相对 registry 文件所在目录解析。
- 消除 cwd 依赖。

修改：

- `src/infertwin/latency/instance_resolver.py`
  - `_load_model_registry(profile_path)` 调用 `validate_model_registry(..., base_dir=profile_path.parent)`。
- `src/infertwin/config/model_registry.py`
  - 继续保存原始 path 或新增 resolved path，不强行改写用户输入。
- `src/infertwin/config/model_binding.py`
  - 保持 `_resolve_profile_path(path, base_dir)` 逻辑。
- `configs/models/registry.yaml`
  - 将 `model_profile_path` 从 cwd-relative 示例改成 registry-relative 示例。

测试：

- 在临时目录下创建：
  - `registry.yaml`
  - 同目录或相对目录的 model profile
  - 使用 `monkeypatch.chdir()` 切到其他目录后，仍能解析。
- 绝对路径仍可解析。
- 缺失 model profile path 报错清晰。

验收：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_model_binding.py tests/unit/latency/test_instance_resolver_model_defaults.py
```

### RP-C：Streaming Sorted-Trace Guard

类型：核心 streaming replay 安全修复。

目标：

- V1 禁止 `streaming.require_sorted_trace=false`。
- 外部排序 / shard sort 作为 V2 能力。

修改：

- `src/infertwin/streaming/sweep.py`
  - `build_streaming_capacity_sweep_config()` 中如果 `require_sorted_trace` 为 false，直接 `ValueError`。
- `src/infertwin/streaming/build.py`
  - `StreamingRequestShardBuilder.__init__` 或 `build()` 中也拒绝 false，防止绕过 runner 直接调用 builder。

错误信息建议：

```text
streaming.require_sorted_trace=false is not supported in InferTwin V1; sort trace by (service_start_time, instance_uuid, request_id) or add a future shard-sort stage.
```

测试：

- config 层 false 失败。
- builder 直接传 false 失败。
- true 正常。
- 缺省仍为 true。

验收：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/streaming/test_build.py tests/integration/test_true_streaming_capacity_sweep_runner.py
```

### RP-D：Model Runtime Defaults Schema

类型：核心配置能力。

目标：

- model registry 能表达每个 model 的默认运行参数。
- 不把 scheduler/cache/deployment 塞进 latency resolver。
- 控制改造范围：新增 model runtime defaults 表和 resolver，不重写现有 replay state machine。

新增：

```text
src/infertwin/config/model_runtime.py
```

建议类型：

```python
ModelCacheDefaults
ModelRuntimeDefaults
ResolvedModelRuntimeProfile
```

职责：

- 从 `ModelRegistryEntry`、`ModelProfile`、`DeploymentProfile` 组合出运行态 profile。
- 作为 `model_name -> runtime defaults` 的轻量表。
- 输出：
  - model name
  - tokenizer profile
  - deployment profile
  - scheduler config
  - cache defaults
  - block-size / CP / MTP 相关上下文
  - pooling flags
  - default latency profile

不负责：

- 不做 replay。
- 不做 TTFT backend 估算。
- 不写 report。

schema 校验：

- `default_cache.hbm_capacity_blocks` 必须为正整数。
- `default_cache.eviction_policy` V1 只允许 `lru`。
- `default_cache.block_size_tokens` 必须为正整数。
- `deployment_profile_path` 必须存在且可解析。
- `model_profile_path` 与 registry entry name 必须一致。
- deployment profile 中 pooling 开启但对应 backend 未实现时，ConfigGuard fail-fast。

测试：

- registry 可以解析 `default_cache`。
- 缺失 `deployment_profile_path` 失败或在 legacy schema 下给出明确兼容策略。
- 非 lru policy 失败。
- pooling true 触发 guard。

### RP-E：Instance Runtime Resolver

类型：核心配置解析。

目标：

建立：

```text
instance_uuid -> model_name -> ResolvedModelRuntimeProfile
```

建议新增：

```python
InstanceRuntimeResolver
```

职责：

- 根据 instance profile 的 `model_name` 找 model registry entry。
- 返回该 instance 的 scheduler/cache/deployment/block-size/runtime defaults。
- 与 `InstanceLatencyBackendResolver` 分工：
  - RuntimeResolver：scheduler/cache/deployment/model defaults。
  - LatencyBackendResolver：TTFT backend。

fallback 规则：

1. instance 有 dedicated latency profile：TTFT 使用 instance profile。
2. instance 没有 dedicated latency profile，但有 model registry：TTFT 使用 model default。
3. 外部 TTFT 仿真器未接入 / 显式关闭拟合 / 本轮不想重新拟合：使用 model default。
4. 外部 TTFT 仿真器超时、失败或返回无效校准结果：使用 model default，并在 metadata / report 中标记 `latency_source=fallback_model_default` 或等价来源。
5. fallback 只兜底 TTFT 仿真器 / 校准链路，不兜底 trace schema、tokenizer、request JSON、instance/model binding、scheduler/cache 配置错误。
6. 缺少 instance -> model 绑定：fail-fast。

测试：

- 多个实例共享同一个 model runtime profile。
- 两个实例使用同一个 model，但不同 instance TTFT profile，能够得到不同 TTFT backend。
- 实例无 TTFT profile 时使用 model default。
- 外部 TTFT 拟合显式关闭或失败时，使用 model default，并能在 metadata 中解释 fallback 来源。
- 缺失 instance 绑定 fail-fast。

### RP-F：Streaming Runner Integration

类型：核心 streaming 主链路集成。

目标：

让 `capacity_sweep_streaming` 主链路使用 instance runtime resolver。

设计原理：

- `sweep-streaming` 已经是大 trace 主路径，天然按 `instance_uuid` 分 shard。
- per-instance shard 适合接入 `instance_uuid -> model_name -> runtime defaults`。
- legacy `simulate` / non-streaming `sweep` 保持小 trace、debug、回归测试入口，暂不承载 V1 模型绑定运行参数主链路。

修改：

- `src/infertwin/streaming/build.py`
  - request build 时按 row 的 `instance_uuid` / `model` 选择对应 `RequestBuildContext`。
  - 如果 request model 与 instance model binding 不一致，fail-fast。
  - tokenizer profile 优先来自 model registry / ModelProfile。
- `src/infertwin/streaming/sweep.py`
  - 每个 shard 创建 scheduler 时，使用该 shard instance 的 runtime profile scheduler。
  - 每个 shard 创建 HBM cache 时：
    - sweep mode 使用当前 capacity candidate；
    - eviction policy 来自 model default，目前只允许 lru；
    - model default capacity 记录到 config details。
  - latency backend 继续由 `InstanceLatencyBackendResolver.backend_for(instance_uuid)` 提供。

注意：

- V1 主集成目标是 `sweep-streaming`。
- legacy `simulate` / non-streaming `sweep` 保持全局配置语义，文档中明确为小 trace / debug / regression path。
- 影响：新模型绑定运行参数能力第一版只保证 streaming path 完整支持；旧入口不会被破坏，但也不会立刻获得完整 per-model runtime defaults。

测试：

- 两个实例共享同一 model runtime profile，但 TTFT profile 不同。
- 两个实例无 dedicated TTFT，其中一个使用 model default。
- request model 与 instance model mismatch 失败。
- scheduler 参数来自 model deployment，而非全局 scheduler。
- sweep capacity 覆盖 model default HBM capacity。

### RP-G：Tests / Docs / E2E

类型：工程验收。

测试矩阵：

```bash
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m ruff format --check src tests scripts
PYTHONPATH=src .venv/bin/python -m pytest
```

端到端合成测试：

- 构造 2 个 model：
  - `glm-v5.1`
  - `glm-v5.1-alt` 或测试用 dummy model
- 构造 3 个 instance：
  - instance-a：model A + dedicated TTFT
  - instance-b：model A + model default TTFT
  - instance-c：model B + model default TTFT
- capacity sweep 至少 2 个容量。
- 验证：
  - request build accepted / rejected 数正确。
  - latency source by instance 正确。
  - scheduler config by instance 生效。
  - capacity candidate 覆盖 default cache capacity。
  - `capacity_sweep.csv` trace / instance row 不变。

文档更新：

- `README.md`
- `docs/infertwin_product_design.md`
- `docs/core_simulator_technical_plan.md`
- `docs/development_governance.md`
- `docs/global_memory.md`

执行记录：

```text
docs/archive/v1_review_repair/03_rp_g_acceptance.md

PYTHONPATH=src .venv/bin/python -m pytest: 260 passed
.venv/bin/python -m ruff check src tests scripts: passed
.venv/bin/python -m ruff format --check src tests scripts: passed
git diff --check: passed
```

### RP-H：Engineering Closure

类型：工程收口。

完成条件：

- 所有 RP-A 到 RP-G 测试通过。
- 新增或修改的 schema 都有文档。
- V1 / V2 边界写入主文档。
- `docs/v1_review_repair/` 移入 `docs/archive/v1_review_repair/`。
- 更新全局记忆。
- 如用户要求，进行一次 git 版本整理。

执行记录：

```text
docs/archive/v1_review_repair/04_rp_h_closure.md
```

## 6. 设计原则与边界控制

### 6.1 模型绑定运行参数采用轻量表驱动

模型绑定运行参数不是大规模重构。V1 repair 只增加和接入两张表：

```text
ModelRuntimeRegistry:
  model_name -> model config / tokenizer / deployment / default cache / default TTFT

InstanceBindingTable:
  instance_uuid -> model_name + optional instance TTFT profile
```

需要控制的是接入点，而不是表本身：

- request build 读取 tokenizer / block-size / model guard。
- scheduler factory 读取 model deployment scheduler defaults。
- cache factory 读取 model default cache 和本次 sweep runtime override。
- latency resolver 读取 instance TTFT 或 model default TTFT。
- ConfigGuard 读取 MTP / PCP / DCP / pooling flags。
- report 记录实际使用的 runtime source。

控制策略：

- 先完成 RP-A / RP-B / RP-C 三个小修复。
- RP-D 到 RP-F 只接入 `sweep-streaming` 主路径。
- legacy `simulate` / non-streaming `sweep` 不强行同步改造，避免破坏已有小 trace 测试。

### 6.2 Capacity Sweep 使用 runtime override

capacity sweep 是外围能力，其目标就是修改 cache capacity 这个实验变量。它不会持久修改 model default cache。

语义：

```text
model default cache = 生产/默认部署 baseline
sweep.hbm_capacity_blocks = 本次外围能力的实验变量
effective_hbm_capacity_blocks = current sweep candidate
```

影响：

- `capacity_sweep.csv` 中的 `hbm_capacity_blocks` 表示本次 replay 使用的实验容量。
- summary / config details 应记录 model default capacity，便于对比 baseline。
- V1 默认按同模型 sweep 设计；多模型同时 sweep 的绝对容量 / 相对倍率语义留到后续单独设计。

### 6.3 Pooling Flag 的阶段行为

pooling true 的行为按阶段区分：

```text
repair 阶段:
  pooling.single_instance=true -> fail-fast
  pooling.multi_instance=true  -> fail-fast

Step7 完成后:
  pooling.single_instance=true -> 使用单实例 DDR/CPU pooling backend

V2 之后:
  pooling.multi_instance=true  -> 使用跨实例 pooling / remote hit backend
```

这样可以避免用户把尚未实现的 DDR / remote hit 当作已生效能力。

### 6.4 外部 TTFT 仿真器 fallback 是受控兜底

外部 TTFT fallback 不是异常现象，而是防止 replay 因外部 TTFT 仿真器未接入、显式关闭、超时或失败而无法继续的兜底方法。

允许 fallback：

- 外部 TTFT 仿真器未接入。
- 显式关闭 TTFT 拟合。
- 本轮不重新拟合。
- 外部 TTFT 仿真器超时 / 失败 / 返回无效校准结果。
- instance 没有 dedicated TTFT profile，但 model registry 有 default TTFT。

不允许 fallback 掩盖：

- trace schema 错误。
- tokenizer 失败。
- request JSON 不合法。
- instance/model binding 缺失。
- scheduler/cache 配置非法。

fallback 生效时，应在 metadata / summary 中显式标记来源，避免用户误以为使用了真实外部 TTFT 仿真器结果。

### 6.5 Registry-Relative Path 是配置自洽规则

`registry.yaml` 示例路径迁移为 registry-relative：

```yaml
model_profile_path: glm-v5.1.yaml
deployment_profile_path: ../deployments/glm-v5.1-vllm-ascend-prefill.yaml
```

设计原理：

- 相对路径相对 `registry.yaml` 自己所在目录解析。
- 从任意 cwd 运行都稳定。
- 配置目录整体搬迁时仍自洽。
- 同事用绝对 config path 运行时不会误解析到当前 shell 目录。

影响：

- 示例配置路径会从 repo-root-relative 改成 registry-relative。
- 绝对路径继续支持。
- 仿真语义不变，只提升配置解析稳定性。

## 7. 已确认设计原则

本轮按以下原则进入代码开发：

1. `sweep-streaming` 是 V1 模型绑定运行参数的主集成入口；legacy `simulate` / non-streaming `sweep` 保持全局配置，作为小 trace / debug / regression path。
2. `default_cache.hbm_capacity_blocks` 是 model 默认部署 baseline；capacity sweep 运行时用 `sweep.hbm_capacity_blocks` 作为 runtime override，不修改 registry 文件。
3. pooling true 在 repair 阶段 fail-fast；Step7 打开单实例 pooling；多实例 pooling 留到 V2。
4. `registry.yaml` 示例路径迁移为 registry-relative，例如 `glm-v5.1.yaml`。
5. 外部 TTFT fallback 是受控兜底机制，不是静默忽略错误；它只兜底 TTFT 仿真器 / 校准链路，并必须在 metadata / summary 中解释来源。
