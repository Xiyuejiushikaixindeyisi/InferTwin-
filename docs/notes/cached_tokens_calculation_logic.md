# Cached Tokens Calculation Logic

本文记录当前评审输入中的 `cached_tokens` 计算逻辑，用于后续设计 InferTwin 的 block size / cache block conversion module。

该文档是学习笔记和设计输入，不表示当前 InferTwin 已实现这些语义。

## 基础规则

对于启用了 prefix caching 的普通 full-attention 模型：

```text
max_cache_hit_length = prompt_tokens - 1
effective_block_size = runtime_block_size
cached_tokens = floor(max_cache_hit_length / effective_block_size) * effective_block_size
```

为什么是 `prompt_tokens - 1`：

即使整个 prompt 都已经在 cache 中，模型也必须至少重新计算最后一个 token，才能得到下一个 token 的 logits。代码在查找 cached blocks 之前会设置：

```text
max_cache_hit_length = request.num_tokens - 1
```

为什么要按 block 向下取整：

vLLM 当前以完整 KV block 为单位匹配和调度。partial-block prefix hit 不会被计入可复用的 `cached_tokens`。

## Runtime Block Size

计算时要使用 runtime block size，而不只是 CLI 上的 `--block-size`。最终 block size 可能被模型相关代码修改。

例如，Ascend hybrid Mamba 模型可能会增大 attention block size，使 attention page size 至少等于 Mamba / SSM page size：

```text
Setting attention block size to 768 tokens to ensure that attention page size is >= mamba page size.
```

当出现这条日志时，即使服务启动时传入的是 `--block-size 128`，cache-hit 计算也应使用：

```text
runtime_block_size = 768
```

## CP: PCP 和 DCP

对于 unitary full-attention cache lookup，context parallelism 会增大 hit 统计使用的 effective block size：

```text
effective_block_size =
  runtime_block_size
  * prefill_context_parallel_size
  * decode_context_parallel_size
```

示例：

```text
runtime_block_size = 128
PCP = 2
DCP = 1
effective_block_size = 256
```

```text
runtime_block_size = 128
PCP = 2
DCP = 2
effective_block_size = 512
```

当前 upstream 代码对部分非 full-attention manager 有支持限制：

- Sliding-window attention 要求 PCP 和 DCP 都是 1。
- Mamba attention 要求 PCP 和 DCP 都是 1。
- Hybrid KV-cache coordinator 要求 PCP 和 DCP 都是 1。

## MTP, EAGLE, EAGLE3

在当前 vLLM 代码中，`SpeculativeConfig.use_eagle()` 对以下 method 返回 true：

```python
self.method in ("eagle", "eagle3", "mtp")
```

当 `use_eagle` 为 true 时，full-attention cache lookup 会丢弃最后一个已匹配 block：

```text
cached_blocks = max(matched_blocks - 1, 0)
```

因此，对于启用了 MTP / EAGLE / EAGLE3 的普通 full-attention 模型：

```text
matched_blocks = floor((prompt_tokens - 1) / effective_block_size)
cached_tokens = max(matched_blocks - 1, 0) * effective_block_size
```

这意味着即使第二次请求和第一次请求完全相同，并且第一次请求已经完成，MTP 仍然可能让 `cached_tokens` 少一个 effective block。

## Qwen3.5, Qwen3.6, Qwen3-Next 和 Mamba / Hybrid 模型

对于 Qwen3.5 / Qwen3.6 / Qwen3-Next 这类 hybrid Mamba 模型，额外需要注意 runtime block-size override。vLLM-Ascend 会把 attention page size 与 Mamba / SSM page size 对齐，并可能增大 `cache_config.block_size`。

实用判断步骤：

1. 检查启动日志中是否有 `Setting attention block size to ...`。
2. 使用日志里的值作为 `runtime_block_size`。
3. 如果启用了并且当前路径支持 CP，则应用 CP 倍数。
4. 如果 `use_eagle()` 为 true，则应用 MTP / EAGLE / EAGLE3 的 one-block drop。

对于不同 cache group 使用不同 block size 的 hybrid 模型，vLLM 会把返回的 hit length 对齐到各 group block size 的最小公倍数。

这会让 `cached_tokens` 低于简单的单 block 公式。coordinator 的注释说明，由于当前不支持 partial-block cache hit，cache hit length 必须同时是每种 attention 类型 block size 的倍数。

## GLM5, DSA, SFA, MLA

当前评审输入中，没有在 scheduler 路径中发现 GLM5 / DSA 专属的 `cached_tokens` 调整。请求级 usage 仍然来自 scheduler 的 `num_cached_tokens`，所以仍遵循同一套 KV-cache manager 规则。

对于 GLM / DSA / SFA / MLA 场景，需要检查：

- 最终 runtime block size。
- 模型是 unitary 还是 hybrid。
- 是否启用了 DCP 或 PCP。
- speculative `method` 是否为 `mtp`、`eagle` 或 `eagle3`。

因此，不应因为 GLM5 使用 DSA attention kernel 就假设它有不同的 usage 公式。除非 runtime config 中出现了 KV-cache block size 改变、hybrid cache groups、CP 或 speculative mode，否则可以把 DSA / SFA / MLA 视为 attention 实现或模型细节。

## PD Colocated 和 KV Transfer

单节点 PD colocated 本身不会改变 local prefix-cache block matching。

如果启用了 external KV transfer 或 KV connector，metrics 会拆分 local 和 external cache stats。完成请求的 prompt-token accounting 可以按下面的不变量报告 local 加 external 的 cached tokens：

```text
local_cache_hit + external_kv_transfer - recomputed_tokens = cached_tokens
```

对于没有 external KV 的 colocated 运行，观测到的 `cached_tokens` 应该遵循 local cache lookup 规则。

## InferTwin 设计含义

未来 InferTwin 需要一个 block size / cache block conversion module，用于把部署 profile 和 runtime 观测转换为 cache lookup 使用的 effective block 语义。

术语应区分：

- `requested_block_size`：用户输入或启动参数中的 block size。
- `runtime_block_size`：真实运行时生效值，可能被模型或平台代码覆盖。
- `effective_block_size`：用于 `cached_tokens` 统计的最终值，可能包含 PCP / DCP 倍数和 hybrid cache group LCM 对齐。

该模块至少应处理：

- `requested_block_size` 与 `runtime_block_size` 的差异。
- PCP / DCP 对 effective block size 的放大。
- MTP / EAGLE / EAGLE3 的 one-block drop。
- hybrid cache groups 的 LCM 对齐。
- unsupported manager + CP 组合的 `config_guard`。

在该模块实现前，InferTwin 不应静默接受会改变 cached_tokens 语义的部署配置。
