# EO-D 执行记录

## 范围

本轮开发对象是：

```text
核心仿真器工程优化
```

本轮目标是 `profile-aware request build integration`。它把 `RunSpec` / profile / block conversion 作为可选上下文接入 request build，同时保持 legacy config 行为兼容。

## 新增代码

新增：

```text
src/hitfloor/request/build_context.py
```

核心类型：

```text
RequestBuildContext
```

职责：

- 管理 legacy request build context。
- 管理 profile-aware request build context。
- 根据 `RunSpec`、`ModelProfile`、`DeploymentProfile` 构造 `BlockSizeResolution`。
- 对 trace request model 执行 `ConfigGuard` 检查。
- 计算 request-level `CacheBlockConversionResult`。

## 修改代码

修改：

```text
src/hitfloor/instance/request.py
src/hitfloor/experiment/request_builder.py
tests/unit/experiment/test_request_builder.py
```

`SimulationRequest` 新增可选 metadata：

```text
requested_block_size
runtime_block_size
effective_block_size
block_conversion_result
```

这些字段是可选的，手工构造测试请求时可以不传。

## Legacy 兼容性

不带 `run` section 的旧 config 继续走 legacy context：

```text
cache.block_size_tokens
-> requested_block_size
-> runtime_block_size
-> effective_block_size
```

因此 Step1-Step6 现有 replay / sweep 路径不需要配置 profile，也不会被强制走新 schema。

## Profile-Aware 路径

带 `run` section 时，request builder 使用：

```text
RunSpec.trace_path
RunSpec.requested_block_size
RunSpec.model_profile
RunSpec.deployment_profile
```

并加载：

```text
ModelProfile
DeploymentProfile
```

随后执行：

```text
ConfigGuard
BlockSizeResolver
CacheBlockConversionPolicy
```

tokenizer 默认 profile 优先级：

1. `tokenizers.default_profile`
2. `ModelProfile.tokenizer_profile`

request model 必须匹配 `RunSpec.model_name`、`ModelProfile.name` 或 `ModelProfile.aliases`。即使配置了 tokenizer default profile，未知 request model 也会被 `ConfigGuard` 阻止。

## 测试覆盖

新增 / 扩展：

```text
tests/unit/experiment/test_request_builder.py
```

覆盖：

- legacy config 构造出的 request 仍可用，且三层 block size 相等。
- profile-aware path 能使用 alias request model。
- profile-aware path 能把 runtime block size 和 CP 转成 effective block size。
- profile-aware path 会写入 `CacheBlockConversionResult`。
- request model mismatch 会在 tokenization 前被 `ConfigGuard` 阻止。

## 验证

本轮验证通过：

```text
pytest: 132 passed
ruff check src tests scripts: passed
ruff format --check src tests scripts: passed
```

## 后续

建议下一轮进入 EO-E：

```text
Materialization policy interface
```

EO-E 仍应保持 `batch_aware_hbm_lru` 默认绑定 finish-time materialization，不直接启用 progressive block visibility。
