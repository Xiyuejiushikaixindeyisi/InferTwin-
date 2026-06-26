import pytest

from hitfloor.request.parser import parse_request_params


def test_parse_request_params_accepts_documented_schema() -> None:
    parsed = parse_request_params(
        """
        {
          "model": "glm-v5",
          "messages": [{"role": "user", "content": "hello"}],
          "tools": [{"type": "function", "function": {"name": "lookup"}}]
        }
        """
    )

    assert parsed.model == "glm-v5"
    assert parsed.messages[0]["role"] == "user"
    assert parsed.tools[0]["type"] == "function"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("[]", "must decode to a JSON object"),
        ('{"messages":[{"role":"user","content":"hello"}],"tools":[]}', "model"),
        ('{"model":"","messages":[{"role":"user","content":"hello"}],"tools":[]}', "model"),
        ('{"model":"glm-v5","messages":"bad","tools":[]}', "messages must be a list"),
        ('{"model":"glm-v5","messages":["bad"],"tools":[]}', r"messages\[0\]"),
        ('{"model":"glm-v5","messages":[{"content":"hello"}],"tools":[]}', "role"),
        ('{"model":"glm-v5","messages":[{"role":"user"}],"tools":[]}', "content"),
        ('{"model":"glm-v5","messages":[{"role":"user","content":"hello"}],"tools":{}}', "tools"),
        (
            '{"model":"glm-v5","messages":[{"role":"user","content":"hello"}],"tools":["bad"]}',
            r"tools\[0\]",
        ),
    ],
)
def test_parse_request_params_rejects_unsupported_schema(
    payload: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_request_params(payload)
