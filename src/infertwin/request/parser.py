"""OpenAI-style request parser."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ParsedRequest:
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    raw: dict[str, Any]


def parse_request_params(raw_json: str) -> ParsedRequest:
    payload = json.loads(raw_json)
    if not isinstance(payload, dict):
        raise ValueError("request_params must decode to a JSON object")

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("request_params.model must be a non-empty string")

    messages = payload.get("messages")
    tools = payload.get("tools", [])
    if not isinstance(messages, list):
        raise ValueError("request_params.messages must be a list")
    for index, message in enumerate(messages):
        _validate_message(message, index)

    if not isinstance(tools, list):
        raise ValueError("request_params.tools must be a list")
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping):
            raise ValueError(f"request_params.tools[{index}] must be an object")

    return ParsedRequest(
        model=model,
        messages=messages,
        tools=tools,
        raw=payload,
    )


def _validate_message(message: object, index: int) -> None:
    if not isinstance(message, Mapping):
        raise ValueError(f"request_params.messages[{index}] must be an object")

    role = message.get("role")
    if not isinstance(role, str) or not role:
        raise ValueError(f"request_params.messages[{index}].role must be a non-empty string")

    if "content" not in message:
        raise ValueError(f"request_params.messages[{index}].content is required")
