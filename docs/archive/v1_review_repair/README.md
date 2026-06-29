# InferTwin V1 Review Repair

状态：已完成并归档。

本目录用于承接 `docs/reviews/infertwin_project_review.md` 和用户评审意见之后的 V1 修复方案。

本轮目标不是新增复杂仿真能力，而是做 V1 可靠性收口：

- 核心 routed trace schema 更严格。
- model registry 路径解析更稳定。
- streaming replay 时间线更安全。
- model registry / instance binding 能承载 V1 模型绑定运行参数兜底。
- 明确 V1 / V2 边界：V1 准出包含 Step7 单实例池化、Step8 KV load latency、Step9 progressive chunk visibility；V2 再处理 streaming validate-trace、外部排序 / shard sort、Hybrid 模型、gateway、排队、多实例池化和 Decode。

本目录已从临时阶段目录归档到：

```text
docs/archive/v1_review_repair/
```

## 文档

```text
docs/archive/v1_review_repair/v1_repair_plan.md
docs/archive/v1_review_repair/hybrid_model_debt_note.md
docs/archive/v1_review_repair/03_rp_g_acceptance.md
docs/archive/v1_review_repair/04_rp_h_closure.md
```

## 开发顺序

```text
RP-A  Trace Schema Guard
RP-B  Registry-Relative Model Paths
RP-C  Streaming Sorted-Trace Guard
RP-D  Model Runtime Defaults Schema
RP-E  Instance Runtime Resolver
RP-F  Streaming Runner Integration
RP-G  Tests / Docs / E2E
RP-H  Engineering Closure
```

评审通过前，不进入代码修改。

## 当前验收

RP-A 到 RP-H 已完成。

RP-G 验收结果：

```text
PYTHONPATH=src .venv/bin/python -m pytest: 260 passed
.venv/bin/python -m ruff check src tests scripts: passed
.venv/bin/python -m ruff format --check src tests scripts: passed
```

严格合成 E2E 已覆盖 model runtime defaults、instance runtime resolver、per-instance TTFT fallback、streaming request build、capacity sweep metadata 和 report/export 边界。

RP-H 收口结果：

```text
docs/archive/v1_review_repair/04_rp_h_closure.md
```
