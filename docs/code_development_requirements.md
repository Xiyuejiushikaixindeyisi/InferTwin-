# InferTwin 代码开发要求

本文档记录 InferTwin 后续开发必须遵守的代码质量、模块边界、测试与输出原则。

## 1. 优先写清晰代码，不写聪明代码

代码首先要让下一个维护者能快速读懂。

优先使用：

- 明确的数据结构。
- 清晰的函数边界。
- 稳定的 schema。
- 直接可测的中间结果。

不要为了少写几行，把逻辑压缩到难以理解。尤其不要把排序、判定、聚合、渲染写成难以调试的一行表达式或隐式副作用。

## 2. 模块职责必须单一

一个文件只负责一类事情。

推荐边界：

| 模块类型 | 只负责 | 不负责 |
| :--- | :--- | :--- |
| parser | 解析输入、返回结构化 record | 归因、聚合、渲染 |
| detector | 判断归因、返回 evidence | 读取 CLI 参数、写文件、渲染 HTML |
| cluster | 聚合统计、排序、计算 summary | 解析输入、跑 detector |
| html | render JSON/schema 到 HTML | 选择 reference、重算分析 |
| cli | 参数解析、调用 lib、写输出 | 承载核心业务逻辑 |

禁止在 HTML、CLI、parser 里偷偷重算核心分析逻辑。

HTML 必须是：

```text
render(JSON)
```

CLI 必须是：

```text
解析参数 + 调 lib + 写结果
```

## 3. 控制文件和函数规模

行数阈值是维护性信号，不是机械禁令。

InferTwin 的 scheduler、replay、cache eviction 这类核心算法文件，本质上接近 vLLM/vLLM-Ascend 的调度状态机。参考本地代码：

- `/home/zhangxiyue/vllm/vllm/v1/core/sched/scheduler.py` 约 2300 行。
- `/home/zhangxiyue/vllm-ascend-kv-study/vllm-ascend/vllm_ascend/core/recompute_scheduler.py` 约 1000 行。
- `/home/zhangxiyue/vllm/vllm/v1/worker/gpu/input_batch.py` 约 580 行。

因此 InferTwin 不要求为了满足较小行数阈值而强行拆分核心状态机。拆分必须让职责更清楚、测试更容易，而不是只让文件变短。

### 文件规模建议

| 文件类型 | 评估阈值 | 强评审阈值 | 处理原则 |
| --- | ---: | ---: | --- |
| schema / config / parser / report / CLI / adapter | 400 行 | 700 行 | 优先拆分，因为这些模块通常职责窄 |
| scheduler / replay / cache policy / event loop | 800 行 | 1200 行 | 可以保持集中，但要有清晰 helper、schema 和测试 |
| 复杂核心状态机 | 1200 行 | 1800 行 | 需要设计说明或评审记录，但不自动拆分 |
| 测试文件 | 800 行 | 1200 行 | 按行为场景拆测试文件，避免 fixture 失控 |

超过强评审阈值时，应说明：

1. 该文件为什么仍然应该保持集中。
2. public API 和内部 helper 的边界是什么。
3. 哪些行为已有测试覆盖。
4. 后续如果继续增长，优先拆出哪一类代码。

### 函数规模建议

| 函数类型 | 评估阈值 | 强评审阈值 | 处理原则 |
| --- | ---: | ---: | --- |
| 普通业务函数 | 120 行 | 180 行 | 优先拆为可测试 helper |
| parser / converter / renderer | 100 行 | 160 行 | 优先拆分，避免隐藏副作用 |
| scheduler / replay 核心状态推进函数 | 220 行 | 350 行 | 可以保留，但要有阶段清晰的代码块和测试 |
| 单纯 schema 校验函数 | 80 行 | 120 行 | 通常应拆分或移动到 schema/helper |

核心状态推进函数如果超过强评审阈值，不要求立刻拆分，但必须满足：

- 输入、输出和副作用清楚。
- 阶段顺序能直接读出来。
- 没有混入 CLI、文件写入、HTML、外部 simulator 调用。
- 关键分支有单测或集成测试覆盖。
- 若继续修改，应先评估是否抽出纯 helper，而不是在主流程里继续堆特殊分支。

### 什么时候应该拆分

优先拆分：

- schema / dataclass / config 类型。
- 无副作用的纯 helper。
- 外部工具 adapter。
- report / render / file writer。
- 与主状态机无关的统计聚合。

谨慎拆分：

- scheduler admission 主流程。
- replay event loop 主流程。
- cache eviction 状态转移。

如果拆分会导致状态在多个文件间来回跳转、隐藏副作用、或让测试更难写，则应保持集中。

### 补丁堆叠规则

同一个函数连续被补丁式修改 3 次时，不再自动要求拆分，但必须重新评估边界：

- 如果问题来自缺少 schema，先补 schema。
- 如果问题来自重复判断，抽纯 helper。
- 如果问题来自外部接口不稳定，新增 adapter 或 converter。
- 如果问题来自核心状态机自然增长，可以保留，但要补测试和注释。

继续补丁会降低可维护性时，应直接提醒用户先重构边界，再继续实现新需求。

## 4. 不要过度兼容

只兼容已经明确声明的输入格式。

坏做法：

```text
字段 A 没有，就猜字段 B；B 没有，就猜 C；C 也没有，就从文本正则猜。
```

好做法：

```text
只支持 documented schema。
无法解析时显式标记 parse_error / unsupported_source_type。
```

遇到未知格式时，应返回清晰错误或 `config_guard`，不要写越来越多的启发式猜测逻辑。

## 5. 错误处理要暴露问题，不要隐藏问题

错误处理的目标不是“让程序无论如何跑完”，而是让用户知道哪里不可信。

原则：

- 输入错误：明确报错。
- 配置错误：进入 `config_guard` 或直接失败。
- 分析前提不成立：不输出强结论。
- 证据不足：输出 `unknown`，而不是硬归因。

禁止为了鲁棒性伪造结果、静默跳过关键数据、用默认值掩盖配置错误。

## 6. 三次失败规则

如果同一个目标已经尝试 3 种合理方案仍无法可靠完成，agent 应停止继续写启发式逻辑。

此时应输出：

1. 当前无法可靠完成。
2. 已经尝试了哪些方案。
3. 失败原因是什么。
4. 需要用户确认什么输入、配置或产品边界。

不要继续添加第四、第五个模糊 fallback。

## 7. 少用环境变量

默认使用 CLI 参数、配置文件或显式函数参数。

如果必须使用环境变量，必须在文档和运行日志中明确声明：

- 环境变量名。
- 默认值。
- 作用。
- 缺失时行为。
- 是否影响结果口径。

禁止代码中偷偷读取环境变量影响分析结果。业务逻辑参数应通过函数参数传入，不要硬编码，也不要为了“灵活”把所有参数都做成环境变量。

## 8. 核心结果必须可测试、可复现

所有核心分析逻辑必须满足：

- 相同输入，多次运行输出一致。
- 排序有确定性 tie-break。
- JSON schema 稳定。
- 关键统计有不变量测试。

关键不变量示例：

```text
sum(cluster.affected_blocks) == total_miss_blocks
footer 各类 block 之和 == total_miss_blocks
HTML = render(JSON)，不得重新分析
```

新增功能必须有独立测试模块。测试要覆盖正常路径、降级路径、错误路径和关键不变量。

## 9. 新增功能先扩展 schema，再写展示

不要先把 HTML 写漂亮，再反推数据结构。

正确顺序：

1. 定义类型 / schema。
2. 实现核心 lib。
3. 写单测。
4. 输出 JSON / CSV。
5. 最后渲染 HTML。

这样后续 CLI、HTML、dashboard、批处理都能复用同一份结果。

## 10. 补丁越来越多时，主动提醒重构

agent 发现以下迹象时，应主动提醒用户：

- 同一段逻辑被多个脚本复制。
- `if/else` 分支越来越多。
- 函数参数不断膨胀。
- 为了兼容历史输入加入大量特殊分支。
- 测试越来越难写。
- HTML / CLI 开始承担分析逻辑。

提醒方式应该直接：

> 继续补丁会降低可维护性。建议先抽出独立模块，再继续实现新需求。

## 11. 保守输出，不夸大结论

分析平台宁可少说，也不要错说。

| 置信度 / 状态 | 输出策略 |
| :--- | :--- |
| 高置信 | 可以给明确建议 |
| 中置信 | 给优化方向和验证方式 |
| 低置信 | 只展示证据，不给强建议 |
| `unknown` | 承认无法解释 |
| `config_guard` | 提示先修配置再看结果 |

不要把 lossy 修改包装成安全修改，不要写“修完一定提升 X%”这类确定性承诺。

## 12. 每次开发都要留下清楚边界

每个新模块应说明：

- 它负责什么。
- 它不负责什么。
- 输入什么。
- 输出是什么。
- 失败时如何表现。
- 哪些行为被测试覆盖。

这些边界可以写在设计文档、模块 docstring、测试文件名或 README 中。原则是让后续维护者知道在哪里改、哪里不能改、出了问题先看哪里。

## 13. 按改动等级选择开发和测试强度

InferTwin 后续开发默认按风险分级，不用每次都做全项目审计，但核心路径不能降低要求。

| 等级 | 类型 | 示例 | 最低要求 |
| --- | --- | --- | --- |
| L0 | 文档治理 | 文档、索引、记忆 | `git diff --check` |
| L1 | 外围能力 | report、benchmark、normalizer、capacity sweep wrapper | 相关单测或小 E2E |
| L2 | 核心非 replay | config guard、schema、registry、profile resolver | 相关单测 + 小 E2E |
| L3 | 核心 replay | scheduler、cache lookup、materialization、latency shape、streaming replay | 新增/相关单测 + 小 E2E；必要时阶段 closure |

如果 L0 / L1 / L2 任务在实现中需要修改 L3 模块，必须停止当前实现，重新说明影响范围并等待用户审批。

## 14. 核心 Replay 改动必须显式自检

以下内容属于核心 replay 保护区：

- trace 到 `SimulationRequest` 的构造。
- tokenizer / chat template / prefix hash。
- scheduler planning、waiting queue、running set。
- chunked prefill selection。
- block conversion / cached token accounting。
- HBM / DDR lookup。
- materialization policy。
- eviction policy 状态转移。
- cache event 顺序和语义。
- latency shape、finish time、TTFT。
- streaming replay 的 instance isolation。

修改核心 replay 前，方案必须说明是否影响：

```text
cached_tokens
hbm_hit_tokens / ddr_hit_tokens / miss_tokens
finish_time / ttft_ms
cache event 顺序
materialization timing
实例隔离
capacity sweep 输出
true streaming 大 trace
```

实现后必须用测试覆盖改变的行为。不能只用报告输出检查替代核心模块测试。

## 15. 外围能力不得重算核心指标

外围能力包括：

- CLI / scripts wrapper。
- report / export。
- capacity sweep 表。
- benchmark。
- trace normalizer。
- dashboard / notebook。
- future hit floor search。

外围能力只能消费核心仿真器 typed result，不得重新计算或修正：

- cache hit。
- cached tokens。
- miss tokens。
- TTFT。
- cache event。
- instance replay ordering。

如果外围能力需要新指标，应先扩展核心 result schema 或新增核心 backend / policy / mode，再由外围能力展示或导出。

## 16. 新语义优先使用新接口

为了保护 V1 replay 语义，新增实验能力时优先使用新接口，而不是修改旧默认行为。

默认策略：

- 新 latency 行为：新增 latency component / backend。
- 新 cache hit 行为：新增 cache backend 或 block conversion policy。
- 新 materialization 行为：新增 materialization policy 和 replay/cache mode。
- 新外部仿真器：新增 adapter boundary。
- 新 report 字段：先扩展 typed result schema，再扩展 exporter。

Step8 已完成 KV load latency accounting；后续开发不得静默改变 Step7 / Step8 已冻结的 HBM / DDR hit 判定和 KV load typed metrics 语义。

Step9 progressive visibility 必须新增独立 mode，不能直接改变默认 `batch_aware_hbm_ddr_lru` 的 finish-time materialization 语义。

V2 的 gateway、instance queue、cross-instance pooling、Decode / TPOT、Hybrid / sparse attention cache 必须作为独立模块或新 mode 接入。
