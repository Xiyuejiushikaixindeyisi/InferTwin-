# HitFloor 技术路线与代码实现方案

## 1. 第一版目标

HitFloor 第一版要做成一个离线仿真工具：

```text
现网 trace + 固定实例路由 + cache/latency 配置
  -> 仿真实例内 prefix KV cache hit
  -> 估算每条请求 TTFT
  -> sweep cache 容量
  -> 输出目标 P90 TTFT 对应的 hit floor
```

确认边界：

- `instance_uuid` 表示请求已经被路由到实例，HitFloor 不改路由。
- 第一版只做实例内 / 机器内 cache，不做跨机互联。
- `batch_admission_delay = 0`，不建模 continuous batching admission wait。
- 只建模 prefill TTFT，不建模 TPOT 和 decode KV。
- cache 只保存 hash key 和 metadata，不保存全量 token ids 或真实 KV tensor。
- tokenizer / chat template 根据请求中的 `model` 字段选择。
- 有限容量策略只做 HBM LRU + DDR LRU。
- 报告输出 `csv + summary.md`。

## 2. 落地路线

按 6 个可验收阶段实现：

| 阶段 | 目标 | 产物 |
| --- | --- | --- |
| P1 | 固定实例路由 + tokenizer/chat template + 无限 HBM prefix hit | ideal hit metrics |
| P2 | 接入 latency backend，跑通 TTFT | request-level TTFT |
| P3 | 加 HBM 容量，模拟 vLLM 风格 LRU block 生命周期 | HBM events + eviction |
| P4 | 跑通 HBM 淘汰后的 TTFT | HBM-only report |
| P5 | 加固定 DDR 容量，模拟 Mooncake 类 DDR 池化命中 | HBM/DDR/miss split |
| P6 | 跑通 HBM+DDR 后的 hit floor 报告 | `hit_floor.csv` |

主流程：

```text
TraceRecord
  -> ParsedRequest
  -> TokenizationResult
  -> PrefixBlock[]
  -> SimulatedInstance.lookup()
  -> LatencyBackend.estimate()
  -> finish_time materialization
  -> RequestMetrics
  -> ConfigMetrics
  -> HitFloorReport
```

## 3. 目录与模块

```text
tokenizers/                  # 离线 tokenizer 资源
configs/                     # model / hardware / backend / experiment 配置
src/hitfloor/trace/          # CSV 读取与校验
src/hitfloor/request/        # request 解析、tokenizer registry、chat template、block hash
src/hitfloor/cache/          # Infinite HBM、HBM LRU、DDR LRU、two-level cache
src/hitfloor/instance/       # 按 instance_uuid replay，请求生命周期
src/hitfloor/latency/        # fitted TTFT backend、backend 接口与外部 simulator adapter
src/hitfloor/external/       # AIConfigurator / MKsim / Ramulator2 adapter
src/hitfloor/experiment/     # sweep、metrics、hit floor search
src/hitfloor/report/         # CSV 与 summary.md
```

第一版新增重点文件：

```text
src/hitfloor/request/model_resolver.py
src/hitfloor/request/tokenizer_registry.py
src/hitfloor/request/chat_template.py
src/hitfloor/request/block_hasher.py
src/hitfloor/cache/infinite_hbm.py
src/hitfloor/cache/hbm_lru.py
src/hitfloor/cache/ddr_lru.py
src/hitfloor/cache/two_level.py
src/hitfloor/cache/events.py
src/hitfloor/instance/replay.py
src/hitfloor/report/summary.py
src/hitfloor/report/hit_floor.py
```

## 4. 数据模型

核心 dataclass：

```text
TraceRecord:
  request_id, tenant_id, instance_uuid, request_params, service_start_time

ParsedRequest:
  model, messages, tools, max_tokens, stream, raw

TokenizationResult:
  request_id, model, tokenizer_profile, prompt_token_ids, prompt_tokens,
  chat_template_hash, tokenizer_config_hash, kv_bytes_per_token

PrefixBlock:
  block_key, content_hash, block_index, token_count, size_bytes

CacheBlockMeta:
  block_key, tier, block_index, token_count, size_bytes,
  created_time_ms, last_access_time_ms, hit_count, refcount

LookupResult:
  hbm_hit_blocks, ddr_hit_blocks, miss_blocks,
  hbm_hit_tokens, ddr_hit_tokens, miss_tokens

RequestMetrics:
  request_id, instance_uuid, model, prompt_tokens,
  hbm_hit_tokens, ddr_hit_tokens, miss_tokens,
  effective_hit_rate, kv_restore_time_ms, prefill_compute_time_ms,
  ttft_ms, finish_time_ms
```

约束：`prompt_token_ids` 只在 tokenization 到 block hash 阶段使用，不写入 cache。

## 5. 核心算法

### 5.1 Tokenizer Registry

目录：

```text
tokenizers/<profile>/manifest.yaml
tokenizers/<profile>/tokenizer.json
tokenizers/<profile>/tokenizer_config.json
tokenizers/<profile>/chat_template.jinja
tokenizers/<profile>/kv_meta.json
```

算法：

```text
startup:
  load tokenizers/*/manifest.yaml
  build model_alias -> tokenizer_profile index

per request:
  model = request_params["model"]
  profile = resolve(model)
  tokenizer = registry.get(profile)
  rendered_prompt = render_chat_template(messages, tools, profile)
  token_ids = tokenizer.encode(rendered_prompt)
```

PrefixLens 参考点：

- tokenizer 以本地 vendor 方式管理。
- 使用 `AutoTokenizer.from_pretrained(local_path, trust_remote_code=True)`。
- GLM-5 支持独立 `chat_template.jinja`。
- `kv_meta.json` 提供 `kv_bytes_per_token`。

### 5.2 Block Hash

每个 prompt 按 `block_size_tokens` 切块：

```text
content_hash[i] = sha256(serialize(block_token_ids[i]))
block_key[i] = sha256(parent_key, model_namespace, cache_scope, content_hash[i])
parent_key = block_key[i]
```

规则：

- cache lookup 使用 `block_key`，不用单独的 `content_hash`。
- `block_key[i]` 表示 block 0 到 block i 的完整 prefix path。
- `cache_scope` 配置为 `tenant_isolated` 或 `model_shared`。
- 默认建议先用 `tenant_isolated`。

### 5.3 Prefix Hit

只统计连续 prefix hit：

```text
for block in prefix_blocks:
  if cache.contains(block.block_key):
    hit.append(block)
  else:
    break

miss = prefix_blocks[len(hit):]
```

第一个 miss 后，后续 block 即使存在也不计入 effective hit。

### 5.4 时间因果

请求不能在开始时立即把 miss blocks 写入 cache。统一规则：

```text
at service_start_time:
  lookup current materialized cache
  estimate TTFT
  finish_time = service_start_time + TTFT
  schedule materialization at finish_time

at finish_time:
  insert newly generated miss blocks
```

P1 无限 HBM 阶段如未接 latency，可令 `TTFT = 0`，但仍走同一事件接口。

### 5.5 无限 HBM

每个实例一个无限 HBM cache：

```text
instance_caches[instance_uuid] = InfiniteHBMCache()
```

行为：

- lookup prefix。
- miss blocks 在 finish_time materialize。
- 不淘汰。
- 不跨实例共享。

验收：同实例重复 prompt 命中，不同实例不命中。

### 5.6 TTFT

第一版：

```text
TTFT = kv_restore_time + prefill_compute_time
```

Batch D 默认 fitted TTFT backend：

```text
prefill_compute_time =
  intercept_ms
  + scheduled_prefill_tokens * ms_per_uncached_token
```

外部接口：

```text
AIConfigurator / MKsim -> 标定 fitted TTFT 参数，或高精度 prefill_compute_time
Ramulator2             -> ddr_restore_time
```

### 5.7 HBM LRU

有限 HBM cache：

```text
HBMLRUCache(capacity_blocks or capacity_bytes)
```

行为：

```text
hit:
  update last_access_time
  move block to MRU

insert:
  while capacity exceeded:
    evict oldest unpinned block
  insert as MRU

request lifecycle:
  pin hit blocks at start
  insert miss blocks at finish
  unpin request blocks at finish
```

事件：

```text
block_hit_hbm
block_miss
block_created
block_pinned
block_unpinned
block_evicted_hbm
eviction_blocked_by_pinning
```

### 5.8 DDR LRU

两级查询：

```text
for block in prefix_blocks:
  if hbm.contains(block):
    count HBM hit
    continue
  if ddr.enabled and ddr.contains(block):
    count DDR hit
    optionally promote to HBM
    continue
  break
```

生命周期：

```text
HBM eviction -> demote to DDR
DDR full     -> evict DDR LRU
DDR hit      -> count restore time
DDR hit      -> promotion controlled by config
```

第一版建议默认：

```yaml
ddr:
  enabled: true
  capacity_gb: 512
  policy: lru
  promote_on_hit: true
```

### 5.9 Hit Floor Search

对每个 cache 配置跑一次 replay：

```text
for hbm_capacity in hbm_capacity_grid:
  run_simulation(hbm_capacity, ddr_capacity)
  collect p50/p90/p99 TTFT and hit metrics
```

对每个目标 TTFT：

```text
candidates = configs where p90_ttft_ms <= target
choose minimal effective_hit_rate
tie break by lower hbm_capacity, then lower ddr_capacity
```

输出：

```text
request_metrics.csv
config_results.csv
hit_floor.csv
summary.md
```

## 6. 编码顺序

### Step 1：Tokenizer 与 Hash Pipeline

实现：

- `model_resolver.py`
- `tokenizer_registry.py`
- `chat_template.py`
- `block_hasher.py`
- `tokenizers/glm-v5/manifest.yaml`
- `configs/models/glm-v5.yaml`

测试：

- model alias 解析。
- 同 prompt hash 稳定。
- 不同 prefix path 不误命中。

### Step 2：Trace 到 SimulationRequest

实现：

- CSV 字段校验。
- request JSON 校验。
- `service_start_time` 排序。
- 构造内部请求对象。

测试：

- 缺字段报错。
- 非法 JSON 报错。
- 样例 trace 可解析。

### Step 3：无限 HBM Replay

实现：

- `InfiniteHBMCache`
- `SimulatedInstance`
- `ReplayEngine`
- request metrics 初版。

测试：

- 同实例复用。
- 跨实例不复用。
- prefix miss 后停止。

### Step 4：Latency + Finish-Time Materialization

实现：

- latency input/output。
- fitted TTFT backend。
- finish event。
- shape memoization。

测试：

- TTFT 随 miss_tokens 增加。
- materialization 只在 finish_time 后可见。

### Step 5：有限 HBM LRU

实现：

- `HBMLRUCache`
- block pin/unpin。
- LRU eviction。
- cache events。

测试：

- LRU 顺序。
- pinned block 不淘汰。
- 大容量接近无限 HBM。

### Step 6：HBM-only Report

实现：

- HBM capacity sweep。
- `config_results.csv`。
- `summary.md`。

测试：

- 默认配置可跑完。
- 输出 P90 TTFT。

### Step 7：DDR Two-Level Cache

实现：

- `DDRLRUCache`
- `TwoLevelPrefixCache`
- demotion / promotion。
- HBM/DDR/miss 分离统计。

测试：

- HBM miss 后 DDR hit。
- DDR LRU 淘汰。
- 关闭 DDR 等价 HBM-only。

### Step 8：最终报告与 CLI

实现：

- `hitfloor simulate --config ...`
- `request_metrics.csv`
- `config_results.csv`
- `hit_floor.csv`
- `summary.md`

测试：

- 同输入多次运行结果一致。
- `hit_floor.csv` 字段完整。

## 7. 里程碑验收

| 里程碑 | 必须通过 |
| --- | --- |
| M1 | 样例 trace 能输出无限 HBM ideal hit |
| M2 | 每条请求能输出 TTFT，且 finish_time 生效 |
| M3 | HBM LRU 淘汰、pinning、events 可测试 |
| M4 | HBM-only capacity sweep 可输出报告 |
| M5 | DDR hit、demotion、promotion、DDR eviction 可测试 |
| M6 | `hit_floor.csv` 和 `summary.md` 可生成 |

## 8. 第一版配置草案

```yaml
trace:
  path: data/samples/sample_trace.csv

tokenizers:
  root: tokenizers
  default_profile: glm-v5
  cache_scope: tenant_isolated

cache:
  block_size_tokens: 16
  hbm:
    mode: lru
    capacity_blocks: [1024, 4096, 16384]
  ddr:
    enabled: false
    capacity_gb: 512
    policy: lru
    promote_on_hit: true

latency:
  backend: fitted_ttft
  fitted_ttft:
    profile: glm-v5_ascend910c_default
    function: token_linear_v1
    intercept_ms: 0.0
    ms_per_uncached_token: 0.02
    calibrated_from: manual_default

targets:
  p90_ttft_ms: [500, 800, 1000, 1500]

output:
  directory: reports
```

## 9. 待确认决策

- 是否将 PrefixLens 的 `models/glm5_tokenizer/` vendor 到 HitFloor 的 `tokenizers/glm-v5/`。
- DDR 第一版是否严格限定为实例内 / 机器内 DDR pool。
- DDR hit 后默认 `promote_on_hit: true` 是否接受。
- HBM 容量是否先按 `capacity_blocks` 实现，再补 GB 换算。
