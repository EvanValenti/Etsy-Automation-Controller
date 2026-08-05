"""FastAPI app — routers, WebSocket broadcaster, request/response schemas.

Design spec Section 5 (verbatim API surface):

    REST — commands and queries:
        POST   /jobs                    create + validate a job (does not launch)
        POST   /jobs/{id}/launch        launch a validated job
        GET    /jobs, /jobs/{id}        list/query jobs
        POST   /jobs/{id}/cancel        best-effort; 409 if engine lacks the capability
        GET    /jobs/{id}/results       normalized EngineResult (once terminal)

        POST   /pipelines/definitions   create a reusable pipeline definition
        POST   /pipelines/runs          start a PipelineRun from a definition
        GET    /pipelines/runs/{id}     status of a pipeline run + its Jobs

        GET    /approvals?status=pending
        POST   /approvals/{id}/resolve  submit a decision -> resumes the Job

        GET    /engines                 registry: capabilities, health, config schema

    WebSocket — live event delivery only:
        A single generic /events stream publishing typed domain events
        straight from the Event Bus, client-side filterable. No
        engine-specific WebSocket endpoints.

Every handler below is intentionally a thin stub: request/response mapping
only, delegating to core/services/ for all actual behavior, per the "thin
delivery layer" principle in Section 1.

Step 1 foundation pass (this revision): every route now obtains its
Application Service through the api/dependencies.py composition root via
FastAPI's Depends(), then calls a real method on that service — proving
the DI path actually reaches core/services/ — rather than raising
NotImplementedError directly in the route body. The service methods
themselves are unchanged and still raise NotImplementedError internally,
so every route below still fails the same way it did before, just one
layer deeper (proving the composition root is real rather than that the
route handler happens to raise the same exception coincidentally).

/health is NOT part of the design spec's API surface above — grep of the
spec document confirms "health" only appears as the Engine.health_status
domain field and in GET /engines' description, never as its own endpoint.
It is added here as a minor, additive, non-structural liveness check
because runnability verification requires one; it intentionally does no
DB/service work, only confirming the process is up.

Step 2 (Engine Registry) addition: the lifespan handler now also registers
every known EngineAdapter at startup by calling ONLY its discover()
method through EngineRegistryService.register() and persisting the
result — no launch/monitor/cancel/collect_results call happens anywhere
in this module. GET /engines was already wired to
EngineRegistryService.list_engines() in Step 1; that method is now real
(reads the engines table) rather than raising NotImplementedError, so
this route's behavior changes with no code change to the route itself.

Step 3 (Adapter Factory) change: this module no longer imports any
concrete adapter class directly (VideoGeneratorAdapter etc. are gone from
this file's imports). `_KNOWN_ADAPTER_BINDINGS` is now pure data — dotted
"module.ClassName" strings — and the same DynamicAdapterFactory that
EngineRegistryService.get_adapter() will use for every future call is
what constructs the one-off instance registered at startup too. This
means adapter construction has exactly one code path in the whole
application, not a special-cased direct-import one for startup and a
reflective one for everything else.

Step 4 (Job domain) change: POST /jobs, GET /jobs, GET /jobs/{id}, and
DELETE /jobs/{id} are now real — real request schema (api/schemas.py),
real JobService methods, real HTTP status codes via the exception
handlers registered just below. POST /jobs/{id}/cancel calls the real
JobService.cancel_job() (a genuine, execution-free status transition for
a Job that hasn't launched yet). GET /jobs/{id}/results is UNCHANGED from
Step 1 — still a placeholder returning the raw Job, not a real
EngineResult; real results collection remains out of scope.

Step 5 (Execution Coordinator) change — read this before touching either
route below: POST /jobs now calls coordinator.evaluate(engine_id) right
after JobService.create_job() persists the new Job, then returns its
up-to-date state (which may already be QUEUED, or even have triggered a
launch attempt that raised — see below). POST /jobs/{id}/launch was
REWRITTEN, not left as "call JobService.mark_running() directly": once
mark_running() gained a real QUEUED-only guard in Step 5, leaving this
route calling it directly would have let a client flip a Job straight to
RUNNING without adapter.launch() ever being attempted — silently
violating the design spec's own principle that the Execution Coordinator
is "the only component in the Core authorized to call adapter.launch()."
This was caught while implementing the Coordinator, not before, and is
disclosed in full in this step's report; the fix was to route this
endpoint through coordinator.evaluate(job.engine_id) exactly like
POST /jobs does, which advances that job's ENTIRE engine queue (FIFO, not
necessarily this specific job_id) rather than force-launching one job out
of order. If evaluate() ends up attempting adapter.launch() for whichever
Job it picks and that raises, the exception propagates out of this route
unchanged — "the request still terminates with the current
NotImplementedError," exactly as instructed, but now genuinely funneled
through the approved architecture instead of bypassing it.

Step 9 (API completeness pass for the frontend) — audited the whole route
surface against the planned UI (dashboard, Job detail, engine detail,
history, activity timeline) with Steps 1-8 verified intact and unchanged.
Three real gaps found and closed, all additive — no existing route,
model, or behavior changed:
  1. GET /jobs had no way to scope to one engine (engine detail view's
     "Jobs for this engine" list) — added an optional ?engine_id= filter
     alongside the existing ?status= one.
  2. No endpoint surfaced JobEvents *across* jobs/engines — every
     Step 8 monitoring route was scoped to one job or one engine. Added
     GET /activity for the dashboard's global timeline, reusing the
     existing JobEvent model (see api schemas note below).
  3. No CORS configured — a browser-based Web UI on its own dev-server
     origin could not have called this API at all. Added permissive
     CORSMiddleware, consistent with the design spec's "V1 is
     single-operator" stance (Section 8).
Approvals/Pipelines routes are UNCHANGED and still raise NotImplementedError
(-> 500, uncaught) — real approval/pipeline persistence remains explicitly
out of scope through V1 per the design spec; Step 9's brief was completing
the REST surface around what Steps 1-8 already implemented for real
(Job/Engine/Event/Metrics/Health), not building new orchestration
capability.

V1 polish change (Dashboard lifetime statistics): GET /metrics/lifetime is
new, and DELETE /jobs/{id} now banks the deleted Job's completion into the
permanent completed-workflow ledger before removing the row. Time Saved
and Lifetime Production used to be recomputed in the browser from whatever
succeeded Job rows still existed, so deleting old job history reduced
them -- reporting retained records rather than lifetime accomplishment.
Deletion itself is otherwise UNCHANGED (same guards, same Recycle Bin
behavior via infra/cleanup.py); it now only affects job history
visibility. See infra/lifetime_metrics.py for the ledger's design.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session

from api.dependencies import (
    get_approval_service,
    get_engine_registry_service,
    get_execution_coordinator,
    get_job_service,
    get_lifetime_metrics_ledger,
    get_monitoring_service,
    get_pipeline_service,
)
from api.schemas import CreateJobRequest
from api.image_generator_routes import router as image_generator_router
from api.listing_workspace_routes import router as listing_workspace_router
from api.mockup_generator_routes import router as mockup_generator_router
from api.video_generator_routes import router as video_generator_router
from core.domain.job import JobStatus
from core.execution.coordinator import ExecutionCoordinator
from core.services.approval_service import ApprovalService
from core.services.engine_registry_service import EngineRegistryService, UnknownEngineError
from core.services.job_service import (
    InvalidJobStatusTransitionError,
    JobNotFoundError,
    JobService,
    JobValidationError,
)
from core.services.monitoring_service import MonitoringService
from core.services.pipeline_service import PipelineService
from infra.adapters.adapter_factory import DynamicAdapterFactory
from infra.adapters.discovery_utils import resolve_engine_repo_root
from infra.cleanup import CleanupError, delete_job as cleanup_delete_job
from infra.db.session import create_db_and_tables, engine as db_engine
from infra.db.sqlite_repositories import SqliteEngineRepository
from infra.lifetime_metrics import LifetimeMetricsLedger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("automation_controller.startup")

# The port the Web UI calls. Must stay in agreement with web/.env.example,
# web/README.md, .claude/launch.json, and FALLBACK_API_BASE_URL in
# web/src/api/diagnostics.ts.
#
# This constant exists because the mismatch it guards against was a real,
# repeated failure: `uvicorn api.main:app` (the obvious short command)
# binds uvicorn's OWN default of 8000, the Web UI keeps calling 8123, and
# the backend's startup output — "Uvicorn running on http://127.0.0.1:8000",
# no traceback, no crash — looks completely healthy. The frontend reports
# "Backend not responding" while the terminal says everything is fine,
# which reads as a stability problem rather than a configuration one. README.md
# documented the right command and warned about this exact trap, and it
# still happened, so the startup path now says it out loud instead.
CANONICAL_API_PORT = 8123
CANONICAL_API_HOST = "127.0.0.1"
_PORT_ENV_VAR = "AUTOMATION_CONTROLLER_API_PORT"


def describe_port_mismatch(argv: list[str]) -> str | None:
    """A warning to log when this process is about to serve on a port the
    Web UI will not call, or None when the invocation is fine.

    Reads argv rather than the live socket because a FastAPI app has no
    access to the uvicorn Server that hosts it — and argv is enough to
    identify the two invocations that actually break: the uvicorn CLI with
    no --port (silently 8000), and the uvicorn CLI with an explicit --port
    that disagrees with CANONICAL_API_PORT. Purely diagnostic: it only ever
    produces a string, never changes how the app serves.
    """
    # Keyed on the ASGI target spec ("api.main:app", colon included) rather
    # than the bare module name, because `python -m api.main` also contains
    # "api.main" and pins the port itself -- matching it here would fire a
    # false PORT MISMATCH warning on the recommended start command.
    argv0 = Path(argv[0]).name.lower() if argv else ""
    launched_via_uvicorn_cli = argv0.startswith("uvicorn") or any("api.main:" in part for part in argv[1:])
    if not launched_via_uvicorn_cli:
        return None  # e.g. `python -m api.main`, which pins the port itself

    port: int | None = None
    for index, part in enumerate(argv):
        if part == "--port" and index + 1 < len(argv):
            try:
                port = int(argv[index + 1])
            except ValueError:
                port = None
            break
        if part.startswith("--port="):
            try:
                port = int(part.split("=", 1)[1])
            except ValueError:
                port = None
            break

    if port == CANONICAL_API_PORT:
        return None

    # ASCII only, deliberately: this text's whole job is to be readable in a
    # Windows console, where the default cp1252 codepage turns an em dash
    # into a replacement character.
    if port is None:
        return (
            f"Backend is starting on port 8000 (uvicorn's own default -- no --port given), but the Web "
            f"UI calls port {CANONICAL_API_PORT}. The UI will report the backend as not responding even "
            f"though this process is healthy. Fix: start the API with `python -m api.main` instead (pins "
            f"port {CANONICAL_API_PORT} automatically; add --reload for auto-restart-on-change), or add "
            f"--port {CANONICAL_API_PORT} to this uvicorn command."
        )
    return (
        f"Backend is starting on port {port}, but the Web UI calls port {CANONICAL_API_PORT}. The UI will "
        f"report the backend as not responding. Fix: either start with `python -m api.main` (pins port "
        f"{CANONICAL_API_PORT} automatically; add --reload for auto-restart-on-change) or set "
        f"VITE_API_BASE_URL in web/.env.local to port {port} and restart the Vite dev server."
    )

# (engine_id, display_name, adapter_binding) for every engine known to the
# Controller. Adding a fourth engine (per the design spec's "with more
# engines to come") means adding one entry here — nothing else in this
# module changes. The binding string is the sole description of "which
# class implements this engine" anywhere in this module now; the actual
# class is only ever touched reflectively, via DynamicAdapterFactory.
_KNOWN_ADAPTER_BINDINGS: list[tuple[str, str, str]] = [
    ("etsy-video-generator", "Etsy Video Generator", "infra.adapters.video_generator.adapter.VideoGeneratorAdapter"),
    (
        "etsy-ai-image-generator",
        "Etsy AI Image Generator",
        "infra.adapters.image_generator.adapter.ImageGeneratorAdapter",
    ),
    (
        "etsy-mockup-generator",
        "Etsy Mockup Generator",
        "infra.adapters.mockup_generator.adapter.MockupGeneratorAdapter",
    ),
]


def _register_engines_at_startup() -> None:
    """Construct each known adapter via the AdapterFactory, run discover()
    through it via EngineRegistryService.register(), and persist the
    resulting Engine row. This is the Step 2 "Engine registration during
    application startup" objective, updated in Step 3 to construct
    adapters through the same factory get_adapter() now uses, rather than
    importing adapter classes directly.

    Scope boundary: this function calls exactly one adapter method —
    discover() — for each engine, exactly once, at startup. No
    validate/launch/monitor/cancel/collect_results call happens here or
    anywhere else in this pass.
    """
    factory = DynamicAdapterFactory()
    with Session(db_engine) as session:
        registry = EngineRegistryService(SqliteEngineRepository(session), factory)
        for engine_id, display_name, binding in _KNOWN_ADAPTER_BINDINGS:
            logger.info("Registering engine '%s' via factory-constructed %s.discover()", engine_id, binding)
            adapter = factory.create(binding)
            registered = registry.register(engine_id, display_name, adapter)
            capabilities = registered.capabilities
            logger.info(
                "Registered '%s': health_status=%s implementation_status=%s "
                "supports_launch=%s supports_monitoring=%s",
                engine_id,
                registered.health_status,
                capabilities.get("implementation_status"),
                capabilities.get("supports_launch"),
                capabilities.get("supports_monitoring"),
            )
            for note in capabilities.get("notes", []):
                logger.info("  [%s] note: %s", engine_id, note)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Schema/table creation, then Engine Registry population — both run
    once at process startup. No query logic beyond that, no orchestration,
    no data seeding for Job/Pipeline/Approval tables.

    Step 5 addition: logs that the Execution Coordinator's dependencies
    (JobService, EngineRegistryService, etc.) are wired and constructible
    via api/dependencies.py's get_execution_coordinator() — "Coordinator
    registration" for this step. Deliberately does NOT call
    coordinator.evaluate() here: any Job left QUEUED from a previous run
    is expected to persist across a restart unchanged (verified in this
    step's report), not be automatically re-evaluated/launched the
    instant the process boots. Auto-resuming queued work on startup is a
    real orchestration behavior of its own and wasn't asked for this
    step — evaluate() is only ever triggered by an explicit job-creation
    or launch request through Step 5.
    """
    create_db_and_tables()

    # Said before engine registration so it isn't buried under it: this is
    # the line that turns "the backend keeps going offline" into "the
    # backend is on the wrong port," at the moment and place the operator
    # is already looking.
    port_warning = describe_port_mismatch(sys.argv)
    if port_warning:
        logger.warning("PORT MISMATCH: %s", port_warning)
    else:
        logger.info("Web UI expects the API on http://%s:%s", CANONICAL_API_HOST, CANONICAL_API_PORT)

    _register_engines_at_startup()
    logger.info(
        "Execution Coordinator wiring ready (JobService/EngineRegistryService available via "
        "api/dependencies.py::get_execution_coordinator()); queue evaluation is synchronous, "
        "triggered by POST /jobs and POST /jobs/{id}/launch this step — no automatic startup "
        "re-evaluation of previously-queued Jobs."
    )
    yield


app = FastAPI(title="Automation Controller", version="0.1.0", lifespan=lifespan)

# Step 9: CORS. Design spec Section 8 — "V1 is single-operator" — and the
# Web UI (web/) is a separate client talking HTTP/WS only, so it runs on
# its own dev-server origin/port and needs the browser's CORS check to
# pass to call this API at all. Permissive (any origin) is deliberate for
# this same single-operator-local-tool reason, not an oversight — there is
# no multi-tenant data to isolate between origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Step 12: etsy-video-generator's purpose-built operator workflow routes
# (multipart upload/staging, preset metadata, output video/folder access).
# Scoped entirely to that one engine — see api/video_generator_routes.py's
# module docstring. Included after CORS so browser requests to these
# routes get the same permissive-origin treatment as everything else.
app.include_router(video_generator_router)

# Step 13: etsy-mockup-generator's purpose-built operator workflow routes
# (ZIP upload/staging, background discovery, preview/result image serving,
# output folder access). Scoped entirely to that one engine — see
# api/mockup_generator_routes.py's module docstring.
app.include_router(mockup_generator_router)

# Step 14: etsy-ai-image-generator's purpose-built operator workflow
# routes (engine-side job creation/listing/status, one-stage-per-Job
# pipeline advancement, output folder access). Scoped entirely to that
# one engine — see api/image_generator_routes.py's module docstring.
app.include_router(image_generator_router)

# Step 15: Listing Workspace -- a Controller-owned feature, not an engine.
# Assembles approved assets from all three engines into one temporary,
# human-browsable folder per listing. See
# infra/listing_workspace.py's module docstring for the full design.
app.include_router(listing_workspace_router)


# -- Exception handlers (Step 4) -----------------------------------------
# Thin, mechanical translation of known Core/service exceptions into HTTP
# status codes. No business logic lives here — this is exactly the "request/
# response mapping" the delivery layer is supposed to own per Section 1.

@app.exception_handler(UnknownEngineError)
async def handle_unknown_engine(request: Request, exc: UnknownEngineError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc), "engine_id": exc.engine_id})


@app.exception_handler(JobValidationError)
async def handle_job_validation_error(request: Request, exc: JobValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "engine_id": exc.engine_id, "errors": exc.errors},
    )


@app.exception_handler(JobNotFoundError)
async def handle_job_not_found(request: Request, exc: JobNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc), "job_id": exc.job_id})


@app.exception_handler(InvalidJobStatusTransitionError)
async def handle_invalid_job_status_transition(
    request: Request, exc: InvalidJobStatusTransitionError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "job_id": exc.job_id, "current_status": exc.current_status.value},
    )


@app.exception_handler(CleanupError)
async def handle_cleanup_error(request: Request, exc: CleanupError) -> JSONResponse:
    """Covers both "nothing to delete" and "still active" -- see
    infra/cleanup.py. A single 409 is enough for the operator-facing
    message this produces; callers don't need to branch on which."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# -- Health (additive, not in the design spec — see module docstring) ------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "automation-controller", "version": "0.1.0"}


# -- Jobs --------------------------------------------------------------

@app.post("/jobs", status_code=201)
def create_job(
    payload: CreateJobRequest,
    service: JobService = Depends(get_job_service),
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
):
    """Create + validate a job, then immediately ask the Execution
    Coordinator to evaluate this engine's queue.

    Validates the engine exists and the config is accepted by that
    engine's resolved adapter (adapter.validate()) before persisting.
    Raises UnknownEngineError (-> 404) or JobValidationError (-> 422) —
    no Job row is created in either failure case.

    If the newly-created Job (or an older queued one for the same
    engine) becomes eligible to run, coordinator.evaluate() will call
    adapter.launch() for it — which currently always raises, so this
    endpoint can legitimately return 500 (NotImplementedError) for a Job
    that WAS successfully created and persisted. That is intentional and
    documented behavior for this step (see this module's docstring), not
    a bug: it is the most direct, honest way to observe "the request
    still terminates with the current NotImplementedError" through real
    HTTP traffic. If the engine is already busy, the new Job is simply
    left QUEUED and this call returns normally.
    """
    job = service.create_job(
        engine_id=payload.engine_id,
        config=payload.config,
        pipeline_run_id=payload.pipeline_run_id,
    )
    coordinator.evaluate(job.engine_id)
    return service.get_job(job.id)


@app.post("/jobs/{job_id}/launch")
def launch_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
):
    """Explicitly nudge this Job's engine queue to be (re-)evaluated.

    Step 5 rewrite — see this module's docstring for why: this no longer
    calls JobService.mark_running() directly. It resolves job_id only to
    find which engine's queue to evaluate, then calls
    coordinator.evaluate(job.engine_id) exactly like POST /jobs does.
    This means the Job that actually advances to RUNNING (and has
    launch() attempted against it) may not be job_id itself — it is
    whichever QUEUED Job is oldest for that engine (FIFO) — job_id merely
    identifies which engine's queue to nudge. Returns the current state of
    job_id itself afterward, which may be unchanged (still QUEUED, if a
    different older Job was the one actually evaluated) or may reflect
    this exact Job having moved to RUNNING. If a launch attempt happened
    and raised, that exception propagates out of this route as a 500,
    regardless of which specific Job triggered it.
    """
    job = service.get_job(job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    coordinator.evaluate(job.engine_id)
    return service.get_job(job_id)


@app.get("/jobs")
def list_jobs(
    status: str | None = None,
    engine_id: str | None = None,
    service: JobService = Depends(get_job_service),
):
    """Optionally filtered by ?status=<one of JobStatus's values> and/or
    ?engine_id=<engine id>. An unrecognized status string is a client
    error (422), not a 500. Step 9 addition: engine_id, needed for the
    planned engine detail view's "Jobs for this engine" list — unlike
    status, engine_id is not validated against a fixed enum (engine ids
    are open-ended, defined by whatever adapters are registered), so an
    unknown engine_id simply yields an empty list, matching how GET
    /engines/{engine_id}/health already treats an unknown engine_id as
    non-fatal rather than a 404.
    """
    status_filter: JobStatus | None = None
    if status is not None:
        try:
            status_filter = JobStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid status filter: {status!r}")
    return service.list_jobs(status=status_filter, engine_id=engine_id)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, service: JobService = Depends(get_job_service)):
    job = service.get_job(job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    return job


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
    ledger: LifetimeMetricsLedger = Depends(get_lifetime_metrics_ledger),
):
    """Prompt 4 (Cleanup & Job Lifecycle) rewrite: routes through
    infra/cleanup.py's delete_job() instead of calling
    JobService.delete_job() directly. That still removes the Job row, but
    first (a) refuses outright if the Job is still active -- pending,
    validated, queued, running, or waiting on approval -- raising
    CleanupError (-> 409) rather than silently deleting live work, and
    (b) recycles the Job's own generated output (never a shared/template/
    background path) and any Controller staging directory still owned by
    it to the Windows Recycle Bin, never a permanent delete. Raises
    JobNotFoundError (-> 404) if the Job doesn't exist at all. Distinct
    from cancelling (see POST /jobs/{id}/cancel below), which changes
    status rather than removing the row.

    V1 polish addition: the Job's completion (if it succeeded) is banked
    into the permanent lifetime-metrics ledger BEFORE the row is removed,
    so deleting job history never reduces Time Saved or Lifetime
    Production -- deleting old jobs is housekeeping, not undoing work that
    was completed. Recording first also means the one case GET
    /metrics/lifetime's own sync can't catch -- a job completed and
    deleted without the Dashboard ever being open -- still counts. See
    infra/lifetime_metrics.py.
    """
    job = service.get_job(job_id)
    if job is not None:
        ledger.record_completed_workflows([job])
    cleanup_delete_job(service, job_id)
    return None


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, coordinator: ExecutionCoordinator = Depends(get_execution_coordinator)):
    """Cancel a Job. Step 7 rewrite: routes through
    coordinator.cancel_job(), not JobService.cancel_job() directly — for a
    RUNNING Job this now performs REAL process-tree termination via the
    resolved adapter's cancel() (see infra/adapters/video_generator/adapter.py);
    for a PENDING/VALIDATED/QUEUED Job it delegates to the existing,
    unchanged JobService.cancel_job() path (Step 4/5, no regression); for
    an already-terminal Job it raises InvalidJobStatusTransitionError
    (-> 409), same as before — "cancellation of completed Jobs must fail
    gracefully."
    """
    return coordinator.cancel_job(job_id)


@app.get("/jobs/{job_id}/results")
def get_job_results(job_id: str, service: JobService = Depends(get_job_service)):
    """Normalized EngineResult, once the Job is terminal. Real results
    collection is EngineAdapter.collect_results() territory, out of scope
    for this pass — this route currently proves the DI path reaches
    JobService.get_job() as the nearest real method available.
    """
    return service.get_job(job_id)


# -- Job monitoring (Step 8) ------------------------------------------------
# Read-only — every handler below delegates to MonitoringService, which
# never writes to the database (see core/services/monitoring_service.py's
# module docstring). 404s for an unknown job_id come from JobNotFoundError,
# raised inside MonitoringService/JobService itself and translated by the
# existing handle_job_not_found() handler above — no new exception handling
# needed for this step.

@app.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, service: MonitoringService = Depends(get_monitoring_service)):
    """The Job's full, ordered event timeline — Step 8 objective 1."""
    return service.get_job_timeline(job_id)


@app.get("/jobs/{job_id}/progress")
def get_job_progress(job_id: str, service: MonitoringService = Depends(get_monitoring_service)):
    """Coarse phase + (never-fabricated) percentage — Step 8 objective 2."""
    return service.get_job_progress(job_id)


@app.get("/jobs/{job_id}/metrics")
def get_job_metrics(job_id: str, service: MonitoringService = Depends(get_monitoring_service)):
    """Queue wait / launch duration / total execution time, each derived
    from the event log — Step 8 objective 5.
    """
    return service.get_job_metrics(job_id)


# -- Global activity (Step 9) ------------------------------------------------

@app.get("/activity")
def get_activity(limit: int = 50, service: MonitoringService = Depends(get_monitoring_service)):
    """Recent JobEvents across every job/engine, newest first — the
    dashboard's activity timeline. The one Step 8 gap found while
    auditing the API surface against the planned UI: /jobs/{id}/events
    and get_engine_metrics() both existed already, but nothing surfaced
    events *across* jobs/engines for a landing-page feed. Reuses the same
    JobEvent model returned by /jobs/{id}/events — no parallel
    "ActivityItem" type introduced.
    """
    return service.get_recent_activity(limit)


# -- Lifetime metrics (V1 polish) --------------------------------------------

@app.get("/metrics/lifetime")
def get_lifetime_metrics(
    service: JobService = Depends(get_job_service),
    ledger: LifetimeMetricsLedger = Depends(get_lifetime_metrics_ledger),
) -> dict[str, int]:
    """The Dashboard's headline accomplishment numbers -- cumulative, and
    unaffected by deleting job history.

    Syncs first, then reads: every currently-succeeded Job is recorded
    into the permanent ledger (idempotent by workflow -- see
    infra/lifetime_metrics.py), then the totals are read back from the
    ledger rather than from the Job rows. That ordering is the whole
    mechanism: a completion that is still visible gets banked, and one
    whose Job row is long gone still counts.
    """
    ledger.record_completed_workflows(service.list_jobs(status=JobStatus.SUCCEEDED))
    return ledger.totals()


# -- Pipelines -----------------------------------------------------------

@app.post("/pipelines/definitions")
def create_pipeline_definition(service: PipelineService = Depends(get_pipeline_service)):
    return service.create_definition(name="", steps=[])


@app.post("/pipelines/runs")
def start_pipeline_run(service: PipelineService = Depends(get_pipeline_service)):
    return service.start_run(definition_id="")


@app.get("/pipelines/runs/{run_id}")
def get_pipeline_run(run_id: str, service: PipelineService = Depends(get_pipeline_service)):
    """Status of a pipeline run + its Jobs."""
    return service.get_run(run_id)


# -- Approvals -------------------------------------------------------------

@app.get("/approvals")
def list_approvals(status: str | None = None, service: ApprovalService = Depends(get_approval_service)):
    return service.list_pending()


@app.post("/approvals/{approval_id}/resolve")
def resolve_approval(approval_id: str, service: ApprovalService = Depends(get_approval_service)):
    """Submit a decision -> resumes the Job. Resuming the Job through the
    adapter is Execution Coordinator territory, out of scope for this
    pass — this route proves the DI path reaches ApprovalService.resolve().
    """
    return service.resolve(approval_id, decision={})


# -- Engines -----------------------------------------------------------

@app.get("/engines")
def list_engines(service: EngineRegistryService = Depends(get_engine_registry_service)):
    """Registry: capabilities, health, config schema."""
    return service.list_engines()


# -- Engine monitoring (Step 8) ---------------------------------------------

@app.get("/engines/{engine_id}/health")
def get_engine_health(engine_id: str, service: MonitoringService = Depends(get_monitoring_service)):
    """Engine health state, current Job, queue length, last success/failure
    — Step 8 objective 3. Unlike the Job routes above, an unknown engine_id
    is not an error here: EngineHealth.state simply reports OFFLINE (see
    MonitoringService.get_engine_health()), matching how GET /engines
    itself never 404s on an engine_id filter either.
    """
    return service.get_engine_health(engine_id)


@app.get("/engines/{engine_id}/queue")
def get_engine_queue(engine_id: str, service: MonitoringService = Depends(get_monitoring_service)):
    """Every QUEUED Job for this engine plus *why* it's waiting — Step 8
    objective 4.
    """
    return service.get_engine_queue(engine_id)


@app.get("/engines/{engine_id}/metrics")
def get_engine_metrics(engine_id: str, service: MonitoringService = Depends(get_monitoring_service)):
    """Success/failure/timeout/cancel counts for this engine — Step 8
    objective 5.
    """
    return service.get_engine_metrics(engine_id)


# Where each engine actually writes its finished work, relative to that
# engine's own sibling repo root. A hardcoded, closed map on purpose: this
# route opens a real Explorer window, so the destination must never be
# derived from anything a client sends. An engine absent from this map has
# no known output root and the route 404s rather than guessing.
_ENGINE_OUTPUT_ROOTS: dict[str, tuple[str, str]] = {
    "etsy-ai-image-generator": ("Etsy-AI-Image-Generator", "jobs"),
    "etsy-mockup-generator": ("etsy-mockup-generator", "output"),
    "etsy-video-generator": ("etsy-video-generator", "output"),
}


@app.post("/engines/{engine_id}/open-output-folder")
def open_engine_output_folder(engine_id: str) -> dict[str, str]:
    """Opens Explorer at the folder where this engine collects its
    generated work, so an operator on an engine page can inspect results
    without first drilling into a specific job.

    Deliberately engine-scoped, not job-scoped: the existing per-job
    open-output-folder routes (video/mockup/image generator routers) still
    own "this job's output" and are unchanged. This is a read-only
    convenience action — it opens a folder and returns its path. It reads
    no Job state, touches no workflow, and cannot be pointed anywhere
    outside _ENGINE_OUTPUT_ROOTS.
    """
    entry = _ENGINE_OUTPUT_ROOTS.get(engine_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"No known output folder for engine {engine_id!r}.")

    repo_dir_name, output_subdir = entry
    repo_root = resolve_engine_repo_root(repo_dir_name)
    if repo_root is None:
        raise HTTPException(status_code=409, detail=f"Engine repository {repo_dir_name!r} is not on disk.")

    folder = (repo_root / output_subdir).resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail=f"This engine has no output folder yet ({folder}).")

    os.startfile(str(folder))  # noqa: S606 — local desktop app, user-triggered, path from a closed map
    return {"opened": str(folder)}


# -- Live events (WebSocket) --------------------------------------------

@app.websocket("/events")
async def events_stream(websocket: WebSocket):
    """A single generic stream publishing typed domain events straight
    from the Event Bus, client-side filterable. No engine-specific
    WebSocket endpoints — REST remains responsible for all commands/
    queries; this endpoint is responsible only for live event delivery.

    Not wired to a service (there is no service call here to prove DI
    against — this endpoint's eventual job is to relay EventBus messages,
    not call an Application Service) and not verified via curl in this
    pass, since a plain HTTP client cannot perform the WebSocket
    handshake; left unchanged in behavior from the previous version.
    """
    raise NotImplementedError


# -- Entrypoint --------------------------------------------------------------

def main() -> None:
    """Run the API on the port the Web UI actually calls.

    `python -m api.main` exists so there is a start command that CANNOT
    bind the wrong port -- the whole failure mode being guarded here is
    that `uvicorn api.main:app` looks correct, starts cleanly, and serves
    on 8000 where nothing is listening for it. The port stays overridable
    via AUTOMATION_CONTROLLER_API_PORT for anyone who genuinely needs a
    different one (they must then update web/.env.local to match).

    Supports `--reload` (`python -m api.main --reload`) so there is no
    remaining reason to drop down to a raw `uvicorn api.main:app --reload`
    just to get auto-restart-on-change during development -- that bare
    command was the other invocation this port guard exists for. uvicorn's
    reloader requires the app be passed as an import string (it re-imports
    fresh code in a subprocess after each change), not the live object, so
    that's the one thing that differs between the two modes below.
    """
    import uvicorn

    raw_port = os.environ.get(_PORT_ENV_VAR)
    try:
        port = int(raw_port) if raw_port else CANONICAL_API_PORT
    except ValueError:
        logger.warning("%s=%r is not a number -- using %s", _PORT_ENV_VAR, raw_port, CANONICAL_API_PORT)
        port = CANONICAL_API_PORT

    # An explicit override is allowed, but it is still the same silent
    # mismatch if web/.env.local wasn't updated with it -- so it warns too.
    if port != CANONICAL_API_PORT:
        logger.warning(
            "PORT MISMATCH: %s set the port to %s, but the Web UI calls port %s. Update "
            "VITE_API_BASE_URL in web/.env.local to match and restart the Vite dev server.",
            _PORT_ENV_VAR,
            port,
            CANONICAL_API_PORT,
        )

    reload = "--reload" in sys.argv[1:]
    uvicorn.run("api.main:app" if reload else app, host=CANONICAL_API_HOST, port=port, reload=reload)


if __name__ == "__main__":
    main()
