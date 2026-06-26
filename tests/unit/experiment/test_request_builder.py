from pathlib import Path

import pytest

from hitfloor.experiment.request_builder import build_requests_from_config


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
    request_params = (
        "{"
        '""model"":""glm-v5"",'
        '""messages"":[{""role"":""user"",""content"":""'
        f"{prompt}"
        '""}],'
        '""tools"":[]'
        "}"
    )
    return f'{request_id},{tenant_id},{instance_uuid},"{request_params}",2026-06-05 09:01:23'
