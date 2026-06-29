# InferTwin

InferTwin is an offline simulator for large-scale LLM inference service clusters. It replays traces through tokenizer, scheduler, cache, and latency models, then exposes structured metrics for outer capabilities such as capacity sweep reports.

## Project Layout

```text
InferTwin/
  configs/              # Model, hardware, backend, and experiment configs
  data/                 # Local trace data, ignored by default
  docs/                 # Product and design documents
  notebooks/            # Exploratory analysis
  reports/              # Generated simulation reports
  scripts/              # Thin operational scripts
  src/infertwin/         # Main Python package
  tests/                # Unit and integration tests
```

## First Commands

Use `sweep-streaming` for large traces. `simulate` and `sweep` are kept for
small traces, local debugging, and regression checks because they use in-memory
request/result paths.

```bash
PYTHONPATH=src python -m infertwin.cli.main --help
PYTHONPATH=src python -m infertwin.cli.main sweep-streaming --config <streaming_capacity_sweep.yaml>
PYTHONPATH=src python -m infertwin.cli.main simulate --config configs/experiments/default.yaml
PYTHONPATH=src python -m infertwin.cli.main sweep --config configs/experiments/step6_capacity_sweep.yaml
PYTHONPATH=src python -m infertwin.cli.main normalize-trace --input <unrouted.csv> --output <routed.csv> --instance-uuid <instance>
python scripts/run_simulation.py --config configs/experiments/default.yaml
pytest
```

When InferTwin is installed as a package, the formal entrypoint is:

```bash
infertwin sweep-streaming --config <streaming_capacity_sweep.yaml>
infertwin simulate --config configs/experiments/default.yaml
infertwin sweep --config configs/experiments/step6_capacity_sweep.yaml
infertwin validate-trace --input data/samples/sample_trace.csv
infertwin normalize-trace --input <unrouted.csv> --output <routed.csv> --instance-uuid <instance>
```

The scripts in `scripts/` are thin wrappers for local development.

`validate-trace` is currently a small-trace validation helper and still reads the
trace into memory. It will be replaced by streaming validation in a later V2
engineering pass; do not use the current command directly on 11G production
traces.

The current repository contains the maintainable project skeleton and stable extension points for latency backends such as AIConfigurator, MKsim, and Ramulator2.

## Architecture Boundary

InferTwin separates the **core simulator** from **outer capabilities**.

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

- InferTwin tables such as `capacity_sweep.csv`.
- Markdown summaries.
- CLI and scripts.
- dashboards, notebooks, and batch jobs.
- future P90 target matching / hit floor search.
- future strategy comparison reports.

Outer capabilities must not change core replay semantics. If a new product surface
needs different semantics, add a new replay mode, cache backend, policy, adapter,
or result schema instead of changing existing meanings in place.

Every new stage or development batch must explicitly state whether it develops
the core simulator or an outer capability. New outer capabilities should wait
until the V1 core simulator exit criteria are satisfied.

## Current Status

Step1-Step6 have built the core offline replay skeleton:

- CSV trace reader for routed requests with `instance_uuid`.
- OpenAI-style request parser with strict documented schema checks.
- tokenizer / chat template registry, including the GLM-5 profile.
- hash-only prefix block generation.
- fixed-routing, multi-instance isolated replay.
- vLLM-like continuous batching and chunked prefill approximation.
- fitted TTFT latency backend.
- instance latency profiles for true streaming replay.
- model registry and instance-to-model binding for default TTFT fallback.
- model-owned runtime defaults for streaming request build and replay setup.
- calibration-failure fallback policy schema for future external TTFT calibration.
- infinite HBM replay and finite HBM LRU replay.
- vLLM-like cached-token accounting for replay metrics.
- profile schema / RunSpec / ConfigGuard foundation.
- profile-aware request build and tokenizer-stage long-request rejection.
- ServingLatencyProfile and materialization policy interfaces.
- streaming `cache_events.csv`.
- HBM capacity sweep runner.
- true streaming request sharding, replay, metric aggregation, and capacity sweep runner.
- streaming benchmark harness for throughput and memory observation.
- package CLI as the formal entrypoint; `scripts/` are wrappers.
- outer `normalize-trace` utility for converting explicitly unrouted traces into
  single-instance routed traces before replay.
- clean `ruff check`, `ruff format --check`, and full pytest baseline.

Step1-Step6 are a simulation foundation. Step6 v1 implements an `HBM Cache Capacity Sweep Report`, not an automatic P90 target solver. The Step6 runner returns structured sweep results; `capacity_sweep.csv` and `summary.md` are report/export outputs around the core simulator.

Step6 v1 boundaries:

- Sweep only `hbm_capacity_blocks`; GB input is out of scope.
- Output capacity-to-metrics tables, not P90 target matching.
- Build requests once and reuse them across capacity candidates.
- Keep DDR fields in the sweep schema with zero values for future multi-tier cache work.
- Disable cache event detail by default; allow event dump only for explicitly selected capacities.
- Keep replay single-threaded first; parallel sweep execution is future work.

The core-simulator engineering optimization stage, true streaming architecture
task, and Pre-Step7 model registry / instance model binding cleanup are complete.
V1 review repair has completed strict E2E validation and has been archived under
`docs/archive/v1_review_repair/`. The next core-simulator stage is Step7.

For large traces, use the opt-in streaming path:

```bash
PYTHONPATH=src python -m infertwin.cli.main sweep-streaming --config <config.yaml>
```

For traces without `instance_uuid`, only use normalization when you explicitly do
not want gateway routing simulation and want a single-instance baseline:

```bash
PYTHONPATH=src python -m infertwin.cli.main normalize-trace \
  --input data/raw/unrouted_trace.csv \
  --output data/processed/routed_single_instance_trace.csv \
  --instance-uuid single-instance
```

`normalize-trace` is an outer data-preparation capability. It is not gateway
routing simulation, and the core simulator still consumes routed traces with an
`instance_uuid` column.

For fixed-routed traces where instances need different fitted TTFT parameters,
enable the instance latency table in the streaming config:

```yaml
model_registry:
  profile_path: configs/models/registry.yaml

instance_latency:
  profile_path: configs/instances/local-fixed-route-latency-example.yaml
  require_all_trace_instances: true

latency_fallback:
  on_calibration_failure: use_model_default
```

Example:

```bash
PYTHONPATH=src python -m infertwin.cli.main sweep-streaming \
  --config configs/experiments/streaming_capacity_sweep_instance_latency.yaml
```

Current scope: this selects latency backend by `instance_uuid` and uses
model-bound runtime defaults for tokenizer selection, scheduler setup, block size
conversion, and model default cache metadata in `sweep-streaming`. It does not
yet provide dynamic per-500-request refit, DDR / remote KV-load latency
materialization, or gateway routing simulation.

`model_registry` is an index from model name to model profile, tokenizer/chat
profile, and default latency profile. `instance_latency` is the fixed-routed
instance binding table: `instance_uuid -> model/deployment/optional latency
profile`. If an instance has a dedicated `latency_profile`, that profile wins.
If it does not and `model_registry.profile_path` is configured, InferTwin uses
the model default TTFT profile. If neither is available, configured instance
tables fail fast instead of silently using unrelated parameters.

`latency_fallback` is only for future external calibration failures, such as an
AIConfigurator / MkSim calibration timeout or invalid calibration output. It
does not catch request build, tokenizer, trace schema, scheduler, cache, replay,
or ordinary fitted backend construction errors. Dynamic per-instance refit every
500 requests is not implemented yet; the current field is schema and policy
preparation.

For local throughput and memory observation:

```bash
.venv/bin/python scripts/benchmark_streaming_replay.py \
  --requests 10000 \
  --instances 4 \
  --prompt-words 256 \
  --reuse-period 64 \
  --capacities 128,512 \
  --output-dir reports/streaming_benchmark \
  --output-json reports/streaming_benchmark/benchmark.json
```

V1 core-simulator exit scope:

- Step7: single-instance pooling, where one instance can hit KV cache from
  DDR/CPU-side storage.
- Step8: KV load latency modeling for non-HBM hits.
- Step9: progressive chunk visibility, where generated full chunks can become
  cache-hit candidates before the whole prompt finishes. TTFT prefill time must
  be composed from uncached-token chunks instead of one whole-request formula.

V2-or-later core-simulator scope:

- complex Hybrid model cache semantics, including Qwen3.6 / DeepSeekV4-style
  cache groups and non-uniform block assumptions.
- gateway routing simulation.
- instance-side queueing policy simulation.
- multi-instance pooling / cross-instance KV hit.
- decode / TPOT modeling.
- broad engineering optimization after V1 exit.
- external AIConfigurator / MkSim production adapters.

Future outer capabilities:

- target-based hit floor solver / P90 target matching.
- dashboard / Web UI.
- strategy comparison reports.

New outer capabilities should be built only after V1 core simulator exit, so
reports and product surfaces consume stable replay semantics instead of forcing
core behavior changes from the outside.

## Document Index

Active docs:

```text
docs/global_memory.md
docs/code_development_requirements.md
docs/infertwin_product_design.md
docs/core_simulator_technical_plan.md
```

Notes / integration references:

```text
docs/notes/simulator_integration_guide.md
docs/notes/aiconfigurator_manual.md
docs/notes/markov_infer_sim_manual.md
docs/notes/internal_model_deployment_method.md
docs/notes/cached_tokens_calculation_logic.md
```

Archived stage docs:

```text
docs/archive/pre_step6_cleanup_plan.md
docs/archive/implementation_plan.md
docs/archive/future_simulation_extensions.md
docs/archive/development_status.md
docs/archive/engineering_optimization/
docs/archive/instance_latency_profiles/
docs/archive/pre_step7_model_registry/
docs/archive/true_streaming/
docs/archive/step4/
docs/archive/step5/
docs/archive/step6/
docs/archive/step7/
docs/archive/step8/
```

## Latency Strategy

Batch D defaults to a fitted TTFT function backend:

```text
FittedTTFTLatencyBackend / fitted_ttft
duration_ms = intercept_ms + ms_per_uncached_token * scheduled_prefill_tokens
```

AIConfigurator and MkSim are treated first as calibration sources for fitted profiles, and only later as optional high-precision replay backends.

For true streaming replay, `instance_latency.profile_path` can override this
global backend per fixed-routed instance. Missing trace instances fail fast when
an instance latency table is configured.

When `model_registry.profile_path` is configured, `InstanceLatencyBackendResolver`
resolves latency backend in this order:

1. Instance-specific `latency_profile`.
2. Model registry `default_latency` for the instance's `model_name`.
3. Legacy global `latency` backend only when no instance profile is configured.

The resolver exposes `latency_source_by_instance` in streaming sweep
`config_details` and `summary.md` so reports can explain whether an instance used
`instance_profile`, `model_default`, or the legacy global backend.

## Core Semantics (Frozen)

The following InferTwin semantics are stable. Future work must not silently change
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
- `cached_prefix_tokens`: replay-facing usage cached tokens after vLLM-like
  accounting at first-schedule-time lookup. Raw cache events may report resident
  block hits that do not count as usage cached tokens.
- `previous_chunk_tokens`: tokens already computed by earlier chunks of the same
  request.
- `computed_tokens_before = cached_prefix_tokens + previous_chunk_tokens`.
- `miss_tokens = prompt_tokens - cached_prefix_tokens` at cache lookup time.
  Miss tokens may be computed across one or more scheduler iterations.
- A zero-miss request has `miss_tokens = 0`, produces no `ScheduledSlice`, and
  must use a zero-miss fast-finish path in batch-aware replay. For ordinary
  positive-length prompts, vLLM-like accounting applies `prompt_tokens - 1` and
  full-block flooring, so raw full-block residency does not automatically imply
  `miss_tokens = 0`.
- Cache lookup happens when a request is first eligible to be considered by the
  scheduler, not when it arrives in the trace.
- Cache materialization happens only after a request's prefill finishes. Blocks
  materialized at an iteration finish are not visible within the same iteration.
  This is a conservative offline replay rule, not a physical vLLM block-manager
  timeline. Real vLLM / vLLM-Ascend deployments may expose full blocks
  progressively during prefill; InferTwin's `batch_aware_hbm_lru` mode does not.
  Step9 must add progressive chunk visibility as a new replay/cache mode instead
  of changing this mode's materialization semantics.
- `ttft_ms = finish_time_ms - arrival_time_ms`.
- Request-level TTFT is modeled as
  `queue_waiting_ms + uncached_prefill_compute_ms + kv_load_ms`. Current replay
  keeps `queue_waiting_ms = 0`; Step8 lets `KVLoadLatencyProfile` control
  `kv_load_ms` for DDR/CPU hits. The default `mode=zero` keeps legacy zero-load
  behavior, while `token_linear` and `byte_linear` can add fitted/static KV load
  latency.
- `scheduler_wait_ms = first_scheduled_time_ms - arrival_time_ms`. For a zero-miss
  request, `first_scheduled_time_ms` is the time when the replay first considers
  and fast-finishes the request.
- Current replay scope is fixed-routing, multi-instance isolated replay. Requests are
  grouped by `instance_uuid` from the trace, each instance replays independently,
  and InferTwin does not simulate routing decisions. Current core replay does not
  model cross-instance KV pooling, PD ratio search, decode TPOT, or gateway
  routing. MTP/EAGLE-style drop-block effects are represented only in
  cached-token accounting, not as real speculative decode execution.
