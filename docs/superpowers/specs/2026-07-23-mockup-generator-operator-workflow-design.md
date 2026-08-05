# Etsy Mockup Generator — Operator Workflow (Step 13) — Design

**Date:** 2026-07-23
**Status:** Approved design, pre-implementation
**Scope:** Make the Etsy Mockup Generator genuinely usable through the Automation Controller, from ZIP upload to finished mockups, as one vertical slice. No AI Image Generator work, no Research Assistant/Draft Editor integration, no broad dashboard redesign.

## Context

Per `CONTROLLER-V1-ARCHITECTURE-HANDBOOK.md` and the V1 design spec (`2026-07-21-automation-controller-v1-design.md`), `etsy-mockup-generator` is the least Controller-ready of the three current engines: fully interactive (three `input()` calls in `batch_generate.py`), no reusable non-interactive function, `launch()` in `MockupGeneratorAdapter` currently always raises `NotImplementedCapability`. `etsy-video-generator`'s operator workflow (Step 12) is the one complete, proven reference: a real headless engine function, a subprocess-worker adapter, dedicated engine-scoped routes, Controller-owned upload staging, and a purpose-built UI — all built without touching the generic `EngineAdapter` Protocol beyond its existing `on_run_reference` extension.

This design repeats that proven shape for the Mockup Generator, adapted for its two-phase (preview → approve → full batch) workflow.

## Guiding decisions (confirmed)

1. **No generic Approval/Pipeline machinery this step.** `ApprovalService`, `PipelineService`, and `ExecutionCoordinator._on_approval_resolved` remain exactly as stubbed today. The preview→approve→batch workflow is modeled as **two sequential Jobs** against the same engine, driven by dedicated mockup-generator routes — mirroring how `api/video_generator_routes.py` already drives Job creation directly, bypassing nothing in `JobService`/`ExecutionCoordinator`. If a second, more complex approval workflow emerges during AI Image Generator integration, a shared abstraction can be extracted then — not speculatively now.
2. **Subprocess worker invocation**, mirroring `infra/adapters/video_generator/_launch_worker.py`: the engine runs in its own OS process, `cwd` set to its own repo root (its relative-path assumptions — `backgrounds/`, `output/`, `working/`, `preview/`, `processed-inputs/` — stay valid untouched), spec/result passed via JSON files, `sys.executable` (same shared venv already has Pillow + mediapipe).
3. **Preview is an opaque, engine-owned run token, not a resumed-process contract.** The Controller never inspects or reconstructs run state — it receives an opaque `run_token` from the preview call and passes it back, unmodified, to the batch call. Whether the engine re-executes classification/extraction internally or resumes cached state is a private implementation detail of `headless.py`.
4. **Preview output is a list of artifacts, not a single hardcoded image field.** The engine returns `preview_artifacts: list[dict]`, each tagged with a `kind` and a `representative: bool`. The Controller displays the one artifact marked representative in V1 (today: the contact sheet) without the engine's return contract needing to change if richer previews (e.g. per-category approval, multiple representative shots) are wanted later.

## 1. Engine headless seam — `etsy-mockup-generator/headless.py` (new file)

Purely additive: `batch_generate.py`'s `main()` and every function it already calls are untouched — the interactive CLI keeps working exactly as today. `headless.py` imports and recomposes those existing functions (`scan_backgrounds`, `background_size_error`, `generate_category_previews`, `render_mockup_to_path`, `make_run_id`, `move_processed_zip`, manifest construction) into three non-interactive, exception-based (never `sys.exit()`) entry points:

```python
class MockupEngineError(Exception):
    def __init__(self, category: str, message: str, detail: dict | None = None): ...

def list_backgrounds() -> list[dict]:
    """[{"name": str, "path": str, "usable": bool, "reason": str | None}, ...]
    reason is populated (e.g. wrong canvas size) when usable is False —
    mirrors background_size_error()'s existing check, run eagerly here
    instead of lazily at selection time."""

def prepare_preview(zip_path: str, background_path: str, design_id: str | None = None) -> dict:
    """Validates the zip and background, extracts + classifies (reusing
    extract_zip/classify unchanged), renders one representative preview per
    present category plus a contact sheet through the exact same
    render_mockup_to_path() the full batch uses. Returns:
    {
      "run_token": str,               # opaque; pass back unmodified
      "preview_artifacts": [
        {"kind": "contact_sheet", "path": str, "representative": True, "label": str},
        {"kind": "category_preview", "path": str, "category": str,
         "source_filename": str, "representative": False},
        ...
      ],
      "category_counts": {...},
      "total_files_extracted": int,
      "background_filename": str,
    }
    Raises MockupEngineError for: unreadable/empty zip, no supported
    backgrounds present, chosen background wrong size, zero classifiable
    categories found."""

def generate_full_batch(run_token: str) -> dict:
    """Resumes/re-derives whatever prepare_preview() cached under run_token
    (implementation detail, not part of the contract) and runs the same
    post-approval loop main() runs today: full per-category render loop,
    manifest.json (written twice, same as today), zip archival into
    processed-inputs/. Returns:
    {"run_id", "run_dir", "manifest_path", "assets_dir", "generated_counts",
     "errors", "run_succeeded", "manifest": {...full parsed manifest...}}
    Raises MockupEngineError only for unrecoverable failure before a
    manifest can be written; partial per-asset failures are captured in
    the returned errors/run_succeeded, matching existing manifest
    semantics."""
```

`zip_path` and `background_path` are explicit parameters — `headless.py` never scans the shared `input/`/`backgrounds/` folders itself for a target to act on (only `list_backgrounds()` scans `backgrounds/` for discovery). This is what lets the Controller pass its own staged ZIP copy without the shared `input/` folder ever being the user-facing workflow, with zero change to `find_first_zip`/`INPUT_DIR` used by the CLI.

`run_token` state lives under a new `runs/` folder in the engine repo (sibling to `preview/`, `working/`), one subfolder per token, holding whatever `headless.py` needs to bridge the two calls (cached extraction/classification, chosen background, design id). Cleanup of consumed/abandoned run folders is `headless.py`'s own concern, not the Controller's.

## 2. Controller adapter — `infra/adapters/mockup_generator/adapter.py` + new `_headless_worker.py`

`_headless_worker.py` mirrors `_launch_worker.py`: spawned with `sys.executable`, `cwd` set to the resolved engine repo root, given a JSON spec file and writing a JSON result file, importing `headless.py` by file path (same pattern `_load_engine_module()` uses in the video adapter).

`MockupGeneratorAdapter` becomes real for `launch()`/`collect_results()`, keyed off `config["phase"]`:

- `launch({"phase": "preview", "zip_path", "background_path", "design_id"})` → spawns the worker calling `prepare_preview()`; on success returns an `EngineRunReference` whose `extra` carries `run_token` and the `preview_artifacts` list (small enough to inline; avoids a second read for collect_results).
- `launch({"phase": "batch", "run_token"})` → spawns the worker calling `generate_full_batch()`; returns a reference to the written `manifest.json`.
- `collect_results()` normalizes each phase's dict into `EngineResult.artifacts` unchanged (no re-parsing) — this adapter's defensive-manifest-parsing responsibility (per the V1 design spec, drifted-schema tolerance) applies to the **batch** phase's manifest read, same as documented in the adapter's existing docstring.

`discover()` updates `supports_launch`/`implementation_status` to reflect that `launch()` is now real (checks for `headless.py` + the three function names, same static-inspection style the video adapter uses). `monitor()` stays `NotImplementedError` — both phases are synchronous within one `launch()` call, exactly like the video generator's Coordinator bridge.

No changes to `core/adapters/engine_adapter.py`'s Protocol — `phase` is just an adapter-private config key, invisible to the Core/Coordinator, exactly like `preset_key` is for the video adapter today.

## 3. Two-Job workflow

- **Preview Job**: `engine_id="etsy-mockup-generator"`, `config={"phase": "preview", "zip_path": <staged>, "background_path": <chosen>, "design_id": <optional>}`. Created + evaluated synchronously via the same `JobService.create_job()` + `ExecutionCoordinator.evaluate()` pair every engine already uses — no changes to either.
- Operator reviews the representative preview artifact in-browser, then either:
  - creates another **preview Job** with a different `background_path` (discarding the prior one), or
  - creates a **batch Job**: `config={"phase": "batch", "run_token": <from the preview Job's result_summary>}`.
- `run_token` is opaque to the Controller — stored and passed through, never parsed.
- No Approval entity, no Pipeline entity, no changes to `ApprovalService`/`PipelineService`/`Coordinator._on_approval_resolved`.

## 4. ZIP upload staging — `infra/storage/mockup_generator_staging.py` (new)

Mirrors `video_generator_staging.py`:

- `var/staging/mockup_generator/<uuid>/<sanitized-filename>.zip`, one ZIP per staging dir.
- Validation before anything is written to disk: real ZIP magic bytes, non-empty, under a size cap.
- `mark_job_id()` / `sweep_terminal_staging_dirs()` reused pattern for cleanup once the owning Job reaches a terminal status; a second preview Job (different background) reuses the same staged ZIP file rather than re-uploading.
- Never touches or deletes the operator's original file on their machine — the browser uploads bytes once; the Controller writes and owns its own copy from that point on.

## 5. API routes — `api/mockup_generator_routes.py` (new)

```
GET  /mockup-generator/backgrounds                 -> adapter.discover()-adjacent call into list_backgrounds(),
                                                        thumbnails served from a route restricted to the
                                                        registered backgrounds/ directory only
POST /mockup-generator/jobs/preview                 multipart on first call (zip file + background_path +
                                                        design_id?), JSON on background-change reruns
                                                        (staged_zip reference + new background_path)
POST /mockup-generator/jobs/{id}/batch               id = a succeeded preview Job; creates + launches the batch Job
GET  /mockup-generator/jobs/{id}/preview-image       serves the representative preview artifact, path re-derived
                                                        from that Job's own result_summary and re-validated
                                                        against the engine's preview/ dir
GET  /mockup-generator/jobs/{id}/result-image        serves one representative completed asset, path re-derived
                                                        from the batch Job's own manifest and re-validated
                                                        against that run's assets/ dir
POST /jobs/{id}/open-output-folder                   reuses the exact pattern from video_generator_routes.py,
POST /jobs/{id}/open-listing-outputs                  scoped to this engine's run_dir
```

Every file-serving route re-derives its path from the Job's own persisted `result_summary` — never a client-supplied path — exactly the safety pattern `_resolve_output_video_path()` already establishes.

## 6. Web UI — `web/src/components/mockup-generator/` (new)

Mirrors the structure of `video-generator/`. One workflow component covering, in order: ZIP upload (click-to-upload + drag-and-drop, selected filename, replace/remove) → background cards (from `GET /mockup-generator/backgrounds`, name + thumbnail where available, clearly marking unusable ones) → optional Design ID field → Generate Preview → single representative preview image with **Approve and Generate Full Batch / Choose Different Background / Cancel** → stage indicator (Uploading ZIP / Validating Inputs / Generating Preview / Waiting for Approval / Generating Mockups / Finalizing Outputs / Complete / Failed / Cancelled, derived from which Job/phase is active and its status — no fabricated percentages) → result screen (representative completed asset, total assets, category counts, background used, Design ID, duration, Open Output Folder, Open Current Listing Outputs, Launch Another Mockup Job) with a collapsed Developer Details section holding the raw manifest, config, and paths.

## 7. Failure handling

`MockupEngineError` raised inside the worker → translated to `EngineLaunchError` at the adapter boundary (`category`/`message`/`detail`, same normalized shape `VideoGeneratorAdapter` already produces) → surfaces through the existing `Coordinator`/`JobService.mark_failed()` path as a `JobError` the UI renders as a friendly banner, with raw detail under Developer Details. Every phase is synchronous within one HTTP request (same as video generator), so a worker crash or unexpected exception always resolves to a terminal Job status before the response returns — no Job is left permanently RUNNING or WAITING.

## 8. Live verification plan

Run the Controller + web UI for real and exercise: click-to-upload, drag-and-drop, replace/remove ZIP, background discovery/selection (including an unusable/wrong-size background), one real preview through the actual rendering pipeline, choose-different-background, cancel, approve → full batch, correct `manifest.json`/`assets/` contents, representative result preview, Open Output Folder, Launch Another Mockup Job, invalid ZIP, missing background, a forced failure path, persistence across an app restart, engine queue serialization (a second Job while one is running for this engine stays QUEUED), and confirm the Video Generator workflow is unaffected throughout.

## Explicitly out of scope

- AI Image Generator integration.
- Research Assistant / Draft Editor integration.
- Broad dashboard redesign or general UI polish beyond this workflow.
- `ApprovalService` / `PipelineService` / `Coordinator._on_approval_resolved` implementation.
- Any change to `core/adapters/engine_adapter.py`'s Protocol.
- Engine-side concurrency/locking fixes inside `etsy-mockup-generator` beyond what V1 serialization (`max_concurrent_runs=1`, already enforced by the Coordinator) already covers.
