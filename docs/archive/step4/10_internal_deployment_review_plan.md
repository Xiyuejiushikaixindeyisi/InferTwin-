# Internal Deployment Notes Review Plan

## 背景

Batch A/B 已完成并通过 review。根据 simulator manual review，Batch A/B 又完成了一次接口修正：

- `ScheduledSlice` 区分 `cached_prefix_tokens` 和 `previous_chunk_tokens`。
- `computed_tokens_before = cached_prefix_tokens + previous_chunk_tokens`。
- 完整 pytest 通过。

在正式进入 Batch C 开发前，用户将提供一份公司内模型部署方法文档。该文档用于辅助理解：

- batch size
- chunked prefill
- PD 分离
- 多级缓存
- 池化

## 文档沉淀位置

收到公司内部署方法文档后，沉淀为：

```text
docs/notes/internal_model_deployment_method.md
```

当前状态：

- 公司内部署方法文档已收到。
- 学习笔记已沉淀为 `docs/notes/internal_model_deployment_method.md`。

notes 文档应包含：

- 原始方法摘要。
- batch size 口径。
- chunked prefill 口径。
- PD 分离口径。
- 多级缓存结构。
- 池化机制。
- 对 HitFloor Step4 的影响。
- 与 AIConfigurator / MkSim 的差异。
- 尚未明确的问题。

## Review 输出

阅读并沉淀该文档后，需要输出：

```text
docs/step4/11_pre_batch_c_code_review.md
```

当前状态：

- pre-Batch-C code review 已输出到 `docs/step4/11_pre_batch_c_code_review.md`。

该 review 需要覆盖：

1. 当前 HitFloor 代码结构是否适合继续 Batch C。
2. scheduler / latency / replay / report 的职责边界是否清晰。
3. 当前 `BatchShape` / `ScheduledSlice` / `RequestState` 是否足以表达部署文档中的 batch size 和 chunked prefill。
4. 当前 latency backend interface 是否能承接 AIConfigurator / MkSim / 公司内部署方法。
5. 当前请求处理流程是否会在 Batch C 中引入语义错误。
6. 哪些问题必须在 Batch C 前修正。
7. 哪些问题可以留到 Batch D 或有限 HBM/DDR 阶段。

## 当前暂停规则

在完成内部署方法学习笔记和 pre-Batch-C code review 前，不进入 Batch C 代码开发。

可以继续做：

- 文档沉淀。
- 代码结构评审。
- 接口设计评审。
- 数据结构评审。
- 请求处理流程评审。

不做：

- 新增 `BatchAwareReplayEngine`。
- 修改 runner/report。
- 实现外部 simulator adapter。
