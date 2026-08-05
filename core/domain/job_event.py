"""JobEvent domain entity — Step 8 (monitoring/observability).

A persistent, structured timeline entry for a Job. Deliberately structured
fields (event_type + a JSON metadata dict), not a free-form log string —
per this step's explicit instruction: "Avoid free-form log strings where
structured fields are more appropriate."

`engine_id` is denormalized onto the event (also derivable via the Job)
specifically to make per-engine aggregation queries (Step 8 objective 5:
timeout_count, cancel_count, etc.) simple single-table queries rather than
requiring a join in a JobEventRepository implementation that may not
support one — see core/repositories/job_event_repository.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class JobEventType(str, Enum):
    """Every event type this step actually emits, matching the Step 8
    instruction's example list one-for-one, mapped onto the real
    transitions core/services/job_service.py and
    core/execution/coordinator.py already implement:

      CREATED           - JobService.create_job() persists a new Job
      QUEUED            - Coordinator._enqueue_validated_jobs() -> mark_queued()
      ENGINE_RESERVED   - Coordinator.evaluate() -> mark_running() (a
                           concurrency slot claimed, before launch() is called)
      LAUNCH_STARTED    - Coordinator.evaluate(), immediately before
                           adapter.launch() is actually invoked
      LAUNCH_SUCCEEDED  - adapter.launch() returned a reference without raising
      LAUNCH_FAILED     - adapter.launch() raised EngineLaunchError (any
                           category other than "launch_timeout"), OR
                           collect_results() reported failure after a
                           successful launch()
      TIMED_OUT         - adapter.launch() raised EngineLaunchError with
                           category "launch_timeout" specifically — a
                           distinct, more diagnostic signal than a generic
                           LAUNCH_FAILED
      CANCELLED         - Coordinator.cancel_job() succeeded, for a Job in
                           any cancellable state (RUNNING or
                           PENDING/VALIDATED/QUEUED)
      COMPLETED         - JobService.mark_succeeded() — the Job's overall
                           lifecycle reached a terminal SUCCEEDED state
                           (distinct from LAUNCH_SUCCEEDED: launch() can
                           succeed while collect_results() still reports
                           failure, e.g. a missing/corrupt manifest — see
                           infra/adapters/video_generator/adapter.py's
                           collect_results())
    """

    CREATED = "created"
    QUEUED = "queued"
    ENGINE_RESERVED = "engine_reserved"
    LAUNCH_STARTED = "launch_started"
    LAUNCH_SUCCEEDED = "launch_succeeded"
    LAUNCH_FAILED = "launch_failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


@dataclass
class JobEvent:
    id: str
    job_id: str
    engine_id: str
    event_type: JobEventType
    occurred_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
