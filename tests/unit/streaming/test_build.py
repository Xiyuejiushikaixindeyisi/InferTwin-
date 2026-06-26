import csv
from pathlib import Path

import pytest

from infertwin.streaming.build import StreamingRequestShardBuilder, UnsortedTraceError
from infertwin.streaming.request_codec import decode_simulation_request_line


def test_streaming_request_shard_builder_writes_per_instance_shards(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    _write_trace(
        trace_path,
        [
            _row(
                "00000000000000000000000000000001",
                "tenant-a",
                "instance-a",
                "alpha",
                "2026-06-05 09:01:23",
            ),
            _row(
                "00000000000000000000000000000002",
                "tenant-a",
                "instance-b",
                "beta",
                "2026-06-05 09:01:24",
            ),
            _row(
                "00000000000000000000000000000003",
                "tenant-b",
                "instance-a",
                "gamma",
                "2026-06-05 09:01:25",
            ),
        ],
    )

    result = StreamingRequestShardBuilder(
        {
            "trace": {"path": str(trace_path)},
            "tokenizers": {
                "root": "tokenizers",
                "default_profile": "glm-v5",
                "cache_scope": "tenant_isolated",
            },
            "cache": {"block_size_tokens": 4},
        },
        shard_root=tmp_path / "shards",
        rejected_path=tmp_path / "rejected_requests.csv",
    ).build()

    assert result.rejected_path is None
    assert result.manifest.accepted_count == 3
    assert result.manifest.rejected_count == 0
    assert [shard.instance_uuid for shard in result.manifest.shards] == [
        "instance-a",
        "instance-b",
    ]
    assert [shard.request_count for shard in result.manifest.shards] == [2, 1]

    requests_by_instance = {
        shard.instance_uuid: _read_shard_request_ids(shard.path) for shard in result.manifest.shards
    }
    assert requests_by_instance == {
        "instance-a": [
            "00000000000000000000000000000001",
            "00000000000000000000000000000003",
        ],
        "instance-b": ["00000000000000000000000000000002"],
    }


def test_streaming_request_shard_builder_writes_prompt_too_long_rejections(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.csv"
    tokenizer_root = tmp_path / "tokenizers"
    rejected_path = tmp_path / "rejected_requests.csv"
    _write_simple_tokenizer_profile(tokenizer_root, profile="simple-model")
    _write_trace(
        trace_path,
        [
            _row_with_model(
                "00000000000000000000000000000001",
                "tenant-a",
                "instance-a",
                "simple-model",
                "short",
                "2026-06-05 09:01:23",
            ),
            _row_with_model(
                "00000000000000000000000000000002",
                "tenant-a",
                "instance-a",
                "simple-model",
                "too many prompt tokens",
                "2026-06-05 09:01:24",
            ),
        ],
    )

    result = StreamingRequestShardBuilder(
        {
            "trace": {"path": str(trace_path)},
            "tokenizers": {
                "root": str(tokenizer_root),
                "default_profile": "simple-model",
                "max_prompt_tokens": 3,
            },
            "cache": {"block_size_tokens": 4},
        },
        shard_root=tmp_path / "shards",
        rejected_path=rejected_path,
    ).build()

    assert result.rejected_path == rejected_path
    assert result.manifest.accepted_count == 1
    assert result.manifest.rejected_count == 1
    assert len(result.manifest.shards) == 1
    assert _read_shard_request_ids(result.manifest.shards[0].path) == [
        "00000000000000000000000000000001"
    ]

    with rejected_path.open("r", encoding="utf-8", newline="") as file:
        rejected_rows = list(csv.DictReader(file))
    assert len(rejected_rows) == 1
    assert rejected_rows[0]["request_id"] == "00000000000000000000000000000002"
    assert rejected_rows[0]["reason"] == "prompt_too_long"
    assert rejected_rows[0]["max_prompt_tokens"] == "3"
    assert rejected_rows[0]["tokenizer_profile"] == "simple-model"


def test_streaming_request_shard_builder_fails_on_unsorted_trace(tmp_path: Path) -> None:
    trace_path = tmp_path / "trace.csv"
    _write_trace(
        trace_path,
        [
            _row(
                "00000000000000000000000000000002",
                "tenant-a",
                "instance-a",
                "later",
                "2026-06-05 09:01:24",
            ),
            _row(
                "00000000000000000000000000000001",
                "tenant-a",
                "instance-a",
                "earlier",
                "2026-06-05 09:01:23",
            ),
        ],
    )

    with pytest.raises(UnsortedTraceError, match="line_number=3"):
        StreamingRequestShardBuilder(
            {
                "trace": {"path": str(trace_path)},
                "tokenizers": {
                    "root": "tokenizers",
                    "default_profile": "glm-v5",
                },
                "cache": {"block_size_tokens": 4},
            },
            shard_root=tmp_path / "shards",
        ).build()


def test_streaming_request_shard_builder_can_disable_sorted_guard(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.csv"
    _write_trace(
        trace_path,
        [
            _row(
                "00000000000000000000000000000002",
                "tenant-a",
                "instance-a",
                "later",
                "2026-06-05 09:01:24",
            ),
            _row(
                "00000000000000000000000000000001",
                "tenant-a",
                "instance-a",
                "earlier",
                "2026-06-05 09:01:23",
            ),
        ],
    )

    result = StreamingRequestShardBuilder(
        {
            "trace": {"path": str(trace_path)},
            "tokenizers": {
                "root": "tokenizers",
                "default_profile": "glm-v5",
            },
            "cache": {"block_size_tokens": 4},
        },
        shard_root=tmp_path / "shards",
        require_sorted_trace=False,
    ).build()

    assert result.manifest.require_sorted_trace is False
    assert result.manifest.accepted_count == 2


def _read_shard_request_ids(path: Path) -> list[str]:
    return [
        decode_simulation_request_line(line).request_id
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _write_trace(path: Path, rows: list[str]) -> None:
    path.write_text(
        "\n".join(
            [
                "request_id,tenant_id,instance_uuid,request_params,service_start_time",
                *rows,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _row(
    request_id: str,
    tenant_id: str,
    instance_uuid: str,
    prompt: str,
    timestamp: str,
) -> str:
    return _row_with_model(request_id, tenant_id, instance_uuid, "glm-v5", prompt, timestamp)


def _row_with_model(
    request_id: str,
    tenant_id: str,
    instance_uuid: str,
    model: str,
    prompt: str,
    timestamp: str,
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
    return f'{request_id},{tenant_id},{instance_uuid},"{request_params}",{timestamp}'


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
