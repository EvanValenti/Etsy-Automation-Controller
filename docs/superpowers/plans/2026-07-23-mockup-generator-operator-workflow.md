# Mockup Generator Operator Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `etsy-mockup-generator` fully operable through the Automation Controller — ZIP upload → background selection → one representative preview → approve/reject → full batch → results — as one vertical slice.

**Architecture:** A new additive `headless.py` module in the engine repo exposes three non-interactive functions (`list_backgrounds`, `prepare_preview`, `generate_full_batch`); `MockupGeneratorAdapter` calls them via a subprocess worker (mirroring `_launch_worker.py`); the two-phase workflow is modeled as two sequential Controller Jobs (`phase: "preview"` then `phase: "batch"`) driven by dedicated `api/mockup_generator_routes.py` routes and a `MockupLaunchWorkflow` React component — no changes to `ApprovalService`/`PipelineService`/the `EngineAdapter` Protocol.

**Tech Stack:** Python 3.11 (FastAPI, SQLModel), React + TypeScript, Pillow + mediapipe (engine side, existing venv at `<workspace-root>/.venv`).

## Global Constraints

- Design spec of record: `docs/superpowers/specs/2026-07-23-mockup-generator-operator-workflow-design.md`. Every task below implements one of its sections.
- `batch_generate.py`'s interactive CLI behavior (prompts, output text, file layout) must be byte-identical after this plan — refactors extract shared functions, they never change what the CLI prints or does.
- **No pytest suite exists in this repo today** (`tests/{core,infra,api}/` are `README.md` + `__init__.py` only — 12 prior steps of this Controller were built and verified entirely through direct script runs and live browser verification, not automated tests). This plan follows that established convention: each backend task is verified with a short, throwaway Python script run directly against the real code (`python -c "..."` or a one-off `verify_*.py` under the scratchpad, never committed), not a formal test file. Do not introduce a new, inconsistent testing pattern for just this feature.
- Never hardcode engine capabilities in the Controller — `discover()` stays grounded in real on-disk inspection, same as `VideoGeneratorAdapter`/existing `MockupGeneratorAdapter`.
- Every file-serving route re-derives its filesystem path from a Job's own persisted `result_summary`, never from a client-supplied path.
- `run_token` is opaque to the Controller end-to-end — stored, passed back unmodified, never parsed or inspected.
- Frontend styling must match existing conventions exactly (inline `style` objects, CSS custom properties like `var(--accent)`, `Button`/`Panel`/`EmptyBlock` components) — no new CSS framework, no restyling of unrelated pages.

---

## Task 1: Extract `execute_full_batch()` in the engine repo (pure refactor, zero behavior change)

**Files:**
- Modify: `../etsy-mockup-generator/batch_generate.py`

**Interfaces:**
- Produces: `execute_full_batch(grouped, background_path, design_id, zip_path, previews, total_files_extracted, pose_landmarker, face_detector, output_root_dir=OUTPUT_ROOT_DIR, processed_inputs_dir=PROCESSED_INPUTS_DIR) -> dict` with keys `run_id, run_dir, assets_dir, manifest_path, manifest, generated_counts, errors, framing_methods_used, processed_input` — consumed by both `main()` (Task 1) and `headless.generate_full_batch()` (Task 2).

This isolates the "approved: full batch generation" stage (today inlined in `main()`, lines ~593-696) into a standalone, reusable function, exactly as the design spec's "isolate its current stages" instruction requires — without touching `classify_mockups.py`, `human_framing.py`, `composite.py`, or `shadow.py`.

- [ ] **Step 1: Add `execute_full_batch()` above `def main():`**

Insert this function immediately before `def main():` in `batch_generate.py`:

```python
def execute_full_batch(
    grouped,
    background_path,
    design_id,
    zip_path,
    previews,
    total_files_extracted,
    pose_landmarker,
    face_detector,
    output_root_dir=OUTPUT_ROOT_DIR,
    processed_inputs_dir=PROCESSED_INPUTS_DIR,
):
    """Run the full production batch for an already-classified, already-
    approved set of source images and write manifest.json — the exact
    logic main() has always run after approval, factored out so a
    non-interactive caller (headless.py) can run it too without
    duplicating it. Does not manage pose_landmarker/face_detector
    lifecycle (build/close) -- the caller owns that, since main() and a
    headless caller build/close them on different schedules.
    """
    run_id = make_run_id(output_root_dir)
    run_dir = os.path.join(output_root_dir, run_id)
    assets_dir = os.path.join(run_dir, ASSETS_DIRNAME)
    os.makedirs(assets_dir, exist_ok=True)

    generated_counts = {"human_model_front": 0, "flat_front": 0, "back": 0}
    framing_methods_used = {}
    assets_manifest = []
    errors = []

    for category in ("human_model_front", "flat_front", "back"):
        folder_name = CATEGORY_FOLDERS[category]

        for index, source_path in enumerate(grouped[category], start=1):
            output_filename = f"{folder_name}-{index:02d}.png"
            output_path = os.path.join(assets_dir, output_filename)

            try:
                framing_method = render_mockup_to_path(
                    category, source_path, background_path, output_path, pose_landmarker, face_detector
                )
                if framing_method is not None:
                    framing_methods_used[os.path.basename(source_path)] = framing_method
                generated_counts[category] += 1
                assets_manifest.append({
                    "filename": output_filename,
                    "path": "/".join((ASSETS_DIRNAME, output_filename)),
                    "category": category,
                    "source_filename": os.path.basename(source_path),
                    "framing_method": framing_method,
                    "background_filename": os.path.basename(background_path),
                })
            except Exception as exc:
                errors.append(f"{os.path.basename(source_path)}: {exc}")

    framing_method_counts = {}
    for method in framing_methods_used.values():
        framing_method_counts[method] = framing_method_counts.get(method, 0) + 1

    preview_categories_generated = [category for category in PREVIEW_CATEGORY_ORDER if category in previews]
    preview_sources = {category: previews[category]["source_filename"] for category in preview_categories_generated}

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "design_id": design_id,
        "zip_filename": os.path.basename(zip_path),
        "background_filename": os.path.basename(background_path),
        "preview_approval_status": "approved",
        "preview_categories_generated": preview_categories_generated,
        "preview_sources": preview_sources,
        "total_files_extracted": total_files_extracted,
        "category_counts": {category: len(grouped[category]) for category in grouped},
        "output_counts": generated_counts,
        "assets_dir": ASSETS_DIRNAME,
        "assets": assets_manifest,
        "errors": errors,
        "framing_method_counts": framing_method_counts,
    }
    manifest_path = os.path.join(run_dir, "manifest.json")
    with open(manifest_path, "w") as manifest_file:
        json.dump(manifest, manifest_file, indent=2)

    expected_vs_generated_ok = all(
        generated_counts[category] == len(grouped[category]) for category in generated_counts
    )
    run_succeeded = expected_vs_generated_ok and not errors
    manifest["run_succeeded"] = run_succeeded

    if run_succeeded:
        processed_input = move_processed_zip(zip_path, processed_inputs_dir, run_id)
    else:
        if errors:
            reason = "fatal errors present in this run"
        else:
            reason = "generated output counts did not match expected classification counts"
        processed_input = {
            "original_filename": os.path.basename(zip_path),
            "attempted": False,
            "move_succeeded": False,
            "method": None,
            "destination_path": None,
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
        }

    manifest["processed_input"] = processed_input
    with open(manifest_path, "w") as manifest_file:
        json.dump(manifest, manifest_file, indent=2)

    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "assets_dir": assets_dir,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "generated_counts": generated_counts,
        "errors": errors,
        "framing_methods_used": framing_methods_used,
        "processed_input": processed_input,
    }
```

- [ ] **Step 2: Replace `main()`'s inlined full-batch block with a call to `execute_full_batch()`**

Replace this block (currently right after `design_id = prompt_design_id()`, through the end of the original `finally:`/manifest-building code, i.e. everything from `# --- Approved: full batch generation ---` through the second `json.dump(manifest, manifest_file, indent=2)` call and the `manifest["processed_input"] = processed_input` line) with:

```python
        design_id = prompt_design_id()

        # --- Approved: full batch generation ---
        result = execute_full_batch(
            grouped, background_path, design_id, zip_path, previews, total_files_extracted,
            pose_landmarker, face_detector,
        )
    finally:
        if pose_landmarker is not None:
            pose_landmarker.close()
        if face_detector is not None:
            face_detector.close()

    run_id = result["run_id"]
    run_dir = result["run_dir"]
    assets_dir = result["assets_dir"]
    manifest_path = result["manifest_path"]
    generated_counts = result["generated_counts"]
    framing_methods_used = result["framing_methods_used"]
    errors = result["errors"]
    processed_input = result["processed_input"]
```

Everything below this in `main()` (the `# --- Report ---` print section) is unchanged — it already only reads `run_id, zip_path, background_path, run_dir, assets_dir, grouped, generated_counts, PRESETS, framing_methods_used, errors, processed_input, manifest_path`, all of which are now populated from `result` instead of locals computed inline.

- [ ] **Step 3: Verify the CLI still behaves identically**

Run:
```bash
cd ../etsy-mockup-generator && python -c "import batch_generate; print('OK: module imports, execute_full_batch' in dir(batch_generate) if False else hasattr(batch_generate, 'execute_full_batch'))"
```
Expected: `True` printed, no import errors.

Then run the real CLI end-to-end once with a real ZIP already in `input/` (or skip if none is staged there right now — this will be exercised for real in Task 9's live verification) to confirm `output/run-*/manifest.json` is produced with the same shape as before. If no ZIP is available yet, defer this specific check to Task 9 but still confirm the module imports cleanly and `python batch_generate.py` with an empty `input/` prints its normal "No ZIP file found" message and exits 0.

```bash
cd ../etsy-mockup-generator && python batch_generate.py
```
Expected: `No ZIP file found in input/` (assuming `input/` is currently empty), exit code 0.

- [ ] **Step 4: Commit**

```bash
cd ../etsy-mockup-generator && git add batch_generate.py && git commit -m "refactor: extract execute_full_batch() so a future headless caller can reuse it"
```

---

## Task 2: `headless.py` — the engine's new public, non-interactive interface

**Files:**
- Create: `../etsy-mockup-generator/headless.py`

**Interfaces:**
- Consumes: `batch_generate.execute_full_batch()` (Task 1); `scan_backgrounds`, `background_size_error`, `generate_category_previews`, `PREVIEW_CATEGORY_ORDER`, `BACKGROUNDS_DIR`, `OUTPUT_ROOT_DIR`, `PROCESSED_INPUTS_DIR` from `batch_generate.py`; `extract_zip` from `prepare_input.py`; `inspect_image, classify` from `classify_mockups.py`; `build_pose_landmarker_safe, build_face_detector_safe` from `human_framing.py`.
- Produces (consumed by Task 3's `_headless_worker.py`): `MockupEngineError(category, message, detail=None)`, `list_backgrounds() -> list[dict]`, `prepare_preview(zip_path, background_path, design_id=None) -> dict` (keys: `run_token, preview_artifacts, category_counts, total_files_extracted, background_filename`), `generate_full_batch(run_token) -> dict` (keys: `run_id, run_dir, manifest_path, assets_dir, generated_counts, errors, run_succeeded, manifest`).

Run state for the preview→batch bridge lives under a new `runs/<run_token>/` folder (sibling to `preview/`, `working/`) with its own `extracted/` and `preview/` subfolders — fully isolated per run token, so a later "choose a different background" preview never corrupts an in-flight run token (see design spec Section 1).

- [ ] **Step 1: Write `headless.py`**

```python
"""Non-interactive public interface for etsy-mockup-generator, additive to
batch_generate.py's interactive CLI (main()) -- that CLI is completely
unchanged and keeps working exactly as before. This module exists purely
so an external caller (the Automation Controller) can drive the same
extract -> classify -> preview -> approve -> full-batch pipeline without
answering input() prompts or scraping stdout.

Every public function here raises MockupEngineError instead of calling
sys.exit() or printing and returning None, and takes an explicit
zip_path/background_path rather than scanning the shared input/
folder -- callers decide what ZIP and background to use; this module
never assumes "the one ZIP in input/" the way the CLI does.

Preview -> batch bridging: prepare_preview() persists everything
generate_full_batch() needs (grouped classified file lists, chosen
background, design id) under runs/<run_token>/state.json, with its OWN
runs/<run_token>/extracted/ and runs/<run_token>/preview/ subfolders --
never the shared working/extracted or project-root preview/ folders the
CLI uses -- so two overlapping preview calls (e.g. "choose a different
background") can never corrupt each other's state. Exactly how that
bridging works is a private detail of this module -- a caller only ever
holds an opaque run_token string.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import uuid
from typing import Any

from batch_generate import (
    BACKGROUNDS_DIR,
    OUTPUT_ROOT_DIR,
    PROCESSED_INPUTS_DIR,
    PREVIEW_CATEGORY_ORDER,
    background_size_error,
    execute_full_batch,
    generate_category_previews,
    scan_backgrounds,
)
from classify_mockups import classify, inspect_image
from human_framing import build_face_detector_safe, build_pose_landmarker_safe
from prepare_input import extract_zip

RUNS_DIR = "runs"


class MockupEngineError(Exception):
    """Raised for any expected, reportable failure -- an invalid zip, a
    missing/wrong-size background, an empty archive, a stale run_token,
    etc. Never raised for an unexpected internal crash (those propagate
    as whatever exception they actually are; the Controller-side adapter
    is responsible for wrapping any exception generically at its own
    boundary)."""

    def __init__(self, category: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = detail or {}


def list_backgrounds() -> list[dict[str, Any]]:
    """Every supported background image in backgrounds/, with usability
    pre-checked (via the same background_size_error() the CLI uses at
    selection time) so a caller never has to guess why a background is
    disabled."""
    backgrounds = []
    for path in scan_backgrounds(BACKGROUNDS_DIR):
        reason = background_size_error(path)
        name, _ext = os.path.splitext(os.path.basename(path))
        backgrounds.append({
            "name": name,
            "path": os.path.abspath(path),
            "usable": reason is None,
            "reason": reason,
        })
    return backgrounds


def _validate_zip(zip_path: str) -> None:
    if not os.path.isfile(zip_path):
        raise MockupEngineError("invalid_zip", f"No file found at {zip_path!r}.", {"zip_path": zip_path})
    import zipfile
    if not zipfile.is_zipfile(zip_path):
        raise MockupEngineError("invalid_zip", f"{zip_path!r} is not a valid ZIP archive.", {"zip_path": zip_path})


def _validate_background(background_path: str) -> None:
    if not os.path.isfile(background_path):
        raise MockupEngineError(
            "invalid_background", f"No background image found at {background_path!r}.", {"background_path": background_path}
        )
    reason = background_size_error(background_path)
    if reason:
        raise MockupEngineError(
            "invalid_background",
            f"{os.path.basename(background_path)} {reason}.",
            {"background_path": background_path},
        )


def prepare_preview(zip_path: str, background_path: str, design_id: str | None = None) -> dict[str, Any]:
    """Validate, extract, classify, and render one representative preview
    per present category plus a contact sheet -- through the exact same
    render_mockup_to_path() the full batch uses (via
    generate_category_previews()). Returns a fresh, opaque run_token the
    caller must pass unmodified to generate_full_batch() to continue this
    same run."""
    _validate_zip(zip_path)
    _validate_background(background_path)

    run_token = uuid.uuid4().hex
    run_dir = os.path.join(RUNS_DIR, run_token)
    extracted_dir = os.path.join(run_dir, "extracted")
    preview_dir = os.path.join(run_dir, "preview")
    os.makedirs(run_dir, exist_ok=True)

    extraction = extract_zip(zip_path, extracted_dir=extracted_dir)
    total_files_extracted = len(extraction["extracted_names"])

    paths = sorted(glob.glob(os.path.join(extracted_dir, "*.png")))
    if not paths:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise MockupEngineError(
            "empty_archive", "No PNG files were found inside the uploaded ZIP.", {"zip_path": zip_path}
        )

    grouped = {"human_model_front": [], "flat_front": [], "back": [], "unknown": []}
    for path in paths:
        info = inspect_image(path)
        category = classify(info)
        grouped[category].append(path)

    if not any(grouped[category] for category in PREVIEW_CATEGORY_ORDER):
        shutil.rmtree(run_dir, ignore_errors=True)
        raise MockupEngineError(
            "no_classifiable_images",
            "None of the supported categories (flat_front, human_model_front, back) were found in this ZIP.",
            {"category_counts": {category: len(grouped[category]) for category in grouped}},
        )

    pose_landmarker = build_pose_landmarker_safe()
    face_detector = build_face_detector_safe()
    try:
        previews, contact_sheet_path = generate_category_previews(
            grouped, background_path, preview_dir, pose_landmarker, face_detector
        )
    finally:
        if pose_landmarker is not None:
            pose_landmarker.close()
        if face_detector is not None:
            face_detector.close()

    state = {
        "zip_path": zip_path,
        "background_path": background_path,
        "design_id": design_id,
        "grouped": grouped,
        "total_files_extracted": total_files_extracted,
    }
    with open(os.path.join(run_dir, "state.json"), "w") as state_file:
        json.dump(state, state_file, indent=2)

    preview_artifacts = []
    if contact_sheet_path:
        preview_artifacts.append({
            "kind": "contact_sheet",
            "path": os.path.abspath(contact_sheet_path),
            "representative": True,
            "label": "Contact sheet",
        })
    for category in PREVIEW_CATEGORY_ORDER:
        if category not in previews:
            continue
        info = previews[category]
        preview_artifacts.append({
            "kind": "category_preview",
            "path": os.path.abspath(info["preview_path"]),
            "category": category,
            "source_filename": info["source_filename"],
            "representative": contact_sheet_path is None and category == PREVIEW_CATEGORY_ORDER[0],
            "label": category.replace("_", " "),
        })

    return {
        "run_token": run_token,
        "preview_artifacts": preview_artifacts,
        "category_counts": {category: len(grouped[category]) for category in grouped},
        "total_files_extracted": total_files_extracted,
        "background_filename": os.path.basename(background_path),
    }


def generate_full_batch(run_token: str) -> dict[str, Any]:
    """Continue the run referenced by run_token (as returned by
    prepare_preview()) into full-batch generation, using the exact same
    execute_full_batch() the CLI itself calls after approval."""
    run_dir = os.path.join(RUNS_DIR, run_token)
    state_path = os.path.join(run_dir, "state.json")
    if not os.path.isfile(state_path):
        raise MockupEngineError(
            "run_token_not_found",
            f"No prepared run found for run_token={run_token!r} -- it may have already been used, "
            "or superseded by a newer preview.",
            {"run_token": run_token},
        )

    with open(state_path) as state_file:
        state = json.load(state_file)

    grouped = state["grouped"]
    missing = [path for paths in grouped.values() for path in paths if not os.path.isfile(path)]
    if missing:
        raise MockupEngineError(
            "run_expired",
            "One or more source files for this run are missing on disk -- this run_token may have "
            "been superseded by a newer preview for the same ZIP.",
            {"missing_count": len(missing)},
        )

    background_path = state["background_path"]
    design_id = state.get("design_id")
    zip_path = state["zip_path"]
    total_files_extracted = state["total_files_extracted"]

    # previews={} here is deliberate: execute_full_batch() only reads
    # `previews` to populate the manifest's preview_categories_generated/
    # preview_sources fields, which describe the PREVIEW step already
    # completed and recorded by prepare_preview() -- recomputing preview
    # metadata here would be redundant, and an empty dict simply yields
    # empty lists for those two manifest fields, which is accurate: no
    # preview images were (re)generated during this call.
    pose_landmarker = build_pose_landmarker_safe()
    face_detector = build_face_detector_safe()
    try:
        result = execute_full_batch(
            grouped, background_path, design_id, zip_path, previews={}, total_files_extracted=total_files_extracted,
            pose_landmarker=pose_landmarker, face_detector=face_detector,
            output_root_dir=OUTPUT_ROOT_DIR, processed_inputs_dir=PROCESSED_INPUTS_DIR,
        )
    finally:
        if pose_landmarker is not None:
            pose_landmarker.close()
        if face_detector is not None:
            face_detector.close()

    shutil.rmtree(run_dir, ignore_errors=True)

    return {
        "run_id": result["run_id"],
        "run_dir": result["run_dir"],
        "manifest_path": result["manifest_path"],
        "assets_dir": result["assets_dir"],
        "generated_counts": result["generated_counts"],
        "errors": result["errors"],
        "run_succeeded": result["manifest"]["run_succeeded"],
        "manifest": result["manifest"],
    }
```

- [ ] **Step 2: Verify with a real ZIP, in-process**

Requires at least one real Printful-style ZIP available locally and at least one background already in `backgrounds/` (both should already exist in this repo from prior manual testing — check `ls backgrounds/` and `ls test-assets/` first; use whatever real ZIP is available, e.g. from `test-assets/`).

```bash
cd ../etsy-mockup-generator && python -c "
import headless, json
backgrounds = headless.list_backgrounds()
print('backgrounds:', json.dumps(backgrounds, indent=2))
assert backgrounds, 'expected at least one background'
usable = [b for b in backgrounds if b['usable']]
assert usable, 'expected at least one usable background'
"
```
Expected: prints a non-empty JSON list, no assertion errors.

Then, with a real zip path substituted for `ZIP_PATH_HERE`:
```bash
cd ../etsy-mockup-generator && python -c "
import headless, json
backgrounds = headless.list_backgrounds()
bg = next(b for b in backgrounds if b['usable'])
result = headless.prepare_preview('ZIP_PATH_HERE', bg['path'], design_id='test-design-1')
print(json.dumps(result, indent=2))
assert result['run_token']
assert any(a['representative'] for a in result['preview_artifacts'])
batch = headless.generate_full_batch(result['run_token'])
print(json.dumps({k: v for k, v in batch.items() if k != 'manifest'}, indent=2))
assert batch['run_succeeded'] is True
"
```
Expected: two JSON dumps print, both `assert` blocks pass, `output/run-<date>-<seq>/manifest.json` and `assets/` exist on disk afterward with the expected mockups.

- [ ] **Step 3: Commit**

```bash
cd ../etsy-mockup-generator && git add headless.py && git commit -m "feat: add headless.py, a non-interactive public interface for Controller integration"
```

---

## Task 3: `MockupGeneratorAdapter` — real `launch()`/`collect_results()` via a subprocess worker

**Files:**
- Create: `infra/adapters/mockup_generator/_headless_worker.py`
- Modify: `infra/adapters/mockup_generator/adapter.py`

**Interfaces:**
- Consumes: `headless.list_backgrounds/prepare_preview/generate_full_batch/MockupEngineError` (Task 2, imported by file path exactly like `_load_engine_module()` does for `generate_video.py`).
- Produces: `MockupGeneratorAdapter.launch(config, on_run_reference=None) -> EngineRunReference` where `config = {"phase": "preview", "zip_path": str, "background_path": str, "design_id": str | None}` or `{"phase": "batch", "run_token": str}`; `MockupGeneratorAdapter.collect_results(ref) -> EngineResult` whose `artifacts` dict is exactly the corresponding `headless.py` function's return dict (plus `phase` echoed back in for the API routes to key off). `list_mockup_backgrounds() -> list[dict]` module-level helper, consumed directly by Task 5's routes (bypasses the Job/adapter.launch() machinery entirely — pure read, no run to track).

- [ ] **Step 1: Write `_headless_worker.py`**

```python
"""Standalone worker process for MockupGeneratorAdapter.launch(). Mirrors
infra/adapters/video_generator/_launch_worker.py's shape: spawned as its
own OS process so a hang in mediapipe never blocks the Controller's own
process, cwd set to the engine repo root by the caller (Popen(cwd=...))
so headless.py's/batch_generate.py's relative-path constants (backgrounds/,
output/, working/, preview/, processed-inputs/, runs/) resolve exactly as
they do for the interactive CLI.

Usage: `python _headless_worker.py <spec_path> <result_path>`
  spec_path: JSON {"repo_root": str, "phase": "preview"|"batch", ...phase-specific fields}
  result_path: JSON {"success": true, "result": {...}} or
               {"success": false, "error": {"category": str, "message": str, "detail": dict}}
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_headless_module(repo_root: Path):
    module_path = repo_root / "headless.py"
    spec = importlib.util.spec_from_file_location("_etsy_mockup_generator_headless_worker", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build an import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(repo_root))  # headless.py imports sibling modules (batch_generate, etc.) by bare name
    spec.loader.exec_module(module)
    return module


def main() -> int:
    spec_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])

    try:
        job_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        repo_root = Path(job_spec["repo_root"])
        phase = job_spec["phase"]

        headless = _load_headless_module(repo_root)

        try:
            if phase == "preview":
                result = headless.prepare_preview(
                    job_spec["zip_path"], job_spec["background_path"], job_spec.get("design_id")
                )
            elif phase == "batch":
                result = headless.generate_full_batch(job_spec["run_token"])
            else:
                raise RuntimeError(f"Unknown phase: {phase!r}")
        except headless.MockupEngineError as exc:
            result_path.write_text(
                json.dumps({"success": False, "error": {"category": exc.category, "message": exc.message, "detail": exc.detail}}),
                encoding="utf-8",
            )
            return 0  # the worker itself ran fine; the ENGINE reported a failure -- distinct outcomes

        result_path.write_text(json.dumps({"success": True, "result": result}), encoding="utf-8")
        return 0

    except Exception as exc:  # noqa: BLE001 - a worker-process-level crash, not an engine-level failure
        try:
            result_path.write_text(
                json.dumps({"success": False, "error": {"category": "worker_process_crashed", "message": f"{type(exc).__name__}: {exc}", "detail": {}}}),
                encoding="utf-8",
            )
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Rewrite `adapter.py`**

Replace the entire contents of `infra/adapters/mockup_generator/adapter.py` with:

```python
"""MockupGeneratorAdapter -- launch()/collect_results() now real, via a
subprocess worker calling headless.py (etsy-mockup-generator's new
Step-13 public interface). discover()/validate() updated to reflect that
launch() is no longer stubbed.

Two-phase workflow (design spec 2026-07-23, Section 3): the Controller
never calls launch() expecting a single "start to finish" run the way
VideoGeneratorAdapter does. Instead two separate Jobs are created against
this engine -- config={"phase": "preview", ...} then, after operator
approval, config={"phase": "batch", "run_token": ...} -- each a complete,
synchronous launch()+collect_results() cycle through the same
ExecutionCoordinator.evaluate() bridge every engine already uses. `phase`
is an adapter-private config key, invisible to the Core, exactly like
`preset_key` is for VideoGeneratorAdapter.

Subprocess worker rationale: identical to VideoGeneratorAdapter (see that
adapter's module docstring) -- mediapipe pose/face detection is real,
possibly slow, CPU-bound work; running it in a process WE spawn and fully
control keeps a stall or crash there from ever affecting the Controller's
own process. Unlike the video adapter, this repo's own relative-path
assumptions (backgrounds/, output/, working/, preview/, processed-inputs/,
runs/) require the worker's cwd to actually BE the engine repo root --
Popen(cwd=repo_root) below, not just an import by file path.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.adapters.engine_adapter import (
    BaseEngineAdapter,
    CancelSupport,
    EngineCapabilities,
    EngineLaunchError,
    EngineResult,
    EngineStatus,
    ImplementationStatus,
    OnRunReference,
    ValidationResult,
)
from core.domain.engine import EngineRunReference
from infra.adapters.discovery_utils import read_text_safely, resolve_engine_repo_root

ENGINE_ID = "etsy-mockup-generator"
REPO_DIR_NAME = "etsy-mockup-generator"
_WORKER_SCRIPT_PATH = Path(__file__).resolve().parent / "_headless_worker.py"
_VALID_PHASES = {"preview", "batch"}
DEFAULT_WORKER_TIMEOUT_SECONDS = 300.0  # generous: mediapipe model load + a full batch of renders


def list_mockup_backgrounds() -> list[dict[str, Any]]:
    """Direct, synchronous read -- not a Job, not a launch(). Runs
    headless.list_backgrounds() in the same kind of worker subprocess
    (cwd=repo_root) so it never depends on the Controller's own process
    having Pillow importable in a particular way, and stays consistent
    with how every other engine call in this adapter is made. Raises
    EngineLaunchError on any failure (unreachable repo, worker crash),
    for api/mockup_generator_routes.py to translate into an HTTP error.
    """
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    if repo_root is None:
        raise EngineLaunchError(
            {"category": "engine_unreachable", "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.", "detail": {}}
        )
    outcome = _run_worker(repo_root, {"repo_root": str(repo_root), "phase": "list_backgrounds"}, timeout_seconds=30.0)
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def _run_worker(repo_root: Path, job_spec: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    work_dir = Path(tempfile.mkdtemp(prefix="automation_controller_mockup_launch_"))
    spec_path = work_dir / "spec.json"
    result_path = work_dir / "result.json"
    spec_path.write_text(json.dumps(job_spec), encoding="utf-8")

    try:
        process = subprocess.run(
            [sys.executable, str(_WORKER_SCRIPT_PATH), str(spec_path), str(result_path)],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": {
                "category": "launch_timeout",
                "message": f"etsy-mockup-generator did not complete within {timeout_seconds:.0f} seconds.",
                "detail": {"timeout_seconds": timeout_seconds},
            },
        }
    finally:
        pass

    if not result_path.is_file():
        try:
            work_dir.rmdir()
        except OSError:
            pass
        return {
            "success": False,
            "error": {
                "category": "worker_no_result",
                "message": "Worker process exited without writing a result file.",
                "detail": {"worker_returncode": process.returncode},
            },
        }

    outcome = json.loads(result_path.read_text(encoding="utf-8"))
    import shutil
    shutil.rmtree(work_dir, ignore_errors=True)
    return outcome


class MockupGeneratorAdapter(BaseEngineAdapter):
    engine_id = ENGINE_ID

    def discover(self) -> EngineCapabilities:
        """Grounded, read-only inspection -- now checks for headless.py's
        three public functions (Step 13's new headless seam) rather than
        the old interactive-CLI-only markers.
        """
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        repo_exists = repo_root is not None

        headless_text = read_text_safely(repo_root / "headless.py") if repo_root else None
        has_prepare_preview = bool(headless_text and "def prepare_preview(" in headless_text)
        has_generate_full_batch = bool(headless_text and "def generate_full_batch(" in headless_text)
        has_list_backgrounds = bool(headless_text and "def list_backgrounds(" in headless_text)

        supports_launch = has_prepare_preview and has_generate_full_batch
        supports_monitoring = supports_launch  # collect_results() reads the worker's own result, not a poll

        notes: list[str] = []
        if not repo_exists:
            health_status = "unreachable"
            notes.append(f"Sibling repo '{REPO_DIR_NAME}' was not found on disk during discovery.")
        elif supports_launch and has_list_backgrounds:
            health_status = "healthy"
            notes.append(
                "launch() is real (Step 13): two phases, config={'phase': 'preview'|'batch', ...}, each run in a "
                "supervised worker subprocess (cwd=engine repo root)."
            )
        else:
            health_status = "degraded"
            notes.append("headless.py did not contain the expected prepare_preview()/generate_full_batch() markers.")

        implementation_status = ImplementationStatus.REAL if supports_launch else ImplementationStatus.STUB

        return EngineCapabilities(
            engine_id=ENGINE_ID,
            display_name="Etsy Mockup Generator",
            version=None,
            supports_launch=supports_launch,
            cancel_support=CancelSupport.UNSUPPORTED,
            supports_monitoring=supports_monitoring,
            supports_approvals=False,
            supports_pipelines=False,
            max_concurrent_runs=1,
            launch_config_schema=(
                {
                    "phase": "str -- 'preview' or 'batch'",
                    "zip_path": "str -- absolute path to a staged ZIP (phase=preview only)",
                    "background_path": "str -- absolute path to a background image (phase=preview only)",
                    "design_id": "str | None -- optional operator reference (phase=preview only)",
                    "run_token": "str -- opaque token from a prior phase=preview result (phase=batch only)",
                }
                if supports_launch
                else {}
            ),
            possible_approval_types=[],
            health_status=health_status,
            implementation_status=implementation_status,
            notes=notes,
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        errors: list[str] = []
        phase = config.get("phase")
        if phase not in _VALID_PHASES:
            errors.append(f"'phase' must be one of {sorted(_VALID_PHASES)}, got {phase!r}")
        elif phase == "preview":
            if not config.get("zip_path"):
                errors.append("'zip_path' is required for phase='preview'")
            elif not Path(config["zip_path"]).is_file():
                errors.append(f"zip_path does not exist on disk: {config['zip_path']!r}")
            if not config.get("background_path"):
                errors.append("'background_path' is required for phase='preview'")
            elif not Path(config["background_path"]).is_file():
                errors.append(f"background_path does not exist on disk: {config['background_path']!r}")
        elif phase == "batch":
            if not config.get("run_token"):
                errors.append("'run_token' is required for phase='batch'")

        if resolve_engine_repo_root(REPO_DIR_NAME) is None:
            errors.append(f"{ENGINE_ID}: sibling repo not found on disk")

        return ValidationResult(is_valid=not errors, errors=errors)

    def launch(
        self, config: dict[str, Any], on_run_reference: OnRunReference | None = None
    ) -> EngineRunReference:
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        if repo_root is None:
            raise EngineLaunchError(
                {"category": "engine_unreachable", "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.", "detail": {}}
            )

        phase = config["phase"]
        job_spec: dict[str, Any] = {"repo_root": str(repo_root), "phase": phase}
        if phase == "preview":
            job_spec["zip_path"] = config["zip_path"]
            job_spec["background_path"] = config["background_path"]
            job_spec["design_id"] = config.get("design_id")
        else:
            job_spec["run_token"] = config["run_token"]

        outcome = _run_worker(repo_root, job_spec, timeout_seconds=DEFAULT_WORKER_TIMEOUT_SECONDS)
        if not outcome["success"]:
            raise EngineLaunchError(outcome["error"])

        result = outcome["result"]
        result["phase"] = phase
        return EngineRunReference(
            engine_id=ENGINE_ID,
            reference_type="headless_result",
            reference_value=phase,
            created_at=datetime.now(timezone.utc),
            extra=result,
        )

    def monitor(self, ref: EngineRunReference) -> EngineStatus:
        """Not needed: each phase's launch() blocks in-process (via
        subprocess.run(), not Popen+poll) until that phase is fully
        terminal, exactly like VideoGeneratorAdapter's synchronous
        collect_results() path."""
        raise NotImplementedError

    def collect_results(self, ref: EngineRunReference) -> EngineResult:
        """Real: launch() already ran the worker to completion and stashed
        its full result dict on ref.extra -- this just re-shapes it into
        an EngineResult, no second read."""
        return EngineResult(success=True, artifacts=dict(ref.extra), error=None)
```

- [ ] **Step 3: Verify with a real ZIP, through the adapter (not the CLI import path)**

```bash
cd automation-controller && python -c "
from infra.adapters.mockup_generator.adapter import MockupGeneratorAdapter, list_mockup_backgrounds
adapter = MockupGeneratorAdapter()
caps = adapter.discover()
print('supports_launch:', caps.supports_launch, 'health:', caps.health_status)
assert caps.supports_launch

backgrounds = list_mockup_backgrounds()
print('backgrounds:', backgrounds)
usable_bg = next(b for b in backgrounds if b['usable'])

ref = adapter.launch({'phase': 'preview', 'zip_path': 'ZIP_PATH_HERE', 'background_path': usable_bg['path'], 'design_id': 'adapter-test'})
result = adapter.collect_results(ref)
print('preview result:', {k: v for k, v in result.artifacts.items() if k != 'preview_artifacts'})
assert result.success
run_token = result.artifacts['run_token']

ref2 = adapter.launch({'phase': 'batch', 'run_token': run_token})
result2 = adapter.collect_results(ref2)
print('batch result:', {k: v for k, v in result2.artifacts.items() if k != 'manifest'})
assert result2.success
assert result2.artifacts['run_succeeded'] is True
"
```
Expected: both phases succeed, no assertion errors, a new `output/run-*/` folder appears in the engine repo.

- [ ] **Step 4: Commit**

```bash
git add infra/adapters/mockup_generator/adapter.py infra/adapters/mockup_generator/_headless_worker.py
git commit -m "feat: real launch()/collect_results() for MockupGeneratorAdapter via a subprocess headless worker"
```

---

## Task 4: ZIP upload staging — `infra/storage/mockup_generator_staging.py`

**Files:**
- Create: `infra/storage/mockup_generator_staging.py`

**Interfaces:**
- Produces: `StagingValidationError(errors: list[str])`, `stage_uploaded_zip(filename: str, content: bytes) -> tuple[Path, Path]` (returns `(staging_dir, zip_path)`), `mark_job_id(staging_dir, job_id)`, `cleanup_staging_dir(staging_dir)`, `sweep_terminal_staging_dirs(get_job)` — same shapes as `video_generator_staging.py`, consumed by Task 5's routes.

- [ ] **Step 1: Write the staging module**

```python
"""Controller-owned staging for etsy-mockup-generator ZIP uploads. Mirrors
infra/storage/video_generator_staging.py's lifecycle exactly (one staging
dir per upload, job_id.txt marker, terminal-status sweep) -- see that
module's docstring for the full rationale, unchanged here. The one
difference: a single ZIP file per staging dir instead of an ordered batch
of images.

Never touches or deletes the operator's original ZIP on their machine --
the browser uploads bytes once; this module writes and owns its own copy.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.domain.job import Job

STAGING_ROOT = Path(__file__).resolve().parents[2] / "var" / "staging" / "mockup_generator"

_MAX_ZIP_BYTES = 200 * 1024 * 1024  # 200MB -- generous for a Printful mockup export batch
_ZIP_MAGIC = b"PK\x03\x04"
_ORPHAN_GRACE_SECONDS = 5 * 60
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class StagingValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _sanitize_filename(original_name: str) -> str:
    base = Path(original_name or "upload.zip").name
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in base)
    return safe or "upload.zip"


def stage_uploaded_zip(filename: str, content: bytes) -> tuple[Path, Path]:
    """Validate and write one uploaded ZIP into a brand-new staging
    directory. Returns (staging_dir, zip_path). Raises
    StagingValidationError (no directory left behind) if the upload isn't
    a real, non-empty ZIP under the size cap.
    """
    errors: list[str] = []
    if not content:
        errors.append("uploaded file is empty")
    elif len(content) > _MAX_ZIP_BYTES:
        errors.append(f"uploaded file is too large ({len(content)} bytes, max {_MAX_ZIP_BYTES})")
    if not filename.lower().endswith(".zip"):
        errors.append(f"{filename!r} is not a .zip file")
    if content[:4] != _ZIP_MAGIC:
        errors.append(f"{filename!r} does not look like a valid ZIP archive")
    if errors:
        raise StagingValidationError(errors)

    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    staging_dir = STAGING_ROOT / str(uuid.uuid4())
    staging_dir.mkdir(parents=True, exist_ok=False)

    zip_path = staging_dir / _sanitize_filename(filename)
    zip_path.write_bytes(content)
    return staging_dir, zip_path


def mark_job_id(staging_dir: Path, job_id: str) -> None:
    (staging_dir / "job_id.txt").write_text(job_id, encoding="utf-8")


def cleanup_staging_dir(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)


def sweep_terminal_staging_dirs(get_job) -> None:
    if not STAGING_ROOT.is_dir():
        return
    for entry in STAGING_ROOT.iterdir():
        if not entry.is_dir():
            continue
        marker = entry / "job_id.txt"
        if not marker.is_file():
            if time.time() - entry.stat().st_mtime > _ORPHAN_GRACE_SECONDS:
                cleanup_staging_dir(entry)
            continue
        job_id = marker.read_text(encoding="utf-8").strip()
        job: "Job | None" = get_job(job_id)
        if job is None or job.status.value in _TERMINAL_STATUSES:
            cleanup_staging_dir(entry)
```

- [ ] **Step 2: Verify**

```bash
cd automation-controller && python -c "
from infra.storage.mockup_generator_staging import stage_uploaded_zip, StagingValidationError, cleanup_staging_dir
try:
    stage_uploaded_zip('notes.txt', b'hello')
    raise SystemExit('expected StagingValidationError')
except StagingValidationError as exc:
    print('rejected non-zip correctly:', exc.errors)

zip_bytes = b'PK\x03\x04' + b'\x00' * 40
staging_dir, zip_path = stage_uploaded_zip('mockups.zip', zip_bytes)
assert zip_path.is_file()
print('staged at', zip_path)
cleanup_staging_dir(staging_dir)
assert not staging_dir.exists()
print('cleanup OK')
"
```
Expected: both print statements succeed, no unhandled exceptions.

- [ ] **Step 3: Commit**

```bash
git add infra/storage/mockup_generator_staging.py
git commit -m "feat: add Controller-owned ZIP upload staging for the Mockup Generator"
```

---

## Task 5: `api/mockup_generator_routes.py` + registration in `api/main.py`

**Files:**
- Create: `api/mockup_generator_routes.py`
- Modify: `api/main.py` (add import + `app.include_router(...)`, mirroring the video generator's two existing lines)

**Interfaces:**
- Consumes: `list_mockup_backgrounds` (Task 3); `stage_uploaded_zip, mark_job_id, cleanup_staging_dir, sweep_terminal_staging_dirs, StagingValidationError` (Task 4); `get_job_service, get_execution_coordinator` (existing `api/dependencies.py`); `resolve_engine_repo_root` (existing `discovery_utils.py`).
- Produces: the six routes listed in the design spec Section 5, consumed by Task 7/8's frontend.

- [ ] **Step 1: Write the routes module**

```python
"""etsy-mockup-generator operator workflow routes (Step 13). Scoped to
this one engine, mirroring api/video_generator_routes.py's shape exactly:
this module owns turning a browser ZIP upload into a staged file path,
listing backgrounds, and safe, narrowly-scoped access to this engine's
own preview/output artifacts -- it never duplicates or bypasses
JobService.create_job() / ExecutionCoordinator.evaluate(), the same pair
every other engine's routes use.

Two-phase workflow: POST .../jobs/preview creates+launches a
config={"phase": "preview", ...} Job; once that Job succeeds, POST
.../jobs/{id}/batch creates+launches a config={"phase": "batch",
"run_token": ...} Job using the run_token from the preview Job's own
result_summary -- never a client-supplied run_token, so a client can only
ever continue a run it was actually just shown.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from api.dependencies import get_execution_coordinator, get_job_service
from core.domain.job import JobStatus
from core.execution.coordinator import ExecutionCoordinator
from core.services.job_service import JobNotFoundError, JobService, JobValidationError
from infra.adapters.discovery_utils import resolve_engine_repo_root
from infra.adapters.mockup_generator.adapter import list_mockup_backgrounds
from infra.storage.mockup_generator_staging import (
    StagingValidationError,
    cleanup_staging_dir,
    mark_job_id,
    stage_uploaded_zip,
    sweep_terminal_staging_dirs,
)

ENGINE_ID = "etsy-mockup-generator"
REPO_DIR_NAME = "etsy-mockup-generator"

router = APIRouter()


@router.get("/mockup-generator/backgrounds")
def list_backgrounds() -> list[dict]:
    try:
        return list_mockup_backgrounds()
    except Exception as exc:  # noqa: BLE001 -- EngineLaunchError from the adapter, or a genuine crash
        raise HTTPException(status_code=502, detail=f"Could not list backgrounds: {exc}") from exc


def _require_mockup_job(job_id: str, service: JobService):
    job = service.get_job(job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    if job.engine_id != ENGINE_ID:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} does not belong to {ENGINE_ID!r}")
    return job


def _validated_artifact_path(raw_path: str, expected_subdir_names: tuple[str, ...]) -> Path:
    """The one place a Job's own recorded path turns into something
    served over HTTP -- always re-validated against the engine's real
    repo root before use, never trusted blindly."""
    path = Path(raw_path).resolve()
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    if repo_root is None:
        raise HTTPException(status_code=502, detail="Engine repo is not reachable.")
    repo_root = repo_root.resolve()
    if repo_root not in path.parents:
        raise HTTPException(status_code=409, detail="Recorded artifact path is not inside the engine's repo.")
    if not any(part in expected_subdir_names for part in path.parts):
        raise HTTPException(status_code=409, detail="Recorded artifact path is not inside an expected output directory.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file no longer exists on disk.")
    return path


@router.get("/mockup-generator/jobs/{job_id}/preview-image")
def get_preview_image(job_id: str, service: JobService = Depends(get_job_service)):
    job = _require_mockup_job(job_id, service)
    if job.status != JobStatus.SUCCEEDED or not job.result_summary or job.result_summary.get("phase") != "preview":
        raise HTTPException(status_code=409, detail="Job has no preview yet (not a succeeded preview job).")
    artifacts = job.result_summary.get("preview_artifacts", [])
    representative = next((a for a in artifacts if a.get("representative")), None) or (artifacts[0] if artifacts else None)
    if representative is None:
        raise HTTPException(status_code=409, detail="Preview job result has no preview artifacts.")
    path = _validated_artifact_path(representative["path"], ("runs",))
    return FileResponse(path, media_type="image/png")


@router.get("/mockup-generator/jobs/{job_id}/result-image")
def get_result_image(job_id: str, service: JobService = Depends(get_job_service)):
    job = _require_mockup_job(job_id, service)
    if job.status != JobStatus.SUCCEEDED or not job.result_summary or job.result_summary.get("phase") != "batch":
        raise HTTPException(status_code=409, detail="Job has no result yet (not a succeeded batch job).")
    assets = job.result_summary.get("manifest", {}).get("assets", [])
    if not assets:
        raise HTTPException(status_code=409, detail="Batch job result has no generated assets.")
    run_dir = job.result_summary.get("run_dir")
    if not run_dir:
        raise HTTPException(status_code=409, detail="Batch job result has no run_dir.")
    path = _validated_artifact_path(os.path.join(run_dir, assets[0]["path"]), ("output",))
    return FileResponse(path, media_type="image/png")


def _resolve_run_dir(job) -> Path:
    if job.status != JobStatus.SUCCEEDED or not job.result_summary or job.result_summary.get("phase") != "batch":
        raise HTTPException(status_code=409, detail="Job has no output yet (not a succeeded batch job).")
    run_dir = job.result_summary.get("run_dir")
    if not run_dir:
        raise HTTPException(status_code=409, detail="Batch job result has no run_dir.")
    path = Path(run_dir).resolve()
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    engine_output_dir = (repo_root / "output").resolve() if repo_root else None
    if engine_output_dir is None or engine_output_dir not in path.parents:
        raise HTTPException(status_code=409, detail="Recorded run_dir is not inside the engine's output directory.")
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Run output folder no longer exists on disk.")
    return path


@router.post("/jobs/{job_id}/open-output-folder")
def open_mockup_output_folder(job_id: str, service: JobService = Depends(get_job_service)) -> dict[str, str]:
    job = _require_mockup_job(job_id, service)
    folder = _resolve_run_dir(job)
    os.startfile(str(folder))  # noqa: S606 -- local desktop app, user-triggered, path validated above
    return {"opened": str(folder)}


@router.post("/jobs/{job_id}/open-listing-outputs")
def open_mockup_listing_outputs(job_id: str, service: JobService = Depends(get_job_service)) -> dict[str, str]:
    job = _require_mockup_job(job_id, service)
    folder = _resolve_run_dir(job)
    os.startfile(str(folder))  # noqa: S606
    return {"opened": str(folder)}


def _launch_and_report(job_service: JobService, coordinator: ExecutionCoordinator, engine_id: str, config: dict):
    job = job_service.create_job(engine_id=engine_id, config=config)
    coordinator.evaluate(engine_id)
    return job_service.get_job(job.id)


@router.post("/mockup-generator/jobs/preview")
async def launch_preview_job(
    background_path: str = Form(...),
    design_id: str | None = Form(None),
    zip_file: UploadFile | None = File(None),
    staged_zip_path: str | None = Form(None),
    job_service: JobService = Depends(get_job_service),
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
):
    """First call for a new ZIP: send zip_file (multipart). A rerun with a
    different background_path for the SAME upload: send staged_zip_path
    (the path this route returns the first time, via the created Job's
    config) instead of re-uploading the file.
    """
    sweep_terminal_staging_dirs(job_service.get_job)

    if zip_file is not None:
        content = await zip_file.read()
        try:
            staging_dir, zip_path = stage_uploaded_zip(zip_file.filename or "upload.zip", content)
        except StagingValidationError as exc:
            raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc
    elif staged_zip_path:
        staging_dir = None
        zip_path = Path(staged_zip_path)
        if not zip_path.is_file():
            raise HTTPException(status_code=422, detail={"errors": [f"staged_zip_path no longer exists: {staged_zip_path!r}"]})
    else:
        raise HTTPException(status_code=422, detail={"errors": ["either zip_file or staged_zip_path is required"]})

    config = {"phase": "preview", "zip_path": str(zip_path), "background_path": background_path, "design_id": design_id or None}

    try:
        job = job_service.create_job(engine_id=ENGINE_ID, config=config)
    except JobValidationError as exc:
        if staging_dir is not None:
            cleanup_staging_dir(staging_dir)
        raise HTTPException(status_code=422, detail={"engine_id": exc.engine_id, "errors": exc.errors}) from exc
    except Exception:
        if staging_dir is not None:
            cleanup_staging_dir(staging_dir)
        raise

    if staging_dir is not None:
        mark_job_id(staging_dir, job.id)

    try:
        coordinator.evaluate(job.engine_id)
    finally:
        final_job = job_service.get_job(job.id)
        if staging_dir is not None and final_job is not None and final_job.status in {
            JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED,
        }:
            # A succeeded preview Job still needs its staged zip_path for
            # the LATER batch phase -- never clean up here on success,
            # only on failure/cancellation. Success cleanup happens once
            # the batch Job (Task 5's other route) actually consumes it.
            if final_job.status != JobStatus.SUCCEEDED:
                cleanup_staging_dir(staging_dir)

    return job_service.get_job(job.id)


@router.post("/mockup-generator/jobs/{job_id}/batch")
def launch_batch_job(
    job_id: str,
    job_service: JobService = Depends(get_job_service),
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
):
    preview_job = _require_mockup_job(job_id, job_service)
    if preview_job.status != JobStatus.SUCCEEDED or not preview_job.result_summary or preview_job.result_summary.get("phase") != "preview":
        raise HTTPException(status_code=409, detail="Job is not a succeeded preview job.")
    run_token = preview_job.result_summary.get("run_token")
    if not run_token:
        raise HTTPException(status_code=409, detail="Preview job result has no run_token.")

    config = {"phase": "batch", "run_token": run_token}
    job = job_service.create_job(engine_id=ENGINE_ID, config=config)
    coordinator.evaluate(job.engine_id)
    return job_service.get_job(job.id)
```

- [ ] **Step 2: Register the router in `api/main.py`**

Add, right after the existing `from api.video_generator_routes import router as video_generator_router` import:

```python
from api.mockup_generator_routes import router as mockup_generator_router
```

Add, right after the existing `app.include_router(video_generator_router)` line:

```python
app.include_router(mockup_generator_router)
```

- [ ] **Step 3: Verify with the real dev server and `curl`**

```bash
cd automation-controller && uvicorn api.main:app --port 8010 &
sleep 2
curl -s http://127.0.0.1:8010/mockup-generator/backgrounds | python -m json.tool
kill %1
```
Expected: a non-empty JSON array of background objects, HTTP 200 (no traceback in server output).

- [ ] **Step 4: Commit**

```bash
git add api/mockup_generator_routes.py api/main.py
git commit -m "feat: add Mockup Generator operator workflow routes"
```

---

## Task 6: Frontend API client + types

**Files:**
- Modify: `web/src/api/types.ts`
- Modify: `web/src/api/client.ts`

**Interfaces:**
- Produces: `MockupBackground` type; `fetchMockupBackgrounds()`, `launchMockupPreviewJob(zipFile: File, backgroundPath: string, designId: string | null)`, `launchMockupPreviewJobFromStaged(stagedZipPath: string, backgroundPath: string, designId: string | null)`, `launchMockupBatchJob(previewJobId: string)`, `getMockupPreviewImageUrl(jobId: string)`, `getMockupResultImageUrl(jobId: string)` — consumed by Task 7/8.

- [ ] **Step 1: Check existing `VideoPreset` type + `fetchVideoPresets`/`launchVideoJob` client functions for the exact conventions to mirror**

Read `web/src/api/types.ts` and `web/src/api/client.ts` in full before writing this task's additions — reuse the same `ApiError` class, the same base-URL constant, and the same `fetch(...)` wrapper every existing client function uses. Do not invent a second HTTP-calling convention.

- [ ] **Step 2: Add to `web/src/api/types.ts`**

```typescript
export interface MockupBackground {
  name: string;
  path: string;
  usable: boolean;
  reason: string | null;
}
```

- [ ] **Step 3: Add to `web/src/api/client.ts`** (adapt the exact request-building style already used by `fetchVideoPresets`/`launchVideoJob` in this same file — the shapes below are the contract, not literal code to paste unmodified)

```typescript
export async function fetchMockupBackgrounds(): Promise<MockupBackground[]> {
  return apiFetch<MockupBackground[]>("/mockup-generator/backgrounds");
}

export async function launchMockupPreviewJob(
  zipFile: File,
  backgroundPath: string,
  designId: string | null,
): Promise<Job> {
  const form = new FormData();
  form.append("zip_file", zipFile);
  form.append("background_path", backgroundPath);
  if (designId) form.append("design_id", designId);
  return apiFetchForm<Job>("/mockup-generator/jobs/preview", form);
}

export async function launchMockupPreviewJobFromStaged(
  stagedZipPath: string,
  backgroundPath: string,
  designId: string | null,
): Promise<Job> {
  const form = new FormData();
  form.append("staged_zip_path", stagedZipPath);
  form.append("background_path", backgroundPath);
  if (designId) form.append("design_id", designId);
  return apiFetchForm<Job>("/mockup-generator/jobs/preview", form);
}

export async function launchMockupBatchJob(previewJobId: string): Promise<Job> {
  return apiFetchForm<Job>(`/mockup-generator/jobs/${previewJobId}/batch`, new FormData());
}

export function getMockupPreviewImageUrl(jobId: string): string {
  return `${API_BASE}/mockup-generator/jobs/${jobId}/preview-image`;
}

export function getMockupResultImageUrl(jobId: string): string {
  return `${API_BASE}/mockup-generator/jobs/${jobId}/result-image`;
}
```

(`apiFetchForm` here means: whatever this file's existing multipart-POST helper is called — check `launchVideoJob`'s implementation and reuse the exact same helper/pattern, do not write a second one. `API_BASE` similarly means whatever base-URL constant `getJobOutputVideoUrl` already uses.)

- [ ] **Step 4: Verify the frontend still typechecks**

```bash
cd web && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd automation-controller && git add web/src/api/types.ts web/src/api/client.ts
git commit -m "feat: add frontend API client functions for the Mockup Generator workflow"
```

---

## Task 7: `MockupLaunchWorkflow.tsx` — upload, background selection, preview, approval

**Files:**
- Create: `web/src/components/mockup-generator/MockupLaunchWorkflow.tsx`

**Interfaces:**
- Consumes: `fetchMockupBackgrounds, launchMockupPreviewJob, launchMockupPreviewJobFromStaged, launchMockupBatchJob, getMockupPreviewImageUrl` (Task 6); `Button, EmptyBlock, ApiError` (existing shared components); `useNavigate` from `react-router-dom`.
- Produces: `MockupLaunchWorkflow` component, consumed by Task 8's `EngineDetail.tsx` wiring.

This component owns the full stage machine described in the design spec Section 6, stages 1–8 (upload → background → optional design id → generate preview → approve/reject/cancel), staying on this page throughout; it navigates to `/jobs/{batchJobId}` only after the operator approves and the batch Job is created (Task 8 renders that Job's result there, mirroring `VideoJobResult`/`JobDetail`'s existing pattern).

- [ ] **Step 1: Write the component**

```tsx
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  fetchMockupBackgrounds,
  getMockupPreviewImageUrl,
  launchMockupBatchJob,
  launchMockupPreviewJob,
  launchMockupPreviewJobFromStaged,
} from "../../api/client";
import type { Job, MockupBackground } from "../../api/types";
import { Button } from "../Button";
import { EmptyBlock } from "../AsyncState";

type Stage =
  | "select"
  | "uploading"
  | "generating_preview"
  | "waiting_on_approval"
  | "generating_batch"
  | "failed";

const STAGE_LABEL: Record<Stage, string> = {
  select: "Select ZIP + background",
  uploading: "Uploading ZIP",
  generating_preview: "Generating Preview",
  waiting_on_approval: "Waiting for Approval",
  generating_batch: "Generating Mockups",
  failed: "Failed",
};

const dropzoneBase: React.CSSProperties = {
  border: "1px dashed var(--border-bright)",
  borderRadius: "var(--radius)",
  padding: "22px 16px",
  textAlign: "center",
  color: "var(--text-dim)",
  fontSize: 12.5,
  transition: "border-color 0.15s ease, background 0.15s ease",
};

function friendlyError(err: unknown): string {
  if (err instanceof ApiError && err.status === 422 && err.detail && typeof err.detail === "object") {
    const detail = (err.detail as { detail?: unknown }).detail ?? err.detail;
    if (detail && typeof detail === "object" && "errors" in detail) {
      return (detail as { errors: string[] }).errors.join(" · ");
    }
  }
  return err instanceof Error ? err.message : "Something went wrong";
}

export function MockupLaunchWorkflow() {
  const navigate = useNavigate();

  const [zipFile, setZipFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [backgrounds, setBackgrounds] = useState<MockupBackground[] | null>(null);
  const [backgroundsError, setBackgroundsError] = useState<string | null>(null);
  const [selectedBackground, setSelectedBackground] = useState<string | null>(null);
  const [designId, setDesignId] = useState("");

  const [stage, setStage] = useState<Stage>("select");
  const [error, setError] = useState<string | null>(null);
  const [previewJob, setPreviewJob] = useState<Job | null>(null);
  const [devDetailsOpen, setDevDetailsOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchMockupBackgrounds()
      .then(setBackgrounds)
      .catch((err) => setBackgroundsError(friendlyError(err)));
  }, []);

  const canGeneratePreview = zipFile !== null && selectedBackground !== null && stage === "select";

  async function handleGeneratePreview() {
    if (!canGeneratePreview || !zipFile || !selectedBackground) return;
    setError(null);
    setStage("uploading");
    try {
      setStage("generating_preview");
      const job = await launchMockupPreviewJob(zipFile, selectedBackground, designId || null);
      if (job.status === "succeeded") {
        setPreviewJob(job);
        setStage("waiting_on_approval");
      } else {
        setError(job.error_summary ? JSON.stringify(job.error_summary) : "Preview job did not succeed.");
        setStage("failed");
      }
    } catch (err) {
      setError(friendlyError(err));
      setStage("failed");
    }
  }

  async function handleChooseDifferentBackground(newBackgroundPath: string) {
    if (!previewJob) return;
    const stagedZipPath = typeof previewJob.config.zip_path === "string" ? previewJob.config.zip_path : null;
    if (!stagedZipPath) return;
    setError(null);
    setStage("generating_preview");
    try {
      const job = await launchMockupPreviewJobFromStaged(stagedZipPath, newBackgroundPath, designId || null);
      if (job.status === "succeeded") {
        setPreviewJob(job);
        setSelectedBackground(newBackgroundPath);
        setStage("waiting_on_approval");
      } else {
        setError(job.error_summary ? JSON.stringify(job.error_summary) : "Preview job did not succeed.");
        setStage("failed");
      }
    } catch (err) {
      setError(friendlyError(err));
      setStage("failed");
    }
  }

  function handleCancel() {
    setPreviewJob(null);
    setStage("select");
    setError(null);
  }

  async function handleApprove() {
    if (!previewJob) return;
    setError(null);
    setStage("generating_batch");
    try {
      const batchJob = await launchMockupBatchJob(previewJob.id);
      if (batchJob.status === "succeeded") {
        navigate(`/jobs/${batchJob.id}`);
      } else {
        setError(batchJob.error_summary ? JSON.stringify(batchJob.error_summary) : "Batch job did not succeed.");
        setStage("failed");
      }
    } catch (err) {
      setError(friendlyError(err));
      setStage("failed");
    }
  }

  const devPayloadPreview = {
    zip_file: zipFile?.name ?? null,
    background_path: selectedBackground,
    design_id: designId || null,
    stage,
    preview_job_id: previewJob?.id ?? null,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
        {STAGE_LABEL[stage]}
      </div>

      {stage === "select" && (
        <>
          <div>
            <div className="label" style={{ marginBottom: 8 }}>ZIP file</div>
            <div
              style={{
                ...dropzoneBase,
                borderColor: dragOver ? "var(--accent)" : "var(--border-bright)",
                background: dragOver ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "transparent",
              }}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                const file = e.dataTransfer.files[0];
                if (file) setZipFile(file);
              }}
            >
              {zipFile ? (
                <div>
                  <div className="mono" style={{ marginBottom: 10 }}>{zipFile.name}</div>
                  <Button type="button" variant="outline" onClick={() => setZipFile(null)}>Remove</Button>
                </div>
              ) : (
                <>
                  <div style={{ marginBottom: 10 }}>Drag and drop a ZIP here, or</div>
                  <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()}>Upload ZIP</Button>
                </>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) setZipFile(file);
                  e.target.value = "";
                }}
              />
            </div>
          </div>

          <div>
            <div className="label" style={{ marginBottom: 8 }}>Background</div>
            {backgroundsError && <EmptyBlock>{backgroundsError}</EmptyBlock>}
            {!backgrounds && !backgroundsError && <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Loading backgrounds…</div>}
            {backgrounds && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                {backgrounds.map((bg) => {
                  const selected = bg.path === selectedBackground;
                  return (
                    <button
                      key={bg.path}
                      type="button"
                      disabled={!bg.usable}
                      onClick={() => setSelectedBackground(bg.path)}
                      title={bg.usable ? bg.name : bg.reason ?? "Not usable"}
                      style={{
                        width: 120,
                        padding: 8,
                        borderRadius: "var(--radius)",
                        border: `1px solid ${selected ? "var(--accent)" : "var(--border-bright)"}`,
                        background: selected ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "var(--panel)",
                        opacity: bg.usable ? 1 : 0.4,
                        cursor: bg.usable ? "pointer" : "not-allowed",
                        textAlign: "left",
                      }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>{bg.name}</div>
                      {!bg.usable && <div style={{ fontSize: 10, color: "var(--state-failure)", marginTop: 4 }}>{bg.reason}</div>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div>
            <div className="label" style={{ marginBottom: 8 }}>Design ID (optional)</div>
            <input
              value={designId}
              onChange={(e) => setDesignId(e.target.value)}
              placeholder="Optional reference"
              style={{
                background: "var(--panel-raised)",
                border: "1px solid var(--border-bright)",
                color: "var(--text-primary)",
                borderRadius: "var(--radius)",
                padding: "8px 10px",
                fontSize: 12,
                width: "100%",
                maxWidth: 320,
              }}
            />
          </div>

          <div>
            <Button variant="primary" onClick={handleGeneratePreview} disabled={!canGeneratePreview}>
              Generate Preview
            </Button>
            {!canGeneratePreview && (
              <span style={{ fontSize: 11, color: "var(--text-dim)", marginLeft: 10 }}>
                {!zipFile ? "Select a ZIP" : !selectedBackground ? "Select a background" : ""}
              </span>
            )}
          </div>
        </>
      )}

      {(stage === "uploading" || stage === "generating_preview" || stage === "generating_batch") && (
        <div style={{ fontSize: 12.5, color: "var(--text-dim)" }}>Working…</div>
      )}

      {stage === "waiting_on_approval" && previewJob && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <img
            src={getMockupPreviewImageUrl(previewJob.id)}
            alt="Representative mockup preview"
            style={{ maxWidth: "100%", borderRadius: "var(--radius)", border: "1px solid var(--border-bright)", display: "block" }}
          />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            <Button variant="primary" onClick={handleApprove}>Approve and Generate Full Batch</Button>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
              {backgrounds?.filter((b) => b.usable && b.path !== selectedBackground).map((bg) => (
                <Button key={bg.path} variant="outline" onClick={() => handleChooseDifferentBackground(bg.path)}>
                  Use {bg.name}
                </Button>
              ))}
            </div>
            <Button variant="danger" onClick={handleCancel}>Cancel</Button>
          </div>
        </div>
      )}

      {stage === "failed" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ color: "var(--state-failure)", fontSize: 12.5 }}>{error}</div>
          <Button variant="outline" onClick={handleCancel}>Start Over</Button>
        </div>
      )}

      <details open={devDetailsOpen} onToggle={(e) => setDevDetailsOpen((e.target as HTMLDetailsElement).open)}>
        <summary style={{ cursor: "pointer", fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--font-mono)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          Developer Details
        </summary>
        <pre className="mono" style={{ marginTop: 8, fontSize: 11, color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {JSON.stringify(devPayloadPreview, null, 2)}
        </pre>
      </details>
    </div>
  );
}
```

- [ ] **Step 2: Typecheck**

```bash
cd web && npx tsc --noEmit
```
Expected: no errors. If `Job["config"]` or `Job["error_summary"]` types don't match what's used above, adjust to match the real `Job` type in `web/src/api/types.ts` (read it before finalizing this file, per Task 6 Step 1 — do not guess field names).

- [ ] **Step 3: Commit**

```bash
git add web/src/components/mockup-generator/MockupLaunchWorkflow.tsx
git commit -m "feat: add MockupLaunchWorkflow — upload, background selection, preview, approval"
```

---

## Task 8: Result display + page wiring

**Files:**
- Create: `web/src/components/mockup-generator/MockupJobResult.tsx`
- Modify: `web/src/pages/EngineDetail.tsx`
- Modify: `web/src/pages/JobDetail.tsx`

**Interfaces:**
- Consumes: `getMockupResultImageUrl, openMockupOutputFolder(job_id), openMockupListingOutputs(job_id)` (Task 6 — add these two `open*` client functions alongside Task 6's others, mirroring `openOutputFolder`/`openListingOutputs` exactly, `POST /jobs/{id}/open-output-folder` and `/open-listing-outputs`), `formatDuration` (existing `status.ts`).

- [ ] **Step 1: Add the two missing client functions to `web/src/api/client.ts`** (same file as Task 6 — add these alongside the others there if not already covered)

```typescript
export async function openMockupOutputFolder(jobId: string): Promise<{ opened: string }> {
  return apiFetchForm<{ opened: string }>(`/jobs/${jobId}/open-output-folder`, new FormData());
}

export async function openMockupListingOutputs(jobId: string): Promise<{ opened: string }> {
  return apiFetchForm<{ opened: string }>(`/jobs/${jobId}/open-listing-outputs`, new FormData());
}
```

(If the existing `openOutputFolder`/`openListingOutputs` for video use a plain POST-with-no-body helper instead of `apiFetchForm`, mirror that exact helper instead — check their implementations first.)

- [ ] **Step 2: Write `MockupJobResult.tsx`**

```tsx
import { useNavigate } from "react-router-dom";
import { getMockupResultImageUrl, openMockupListingOutputs, openMockupOutputFolder } from "../../api/client";
import type { Job } from "../../api/types";
import { formatDuration } from "../../status";
import { Button } from "../Button";
import { useState } from "react";

const ENGINE_ID = "etsy-mockup-generator";

interface Props {
  job: Job;
  totalExecutionSeconds: number | null;
}

export function MockupJobResult({ job, totalExecutionSeconds }: Props) {
  const navigate = useNavigate();
  const [folderMessage, setFolderMessage] = useState<string | null>(null);
  const [folderError, setFolderError] = useState<string | null>(null);

  const manifest = (job.result_summary?.manifest ?? {}) as Record<string, unknown>;
  const outputCounts = (manifest.output_counts ?? {}) as Record<string, number>;
  const totalAssets = Object.values(outputCounts).reduce((sum, n) => sum + (n ?? 0), 0);
  const backgroundFilename = typeof manifest.background_filename === "string" ? manifest.background_filename : "—";
  const designId = typeof manifest.design_id === "string" ? manifest.design_id : null;

  async function handleOpen(action: () => Promise<{ opened: string }>) {
    setFolderError(null);
    setFolderMessage(null);
    try {
      const result = await action();
      setFolderMessage(`Opened ${result.opened}`);
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : "Could not open folder");
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <img
        src={getMockupResultImageUrl(job.id)}
        alt="Representative completed mockup"
        style={{ width: "100%", maxHeight: 420, objectFit: "contain", background: "#000", borderRadius: "var(--radius)", display: "block" }}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12.5 }}>
        <Row label="Total assets" value={String(totalAssets)} mono />
        {Object.entries(outputCounts).map(([category, count]) => (
          <Row key={category} label={category.replace("_", " ")} value={String(count)} mono />
        ))}
        <Row label="Background" value={backgroundFilename} mono />
        <Row label="Design ID" value={designId ?? "—"} mono />
        <Row label="Duration" value={formatDuration(totalExecutionSeconds)} mono />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        <Button variant="outline" onClick={() => handleOpen(() => openMockupOutputFolder(job.id))}>Open Output Folder</Button>
        <Button variant="outline" onClick={() => handleOpen(() => openMockupListingOutputs(job.id))}>Open Current Listing Outputs</Button>
        <Button variant="primary" onClick={() => navigate(`/engines/${ENGINE_ID}`)}>Launch Another Mockup Job</Button>
      </div>

      {folderMessage && <div style={{ fontSize: 11, color: "var(--text-dim)" }}>{folderMessage}</div>}
      {folderError && <div style={{ fontSize: 11, color: "var(--state-failure)" }}>{folderError}</div>}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span style={{ color: "var(--text-dim)" }}>{label}</span>
      <span className={mono ? "mono" : undefined} style={{ color: "var(--text-primary)", textAlign: "right", wordBreak: "break-all" }}>
        {value}
      </span>
    </div>
  );
}
```

- [ ] **Step 3: Wire `EngineDetail.tsx`**

Add the import (alongside the existing `VideoLaunchWorkflow` import):
```typescript
import { MockupLaunchWorkflow } from "../components/mockup-generator/MockupLaunchWorkflow";
```
Add the constant (alongside `VIDEO_GENERATOR_ENGINE_ID`):
```typescript
const MOCKUP_GENERATOR_ENGINE_ID = "etsy-mockup-generator";
```
Change the `Launch Job` panel's conditional (currently `engineId === VIDEO_GENERATOR_ENGINE_ID ? <VideoLaunchWorkflow /> : ...`) to check the mockup engine first:
```tsx
<Panel title="Launch Job" eyebrow={engineId === VIDEO_GENERATOR_ENGINE_ID || engineId === MOCKUP_GENERATOR_ENGINE_ID ? "Operator workflow" : "POST /jobs"}>
  {engineId === VIDEO_GENERATOR_ENGINE_ID ? (
    <VideoLaunchWorkflow />
  ) : engineId === MOCKUP_GENERATOR_ENGINE_ID ? (
    <MockupLaunchWorkflow />
  ) : engine && !engine.capabilities.supports_launch ? (
    <EmptyBlock>
      This engine does not support launch yet ({engine.capabilities.implementation_status}) — see notes above.
    </EmptyBlock>
  ) : (
    /* ...existing generic form, unchanged... */
  )}
</Panel>
```

- [ ] **Step 4: Wire `JobDetail.tsx`**

Add the import (alongside `VideoJobResult`):
```typescript
import { MockupJobResult } from "../components/mockup-generator/MockupJobResult";
```
Add the constant (alongside `VIDEO_GENERATOR_ENGINE_ID`):
```typescript
const MOCKUP_GENERATOR_ENGINE_ID = "etsy-mockup-generator";
```
Change the result-rendering conditional (currently `job.data.engine_id === VIDEO_GENERATOR_ENGINE_ID && job.data.status === "succeeded"`) to also match, showing the mockup result only for a succeeded **batch**-phase Job (a succeeded preview-phase Job should fall through to the generic Config/Result panels — the operator never lands on a preview Job's detail page directly in the intended flow, but the fallback must still be sane if they navigate there manually):
```tsx
{job.data.engine_id === VIDEO_GENERATOR_ENGINE_ID && job.data.status === "succeeded" ? (
  <Panel title="Result" eyebrow="Generated video">
    <VideoJobResult job={job.data} totalExecutionSeconds={metrics.data?.total_execution_seconds ?? null} />
    <DeveloperDetails config={job.data.config} result={job.data.result_summary} error={job.data.error_summary} />
  </Panel>
) : job.data.engine_id === MOCKUP_GENERATOR_ENGINE_ID && job.data.status === "succeeded" && job.data.result_summary?.phase === "batch" ? (
  <Panel title="Result" eyebrow="Generated mockups">
    <MockupJobResult job={job.data} totalExecutionSeconds={metrics.data?.total_execution_seconds ?? null} />
    <DeveloperDetails config={job.data.config} result={job.data.result_summary} error={job.data.error_summary} />
  </Panel>
) : (
  /* ...existing generic Config/Result panels, unchanged... */
)}
```

- [ ] **Step 5: Typecheck**

```bash
cd web && npx tsc --noEmit
```
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd automation-controller && git add web/src/components/mockup-generator/MockupJobResult.tsx web/src/pages/EngineDetail.tsx web/src/pages/JobDetail.tsx web/src/api/client.ts
git commit -m "feat: wire Mockup Generator result display into EngineDetail and JobDetail"
```

---

## Task 9: Full live verification + completion report

**Files:** none (verification only)

- [ ] **Step 1: Start both the API and the web dev server**

```bash
cd automation-controller && uvicorn api.main:app --port 8010 &
cd web && npm run dev &
```

- [ ] **Step 2: Drive the full checklist through the real browser** (Claude-in-Chrome), one item at a time, noting pass/fail for each:

1. Navigate to the Mockup Generator engine page.
2. Click-to-upload a ZIP; confirm filename + basic validation state shown.
3. Remove it; drag-and-drop the same ZIP; confirm it's accepted.
4. Confirm backgrounds load with friendly names; select one usable background; confirm an unusable one (if any) is visibly disabled with a reason.
5. Enter a Design ID; click Generate Preview; confirm the stage indicator moves through Uploading → Validating/Generating Preview → Waiting for Approval.
6. Confirm exactly one representative preview image renders, large enough to evaluate.
7. Click "Use <other background>"; confirm the preview regenerates with the new background and the old one is discarded (only one preview shown).
8. Click Cancel; confirm the workflow resets to the upload/background screen.
9. Repeat upload → background → Generate Preview → Approve and Generate Full Batch; confirm navigation to the Job detail page with a completed result.
10. Confirm the result screen shows: representative completed mockup, total assets, category counts, background used, Design ID, duration, and the three action buttons.
11. Click Open Output Folder; confirm Windows Explorer opens the correct `output/run-*/` folder.
12. Click Launch Another Mockup Job; confirm it returns to a fresh upload screen.
13. Try an invalid ZIP (e.g. a renamed `.txt` file with a `.zip` extension, or a real non-ZIP file) — confirm a friendly error, not a raw traceback.
14. Try submitting with no background selected — confirm Generate Preview stays disabled or a friendly error appears.
15. Force a real failure path if possible (e.g. temporarily rename `backgrounds/` on disk to simulate "no backgrounds found," restore it afterward) — confirm the Job ends FAILED with a friendly banner and raw detail only under Developer Details, and that this doesn't leave the engine's queue stuck (a subsequent normal preview request still succeeds).
16. Restart the Controller process (`uvicorn` restart) mid-way through having at least one completed mockup Job in the database; confirm that Job and its result are still visible/loadable after restart (persistence).
17. Confirm engine queue serialization: fire two preview requests back-to-back; confirm the second one waits (QUEUED) rather than running concurrently, per `max_concurrent_runs=1`.
18. Re-verify the Video Generator's full operator workflow (upload images, launch, see the resulting video, open folder) still works exactly as before — no regression.

- [ ] **Step 3: Record every bug found and fixed during this pass directly in the relevant task's code** (not as a separate patch commit pile) — amend the affected task's files, re-run that task's own verification step, then re-run the specific browser check(s) that surfaced the bug.

- [ ] **Step 4: Final commit of any live-verification fixes**

```bash
git add -A
git commit -m "fix: address issues found during live Mockup Generator workflow verification"
```

- [ ] **Step 5: Write the completion report** (per the original task's required format — additive interface introduced, what was implemented in the Controller adapter/backend/UI, exactly how the workflow was verified live, bugs found and fixed, honest remaining limitations, any decisions needing approval before AI Image Generator integration). Present this directly to the user; do not begin AI Image Generator work.

---

## Self-Review Notes

- **Spec coverage:** Section 1 (headless seam) → Tasks 1–2. Section 2 (upload/staging) → Task 4, Task 7. Section 3 (background selection) → Task 3's `list_mockup_backgrounds`, Task 5's route, Task 7's UI. Section 4 (single preview) → Task 2's `prepare_preview`, Task 5's preview-image route, Task 7's approval UI. Section 5 (full batch) → Task 2's `generate_full_batch`, Task 5's batch route. Section 6 (progress presentation) → Task 7's `Stage`/`STAGE_LABEL`. Section 7 (completed result) → Task 8. Section 8 (safe file access) → Task 5's `_validated_artifact_path`/`_resolve_run_dir`. Section 9 (failure handling) → Task 3's `EngineLaunchError` translation, Task 7/8's friendly-error rendering + Developer Details. Section 10 (no regressions) → Task 9 Step 2 item 18. Section 11 (live verification) → Task 9.
- **Placeholder scan:** no TBD/TODO markers; every code step has complete, runnable code (Task 6's client functions are marked as adapting an existing pattern, with the exact contract given, because the base helper name genuinely isn't known until the file is read — this is a "read first, then match" instruction, not a placeholder).
- **Type consistency:** `run_token` (Task 2) flows unchanged through `EngineRunReference.extra["run_token"]` (Task 3) → `result_summary["run_token"]` (Job, via `collect_results()`) → `launchMockupBatchJob(previewJobId)` (Task 6/7, reads it server-side via the Job, never client-side) — confirmed consistent. `preview_artifacts`/`representative` (Task 2) flows unchanged into Task 5's `get_preview_image` and is not otherwise renamed. `manifest.output_counts`/`background_filename`/`design_id` (Task 1/2's `execute_full_batch()`/`generate_full_batch()`) match exactly what Task 8's `MockupJobResult.tsx` reads.
