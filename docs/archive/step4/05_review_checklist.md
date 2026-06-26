# Step4 Review Checklist

## 进入代码开发前

- [x] 用户确认 Step4 产品边界：固定路由、多实例隔离 replay；每个实例内无限 HBM、vLLM-like batch-aware replay；不做请求路由仿真。
- [x] 用户确认 `ttft_ms = finish_time - arrival_time`，并单独输出 `scheduler_wait_ms`。
- [x] 用户确认 Step4 第一版不建模 decode TPOT 对 prefill 的干扰。
- [x] 用户确认 `batch_size` 在 HitFloor 内部先定义为 iteration 内 request slice 数。
- [x] 用户确认 Batch C 可使用 FormulaLatencyBackend 完成开发闭环；Batch D 默认 backend 调整为拟合型 TTFT 函数 backend。
- [x] 用户提供 AIConfigurator / Markov-Infer-sim 的最小接口信息，或确认外部 adapter 延后。

## 代码结构检查

- [x] scheduler、latency、replay、report 职责分离。
- [x] replay 核心不 import AIConfigurator / Markov-Infer-sim。
- [x] CLI 只解析参数、调用 lib、写输出。
- [x] report writer 不重算核心 replay 逻辑。
- [x] 当时已评估文件 / 函数规模；当前阈值以 `docs/code_development_requirements.md` 第 3 节为准。

## 正确性检查

- [x] prefix cache lookup 发生在首次调度时，而不是请求到达时。
- [x] materialization 只在 finish_time 后可见。
- [x] `sum(scheduled_prefill_tokens per request) == miss_tokens`。
- [x] token budget 不被突破。
- [x] seq budget 不被突破。
- [x] sorting/tie-break 稳定。
- [x] 相同输入多次运行输出一致。

## 测试检查

- [x] scheduler 正常路径测试。
- [x] chunked prefill 测试。
- [x] cache lookup timing 测试。
- [x] finish-time materialization 测试。
- [x] formula latency 单测。
- [x] shape memoization 单测。
- [x] synthetic E2E 测试。

## 报告检查

- [x] `request_metrics.csv` schema 稳定。
- [x] `iteration_metrics.csv` schema 稳定。
- [x] `summary.md` 只总结已有结果，不重算 replay。
- [x] 报告明确 latency backend。
- [x] 报告明确 Step4 未建模的部分。

## 完成 Step4 后

- [x] 更新 `docs/development_status.md`。
- [x] 更新 `docs/global_memory.md`。
- [x] 将 `docs/step4/` 移动到 `docs/archive/step4/`。
- [x] 在 summary 中记录测试命令与结果。
