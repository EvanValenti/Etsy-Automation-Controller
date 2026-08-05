"""Domain event type definitions.

Design spec Section 2: "Events are domain facts, not commands — past-tense,
immutable, typed." Application Services publish these as a side effect of
state transitions they already own and persist — the bus never decides
anything, only announces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DomainEvent:
    """Base type for all events on the bus. Immutable by convention (frozen)."""

    occurred_at: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class JobCreated(DomainEvent):
    job_id: str = ""
    engine_id: str = ""
    pipeline_run_id: str | None = None


@dataclass(frozen=True)
class JobStarted(DomainEvent):
    job_id: str = ""
    engine_id: str = ""


@dataclass(frozen=True)
class JobProgressUpdated(DomainEvent):
    job_id: str = ""
    progress: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRequested(DomainEvent):
    approval_id: str = ""
    job_id: str = ""
    type: str = ""


@dataclass(frozen=True)
class ApprovalResolved(DomainEvent):
    approval_id: str = ""
    job_id: str = ""
    decision: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineCompleted(DomainEvent):
    job_id: str = ""
    engine_id: str = ""


@dataclass(frozen=True)
class EngineFailed(DomainEvent):
    job_id: str = ""
    engine_id: str = ""
    error_category: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class PipelineAdvanced(DomainEvent):
    pipeline_run_id: str = ""
    completed_step_id: str | None = None
    next_step_id: str | None = None


@dataclass(frozen=True)
class PipelineCompleted(DomainEvent):
    pipeline_run_id: str = ""


@dataclass(frozen=True)
class RetryScheduled(DomainEvent):
    job_id: str = ""
    attempt: int = 0
    reason: str = ""
