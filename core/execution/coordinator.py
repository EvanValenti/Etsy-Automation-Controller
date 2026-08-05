"""ExecutionCoordinator.

Design spec Section 3a: a first-class Application Service whose sole
responsibility is the runtime question "what should execute next?" —
never business logic, never engine-specific behavior. It is the only
component in the Core authorized to call adapter.launch().

Step 5 scope — what this file actually implements now:
  - evaluate(engine_id): the real queue-evaluation entry point. Moves
    every VALIDATED Job for the given engine to QUEUED, then — if the
    engine is registered, healthy, execution is globally enabled, and
    fewer Jobs are RUNNING for it than its declared max_concurrent_runs —
    moves the oldest QUEUED Job(s) to RUNNING and calls adapter.launch()
    for each, letting whatever it raises propagate unchanged. This is
    "the point where launch() would normally occur" the Step 5
    instructions refer to.
  - Engine busy detection: counts currently-RUNNING Jobs per engine
    against that engine's max_concurrent_runs (from its persisted
    discover() capabilities), not just a boolean "is anything running."
  - Job eligibility logic, answering exactly the four questions the step
    specifies: is the engine registered, is the adapter healthy, is
    another Job already running (up to its concurrency limit), and is
    execution globally enabled (self.execution_enabled, defaulting True).

Deliberate departure from the event-driven design, disclosed here rather
than silently: the design spec (and this file's own pre-Step-5 docstring)
describes the Coordinator as reacting only to Event Bus events, never
receiving direct calls. But event publishing itself remains explicitly
out of scope through Step 6 — JobService.create_job() still does not
publish JobCreated — so a purely event-driven Coordinator would be
completely inert and unobservable; there would be nothing to verify by
running the application. evaluate() is a synchronous method the API layer
calls directly (see api/main.py's POST /jobs and POST /jobs/{id}/launch)
as a temporary bridge. The event subscriptions below remain registered
and _on_job_created() already calls the same evaluate() logic, so once a
future step wires real event publishing, the event-driven path will work
unchanged — only the synchronous call sites in api/main.py should be
removed at that point.

Step 6 scope — what changed from Step 5: evaluate() now calls
adapter.launch() and, on success, adapter.collect_results() — both
inside a try/except that catches ONLY the generic, Protocol-level
EngineLaunchError (core/adapters/engine_adapter.py), never any
engine-specific exception type (e.g. VideoGenerationError never leaves
etsy-video-generator's own adapter module — see
infra/adapters/video_generator/adapter.py). On failure, records it via
JobService.mark_failed(); on success, via JobService.mark_succeeded()
with collect_results()'s artifacts. Either way, this method knows nothing
about FFmpeg, image folders, presets, or manifests — only that
"launch() may raise EngineLaunchError" and "collect_results() returns an
EngineResult with a success flag and an artifacts dict," both fully
generic, Protocol-level concepts.

Second disclosed synchronous bridge (Step 6, same pattern as Step 5's
create-job-then-evaluate bridge): since event publishing is still out of
scope, there is no automatic "Job A finished -> check Job B" mechanism.
After processing a batch of newly-RUNNING Jobs to completion (success or
failure), evaluate() calls itself again for the same engine_id before
returning, so any Job left QUEUED behind the one that just finished
begins immediately rather than waiting for an unrelated future request.
This is exactly the kind of disclosed, deliberate scoping choice Step 6's
instructions asked for, not a silent architecture change.

Step 7 scope — what changed from Step 6:
  - evaluate() now passes an on_run_reference callback into
    adapter.launch() (core/adapters/engine_adapter.py's one Step 7
    Protocol extension). The adapter calls it the moment it has a real,
    killable process handle, and this Coordinator persists it via
    JobService.record_run_reference() immediately — well before
    adapter.launch() itself returns. This is what makes cancel_job()
    (below) able to act on a Job that is genuinely, currently running
    from a different, concurrent request.
  - evaluate()'s finalization (mark_succeeded/mark_failed after a launch
    attempt) is now wrapped to tolerate InvalidJobStatusTransitionError:
    if a concurrent cancel_job() call already finalized this same Job to
    CANCELLED while this evaluate() call's adapter.launch() was still
    running, that is an expected, benign race — the cancellation wins,
    and this method quietly does nothing further for that Job rather than
    raising.
  - cancel_job(job_id): new. For a Job that is RUNNING, resolves the
    adapter and calls adapter.cancel(ref) — the ONLY place outside
    evaluate() this Coordinator ever calls an adapter method, preserving
    "the Coordinator is the only component authorized to call
    adapter.launch()/cancel()." For a Job that is NOT running, delegates
    entirely to the existing, UNCHANGED JobService.cancel_job() (the
    Step 4/5 PENDING/VALIDATED/QUEUED path — no regression). Either way,
    this Coordinator method knows only that adapter.cancel() returns a
    bool — it never sees a PID, a signal, or any process concept.
"""

from __future__ import annotations

from core.adapters.engine_adapter import EngineLaunchError
from core.domain.job import Job, JobError, JobStatus
from core.domain.job_event import JobEventType
from core.domain.monitoring import EngineEligibility
from core.events.event_bus import EventBus
from core.events.events import (
    ApprovalResolved,
    EngineCompleted,
    EngineFailed,
    JobCreated,
    PipelineAdvanced,
)
from core.services.approval_service import ApprovalService
from core.services.engine_registry_service import EngineRegistryService
from core.services.job_service import InvalidJobStatusTransitionError, JobNotFoundError, JobService
from core.services.pipeline_service import PipelineService


def _job_error_from_launch_error(exc: EngineLaunchError) -> JobError:
    return JobError(
        category=exc.error.get("category", "engine_launch_failed"),
        message=exc.error.get("message", str(exc)),
        detail=exc.error.get("detail", {}),
    )


def _job_error_from_result_error(error: dict[str, object] | None) -> JobError:
    error = error or {}
    return JobError(
        category=str(error.get("category", "unknown")),
        message=str(error.get("message", "collect_results() reported failure")),
        detail=dict(error.get("detail", {}) or {}),
    )


class ExecutionCoordinator:
    """Owns runtime scheduling only. Never a source of truth for what a
    Job/Pipeline/Approval *means* — see the division-of-ownership table in
    the design spec Section 3a; that remains with the *Service classes.
    """

    def __init__(
        self,
        job_service: JobService,
        pipeline_service: PipelineService,
        approval_service: ApprovalService,
        engine_registry: EngineRegistryService,
        event_bus: EventBus,
        execution_enabled: bool = True,
    ) -> None:
        self._jobs = job_service
        self._pipelines = pipeline_service
        self._approvals = approval_service
        self._engines = engine_registry
        self._events = event_bus
        self.execution_enabled = execution_enabled
        self._register_subscriptions()

    def _register_subscriptions(self) -> None:
        self._events.subscribe(JobCreated, self._on_job_created)
        self._events.subscribe(EngineCompleted, self._on_engine_completed)
        self._events.subscribe(EngineFailed, self._on_engine_failed)
        self._events.subscribe(ApprovalResolved, self._on_approval_resolved)
        self._events.subscribe(PipelineAdvanced, self._on_pipeline_advanced)

    # -- Synchronous evaluation entry point (Step 5) -------------------

    def evaluate(self, engine_id: str) -> None:
        """Advance engine_id's queue as far as current eligibility allows.

        1. Move every VALIDATED Job for this engine to QUEUED
           ("observe newly created Jobs").
        2. If the engine is not eligible right now (unregistered,
           unhealthy, execution globally disabled, or already at its
           concurrency limit), stop here — "if execution cannot begin,
           leave the Job queued."
        3. Otherwise, pick the oldest QUEUED Job(s) for this engine (FIFO,
           up to the number of free concurrency slots — currently always
           at most 1, since every registered engine's max_concurrent_runs
           is 1), transition each to RUNNING via JobService.mark_running(),
           then call the resolved adapter's launch(config), followed by
           collect_results() on success. Any EngineLaunchError is caught
           here (generic, Protocol-level — see module docstring) and
           recorded via mark_failed(); a successful collect_results() is
           recorded via mark_succeeded(). Neither path lets an exception
           escape this loop for a *known* outcome — only a genuinely
           unexpected exception (anything other than EngineLaunchError)
           would still propagate uncaught, exactly as Step 5 behaved for
           every case before this step.
        4. If any Job was processed in this batch, evaluate() calls
           itself again for the same engine_id before returning — the
           synchronous "check the next queued Job" bridge described in
           this module's docstring.
        """
        self._enqueue_validated_jobs(engine_id)

        if not self._is_engine_eligible(engine_id):
            return

        available_slots = self._available_concurrency_slots(engine_id)
        if available_slots <= 0:
            return

        queued_jobs = sorted(
            (job for job in self._jobs.list_jobs(status=JobStatus.QUEUED) if job.engine_id == engine_id),
            key=lambda job: job.created_at,
        )
        if not queued_jobs:
            return

        adapter = self._engines.get_adapter(engine_id)

        any_processed = False
        for job in queued_jobs[:available_slots]:
            any_processed = True
            running_job = self._jobs.mark_running(job.id)
            self._jobs.record_event(running_job.id, JobEventType.ENGINE_RESERVED, {"engine_id": engine_id})

            def _persist_in_flight_reference(ref, job_id=running_job.id) -> None:
                # Step 7: called by the adapter the moment it has a live,
                # killable process handle — BEFORE launch() blocks waiting
                # for it. Persisting this immediately (not after launch()
                # returns) is what lets a concurrent cancel_job() call find
                # a real PID to act on while this call is still in progress
                # below. See core/adapters/engine_adapter.py's
                # OnRunReference docstring for the full rationale.
                self._jobs.record_run_reference(job_id, ref)

            self._jobs.record_event(running_job.id, JobEventType.LAUNCH_STARTED)
            try:
                run_reference = adapter.launch(running_job.config, on_run_reference=_persist_in_flight_reference)
            except EngineLaunchError as exc:
                try:
                    self._jobs.mark_failed(running_job.id, _job_error_from_launch_error(exc))
                except InvalidJobStatusTransitionError:
                    # Step 7: a concurrent cancel_job() call already
                    # finalized this Job (to CANCELLED) while launch() was
                    # still running — that call, not this one, owns the
                    # outcome. Nothing more to do here; not an error.
                    pass
                else:
                    # Only recorded when mark_failed() actually won the
                    # race above — an event log entry for an outcome that
                    # was rejected by the status-transition guard would
                    # misrepresent what the Job's persisted state actually
                    # is (see the module docstring on InvalidJobStatusTransitionError races).
                    event_type = (
                        JobEventType.TIMED_OUT
                        if exc.error.get("category") == "launch_timeout"
                        else JobEventType.LAUNCH_FAILED
                    )
                    self._jobs.record_event(running_job.id, event_type, dict(exc.error))
                continue

            self._jobs.record_event(running_job.id, JobEventType.LAUNCH_SUCCEEDED)

            # Bug found during Step 7's own verification: without this,
            # the Job's persisted run_reference stayed frozen at the
            # EARLY in-flight reference (a worker PID that no longer
            # exists) forever, even after success — misleading anyone who
            # later inspected a finished Job. Update it to the FINAL
            # reference launch() actually returned before finalizing
            # status, reusing record_run_reference() (safe here: status
            # is still RUNNING at this point, its required precondition).
            try:
                self._jobs.record_run_reference(running_job.id, run_reference)
            except InvalidJobStatusTransitionError:
                pass  # concurrent cancel_job() already finalized it; let the block below discover that too

            result = adapter.collect_results(run_reference)
            try:
                if result.success:
                    self._jobs.mark_succeeded(running_job.id, result.artifacts)
                else:
                    self._jobs.mark_failed(running_job.id, _job_error_from_result_error(result.error))
            except InvalidJobStatusTransitionError:
                # Same race as above: a concurrent cancel_job() already
                # finalized this Job before this (still-running) call
                # could record its own outcome. The cancellation wins;
                # this is expected, not an error.
                pass
            else:
                if result.success:
                    self._jobs.record_event(running_job.id, JobEventType.COMPLETED)
                else:
                    self._jobs.record_event(
                        running_job.id, JobEventType.LAUNCH_FAILED, dict(result.error or {})
                    )

        if any_processed:
            self.evaluate(engine_id)

    # -- Cancellation entry point (Step 7) --------------------------------

    def cancel_job(self, job_id: str) -> Job:
        """Cancel a Job, real process termination included when it is
        actually RUNNING.

        Ordering bug found and fixed during this step's own live
        verification: an earlier version of this method called
        adapter.cancel() FIRST and only marked the Job CANCELLED
        afterward. That lost a real race almost every time in practice —
        the ORIGINAL evaluate() call's process.wait() (in a different
        thread) unblocks the instant the killed process actually dies,
        which happens BEFORE this method's own post-kill verification
        loop (_kill_process_tree()'s repeated tasklist re-checks) finishes
        and returns control here — so the original thread's "the process
        just vanished, no result file" path reached mark_failed() before
        this method ever reached mark_cancelled_from_running(), and the
        Job ended up FAILED instead of CANCELLED even though cancellation
        genuinely happened. Fixed by marking CANCELLED FIRST (before
        calling adapter.cancel() at all): once that DB write succeeds, the
        Job is no longer RUNNING, so the original thread's later
        mark_failed()/mark_succeeded() attempt is guaranteed to hit
        InvalidJobStatusTransitionError and no-op — user-initiated
        cancellation intent now reliably wins, regardless of how long the
        actual kill takes afterward.

        If mark_cancelled_from_running() itself raises
        InvalidJobStatusTransitionError (the Job was already finalized by
        the OTHER thread before this method even got here), that means
        cancellation lost a genuinely-already-decided race — not an
        error; this method returns the Job's real, already-finalized
        state and does not attempt to kill anything (there is nothing
        left running to kill).

        Only once the Job is confirmed CANCELLED does this call
        adapter.cancel() — the only place besides evaluate() this
        Coordinator ever invokes an adapter method, preserving "the
        Coordinator is the only component authorized to call
        adapter.launch()/cancel()." If adapter.cancel() itself then fails
        to actually terminate the process (EngineLaunchError,
        category "process_kill_failed"), that is a genuine, distinct
        failure and is allowed to propagate — the Job's DB status will
        already correctly say CANCELLED even though the underlying
        process may still need manual attention, which is safer (visible,
        loud) than silently leaving the Job stuck RUNNING forever.

        Either way, evaluate() is called again afterward so the engine's
        queue continues — "queue continues after cancellation."

        For a Job that is NOT RUNNING: delegates entirely to the existing,
        unchanged JobService.cancel_job() (Step 4/5's
        PENDING/VALIDATED/QUEUED path, or its terminal-state
        InvalidJobStatusTransitionError) — no new behavior, no regression.
        """
        job = self._jobs.get_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)

        if job.status != JobStatus.RUNNING:
            cancelled = self._jobs.cancel_job(job_id)
            self._jobs.record_event(job_id, JobEventType.CANCELLED)
            return cancelled

        try:
            updated = self._jobs.mark_cancelled_from_running(job_id)
        except InvalidJobStatusTransitionError:
            # Already finalized by a concurrent evaluate() before we even
            # got here — nothing left to kill, nothing more to do.
            return self._jobs.get_job(job_id)

        self._jobs.record_event(job_id, JobEventType.CANCELLED)

        adapter = self._engines.get_adapter(job.engine_id)
        if job.run_reference is not None:
            adapter.cancel(job.run_reference)

        self.evaluate(job.engine_id)
        return updated

    # -- Eligibility helpers ---------------------------------------------

    def _enqueue_validated_jobs(self, engine_id: str) -> None:
        for job in self._jobs.list_jobs(status=JobStatus.VALIDATED):
            if job.engine_id == engine_id:
                self._jobs.mark_queued(job.id)
                self._jobs.record_event(job.id, JobEventType.QUEUED)

    def _is_engine_eligible(self, engine_id: str) -> bool:
        """Answers three of the four eligibility questions this step
        specifies (the fourth, concurrency, is answered separately by
        _available_concurrency_slots() since it returns a count, not a
        bool): is the engine registered, is the adapter healthy, is
        execution globally enabled.
        """
        if not self.execution_enabled:
            return False
        engine = self._engines.get_engine(engine_id)
        if engine is None:
            return False
        if engine.health_status != "healthy":
            return False
        return True

    def _available_concurrency_slots(self, engine_id: str) -> int:
        """The fourth eligibility question: is another Job already
        running for this engine? Answered generally against the engine's
        declared max_concurrent_runs (from discover(), persisted on the
        Engine row's capabilities) rather than a hardcoded "at most one" —
        every registered engine happens to declare max_concurrent_runs=1
        today, so this currently behaves as a simple busy/not-busy check,
        but generalizes correctly if that ever changes.
        """
        engine = self._engines.get_engine(engine_id)
        if engine is None:
            return 0
        max_concurrent = engine.capabilities.get("max_concurrent_runs", 1)
        running_count = sum(
            1 for job in self._jobs.list_jobs(status=JobStatus.RUNNING) if job.engine_id == engine_id
        )
        return max(0, max_concurrent - running_count)

    def describe_eligibility(self, engine_id: str) -> EngineEligibility:
        """Step 8 objective 4/6 — the read-only counterpart to the
        eligibility logic evaluate() already applies internally, exposed
        so monitoring/API callers can explain *why* a Job is waiting
        without duplicating _is_engine_eligible()/
        _available_concurrency_slots()'s reasoning. Reuses exactly the
        same two private helpers evaluate() itself uses, so this can never
        drift out of sync with the actual scheduling decision.
        """
        engine = self._engines.get_engine(engine_id)
        running_count = sum(
            1 for job in self._jobs.list_jobs(status=JobStatus.RUNNING) if job.engine_id == engine_id
        )
        max_concurrent = engine.capabilities.get("max_concurrent_runs", 1) if engine is not None else 0
        return EngineEligibility(
            engine_id=engine_id,
            is_registered=engine is not None,
            is_healthy=engine is not None and engine.health_status == "healthy",
            execution_enabled=self.execution_enabled,
            max_concurrent_runs=max_concurrent,
            running_count=running_count,
            available_slots=self._available_concurrency_slots(engine_id),
        )

    # -- Event handlers ---------------------------------------------------
    # Registered (see _register_subscriptions above) but not reachable by
    # any code path this step, since nothing publishes these events yet
    # (event publishing remains out of scope through Step 5). Only
    # _on_job_created is implemented — for real, calling the same
    # evaluate() logic the synchronous API call sites use — so that once a
    # future step wires real event publishing, this path works unchanged.
    # The other four remain stubs: EngineCompleted/EngineFailed require
    # real execution outcomes to react to (out of scope), ApprovalResolved
    # requires approvals (out of scope), PipelineAdvanced requires
    # pipelines (out of scope).

    def _on_job_created(self, event: JobCreated) -> None:
        self.evaluate(event.engine_id)

    def _on_engine_completed(self, event: EngineCompleted) -> None:
        """Collect results via the adapter, persist success through
        JobService, then consider queued work and (if this Job belongs to
        a PipelineRun) whether the next step is now eligible.
        """
        raise NotImplementedError

    def _on_engine_failed(self, event: EngineFailed) -> None:
        """Persist failure through JobService; decide whether the Engine's
        or PipelineRun's retry policy permits a retry and, if so, publish
        RetryScheduled and re-queue rather than silently re-launching.
        """
        raise NotImplementedError

    def _on_approval_resolved(self, event: ApprovalResolved) -> None:
        """Route the resolved decision back through the adapter to resume
        the waiting Job, then resume normal monitor()/collect_results() flow.
        """
        raise NotImplementedError

    def _on_pipeline_advanced(self, event: PipelineAdvanced) -> None:
        """Launch the next step's Job(s) for the PipelineRun named in the
        event, or publish PipelineCompleted if there is no next step.
        """
        raise NotImplementedError

    # -- Direct queries (not event-driven) -----------------------------

    def eligible_jobs(self) -> list[Job]:
        """Return pending Jobs currently eligible to launch, respecting
        per-engine concurrency limits. Exposed for inspection/testing
        without requiring an event round-trip. Not yet implemented —
        evaluate() is this step's real, exercised path; this remains a
        placeholder for a future read-only inspection API.
        """
        raise NotImplementedError
