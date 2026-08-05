"""Read-only monitoring/observability value types — Step 8.

Every dataclass here is a DERIVED view, computed on demand by
core/services/monitoring_service.py from Job + JobEvent data (and, for
queue-wait-reason, the Execution Coordinator's own live eligibility
logic) — none of these are separately persisted or separately mutated.
This is a deliberate design choice, not an oversight: persisting a
parallel copy of "current progress" or "current health" alongside the
Job/JobEvent tables would create two sources of truth that could drift
out of sync; computing them fresh from the append-only event log is
always correct by construction and costs nothing meaningful at this
project's scale ("avoid premature analytics" — Step 8 objective 5).

No field here is ever fabricated. Percentages are always None for engines
that cannot genuinely report one (etsy-video-generator, whose launch() is
a single blocking call with no real progress-fraction signal available) —
per Step 8's explicit instruction: "Do not fabricate percentages."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class JobProgressPhase(str, Enum):
    """The coarse, engine-agnostic phase every engine can report without
    any special support, derived purely from Job status + the most recent
    JobEvent — exactly the Step 8 objective 2 list.
    """

    UNKNOWN = "unknown"
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobProgress:
    phase: JobProgressPhase
    percentage: float | None = None
    stage: str | None = None
    message: str | None = None
    updated_at: datetime | None = None


@dataclass
class JobMetrics:
    """Step 8 objective 5's per-Job metrics, each derived from JobEvent
    timestamps (None if the relevant pair of events hasn't happened yet —
    e.g. queue_wait_seconds is None for a Job that was launched
    immediately with no observable queue time, and launch_duration_seconds
    is None until the Job reaches a terminal state).
    """

    queue_wait_seconds: float | None = None
    launch_duration_seconds: float | None = None
    total_execution_seconds: float | None = None


class EngineState(str, Enum):
    """Step 8 objective 3's example list. Derived from: is a Job currently
    RUNNING for this engine, and if so has it recorded LAUNCH_STARTED
    without LAUNCH_SUCCEEDED yet (LAUNCHING), or has launch() already
    returned successfully (BUSY) — else the engine's own discover()-
    reported health_status (IDLE/DEGRADED/OFFLINE). Even though
    etsy-video-generator's adapter.launch() is a single blocking call from
    the evaluate() request's own point of view, LAUNCHING is genuinely
    observable by a *different*, concurrent monitoring request: it sees
    whatever is actually persisted at query time, and there is a real
    (if brief) window between the LAUNCH_STARTED JobEvent being recorded
    and adapter.launch() returning (see
    core/execution/coordinator.py::evaluate()) during which that is the
    true state — not fabricated, just rarely observed at this project's
    scale.
    """

    IDLE = "idle"
    BUSY = "busy"
    LAUNCHING = "launching"
    OFFLINE = "offline"
    DEGRADED = "degraded"


@dataclass
class EngineHealth:
    engine_id: str
    state: EngineState
    current_job_id: str | None
    queue_length: int
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_launch_duration_seconds: float | None


class QueueWaitReason(str, Enum):
    """Step 8 objective 4: "design for future reasons" even though only
    ENGINE_BUSY is reachable today. WAITING_FOR_DEPENDENCY,
    WAITING_FOR_APPROVAL, and WAITING_FOR_SCHEDULER are reserved,
    unreachable values — pipelines, approvals, and any future scheduler
    concept are all still out of scope (per every prior step's disclosed
    scoping), so nothing in this codebase can produce them yet. Listing
    them now costs nothing and means core/services/monitoring_service.py
    doesn't need to change shape when those features eventually land.
    """

    ENGINE_BUSY = "engine_busy"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    EXECUTION_DISABLED = "execution_disabled"
    ELIGIBLE_PENDING_EVALUATION = "eligible_pending_evaluation"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"  # reserved — pipelines out of scope
    WAITING_FOR_APPROVAL = "waiting_for_approval"  # reserved — approvals out of scope
    WAITING_FOR_SCHEDULER = "waiting_for_scheduler"  # reserved — no scheduler concept exists yet


@dataclass
class QueuedJobInfo:
    job_id: str
    queued_at: datetime | None
    wait_reason: QueueWaitReason


@dataclass
class EngineMetrics:
    """Step 8 objective 5's per-engine aggregate counts, computed by
    counting JobEvent rows for this engine by type — never a separately
    maintained counter (see this module's docstring on why).
    """

    engine_id: str
    success_count: int
    failure_count: int
    timeout_count: int
    cancel_count: int


@dataclass
class EngineEligibility:
    """The Execution Coordinator's own live eligibility answer, exposed
    read-only (core/execution/coordinator.py::describe_eligibility()) so
    MonitoringService can explain *why* a queued Job is waiting using the
    exact same logic that decides it — never a second, potentially
    drifting reimplementation of that logic.
    """

    engine_id: str
    is_registered: bool
    is_healthy: bool
    execution_enabled: bool
    max_concurrent_runs: int
    running_count: int
    available_slots: int
