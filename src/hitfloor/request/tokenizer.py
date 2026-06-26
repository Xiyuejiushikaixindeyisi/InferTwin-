"""Tokenizer boundary.

Production implementations should wrap the exact tokenizer and chat template used by
the serving system. The simple tokenizer exists only for smoke tests and examples.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from hitfloor.request.parser import ParsedRequest


class RequestTokenizer(Protocol):
    def encode_prompt(self, request: ParsedRequest) -> list[int]:
        """Return prompt token ids after applying the chat template."""


class SimpleWhitespaceTokenizer:
    def encode_prompt(self, request: ParsedRequest) -> list[int]:
        text_parts: list[str] = []
        for message in request.messages:
            content = message.get("content", "")
            if isinstance(content, str):
                text_parts.append(content)
        text = " ".join(text_parts)
        return self.encode_text(text)

    def encode_text(self, text: str) -> list[int]:
        token_ids: list[int] = []
        for token in text.split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            token_ids.append(int.from_bytes(digest[:8], "big"))
        return token_ids
