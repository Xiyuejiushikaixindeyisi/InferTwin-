# pre_hitfloor 状态说明

## 当前状态

`pre_hitfloor` 当前整体 pending，不归档。

原因：

- `prefix_cache_hit_factors_and_priorities.md` 对真实系统 prefix cache hit 影响因素、优先级和误差来源有重要参考价值。
- `ttft_modeling.md` 对 HitFloor 第一版 TTFT 组成、命名和边界有重要参考价值。
- 当前 HitFloor 外围能力需要先进入背景、产品形态和方案讨论；`pre_hitfloor` 中未达成一致的 P0 风险项后续再回头处理。

## 后续使用方式

进入 HitFloor 外围能力设计时，可以引用本目录文档作为理论参考，但不得把 pending 的能力写成已实现能力。

当前 pending 的重点包括：

- active KV occupancy-aware HBM capacity 是否作为 HitFloor 前置必须实现。
- pooling mode / DDR visibility 是否先支持 `write_through_on_materialization` 与 `hbm_evict_offload_ddr` 两种接口。
- LCP / hot prefix telemetry 的 exact prefix、阈值聚合和 streaming aggregation 边界。

如果后续 HitFloor 方案发现必须依赖这些能力，再回到 `pre_hitfloor` 继续技术路线和代码方案审批。
