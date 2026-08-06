# Automation Controller

Orchestration layer coordinating independent automation engines
(`etsy-mockup-generator`, `Etsy-AI-Image-Generator`, `etsy-video-generator`,
with more engines to come). This project is a sibling of those engine
repos, never nested inside one — it is the coordination/monitoring layer
for the ecosystem, not another single-purpose automation tool.

## Purpose

Each automation engine (image generation, mockup generation, video
generation, ...) is its own independently developed, independently
runnable repository with its own logic, dependencies, and runtime state.
The Controller does not reimplement or contain that logic. Instead it:

- **discovers** which engines are present and what they can currently do
  (`EngineAdapter.discover()`),
- **launches and monitors** engine work through a uniform adapter
  interface, without ever importing an engine's own code,
- gives operators a single **dashboard** (the Web UI) to see job status,
  approvals, and engine health across every engine at once, instead of
  switching between separate tools per engine,
- and is designed to grow to more engines over time by adding one
  adapter binding per engine — the Core's architecture does not change.

**Authoritative architecture:** [`docs/superpowers/specs/2026-07-21-automation-controller-v1-design.md`](docs/superpowers/specs/2026-07-21-automation-controller-v1-design.md)
(approved, pre-implementation as of 2026-07-21).

**Background research the design is built on** (`docs/architecture/`):
[`CONTROLLER-V1-ARCHITECTURE-HANDBOOK.md`](docs/architecture/CONTROLLER-V1-ARCHITECTURE-HANDBOOK.md)
(per-engine deep architecture extraction, cited to file and line, for each
of the three sibling engine repos) and
[`CONTROLLER-V1-ARCHITECTURE-INSPECTION.md`](docs/architecture/CONTROLLER-V1-ARCHITECTURE-INSPECTION.md)
(cross-repo integration-readiness assessment). Both are architectural
extraction, not design opinion — they exist so the Controller's design is
grounded in what the engine repos actually do, not assumptions about them.

## Architecture Overview

The backend follows a hexagonal / ports-and-adapters layout:

- **`core/`** — framework-independent domain and orchestration logic. Zero
  imports from `api/` or `infra/`. This is where Job, Pipeline, Approval,
  and Engine are defined, along with the application services that
  operate on them and the `EngineAdapter` protocol every engine adapter
  must satisfy.
- **`infra/`** — concrete implementations `core/` depends on only through
  interfaces: SQLite-backed repositories (`infra/db/`), the three
  concrete engine adapters (`infra/adapters/<engine>/`), and storage/
  staging helpers for uploads and generated workspaces (`infra/storage/`,
  `infra/listing_workspace.py`).
- **`api/`** — the FastAPI delivery layer. Routes, request/response
  schemas, dependency injection wiring, and process startup
  (`api/main.py`). This is the only layer that knows it's running over
  HTTP.
- **`web/`** — the Web UI, a separate React/TypeScript client. It talks to
  the Controller exclusively over REST/WebSocket, the same way any future
  client (CLI, mobile, a second dashboard) would — it has no special
  access to `core/`/`infra/` internals.

## Orchestration Philosophy

From the design spec, the rules the Controller is built around:

1. The Controller orchestrates; engines execute. No engine business logic
   is ever reimplemented here.
2. Engines remain the source of truth for their own state — the Core
   stores references (`EngineRunReference`), never a copy of engine
   business state.
3. `core/` never knows *how* an adapter talks to its engine — only the
   `EngineAdapter` interface.
4. Clients (Web UI, future CLI/mobile/API) are peers, not privileged —
   all talk to the Controller through REST/WebSocket only, never by
   importing `core/`/`infra/` internals.
5. Don't overbuild V1 — define boundaries and interfaces now, implement
   the simplest correct thing behind them.

See the design spec's Section 8 for what is explicitly out of scope for
this project (engine-side concurrency fixes, building headless entry
points inside the engine repos themselves, cross-repo correlation ID
population, auth/multi-user access control, and Web UI visual design).

## Supported Automation Engines

Engines are registered through a small, explicit binding list in
`api/main.py` (`_KNOWN_ADAPTER_BINDINGS`) — adding a new engine means
adding one entry there and one adapter implementation under
`infra/adapters/<engine>/`; nothing else in the Controller changes.
Currently registered:

| Engine | Repo directory name (sibling of this repo) | Adapter |
|---|---|---|
| Etsy Video Generator | `etsy-video-generator` | `infra/adapters/video_generator/adapter.py` |
| Etsy AI Image Generator | `Etsy-AI-Image-Generator` | `infra/adapters/image_generator/adapter.py` |
| Etsy Mockup Generator | `etsy-mockup-generator` | `infra/adapters/mockup_generator/adapter.py` |

Adapters interact with their engine repo **read-only** at discovery time
(reading manifests/README files, checking for a worker script) and via
subprocess at launch time — never by importing the engine's own code.
A missing or restructured sibling repo degrades to a conservative
"unknown/not available" report rather than raising.

## Repository Layout

```
core/           framework-independent orchestration core (zero imports from api/ or infra/)
  domain/       Job, Pipeline (Definition + Run), Approval, Engine, EngineRunReference
  services/     JobService, PipelineService, ApprovalService, EngineRegistryService
  execution/    ExecutionCoordinator — the only component authorized to call adapter.launch()
  events/       EventBus protocol + in-memory implementation, event type definitions
  adapters/     EngineAdapter protocol + supporting types + base class
  repositories/ repository interfaces (SQLite implementations live in infra/db/)
infra/
  db/           SQLAlchemy/SQLModel models, SQLite repository implementations
  adapters/     concrete adapters: video_generator/, image_generator/, mockup_generator/
                (+ discovery_utils.py, resolving sibling repo locations)
  storage/      staging + presentation-cache helpers for uploads/generated workspaces
api/            FastAPI app — routers, request/response schemas, process startup
web/            Web UI — separate client, HTTP/WS only
tests/
  core/         pure domain/service tests — no FastAPI, no SQLite
  infra/        repository + adapter tests
  api/          HTTP/WebSocket contract tests
docs/
  superpowers/specs/  design docs (authoritative V1 design spec)
  architecture/       background research the design is built on
```

## Installation

### Requirements

- Python 3.11+
- Node.js (for the Web UI — any version compatible with Vite 8 / React 19)
- The sibling engine repos this Controller discovers, checked out as
  siblings of this repository (see **Engine Discovery** below) — the
  Controller runs without them, but engine-specific routes will report
  those engines as unavailable.

### Python setup

From this directory:

```
python -m venv .venv
.venv/Scripts/python.exe -m pip install fastapi uvicorn[standard] sqlmodel pydantic python-multipart send2trash pytest httpx
```

This installs the runtime dependencies (`fastapi`, `uvicorn`, `sqlmodel`,
`pydantic`, `python-multipart`, `send2trash`) plus the dev/test
dependencies (`pytest`, `httpx`) declared in `pyproject.toml`. The project
itself is not installed as a package — `api`, `core`, and `infra` are
resolved as top-level packages from the working directory (see
**Running the Backend**), so there is no `pip install -e .` step.

### Node setup

```
cd web
npm install
```

## Environment Configuration

| Variable | Where | Purpose | Default |
|---|---|---|---|
| `AUTOMATION_CONTROLLER_ENGINES_ROOT` | backend process env | Directory the Controller searches for sibling engine repos (see **Engine Discovery**) | Computed from this file's own location if unset — set this explicitly rather than relying on that default. |
| `AUTOMATION_CONTROLLER_API_PORT` | backend process env | Overrides the API port | `8123` |
| `VITE_API_BASE_URL` | `web/.env.local` (copy from `web/.env.example`) | Where the Web UI expects the API | `http://127.0.0.1:8123` |
| `VILICITY_RUNTIME_ROOT` | backend process env | Optional shared folder (e.g. OneDrive) for the multi-machine job-history feature — see `docs/shared-runtime.md` | Local-only `var/vilicity_runtime/`, never shared |
| `VILICITY_MACHINE_ID` | backend process env | Optional friendlier/stable override for the machine identifier used to keep two machines' published jobs from colliding | OS hostname |

If you change the API port, update `VITE_API_BASE_URL` to match and
restart the Vite dev server — `.env` files are read at startup, not per
request.

### Engine Discovery

By default, the Controller looks for sibling engine repos in the parent
directory of wherever this repo's `infra/adapters/discovery_utils.py`
lives (i.e. it assumes this repo and the engine repos are all direct
children of one shared parent folder, matching the layout used when
these repos were migrated to standalone form). If your engine repos live
somewhere else, set `AUTOMATION_CONTROLLER_ENGINES_ROOT` to that parent
directory explicitly before starting the backend — don't rely on the
implicit default across machines.

Each engine's adapter also looks for that engine's own `.venv` (falling
back to `AUTOMATION_CONTROLLER_ENGINES_ROOT/.venv`, then to the
Controller's own interpreter as a last resort) to run that engine's
headless worker script as a subprocess. For full functionality, give each
engine repo its own `.venv` with that engine's own dependencies installed
— the Controller's own `.venv` intentionally does not include any
engine's dependencies (e.g. `Pillow`, `mediapipe`).

## Running the Backend

Start it from **this directory** (`automation-controller/`) with:

```
.venv/Scripts/python.exe -m api.main
```

Add `--reload` for auto-restart on file changes during development:

```
.venv/Scripts/python.exe -m api.main --reload
```

That entrypoint pins the canonical host/port itself, so it cannot start on
the wrong one — including with `--reload`, which is the other invocation
this guards against (a bare `uvicorn ... --reload` has the exact same
port-8000 trap as a bare `uvicorn ... app`). Use `python -m api.main`
rather than a bare `uvicorn` command; there is no longer a reason to reach
for raw `uvicorn` even for live-reload. It must be run from this
directory, since `-m api.main` resolves `api` as a top-level package from
the working directory.

**Why this matters.** The Controller API's canonical port is **8123**
(matches `web/.env.example`, `web/README.md`, and
`FALLBACK_API_BASE_URL` in `web/src/api/diagnostics.ts`). Plain
`uvicorn api.main:app` silently falls back to uvicorn's own default of
**8000**, and the resulting failure is genuinely confusing rather than
obvious: the backend starts cleanly, prints "Uvicorn running on
http://127.0.0.1:8000", never crashes and never logs a traceback — while
the Web UI reports "Backend not responding" because nothing is listening on
8123. It reads like backend instability; it is a port mismatch. This
happened repeatedly, which is why `api/main.py` now logs a `PORT MISMATCH`
warning at startup for any invocation that would serve on a port the Web UI
won't call.

`uvicorn` directly still works if you pin the port explicitly:

```
.venv/Scripts/python.exe -m uvicorn api.main:app --app-dir . --host 127.0.0.1 --port 8123
```

## Running the Web UI

```
cd web
npm run dev
```

Expects the Controller API running at `http://127.0.0.1:8123` by default
— override via `VITE_API_BASE_URL` (see **Environment Configuration**).

## Development Workflow

- Backend and frontend are run as two separate processes in development
  (`python -m api.main --reload` and `npm run dev`), talking over HTTP/WS
  on `127.0.0.1`.
- `core/` has no test dependency on FastAPI or SQLite — domain/service
  logic can be tested in isolation from the delivery and persistence
  layers (see `tests/core/`).
- Route/contract changes are exercised through FastAPI's `TestClient`
  (`tests/api/`), not by running a live server.
- The Web UI's typed REST client (`web/src/api/`) mirrors
  `api/schemas.py` and the `core/domain/*` response shapes 1:1 — there
  are no parallel frontend data models to keep in sync by hand.

## Adding a New Automation Engine

1. Implement an adapter under `infra/adapters/<engine>/adapter.py`
   satisfying the `EngineAdapter` protocol (`core/adapters/`) — discovery,
   launch, monitoring, and result-collection behavior specific to that
   engine.
2. Add one entry to `_KNOWN_ADAPTER_BINDINGS` in `api/main.py`, mapping
   the engine's sibling-repo directory name to its adapter's dotted class
   path.
3. If the engine exposes routes of its own (upload workflows, review
   steps, etc.), add a router module under `api/` following the pattern
   of the existing `*_routes.py` files.
4. If the API needs to open the engine's output folder directly, add an
   entry to `_ENGINE_OUTPUT_ROOTS` in `api/main.py`.

No changes to `core/` are needed to add an engine — that is the point of
the adapter boundary.

## Testing

```
.venv/Scripts/python.exe -m pytest
```

Runs the full suite (`tests/core/`, `tests/infra/`, `tests/api/`) per
`pyproject.toml`'s `[tool.pytest.ini_options]`. `tests/core/` is currently
scaffolded (a `README.md` describing its scope) with no test files yet —
domain/service logic doesn't have dedicated unit tests at this point in
the project.

## Troubleshooting

- **Web UI says "Backend not responding" but the backend looks like it
  started fine.** Almost always the port-8000-vs-8123 mismatch described
  above — check the backend's startup log for a `PORT MISMATCH` warning.
- **An engine shows as unavailable / not discovered.** Confirm the
  engine's repo is checked out as a sibling of this repo (or that
  `AUTOMATION_CONTROLLER_ENGINES_ROOT` points at the correct parent
  directory) and that its directory name exactly matches the name in
  `_KNOWN_ADAPTER_BINDINGS` (case-sensitive on some tooling).
- **An engine is discovered but a launched job fails immediately.** Check
  that engine's own repo has a `.venv` with its dependencies installed —
  the Controller's adapter falls back to a shared/parent `.venv` or its
  own interpreter, neither of which has any engine's own dependencies
  (e.g. `Pillow`, `mediapipe`) installed by default.

## Current State

The core job lifecycle is implemented and tested end to end, not a
scaffold: a Job moves `validated -> queued -> running -> succeeded /
failed / cancelled` through `JobService`'s guarded status transitions
(`core/services/job_service.py`), with the `ExecutionCoordinator`
(`core/execution/coordinator.py`) as the only component authorized to
call `adapter.launch()`/`adapter.cancel()`. Every transition is
append-only-logged via `JobEvent` (`core/domain/job_event.py`), and
`MonitoringService` derives progress, per-engine health, and metrics
from that event log on demand rather than from a second, separately
mutated copy of the same state.

**Cancellation is race-safe by construction.** `ExecutionCoordinator.
cancel_job()` marks a Job `CANCELLED` in the database *before* calling
`adapter.cancel()`, so a concurrent in-flight `evaluate()` call that
finishes (successfully or not) after the cancellation always loses the
race via a guarded `InvalidJobStatusTransitionError` and never
overwrites a user-initiated cancellation with a stale success/failure
result. Process termination goes through platform process-tree kill
logic in each subprocess-based adapter, not a single PID signal.

**Engine adapters** (`infra/adapters/<engine>/adapter.py`) for the Video
Generator, AI Image Generator, and Mockup Generator are real, launching
each engine's own headless worker as a subprocess and polling for
completion — never importing an engine's own code. **Human-review /
approval gates** are implemented per engine, directly in each engine's
own route module (concept approval, prompt review, generated-image
review for the AI Image Generator; preview/batch approval for the
Mockup Generator — see `api/image_generator_routes.py`,
`api/mockup_generator_routes.py`) rather than through the generic
cross-engine `Approval`/`Pipeline` abstractions in `core/`, which remain
scaffolded (signatures + docstrings, `NotImplementedError` bodies) for a
future cross-engine pipeline feature that isn't built yet — see
**Future Architecture Direction** below.

**Also implemented:** listing asset package assembly across engines
(`infra/listing_workspace.py`), safe job/workspace deletion that only
ever recycles a Job's own output — never a shared template or another
Job's still-referenced path — via the Windows Recycle Bin, never a
permanent delete (`infra/cleanup.py`), lifetime metrics
(`infra/lifetime_metrics.py`), and an optional shared-runtime feature
(`infra/runtime_root.py` / `runtime_publisher.py` / `runtime_index.py`,
see `docs/shared-runtime.md`) that lets two independent local clones
(e.g. a desktop and a laptop) see the same completed-job history through
a synced folder such as OneDrive, entirely opt-in via one environment
variable and never part of either Git repository.

**Not yet implemented:** the generic cross-engine `PipelineService`/
`ApprovalService` abstractions (`core/services/pipeline_service.py`,
`core/services/approval_service.py` — see their docstrings), automatic
event-driven re-evaluation on `EngineCompleted`/`EngineFailed`/
`ApprovalResolved`/`PipelineAdvanced` (the Coordinator currently reaches
the same outcome through a disclosed synchronous bridge called directly
from `api/main.py`, not through the Event Bus these handlers are wired
to but never fired for), and the `/events` WebSocket route.

## Future Architecture Direction

The design's explicit stance is that the Controller's core architecture
is built around the permanent target state — every engine behind one
uniform adapter contract, and eventually a real cross-engine pipeline
running purely off Event Bus events — while individual pieces are
allowed to be incomplete where the underlying need isn't there yet. The
per-engine approval routes described above are the practical, working
V1 path; folding them into the generic `ApprovalService`/
`PipelineService` abstractions (so a future multi-engine pipeline could
pause on any engine's approval uniformly) is the next architectural
step, not a V1 requirement. As sibling engines mature (e.g. gaining
their own headless, non-interactive entry points), their adapters can be
filled in further without any change to `core/` or the adapter interface
itself. See `docs/architecture/CONTROLLER-V1-ARCHITECTURE-INSPECTION.md`
for a per-engine assessment of what each sibling repo still needs.
