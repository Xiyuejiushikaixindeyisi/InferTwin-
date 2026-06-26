# HitFloor 工程优化阶段

## 定位

本目录记录 HitFloor 核心仿真器工程优化阶段的临时方案、调研和代码开发计划。

本阶段开发对象是：

```text
核心仿真器
```

不是外围能力。容量 sweep、CSV 导出、GB 到 block 转换、部署脚本导入、dashboard、hit floor search 都不属于本阶段主线。

## 目标

- 保持 Step1-Step6 已完成 replay 能力稳定。
- 对齐真实 vLLM / vLLM-Ascend 的关键语义，尤其是 scheduler、KV cache lookup、block size、cached_tokens 和 materialization。
- 补齐 profile / config guard / block conversion 这些后续核心能力的地基。
- 明确当前仿真近似与真实推理服务之间的差异。
- 提升大 trace 下的可维护性、可测试性和性能安全性。

## 文件索引

```text
docs/engineering_optimization/
  README.md
  01_vllm_vllm_ascend_study.md
  02_code_implementation_plan.md
  03_eo_a_b_c_execution.md
  04_eo_d_execution.md
  05_eo_e_execution.md
  06_eo_f_execution.md
  07_eo_g_execution.md
  08_core_simulator_closeout_review.md
  09_eo_h_execution.md
```

## 收口 Review

当前工程优化收口 review 记录在：

```text
docs/engineering_optimization/08_core_simulator_closeout_review.md
```

该文档说明有限 HBM 下一条 request 进入核心仿真器后的处理顺序、cache / scheduler / latency 信号、与 vLLM / vLLM-Ascend 的主要差异、测试结果、遗留问题和工程收口判断。

## 生命周期

工程优化完成后，本目录应整体移动到：

```text
docs/archive/engineering_optimization/
```

主文档只保留轻量索引和最终结论。
