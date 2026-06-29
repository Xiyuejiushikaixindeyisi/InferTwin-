# S8-F 实施方案：Ramulator2 / Mooncake Calibration Boundary

状态：已完成代码开发，待用户代码评审。

所属 Step：Step8 KV Load Latency。

本 Batch 名称：S8-F：Ramulator2 / Mooncake Calibration Boundary。

前置条件：

- S8-A 已完成 KV load shape / shape key。
- S8-B 已完成 `KVLoadLatencyComponent` 与 `KVLoadLatencyProfile` mode schema。
- S8-C 已完成 instance/model resolver 到 `ServingLatencyProfile` 的接入。
- S8-D 已完成 replay 中 DDR hit -> KV load latency 的主链路。
- S8-E 已完成 request / iteration / streaming typed metrics 中的 KV load 字段。

## 1. 类型与改动等级

本 Batch 属于核心仿真器的非 replay 支撑边界。

改动等级：L2。

原因：

- 本 Batch 设计外部校准数据如何进入核心 latency profile。
- 本 Batch 不修改 scheduler、cache、materialization、event loop、streaming replay。
- 本 Batch 不把 Ramulator2 / Mooncake 放入默认 replay 主路径。
- 本 Batch 只新增 opt-in calibration boundary、typed artifact 和测试。

一句话边界：

```text
Ramulator2 / Mooncake -> calibration observations -> fitted KVLoadLatencyProfile -> InferTwin replay
```

而不是：

```text
InferTwin replay -> online call Ramulator2 / Mooncake -> per-iteration latency
```

## 2. 本 Batch 做什么

S8-F 只做 calibration boundary：

1. 定义外部 KV load calibration observation 的 typed schema。
2. 定义 token-linear / byte-linear v1 的轻量拟合 helper。
3. 定义拟合结果如何转换为现有 `KVLoadLatencyProfile` mapping。
4. 明确 `calibrated_from` 的推荐命名口径。
5. 给 Ramulator2 / Mooncake 留出 opt-in adapter / harness 边界，但不接默认 replay。
6. 保持没有安装外部 simulator 时，InferTwin 单测和 replay 全部可运行。
7. 增加文档，说明如何用 Ramulator2 输出、Mooncake benchmark 或实机观测拟合 Step8 KV load 参数。

建议校准数据流：

```text
external benchmark / simulator / measured logs
  -> KVLoadCalibrationObservation
  -> fit_token_linear_v1 or fit_byte_linear_v1
  -> KVLoadCalibrationFit
  -> KVLoadLatencyProfile mapping
  -> instance/model latency profile yaml
```

## 3. 本 Batch 不做什么

S8-F 不做：

- 不修改 `KVLoadLatencyProfile` 当前 schema。
- 不新增 Step8 默认配置项。
- 不自动修改 model registry 或 instance latency profile 文件。
- 不新增默认 CLI。
- 不安装、vendor 或运行 Ramulator2。
- 不安装、vendor 或运行 Mooncake。
- 不导入 Mooncake SDK / TransferEngine。
- 不把 external adapter 接进 `BatchAwareReplayEngine`。
- 不把 Ramulator2 / Mooncake 放进 streaming capacity sweep 主路径。
- 不做 memory request / cacheline / DRAM address 级在线 replay。
- 不做 KV load queue / backpressure。
- 不做 compute/load overlap。
- 不做 layerwise 或 chunkwise KV load 拆分。
- 不做 DDR hit promotion。
- 不新增 load completion event。
- 不改变 report/export 字段。

如果开发中发现必须修改 replay、cache、scheduler、streaming runner 或 `KVLoadLatencyProfile` schema，应暂停并重新提交方案。

## 4. 计划新增/修改的文件

### 4.1 `src/infertwin/external/kv_load_calibration.py`

职责：

- 定义外部 KV load 校准数据的 typed boundary。
- 提供确定性的 token-linear / byte-linear v1 拟合 helper。
- 将拟合结果转换为现有 `KVLoadLatencyProfile` mapping。

计划新增数据结构：

```python
@dataclass(frozen=True, slots=True)
class KVLoadCalibrationObservation:
    source: str
    model_name: str
    hardware_name: str
    transfer_path: str
    kv_load_tokens: int
    kv_load_bytes: int
    kv_load_request_count: int
    batch_size: int
    duration_ms: float
    note: str = ""


@dataclass(frozen=True, slots=True)
class KVLoadCalibrationFit:
    mode: Literal["token_linear_v1", "byte_linear_v1"]
    aggregation: Literal["shared_link_sum"]
    overlap_mode: Literal["none_v1"]
    transfer_path: str
    ddr_fixed_overhead_ms: float
    ddr_ms_per_cached_token: float = 0.0
    ddr_ms_per_byte: float = 0.0
    calibrated_from: str
    sample_count: int
```

计划新增函数：

```python
def fit_token_linear_v1(
    observations: Sequence[KVLoadCalibrationObservation],
    *,
    calibrated_from: str,
    fit_intercept: bool = True,
) -> KVLoadCalibrationFit:
    ...


def fit_byte_linear_v1(
    observations: Sequence[KVLoadCalibrationObservation],
    *,
    calibrated_from: str,
    fit_intercept: bool = True,
) -> KVLoadCalibrationFit:
    ...


def to_kv_load_profile_mapping(fit: KVLoadCalibrationFit) -> dict[str, object]:
    ...
```

职责边界：

- 只处理已经离线得到的 observation。
- 不运行外部程序。
- 不读取业务 trace。
- 不更新用户 yaml。
- 不接 replay。

### 4.2 `src/infertwin/external/ramulator2.py`

职责：

- 继续作为 Ramulator2 integration boundary。
- 明确 Ramulator2 在 S8-F 中只作为 calibration source。

计划修改：

- 保留 `Ramulator2Adapter.estimate_kv_restore(...)` 的 `NotImplementedError`。
- 可新增轻量 reference / command builder，例如：

```python
@dataclass(frozen=True, slots=True)
class Ramulator2CalibrationReference:
    repo_path: Path
    executable: Path
    source_name: str = "ramulator2_git"

    def validate_checkout(self) -> None:
        ...
```

- 可新增文档化 helper，说明如何把 Ramulator2 离线结果写成 `KVLoadCalibrationObservation`。

边界：

- 不执行 Ramulator2。
- 不生成 memory address trace。
- 不解析真实 Ramulator2 stats 文件，除非用户后续单独审批 calibration harness。

### 4.3 `src/infertwin/external/mooncake.py`

职责：

- 定义 Mooncake benchmark / 实机观测作为校准来源的边界。
- 避免直接依赖 Mooncake SDK。

计划新增：

```python
@dataclass(frozen=True, slots=True)
class MooncakeCalibrationReference:
    source_name: str = "mooncake_benchmark"
    protocol: str = "unknown"
    transfer_path: str = "mooncake"

    def calibrated_from(self, run_id: str) -> str:
        ...
```

边界：

- 不 import Mooncake。
- 不调用 `TransferEngine`。
- 不模拟 replica placement、lease、pin、eviction。
- 不表达 remote / local / SSD fallback 的完整队列。

### 4.4 `src/infertwin/external/__init__.py`

职责：

- 导出 S8-F 新增的 calibration boundary 类型。

计划修改：

- 导出 `KVLoadCalibrationObservation`、`KVLoadCalibrationFit`、fit helpers。
- 如果新增 `MooncakeCalibrationReference` / `Ramulator2CalibrationReference`，一并导出。

### 4.5 `docs/step8/06_calibration_boundary.md`

职责：

- 面向同事说明如何把 Ramulator2 / Mooncake / 实机观测变成 InferTwin KV load profile。

计划内容：

- 为什么不在线接 Ramulator2 / Mooncake。
- 推荐 observation schema。
- token-linear 与 byte-linear 的选择。
- `calibrated_from` 命名约定。
- profile yaml 示例。
- S8-F 与 Step9 / V2 的边界。

### 4.6 `docs/step8/s8_f_ramulator2_mooncake_calibration_boundary_implementation_plan.md`

职责：

- 本 Batch 的开发方案和执行记录。

开发完成后更新：

- 已做内容。
- 未做内容。
- 验证命令。
- 是否具备进入 S8-G 的条件。

### 4.7 测试文件

计划新增或修改：

```text
tests/unit/external/test_kv_load_calibration.py
tests/unit/external/test_adapter_boundaries.py
```

原则：

- 只测试 schema、fit helper、profile mapping 和 adapter boundary。
- 不执行外部 simulator。
- 不依赖网络。
- 不依赖 Ramulator2 / Mooncake 本地 checkout。

## 5. 新增或修改的数据结构 / schema / interface

### 5.1 Calibration observation

`KVLoadCalibrationObservation` 表示一个离线校准点。

字段语义：

| 字段 | 含义 |
| --- | --- |
| `source` | 数据来源，例如 `ramulator2_git`、`mooncake_benchmark`、`production_measurement` |
| `model_name` | 模型名，用于防止跨模型混合拟合 |
| `hardware_name` | 硬件名，用于防止跨硬件混合拟合 |
| `transfer_path` | 传输路径口径，例如 `local_ddr_cpu`、`mooncake_rdma`、`mooncake_ascend` |
| `kv_load_tokens` | 本 observation 对应的 KV load token 数 |
| `kv_load_bytes` | 本 observation 对应的 KV load bytes |
| `kv_load_request_count` | 本 observation 中参与 load 的 request 数 |
| `batch_size` | 本 observation 中的 scheduler / benchmark batch size |
| `duration_ms` | 观测或仿真的 KV load latency |
| `note` | 可选备注，不参与计算 |

校验：

- `source`、`model_name`、`hardware_name`、`transfer_path` 非空。
- tokens / bytes / request_count / batch_size 非负或正数按字段语义校验。
- `duration_ms >= 0`。

### 5.2 Calibration fit

`KVLoadCalibrationFit` 表示拟合后的 Step8 profile 参数。

token-linear 输出：

```yaml
kv_load:
  mode: token_linear_v1
  aggregation: shared_link_sum
  overlap_mode: none_v1
  transfer_path: local_ddr_cpu
  ddr_fixed_overhead_ms: <intercept>
  ddr_ms_per_cached_token: <slope>
  calibrated_from: ramulator2_git:<run_id>
```

byte-linear 输出：

```yaml
kv_load:
  mode: byte_linear_v1
  aggregation: shared_link_sum
  overlap_mode: none_v1
  transfer_path: local_ddr_cpu
  ddr_fixed_overhead_ms: <intercept>
  ddr_ms_per_byte: <slope>
  calibrated_from: mooncake_benchmark:<run_id>
```

### 5.3 `calibrated_from` 命名约定

S8-F 不把 `calibrated_from` 改成 enum，保持当前 string schema，避免破坏现有 profile。

推荐格式：

```text
manual_default
synthetic:<name>
ramulator2_git:<run_id>
mooncake_benchmark:<run_id>
production_measurement:<run_id>
company_AIConfigurator:<run_id>
aiconfigurator_git:<run_id>
```

说明：

- `aiconfigurator_git` 是开源项目测试口径。
- `company_AIConfigurator` 是公司内修改版口径。
- S8-F 只规定命名，不验证外部 run 是否存在。

## 6. 核心算法逻辑

### 6.1 Observation normalization

输入：

```text
KVLoadCalibrationObservation[]
```

校验：

1. observations 非空。
2. 所有 observation 的 `model_name` 一致。
3. 所有 observation 的 `hardware_name` 一致。
4. 所有 observation 的 `transfer_path` 一致。
5. token-linear 要求至少一个 observation 的 `kv_load_tokens > 0`。
6. byte-linear 要求至少一个 observation 的 `kv_load_bytes > 0`。

### 6.2 Linear fit

拟合目标：

```text
duration_ms = fixed_overhead_ms + coefficient * x
```

其中：

```text
token_linear_v1: x = kv_load_tokens
byte_linear_v1:  x = kv_load_bytes
```

`fit_intercept=True`：

- 使用简单最小二乘拟合 intercept 和 slope。
- 如果样本 x 全相同，fail-fast，因为无法可靠拟合 slope。
- 如果拟合出负 intercept 或负 slope，fail-fast，要求用户重新检查 observation 或改用固定参数。

`fit_intercept=False`：

- 固定 intercept 为 0。
- slope 使用 `sum(x*y) / sum(x*x)`。
- 如果 `sum(x*x) == 0`，fail-fast。

### 6.3 Profile mapping

`to_kv_load_profile_mapping(fit)` 输出 dict，直接可写入现有 profile yaml：

```text
mode
aggregation
overlap_mode
transfer_path
ddr_fixed_overhead_ms
ddr_ms_per_cached_token or ddr_ms_per_byte
calibrated_from
```

该函数不写文件，避免自动修改用户配置。

## 7. 对核心 replay 语义的影响

| 问题 | S8-F 影响 |
| --- | --- |
| 是否改变 `cached_tokens` | 不改变 |
| 是否改变 `hbm_hit_tokens` / `ddr_hit_tokens` / `miss_tokens` | 不改变 |
| 是否改变 `finish_time` / `ttft_ms` | 不直接改变；只有用户手动把拟合 profile 配进 replay 后，Step8 已有 latency component 才会按配置生效 |
| 是否改变 cache event 顺序 | 不改变 |
| 是否改变 materialization timing | 不改变 |
| 是否改变实例隔离 | 不改变 |
| 是否影响 true streaming 大 trace | 不影响 streaming replay 主路径；只提供离线 profile 参数来源 |

## 8. 测试计划

### 8.1 单测

新增 `tests/unit/external/test_kv_load_calibration.py`：

- observation 校验：空 source / 负 tokens / 负 bytes / 负 duration fail-fast。
- token-linear fitting：
  - 两个或多个合成点能拟合出预期 intercept / slope。
  - x 全相同时 fail-fast。
  - 负 slope / 负 intercept fail-fast。
- byte-linear fitting：
  - bytes 线性样本能拟合出预期参数。
  - bytes 全 0 时 fail-fast。
- profile mapping：
  - token fit 输出 `mode=token_linear_v1` 和 `ddr_ms_per_cached_token`。
  - byte fit 输出 `mode=byte_linear_v1` 和 `ddr_ms_per_byte`。

修改 `tests/unit/external/test_adapter_boundaries.py`：

- `Ramulator2Adapter.estimate_kv_restore(...)` 仍显式 `NotImplementedError`。
- `Ramulator2CalibrationReference` 不执行外部命令，只验证路径或构造 source 名称。
- `MooncakeCalibrationReference` 不 import Mooncake，只生成 `calibrated_from` 字符串。

### 8.2 集成测试

S8-F 不要求新增完整 replay 集成测试。

可选小 E2E：

- 使用 synthetic observation 拟合 `KVLoadCalibrationFit`。
- 转成 mapping。
- 通过 `KVLoadLatencyProfile.from_mapping(...)` 构造 profile。
- 通过 `build_kv_load_component(...)` 对一个 `BatchShape` 估算 latency。

该 E2E 不进入 `BatchAwareReplayEngine`，因为 S8-F 不改变 replay。

### 8.3 Golden 更新

不需要 golden 更新。

原因：

- 不改变 replay 输出字段。
- 不改变 report schema。
- 不改变已有 profile 默认值。

## 9. 风险与回滚边界

风险 1：用户误以为 S8-F 已经接入真实 Ramulator2 / Mooncake online replay。

控制：

- 文档和 docstring 明确：S8-F 只做 calibration boundary。
- `Ramulator2Adapter.estimate_kv_restore(...)` 继续 `NotImplementedError`。
- Mooncake 不提供 online adapter。

风险 2：拟合参数被误解为硬件真值。

控制：

- `calibrated_from` 必须记录来源。
- observation / fit 只输出 profile 参数，不给“真实硬件精确模拟”的承诺。

风险 3：不同模型 / 硬件 / transfer path 的 observation 被混合拟合。

控制：

- fit helper 对 `model_name`、`hardware_name`、`transfer_path` 一致性 fail-fast。

风险 4：S8-F 变成新外围能力或 CLI，打乱 V1 准出前节奏。

控制：

- 本 Batch 不新增默认 CLI。
- 如果需要 CSV parser、CLI、profile auto-writer，另开外围能力方案。

回滚边界：

- 可整体回滚 `src/infertwin/external/kv_load_calibration.py`、`src/infertwin/external/mooncake.py`、reference exports 和对应测试。
- 不涉及 replay/cache/scheduler/materialization 回滚。

## 10. 完成后如何判断可以进入下一个 Batch

S8-F 完成条件：

1. 外部 calibration observation / fit 的 typed boundary 已定义。
2. token-linear / byte-linear fitting helper 有单测覆盖。
3. fitting result 能转换成现有 `KVLoadLatencyProfile` mapping。
4. Ramulator2 / Mooncake 仍不进入默认 replay 主路径。
5. 没有安装外部 simulator 时，相关测试仍可运行。
6. 未修改 cache lookup / materialization / eviction / event loop。
7. 文档解释清楚：Ramulator2 / Mooncake 如何作为 calibration source，而不是 online replay dependency。

完成后可进入 S8-G：Review / Docs / Archive。

## 11. 执行记录

本轮已完成：

- 新增 `src/infertwin/external/kv_load_calibration.py`。
  - 定义 `KVLoadCalibrationObservation`。
  - 定义 `KVLoadCalibrationFit`。
  - 实现 `fit_token_linear_v1(...)`。
  - 实现 `fit_byte_linear_v1(...)`。
  - 实现 `to_kv_load_profile_mapping(...)`。
- 扩展 `src/infertwin/external/ramulator2.py`。
  - 新增 `Ramulator2CalibrationReference`。
  - 保持 `Ramulator2Adapter.estimate_kv_restore(...)` 为显式 `NotImplementedError`。
- 新增 `src/infertwin/external/mooncake.py`。
  - 新增 `MooncakeCalibrationReference`。
  - 不 import Mooncake SDK，不调用 TransferEngine。
- 更新 `src/infertwin/external/__init__.py`，导出 S8-F calibration boundary。
- 新增 `docs/step8/06_calibration_boundary.md`，说明 Ramulator2 / Mooncake / 实机观测如何变成 `KVLoadLatencyProfile`。
- 新增 / 更新 S8-F 单测。

本轮没有做：

- 没有修改 `KVLoadLatencyProfile` schema。
- 没有新增默认配置项。
- 没有新增 CLI。
- 没有安装、vendor 或运行 Ramulator2。
- 没有安装、vendor 或运行 Mooncake。
- 没有接入默认 replay 主路径。
- 没有修改 scheduler、cache、materialization、event loop、streaming runner。
- 没有改变 report/export schema。

验证结果：

```text
24 passed:
tests/unit/external/test_kv_load_calibration.py
tests/unit/external/test_adapter_boundaries.py
tests/unit/latency/test_kv_load_latency.py

ruff check: passed
git diff --check: passed
```

能否进入下一个 Batch：

- 从 S8-F 自身看，已满足进入 S8-G：Review / Docs / Archive 的技术条件。
- 进入 S8-G 前建议用户 review 两点：
  - 当前 S8-F 只提供 fit helper，不提供 CSV/JSON parser 或 CLI。
  - `calibrated_from` 仍是 string 约定，不是严格 enum。

## 12. 已审批的决定

用户已审批后进入代码开发：

1. 是否接受 S8-F 属于核心仿真器的非 replay 支撑边界，改动等级为 L2。
2. 是否接受 S8-F 不把 Ramulator2 / Mooncake 接入默认 replay 主路径。
3. 是否接受 S8-F 不安装、不 vendor、不运行外部 simulator。
4. 是否接受 S8-F 只新增 calibration observation / fit / profile mapping，而不新增默认 CLI。
5. 是否接受 `KVLoadLatencyProfile` 当前 schema 不修改，只通过 `calibrated_from` 字符串约定记录来源。
6. 是否接受新增 `src/infertwin/external/kv_load_calibration.py`，用于 token-linear / byte-linear v1 拟合。
7. 是否接受新增 `src/infertwin/external/mooncake.py` 作为 Mooncake calibration reference boundary，但不 import Mooncake SDK。
8. 是否接受 `Ramulator2Adapter.estimate_kv_restore(...)` 继续保持 `NotImplementedError`，只补 calibration reference 边界。
9. 是否接受 S8-F 不新增 replay E2E，只做 external / latency profile 小 E2E。
10. 是否接受如果开发中发现必须修改 replay、cache、scheduler、streaming runner 或 `KVLoadLatencyProfile` schema，应暂停并重新评审。
