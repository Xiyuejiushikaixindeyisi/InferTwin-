# vLLM / vLLM-Ascend Batching Study

本文件记录 Step4 设计阶段对本地推理框架 scheduler 的学习结果。它不是完整源码解读，只提炼 HitFloor 需要仿真的 batch 机制。

## 本地代码位置

- vLLM:
  - `/home/zhangxiyue/vllm/vllm/config/scheduler.py`
  - `/home/zhangxiyue/vllm/vllm/v1/core/sched/scheduler.py`
- vLLM-Ascend:
  - `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/core/scheduler_dynamic_batch.py`

## vLLM SchedulerConfig 关键信息

HitFloor Step4 需要抽取的配置项：

- `max_num_batched_tokens`: 一个 iteration 允许调度的最大 token 数。
- `max_num_scheduled_tokens`: scheduler 实际可发出的 token budget，默认等于 `max_num_batched_tokens`。
- `max_num_seqs`: 一个 iteration 允许同时调度的最大请求数。
- `max_num_partial_prefills`: 允许同时存在的 partial prefill 数。
- `max_long_partial_prefills`: 长 prefill 请求的 partial prefill 限制。
- `long_prefill_token_threshold`: 超过该阈值的 prefill 可被截断成 chunk。
- `enable_chunked_prefill`: 是否开启 chunked prefill。
- `policy`: `fcfs` 或 `priority`，Step4 第一版只实现 `fcfs`。

## vLLM v1 scheduler 核心逻辑

vLLM v1 scheduler 的设计重点不是“prefill 阶段”和“decode 阶段”硬切分，而是在每个 iteration 内给请求分配 token，使：

```text
num_computed_tokens 追赶 num_tokens_with_spec
```

这个统一逻辑同时覆盖：

- chunked prefill
- prefix caching
- speculative decoding
- future jump decoding

对 HitFloor Step4 来说，需要保留的最小状态是：

- `num_prompt_tokens`
- `num_computed_tokens`
- `cached_tokens`
- `remaining_uncached_tokens`
- request status: waiting / running / finished

每次 schedule 的核心过程：

1. 从 running 队列开始，先给已经运行中的请求分配 token。
2. 对每个 running request 计算本轮 `num_new_tokens`。
3. 用 token budget、model length、chunk threshold 截断 `num_new_tokens`。
4. 如果 KV slot 分配失败，真实 vLLM 会做 preemption；HitFloor Step4 暂不模拟有限 KV slot，因此不实现 preemption。
5. running 处理后，再从 waiting 队列接纳新请求。
6. waiting request 首次调度前会查询 prefix cache，得到可复用的 computed blocks。

## 对 HitFloor 的关键启发

### 1. prefix cache lookup 应移动到首次调度时

Step1-Step3 当前 replay 更接近“请求到达时立刻 lookup”。Step4 为了更贴近 vLLM，应改成：

- 请求到达时只完成 tokenizer、chat template、block hash 构建，并进入 waiting queue。
- 请求首次被 scheduler 考虑时，先 flush 已完成请求的 materialization event，再做 prefix cache lookup。
- lookup 结果决定 `cached_tokens` 与 `miss_tokens`。

这样可以正确表达：

```text
某请求到达时不可见的 cache block，可能在它真正被调度前已经由其他请求完成并 materialize。
```

### 2. chunked prefill 是 token budget 约束下的自然结果

当 `miss_tokens` 大于当前 iteration token budget 时，scheduler 只调度一部分 tokens，请求保持 running，下一轮继续补齐。

HitFloor 不需要复刻 vLLM 的所有内部对象，只需要保证：

```text
sum(scheduled_tokens for request) == miss_tokens
```

并且请求只有在最后一个 prefill chunk 完成后才产生 TTFT 和 cache materialization。

### 3. Step4 可以暂不实现真实 KV slot 分配

Step4 仍处于无限 HBM 阶段，不做有限 HBM LRU。因此真实 vLLM 中的 block manager slot allocation、preemption、swap 等行为不进入 Step4。

这些内容留到后续有限 HBM / DDR 阶段实现。

## vLLM-Ascend dynamic batch 关键差异

vLLM-Ascend 的 `SchedulerDynamicBatch` 在 vLLM scheduler 基础上增加了动态 batch token budget 调整：

- 根据 running decode request 数量调整 prefill token budget。
- 使用 lookup table/refiner 调整本轮可用 token budget。
- 在 running 队列中倾向 decode-first：先处理 decode 请求，再处理 prefill 请求。

对 HitFloor Step4 的判断：

- 第一版 TTFT 只关心 prefill，不完整模拟 decode TPOT，因此不应先实现 vLLM-Ascend dynamic batch。
- 可以在数据结构中保留 `scheduled_decode_tokens` 字段，方便后续扩展。
- 当前更适合实现 vLLM-like FCFS + chunked prefill scheduler，后续再通过策略类扩展 Ascend dynamic batch。

## Step4 采用的仿真近似

Step4 第一版建议采用：

```text
vLLM-like FCFS continuous batching + chunked prefill + infinite KV slots
```

保留：

- waiting/running 队列。
- 每 iteration token budget。
- `max_num_seqs`。
- prefix cache lookup at first scheduling。
- finish-time materialization。

不保留：

- decode-first dynamic batch。
- priority policy。
- preemption。
- finite KV slot allocation。
- swap / recompute policy。

