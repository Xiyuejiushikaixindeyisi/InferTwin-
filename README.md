# HitFloor

HitFloor is an offline simulator for large-scale LLM inference service clusters. It replays traces through tokenizer, scheduler, cache, and latency models, then exposes structured metrics for outer capabilities such as capacity sweep reports.

## Project Layout

```text
HitFloor/
  configs/              # Model, hardware, backend, and experiment configs
  data/                 # Local trace data, ignored by default
  docs/                 # Product and design documents
  notebooks/            # Exploratory analysis
  reports/              # Generated simulation reports
  scripts/              # Thin operational scripts
  src/hitfloor/         # Main Python package
  tests/                # Unit and integration tests
```

## First Commands

```bash
PYTHONPATH=src python -m hitfloor.cli.main --help
PYTHONPATH=src python -m hitfloor.cli.main simulate --config configs/experiments/default.yaml
PYTHONPATH=src python -m hitfloor.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml
python scripts/run_simulation.py --config configs/experiments/default.yaml
pytest
```

When HitFloor is installed as a package, the formal entrypoint is:

```bash
hitfloor simulate --config configs/experiments/default.yaml
hitfloor sweep --config configs/experiments/step6_capacity_sweep.yaml
hitfloor validate-trace --input data/samples/sample_trace.csv
```

The scripts in `scripts/` are thin wrappers for local development.

The current repository contains the maintainable project skeleton and stable extension points for latency backends such as AIConfigurator, MKsim, and Ramulator2.

## Architecture Boundary

HitFloor separates the **core simulator** from **outer capabilities**.

The core simulator owns replay semantics and structured results:

- trace records -> `SimulationRequest` build.
- tokenizer / chat template selection.
- prefix block hashing.
- gateway / routing simulation when enabled.
- scheduler replay.
- instance queue simulation when enabled.
- cache lookup, materialization, eviction, and event stats.
- multi-tier cache backends when enabled.
- latency backend calls.
- deterministic request / iteration / sweep metrics.

Outer capabilities consume the core simulator output:

- HitFloor tables such as `capacity_sweep.csv`.
- Markdown summaries.
- CLI and scripts.
- dashboards, notebooks, and batch jobs.
- future P90 target matching / hit floor search.
- future strategy comparison reports.

Outer capabilities must not change core replay semantics. If a new product surface
needs different semantics, add a new replay mode, cache backend, policy, adapter,
or result schema instead of changing existing meanings in place.

Every new stage or development batch must explicitly state whether it develops
the core simulator or an outer capability.

## Current Status

Step1-Step6 have built the core offline replay skeleton:

- CSV trace reader for routed requests with `instance_uuid`.
- OpenAI-style request parser with strict documented schema checks.
- tokenizer / chat template registry, including the GLM-5 profile.
- hash-only prefix block generation.
- fixed-routing, multi-instance isolated replay.
- vLLM-like continuous batching and chunked prefill approximation.
- fitted TTFT latency backend.
- infinite HBM replay and finite HBM LRU replay.
- streaming `cache_events.csv`.
- HBM capacity sweep runner.
- package CLI as the formal entrypoint; `scripts/` are wrappers.
- clean `ruff check`, `ruff format --check`, and full pytest baseline.

Step1-Step6 are a simulation foundation. Step6 v1 implements an `HBM Cache Capacity Sweep Report`, not an automatic P90 target solver. The Step6 runner returns structured sweep results; `capacity_sweep.csv` and `summary.md` are report/export outputs around the core simulator.

Step6 v1 boundaries:

- Sweep only `hbm_capacity_blocks`; GB input is out of scope.
- Output capacity-to-metrics tables, not P90 target matching.
- Build requests once and reuse them across capacity candidates.
- Keep DDR fields in the sweep schema with zero values for future multi-tier cache work.
- Disable cache event detail by default; allow event dump only for explicitly selected capacities.
- Keep replay single-threaded first; parallel sweep execution is future work.

The project is now in a core-simulator engineering optimization stage before Step7.

Future core-simulator capabilities:

- DDR / SSD / multi-tier cache.
- KV load latency.
- gateway routing simulation.
- instance-side queueing policy simulation.
- external AIConfigurator / MkSim production adapters.
- cross-instance KV pooling.

Future outer capabilities:

- target-based hit floor solver / P90 target matching.
- dashboard / Web UI.
- strategy comparison reports.

## Document Index

Active docs:

```text
docs/global_memory.md
docs/code_development_requirements.md
docs/hitfloor_product_design.md
docs/core_simulator_technical_plan.md
```

Notes / integration references:

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
docs/notes/internal_model_deployment_method.md
```

Archived stage docs:

```text
docs/archive/pre_step6_cleanup_plan.md
docs/archive/implementation_plan.md
docs/archive/future_simulation_extensions.md
docs/archive/development_status.md
docs/archive/step4/
docs/archive/step5/
docs/archive/step6/
```

## Latency Strategy

Batch D defaults to a fitted TTFT function backend:

```text
FittedTTFTLatencyBackend / fitted_ttft
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

AIConfigurator and MkSim are treated first as calibration sources for fitted profiles, and only later as optional high-precision replay backends.

## Core Semantics (Frozen)

The following HitFloor semantics are stable. Future work must not silently change
their meaning. If a later stage needs different semantics, add a new Python type,
data structure, adapter, or interface instead of reusing these names with changed
meaning.

- `batch_size`: the number of request slices scheduled in one scheduler iteration.
  It is request-count semantics, not token-count semantics.
- `max_num_batched_tokens`: the token budget for one scheduler iteration. It is
  not batch size.
- `max_num_seqs`: the scheduler's per-iteration request/sequence upper bound. It
  is not the business "maximum supported concurrency" under TTFT/TPOT SLO.
- Business maximum supported concurrency means the largest stable request
  concurrency that still satisfies TTFT/TPOT constraints. It must be measured or
  searched at experiment level, not inferred from `batch_size` or `max_num_seqs`.
- `BatchShape`: scheduler output for one replay iteration. It is not a direct
  AIConfigurator or MkSim input. External simulators must use explicit converters
  and simulator-specific input types.
- `ScheduledSlice`: one request's prefill work scheduled in one iteration.
  `scheduled_prefill_tokens` must be positive.
- `cached_prefix_tokens`: tokens hit from prefix cache at first-schedule-time
  lookup.
- `previous_chunk_tokens`: tokens already computed by earlier chunks of the same
  request.
- `computed_tokens_before = cached_prefix_tokens + previous_chunk_tokens`.
- `miss_tokens = prompt_tokens - cached_prefix_tokens` at cache lookup time.
  Miss tokens may be computed across one or more scheduler iterations.
- A 100% prefix-hit request has `miss_tokens = 0`, produces no `ScheduledSlice`,
  and must use a zero-miss fast-finish path in batch-aware replay.
- Cache lookup happens when a request is first eligible to be considered by the
  scheduler, not when it arrives in the trace.
- Cache materialization happens only after a request's prefill finishes. Blocks
  materialized at an iteration finish are not visible within the same iteration.
  This is a conservative offline replay rule, not a physical vLLM block-manager
  timeline. Real vLLM / vLLM-Ascend deployments may expose full blocks
  progressively during prefill; HitFloor's `batch_aware_hbm_lru` mode does not.
  If progressive block visibility is needed later, add a new replay/cache mode
  instead of changing this mode's materialization semantics.
- `ttft_ms = finish_time_ms - arrival_time_ms`.
- `scheduler_wait_ms = first_scheduled_time_ms - arrival_time_ms`. For a zero-miss
  request, `first_scheduled_time_ms` is the time when the replay first considers
  and fast-finishes the request.
- Step4 scope is fixed-routing, multi-instance isolated replay. Requests are
  grouped by `instance_uuid` from the trace, each instance replays independently
  with its own infinite HBM prefix cache, and HitFloor does not simulate routing
  decisions. Step4 does not model DDR, SSD, cross-instance KV pooling, PD ratio
  search, decode TPOT, MTP, or KV transfer time.
