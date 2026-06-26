"""Chat template rendering helpers."""

from __future__ import annotations

import json
from pathlib import Path

from hitfloor.request.parser import ParsedRequest


def render_simple_chat_template(
    request: ParsedRequest,
    include_tools: bool = True,
    template_path: str | Path | None = None,
) -> str:
    """Render a deterministic text prompt for simple-tokenizer tests and smoke runs."""

    if template_path is not None and Path(template_path).is_file():
        template = Path(template_path).read_text(encoding="utf-8")
        return _render_jinja_template(template, request, include_tools)

    parts: list[str] = []
    for message in request.messages:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, sort_keys=True)
        parts.append(f"<|{role}|>\n{content}")

    if include_tools and request.tools:
        tools_json = json.dumps(request.tools, ensure_ascii=False, sort_keys=True)
        parts.append(f"<|tools|>\n{tools_json}")

    parts.append("<|assistant|>")
    return "\n".join(parts)


def _render_basic_placeholders(
    template: str,
    request: ParsedRequest,
    include_tools: bool,
) -> str:
    messages_json = json.dumps(request.messages, ensure_ascii=False, sort_keys=True)
    tools_json = json.dumps(
        request.tools if include_tools else [], ensure_ascii=False, sort_keys=True
    )
    return (
        template.replace("{{ messages_json }}", messages_json)
        .replace("{{ tools_json }}", tools_json)
        .replace("{{ model }}", request.model)
    )


def _render_jinja_template(
    template: str,
    request: ParsedRequest,
    include_tools: bool,
) -> str:
    try:
        from jinja2 import Environment
    except ImportError:
        return _render_basic_placeholders(template, request, include_tools)

    environment = Environment(autoescape=False)
    compiled = environment.from_string(template)
    return compiled.render(
        messages=request.messages,
        tools=request.tools if include_tools else [],
        add_generation_prompt=True,
    )


def file_sha256(path: str | Path | None) -> str:
    if path is None:
        return ""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    import hashlib

    digest = hashlib.sha256()
    digest.update(file_path.read_bytes())
    return digest.hexdigest()
