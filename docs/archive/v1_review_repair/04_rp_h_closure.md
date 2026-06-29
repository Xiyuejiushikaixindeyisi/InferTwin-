# RP-H 收口记录：Engineering Closure

状态：已完成。

## 收口范围

RP-H 用于关闭 V1 review repair 阶段，并确认 InferTwin 可以进入 Step7 前的产品形态和技术路线讨论。

本批没有新增核心 replay 语义，也没有新增外围能力。主要动作：

- 将 `docs/v1_review_repair/` 移入 `docs/archive/v1_review_repair/`。
- 更新 `README.md`、核心技术路线、产品设计、开发治理和全局记忆中的阶段状态。
- 将活跃文档中的 V1 review repair 路径统一指向 archive。
- 保留 RP-G 严格合成 E2E 作为 Step7 前的验收基线。
- 确认 V1 / V2 边界仍然一致。

## 已完成能力

V1 review repair 已完成：

- RP-A Trace Schema Guard。
- RP-B Registry-Relative Model Paths。
- RP-C Streaming Sorted-Trace Guard。
- RP-D Model Runtime Defaults Schema。
- RP-E Instance Runtime Resolver。
- RP-F Streaming Runner Integration。
- RP-G Tests / Docs / E2E。
- RP-H Engineering Closure。

其中 RP-E / RP-F / RP-G 的关键结论是：

- `sweep-streaming` 支持 fixed-routing multi-instance isolated replay。
- 多个实例可以共享同一模型配置，也可以绑定不同模型配置。
- cache 容量默认与 model runtime defaults 绑定；capacity sweep 运行时可以用候选 capacity 覆盖模型默认 HBM capacity。
- 实例专属 TTFT profile 优先于模型默认 TTFT profile。
- 未配置实例专属 TTFT 时，可使用 model default TTFT fallback。
- model runtime integration 已进入 streaming request build、scheduler setup、block size conversion 和 latency backend resolution。
- report/export 仍是外围能力，不反向修改 core replay 语义。

## 最终验证

```text
PYTHONPATH=src .venv/bin/python -m pytest

260 passed

.venv/bin/python -m ruff check src tests scripts

All checks passed!

.venv/bin/python -m ruff format --check src tests scripts

157 files already formatted
```

最终检查：

```text
git diff --check: passed
```

## 下一阶段

下一阶段是 Step7。

Step7 是核心仿真器开发，不是外围能力开发。目标是单实例池化：一个实例可以在 DDR/CPU 侧额外 KV cache 存储中命中。

进入 Step7 时必须继续遵守：

- 先讨论产品形态，再讨论技术路线，再进入代码开发。
- 明确 cache tier、cache event、hit accounting 和 latency accounting。
- 不把 DDR/CPU 池化简化成 HBM capacity 扩容。
- `kv_load_ms` 在 Step7 仍可保持 0；Step8 再接入非 HBM hit latency。
- progressive chunk visibility 仍放在 Step9，通过新的 replay/cache mode 实现。
- V2 再处理复杂 Hybrid 模型、gateway、实例侧排队、多实例池化跨实例命中、Decode / TPOT 和后续大规模工程优化。
