from pathlib import Path

from hitfloor.trace.reader import read_trace_csv


def test_read_sample_trace() -> None:
    trace_path = Path("data/samples/sample_trace.csv")

    records = list(read_trace_csv(trace_path))

    assert len(records) == 1
    assert records[0].request_id == "00000000000000000000000000000001"
