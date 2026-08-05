"""JobService — owns a Job's existence, config, persisted status, and history.

Design spec Section 3a division-of-ownership table: "A Job exists, its
config, its persisted status, its history" -> JobService. It does NOT
decide *when* a Job launches — that remains the Execution Coordinator's
job (out of scope through Step 4).

Step 4 scope: create_job(), get_job(), list_jobs(), cancel_job(), and
delete_job() were made real. mark_running/record_progress/mark_succeeded/
mark_failed/mark_waiting_on_approval were left unimplemented, with an
explicit open question flagged for Step 5: should mark_running() (and by
extension mark_succeeded()/mark_failed()) enforce their own strict
status-transition guards the way cancel_job() does?

Step 5 answer (Execution Coordinator step): YES, decided now rather than
deferred again. mark_queued() (new), mark_running(), mark_succeeded(), and
mark_failed() all now enforce a single required prior status each,
exactly mirroring cancel_job()'s guard pattern:
  VALIDATED -> QUEUED   (mark_queued)   — driven by the Execution
                                           Coordinator's evaluate(), see
                                           core/execution/coordinator.py
  QUEUED    -> RUNNING  (mark_running)  — driven by the Coordinator,
                                           immediately before it calls
                                           adapter.launch()
  RUNNING   -> SUCCEEDED (mark_succeeded) — not reachable by any code path
                                             yet; monitor()/collect_results()
                                             remain unimplemented, so
                                             nothing ever calls this. Guard
                                             logic is decided now anyway,
                                             per this step's explicit
                                             instruction not to defer it.
  RUNNING   -> FAILED    (mark_failed)    — same scope note as above.
None of these four methods publish an event or call anything on an
adapter themselves — event publishing remains out of scope, and adapter
interaction is the Coordinator's job, never JobService's.

record_progress()/mark_waiting_on_approval() remain unimplemented stubs —
approvals remain out of scope through Step 8; real progress *reporting*
(as opposed to progress *polling*, which record_progress() would be for)
is now handled entirely differently — see core/services/monitoring_service.py,
which derives progress from the event log rather than needing a live
monitor() poll to feed it.

Step 8 addition: record_event()/list_events(), backed by a new
JobEventRepository dependency (additive constructor parameter — every
existing call site was updated, no behavior of any existing method
changed). create_job() now calls record_event() itself to log CREATED —
the one deliberate exception to "the Coordinator records events" (see
core/execution/coordinator.py's module docstring for the full
rationale): job creation isn't an orchestration decision, so there is no
Coordinator step to attribute it to. Every other event type
(QUEUED, ENGINE_RESERVED, LAUNCH_STARTED, LAUNCH_SUCCEEDED,
LAUNCH_FAILED, TIMED_OUT, COMPLETED, CANCELLED) is recorded by the
Coordinator, not by any change to this file's existing mark_*() methods —
those remain exactly as they were in Step 7, unchanged.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from core.domain.engine import EngineRunReference
from core.domain.job import Job, JobError, JobStatus
from core.domain.job_event import JobEvent, JobEventType
from core.events.event_bus import EventBus
from core.repositories.job_event_repository import JobEventRepository
from core.repositories.job_repository import JobRepository
from core.services.engine_registry_service import EngineRegistryService

_CANCELLABLE_WITHOUT_EXECUTION = {JobStatus.PENDING, JobStatus.VALIDATED, JobStatus.QUEUED}
_TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}


class JobNotFoundError(Exception):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"No job found with id={job_id!r}")
        self.job_id = job_id


class JobValidationError(Exception):
    """Raised when the resolved adapter's validate() reports the submitted
    config invalid. No Job is persisted when this is raised.
    """

    def __init__(self, engine_id: str, errors: list[str]) -> None:
        super().__init__(f"Invalid configuration for engine_id={engine_id!r}: {errors}")
        self.engine_id = engine_id
        self.errors = errors


class InvalidJobStatusTransitionError(Exception):
    def __init__(self, job_id: str, current_status: JobStatus, attempted: str) -> None:
        super().__init__(f"Job {job_id!r} cannot transition from {current_status.value!r} via {attempted!r}")
        self.job_id = job_id
        self.current_status = current_status


class JobService:
    def __init__(
        self,
        job_repository: JobRepository,
        event_bus: EventBus,
        engine_registry: EngineRegistryService,
        job_event_repository: JobEventRepository,
    ) -> None:
        self._jobs = job_repository
        self._events = event_bus
        self._engines = engine_registry
        self._job_events = job_event_repository

    def create_job(
        self,
        engine_id: str,
        config: dict[str, Any],
        pipeline_run_id: str | None = None,
    ) -> Job:
        """Validate the engine exists, validate configuration using the
        resolved adapter's validate() method, persist the Job as VALIDATED,
        and return it. No Job is persisted if either validation step fails.

        `self._engines.get_adapter(engine_id)` performs the "engine
        exists" check itself (it raises EngineRegistryService's own
        UnknownEngineError if the engine_id isn't registered) before ever
        resolving/constructing an adapter — this method does not duplicate
        that lookup.
        """
        adapter = self._engines.get_adapter(engine_id)

        validation_result = adapter.validate(config)
        if not validation_result.is_valid:
            raise JobValidationError(engine_id, validation_result.errors)

        now = datetime.now(timezone.utc)
        job = Job(
            id=str(uuid.uuid4()),
            engine_id=engine_id,
            config=config,
            status=JobStatus.VALIDATED,
            pipeline_run_id=pipeline_run_id,
            created_at=now,
            updated_at=now,
        )
        self._jobs.add(job)
        self.record_event(job.id, JobEventType.CREATED, {"engine_id": engine_id, "pipeline_run_id": pipeline_run_id})
        return job

    def record_event(
        self, job_id: str, event_type: JobEventType, metadata: dict[str, Any] | None = None
    ) -> JobEvent:
        """Persist one JobEvent (Step 8). Looks the Job up itself to
        denormalize engine_id onto the event (see
        core/domain/job_event.py's docstring for why) — raises
        JobNotFoundError if the job_id is unknown, same as every other
        method here, rather than silently recording an orphaned event.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        event = JobEvent(
            id=str(uuid.uuid4()),
            job_id=job_id,
            engine_id=job.engine_id,
            event_type=event_type,
            occurred_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )
        self._job_events.add(event)
        return event

    def list_events(self, job_id: str) -> list[JobEvent]:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return self._job_events.list_by_job(job_id)

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self, status: JobStatus | None = None, engine_id: str | None = None) -> list[Job]:
        """Step 9 addition: optional engine_id filter, alongside the existing
        status filter (either, both, or neither may be supplied). No new
        repository method needed — JobRepository only distinguishes by
        status (list_by_status/list_all), so an engine_id filter is applied
        in-memory over that result, mirroring the same pattern
        MonitoringService already uses for its own per-engine views (e.g.
        get_engine_health()'s `[job for job in ... if job.engine_id ==
        engine_id]`) rather than introducing a second, inconsistent
        filtering approach.
        """
        jobs = self._jobs.list_by_status(status) if status is not None else self._jobs.list_all()
        if engine_id is not None:
            jobs = [job for job in jobs if job.engine_id == engine_id]
        return jobs

    def cancel_job(self, job_id: str) -> Job:
        """The one status transition this step implements as reachable
        for an in-flight Job: PENDING/VALIDATED/QUEUED -> CANCELLED,
        requiring no adapter interaction at all — purely Controller-side
        bookkeeping. A Job already in a terminal state cannot be
        cancelled again. A Job that is RUNNING or WAITING_ON_APPROVAL
        cannot be cancelled by this method — that requires
        adapter.cancel(), which is Execution Coordinator territory and
        explicitly out of scope this step; calling this method on such a
        Job raises NotImplementedError rather than silently no-op'ing or
        pretending to cancel it.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)

        if job.status in _CANCELLABLE_WITHOUT_EXECUTION:
            job.status = JobStatus.CANCELLED
            job.updated_at = datetime.now(timezone.utc)
            self._jobs.update(job)
            return job

        if job.status in _TERMINAL_STATUSES:
            raise InvalidJobStatusTransitionError(job_id, job.status, "cancel")

        raise NotImplementedError(
            f"Job {job_id!r} is {job.status.value!r} — cancelling a Job that is already running or "
            "waiting on approval requires adapter.cancel(), which is not implemented until the "
            "Execution Coordinator step."
        )

    def delete_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._jobs.delete(job_id)

    # -- Execution-adjacent transitions (Step 5: real, guarded) -------------

    def mark_queued(self, job_id: str) -> Job:
        """VALIDATED -> QUEUED. Driven by the Execution Coordinator's
        evaluate() for every VALIDATED Job belonging to the engine it is
        currently evaluating. Purely Controller-side bookkeeping — no
        adapter interaction.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.VALIDATED:
            raise InvalidJobStatusTransitionError(job_id, job.status, "mark_queued")
        job.status = JobStatus.QUEUED
        job.updated_at = datetime.now(timezone.utc)
        self._jobs.update(job)
        return job

    def mark_running(self, job_id: str) -> Job:
        """QUEUED -> RUNNING. Called by the Execution Coordinator
        immediately before it calls adapter.launch() for this Job — see
        core/execution/coordinator.py::evaluate(). Guard requires QUEUED
        as the only valid prior state (Step 5's resolution of the design
        question deferred at the end of Step 4). Does not itself call
        adapter.launch() or publish JobStarted — launching is the
        Coordinator's responsibility, and event publishing remains out of
        scope.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.QUEUED:
            raise InvalidJobStatusTransitionError(job_id, job.status, "mark_running")
        job.status = JobStatus.RUNNING
        job.updated_at = datetime.now(timezone.utc)
        self._jobs.update(job)
        return job

    def record_run_reference(self, job_id: str, run_reference: EngineRunReference) -> Job:
        """Persist an in-flight EngineRunReference WITHOUT changing status
        (Step 7). Called by the Execution Coordinator's on_run_reference
        callback the moment adapter.launch() reports a live process
        handle — see core/adapters/engine_adapter.py's Protocol docstring
        for why this needs to happen before launch() returns, not after:
        it's what makes a concurrent cancel_job() call able to find a
        real PID to act on while the original request's launch() call is
        still blocking. Requires the Job to currently be RUNNING (it
        should always be, since mark_running() is called immediately
        before adapter.launch()) — raises InvalidJobStatusTransitionError
        otherwise rather than silently overwriting a reference on a Job
        that isn't actually in flight.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.RUNNING:
            raise InvalidJobStatusTransitionError(job_id, job.status, "record_run_reference")
        job.run_reference = run_reference
        job.updated_at = datetime.now(timezone.utc)
        self._jobs.update(job)
        return job

    def mark_cancelled_from_running(self, job_id: str) -> Job:
        """RUNNING -> CANCELLED (Step 7). Distinct from cancel_job() above
        (which only ever handles PENDING/VALIDATED/QUEUED -> CANCELLED and
        is UNCHANGED from Step 4/5 — no regression to that path). This
        method exists specifically for the Execution Coordinator's new
        cancel_job() to call AFTER adapter.cancel() has already stopped
        the real process — never called directly by any route or by this
        class's own cancel_job(), since JobService itself never talks to
        an adapter (that stays Coordinator territory).
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.RUNNING:
            raise InvalidJobStatusTransitionError(job_id, job.status, "mark_cancelled_from_running")
        job.status = JobStatus.CANCELLED
        job.updated_at = datetime.now(timezone.utc)
        self._jobs.update(job)
        return job

    def record_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        """Called from a monitor() poll result. Persists nothing beyond the
        latest progress snapshot and publishes JobProgressUpdated.
        Monitoring remains out of scope through Step 5 — unimplemented.
        """
        raise NotImplementedError

    def mark_succeeded(self, job_id: str, result: dict[str, Any]) -> Job:
        """RUNNING -> SUCCEEDED. Guard decided as part of Step 5 alongside
        mark_running()/mark_failed(), but not reachable by any code path
        yet — nothing currently calls this, since monitor()/
        collect_results() remain unimplemented and the Coordinator never
        catches or resolves a launch() outcome this step.
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.RUNNING:
            raise InvalidJobStatusTransitionError(job_id, job.status, "mark_succeeded")
        job.status = JobStatus.SUCCEEDED
        job.result_summary = result
        job.updated_at = datetime.now(timezone.utc)
        self._jobs.update(job)
        return job

    def mark_failed(self, job_id: str, error: JobError) -> Job:
        """RUNNING -> FAILED. Same scope note as mark_succeeded(): guard
        decided now, not yet reachable — the Coordinator intentionally
        lets adapter.launch() exceptions propagate raw this step rather
        than catching them and calling this method (see coordinator.py).
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.RUNNING:
            raise InvalidJobStatusTransitionError(job_id, job.status, "mark_failed")
        job.status = JobStatus.FAILED
        job.error_summary = error.to_dict()
        job.updated_at = datetime.now(timezone.utc)
        self._jobs.update(job)
        return job

    def mark_waiting_on_approval(self, job_id: str, approval_id: str) -> None:
        raise NotImplementedError
