# RP-G 验收记录：Tests / Docs / E2E

状态：已完成。

## 目标

RP-G 用于在进入 Step7 前，对 V1 review repair 进行严格工程验收。

验收重点不是新增外围能力，而是确认核心仿真器在以下路径上稳定：

- routed trace fail-fast。
- registry-relative model profile path。
- streaming sorted-trace guard。
- model-owned runtime defaults。
- instance runtime resolver。
- streaming runner 按 `instance_uuid` 选择 tokenizer、scheduler、block size 和 TTFT backend。
- capacity sweep 作为外围 report/export 能力，不反向修改 core replay 语义。

## 合成 E2E 场景

新增测试：

```text
tests/integration/test_v1_review_repair_e2e.py
```

测试构造：

- 两个模型：`model-a`、`model-b`。
- 三个实例：
  - `instance-a` 使用 `model-a`，并配置实例专属 TTFT profile。
  - `instance-b` 使用 `model-a`，不配置实例专属 TTFT，回退到 model default TTFT。
  - `instance-c` 使用 `model-b`，不配置实例专属 TTFT，回退到 model default TTFT。
- 两个 capacity：`hbm_capacity_blocks = [1, 2]`。
- 每个模型使用不同 tokenizer profile，且 tokenizer registry 不提供匹配模型名的 alias，用于验证 request build 必须通过 instance runtime resolver 强制选择 tokenizer。
- `model-a` 与 `model-b` 使用不同 `block_size_tokens` 和 scheduler chunk 参数，用于验证 model-bound runtime defaults 已进入 streaming request build 和 replay engine。

关键断言：

- trace accepted request 数量为 6，rejected request 数量为 0。
- `latency_source_by_instance` 正确区分 instance 专属 TTFT 与 model default TTFT。
- `runtime_model_by_instance` 正确记录实例到模型绑定。
- `model_default_cache_by_instance` 记录模型默认 cache metadata。
- capacity sweep 输出 `trace + per-instance` long-format rows。
- `instance-b` 与 `instance-a` 使用同模型同请求，但由于 TTFT slope 不同，P90 TTFT 比例符合预期。
- `instance-c` 使用另一模型，chunking / block size / tokenizer metadata 与 `instance-a/b` 不同。
- 写出的 `capacity_sweep.csv` 包含 capacity 与 scope 维度。
- `summary.md` 包含 Latency Resolution 信息。

## 验证命令

目标测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_v1_review_repair_e2e.py \
  tests/integration/test_streaming_runtime_integration.py \
  tests/integration/test_instance_runtime_resolver_e2e.py \
  tests/unit/config/test_instance_runtime.py \
  tests/unit/config/test_model_runtime.py

14 passed
```

全量测试：

```text
PYTHONPATH=src .venv/bin/python -m pytest

260 passed
```

代码风格：

```text
.venv/bin/python -m ruff check src tests scripts

All checks passed!

.venv/bin/python -m ruff format --check src tests scripts

157 files already formatted
```

## 结论

RP-G 验收通过。

当前 V1 review repair 已证明：

- `sweep-streaming` 可以在合成集群 trace 上完成多实例隔离 replay。
- 多个实例可以共享同一个模型配置，也可以绑定不同模型配置。
- 实例专属 TTFT 与模型默认 TTFT fallback 能够同时工作。
- cache 容量仍由 capacity sweep 候选值控制；model default cache 是模型运行默认值和 metadata，不会阻止外围 sweep 覆盖 capacity。
- streaming runner 的 model runtime integration 只影响 request build、scheduler/config selection 和 latency backend resolution，不把 report/export 逻辑塞入 core replay。

## Step7 前注意事项

Step7 是核心仿真器开发，不是外围能力。

进入 Step7 时需要继续保持：

- cache backend / cache tier / event schema 显式建模。
- 单实例池化先作为新的多级 cache backend 能力引入，不能把 DDR/CPU 简化成 HBM 容量扩容。
- `kv_load_ms` 当前仍为 0；Step8 再把非 HBM hit latency 接入 ServingLatencyProfile。
- progressive block visibility 仍未启用；Step9 通过新 replay/cache mode 实现。
- V2 再处理复杂 Hybrid 模型、gateway、实例侧排队、多实例池化跨实例命中、Decode / TPOT 和后续大规模工程优化。
