# Ramulator2 学习笔记

状态：初版调研完成。

调研来源：

- 本地临时 clone：`/tmp/ramulator2`
- GitHub: <https://github.com/CMU-SAFARI/ramulator2>
- perf comparison: <https://github.com/CMU-SAFARI/ramulator2/tree/main/perf_comparison>

说明：Ramulator2 只作为 Step8 的知识和未来 adapter 设计输入。本阶段不会把 Ramulator2 vendor 到 InferTwin 仓库。

## 1. Ramulator2 是什么

Ramulator2 是 cycle-accurate DRAM simulator。它关注的是 DRAM memory system 内部行为，例如：

- DRAM 标准：DDR3、DDR4、DDR5、LPDDR5、GDDR6、HBM/HBM2/HBM3。
- Memory controller。
- DRAM scheduler。
- Row policy。
- Refresh manager。
- Address mapping。
- Memory request queue。
- request accept/reject 和 completion callback。

它不是 LLM 推理仿真器，也不直接理解：

- KV cache block。
- PageAttention。
- prefix cache hit。
- vLLM scheduler。
- TTFT / TPOT。

因此 InferTwin 不能把 `ddr_hit_tokens` 直接交给 Ramulator2 就得到 TTFT。中间必须有一层 mapping：

```text
ddr_hit_tokens / blocks
-> kv_load_bytes
-> memory access trace or fitted coefficient
-> kv_load_ms
```

## 2. 使用方式

### 2.1 Standalone mode

README 中的主路径是构建 `ramulator2` 可执行文件，然后用 YAML config 启动：

```bash
./ramulator2 -f ./example_config.yaml
```

也可以通过命令行传入 YAML dump。`perf_comparison/perf_comparison.py` 就采用这种方式改写 config 并运行：

```text
./ramulatorv2 --config <yaml dump>
```

Standalone mode 适合做：

- 小 trace memory simulation。
- benchmark。
- 参数 sweep。
- 离线校准 DDR/HBM memory latency coefficient。

不适合作为 InferTwin 大 trace 主 replay 的同步依赖。

### 2.2 Library / wrapper mode

Ramulator2 构建后会生成：

```text
ramulator2
libramulator.so
```

README 给出的外部 simulator 集成路径是：

1. 用 YAML config 创建 frontend 和 memory system。
2. `frontend.connect_memory_system(memory_system)`。
3. `memory_system.connect_frontend(frontend)`。
4. 外部 simulator 调用 `receive_external_requests(...)` 或 `memory_system.send(...)`。
5. Ramulator2 request 完成后触发 callback。
6. simulator 结束时调用 `finalize()`。

关键接口文件：

```text
src/base/request.h
src/base/config.h
src/frontend/frontend.h
src/memory_system/memory_system.h
```

其中 `Request` 包含：

- address。
- request type：Read / Write。
- source id。
- arrive / depart cycle。
- callback。

`IMemorySystem.send(Request)` 会返回是否接受 request。拒绝通常意味着 controller queue 满或当前无法接收。

这对 InferTwin 的启发是：未来如果要建 KV-load queue，应显式建模 accept/reject/wait，而不是把所有 load latency 都静态相加。

## 3. 代码结构

Ramulator2 的源码目录按组件组织：

```text
src/base/             # config, request, factory, stats, logging
src/frontend/         # trace frontend, external wrapper frontend
src/memory_system/    # GenericDRAM 等 memory system
src/dram_controller/  # controller, scheduler, row policy, refresh, plugins
src/dram/             # DDR/HBM/LPDDR/GDDR implementations
src/addr_mapper/      # address mapping
src/translation/      # address translation
perf_comparison/      # 与其他 DRAM simulator 的性能对比脚本和配置
```

这种结构值得 InferTwin 借鉴：

- 接口和实现分开。
- 配置驱动具体实现。
- 外部 simulator 通过 wrapper 接入，而不是把内部实现绑死。

Step8 也应延续这个原则：

```text
InferTwin replay
-> KVLoadLatencyComponent interface
-> fitted / static / ramulator2-calibrated implementation
```

## 4. Memory trace frontend

`perf_comparison/configs/ramulatorv2.yaml` 使用：

```yaml
Frontend:
  impl: LoadStoreTrace
  path: ./traces/stream_5M_R8W2_ramulatorv2.trace
```

`LoadStoreTrace` 的 trace 格式是：

```text
LD 0x...
ST 0x...
```

`ReadWriteTrace` 的 trace 格式是：

```text
R <addr_vec>
W <addr_vec>
```

重要限制：当前 `LoadStoreTrace` 会把 trace 文件读入内存后再模拟。这说明它适合小型标定 trace，不适合作为 InferTwin 11G 业务 trace 的直接在线 replay 组件。

## 5. 与 KV load 的关系

KV load 可以从两个层次接 Ramulator2。

### 5.1 V1 推荐：离线标定

流程：

```text
选择模型 / 硬件 / DDR 配置
-> 生成代表性 memory trace
-> 运行 Ramulator2
-> 得到 latency / bandwidth 统计
-> 拟合 ddr_ms_per_byte 或 ddr_ms_per_cached_token
-> 写入 InferTwin model / instance latency profile
```

优点：

- 不破坏 InferTwin streaming replay。
- 不让大 trace 依赖外部 C++ simulator。
- 结果可复现、可测试。
- 出错时可 fallback 到默认超参数。

缺点：

- 不建模请求间真实 memory controller queue。
- 不建模具体 address mapping 对每个 block 的影响。
- 无法反映 load 与 compute 的细粒度 overlap。

### 5.2 未来可选：online wrapper

流程：

```text
InferTwin iteration
-> KV blocks -> memory addresses
-> Ramulator2 request queue
-> callback completion
-> KV load finish event
-> replay time advances
```

这条路线更真实，但会显著扩大 Step8：

- 需要生成稳定 memory address。
- 需要 block/page 到 memory request 的拆分。
- 需要处理 request queue full。
- 需要同步 Ramulator2 clock 和 InferTwin ms time。
- 需要定义 load 与 compute 是否 overlap。
- 需要引入 KV load completion event。

建议不放入 Step8 v1。

## 6. Step8 可借鉴的接口语义

Ramulator2 中对 InferTwin 最有价值的不是具体 DRAM 算法，而是接口语义：

| Ramulator2 概念 | InferTwin 可借鉴设计 |
|---|---|
| YAML config | `KVLoadLatencyProfile` 和 model runtime defaults |
| frontend / memory system 分离 | replay core 不直接依赖 Ramulator2 |
| request accepted / rejected | 未来 KV-load queue wait / backpressure |
| request callback | 未来 KV load completion event |
| `arrive` / `depart` cycle | 未来精细 load latency 统计 |
| memory standards implementations | 区分 DDR / HBM / LPDDR / HBM3 的标定参数来源 |

## 7. 对 Step8 的建议

Step8 v1 应采用：

```text
KV load latency = fitted/static function(ddr_load_tokens, ddr_load_bytes, batch shape)
```

Ramulator2 在 Step8 v1 中只作为 calibration source：

```text
calibrated_from: ramulator2_git
```

如果要接真实 Ramulator2 adapter，应另开 opt-in calibration harness，不进入默认 replay 主路径。

## 8. 遗留问题

需要存储/通信同事进一步确认：

1. KV load 是否可以与 prefill compute overlap。
2. DDR/CPU -> HBM 的真实传输路径：PCIe、HCCL、DMA、RDMA 或 CPU copy。
3. load 粒度是 block、page、layer 还是 request。
4. 多请求同时 load 时是共享带宽、独立 stream，还是有优先级调度。
5. Ramulator2 只覆盖 DRAM access，不覆盖完整通信链路；通信链路是否需要单独建模。
