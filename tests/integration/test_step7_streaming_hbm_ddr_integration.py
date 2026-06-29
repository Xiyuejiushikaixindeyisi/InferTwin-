import csv
import json
from pathlib import Path

import yaml

from infertwin.cache.events import CACHE_TIER_DDR, LOOKUP_HIT, STORE
from infertwin.streaming.cache_factory import CACHE_MODE_HBM_DDR_LRU
from infertwin.streaming.sweep import STREAMING_CAPACITY_SWEEP_MODE, StreamingCapacitySweepRunner


def test_streaming_hbm_ddr_mode_reports_ddr_hits_and_keeps_instances_isolated(
    tmp_path: Path,
) -> None:
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="runtime-tokenizer")
    trace_path = _write_trace(
        tmp_path,
        rows=[
            ("req-a1", "instance-a", "one two three four five six seven eight", 23),
            ("req-b1", "instance-b", "one two three four five six seven eight", 24),
            ("req-a2", "instance-a", "one two three four five six seven eight", 25),
        ],
    )
    instance_profile_path = _write_instance_profile(tmp_path)
    registry_path = _write_pooling_model_registry(tmp_path)
    output_dir = tmp_path / "reports"
    config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=instance_profile_path,
        registry_path=registry_path,
        output_dir=output_dir,
        capacities=[1],
        cache_events=True,
        cache_event_capacities=[1],
    )

    result = StreamingCapacitySweepRunner(config).run()

    rows = {(row.scope, row.instance_uuid): row for row in result.rows}
    trace_row = rows[("trace", "")]
    instance_a = rows[("instance", "instance-a")]
    instance_b = rows[("instance", "instance-b")]
    assert result.config_details["streaming_cache_mode"] == CACHE_MODE_HBM_DDR_LRU
    assert trace_row.hbm_capacity_blocks == 1
    assert trace_row.ddr_hit_tokens > 0
    assert trace_row.total_hit_tokens == trace_row.hbm_hit_tokens + trace_row.ddr_hit_tokens
    assert instance_a.ddr_hit_tokens > 0
    assert instance_b.ddr_hit_tokens == 0
    assert result.config_details["model_default_cache_by_instance"]["instance-a"][
        "ddr_capacity_blocks"
    ] == 32
    assert result.config_details["model_default_cache_by_instance"]["instance-a"][
        "pooling_enabled"
    ] is True

    event_path = output_dir / "capacity_1" / "cache_events.csv"
    event_rows = list(csv.DictReader(event_path.open(encoding="utf-8")))
    ddr_rows = [row for row in event_rows if row["cache_tier"] == CACHE_TIER_DDR]
    assert ddr_rows
    assert any(row["event_type"] == STORE for row in ddr_rows)
    assert any(
        row["event_type"] == LOOKUP_HIT and row["instance_uuid"] == "instance-a"
        for row in ddr_rows
    )
    assert not any(
        row["event_type"] == LOOKUP_HIT and row["instance_uuid"] == "instance-b"
        for row in ddr_rows
    )


def test_streaming_hbm_ddr_mode_fails_without_model_runtime_defaults(tmp_path: Path) -> None:
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="runtime-tokenizer")
    trace_path = _write_trace(
        tmp_path,
        rows=[("req-a1", "instance-a", "one two three four", 23)],
    )
    config = _streaming_config(
        trace_path=trace_path,
        tokenizer_root=tokenizer_root,
        instance_profile_path=None,
        registry_path=None,
        output_dir=tmp_path / "reports",
        capacities=[1],
        cache_events=False,
        cache_event_capacities=[],
    )

    try:
        StreamingCapacitySweepRunner(config).run()
    except ValueError as exc:
        assert "requires model registry and instance runtime defaults" in str(exc)
    else:
        raise AssertionError("expected DDR streaming mode to require model runtime defaults")


def _streaming_config(
    *,
    trace_path: Path,
    tokenizer_root: Path,
    instance_profile_path: Path | None,
    registry_path: Path | None,
    output_dir: Path,
    capacities: list[int],
    cache_events: bool,
    cache_event_capacities: list[int],
) -> dict[str, object]:
    config: dict[str, object] = {
        "simulation": {"mode": STREAMING_CAPACITY_SWEEP_MODE},
        "trace": {"path": str(trace_path)},
        "tokenizers": {
            "root": str(tokenizer_root),
            "default_profile": "runtime-tokenizer",
            "cache_scope": "tenant_isolated",
        },
        "cache": {
            "mode": CACHE_MODE_HBM_DDR_LRU,
            "eviction_policy": "lru",
        },
        "sweep": {
            "hbm_capacity_blocks": capacities,
            "parallel_instances": False,
        },
        "scheduler": {
            "policy": "fcfs",
            "max_num_batched_tokens": 8192,
            "max_num_seqs": 32,
            "enable_chunked_prefill": True,
        },
        "latency": {
            "backend": "fitted_ttft",
            "model_name": "global",
            "hardware_name": "global",
            "fitted_ttft": {
                "profile": "global-ttft",
                "function": "token_linear_v1",
                "intercept_ms": 0.0,
                "ms_per_uncached_token": 1.0,
                "calibrated_from": "global",
            },
        },
        "streaming": {
            "shard_root": str(output_dir / "streaming_shards"),
            "rejected_path": str(output_dir / "rejected_requests.csv"),
            "require_sorted_trace": True,
        },
        "output": {
            "directory": str(output_dir),
            "cache_events": cache_events,
            "cache_event_capacities": cache_event_capacities,
        },
    }
    if registry_path is not None:
        config["model_registry"] = {"profile_path": str(registry_path)}
    if instance_profile_path is not None:
        config["instance_runtime"] = {"profile_path": str(instance_profile_path)}
        config["instance_latency"] = {
            "profile_path": str(instance_profile_path),
            "require_all_trace_instances": True,
        }
    return config


def _write_trace(
    tmp_path: Path,
    *,
    rows: list[tuple[str, str, str, int]],
) -> Path:
    path = tmp_path / "trace.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "request_id",
                "tenant_id",
                "instance_uuid",
                "request_params",
                "service_start_time",
            ),
        )
        writer.writeheader()
        for request_id, instance_uuid, prompt, second in rows:
            writer.writerow(
                {
                    "request_id": request_id,
                    "tenant_id": "tenant-a",
                    "instance_uuid": instance_uuid,
                    "request_params": json.dumps(
                        {
                            "model": "model-a",
                            "messages": [{"role": "user", "content": prompt}],
                            "tools": [],
                        }
                    ),
                    "service_start_time": f"2026-06-05 09:01:{second}",
                }
            )
    return path


def _write_instance_profile(tmp_path: Path) -> Path:
    path = tmp_path / "instances.yaml"
    path.write_text(
        """
instances:
  name: step7-streaming
  items:
    instance-a:
      model_name: model-a
      deployment: model-a-deployment
    instance-b:
      model_name: model-a
      deployment: model-a-deployment
""",
        encoding="utf-8",
    )
    return path


def _write_pooling_model_registry(tmp_path: Path) -> Path:
    _write_model_profile(tmp_path / "model-a.yaml")
    _write_deployment_profile(tmp_path / "model-a-deployment.yaml")
    path = tmp_path / "registry.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "models": {
                    "model-a": {
                        "model_profile_path": "model-a.yaml",
                        "deployment_profile_path": "model-a-deployment.yaml",
                        "tokenizer_profile": "runtime-tokenizer",
                        "default_cache": {
                            "hbm_capacity_blocks": 99,
                            "ddr_capacity_blocks": 32,
                            "block_size_tokens": 2,
                            "eviction_policy": "lru",
                            "pooling": {
                                "enabled": True,
                                "single_instance": True,
                                "multi_instance": False,
                                "ddr_enabled": True,
                                "remote_enabled": False,
                                "ssd_enabled": False,
                            },
                        },
                        "default_latency": {
                            "backend": "fitted_ttft",
                            "model_name": "model-a",
                            "hardware_name": "runtime-hardware",
                            "fitted_ttft": {
                                "profile": "model-a-default-ttft",
                                "function": "token_linear_v1",
                                "intercept_ms": 0.0,
                                "ms_per_uncached_token": 1.0,
                                "calibrated_from": "model-default",
                            },
                            "kv_load": {
                                "ddr_ms_per_cached_token": 0.0,
                                "remote_ms_per_cached_token": 0.0,
                            },
                        },
                    }
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _write_model_profile(path: Path) -> None:
    path.write_text(
        """
model:
  name: model-a
  tokenizer_profile: runtime-tokenizer
  cache_family: full_attention
""",
        encoding="utf-8",
    )


def _write_deployment_profile(path: Path) -> None:
    path.write_text(
        """
deployment:
  name: model-a-deployment
  engine: vllm-ascend
  scheduler:
    max_num_seqs: 32
    max_num_batched_tokens: 8192
    enable_chunked_prefill: true
  cache_features:
    prefix_caching: true
    pooling: true
    multi_tier_cache: true
    runtime_block_size: 2
""",
        encoding="utf-8",
    )


def _write_simple_tokenizer_profile(root: Path, *, profile: str) -> None:
    profile_dir = root / profile
    profile_dir.mkdir(parents=True)
    (profile_dir / "manifest.yaml").write_text(
        f"""
tokenizer:
  profile: {profile}
  type: simple
  include_tools: true
  model_aliases:
    - {profile}
""",
        encoding="utf-8",
    )
