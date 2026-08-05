"""Job domain entity.

Design spec Section 3:

    Job — one execution of one engine.
      id, engine_id, pipeline_run_id (nullable — a Job can stand alone or
      belong to a PipelineRun)
      status: pending / running / waiting_on_approval / succeeded / failed / cancelled
      config: engine-specific launch params, opaque JSON to the Core
      run_reference: an EngineRunReference
      error: nullable, structured
      created_at, timestamps for each transition

Step 4 revision: JobStatus gains VALIDATED and QUEUED (both reachable
without any engine execution — see core/services/job_service.py). The
`error` field is renamed to `error_summary` and a new `result_summary`
field is added, matching the exact field list specified for Step 4;
`started_at`/`completed_at` are replaced by a single `updated_at`,
bumped on every transition — a more general "last changed" timestamp
that doesn't need a new column for every future transition kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from core.domain.engine import EngineRunReference


class JobStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_ON_APPROVAL = "waiting_on_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobError:
    """Structured failure detail, never a bare string.

    Not produced by any real code path this step — mark_failed() remains
    unimplemented (requires real engine execution, out of scope for
    Step 4) — this exists so error_summary's eventual shape is anticipated
    now, per "design everything so these can be added without changing
    the Job schema."
    """

    category: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "message": self.message, "detail": self.detail}


@dataclass
class Job:
    """One execution of one engine, optionally belonging to a PipelineRun.

    `config` is opaque JSON to the Core — the Core never interprets its
    contents; only the bound EngineAdapter's `validate()`/`launch()` do.
    """

    id: str
    engine_id: str
    config: dict[str, Any]
    status: JobStatus = JobStatus.PENDING
    pipeline_run_id: str | None = None
    run_reference: EngineRunReference | None = None
    result_summary: dict[str, Any] | None = None
    error_summary: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
