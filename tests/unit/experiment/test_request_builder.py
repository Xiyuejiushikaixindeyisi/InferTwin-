from pathlib import Path

import pytest

from infertwin.experiment.request_builder import (
    build_request_build_result_from_config,
    build_requests_from_config,
)


def test_build_requests_from_config_uses_trace_tokenizer_and_cache_settings(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row("00000000000000000000000000000001", "tenant-a", "instance-a", "hello"),
                _row("00000000000000000000000000000002", "tenant-b", "instance-b", "world"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    requests = build_requests_from_config(
        {
            "trace": {"path": str(trace_path)},
            "tokenizers": {
                "root": "tokenizers",
                "default_profile": "glm-v5",
                "cache_scope": "model_shared",
            },
            "cache": {"block_size_tokens": 4},
        }
    )

    assert [request.request_id for request in requests] == [
        "00000000000000000000000000000001",
        "00000000000000000000000000000002",
    ]
    assert {request.instance_uuid for request in requests} == {"instance-a", "instance-b"}
    assert {request.tokenizer_profile for request in requests} == {"glm-v5"}
    assert all(request.prompt_tokens > 0 for request in requests)
    assert all(request.prompt_blocks for request in requests)
    assert {request.requested_block_size for request in requests} == {4}
    assert {request.runtime_block_size for request in requests} == {4}
    assert {request.effective_block_size for request in requests} == {4}
    assert all(request.block_conversion_result is not None for request in requests)


def test_build_requests_from_config_uses_profile_aware_context(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    model_profile_path = tmp_path / "model.yaml"
    deployment_profile_path = tmp_path / "deployment.yaml"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row(
                    "00000000000000000000000000000001",
                    "tenant-a",
                    "instance-a",
                    "alpha beta gamma delta epsilon zeta eta theta",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_model_profile(model_profile_path)
    _write_deployment_profile(deployment_profile_path)

    requests = build_requests_from_config(
        {
            "run": {
                "trace_path": str(trace_path),
                "output_dir": str(tmp_path / "reports"),
                "mode": "simulate",
                "model_name": "glm-v5.1",
                "requested_block_size": 4,
                "model_profile": str(model_profile_path),
                "deployment_profile": str(deployment_profile_path),
            },
            "tokenizers": {
                "root": "tokenizers",
                "cache_scope": "tenant_isolated",
            },
            "cache": {"block_size_tokens": 2},
        }
    )

    request = requests[0]
    assert request.model == "glm-v5"
    assert request.tokenizer_profile == "glm-v5"
    assert request.requested_block_size == 4
    assert request.runtime_block_size == 8
    assert request.effective_block_size == 16
    assert request.block_conversion_result is not None
    assert request.block_conversion_result.requested_block_size == 4
    assert request.block_conversion_result.runtime_block_size == 8
    assert request.block_conversion_result.effective_block_size == 16
    assert request.block_conversion_result.max_cache_hit_length == max(request.prompt_tokens - 1, 0)
    assert all(block.token_count <= 16 for block in request.prompt_blocks)


def test_request_build_rejects_prompt_over_configured_tokenizer_limit(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    tokenizer_root = tmp_path / "tokenizers"
    _write_simple_tokenizer_profile(tokenizer_root, profile="simple-model")
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row_with_model(
                    "00000000000000000000000000000001",
                    "tenant-a",
                    "instance-a",
                    "simple-model",
                    "short",
                ),
                _row_with_model(
                    "00000000000000000000000000000002",
                    "tenant-a",
                    "instance-a",
                    "simple-model",
                    "too many prompt tokens",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_request_build_result_from_config(
        {
            "trace": {"path": str(trace_path)},
            "tokenizers": {
                "root": str(tokenizer_root),
                "default_profile": "simple-model",
                "max_prompt_tokens": 3,
            },
            "cache": {"block_size_tokens": 4},
        }
    )

    assert [request.request_id for request in result.requests] == [
        "00000000000000000000000000000001"
    ]
    assert result.accepted_count == 1
    assert result.rejected_count == 1
    rejected = result.rejected_records[0]
    assert rejected.request_id == "00000000000000000000000000000002"
    assert rejected.reason == "prompt_too_long"
    assert rejected.prompt_tokens is not None
    assert rejected.prompt_tokens > 3
    assert rejected.max_prompt_tokens == 3
    assert rejected.tokenizer_profile == "simple-model"


def test_profile_aware_request_build_uses_profile_max_model_len_as_limit(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.csv"
    tokenizer_root = tmp_path / "tokenizers"
    model_profile_path = tmp_path / "model.yaml"
    deployment_profile_path = tmp_path / "deployment.yaml"
    _write_simple_tokenizer_profile(tokenizer_root, profile="glm-v5")
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row(
                    "00000000000000000000000000000001",
                    "tenant-a",
                    "instance-a",
                    "alpha beta gamma delta",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_model_profile(model_profile_path, max_model_len=3)
    _write_deployment_profile(deployment_profile_path)

    result = build_request_build_result_from_config(
        {
            "run": {
                "trace_path": str(trace_path),
                "output_dir": str(tmp_path / "reports"),
                "mode": "simulate",
                "model_name": "glm-v5.1",
                "requested_block_size": 4,
                "model_profile": str(model_profile_path),
                "deployment_profile": str(deployment_profile_path),
            },
            "tokenizers": {
                "root": str(tokenizer_root),
            },
            "cache": {"block_size_tokens": 4},
        }
    )

    assert result.requests == ()
    assert result.rejected_count == 1
    assert result.rejected_records[0].max_prompt_tokens == 3


def test_profile_aware_request_build_rejects_request_model_mismatch(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    model_profile_path = tmp_path / "model.yaml"
    deployment_profile_path = tmp_path / "deployment.yaml"
    trace_path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                _row_with_model(
                    "00000000000000000000000000000001",
                    "tenant-a",
                    "instance-a",
                    "qwen",
                    "hello",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_model_profile(model_profile_path)
    _write_deployment_profile(deployment_profile_path)

    with pytest.raises(ValueError, match="REQUEST_MODEL_MISMATCH"):
        build_requests_from_config(
            {
                "run": {
                    "trace_path": str(trace_path),
                    "output_dir": str(tmp_path / "reports"),
                    "mode": "simulate",
                    "model_name": "glm-v5.1",
                    "requested_block_size": 4,
                    "model_profile": str(model_profile_path),
                    "deployment_profile": str(deployment_profile_path),
                },
                "tokenizers": {
                    "root": "tokenizers",
                    "default_profile": "glm-v5",
                },
                "cache": {"block_size_tokens": 4},
            }
        )


def test_build_requests_from_config_rejects_invalid_cache_section() -> None:
    with pytest.raises(ValueError, match="cache config must be a mapping"):
        build_requests_from_config(
            {
                "trace": {"path": "data/samples/sample_trace.csv"},
                "tokenizers": {"root": "tokenizers", "default_profile": "glm-v5"},
                "cache": "bad",
            }
        )


def _row(request_id: str, tenant_id: str, instance_uuid: str, prompt: str) -> str:
    return _row_with_model(request_id, tenant_id, instance_uuid, "glm-v5", prompt)


def _row_with_model(
    request_id: str,
    tenant_id: str,
    instance_uuid: str,
    model: str,
    prompt: str,
) -> str:
    request_params = (
        "{"
        f'""model"":""{model}"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},{tenant_id},{instance_uuid},"{request_params}",2026-06-05 09:01:23'


def _write_model_profile_with_limit(path: Path, *, max_model_len: int | None) -> None:
    max_model_len_yaml = f"  max_model_len: {max_model_len}\n" if max_model_len else ""
    path.write_text(
        f"""
model:
  name: glm-v5.1
  aliases:
    - glm-v5
  tokenizer_profile: glm-v5
{max_model_len_yaml.rstrip()}
  cache_family: full_attention
""",
        encoding="utf-8",
    )


def _write_model_profile(path: Path, max_model_len: int | None = None) -> None:
    _write_model_profile_with_limit(path, max_model_len=max_model_len)


def _write_deployment_profile(path: Path) -> None:
    path.write_text(
        """
deployment:
  name: glm-v5.1-vllm-ascend-prefill
  engine: vllm-ascend
  scheduler:
    max_num_seqs: 32
    max_num_batched_tokens: 8192
    enable_chunked_prefill: true
  parallel:
    prefill_context_parallel_size: 2
    decode_context_parallel_size: 1
  speculative:
    enabled: false
  cache_features:
    runtime_block_size: 8
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
