"""Streaming request shard builder."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Any
from collections.abc import Mapping

from infertwin.config.guard import guard_core_profiles
from infertwin.config.instance_runtime import InstanceRuntimeResolver
from infertwin.config.model_runtime import ResolvedModelRuntimeProfile
from infertwin.config.run_spec import RunSpec
from infertwin.experiment.request_builder import (
    RejectedTraceRecord,
    RequestBuildSettings,
    build_prompt_too_long_rejection,
    build_request_build_settings_from_config,
)
from infertwin.instance.request import build_simulation_request
from infertwin.request.build_context import RequestBuildContext
from infertwin.request.tokenizer_registry import PromptTooLongError
from infertwin.streaming.manifest import (
    STREAMING_MANIFEST_SCHEMA_VERSION,
    StreamingBuildManifest,
)
from infertwin.streaming.shard_store import StreamingRequestShardStore
from infertwin.trace.reader import read_trace_csv
from infertwin.trace.schema import TraceRecord

REJECTED_REQUEST_FIELDNAMES = (
    "request_id",
    "tenant_id",
    "instance_uuid",
    "reason",
    "detail",
    "prompt_tokens",
    "max_prompt_tokens",
    "tokenizer_profile",
)


@dataclass(frozen=True, slots=True)
class StreamingBuildResult:
    """Result of one streaming request shard build."""

    manifest: StreamingBuildManifest
    rejected_path: Path | None


class UnsortedTraceError(ValueError):
    """Raised when streaming build sees a trace row out of replay order."""


class StreamingRequestShardBuilder:
    """Build per-instance request shards without keeping all requests in memory."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        shard_root: str | Path,
        rejected_path: str | Path | None = None,
        require_sorted_trace: bool = True,
        runtime_resolver: InstanceRuntimeResolver | None = None,
    ) -> None:
        self.config = config
        self.shard_root = Path(shard_root)
        self.rejected_path = Path(rejected_path) if rejected_path is not None else None
        self.require_sorted_trace = require_sorted_trace
        self.runtime_resolver = runtime_resolver

    def build(self) -> StreamingBuildResult:
        settings = build_request_build_settings_from_config(self.config)
        request_build_resolver = StreamingRequestBuildResolver(
            settings=settings,
            runtime_resolver=self.runtime_resolver,
            output_dir=self.shard_root,
        )
        accepted_count = 0
        rejected_count = 0
        previous_key: tuple[float, str, str] | None = None

        with (
            StreamingRequestShardStore(self.shard_root) as shard_store,
            CsvRejectedTraceRecordWriter(self.rejected_path) as rejected_writer,
        ):
            for line_number, record in enumerate(read_trace_csv(settings.trace_path), start=2):
                current_key = _trace_sort_key(record)
                if self.require_sorted_trace:
                    _guard_sorted_trace(
                        previous_key=previous_key,
                        current_key=current_key,
                        line_number=line_number,
                    )
                previous_key = current_key

                try:
                    build_inputs = request_build_resolver.inputs_for(record)
                    request = build_simulation_request(
                        record,
                        tokenizer_registry=settings.tokenizer_registry,
                        block_size_tokens=build_inputs.block_size_tokens,
                        cache_scope=settings.cache_scope,
                        build_context=build_inputs.build_context,
                        tokenizer_profile=build_inputs.tokenizer_profile,
                    )
                except PromptTooLongError as exc:
                    rejected_writer.write(build_prompt_too_long_rejection(record, exc))
                    rejected_count += 1
                    continue

                shard_store.write(request)
                accepted_count += 1

            manifest = StreamingBuildManifest(
                schema_version=STREAMING_MANIFEST_SCHEMA_VERSION,
                trace_path=settings.trace_path,
                shard_root=self.shard_root,
                shards=shard_store.build_shards(),
                accepted_count=accepted_count,
                rejected_count=rejected_count,
                require_sorted_trace=self.require_sorted_trace,
            )

        return StreamingBuildResult(
            manifest=manifest,
            rejected_path=self.rejected_path if rejected_count > 0 else None,
        )


@dataclass(frozen=True, slots=True)
class StreamingRequestBuildInputs:
    """Resolved request-build inputs for one trace record."""

    block_size_tokens: int
    build_context: RequestBuildContext
    tokenizer_profile: str | None


class StreamingRequestBuildResolver:
    """Resolve request-build context from optional instance runtime profiles."""

    def __init__(
        self,
        *,
        settings: RequestBuildSettings,
        runtime_resolver: InstanceRuntimeResolver | None,
        output_dir: Path,
    ) -> None:
        self._settings = settings
        self._runtime_resolver = runtime_resolver
        self._output_dir = output_dir
        self._context_by_model: dict[str, RequestBuildContext] = {}

    def inputs_for(self, record: TraceRecord) -> StreamingRequestBuildInputs:
        if self._runtime_resolver is None:
            return StreamingRequestBuildInputs(
                block_size_tokens=self._settings.block_size_tokens,
                build_context=self._settings.build_context,
                tokenizer_profile=None,
            )
        runtime_profile = self._runtime_resolver.runtime_profile_for(record.instance_uuid)
        return StreamingRequestBuildInputs(
            block_size_tokens=runtime_profile.default_cache.block_size_tokens,
            build_context=self._context_for(runtime_profile),
            tokenizer_profile=runtime_profile.tokenizer_profile,
        )

    def _context_for(self, runtime_profile: ResolvedModelRuntimeProfile) -> RequestBuildContext:
        cached = self._context_by_model.get(runtime_profile.model_name)
        if cached is not None:
            return cached
        run_spec = RunSpec(
            trace_path=self._settings.trace_path,
            output_dir=self._output_dir,
            mode="capacity_sweep_streaming",
            model_name=runtime_profile.model_name,
            requested_block_size=runtime_profile.default_cache.block_size_tokens,
            model_profile=runtime_profile.model_profile_path,
            deployment_profile=runtime_profile.deployment_profile_path,
        )
        guard_core_profiles(
            run_spec=run_spec,
            model_profile=runtime_profile.model_profile,
            deployment_profile=runtime_profile.deployment_profile,
            block_conversion_enabled=True,
        ).raise_if_blocked()
        context = RequestBuildContext.from_profiles(
            run_spec=run_spec,
            model_profile=runtime_profile.model_profile,
            deployment_profile=runtime_profile.deployment_profile,
            max_prompt_tokens=self._settings.build_context.max_prompt_tokens,
        )
        self._context_by_model[runtime_profile.model_name] = context
        return context


class CsvRejectedTraceRecordWriter:
    """Write tokenizer-stage rejected trace records incrementally."""

    def __init__(self, path: str | Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self._file = None
        self._writer: csv.DictWriter | None = None

    def __enter__(self) -> CsvRejectedTraceRecordWriter:
        if self._path is None:
            return self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=REJECTED_REQUEST_FIELDNAMES)
        self._writer.writeheader()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._file is not None:
            self._file.close()
        self._file = None
        self._writer = None

    def write(self, record: RejectedTraceRecord) -> None:
        if self._writer is None:
            return
        self._writer.writerow(asdict(record))


def _trace_sort_key(record: TraceRecord) -> tuple[float, str, str]:
    return (
        record.service_start_time.timestamp() * 1000.0,
        record.instance_uuid,
        record.request_id,
    )


def _guard_sorted_trace(
    *,
    previous_key: tuple[float, str, str] | None,
    current_key: tuple[float, str, str],
    line_number: int,
) -> None:
    if previous_key is None or current_key >= previous_key:
        return
    raise UnsortedTraceError(
        "trace must be sorted by (service_start_time, instance_uuid, request_id); "
        f"line_number={line_number}, previous_key={previous_key}, current_key={current_key}"
    )
