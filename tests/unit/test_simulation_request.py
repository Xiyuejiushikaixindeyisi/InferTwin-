from pathlib import Path

from hitfloor.instance.request import build_simulation_requests
from hitfloor.request.tokenizer_registry import TokenizerRegistry
from hitfloor.trace.reader import read_trace_csv
from hitfloor.trace.schema import TraceRecord


def test_build_simulation_requests_from_sample_trace() -> None:
    records = list(read_trace_csv(Path("data/samples/sample_trace.csv")))
    registry = TokenizerRegistry.from_root("tokenizers", default_profile="glm-v5")

    requests = build_simulation_requests(
        records,
        tokenizer_registry=registry,
        block_size_tokens=4,
    )

    assert len(requests) == 1
    assert requests[0].instance_uuid == "instance-a"
    assert requests[0].tokenizer_profile == "glm-v5"
    assert requests[0].prompt_tokens > 0
    assert requests[0].prompt_blocks


def test_build_simulation_requests_has_explicit_same_timestamp_tie_break() -> None:
    registry = TokenizerRegistry.from_root("tokenizers", default_profile="glm-v5")
    timestamp = next(iter(read_trace_csv(Path("data/samples/sample_trace.csv")))).service_start_time
    records = [
        TraceRecord(
            request_id="request-c",
            tenant_id="tenant-a",
            instance_uuid="instance-b",
            request_params=_request_params("gamma"),
            service_start_time=timestamp,
        ),
        TraceRecord(
            request_id="request-b",
            tenant_id="tenant-a",
            instance_uuid="instance-a",
            request_params=_request_params("beta"),
            service_start_time=timestamp,
        ),
        TraceRecord(
            request_id="request-a",
            tenant_id="tenant-a",
            instance_uuid="instance-a",
            request_params=_request_params("alpha"),
            service_start_time=timestamp,
        ),
    ]

    requests = build_simulation_requests(
        records,
        tokenizer_registry=registry,
        block_size_tokens=4,
    )

    assert [request.request_id for request in requests] == [
        "request-a",
        "request-b",
        "request-c",
    ]


def _request_params(content: str) -> str:
    return f'{{"model":"glm-v5","messages":[{{"role":"user","content":"{content}"}}],"tools":[]}}'
