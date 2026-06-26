# EO-F 执行记录：ServingLatencyProfile 接口

## 开发对象

```text
核心仿真器
```

EO-F 不开发新的外围报表能力，不改变 Step1-Step6 已冻结的 replay 语义。

## 背景

Batch D 之后，HitFloor 默认使用 `FittedTTFTLatencyBackend`：

```text
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

这个公式适合作为确定性测试 backend，但后续真实服务延迟会包含更多维度：

- TTFT / prefill compute latency。
- KV load latency。
- 机器侧 queue time。
- TPOT / decode 干扰。
- 部署形态、并行策略、启动参数和外部 simulator 校准结果。

EO-F 先新增 `ServingLatencyProfile` 接口，把这些维度放到统一的 replay-facing backend 后面。

## 本轮实现

新增：

```text
src/hitfloor/latency/profile.py
```

核心类型：

```text
LatencyComponentResult
IterationLatencyComponent
ZeroLatencyComponent
StaticLatencyComponent
ServingLatencyProfile
```

`ServingLatencyProfile` 仍实现 `BatchLatencyBackend` contract：

```text
estimate_iteration(BatchShape) -> LatencyResult
```

当前 replay duration 计算为：

```text
iteration_duration_ms = queue_ms + ttft_ms + kv_load_ms
```

默认状态：

- `ttft_ms` 由内层 `FittedTTFTLatencyBackend` 给出。
- `queue_ms = 0`，并标记为 `queue_modeled = false`。
- `kv_load_ms = 0`，并标记为 `kv_load_modeled = false`。
- `decode_mode = not_modeled_in_current_replay`。
- `tpot_mode = not_modeled_in_current_replay`。

因此，默认 replay 行为不变；EO-F 只是把未来延迟维度放到了显式接口里。

## Factory 接入

新增配置入口：

```yaml
latency:
  backend: serving_latency_profile
  model_name: glm-v5
  hardware_name: ascend910c
  serving_latency_profile:
    profile: glm-v5_ascend910c_serving_v1
    ttft_backend: fitted_ttft
    calibrated_from: manual_default
    calibration_window_requests: 500
  fitted_ttft:
    profile: glm-v5_ascend910c_ttft
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 1.0
    calibrated_from: manual_default
```

约束：

- `latency.backend: fitted_ttft` 仍然可用，旧配置不受影响。
- `serving_latency_profile.ttft_backend` 第一版只支持 `fitted_ttft`。
- 公司内 `AIConfigurator` 和开源 `aiconfigurator_git` 尚未接入生产 replay path。

## aiconfigurator_git 边界

开源仓库已 clone 到：

```text
/home/zhangxiyue/aiconfigurator_git
```

命名约束：

- `AIConfigurator`：公司内版本，未来生产 adapter 边界。
- `aiconfigurator_git`：开源 GitHub 版本，只用于测试、学习和校准实验。

新增：

```text
src/hitfloor/external/aiconfigurator_git.py
```

该模块只做两件事：

- 校验本地 checkout 是否包含 README、CLI guide、Python API 文件。
- 构造开源 `aiconfigurator cli estimate ...` 参数。

它不执行外部命令，也不进入默认 pytest 的重依赖路径。

参考开源接口：

```text
aiconfigurator cli estimate
  --model-path ...
  --system ...
  --backend vllm
  --estimate-mode agg
  --batch-size ...
  --isl ...
  --osl ...
  --tp-size ...
  --pp-size ...
  --prefix ...
```

## 测试

新增/更新：

```text
tests/unit/latency/test_serving_latency_profile.py
tests/unit/latency/test_backend_factory.py
tests/unit/external/test_adapter_boundaries.py
```

覆盖：

- `ServingLatencyProfile` 组合 `queue + fitted_ttft + kv_load`。
- 默认 queue / KV-load 为 0，且明确标记未建模。
- `decode_mode` 当前只能是 `not_modeled_in_current_replay`。
- factory 能构造 `serving_latency_profile`。
- factory 拒绝尚未支持的 `ttft_backend`。
- `aiconfigurator_git` 与公司内 `AIConfigurator` 命名边界隔离。

聚焦测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/latency/test_serving_latency_profile.py \
  tests/unit/latency/test_backend_factory.py \
  tests/unit/external/test_adapter_boundaries.py
```

结果：

```text
14 passed
```

开源 `aiconfigurator_git` 导入探测：

```text
PYTHONPATH=/home/zhangxiyue/aiconfigurator_git/src \
  .venv/bin/python -c \
  "from aiconfigurator.cli.api import cli_estimate; print(cli_estimate.__name__)"
```

结果：

```text
importlib.metadata.PackageNotFoundError: No package metadata was found for aiconfigurator
```

原因：

- 公开仓库使用 Python package metadata 和 maturin/Rust build。
- 只设置 `PYTHONPATH` 不等价于安装 `aiconfigurator` package。
- EO-F 不把开源项目及其重依赖安装进 HitFloor 默认 `.venv`，避免默认测试路径被外部项目污染。

## 后续边界

EO-F 暂不实现：

- 外部 `AIConfigurator` / `aiconfigurator_git` 实时调用。
- Markov-Infer-Sim 实时调用。
- Ramulator2 KV-load 实时调用。
- decode-aware replay。
- TPOT 对 prefill iteration 的干扰。
- 每 N 条请求动态重新拟合 profile。

如果后续要把开源 `aiconfigurator_git` 用作校准实验，应新增独立 experiment/test harness；不能让它成为默认 replay path 的隐式依赖。

## 工程优化收口 Review 结论

### EO-F 做了什么

EO-F 完成的是 `ServingLatencyProfile` 的核心接口搭建，而不是外部仿真器生产接入。

已完成：

- clone 并阅读开源 `aiconfigurator_git` 的 CLI / Python API 边界。
- 明确公司内 `AIConfigurator` 与开源 `aiconfigurator_git` 的命名和职责边界。
- 新增 `ServingLatencyProfile`，作为 replay-facing latency backend 组合接口。
- 保持默认 TTFT 仍由 `FittedTTFTLatencyBackend` 计算。
- 将 queue / KV-load 作为显式 latency component 暴露，但当前默认值为 0ms，并标记为未建模。
- 将 TPOT / decode 标记为 `not_modeled_in_current_replay`，不进入当前 prefill iteration duration。
- 新增 `aiconfigurator_git` reference boundary，只做 checkout 校验和 `estimate` CLI 参数构造。
- 补充单测，验证 profile 组合、factory 构造、命名边界和 unsupported backend guard。
- 跑通 HitFloor 全量测试，确认 Step1-Step6 replay 能力未被 EO-F 破坏。

### EO-F 没有做什么

EO-F 明确没有做：

- 没有把 `aiconfigurator_git` 安装进 HitFloor `.venv`。
- 没有运行 `aiconfigurator cli estimate`。
- 没有把开源 `aiconfigurator_git` 接入默认 replay / runner / report。
- 没有接入公司内 `AIConfigurator`。
- 没有接入 Markov-Infer-Sim。
- 没有接入 Ramulator2 KV-load latency。
- 没有用外部仿真结果拟合 `FittedTTFTLatencyBackend` 参数。
- 没有改变 `batch_aware_hbm_lru` 的 replay 语义。
- 没有实现 decode-aware replay、TPOT 或 decode 对 prefill 的干扰。

### 轻量导入探测失败原因

导入探测失败不是因为没有联网或没有 pip 权限。

本轮只设置了：

```text
PYTHONPATH=/home/zhangxiyue/aiconfigurator_git/src
```

这只能让 Python 找到源码目录，不能生成已安装 package 的 `.dist-info` 元数据。

开源 `aiconfigurator` 的 `__init__.py` 会查询：

```text
importlib.metadata.version("aiconfigurator")
```

由于 HitFloor `.venv` 中没有执行 `pip install -e /home/zhangxiyue/aiconfigurator_git` 或对应 maturin 安装流程，环境里不存在 `aiconfigurator` package metadata，因此抛出：

```text
PackageNotFoundError: No package metadata was found for aiconfigurator
```

后续如果安装依赖，才可能遇到网络、编译或权限问题；这些不是本次轻量导入失败的直接原因。

### 后续 Calibration Harness 的收尾条件

后续如果要做 `aiconfigurator_git` calibration harness，应作为显式 opt-in 工具或实验路径，不进入默认 replay path。

这里的真实 TTFT 对比指：

```text
HitFloor BatchShape / miss tokens / batch size
-> 转换为 aiconfigurator_git estimate 输入
-> 运行 aiconfigurator_git 单点 TTFT 估算
-> 与 HitFloor FittedTTFTLatencyBackend / ServingLatencyProfile 输出对比
```

它不是部署真实模型，也不是在线压测；它是用外部性能估算器校准或验证 HitFloor 的 TTFT 函数。

收尾条件：

- 独立安装或运行 `aiconfigurator_git`，并记录安装方式、依赖版本和失败行为。
- 明确定义 `BatchShape -> aiconfigurator_git estimate` 的字段映射，包括 `batch_size`、`isl`、`osl`、`prefix`、`tp_size`、`pp_size`、`backend`、`system`。
- 明确 HitFloor 的 `batch_size` 与 `aiconfigurator_git --batch-size` 是否同义；如果不同，必须新增 adapter schema，不允许直接混用。
- 使用小规模合成 shape 跑出 TTFT 对比表。
- 输出拟合参数或误差报告，至少包含 HitFloor TTFT、aiconfigurator_git TTFT、绝对误差、相对误差和输入 shape。
- 证明外部工具失败时核心 replay 仍能运行，默认 pytest 不依赖外部工具。
- calibration harness 只更新显式 profile / fitted 参数，不静默改变默认 replay 语义。
