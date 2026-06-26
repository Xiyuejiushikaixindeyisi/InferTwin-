"""Resolve request model names to tokenizer profiles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelResolution:
    model: str
    tokenizer_profile: str


class ModelResolver:
    def __init__(self, aliases: dict[str, str], default_profile: str | None = None) -> None:
        self._aliases = dict(aliases)
        self._default_profile = default_profile

    def resolve(self, model: str) -> ModelResolution:
        if model in self._aliases:
            return ModelResolution(model=model, tokenizer_profile=self._aliases[model])
        if self._default_profile is not None:
            return ModelResolution(model=model, tokenizer_profile=self._default_profile)
        raise KeyError(f"Unknown model {model!r}; no tokenizer profile is configured")
