# HitFloor 外围能力讨论区

## 当前状态

本文档夹用于沉淀 HitFloor 外围能力的背景知识、产品形态、技术路线、代码方案和执行记录。

当前只创建目录和入口文档，等待用户继续提供：

- HitFloor 背景知识。
- HitFloor 初始形态。
- HitFloor 与 InferTwin 核心仿真器的关系。

## 边界

HitFloor 是 InferTwin 之上的外围能力，不是核心 replay 引擎。

默认原则：

```text
InferTwin core simulator
  -> 负责 request build / scheduler replay / cache hit / latency / typed result

HitFloor outer capability
  -> 消费 typed result，生成容量、HBM hit、DDR hit、miss、TTFT、P90 TTFT 等关系表
```

HitFloor 不应在 report / CLI / script 中重算 prefix cache hit、TTFT、cache lifecycle 或 eviction 语义。若发现 HitFloor 需要新的 replay 语义，应回到核心仿真器新增 mode / backend / policy / schema。

## 参考文档

`pre_hitfloor` 当前整体 pending，但保留原地作为参考，不归档：

- `docs/pre_hitfloor/prefix_cache_hit_factors_and_priorities.md`
- `docs/pre_hitfloor/ttft_modeling.md`
- `docs/pre_hitfloor/technical_route.md`
- `docs/pre_hitfloor/p0_technical_route.md`
