# Hybrid 模型 KV Cache 遗留问题备忘

状态：V2 之后遗留问题，不进入本轮 V1 修复。

## 背景

Qwen3.6、DeepSeekV4 等 Hybrid 模型会打破 InferTwin 当前很多 full-attention prefix cache 简化假设。

当前 V1 主假设是：

- 一个 prompt 可以切成连续 token block。
- 每个 block 在所有层上代表同一段 token。
- prefix cache 命中可以按完整 block 连续匹配。
- `cached_tokens` 可以通过 runtime block size、CP、MTP/EAGLE drop 等规则折算。

Hybrid 模型可能不满足这些假设。

## 关键破坏点

### 1. 不是所有层都按 per-token KV 可拼接方式存储

Full-attention KV cache 通常可以理解为按 token 维度连续追加和复用。

Hybrid / Mamba / SSM 相关结构可能包含不同形态的 state，不一定能用同一套“每层 KV 都是 token block”的模型表达。

### 2. 不同 cache group 的 block size 可能不一致

Hybrid KV-cache coordinator 可能同时维护多种 cache group。

这会破坏“所有层的一个 block 代表同一段 token”的假设。

即使某些 group 命中，最终可报告的 `cached_tokens` 也可能需要对齐到多个 group block size 的最小公倍数，甚至在部分路径上不支持 partial-block hit。

### 3. Prefix cache 命中不再只是简单的 hash-chain lookup

当前 InferTwin 的 hash-only prefix block 设计适合 full-attention 逻辑 prefix cache：

```text
parent_hash + model + tenant/cache_scope + content_hash -> block_key
```

Hybrid 场景可能需要：

- cache group 维度的 block metadata。
- group-level hit / miss / materialization。
- group-level block size conversion。
- 更细粒度的 unsupported guard。

## V1 处理方式

V1 不实现 Hybrid 模型完整 KV cache replay。

V1 只做：

- full-attention 模型路径继续支持。
- 已有 `cache_family=hybrid` 的 block-size resolver 保持 guard / limited accounting 用途。
- 如果模型配置声明了当前 replay 不支持的 Hybrid 语义，ConfigGuard 应 fail-fast，而不是给出不可信强结果。

## V2 之后建议

后续需要单独设计：

- `CacheGroupProfile` 的运行态语义。
- group-level prefix lookup result。
- group-level materialization policy。
- Hybrid cached_tokens accounting。
- vLLM / vLLM-Ascend Hybrid KV-cache coordinator 对齐测试。

这应作为核心仿真器专项阶段，不应在外围 report / CLI 中补丁式实现。
