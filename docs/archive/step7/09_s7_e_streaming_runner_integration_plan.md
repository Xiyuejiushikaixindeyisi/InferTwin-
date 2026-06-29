# S7-E：Streaming Runner Integration 开发方案与执行记录

状态：已完成。

阶段类型：核心仿真器开发。

## 1. Batch 目标

S7-E 的目标是把 S7-D 已经实现的 `TieredPrefixCache` 接入大 trace 主路径：

```text
sweep-streaming -> StreamingCapacitySweepRunner -> StreamingBatchAwareReplayEngine
```

接入后，用户可以显式选择新的 cache backend：

```text
batch_aware_hbm_ddr_lru
```

该模式表示：

```text
fixed-route multi-instance isolated replay
+ vLLM-like batch-aware replay
+ HBM LRU
+ single-instance DDR/CPU LRU pooling tier
+ finish-time materialization
```

S7-E 不改变 `capacity_sweep_streaming` 这个外围运行入口的定位。`capacity_sweep_streaming` 仍然只是执行大 trace capacity sweep 的 runner mode；真正的 cache/replay 语义由 cache mode 决定。

## 2. 为什么需要 S7-E

S7-A 到 S7-D 已经完成了四块基础能力：

- S7-A：model registry 可以表达 `ddr_capacity_blocks` 和 single-instance pooling flags。
- S7-B：`CacheEvent` 可以表达 HBM / DDR tier。
- S7-C：独立 `DDRLRUCache` 已实现。
- S7-D：`TieredPrefixCache` 已实现，replay 仍只消费 `PrefixCache` 协议。

但当前 streaming runner 仍然固定构造：

```python
HBMCache(capacity_blocks=capacity, evictor=LRUEvictor())
```

因此，即使 model registry 中已经配置 DDR capacity 和 pooling，真实 streaming replay 仍然只会产生 HBM hit / miss，不会产生 DDR hit。

S7-E 要补齐这一层集成，但必须保持边界清晰：

- cache backend 选择是核心仿真器能力。
- `capacity_sweep.csv` / event dump 是外围 report/export 能力。
- report 不能反向决定 replay 语义。

## 3. 当前代码现状

主要入口：

```text
src/infertwin/streaming/sweep.py
```

当前关键链路：

```text
StreamingCapacitySweepRunner.run()
-> StreamingRequestShardBuilder(...).build()
-> for capacity in sweep.hbm_capacity_blocks
   -> _run_capacity(...)
      -> for shard in manifest.shards
         -> _build_streaming_replay_engine(...)
         -> _default_cache_for_instance(...)
         -> _build_hbm_cache(capacity, cache_defaults)
         -> engine.run_instance_stream(...)
```

当前 `_build_hbm_cache()` 只支持 HBM：

```python
def _build_hbm_cache(
    *,
    capacity: int,
    cache_defaults: ModelCacheDefaults | None,
) -> HBMCache:
    if cache_defaults is not None and cache_defaults.eviction_policy != "lru":
        raise ValueError(...)
    return HBMCache(capacity_blocks=capacity, evictor=LRUEvictor())
```

当前 metrics 层已经具备 DDR 字段：

```text
CapacitySweepRow.ddr_hit_tokens
CapacitySweepRow.ddr_hit_rate
BatchAwareRequestMetrics.ddr_hit_tokens
LookupMetrics.ddr_hit_tokens
```

因此 S7-E 的核心改动不是扩展 report schema，而是让 streaming runner 能构造正确的 `PrefixCache` backend。

## 4. 核心语义

### 4.1 新增 cache mode

S7-E 新增显式 cache mode：

```text
batch_aware_hbm_ddr_lru
```

保留已有 HBM-only mode：

```text
batch_aware_hbm_lru
```

推荐配置：

```yaml
cache:
  mode: batch_aware_hbm_ddr_lru
  eviction_policy: lru
```

兼容旧配置：

```yaml
cache:
  policy: hbm
  eviction_policy: lru
```

如果 `cache.mode` 缺省，且 `cache.policy: hbm`，则解释为：

```text
batch_aware_hbm_lru
```

原因：

- `simulation.mode: capacity_sweep_streaming` 表示 runner 类型，不应该承载 cache backend 语义。
- `cache.policy` 历史上只表达 HBM policy，不适合继续扩展为多级 backend 名称。
- 新增 `cache.mode` 可以让后续 `batch_aware_hbm_ddr_lru_progressive`、remote pooling mode、sparse cache mode 有稳定扩展点。

### 4.2 HBM capacity 仍由 sweep candidate 覆盖

在 capacity sweep 中：

```text
sweep.hbm_capacity_blocks
```

仍然是本次外层实验要扫描的 HBM prefix cache 容量。

model default cache 中的：

```text
default_cache.hbm_capacity_blocks
```

是模型运行默认值和 metadata。在 capacity sweep 中，它会被当前 sweep candidate 覆盖。

### 4.3 DDR capacity 从 model default 读取

Step7 v1 不 sweep DDR capacity。

DDR capacity 只从 model registry 中读取：

```yaml
default_cache:
  ddr_capacity_blocks: 65536
  pooling:
    enabled: true
    single_instance: true
    ddr_enabled: true
```

原因：

- Step7 的目标是先把 tier hit accounting 做准。
- HBM capacity sweep 是当前外围能力已有主轴。
- DDR capacity sweep 会引入新的产品口径，应放到后续外围能力或 Step7 收口后单独评审。

### 4.4 多实例隔离

S7-E 保持当前 fixed-route multi-instance isolated replay：

```text
每个 shard / instance 独立构造 cache backend
```

因此：

- instance A 的 HBM / DDR resident blocks 不会被 instance B 看到。
- 同一个 prefix 在不同实例之间不会产生 cross-instance hit。
- Step7 仍不是 Mooncake global store，也不是多实例 pooling。

### 4.5 DDR hit 不产生 KV load latency

S7-E 只接入 DDR hit accounting：

```text
kv_load_ms = 0
```

DDR hit 对 TTFT 的影响仍然只体现为：

```text
miss_tokens 减少 -> fitted TTFT backend 看到的 uncached tokens 减少
```

真实 DDR load latency 放到 Step8。

### 4.6 finish-time materialization 继续保持

S7-E 不改变 S7-D 的 materialization 语义：

```text
request prefill finish_time 到达后，miss blocks 同时写 HBM 和 DDR
```

不启用 progressive block visibility。后续如果要做，需要新增：

```text
ProgressiveChunkMaterializationPolicy
batch_aware_hbm_lru_progressive
batch_aware_hbm_ddr_lru_progressive
```

## 5. 代码结构方案

### 5.1 新增 streaming cache factory 模块

新增：

```text
src/infertwin/streaming/cache_factory.py
```

职责：

- 解析 streaming runner 使用的 cache mode。
- 根据 sweep capacity 和 instance model defaults 构造 `PrefixCache`。
- 对不支持的组合 fail-fast。

不负责：

- 不读取 CSV。
- 不跑 replay event loop。
- 不聚合 metrics。
- 不写 report。
- 不做 latency backend 选择。

建议类型：

```python
CACHE_MODE_HBM_LRU = "batch_aware_hbm_lru"
CACHE_MODE_HBM_DDR_LRU = "batch_aware_hbm_ddr_lru"

@dataclass(frozen=True, slots=True)
class StreamingCacheFactoryConfig:
    mode: str
    eviction_policy: str
```

建议函数：

```python
def build_streaming_cache_factory_config(
    config: Mapping[str, Any],
) -> StreamingCacheFactoryConfig:
    ...

def build_streaming_prefix_cache(
    *,
    capacity: int,
    instance_uuid: str,
    cache_defaults: ModelCacheDefaults | None,
    config: StreamingCacheFactoryConfig,
) -> PrefixCache:
    ...
```

`build_streaming_prefix_cache()` 的行为：

```text
mode=batch_aware_hbm_lru
-> HBMCache(capacity_blocks=capacity)

mode=batch_aware_hbm_ddr_lru
-> TieredPrefixCache(
     hbm=HBMCache(capacity_blocks=capacity),
     ddr=DDRLRUCache(capacity_blocks=cache_defaults.ddr_capacity_blocks),
   )
```

### 5.2 修改 streaming runner

修改：

```text
src/infertwin/streaming/sweep.py
```

改动点：

1. `StreamingCapacitySweepRunner.__init__()` 中构造 `self.cache_factory_config`。
2. `_run_capacity()` 中用 `build_streaming_prefix_cache(...)` 替换 `_build_hbm_cache(...)`。
3. `_config_details()` 中新增：

```text
streaming_cache_mode
streaming_cache_eviction_policy
```

4. `_default_cache_by_instance_detail()` 中补充：

```text
ddr_capacity_blocks
pooling_enabled
single_instance_pooling_enabled
ddr_enabled
multi_instance_pooling_enabled
remote_pooling_enabled
ssd_pooling_enabled
```

这样 review 和报告能看出每个 instance 的 model-bound cache defaults。

### 5.3 配置示例

新增：

```text
configs/experiments/step7_streaming_hbm_ddr_sweep.yaml
```

建议内容：

```yaml
simulation:
  mode: capacity_sweep_streaming

cache:
  mode: batch_aware_hbm_ddr_lru
  eviction_policy: lru

sweep:
  hbm_capacity_blocks: [1, 2, 4]

model_registry:
  profile_path: configs/models/registry_step7_pooling.yaml
```

该示例只用于 Step7 DDR mode，不修改默认 HBM-only 示例。

## 6. Fail-Fast 规则

### 6.1 DDR mode 缺少 model registry/runtime resolver

如果启用：

```text
cache.mode=batch_aware_hbm_ddr_lru
```

但没有 `model_registry` / `instance_runtime`，直接失败。

原因：

- DDR capacity 是 model-owned runtime default。
- 不能用全局默认值或静默 0 容量伪造 DDR replay。

### 6.2 DDR mode 但 instance 缺少 default cache

如果某个 shard 的 `instance_uuid` 无法解析到 model default cache，直接失败。

原因：

- fixed-route replay 必须明确知道每个 instance 绑定的 model。
- 否则无法确定 DDR capacity、pooling flags、block size conversion 语义。

### 6.3 DDR mode 但 pooling 未启用

以下情况全部失败：

```text
default_cache.pooling.enabled=false
default_cache.pooling.single_instance=false
default_cache.pooling.multi_instance=true
default_cache.pooling.ddr_enabled=false
default_cache.pooling.remote_enabled=true
default_cache.pooling.ssd_enabled=true
default_cache.ddr_capacity_blocks is None
```

原因：

- Step7 v1 只支持 single-instance DDR/CPU pooling。
- remote / SSD / cross-instance pooling 都不是 S7-E 能力。

### 6.4 非 LRU policy

S7-E 仍只支持：

```text
eviction_policy=lru
```

原因：

- S7-C / S7-D 当前只实现 LRU。
- 未来新增 policy 应通过 stateful policy 接口扩展，不应在 S7-E 中添加隐式兼容。

## 7. 测试计划

### 7.1 单元测试

新增：

```text
tests/unit/streaming/test_cache_factory.py
```

覆盖：

1. 旧配置 `cache.policy: hbm` 缺省映射到 `batch_aware_hbm_lru`。
2. 显式 `cache.mode: batch_aware_hbm_lru` 构造 `HBMCache`。
3. 显式 `cache.mode: batch_aware_hbm_ddr_lru` 构造 `TieredPrefixCache`。
4. DDR mode 缺少 `cache_defaults` 失败。
5. DDR mode 缺少 `ddr_capacity_blocks` 失败。
6. DDR mode pooling disabled 失败。
7. unsupported cache mode 失败。
8. unsupported eviction policy 失败。

### 7.2 集成测试

新增：

```text
tests/integration/test_step7_streaming_hbm_ddr_integration.py
```

覆盖：

1. HBM-only streaming sweep 行为保持不变，`ddr_hit_tokens == 0`。
2. DDR mode 合成 trace 下出现 `ddr_hit_tokens > 0`。
3. HBM capacity sweep candidate 覆盖 model default HBM capacity。
4. DDR capacity 从 model default 读取，不被 sweep 覆盖。
5. 多实例隔离：instance A materialize 的 DDR blocks 不会被 instance B 命中。
6. 选择性开启 `cache_events` 时，event dump 中出现 DDR tier `store` / `lookup_hit`。

建议合成数据形态：

```text
block_size_tokens = 2
hbm_capacity_blocks = 1
ddr_capacity_blocks >= 4

request-1: instance-a, prompt prefix P
request-2: instance-a, same prompt prefix P
```

预期：

- request-1 materialize miss blocks，同时写 HBM 和 DDR。
- HBM 容量较小，只保留 suffix blocks。
- request-2 的 prefix 不能完整从 HBM 命中，但可以从 DDR 产生连续 prefix hit。
- request-level metrics 中 `ddr_hit_tokens > 0`。

多实例隔离测试：

```text
request-1: instance-a, prompt P
request-2: instance-b, prompt P
```

预期：

- instance-b 不应命中 instance-a 的 DDR。

### 7.3 回归测试命令

建议 S7-E 开发完成后运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/streaming/test_cache_factory.py \
  tests/unit/cache/test_tiered_prefix_cache.py \
  tests/unit/cache/test_ddr_lru_cache.py \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py \
  tests/integration/test_streaming_runtime_integration.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py
```

再运行：

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src tests
git diff --check
```

## 8. 验收标准

S7-E 通过条件：

1. `batch_aware_hbm_lru` HBM-only 旧链路不变。
2. `batch_aware_hbm_ddr_lru` 只在显式配置时启用。
3. DDR mode 下合成数据能产生非零 `ddr_hit_tokens`。
4. DDR mode 下 `kv_load_ms` 仍为 0，不影响 Step8 设计空间。
5. 多实例 DDR cache 互相隔离。
6. HBM capacity 由 sweep candidate 控制。
7. DDR capacity 从 model default runtime 读取。
8. cache event dump 中可以观察到 DDR tier event。
9. 不修改 legacy `simulate` / non-streaming `sweep` 行为。
10. targeted tests、ruff 和 `git diff --check` 通过。

## 9. 不做什么

S7-E 不做：

- 不接 Ramulator2。
- 不建 KV load latency。
- 不做 DDR hit promotion 到 HBM。
- 不做 DDR capacity sweep。
- 不做 report 美化。
- 不做新的外围能力。
- 不做 progressive chunk visibility。
- 不做 cross-instance pooling。
- 不改 legacy `simulate` / non-streaming `sweep` 的 HBM-only 口径。

## 10. 影响与风险

### 10.1 影响

S7-E 会让 `sweep-streaming` 具备真正的 single-instance HBM + DDR replay 能力。

现有 replay state machine 不需要理解 DDR 细节，因为多级 cache 仍通过 `PrefixCache` 协议封装。

### 10.2 风险

主要风险：

- cache mode 与 runner mode 混淆。
- HBM capacity sweep 与 model default HBM capacity 混淆。
- DDR capacity 被误认为也参与 sweep。
- DDR hit event 与 request-level token accounting 不一致。
- 多实例 cache 复用对象导致隔离破坏。

规避方式：

- 新增 `cache.mode`，不复用 `simulation.mode`。
- `build_streaming_prefix_cache()` 每个 shard / instance 每个 capacity 都构造新 cache。
- integration tests 覆盖 DDR hit、event dump、多实例隔离。
- config details 显式输出 mode 和每实例 cache defaults。

## 11. 文件改动清单

预计新增：

```text
src/infertwin/streaming/cache_factory.py
tests/unit/streaming/test_cache_factory.py
tests/integration/test_step7_streaming_hbm_ddr_integration.py
configs/experiments/step7_streaming_hbm_ddr_sweep.yaml
```

预计修改：

```text
src/infertwin/streaming/sweep.py
docs/step7/README.md
docs/global_memory.md
```

预计不修改：

```text
src/infertwin/replay/event_loop.py
src/infertwin/streaming/replay.py
src/infertwin/experiment/runner.py
src/infertwin/experiment/sweep.py
src/infertwin/report/sweep.py
```

如果开发中发现必须修改 replay event loop，应暂停并重新评审，因为这说明 S7-D 的 `PrefixCache` 封装边界不够，需要先处理核心设计问题。

## 12. 进入 S7-F 的条件

S7-E 完成后，如满足以下条件，可以进入 S7-F：

- streaming runner 已能在 DDR mode 下产生 DDR hit。
- metrics token invariant 仍成立：

```text
hbm_hit_tokens + ddr_hit_tokens + miss_tokens == prompt_tokens
```

- cache event dump 中 DDR tier event 可观测。
- legacy HBM-only streaming tests 不回退。

S7-F 再统一做 report / metrics / E2E 收口，而不是在 S7-E 中扩张外围输出能力。

## 13. 执行记录

状态：已完成。

### 13.1 做了什么

- 新增 `src/infertwin/streaming/cache_factory.py`。
- 新增 streaming cache mode 常量：
  - `batch_aware_hbm_lru`
  - `batch_aware_hbm_ddr_lru`
- `cache.policy: hbm` 旧配置继续映射到 `batch_aware_hbm_lru`。
- `cache.mode: batch_aware_hbm_ddr_lru` 显式启用 single-instance HBM + DDR LRU tier。
- `StreamingCapacitySweepRunner` 改为通过 cache factory 构造 replay cache backend。
- HBM-only mode 继续构造 `HBMCache`。
- DDR mode 构造 `TieredPrefixCache(HBMCache + DDRLRUCache)`。
- DDR mode 按 instance model defaults 读取 `ddr_capacity_blocks` 和 pooling flags。
- DDR mode 对缺少 model runtime defaults、pooling disabled、remote / SSD / multi-instance pooling、非 LRU policy fail-fast。
- `config_details` 新增：
  - `streaming_cache_mode`
  - `streaming_cache_eviction_policy`
- `model_default_cache_by_instance` 扩展输出：
  - `ddr_capacity_blocks`
  - `pooling_enabled`
  - `single_instance_pooling_enabled`
  - `multi_instance_pooling_enabled`
  - `ddr_enabled`
  - `remote_pooling_enabled`
  - `ssd_pooling_enabled`
- 新增 Step7 DDR streaming 示例：
  - `configs/experiments/step7_streaming_hbm_ddr_sweep.yaml`
  - `data/samples/step7_pooling_trace.csv`
- 新增 cache factory 单测。
- 新增 streaming HBM + DDR 端到端集成测试。
- 更新旧 HBM-only streaming metadata 断言，明确 pooling disabled 状态。

### 13.2 没有做什么

- 没有修改 `replay/event_loop.py`。
- 没有修改 `streaming/replay.py`。
- 没有修改 legacy `simulate` / non-streaming `sweep` 的 HBM-only 口径。
- 没有新增 DDR capacity sweep。
- 没有实现 DDR hit promotion 到 HBM。
- 没有接入 KV load latency；`kv_load_ms` 仍留给 Step8。
- 没有实现 progressive block visibility。
- 没有实现 cross-instance pooling。
- 没有做 report 美化或新的外围能力。

### 13.3 影响

- `sweep-streaming` 现在可以在显式 `cache.mode=batch_aware_hbm_ddr_lru` 下运行 single-instance HBM + DDR replay。
- HBM capacity 仍由 `sweep.hbm_capacity_blocks` 控制。
- DDR capacity 由 model default cache 控制，不参与本次 sweep。
- request / trace metrics 已能自然消费 `ddr_hit_tokens` 和 `ddr_hit_rate`。
- cache event dump 可以观察到 DDR tier `store` 和 `lookup_hit`。
- HBM-only 默认路径保持兼容。

### 13.4 边界

- S7-E 是核心仿真器集成，不是外围 report 能力。
- `capacity_sweep_streaming` 仍是 runner mode；cache/replay 语义由 `cache.mode` 决定。
- DDR tier 仅限同一 fixed-routed instance 内可见。
- 每个 capacity、每个 instance shard 都构造独立 cache backend，实例之间不共享 HBM / DDR resident set。
- Step7 v1 只支持 LRU。
- Step7 v1 只支持 single-instance DDR/CPU pooling。

### 13.5 风险

- 默认 finish-time materialization 仍可能低估长 prefill 期间的 block reuse；该问题保留到 Step9。
- DDR hit 尚未建模 KV load latency；Step8 需要接入 tier-aware latency。
- 当前新增 metadata 字段会让依赖精确 `config_details` dict 的测试或外部脚本需要同步更新。
- 如果后续新增 remote / SSD / multi-instance pooling，必须新增 cache mode 或 backend，不能改写 `batch_aware_hbm_ddr_lru` 语义。

### 13.6 测试结果

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/streaming/test_cache_factory.py
```

结果：

```text
9 passed
```

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_step7_streaming_hbm_ddr_integration.py
```

结果：

```text
2 passed
```

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/unit/streaming/test_cache_factory.py \
  tests/unit/cache/test_tiered_prefix_cache.py \
  tests/unit/cache/test_ddr_lru_cache.py \
  tests/integration/test_step7_streaming_hbm_ddr_integration.py \
  tests/integration/test_streaming_runtime_integration.py \
  tests/integration/test_true_streaming_capacity_sweep_runner.py \
  tests/integration/test_v1_review_repair_e2e.py
```

结果：

```text
39 passed
```

已通过：

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src tests
git diff --check
```

### 13.7 是否建议进入下一 batch

建议进入 S7-F：Report / Metrics / E2E。

原因：

- S7-E 已经让 streaming runner 能产生 DDR hit。
- request / trace metrics 已能聚合 DDR hit。
- 下一步应集中做 report / metrics / E2E 收口，确认 CSV、summary、cache event dump 和文档口径完整一致。
