"""Tokenizer registry and profile-based prompt encoding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infertwin.request.chat_template import file_sha256, render_simple_chat_template
from infertwin.request.model_resolver import ModelResolver
from infertwin.request.parser import ParsedRequest
from infertwin.request.tokenizer import SimpleWhitespaceTokenizer


@dataclass(frozen=True, slots=True)
class TokenizerProfile:
    profile: str
    tokenizer_type: str
    root: Path
    tokenizer_path: Path
    chat_template_path: Path | None
    include_tools: bool
    model_aliases: tuple[str, ...]
    kv_bytes_per_token: int | None = None


@dataclass(frozen=True, slots=True)
class TokenizationResult:
    model: str
    tokenizer_profile: str
    prompt_token_ids: list[int]
    prompt_tokens: int
    chat_template_hash: str
    tokenizer_config_hash: str
    kv_bytes_per_token: int | None


class PromptTooLongError(ValueError):
    """Raised after tokenization when a prompt exceeds the configured limit."""

    def __init__(
        self,
        *,
        model: str,
        tokenizer_profile: str,
        prompt_tokens: int,
        max_prompt_tokens: int,
    ) -> None:
        super().__init__(
            "prompt token length exceeds tokenizer limit: "
            f"model={model}, tokenizer_profile={tokenizer_profile}, "
            f"prompt_tokens={prompt_tokens}, max_prompt_tokens={max_prompt_tokens}"
        )
        self.model = model
        self.tokenizer_profile = tokenizer_profile
        self.prompt_tokens = prompt_tokens
        self.max_prompt_tokens = max_prompt_tokens


class TokenizerRegistry:
    def __init__(
        self,
        profiles: dict[str, TokenizerProfile],
        default_profile: str | None = None,
    ) -> None:
        self._profiles = profiles
        aliases: dict[str, str] = {}
        for profile in profiles.values():
            aliases[profile.profile] = profile.profile
            for alias in profile.model_aliases:
                aliases[alias] = profile.profile
        self._resolver = ModelResolver(aliases, default_profile=default_profile)
        self._tokenizers: dict[str, Any] = {}

    @classmethod
    def from_root(
        cls,
        root: str | Path,
        default_profile: str | None = None,
    ) -> "TokenizerRegistry":
        root_path = Path(root)
        profiles: dict[str, TokenizerProfile] = {}
        for manifest_path in sorted(root_path.glob("*/manifest.yaml")):
            profile = _load_profile(manifest_path)
            profiles[profile.profile] = profile

        if not profiles:
            raise ValueError(f"No tokenizer manifests found under {root_path}")
        return cls(profiles=profiles, default_profile=default_profile)

    def resolve_profile(self, model: str) -> TokenizerProfile:
        resolution = self._resolver.resolve(model)
        return self._profiles[resolution.tokenizer_profile]

    def encode(
        self,
        request: ParsedRequest,
        *,
        max_prompt_tokens: int | None = None,
    ) -> TokenizationResult:
        profile = self.resolve_profile(request.model)
        token_ids = self._encode_with_profile(profile, request)
        prompt_tokens = len(token_ids)
        if max_prompt_tokens is not None and prompt_tokens > max_prompt_tokens:
            raise PromptTooLongError(
                model=request.model,
                tokenizer_profile=profile.profile,
                prompt_tokens=prompt_tokens,
                max_prompt_tokens=max_prompt_tokens,
            )
        tokenizer_config_path = profile.tokenizer_path / "tokenizer_config.json"
        return TokenizationResult(
            model=request.model,
            tokenizer_profile=profile.profile,
            prompt_token_ids=token_ids,
            prompt_tokens=prompt_tokens,
            chat_template_hash=file_sha256(profile.chat_template_path),
            tokenizer_config_hash=file_sha256(tokenizer_config_path),
            kv_bytes_per_token=profile.kv_bytes_per_token,
        )

    def _encode_with_profile(
        self,
        profile: TokenizerProfile,
        request: ParsedRequest,
    ) -> list[int]:
        if profile.tokenizer_type == "simple":
            rendered = render_simple_chat_template(
                request,
                include_tools=profile.include_tools,
                template_path=profile.chat_template_path,
            )
            return SimpleWhitespaceTokenizer().encode_text(rendered)

        if profile.tokenizer_type == "huggingface":
            tokenizer = self._tokenizers.get(profile.profile)
            if tokenizer is None:
                tokenizer = _load_hf_tokenizer(profile.tokenizer_path)
                self._tokenizers[profile.profile] = tokenizer
            return _encode_hf(tokenizer, request, profile)

        raise ValueError(
            f"Unsupported tokenizer type {profile.tokenizer_type!r} for profile {profile.profile!r}"
        )


def _load_profile(manifest_path: Path) -> TokenizerProfile:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    tokenizer = data.get("tokenizer", {})
    if not isinstance(tokenizer, dict):
        raise ValueError(f"{manifest_path}: tokenizer must be a mapping")

    profile_name = str(tokenizer.get("profile") or manifest_path.parent.name)
    tokenizer_type = str(tokenizer.get("type", "simple"))
    tokenizer_path = manifest_path.parent / str(tokenizer.get("tokenizer_path", "."))
    chat_template = tokenizer.get("chat_template")
    chat_template_path = manifest_path.parent / str(chat_template) if chat_template else None
    aliases = tokenizer.get("model_aliases", [profile_name])
    if not isinstance(aliases, list):
        raise ValueError(f"{manifest_path}: tokenizer.model_aliases must be a list")

    kv_meta = _load_kv_meta(tokenizer_path)
    return TokenizerProfile(
        profile=profile_name,
        tokenizer_type=tokenizer_type,
        root=manifest_path.parent,
        tokenizer_path=tokenizer_path,
        chat_template_path=chat_template_path,
        include_tools=bool(tokenizer.get("include_tools", True)),
        model_aliases=tuple(str(alias) for alias in aliases),
        kv_bytes_per_token=kv_meta.get("kv_bytes_per_token") if kv_meta else None,
    )


def _load_kv_meta(tokenizer_path: Path) -> dict[str, Any]:
    kv_meta_path = tokenizer_path / "kv_meta.json"
    if not kv_meta_path.is_file():
        return {}
    with kv_meta_path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{kv_meta_path}: kv_meta must be a JSON object")
    return data


class _TokenizersFileTokenizer:
    def __init__(self, tokenizer_path: Path) -> None:
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(tokenizer_path / "tokenizer.json"))

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        encoding = self._tokenizer.encode(text, add_special_tokens=add_special_tokens)
        return list(encoding.ids)


def _load_hf_tokenizer(tokenizer_path: Path) -> Any:
    hf_error: Exception | None = None
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(str(tokenizer_path), trust_remote_code=True)
    except (ImportError, ValueError) as exc:
        hf_error = exc

    try:
        return _TokenizersFileTokenizer(tokenizer_path)
    except ImportError as exc:
        raise ImportError(
            "HuggingFace tokenizer profiles require transformers or tokenizers"
        ) from hf_error or exc


def _encode_hf(
    tokenizer: Any,
    request: ParsedRequest,
    profile: TokenizerProfile,
) -> list[int]:
    if not hasattr(tokenizer, "apply_chat_template"):
        rendered = render_simple_chat_template(
            request,
            include_tools=profile.include_tools,
            template_path=profile.chat_template_path,
        )
        return list(tokenizer.encode(rendered, add_special_tokens=False))

    messages = request.messages
    kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
    if profile.include_tools and request.tools:
        kwargs["tools"] = request.tools
    rendered = tokenizer.apply_chat_template(messages, **kwargs)
    return list(tokenizer.encode(rendered, add_special_tokens=False))
