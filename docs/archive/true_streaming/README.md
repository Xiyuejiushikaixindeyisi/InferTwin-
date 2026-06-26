# True Streaming Architecture Task

状态：Batch TS-F 已完成，Batch TS-G 待评审/开发。

任务类型：核心仿真器架构任务。

目标：修正当前 request build / replay 仍会持有全部 accepted `SimulationRequest` 的问题，为 11G 级 CSV trace、数万条长请求、32K 到 200K tokens prompt 提供可落地的大 trace 处理路径。

## 背景

工程优化 EO-G 已经降低了大 trace 风险：

- trace reader 不再一次性持有全量 `TraceRecord`。
- tokenizer 阶段可以拒绝超过 `max_prompt_tokens` 的请求。
- cache event 默认使用 stats-only sink。

但 EO-G 没有实现 true streaming：

- `build_request_build_result_from_config()` 仍返回全部 accepted `SimulationRequest`。
- `CapacitySweepRunner` 仍把 requests 转成 list 后复用。
- `BatchAwareReplayEngine.run()` 仍按 instance 分组并构造每个实例的 pending list。
- replay 内仍维护 `states_by_id`、`requests_by_id`、`lookup_by_id` 等状态，且当前 list path 不主动释放 finished request 的 request object。

因此 true streaming 需要作为独立架构任务处理，不能靠局部删除 `list()` 解决。

## 文档索引

- `01_vllm_vllm_ascend_study.md`：本地 vLLM / vLLM-Ascend 结构调研和对 HitFloor 的启发。
- `02_true_streaming_code_plan.md`：true streaming 技术路线、数据模型、模块边界、Batch 开发顺序和验收标准。
- `03_ts_a_execution.md`：Batch TS-A schema / codec 执行记录。
- `04_ts_b_execution.md`：Batch TS-B streaming request shard builder 执行记录。
- `05_ts_c_execution.md`：Batch TS-C request source 与 streaming replay engine 执行记录。
- `06_ts_d_execution.md`：Batch TS-D streaming metrics aggregator 执行记录。
- `07_ts_e_execution.md`：Batch TS-E streaming capacity sweep runner 执行记录。
- `08_ts_f_execution.md`：Batch TS-F benchmark 与大 trace 安全执行记录。

## 不变边界

true streaming 不能破坏现有 replay 能力。

现有路径保持不变：

```text
build_request_build_result_from_config()
-> CapacitySweepRunner
-> BatchAwareReplayEngine.run(list[SimulationRequest])
-> capacity_sweep.csv / summary.md
```

新增 streaming path 必须是 opt-in：

```text
streaming request shard build
-> streaming per-instance replay
-> streaming metric aggregation
-> same report schema
```

旧的 `capacity_sweep` 和新的 streaming runner 应在同一份合成 trace 上输出相同核心指标。

## True Streaming 定义

本任务中的 true streaming 指：

- 不在内存中保存全量 accepted `SimulationRequest`。
- 不在内存中保存每个实例的全量 pending request list。
- request build 逐行读取 CSV，逐条 tokenizer / chat template / hash，随即写入 per-instance shard 或流入 replay。
- replay 只保留当前实例的 active state：next request buffer、waiting queue、running states、lookup state、cache metadata 和聚合器状态。
- sweep 可以 build once，但复用的是磁盘 shard，不是内存中的 request list。

本任务不承诺：

- tokenizer 内部 tokenization 本身是 streaming。第一版仍允许单条 request 的 token ids 在 tokenizer 阶段短暂驻留内存。
- progressive block visibility。
- decode / TPOT。
- 多实例并行 replay。
- external sort。
- DDR / SSD / multi-tier cache。
