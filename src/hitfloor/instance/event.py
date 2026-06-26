"""Discrete event primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    ARRIVAL = "arrival"
    ITERATION_COMPLETE = "iteration_complete"


@dataclass(order=True, frozen=True, slots=True)
class Event:
    time_ms: float
    sequence: int
    event_type: EventType = field(compare=False)
    payload: object = field(compare=False, default=None)
