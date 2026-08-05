# Automation Controller V1 — Architecture Design

**Date:** 2026-07-21
**Status:** Approved architecture, pre-implementation
**Location:** `E:\Vilicity\automation-controller` (new sibling project; never nested inside an engine)

## Context

This is the kickoff of the Automation Controller — the orchestration layer coordinating independent automation engines (`etsy-mockup-generator`, `Etsy-AI-Image-Generator`, `etsy-video-generator`, with more engines to come). Two reference documents already exist in the repo root and are treated as authoritative, exhaustively-researched ground truth for the three current engines — this design does not re-derive their internals:

- `CONTROLLER-V1-ARCHITECTURE-HANDBOOK.md` — per-engine deep architecture extraction, cross-repo glossary, final knowledge transfer.
- `CONTROLLER-V1-ARCHITECTURE-INSPECTION.md` — cross-repo analysis, integration readiness ranking, Controller dependency report, open integration questions.

**Key finding from those documents:** the three engines are unevenly ready for a Controller today. `etsy-video-generator` exposes a genuinely non-interactive, tested, importable function (`run_video_generation()`). `Etsy-AI-Image-Generator` has the best-designed artifacts (`job_manifest.json`, `approved_media_handoff.json`) but no headless entry point at all. `etsy-mockup-generator` is fully interactive with no reusable function and an already-drifted manifest schema.

**Explicit architectural stance (governs this entire document):** the Controller is designed around its *permanent* target architecture — every engine exposed through one uniform, headless-capable contract — not around today's limitations. Where an engine (video-generator) already has a real headless seam, its adapter uses it now. Where an engine doesn't yet (image-generator, mockup-generator), its adapter is stubbed/simulated for the methods that are genuinely blocked, while still doing real work for the methods that already work today (e.g. reading manifests). The Controller's core architecture never bends to accommodate an immature engine — only the adapter for that engine is incomplete.

## Guiding principles

1. **The Controller orchestrates; engines execute.** No engine business logic is ever reimplemented in the Controller.
2. **Engines remain the source of truth for their own state.** The Controller never duplicates engine business state — it stores references (paths, IDs, manifest locations) and reads engines' own artifacts live when it needs details.
3. **Strict separation between orchestration and execution.** The Core never knows *how* an adapter talks to its engine (in-process import, subprocess, future HTTP call) — only the Adapter interface.
4. **Clients are peers, not privileged.** The Web UI is one client of the Controller Core among future others (CLI, mobile, remote API) — none of them import Core internals; all talk through the same REST/WebSocket boundary.
5. **Don't overbuild V1.** Define architectural boundaries and interfaces now; implement the simplest correct version behind them (e.g. in-memory event bus, not a message broker).

## 1. Layered Architecture

```
Clients (Web UI now; CLI / mobile / API later)
   │  HTTP + WebSocket only — no imports of Core internals
   ▼
Delivery Layer (FastAPI) — thin: request/response mapping, auth, validation
   │
   ▼
Application Services (framework-independent, plain Python)
   JobService · PipelineService · ApprovalService · EngineRegistryService
   (lifecycle, persistence, and workflow ownership — no runtime "what next")
   │                                    │
   │                                    ▼
   │                          Event Bus (in-process pub/sub)
   │                          JobCreated, JobStarted, JobProgressUpdated,
   │                          ApprovalRequested, ApprovalResolved,
   │                          EngineCompleted, EngineFailed,
   │                          PipelineAdvanced, PipelineCompleted,
   │                          RetryScheduled
   │                                    │
   │                     ┌──────────────┼───────────────────────────┐
   │                     ▼              ▼                           ▼
   │       Execution Coordinator   WebSocket broadcaster   audit/history writer,
   │       (consumes + publishes    (delivery layer)        (future) notifications,
   │        Event Bus events —                               automation rules, plugins
   │        never bypasses it)
   │                     │
   │                     │  decides what runs next; calls adapters directly
   ▼                     ▼
Domain Model  ◄──── Repository Layer (SQLite, behind repo interfaces)
Job · Pipeline · Approval
Engine · EngineRunReference
                          │
                          ▼
                Engine Adapter Interface
       discover / validate / launch / monitor / cancel / collect_results
                          │
        ├── VideoGeneratorAdapter   (real, in-process import of run_video_generation())
        ├── ImageGeneratorAdapter   (stub launch(); real monitor()/collect_results() via manifest reads)
        └── MockupGeneratorAdapter  (stub launch(); real monitor()/collect_results() via manifest reads)
```

Dependencies point inward: `core/` (domain + services + events + adapter interfaces) has zero imports from `api/` or `infra/`. FastAPI is an outer delivery shell around Application Services, not a home for orchestration logic.

**Two distinct kinds of "service" now exist in Application Services, deliberately separated:**

- **Lifecycle/workflow owners** — `JobService`, `PipelineService`, `ApprovalService`, `EngineRegistryService` — own persistence and the *meaning* of a state transition (what does it mean for a Job to be "created," a Pipeline to be "defined," an Approval to be "resolved"). They never decide *when* something should run.
- **The Execution Coordinator** — owns the *runtime* decision of "what should execute next, right now." It is the only component that decides to actually invoke `adapter.launch()`. This split keeps "what a Job/Pipeline/Approval means" (workflow owners) cleanly separate from "what runs now, under what constraints" (Coordinator) — see Section 3a.

## 2. Event Bus

An in-process, framework-independent event bus inside the Core — the seam between "something happened" and "something reacts to it."

- **Interface**: minimal `EventBus` protocol — `publish(event)`, `subscribe(event_type, handler)`. V1 implementation is in-memory pub/sub (no external broker). The protocol is what's stable; the implementation can be swapped later (e.g. for a multi-process Controller) without touching publishers or subscribers.
- **Events are domain facts, not commands** — past-tense, immutable, typed: `JobCreated`, `JobStarted`, `JobProgressUpdated`, `ApprovalRequested`, `ApprovalResolved`, `EngineCompleted`, `EngineFailed`, `PipelineAdvanced`, `PipelineCompleted`, `RetryScheduled`.
- Application Services publish events as a side effect of state transitions they already own and persist — the bus never decides anything, only announces.
- Subscribers in V1: the **Execution Coordinator** (see Section 3a — the one subscriber that acts on events by launching work, not just observing), the WebSocket broadcaster (delivery layer), and an audit/history writer. The latter two are optional/observational — removing either doesn't break orchestration. This is also the extension point for future notifications, automation rules, and plugins, without coupling those concerns to Application Services.

## 3. Domain Model

Four first-class entities, persisted in SQLite behind repository interfaces:

- **Engine** — registry entry: name, adapter binding, capability flags (from `discover()`, e.g. `supports_cancel: bool`), current health/status. Statically registered at startup, not user-created.
- **Job** — one execution of one engine.
  - `id`, `engine_id`, `pipeline_run_id` (nullable — a Job can stand alone or belong to a PipelineRun)
  - `status`: `pending / running / waiting_on_approval / succeeded / failed / cancelled`
  - `config`: engine-specific launch params, opaque JSON to the Core
  - `run_reference`: an `EngineRunReference` (see below)
  - `error`: nullable, structured
  - `created_at`, timestamps for each transition
- **EngineRunReference** — a generic abstraction over "however this engine represents one of its own executions" (manifest path + run ID today; could be a job folder, an external API identifier, or something else for a future engine). Stores *pointers*, never a copy of engine state. The Core reads through this reference (via the adapter) when it needs current detail, rather than trusting a cached copy.
- **Approval** — a pending or resolved human decision, generic across engines and decision kinds:
  - `id`, `job_id`, `type` (free-form string — `confirmation`, `selection`, `review`, `rejection`, `retry`, extensible without schema changes)
  - `prompt_payload`: JSON — whatever the engine needs shown (candidate images, an order list, a background choice, etc.)
  - `status`: `pending / resolved`
  - `decision`: JSON, nullable until resolved
  - `resolved_at`
  - An engine reports "I need a decision" plus the info needed to make it; the Controller owns presenting it, persisting the outcome, and returning the result back through the adapter. No engine-specific approval logic lives in the Core.
- **Pipeline** — split into two:
  - **PipelineDefinition**: a reusable template — an ordered/graph sequence of steps, each naming an engine plus how its inputs map from a prior step's outputs. Contains orchestration only, never engine-specific logic.
  - **PipelineRun**: one execution of a definition — tracks which Jobs belong to it, current step, overall status. `PipelineAdvanced` / `PipelineCompleted` events fire off its transitions.
  - The Controller can execute a single Job independently, or execute a full Pipeline — both use the same scheduling, approval, monitoring, and adapter infrastructure. Simple tasks stay simple; complex end-to-end Etsy workflows emerge by connecting Jobs into Pipelines.

## 3a. Execution Coordinator

A first-class Application Service whose sole responsibility is the runtime question **"what should execute next?"** — never business logic, never engine-specific behavior. It is the only component in the Core authorized to call `adapter.launch()`.

**Responsibilities:**
- Determine which `pending` Jobs are eligible to run (config validated, no unresolved blocking dependency).
- Respect each Engine's declared capability and concurrency constraints (from `discover()` — e.g. "this engine allows at most 1 concurrent run").
- Enforce per-engine concurrency limits, ensuring only the allowed number of Jobs execute against a given engine at once (this is where the handbook's documented unguarded-concurrency races are deliberately fenced off at the Controller layer, without requiring the engines themselves to change).
- Launch eligible Jobs through the appropriate `EngineAdapter`.
- React to `EngineCompleted` / `EngineFailed` events by updating scheduling state and considering queued work.
- Advance a `PipelineRun` by launching its next step's Job(s) once that step's dependencies (prior steps' outputs) are satisfied — implemented as reacting to `EngineCompleted` and publishing `PipelineAdvanced`, never by directly mutating Pipeline definitions.
- Schedule retries when a Job's failure and the Engine's/Pipeline's retry policy allow it, publishing `RetryScheduled` and re-queuing rather than silently re-launching.
- Coordinate queued work generally (ordering, backpressure) without embedding any engine-specific logic — its inputs are always Job/Engine/Pipeline domain objects and Event Bus events, never engine internals.

**Interaction model:** the Coordinator **consumes and publishes Event Bus events rather than bypassing the event architecture** — it subscribes to `JobCreated`, `EngineCompleted`, `EngineFailed`, `ApprovalResolved`, `PipelineAdvanced` (its own), and publishes `JobStarted`, `PipelineAdvanced`, `PipelineCompleted`, `RetryScheduled` as it acts. It never receives a direct method call from `JobService` telling it to launch something — it reacts to the same events everything else does, which keeps it swappable/testable in isolation (a test can publish a `JobCreated` event and assert the Coordinator calls the right adapter, with no HTTP or DB involved).

**Division of ownership, stated precisely:**
| Concern | Owner |
|---|---|
| A Job exists, its config, its persisted status, its history | `JobService` |
| A Pipeline definition's shape; a PipelineRun's persisted state | `PipelineService` |
| An Approval's payload, resolution, persistence | `ApprovalService` |
| *When* a Job actually launches; concurrency limits; retries; pipeline step advancement timing | **Execution Coordinator** |

`JobService.create_job()` persists a `pending` Job and publishes `JobCreated` — it does not launch anything itself. The Execution Coordinator, on seeing `JobCreated`, decides whether the Job is eligible to run now and, if so, calls the adapter and updates status through `JobService` (Coordinator still goes through `JobService`'s persistence methods to record `running`/`succeeded`/`failed` — it doesn't write to the repository directly, preserving `JobService` as the single writer of Job state).

## 4. Engine Adapter Interface

The one contract the Core depends on; everything engine-specific hides behind it.

```python
class EngineAdapter(Protocol):
    def discover(self) -> EngineCapabilities: ...
    def validate(self, config: dict) -> ValidationResult: ...
    def launch(self, config: dict) -> EngineRunReference: ...
    def monitor(self, ref: EngineRunReference) -> EngineStatus: ...
    def cancel(self, ref: EngineRunReference) -> bool: ...          # best-effort, capability-gated
    def collect_results(self, ref: EngineRunReference) -> EngineResult: ...
```

- **`discover()`** — static capability metadata, cached on the `Engine` registry row at startup: does this engine support cancel (and to what degree — graceful vs. stop-after-current-step vs. unsupported)? What config schema does `launch()` expect? What approval types might it raise? The Controller enables/disables UI actions based on these declared capabilities rather than assuming uniform support — `cancel()` in particular is always best-effort, never guaranteed.
- **`validate(config)`** — pre-flight check before committing to a launch (e.g. "is there a ZIP in `input/`?"), separating job creation from job launch so the UI can surface config errors before a Job even exists.
- **`launch(config)`** — starts the engine, returns an `EngineRunReference` immediately. The adapter owns whether this is an in-process thread, a subprocess, or (future) an HTTP call. May raise a `NotImplementedCapability`-style error for engines whose launch path is genuinely stubbed (image-generator, mockup-generator today).
- **`monitor(ref)`** — polled to get current `EngineStatus` (running / waiting_on_approval / succeeded / failed, optional progress, optional `ApprovalRequest` payload). `JobService` translates this into `JobProgressUpdated` / `ApprovalRequested` events.
- **`collect_results(ref)`** — called once terminal; reads the engine's actual artifacts (whatever form they take) and returns a normalized `EngineResult`. This is where defensive parsing of each engine's real (sometimes drifted) manifest schema lives — scoped per adapter, never in the Core.

**V1 adapters:**

| Adapter | `launch()` | `monitor()` / `collect_results()` |
|---|---|---|
| `VideoGeneratorAdapter` | Real — calls `run_video_generation()` in-process (worker thread) | Real — reflects thread state; reads returned path / `VideoGenerationError` |
| `ImageGeneratorAdapter` | Stubbed — no headless entry point exists yet | Real — polls/parses `job_manifest.json` / `approved_media_handoff.json` today |
| `MockupGeneratorAdapter` | Stubbed — no headless entry point exists yet | Real — polls/parses `manifest.json` defensively (known schema drift) |

Swapping a stub `launch()` for a real one later (once those repos gain headless entry points) is purely an adapter-internal change — the Core, services, domain model, and UI never change.

## 5. API Surface

**REST — commands and queries:**
```
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
```

**WebSocket — live event delivery only:**
A single generic `/events` stream publishing typed domain events straight from the Event Bus (`JobStarted`, `ApprovalRequested`, etc.), client-side filterable. No engine-specific WebSocket endpoints — REST remains responsible for all commands/queries; WebSocket remains responsible only for live event delivery. New engines, event types, and future clients work without changing the API surface.

## 6. Persistence

- Controller-owned orchestration state (jobs, approvals, pipeline definitions/runs, scheduling, history, engine registry, user preferences) lives in an embedded **SQLite** database.
- Access goes through **repository interfaces** defined in `core/repositories/`, with SQLite implementations in `infra/db/`. Orchestration logic depends only on the interfaces, so the storage backend (e.g. a future Postgres) can change without touching Application Services.
- The Controller **never** persists a copy of engine business state — only references (`EngineRunReference` fields: paths, run IDs, manifest locations, timestamps). Current detail is always read live from the engine's own artifacts via the adapter.

## 7. Project Structure

```
automation-controller/
├── core/                          # framework-independent — the actual product
│   ├── domain/                    # Job, Pipeline, Approval, Engine, EngineRunReference
│   ├── services/                  # JobService, PipelineService, ApprovalService, EngineRegistryService
│   ├── execution/                 # ExecutionCoordinator — subscribes/publishes Event Bus events,
│   │                               # owns concurrency limits, retries, pipeline-step advancement
│   ├── events/                    # EventBus protocol + in-memory impl, event type definitions
│   ├── adapters/                  # EngineAdapter protocol + base classes
│   └── repositories/              # repository interfaces (SQLite impls live in infra/)
├── infra/
│   ├── db/                        # SQLAlchemy/SQLModel models, SQLite repository impls, migrations
│   └── adapters/                  # concrete adapters: video_generator/, image_generator/, mockup_generator/
├── api/                           # FastAPI app — routers, WS broadcaster, request/response schemas
│   └── main.py
├── web/                           # Web UI — separate client, talks HTTP/WS only
├── tests/
│   ├── core/                      # pure domain/service tests — no FastAPI, no SQLite (fakes/in-memory repos)
│   ├── infra/                     # repository + adapter tests
│   └── api/                       # HTTP/WebSocket contract tests
└── docs/superpowers/specs/        # design docs
```

## 8. Explicitly out of scope for this design

- Engine-side concurrency/locking fixes (the handbook documents concrete unguarded races in mockup-generator and video-generator today, e.g. unlocked numbering scans, single-ZIP assumptions). The Controller does not wait for engines to fix this themselves — the Execution Coordinator (Section 3a) enforces per-engine concurrency limits at the orchestration layer, so V1 never launches more concurrent Jobs against a given engine than that engine can safely handle. Fixing the underlying races *inside* the engines remains out of scope for the Controller project.
- Building the actual headless entry points inside `Etsy-AI-Image-Generator` / `etsy-mockup-generator` — those are separate, scoped, additive changes to those repos, not part of the Controller project.
- Cross-repo correlation ID population (`design_id` / `listing_id` fields the engines already anticipate but don't populate) — the Controller can mint and track its own IDs internally now; wiring them *into* engine-side fields is deferred until those engines are ready to consume them.
- Authentication/multi-user access control for the API — V1 is single-operator, matching the engines' own assumption.
- Any specific Web UI visual design — covered by a future frontend-design pass, not this architecture doc.
