"""Trace record schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TraceRecord:
    request_id: str
    tenant_id: str
    instance_uuid: str
    request_params: str
    service_start_time: datetime
