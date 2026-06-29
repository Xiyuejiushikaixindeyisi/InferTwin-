# Step8：KV Load Latency 设计资料

状态：技术路线待评审，尚未进入代码开发。

阶段类型：核心仿真器。

Step8 的目标是在 Step7 已完成的 single-instance HBM + DDR/CPU pooling hit accounting 基础上，为非 HBM cache hit 增加 KV load latency accounting。

Step8 不应该改变 Step7 已确认的 cache hit 语义：

- HBM contiguous hit -> DDR contiguous hit -> miss。
- finish-time materialization 同时写 HBM 和 DDR。
- DDR hit 不自动 promotion 到 HBM。
- 实例之间 cache 隔离。

## 文档索引

| 文档 | 内容 |
|---|---|
| [01_ramulator2_study.md](01_ramulator2_study.md) | Ramulator2 使用方式、代码结构、与 InferTwin KV load 的关系 |
| [02_kv_load_background.md](02_kv_load_background.md) | KV load 涉及的存储、通信、KV tensor 大小和仿真粒度 |
| [03_step8_technical_route.md](03_step8_technical_route.md) | 旧版 Step8 产品边界、代码结构建议、Batch 开发顺序和准入/准出；仅作为参考 |
| [04_kv_load_overlap_and_source_study.md](04_kv_load_overlap_and_source_study.md) | vLLM / vLLM-Ascend / Mooncake KV load overlap、传输路径、load 粒度和并发调度源码调研 |
| [05_technical_route.md](05_technical_route.md) | 当前 Step8 高优先级技术路线；如与 03 冲突，以 05 为准 |

## 当前结论

Step8 可以继续推进，但必须采用分层方案：

1. InferTwin replay 主路径先实现轻量、确定性的 KV-load latency profile。
2. Ramulator2 第一阶段作为离线标定工具，用来得到 bytes/token 到 latency 的参数。
3. 不把 Ramulator2 作为每条请求的在线 replay 依赖。

如果后续评审要求做到 memory request 级别的真实 DDR trace replay、transfer queue、promotion completion 或跨实例 KV transfer，Step8 应暂停核心代码开发，转为存储/通信专项设计。
