"""MonitoringService — Step 8's read-only observability layer.

Design constraint repeated throughout Step 8's brief: progress, health,
queue-wait-reason, and metrics views must be *derived at query time* from
the Job table and the JobEvent log, never stored as a second, separately
mutated copy of state that could drift from what actually happened. Every
method below is a pure read: it never calls a JobService `mark_*()`
method, never writes a JobEvent, and never touches an adapter. This keeps
the ownership split intact — "Coordinator records events; adapters own
engine-specific progress, never manipulate Controller persistence
directly" — MonitoringService does not manipulate persistence either; it
only reads it.

"Do not fabricate percentages": get_job_progress() below never invents a
JobProgress.percentage. Every generic (non-engine-aware) Job in this
Controller reports None for percentage — there is no generic way to know
how far through an arbitrary engine run a Job is, only whether it has
started, is running, or has finished. A future engine-aware progress
source (an adapter's own monitor() call) could supply a real percentage,
but wiring that in is out of scope here; nothing about this service's
shape prevents it from being added later.
"""

from __future__ import annotations

from datetime import datetime

from core.domain.job import JobStatus
from core.domain.job_event import JobEvent, JobEventType
from core.domain.monitoring import (
    EngineHealth,
    EngineMetrics,
    EngineState,
    JobMetrics,
    JobProgress,
    JobProgressPhase,
    QueuedJobInfo,
    QueueWaitReason,
)
from core.execution.coordinator import ExecutionCoordinator
from core.repositories.job_event_repository import JobEventRepository
from core.services.engine_registry_service import EngineRegistryService
from core.services.job_service import JobNotFoundError, JobService

_TERMINAL_EVENT_TYPES = {
    JobEventType.COMPLETED,
    JobEventType.LAUNCH_FAILED,
    JobEventType.TIMED_OUT,
    JobEventType.CANCELLED,
}

_FAILURE_EVENT_TYPES = {JobEventType.LAUNCH_FAILED, JobEventType.TIMED_OUT}


def _first_occurrence(events: list[JobEvent], event_type: JobEventType) -> datetime | None:
    for event in events:
        if event.event_type == event_type:
            return event.occurred_at
    return None


def _last_occurrence(events: list[JobEvent], event_type: JobEventType) -> datetime | None:
    match = None
    for event in events:
        if event.event_type == event_type:
            match = event.occurred_at
    return match


class MonitoringService:
    def __init__(
        self,
        job_service: JobService,
        job_event_repository: JobEventRepository,
        engine_registry: EngineRegistryService,
        coordinator: ExecutionCoordinator,
    ) -> None:
        self._jobs = job_service
        self._job_events = job_event_repository
        self._engines = engine_registry
        self._coordinator = coordinator

    # -- Job-level views ---------------------------------------------------

    def get_job_timeline(self, job_id: str) -> list[JobEvent]:
        return self._jobs.list_events(job_id)

    # -- Cross-cutting views (Step 9) ---------------------------------------

    def get_recent_activity(self, limit: int = 50) -> list[JobEvent]:
        """Global activity timeline for the dashboard — the most recent
        JobEvents across every job/engine, newest first. Unlike every
        other method on this service, this one is not scoped to a single
        job or engine; it exists specifically because the Step 8 surface
        (get_job_timeline/get_engine_health/etc.) has no view that spans
        the whole Controller at once, which the design spec's planned
        dashboard needs.
        """
        return self._job_events.list_recent(limit)

    def get_job_progress(self, job_id: str) -> JobProgress:
        job = self._jobs.get_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        events = self._jobs.list_events(job_id)
        phase = self._infer_phase(job.status, events)
        stage = events[-1].event_type.value if events else None
        return JobProgress(phase=phase, percentage=None, stage=stage, message=None, updated_at=job.updated_at)

    def _infer_phase(self, status: JobStatus, events: list[JobEvent]) -> JobProgressPhase:
        if status == JobStatus.QUEUED:
            return JobProgressPhase.QUEUED
        if status == JobStatus.RUNNING:
            if _first_occurrence(events, JobEventType.LAUNCH_STARTED) is not None and (
                _first_occurrence(events, JobEventType.LAUNCH_SUCCEEDED) is None
            ):
                return JobProgressPhase.STARTING
            return JobProgressPhase.RUNNING
        if status == JobStatus.SUCCEEDED:
            return JobProgressPhase.COMPLETED
        if status == JobStatus.FAILED:
            return JobProgressPhase.FAILED
        if status == JobStatus.CANCELLED:
            return JobProgressPhase.CANCELLED
        return JobProgressPhase.UNKNOWN

    def get_job_metrics(self, job_id: str) -> JobMetrics:
        events = self._jobs.list_events(job_id)
        created_at = _first_occurrence(events, JobEventType.CREATED)
        queued_at = _first_occurrence(events, JobEventType.QUEUED)
        reserved_at = _first_occurrence(events, JobEventType.ENGINE_RESERVED)
        launch_started_at = _first_occurrence(events, JobEventType.LAUNCH_STARTED)

        terminal_at = None
        for event in events:
            if event.event_type in _TERMINAL_EVENT_TYPES:
                terminal_at = event.occurred_at

        queue_wait_seconds = (
            (reserved_at - queued_at).total_seconds() if queued_at is not None and reserved_at is not None else None
        )
        launch_duration_seconds = (
            (terminal_at - launch_started_at).total_seconds()
            if launch_started_at is not None and terminal_at is not None
            else None
        )
        total_execution_seconds = (
            (terminal_at - created_at).total_seconds() if created_at is not None and terminal_at is not None else None
        )
        return JobMetrics(
            queue_wait_seconds=queue_wait_seconds,
            launch_duration_seconds=launch_duration_seconds,
            total_execution_seconds=total_execution_seconds,
        )

    # -- Engine-level views --------------------------------------------------

    def get_engine_health(self, engine_id: str) -> EngineHealth:
        engine = self._engines.get_engine(engine_id)
        running_jobs = [job for job in self._jobs.list_jobs(status=JobStatus.RUNNING) if job.engine_id == engine_id]
        queue_length = len(
            [job for job in self._jobs.list_jobs(status=JobStatus.QUEUED) if job.engine_id == engine_id]
        )
        events = self._job_events.list_by_engine(engine_id)

        if engine is None:
            state = EngineState.OFFLINE
        elif engine.health_status != "healthy":
            state = EngineState.DEGRADED
        elif running_jobs:
            current_events = self._jobs.list_events(running_jobs[0].id)
            launched = _first_occurrence(current_events, JobEventType.LAUNCH_STARTED) is not None
            succeeded = _first_occurrence(current_events, JobEventType.LAUNCH_SUCCEEDED) is not None
            state = EngineState.LAUNCHING if launched and not succeeded else EngineState.BUSY
        else:
            state = EngineState.IDLE

        last_success_at = _last_occurrence(events, JobEventType.COMPLETED)
        last_failure_at = None
        for event in events:
            if event.event_type in _FAILURE_EVENT_TYPES:
                last_failure_at = event.occurred_at

        last_launch_duration_seconds = None
        last_started_at = _last_occurrence(events, JobEventType.LAUNCH_STARTED)
        if last_started_at is not None:
            for event in events:
                if event.occurred_at >= last_started_at and event.event_type in (
                    JobEventType.LAUNCH_SUCCEEDED,
                    *_FAILURE_EVENT_TYPES,
                ):
                    last_launch_duration_seconds = (event.occurred_at - last_started_at).total_seconds()
                    break

        return EngineHealth(
            engine_id=engine_id,
            state=state,
            current_job_id=running_jobs[0].id if running_jobs else None,
            queue_length=queue_length,
            last_success_at=last_success_at,
            last_failure_at=last_failure_at,
            last_launch_duration_seconds=last_launch_duration_seconds,
        )

    def get_engine_queue(self, engine_id: str) -> list[QueuedJobInfo]:
        queued_jobs = sorted(
            (job for job in self._jobs.list_jobs(status=JobStatus.QUEUED) if job.engine_id == engine_id),
            key=lambda job: job.created_at,
        )
        eligibility = self._coordinator.describe_eligibility(engine_id)

        if not eligibility.is_registered or not eligibility.is_healthy:
            engine_level_reason = QueueWaitReason.ENGINE_UNAVAILABLE
        elif not eligibility.execution_enabled:
            engine_level_reason = QueueWaitReason.EXECUTION_DISABLED
        else:
            engine_level_reason = None

        result = []
        for position, job in enumerate(queued_jobs):
            if engine_level_reason is not None:
                reason = engine_level_reason
            elif position < eligibility.available_slots:
                reason = QueueWaitReason.ELIGIBLE_PENDING_EVALUATION
            else:
                reason = QueueWaitReason.ENGINE_BUSY
            result.append(QueuedJobInfo(job_id=job.id, queued_at=job.created_at, wait_reason=reason))
        return result

    def get_engine_metrics(self, engine_id: str) -> EngineMetrics:
        events = self._job_events.list_by_engine(engine_id)
        return EngineMetrics(
            engine_id=engine_id,
            success_count=sum(1 for e in events if e.event_type == JobEventType.COMPLETED),
            failure_count=sum(1 for e in events if e.event_type == JobEventType.LAUNCH_FAILED),
            timeout_count=sum(1 for e in events if e.event_type == JobEventType.TIMED_OUT),
            cancel_count=sum(1 for e in events if e.event_type == JobEventType.CANCELLED),
        )
