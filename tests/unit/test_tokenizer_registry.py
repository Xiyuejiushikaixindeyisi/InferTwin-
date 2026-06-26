from infertwin.request.parser import parse_request_params
from infertwin.request.tokenizer_registry import TokenizerRegistry


def test_tokenizer_registry_resolves_model_alias_and_encodes_prompt() -> None:
    registry = TokenizerRegistry.from_root("tokenizers", default_profile="glm-v5")
    request = parse_request_params(
        """
        {
          "model": "example-llm",
          "messages": [{"role": "user", "content": "hello infertwin"}],
          "tools": []
        }
        """
    )

    result = registry.encode(request)

    assert result.tokenizer_profile == "glm-v5"
    assert result.prompt_tokens > 0
    assert result.prompt_token_ids == registry.encode(request).prompt_token_ids


def test_simple_tokenizer_is_content_sensitive() -> None:
    registry = TokenizerRegistry.from_root("tokenizers", default_profile="glm-v5")
    first = parse_request_params(
        '{"model":"glm-v5","messages":[{"role":"user","content":"alpha beta"}],"tools":[]}'
    )
    second = parse_request_params(
        '{"model":"glm-v5","messages":[{"role":"user","content":"gamma beta"}],"tools":[]}'
    )

    assert registry.encode(first).prompt_token_ids != registry.encode(second).prompt_token_ids
