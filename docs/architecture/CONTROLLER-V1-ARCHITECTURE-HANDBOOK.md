# Automation Controller V1 — Repository Deep Architecture Extraction (Engineering Handbook)

**Scope:** `etsy-mockup-generator`, `Etsy-AI-Image-Generator`, `etsy-video-generator`
**Date:** 2026-07-21
**Nature of this document:** This is an architectural extraction, not a review. It is internal engineering documentation intended to let another engineer design an external Automation Controller against these three repositories without reading their source trees. Every claim below is grounded in code actually read in this repository, cited by file and, wherever the source agent captured it, by line number. Nothing here proposes redesigns, improvements, or opinions about code quality — the repositories are treated as the source of truth and simply explained.

**Relationship to the prior readiness report:** `CONTROLLER-V1-ARCHITECTURE-INSPECTION.md` (same workspace) is a separate, narrower "is this ready for a Controller" assessment. This handbook is deliberately more exhaustive — call graphs, sequence diagrams, a full module inventory, hidden assumptions, and a cross-repo glossary — and was produced by independently re-reading all three repositories at a much finer grain. Where the two documents overlap, treat this handbook as the more detailed and more current source; where they might appear to disagree in emphasis, both were produced from the same underlying code and neither redesigns anything, so any apparent tension is a difference in framing, not in fact.

**How to use this document.** Each of the three repositories gets its own self-contained chapter (Parts 1–14 of the outline below, repeated per repo). After all three chapters, Part 15 is a single cross-repository architectural glossary, and Part 16 is a final, ecosystem-level knowledge-transfer synthesis written to stand alone — read Part 16 first if you want the shortest path to being productive, then drop into whichever repo chapter you need depth on.

---

## Table of Contents

- **Chapter A — `etsy-mockup-generator`** (Parts 1–14)
- **Chapter B — `Etsy-AI-Image-Generator`** (Parts 1–14)
- **Chapter C — `etsy-video-generator`** (Parts 1–14)
- **Part 15 — Architectural Glossary** (cross-repository)
- **Part 16 — Final Knowledge Transfer** (cross-repository synthesis)

---


---

# CHAPTER A — etsy-mockup-generator

## PART 1 — REPOSITORY MAP

`E:\Vilicity\etsy-mockup-generator` is a **flat, script-based Python repository** — there is no `src/` package, no installed CLI entry point, no `tests/` directory, and no `docs/` folder. Everything an engineer needs is either a root-level `.py` file or a project-root data folder. This is confirmed by direct directory listing (`ls` at repo root, run 2026-07-21) and by `archive/README.md:1-44`.

```
etsy-mockup-generator/
├── .claude/settings.local.json      # Claude Code tool-permission allowlist only; not app config
├── README.md                        # Primary human-facing spec — read PART 0 material from this
├── batch_generate.py                # THE entry point / orchestrator (interactive)
├── prepare_input.py                 # ZIP discovery + extraction
├── classify_mockups.py              # PNG -> category classifier
├── composite.py                     # Low-level compositing primitive (+ its own standalone main())
├── shadow.py                        # Shadow-layer rendering, alpha-composite math
├── human_framing.py                 # Production human-model framing orchestration (fallback chain)
├── human_dynamic_framing_test_v2.py # Framing generation "v2" (superseded baseline, still imported for get_visible_bbox)
├── human_dynamic_framing_test_v3.py # Framing generation "v3" (garment-band estimator; imported live)
├── human_dynamic_framing_test_v4.py # Framing generation "v4" (current production baseline; imported live)
├── hybrid_human_framing_v1.py       # Pose-corrected framing ("Hybrid V1"; imported live)
├── pose_landmark_experiment.py      # MediaPipe pose-landmark helper library (imported live)
├── requirements.txt                 # Pillow, mediapipe
├── input/                           # DROP ZONE: exactly one Printful export ZIP goes here
├── backgrounds/                     # Permanent reusable background library (2000x2000 PNG/JPG)
├── working/extracted/               # Scratch: ZIP contents, wiped+rebuilt every run
├── preview/                         # Scratch: pre-approval preview renders, wiped every preview cycle
├── output/                          # Production output: output/run-<date>-<seq>/{assets/,manifest.json}
│   └── regression-references/       # Hand-curated, DO-NOT-DELETE before/after evidence
├── processed-inputs/                # Successfully-processed ZIPs land here (moved from input/)
├── processed-outputs/               # Reserved, currently completely unused
├── models/                          # MediaPipe model weight files (.task, .tflite)
├── test-assets/                     # Fixture images used by the *-test-v*.py / experiment scripts' own main()s
├── backups/                         # Ad hoc *.bak snapshots of edited .py files + 2 zip snapshots (manual safety net, not code-managed)
├── archive/                         # Superseded code, diagnostics, historical runs — read-only history
└── __pycache__/                     # Compiled bytecode, irrelevant
```

**Folder-by-folder rationale:**

- **`input/`** — the operator's drop zone. `prepare_input.find_first_zip()` (`prepare_input.py:13-17`) expects *at most one* ZIP; `batch_generate.py` only ever looks at the first match from `glob.glob("input/*.zip")`. This folder is the trigger condition for the whole pipeline (README.md:40-43).
- **`backgrounds/`** — the reusable background library. Every `.png/.jpg/.jpeg` here becomes a numbered choice (`batch_generate.scan_backgrounds`, `batch_generate.py:151-159`). Backgrounds must be exactly 2000x2000 or they're rejected at selection time (`batch_generate.background_size_error`, `batch_generate.py:162-179`).
- **`working/extracted/`** — pure scratch. `prepare_input.extract_zip()` (`prepare_input.py:20-34`) `shutil.rmtree`s and recreates it on every run — never authoritative, never safe to hand-edit (README.md:177-186).
- **`preview/`** — project-root scratch (NOT under `working/`), for pre-approval preview renders. Wiped by `batch_generate.clear_preview_dir()` (`batch_generate.py:298-306`) every time a preview cycle runs, including every "choose another background" loop.
- **`output/`** — production output root. Each run is `output/run-<date>-<sequence>/` containing `assets/` (flat, all categories mixed, category encoded in filename) + `manifest.json` (`batch_generate.py:594-663`).
- **`output/regression-references/`** — hand-curated "known good" images + JSON describing exact scale/offset values, kept specifically to catch silent framing/shadow regressions (`output/regression-references/README.md:1-23`). Never produced by `batch_generate.py` itself.
- **`processed-inputs/`** — destination for a ZIP after a *fully successful* run (`batch_generate.move_processed_zip`, `batch_generate.py:437-507`). A failed/partial run leaves the ZIP in `input/` untouched.
- **`processed-outputs/`** — reserved for a not-yet-built later workflow step; currently dead space (README.md:169-175).
- **`models/`** — two MediaPipe model weight files: `pose_landmarker_lite.task` (pose) and `blaze_face_short_range.tflite` (face). Their *presence* is a runtime feature-gate, not just data (see PART 6/PART 8).
- **`test-assets/`** — fixture PNGs + one background, used only by the standalone `main()` functions of the framing-experiment scripts (`human_dynamic_framing_test_v2/v3/v4.py`, `hybrid_human_framing_v1.py`, `pose_landmark_experiment.py`) — never touched by `batch_generate.py`.
- **`backups/`** — manually-created `.bak`/timestamped snapshots of specific files (e.g. `shadow.py.bak-20260717212537`) plus two full-repo `.zip` snapshots. Not code-managed, not read by any script — purely a human safety net evidenced by file naming.
- **`archive/`** — explicitly documented dumping ground for everything superseded: old experiment scripts (`archive/experiments/`), diagnostic investigations (`archive/diagnostics/{shadows,transparency,checkerboard-artifact,background-recomposite,framing}/`), and superseded production runs (`archive/historical-runs/`). `archive/README.md:3-5` states plainly: "nothing here is read by production code."

## PART 2 — MODULE MAP

For each module: Purpose / Responsibilities / Callers / Calls / Inputs / Outputs / Internal-vs-Controller-relevant / Dependencies.

### `batch_generate.py` (759 lines)
- **Purpose**: The one true entry point / orchestrator for the entire interactive workflow.
- **Responsibilities**: background scan+selection, ZIP extraction+classification (once), preview generation loop, approval gate, full-batch generation, manifest writing, ZIP archival.
- **Callers**: none (this is the top of the call graph); invoked as `python batch_generate.py`.
- **Calls**: `classify_mockups.{inspect_image,classify}`, `composite.{CANVAS_SIZE,composite_mockup}`, `human_dynamic_framing_test_v2.get_visible_bbox`, `human_framing.{build_face_detector_safe,build_pose_landmarker_safe,calculate_human_model_framing}`, `prepare_input.{EXTRACTED_DIR,INPUT_DIR,extract_zip,find_first_zip}`, `shadow.SHADOW_PRESETS`.
- **Inputs**: `input/*.zip`, `backgrounds/*.{png,jpg,jpeg}`, operator stdin.
- **Outputs**: `output/run-<id>/assets/*.png`, `output/run-<id>/manifest.json`, `preview/*.png`, moves ZIP to `processed-inputs/`.
- **Controller-relevant**: YES — this is the launch surface a Controller must drive (see PART 13). Currently fully interactive/stdin-blocking (see PART 4).
- **Dependencies**: Pillow (`PIL.Image`, `PIL.ImageDraw`), stdlib (`glob, hashlib, json, os, shutil, datetime`).

### `prepare_input.py` (58 lines)
- **Purpose**: ZIP discovery + extraction, isolated so `batch_generate.py` doesn't own filesystem/zip mechanics directly.
- **Responsibilities**: find exactly one ZIP in `input/`; wipe+recreate `working/extracted/`; extract; report which names were PNGs.
- **Callers**: `batch_generate.py` (imports `EXTRACTED_DIR, INPUT_DIR, extract_zip, find_first_zip`).
- **Calls**: stdlib `zipfile`, `shutil`, `glob`, `os`.
- **Inputs**: `input_dir` path, `zip_path`.
- **Outputs**: dict `{"extracted_names", "png_names"}`; side effect populates `working/extracted/`.
- **Controller-relevant**: internal detail; a Controller should never call this directly — it drives `batch_generate.py`'s workflow, not the reverse. Also has its own standalone `main()` (secondary entry point, see PART 4).
- **Dependencies**: none beyond stdlib.

### `classify_mockups.py` (131 lines)
- **Purpose**: Deterministic, filename+pixel-geometry classifier for extracted PNGs into `back` / `flat_front` / `human_model_front` / `unknown`.
- **Responsibilities**: measure `visible_percent` (alpha bbox area / canvas area) and `aspect_ratio` (h/w of alpha bbox); apply filename + threshold rules (`classify_mockups.py:83-102`).
- **Callers**: `batch_generate.py` (`inspect_image`, `classify`), and every `human_dynamic_framing_test_v*.py` / `hybrid_human_framing_v1.py` standalone `main()` (to select `human_model_front` fixtures from `test-assets/`).
- **Calls**: `PIL.Image` only.
- **Inputs**: a PNG file path.
- **Outputs**: `inspect_image()` -> `{"filename","visible_percent","aspect_ratio"}`; `classify()` -> one of 4 category strings.
- **Controller-relevant**: The category taxonomy (`back`/`flat_front`/`human_model_front`/`unknown`) is part of the stable public contract (it appears in `manifest.json`). The exact thresholds (`VISIBLE_PERCENT_SPLIT = 60.0`, `ASPECT_RATIO_SPLIT = 1.5`, `classify_mockups.py:41,46`) are internal tuning, not to be relied on externally.
- **Dependencies**: Pillow only. Filename convention dependency: `"-back-"` / `"-front-"` substring must appear in Printful export filenames (`classify_mockups.py:86,89` — hidden assumption, see PART 14).

### `composite.py` (85 lines)
- **Purpose**: Lowest-level compositing primitive: resize a mockup PNG by `scale`, place at `(x_offset, y_offset)` on a background, optionally through the shadow pipeline.
- **Responsibilities**: `composite_mockup()` is the single function every other module in the pipeline calls to actually produce a final image.
- **Callers**: `batch_generate.render_mockup_to_path`, and every framing-experiment script's own `main()` (`human_dynamic_framing_test_v2/v3/v4.py`, `hybrid_human_framing_v1.py`).
- **Calls**: `shadow.composite_with_shadow` (when `shadow_preset` is passed) or a raw `Image.paste` fallback path (when `shadow_preset is None` — used only by `composite.py`'s own hardcoded `main()` demo and the framing-experiment scripts' calls that omit the argument).
- **Inputs**: `background_path, mockup_path, output_path, scale, x_offset, y_offset, shadow_preset=None`.
- **Outputs**: saves a PNG to `output_path`; returns the composited `Image`.
- **Controller-relevant**: internal — a Controller should never call `composite_mockup` directly; it has no manifest/error-reporting layer of its own.
- **Dependencies**: Pillow, `shadow.py`.
- **Note**: `composite.py` also defines module-level hardcoded constants (`SCALE=1.285`, `X_OFFSET=-285`, `Y_OFFSET=-575`, `BACKGROUND_PATH="test-assets/Orb Background.png"`, `MOCKUP_GLOB=...`) used only by its own `main()` — a leftover proof-of-concept demo (`composite.py:1,9-20`), not part of the production path.

### `shadow.py` (171 lines)
- **Purpose**: Builds and composites the shadow layer under every mockup; the single place shadow style/strength is defined.
- **Responsibilities**: `SHADOW_PRESETS` dict (per-category style config), `build_shadow_layer()` (renders one shadow layer from the mockup's alpha silhouette), `composite_with_shadow()` (background -> shadow -> mockup, fully alpha-composited, forced opaque on output).
- **Callers**: `composite.composite_mockup` (when `shadow_preset` supplied).
- **Calls**: `PIL.Image`, `PIL.ImageFilter.GaussianBlur`.
- **Inputs**: `background, mockup` (RGBA Images), `canvas_size, position, preset`.
- **Outputs**: a flattened, fully-opaque RGBA `Image`.
- **Controller-relevant**: internal, but `SHADOW_PRESETS` keys (`"human_model_front"`, `"flat_front"`, `"back"`) are conceptually tied to the classification taxonomy a Controller may see in `manifest.json`.
- **Dependencies**: Pillow only. Documents (in its own docstring, `shadow.py:1-42`) a historical checkerboard/transparency bug and its fix — this is institutional knowledge, not currently-live risk.

### `human_framing.py` (314 lines)
- **Purpose**: THE production framing entry point for `human_model_front` images — wires together the V4 baseline, Hybrid V1 pose correction, and an optional face-box vertical correction, with a fully-guarded fallback chain.
- **Responsibilities**: `calculate_human_model_framing()` is the single function `batch_generate.py` calls per human-model image; also owns the two "safe builder" functions (`build_pose_landmarker_safe`, `build_face_detector_safe`) that never raise.
- **Callers**: `batch_generate.py` only.
- **Calls**: `human_dynamic_framing_test_v3.estimate_garment_region`, `human_dynamic_framing_test_v4.calculate_dynamic_framing`, `hybrid_human_framing_v1.calculate_hybrid_framing`, `pose_landmark_experiment.{MODEL_PATH, build_landmarker, flatten_to_rgb, run_pose_detection, extract_landmarks}`, plus `mediapipe` directly (for the face detector, `human_framing.py:124-131,143-148`).
- **Inputs**: `mockup_path, bbox, source_size, landmarker, face_detector=None`.
- **Outputs**: `{"scale","x_offset","y_offset","framing_method"}` where `framing_method` ∈ `{"hybrid_v1","hybrid_v1_face_corrected","v2_fallback_no_landmarker","v2_fallback_detection_error","v2_fallback_low_confidence","v2_fallback_hybrid_error","v2_fallback_invalid_geometry"}`.
- **Controller-relevant**: `framing_method` values are part of the stable manifest contract a Controller can read to judge quality (e.g., "mostly hybrid_v1, not a sea of fallbacks" — README.md:216-219).
- **Dependencies**: Pillow, mediapipe (indirectly via imported modules and directly for face detection), `human_dynamic_framing_test_v3.py`, `human_dynamic_framing_test_v4.py`, `hybrid_human_framing_v1.py`, `pose_landmark_experiment.py`.

### `human_dynamic_framing_test_v2.py` (196 lines)
- **Purpose**: Second-generation framing prototype ("garment-focused bbox-driven framing"). Superseded as the *framing formula* by v3/v4, but its `get_visible_bbox()` helper is still the live production bbox-extraction function.
- **Callers**: `batch_generate.py` imports **only** `get_visible_bbox` (`batch_generate.py:62`) — `calculate_dynamic_framing` here is NOT used in production (superseded).
- **Calls**: `classify_mockups.{inspect_image,classify}`, `composite.composite_mockup` (only inside its own `main()`).
- **Dependencies**: Pillow.
- **Controller-relevant**: internal implementation detail; `get_visible_bbox` is a pure geometry helper with no external contract.

### `human_dynamic_framing_test_v3.py` (225 lines)
- **Purpose**: Third-generation framing: introduces the "garment band" concept — a horizontal alpha-geometry slice (shoulders-to-waist) used for sizing instead of the whole-body bbox.
- **Callers**: `human_framing.py` imports `estimate_garment_region` LIVE (`human_framing.py:60`); `human_dynamic_framing_test_v4.py` imports several constants and `estimate_garment_region`/`get_alpha_and_bbox` LIVE (`human_dynamic_framing_test_v4.py:39-50`); `hybrid_human_framing_v1.py` imports `estimate_garment_region`/`get_alpha_and_bbox` via v4's re-export chain.
- **Calls**: `classify_mockups`, `composite.composite_mockup` (own `main()` only).
- **Key function**: `estimate_garment_region(alpha, bbox, source_size)` returning band geometry (`garment_left/right/width/center_x/center_y`, `human_dynamic_framing_test_v3.py:87-118`). This module's *own* `calculate_dynamic_framing` is NOT what's used in production — v4's version is.
- **Controller-relevant**: internal geometry only.

### `human_dynamic_framing_test_v4.py` (168 lines)
- **Purpose**: Fourth-generation framing: same scale/horizontal math as v3, only changes the vertical-anchor target ratio. **This IS the live production baseline formula.**
- **Callers**: `human_framing.py` imports `calculate_dynamic_framing` LIVE (`human_framing.py:61`); `hybrid_human_framing_v1.py` imports `calculate_dynamic_framing` and several constants LIVE (`hybrid_human_framing_v1.py:88-100`).
- **Calls**: re-exports several v3 symbols unchanged; `composite.composite_mockup` (own `main()` only).
- **Key function**: `calculate_dynamic_framing(bbox, source_size, garment_region)` returning `{"scale","x_offset","y_offset","bottom_correction_applied"}` (`human_dynamic_framing_test_v4.py:67-99`).
- **Explicit self-declared status**: docstring states plainly it is "imported directly by production... despite the 'prototype'/'experimental' framing... it is no longer test-only" (`human_dynamic_framing_test_v4.py:24-27`) — an important, otherwise-invisible fact about naming vs. reality (see PART 14).

### `hybrid_human_framing_v1.py` (419 lines)
- **Purpose**: Pose-corrected framing layered on top of the V4 baseline: bounded scale boost (legs/lower-body trim, "Correction C"), horizontal pose-anchor nudge ("Correction A"), vertical face-crop-aware nudge ("Correction B"), plus the same bottom-edge safety clamp as the baseline.
- **Callers**: `human_framing.py` imports `calculate_hybrid_framing` LIVE (`human_framing.py:62`).
- **Calls**: `human_dynamic_framing_test_v4.{...,calculate_dynamic_framing}`, `pose_landmark_experiment.{MODEL_PATH, build_landmarker, flatten_to_rgb, run_pose_detection, extract_landmarks}`.
- **Key function**: `calculate_hybrid_framing(bbox, source_size, garment_region, landmarks)` (`hybrid_human_framing_v1.py:170-260`) — returns a rich dict including `scale, x_offset, y_offset, framing_method`-adjacent debug fields (`scale_boost_applied, face_crop_adjusted, bottom_correction_applied`, plus canvas-space landmark positions for debug-image rendering).
- **Controller-relevant**: internal; `human_framing.py` extracts only `scale/x_offset/y_offset` from its return dict (`human_framing.py:294-298`).

### `pose_landmark_experiment.py` (187 lines)
- **Purpose**: Thin MediaPipe Pose Landmarker wrapper library — the ONLY place that talks to MediaPipe's pose API directly.
- **Callers**: `human_framing.py`, `hybrid_human_framing_v1.py` both import `MODEL_PATH, build_landmarker, flatten_to_rgb, run_pose_detection, extract_landmarks` LIVE.
- **Calls**: `mediapipe`, `numpy`, `PIL`.
- **Key functions**: `build_landmarker()` (constructs a `PoseLandmarker`, will raise if model file missing/mediapipe broken — this is why `human_framing.build_pose_landmarker_safe` wraps it in try/except), `flatten_to_rgb(path, background_color=(200,200,200))` (composites transparent PNG onto neutral gray before feeding a pose model — models expect real photos, not RGBA), `run_pose_detection`, `extract_landmarks` (extracts 5 named landmarks: `nose, left_shoulder, right_shoulder, left_hip, right_hip` via `LANDMARK_INDICES`, `pose_landmark_experiment.py:38-44`, from MediaPipe's 33-point BlazePose topology).
- **Controller-relevant**: internal; defines `MODEL_PATH = "models/pose_landmarker_lite.task"` (`pose_landmark_experiment.py:33`) which is an important artifact dependency (PART 5/PART 8).

## PART 3 — CALL GRAPH

### Main interactive run (`python batch_generate.py`)

```
batch_generate.main()
├── os.makedirs() for every workflow folder (idempotent)
├── prepare_input.find_first_zip(INPUT_DIR)                     -> zip_path or None (stop if None)
├── batch_generate.scan_backgrounds(BACKGROUNDS_DIR)             -> backgrounds[] (stop if empty)
├── human_framing.build_pose_landmarker_safe()                   -> pose_landmarker | None
├── human_framing.build_face_detector_safe()                     -> face_detector | None
├── LOOP (background selection / preview / approval):
│   ├── batch_generate.prompt_background_selection(backgrounds)  -> background_path | None(cancel)
│   ├── [first iteration only] prepare_input.extract_zip(zip_path)  -> wipes+fills working/extracted/
│   ├── [first iteration only] classify_mockups.inspect_image() + classify()  per extracted PNG -> grouped{}
│   ├── batch_generate.generate_category_previews(grouped, background_path, PREVIEW_DIR, pose_landmarker, face_detector)
│   │   ├── clear_preview_dir(PREVIEW_DIR)                       -> wipes preview/
│   │   ├── per present category (flat_front, human_model_front, back), first source only:
│   │   │   └── render_mockup_to_path(category, source, background_path, preview_path, pose_landmarker, face_detector)
│   │   │       ├── [human_model_front] human_dynamic_framing_test_v2.get_visible_bbox(source)
│   │   │       ├── [human_model_front] human_framing.calculate_human_model_framing(...)
│   │   │       │     (see "Human-model framing sub-call-graph" below)
│   │   │       └── composite.composite_mockup(..., shadow_preset=SHADOW_PRESETS[category])
│   │   │             └── shadow.composite_with_shadow(background, mockup, CANVAS_SIZE, position, preset)
│   │   │                   └── shadow.build_shadow_layer(...) -> (Gaussian blur, alpha rescale, tint, paste)
│   │   └── build_contact_sheet(previews, preview_dir)           -> 00-preview-contact-sheet.png
│   ├── print_preview_report(previews)
│   ├── batch_generate.prompt_approval_menu(contact_sheet_path)  -> "A" | "B" | "C"
│   └── A: break loop | B: continue loop (re-pick background) | C: cancel, rmtree(preview/), return
├── batch_generate.prompt_design_id()                             -> design_id | None
├── make_run_id(OUTPUT_ROOT_DIR)                                  -> "run-YYYY-MM-DD-NNN"
├── os.makedirs(assets_dir)
├── FULL BATCH LOOP: for category in (human_model_front, flat_front, back):
│   └── for source in grouped[category]:
│       └── render_mockup_to_path(...)   [same function/sub-call-graph as preview]
│           -> writes output/run-<id>/assets/<category-prefix>-NN.png
├── (finally) pose_landmarker.close(), face_detector.close()
├── build manifest dict (framing_method_counts, preview_categories_generated, ...)
├── json.dump -> output/run-<id>/manifest.json                   (written TWICE: once before, once after processed_input decision)
├── compute run_succeeded = (generated counts match classification counts) and (no errors)
├── move_processed_zip(zip_path, PROCESSED_INPUTS_DIR, run_id) if run_succeeded, else record failure reason
├── re-write manifest.json with "processed_input" key added
└── print human-readable summary report to stdout
```

### Human-model framing sub-call-graph (`human_framing.calculate_human_model_framing`)

```
calculate_human_model_framing(mockup_path, bbox, source_size, landmarker, face_detector=None)
├── Image.open(mockup_path).convert("RGBA").split()[-1]           -> alpha
├── human_dynamic_framing_test_v3.estimate_garment_region(alpha, bbox, source_size)  -> garment_region
├── human_dynamic_framing_test_v4.calculate_dynamic_framing(bbox, source_size, garment_region)  -> baseline_framing (V4)
│     [this baseline_framing is ALWAYS computed and is the guaranteed fallback]
├── if landmarker is None: return v2_result with framing_method="v2_fallback_no_landmarker"
├── try:
│   ├── pose_landmark_experiment.flatten_to_rgb(mockup_path)       -> rgb_image
│   ├── pose_landmark_experiment.run_pose_detection(landmarker, rgb_image) -> result, w, h
│   └── pose_landmark_experiment.extract_landmarks(result, w, h)   -> landmarks | None
│   except Exception: return fallback "v2_fallback_detection_error"
├── if not landmarks_are_usable(landmarks): return fallback "v2_fallback_low_confidence"
├── try: hybrid_human_framing_v1.calculate_hybrid_framing(bbox, source_size, garment_region, landmarks) -> hybrid_framing
│   except Exception: return fallback "v2_fallback_hybrid_error"
├── if not _framing_is_valid(hybrid_result): return fallback "v2_fallback_invalid_geometry"
├── if face_detector is not None:
│   ├── _detect_face_box(face_detector, rgb_image)                 -> face_box | None
│   └── if face_box: _apply_face_box_vertical_correction(hybrid_result, face_box, landmarks) -> corrected y_offset
│         if changed and valid: return "hybrid_v1_face_corrected"
└── else: return "hybrid_v1"
```

### Secondary entry points (each has its own standalone `main()`)

**`composite.py` main()**:
```
composite.main() -> glob.glob(MOCKUP_GLOB) [test-assets] -> composite_mockup(hardcoded SCALE/X_OFFSET/Y_OFFSET, shadow_preset=None)
                                                              -> output/test-composite.png
```

**`classify_mockups.py` main()**:
```
classify_mockups.main() -> glob PNG_GLOB [working/extracted/*.png] -> inspect_image() + classify() per file -> print report + totals
```
(No file writes — read-only reporting tool.)

**`prepare_input.py` main()**:
```
prepare_input.main() -> find_first_zip(input/) -> extract_zip() -> print report
```

**`human_dynamic_framing_test_v2.py` / `_v3.py` / `_v4.py` main()**:
```
main() -> glob test-assets/*.png -> filter human_model_front via classify_mockups
       -> per file: get_visible_bbox/get_alpha_and_bbox -> [estimate_garment_region for v3/v4] -> calculate_dynamic_framing()
       -> composite.composite_mockup(shadow_preset=None) -> output/human-dynamic-tests-v{2,3,4}/
       -> print report
```

**`hybrid_human_framing_v1.py` main()**:
```
main() -> requires models/pose_landmarker_lite.task to exist (else early-return)
       -> build_landmarker() -> per human_model_front file:
          get_alpha_and_bbox -> estimate_garment_region -> flatten_to_rgb -> run_pose_detection -> extract_landmarks
          -> if landmarks: calculate_hybrid_framing() else: pure V4 baseline
          -> composite.composite_mockup(shadow_preset=None) -> output/human-hybrid-framing-v1/
          -> save_debug_image() -> output/human-hybrid-framing-v1-debug/
       -> landmarker.close() -> print report
```

**`pose_landmark_experiment.py` main()**:
```
main() -> requires models/pose_landmarker_lite.task (else early-return)
       -> build_landmarker() -> per human_model_front file:
          flatten_to_rgb -> run_pose_detection -> extract_landmarks -> draw_debug_image()
          -> output/pose-detection-debug/
       -> landmarker.close() -> print report
```

Note: none of these secondary `main()`s write to `manifest.json`, `processed-inputs/`, or touch `input/` — they are purely diagnostic/development tools operating on `test-assets/`.

## PART 4 — ENTRY POINTS

| Module | Purpose | Args | Returns | Exceptions | Interactive? | Importable? | Reusable? | Headless? | Controller-safe? |
|---|---|---|---|---|---|---|---|---|---|
| `batch_generate.py` (`main()`) | Full production workflow | none (CLI, reads stdin) | none (prints report, writes files) | Not internally caught at top level except per-asset generation errors (caught into `errors[]`, `batch_generate.py:626-627`); a crash in extraction/classification/manifest-write would propagate and kill the process uncaught | **YES — blocks on `input()` up to 3 times** (background selection, approval menu, design ID) | Yes (`main` importable) but designed to run standalone | Partially — its helper functions (`scan_backgrounds`, `render_mockup_to_path`, `make_run_id`, `move_processed_zip`, etc.) are independently reusable and already factored for reuse | **NO as-is** — hard stdin dependency | **NO, not directly** — a Controller must either (a) drive it via a PTY/subprocess feeding stdin the exact A/B/C/design-id sequence, or (b) call its component functions directly, bypassing `main()`. See PART 13. |
| `prepare_input.py` (`main()`) | Extraction-only diagnostic | none | none (prints) | Uncaught `zipfile.BadZipFile`, `FileNotFoundError` etc. propagate | No | Yes | Yes — `extract_zip()`/`find_first_zip()` are the real reusable primitives | Yes | Yes (read/extract only, no side effects beyond `working/extracted/`) |
| `classify_mockups.py` (`main()`) | Classification-only diagnostic/report | none | none (prints) | Uncaught PIL errors on unreadable images propagate | No | Yes | Yes — `inspect_image()`/`classify()` are pure functions, no side effects | Yes | Yes (fully read-only, no writes at all) |
| `composite.py` (`main()`) | Proof-of-concept demo composite | none (hardcoded constants) | none | `FileNotFoundError` if `MOCKUP_GLOB` matches nothing (`composite.py:69-70`) | No | Yes | `composite_mockup()` itself is the real reusable primitive; `main()` is throwaway demo code | Yes | `composite_mockup()` yes; `main()` no (hardcoded paths reference `test-assets/`, not the production pipeline) |
| `human_dynamic_framing_test_v2.py` (`main()`) | v2 framing formula test harness | none | none | Uncaught errors propagate | No | Yes | `get_visible_bbox()` is production-reused; `calculate_dynamic_framing()` here is superseded, not production-reused | Yes | Its `main()`: internal-only tool. `get_visible_bbox` alone: Controller-irrelevant internal helper. |
| `human_dynamic_framing_test_v3.py` (`main()`) | v3 framing formula test harness | none | none | Uncaught errors propagate | No | Yes | `estimate_garment_region()` is production-reused | Yes | Internal only |
| `human_dynamic_framing_test_v4.py` (`main()`) | v4 framing formula test harness | none | none | Uncaught errors propagate | No | Yes | `calculate_dynamic_framing()` here IS the production baseline function, reused live | Yes | Internal only (the module is production-critical, but its `main()` entry point is a dev tool) |
| `hybrid_human_framing_v1.py` (`main()`) | Hybrid V1 test harness with debug-image output | none | none | Early-returns (not exception) if `models/pose_landmarker_lite.task` missing (`hybrid_human_framing_v1.py:331-333`) | No | Yes | `calculate_hybrid_framing()` is production-reused | Yes, but silently no-ops without the model file | Internal only |
| `pose_landmark_experiment.py` (`main()`) | Pose-detection debug-image generator | none | none | Early-returns if model missing (`pose_landmark_experiment.py:137-139`) | No | Yes | Its helper functions (`build_landmarker`, `flatten_to_rgb`, `run_pose_detection`, `extract_landmarks`) are production-reused | Yes, conditional on model file | Internal only |
| `shadow.py` | No `main()` / no `if __name__` block — pure library | n/a | n/a | n/a | No | Yes | Yes — `composite_with_shadow`, `build_shadow_layer` | Yes | Internal only |
| `human_framing.py` | No `main()` — pure library, the real production framing API | n/a | n/a | n/a | No | Yes | Yes — `calculate_human_model_framing`, `build_pose_landmarker_safe`, `build_face_detector_safe` never raise | Yes | **This is the module a Controller would call directly if bypassing `batch_generate.main()`'s stdin loop** — but note it still requires the full source-image + bbox + landmarker/face_detector plumbing `batch_generate.py` otherwise does for you. |

## PART 5 — DEPENDENCY MAP

### Classification subsystem (`classify_mockups.py`)
- Third-party: **Pillow** (`PIL.Image`) — load-bearing (alpha bbox measurement is the entire mechanism).
- Shared utilities used: none.
- Configuration touched: none (thresholds are hardcoded module constants, see PART 8).
- Architectural vs incidental: Pillow is architectural. The specific threshold values (60%, 1.5 ratio) are incidental tuning constants, explicitly documented as "not the exact numbers we measured... simple round thresholds" (`classify_mockups.py:34-36`).

### Framing/pose-detection subsystem (`human_framing.py`, `human_dynamic_framing_test_v2/v3/v4.py`, `hybrid_human_framing_v1.py`, `pose_landmark_experiment.py`)
- Third-party: **Pillow** (image geometry, alpha, drawing debug overlays), **mediapipe** (pose landmarker + face detector — both Apache-2.0, local CPU inference, no network calls at inference time per `pose_landmark_experiment.py:5-9`), **numpy** (array conversion for MediaPipe's `mp.Image`).
- Shared utilities used: `classify_mockups.{inspect_image, classify}` (each test-harness `main()` uses this to filter `test-assets/*.png` down to `human_model_front` only).
- Configuration touched: `models/pose_landmarker_lite.task` (pose model file, `MODEL_PATH` in `pose_landmark_experiment.py:33`), `models/blaze_face_short_range.tflite` (face model file, `FACE_MODEL_PATH` in `human_framing.py:82`) — both are **required files whose mere presence/absence changes runtime behavior** (feature-gate, not just data — see PART 8).
- Architectural vs incidental: mediapipe + the two model files are architectural to the *quality* of `human_model_front` framing but are explicitly designed to degrade gracefully — `build_pose_landmarker_safe()`/`build_face_detector_safe()` never raise (`human_framing.py:102-133`), so mediapipe/model absence is NOT a hard architectural dependency for the pipeline to run at all, only for it to run at *full quality*. Pillow is fully architectural (all geometry is alpha-channel-driven).

### Compositing subsystem (`composite.py`, `shadow.py`)
- Third-party: **Pillow** only (`Image`, `ImageFilter.GaussianBlur`).
- Shared utilities used: none.
- Configuration touched: `shadow.SHADOW_PRESETS` (hardcoded per-category dict), `composite.CANVAS_SIZE = (2000, 2000)` (hardcoded, load-bearing — background-size validation in `batch_generate.py:162-179` depends on it matching).
- Architectural vs incidental: Pillow's `Image.alpha_composite` (as opposed to `Image.paste` with a mask) is explicitly architectural — `shadow.py`'s docstring (lines 1-42) documents a real historical bug caused by using `paste()`'s mask argument on an already-opaque destination, and the fix (exclusively using `alpha_composite`) is now load-bearing correctness, not a style choice.

### Orchestration subsystem (`batch_generate.py`, `prepare_input.py`)
- Third-party: **Pillow** (`Image`, `ImageDraw` for the contact sheet).
- Shared utilities used: everything else in the repo (this is the integration layer).
- Configuration touched: hardcoded folder-name constants (`BACKGROUNDS_DIR`, `PREVIEW_DIR`, etc.), `PRESETS` dict (fixed placement for `flat_front`/`back`), `MANIFEST_VERSION = 1` (`batch_generate.py:142`).
- Architectural vs incidental: `hashlib` (SHA-256 verification in `move_processed_zip`'s copy-then-delete fallback, `batch_generate.py:429-434,463-479`) is architectural — it's the correctness guarantee that a ZIP is never deleted from `input/` unless verified byte-identical at its new location. `datetime`/`date` (run-ID sequencing, timestamps) are architectural to the run-folder-naming contract.

## PART 6 — WORKFLOW STATE MACHINE

The tool has **no persisted state machine** in the formal sense — there is no state file written between steps; the "state" at any moment is entirely inferable from filesystem contents (which folders exist / are non-empty) plus the in-memory Python call stack of the single running `batch_generate.main()` invocation. This has real consequences for a Controller (PART 14).

**States** (inferred from `batch_generate.main()`'s control flow, `batch_generate.py:510-757`):

1. **IDLE** — no ZIP in `input/`. Detected by `find_first_zip()` returning `None` (`batch_generate.py:524-527`). Terminal: process exits immediately, prints message, nothing else happens.
2. **NO_BACKGROUNDS** — ZIP present, but `backgrounds/` has zero supported images. Detected `batch_generate.py:530-536`. Terminal: process exits, ZIP left untouched, no output folder created.
3. **AWAITING_BACKGROUND_SELECTION** — inside the `while True` loop, waiting on `prompt_background_selection()`'s `input()` call (`batch_generate.py:549`). Persistence: none (in-memory only). Recovery: none — if the process is killed here, nothing has been written except possibly a stale `preview/` from a prior run.
4. **EXTRACTING_AND_CLASSIFYING** — happens exactly once per process run, on the first successful background pick (`grouped is None` check, `batch_generate.py:556-569`). Persistence: `working/extracted/` is populated on disk (wiped+rebuilt). Recovery: none formal — but since it's idempotent (deterministic re-extraction of the same ZIP), a fresh process restart from IDLE would simply redo this step identically.
5. **GENERATING_PREVIEWS** — `generate_category_previews()` runs, wiping and rebuilding `preview/` (`batch_generate.py:571-573`). Persistence: `preview/*.png` + `00-preview-contact-sheet.png` on disk.
6. **AWAITING_APPROVAL** — blocks on `prompt_approval_menu()`'s `input()` (`batch_generate.py:580`). Three transitions:
   - **A (Approve)** -> breaks the loop, proceeds to full-batch generation.
   - **B (Choose another background)** -> loops back to state 3, regenerating previews with a new background but WITHOUT re-extracting/re-classifying (reuses `grouped`).
   - **C (Cancel)** -> `shutil.rmtree(PREVIEW_DIR, ignore_errors=True)`, `input/` ZIP left untouched, process exits. This is the only state where `preview/` is deliberately cleaned up as part of the terminal transition (not just overwritten by the next run).
   - **EOF on stdin at either prompt is treated as Cancel** (`batch_generate.py:246-247` for approval; background-selection EOF returns `None` which also leads to cancel, `batch_generate.py:198-199,550-554`).
7. **AWAITING_DESIGN_ID** — blocks on `prompt_design_id()`'s `input()` (`batch_generate.py:591`). EOF here returns `None` silently (not a cancel — the run proceeds with `design_id=None`, `batch_generate.py:255-267`). This is an asymmetry worth noting: EOF means "cancel" at the two earlier prompts but "skip" here.
8. **GENERATING_FULL_BATCH** — the main `for category / for source_path` double loop (`batch_generate.py:604-627`). Persistence: `output/run-<id>/assets/*.png` written incrementally, one file at a time. Per-file errors are caught and appended to `errors[]` rather than aborting the whole run (`batch_generate.py:611-627`) — so this state can partially complete with some assets written and some skipped.
9. **WRITING_MANIFEST (pre-processed-input)** — `manifest.json` written once here WITHOUT the `processed_input` key or final `run_succeeded` (technically `run_succeeded` is computed after this first write and added before the second write — re-reading the code: `manifest["run_succeeded"] = run_succeeded` happens at `batch_generate.py:674` which is BEFORE the first-vs-second write... actually inspecting order: the manifest dict is built and written once at `batch_generate.py:660-662`, THEN `run_succeeded` is computed and added (`673-674`), THEN written AGAIN at `695-696` with `processed_input` also added). **So `manifest.json` is written to disk twice per run — an intermediate version without `run_succeeded`/`processed_input`, then the final version with both.** A Controller polling the manifest file mid-run could observe the intermediate (incomplete) version.
10. **MOVING_PROCESSED_INPUT** — `move_processed_zip()` (only if `run_succeeded`), or a synthetic failure-reason dict otherwise (`batch_generate.py:676-693`). Persistence: `processed-inputs/<zip>` created (move or verified-copy-then-delete); `input/` ZIP removed only in this state and only on the success path.
11. **DONE** — final print report to stdout, process exits normally.

**Cancellation paths**: only at states 3 (background selection) and 6 (approval menu) — both listed above. There is **no cancellation path once full-batch generation has started** (state 8 onward) — a kill at that point leaves a partially-populated `output/run-<id>/assets/` with no manifest at all (since manifest writing is after the full loop completes), which would look like a run that never finished, not a failed run per se (no `manifest.json` = ambiguous state for any external reader).

**Recovery/resume**: there is **no resume capability anywhere**. Every state above either completes atomically-ish within one process invocation or the whole run is abandoned; restarting `batch_generate.py` always starts over from IDLE (find_first_zip again), and if the same ZIP is still in `input/` it will be re-extracted/re-classified/re-generated from scratch into a NEW run folder (via `make_run_id`'s sequence-number logic, `batch_generate.py:394-402`) — it does not detect or resume a prior partial run.

## PART 7 — ARTIFACT DOCUMENTATION

### `manifest.json` (per-run, in `output/run-<id>/manifest.json`)
- **Producer**: `batch_generate.main()`, written twice (see PART 6, state 9).
- **Consumer**: intended for human review (README.md's validation-run checklist, README.md:207-221) and, per the README's "Future architectural direction" section, an eventual downstream Controller.
- **Lifecycle**: created once per successful-or-attempted full-batch run; never modified after the run's process exits; never deleted by any script.
- **Exact schema** (current code, `batch_generate.py:642-696`):
```json
{
  "manifest_version": 1,
  "run_id": "run-2026-07-21-001",
  "timestamp": "ISO-8601 string",
  "design_id": "string or null",
  "zip_filename": "string",
  "background_filename": "string",
  "preview_approval_status": "approved",
  "preview_categories_generated": ["flat_front", "human_model_front", "back"],
  "preview_sources": {"flat_front": "source_filename.png", "...": "..."},
  "total_files_extracted": 20,
  "category_counts": {"human_model_front": 0, "flat_front": 20, "back": 0, "unknown": 0},
  "output_counts": {"human_model_front": 0, "flat_front": 20, "back": 0},
  "assets_dir": "assets",
  "assets": [
    {
      "filename": "flat-front-01.png",
      "path": "assets/flat-front-01.png",
      "category": "flat_front",
      "source_filename": "original-printful-filename.png",
      "framing_method": "hybrid_v1 | hybrid_v1_face_corrected | v2_fallback_* | null",
      "background_filename": "Background Name.png"
    }
  ],
  "errors": ["source_filename.png: exception message", "..."],
  "framing_method_counts": {"hybrid_v1": 5, "v2_fallback_low_confidence": 1},
  "run_succeeded": true,
  "processed_input": {
    "original_filename": "archive (8).zip",
    "attempted": true,
    "move_succeeded": true,
    "method": "move | copy_then_delete_fallback | null",
    "destination_path": "processed-inputs\\archive (8).zip",
    "run_id": "run-2026-07-19-002",
    "timestamp": "ISO-8601 string",
    "reason": "string or null"
  }
}
```
- **Versioning**: `manifest_version` (currently `1`) is explicitly separate from the application/pipeline version (README.md:117-118, `batch_generate.py:140-142`) — intended to increment ONLY when the field structure changes.
- **IMPORTANT — observed schema drift**: actual on-disk manifests (e.g. `output/run-2026-07-19-002/manifest.json`) do **NOT** contain `manifest_version`, `design_id`, or `run_succeeded` fields at all — those runs predate the current `batch_generate.py` code. This confirms the schema has evolved and **old manifests on disk are not guaranteed to match the current schema** — any Controller reading historical manifests must treat missing keys as "field didn't exist yet," not as an error.
- **Public contract vs internal**: the overall shape (top-level keys, `assets[]` structure, `run_succeeded`, `processed_input`) is the closest thing this repo has to a public API and is explicitly documented as such in the README (README.md:117-132). The exact wording of `errors[]` strings and `processed_input.reason` strings is NOT contractual — free text.

### `output/run-<id>/assets/*.png`
- **Producer**: `render_mockup_to_path()` inside the full-batch loop.
- **Consumer**: the operator (for Etsy listing use), and per the README's future direction, an eventual "Asset Library."
- **Lifecycle**: one per classified+generated source image; filename is `<category-prefix>-NN.png` where NN is a 1-based, per-category, 2-digit zero-padded sequence in classification/glob order (`batch_generate.py:604-609`); never modified after creation.
- **Schema**: 2000x2000 RGBA-flattened-to-opaque PNG (forced fully opaque by `shadow.composite_with_shadow`'s final step, `shadow.py:167-170`).
- **Versioning**: none — a re-run always creates a new `run-<id>` folder (via `make_run_id`), never overwrites a prior run's assets.
- **Public contract**: filename pattern (`flat-front-NN.png`, `human-model-front-NN.png`, `back-NN.png`) and folder layout (flat `assets/`, no per-category subfolders) is the current stable contract — the README explicitly warns this "only applies to runs created after this change; older `output/run-*` folders keep their original per-category-subfolder layout and are not migrated" (README.md:130-132) — i.e. **older run folders under `output/` do not follow the current schema and must not be assumed to**.

### `preview/*.png` and `00-preview-contact-sheet.png`
- **Producer**: `generate_category_previews()` / `build_contact_sheet()`.
- **Consumer**: the human operator during the approval step; never read by any script downstream.
- **Lifecycle**: fully ephemeral — wiped and rebuilt on every preview generation cycle (including every "B: choose another background" loop iteration) and on cancel.
- **Schema**: `01-preview-flat-front.png`, `02-preview-human-model-front.png`, `03-preview-back.png` (whichever categories present) — true production-pipeline renders (not thumbnails) — plus `00-preview-contact-sheet.png` (RGB, side-by-side labeled thumbnails, `CONTACT_SHEET_THUMB_SIZE=640`, `batch_generate.py:145`).
- **Public contract**: NONE — explicitly "not production output and none of it is a listing asset" (README.md:194-195).

### `manifest.json` inside `output/regression-references/loose-human-model/`
- **Producer**: hand-curated (NOT generated by `batch_generate.py`).
- **Consumer**: human engineers doing visual regression comparison.
- **Schema** (different from run manifests!):
```json
{
  "description": "string",
  "entries": [
    {
      "label": "string",
      "source_filename": "string",
      "reference_filename": "string",
      "classification": "human_model_front",
      "scale": 1.7311608961303462,
      "x_offset": -800,
      "y_offset": -796,
      "framing_method": "hybrid_v1_face_corrected"
    }
  ]
}
```
- **Public contract**: this is explicitly a DO-NOT-DELETE reference artifact (`output/regression-references/README.md:23`), not part of the pipeline's normal I/O.

### ZIP file lifecycle (`input/*.zip` -> `processed-inputs/*.zip`)
- **Producer of the input ZIP**: external (Printful export, placed by the operator).
- **Producer of the move**: `move_processed_zip()` (`batch_generate.py:437-507`).
- **Consumer**: none downstream reads `processed-inputs/` programmatically — it's an archival destination only.
- **Lifecycle**: exists in `input/` until a fully-successful run moves it; on failure it's left in `input/` for re-diagnosis/re-run. Naming collisions in `processed-inputs/` are resolved via `unique_processed_input_path()`: plain name -> `<name>__<run-id>.ext` -> `<name>__<run-id>-N.ext` (`batch_generate.py:405-426`).
- **Move integrity guarantee**: `shutil.move` first; on `OSError` (documented as a Windows file-lock scenario), falls back to `shutil.copy2` + size+SHA-256 verification + `os.remove(source)` — the source is **never removed unless the copy is byte-verified identical** (`batch_generate.py:459-497`).

### Model weight files (`models/pose_landmarker_lite.task`, `models/blaze_face_short_range.tflite`)
- **Producer**: external — one-time manual download (see `.claude/settings.local.json`'s allowed `curl` commands pointing at `storage.googleapis.com/mediapipe-models/...`), not generated or version-controlled by any script here.
- **Consumer**: `pose_landmark_experiment.build_landmarker()` and `human_framing.build_face_detector_safe()`.
- **Lifecycle**: static, presence-checked at each run (`os.path.exists(MODEL_PATH)`, `os.path.exists(FACE_MODEL_PATH)`) — absence degrades framing quality but never crashes the pipeline.

## PART 8 — CONFIGURATION SYSTEM

**There is no config-file system, no `.env`, no environment-variable reads anywhere in the repo** (confirmed by reading every root `.py` file in full — no `os.environ` or `os.getenv` call appears anywhere). All "configuration" is hardcoded Python module-level constants. This is a deliberate, simple design, not an oversight — the README frames tuning presets as "simple starting points to visually test and adjust by hand — not an automatic placement system" (`batch_generate.py:84-85`).

**Config-by-constant, by module:**
- `batch_generate.py`: `BACKGROUNDS_DIR="backgrounds"`, `WORKING_DIR="working"`, `PREVIEW_DIR="preview"`, `OUTPUT_ROOT_DIR="output"`, `PROCESSED_INPUTS_DIR="processed-inputs"`, `PROCESSED_OUTPUTS_DIR="processed-outputs"`, `BACKUPS_DIR="backups"`, `BACKGROUND_IMAGE_EXTENSIONS=(".png",".jpg",".jpeg")`, `PRESETS` dict (flat_front/back scale=1.0, offsets=0), `CATEGORY_FOLDERS` (category->filename-prefix map), `PREVIEW_CATEGORY_ORDER`, `PREVIEW_FILENAMES`, `MANIFEST_VERSION=1`, `CONTACT_SHEET_FILENAME/THUMB_SIZE/LABEL_HEIGHT/BACKGROUND_COLOR/LABEL_COLOR`.
- `classify_mockups.py`: `VISIBLE_PERCENT_SPLIT=60.0`, `ASPECT_RATIO_SPLIT=1.5` (`classify_mockups.py:41,46`).
- `composite.py`: `CANVAS_SIZE=(2000,2000)` — this is the single most load-bearing constant in the repo; `batch_generate.background_size_error` validates every candidate background against it exactly.
- `shadow.py`: `SHADOW_PRESETS` dict — the entire shadow "config" for all 3 categories, including the shared `_FLAT_GARMENT_GLOW_LAYERS` list (documented tuning history embedded directly in comments, `shadow.py:60-78`).
- `human_framing.py`: `MIN_LANDMARK_CONFIDENCE=0.5`, `REQUIRED_LANDMARK_NAMES=[...]`, `FACE_MODEL_PATH`, `MIN_FACE_SCORE=0.5`, `MAX_FACE_BOTTOM_CANVAS_Y_PX=40`, `TORSO_TOP_PADDING_RATIO=0.15`, `DESIGN_TOP_SAFETY_MARGIN_PX=0`.
- `human_dynamic_framing_test_v3.py`/`v4.py`: `GARMENT_BAND_TOP_RATIO=0.15`, `GARMENT_BAND_BOTTOM_RATIO=0.55`, `TARGET_GARMENT_WIDTH_RATIO=0.85`, `TARGET_TORSO_CENTER_Y_RATIO=0.45` (reverted from an earlier 0.40, with the historical reasoning preserved in the v4 docstring), `TARGET_HORIZONTAL_CENTER_RATIO=0.50`, `BOTTOM_TOUCH_MARGIN_RATIO=0.02`.
- `hybrid_human_framing_v1.py`: `TORSO_ANCHOR_RATIO=0.40`, `LOWER_BODY_SLACK_ALLOWANCE_PX=250`, `SCALE_BOOST_RATIO=1.10`, `CORRECTION_C_DEADBAND_PX=60`, `MAX_NOSE_CANVAS_Y_PX=75`.
- `pose_landmark_experiment.py`: `MODEL_PATH="models/pose_landmarker_lite.task"`, `LANDMARK_INDICES` (5 named BlazePose indices).

**"Config discovery" mechanism**: modules simply `import` constants from each other directly (Python name binding), e.g. `human_dynamic_framing_test_v4.py` imports `TARGET_GARMENT_WIDTH_RATIO` etc. straight from `human_dynamic_framing_test_v3.py` (`human_dynamic_framing_test_v4.py:39-50`) rather than redefining them — this is the repo's only form of "shared configuration," and it is fragile in the sense that changing a constant in v3 silently ripples into v4/hybrid without any explicit versioning or override mechanism.

**Defaults/overrides**: there is no override mechanism at all (no CLI flags, no config file merge) — every constant is a single hardcoded value; "overriding" means editing the `.py` file directly.

## PART 9 — PUBLIC API SURFACE

Treating this repo as a library an external Controller depends on:

**Stable / Controller-relied-upon:**
- **Launch contract**: `python batch_generate.py`, run from the repo root, with `input/` containing exactly one Printful-style ZIP and `backgrounds/` containing at least one exact-2000x2000 image.
- **Interaction contract**: three `input()` prompts in fixed order — background number (or `Q`), then `A`/`B`/`C` approval, then optional design-ID text. (Documented, deliberate, and the ONLY sanctioned interaction surface — see PART 13 on why a Controller must drive this via stdin rather than reimplementing it.)
- **Output folder contract**: `output/run-<date>-<seq>/assets/*.png` + `output/run-<date>-<seq>/manifest.json`, per PART 7's schema, for runs created going forward.
- **Completion signal**: presence of `manifest.json` with `run_succeeded: true` (only guaranteed present on current-schema runs — see the schema-drift caveat in PART 7) is the authoritative success signal; the same value gates whether the ZIP was moved to `processed-inputs/`.
- **Error reporting**: `manifest.json`'s `errors[]` array (free-text per-asset failure strings) and `processed_input.reason` (free-text failure explanation) are the only structured error channels; stdout print statements are the only other error surface (not machine-parseable beyond eyeballing).
- **Category taxonomy**: `flat_front`, `back`, `human_model_front`, `unknown` — stable strings appearing in `manifest.json`.
- **Framing-method taxonomy**: `hybrid_v1`, `hybrid_v1_face_corrected`, and the five `v2_fallback_*` reasons — stable strings appearing in `manifest.json`, described in the README (README.md:149-155).

**Internal — do not rely on:**
- Every constant/threshold value (60% visible-percent split, 1.5 aspect ratio, all framing ratios, shadow blur/opacity numbers) — tuning detail, subject to change without notice, no versioning attached to them individually.
- `composite_mockup()`, `composite_with_shadow()`, `build_shadow_layer()`, `calculate_human_model_framing()`, `calculate_hybrid_framing()`, `calculate_dynamic_framing()` (any version), `estimate_garment_region()`, `get_visible_bbox()`/`get_alpha_and_bbox()` — all internal Python functions with no external stability guarantee; not designed to be imported by anything outside this repo.
- `working/extracted/`, `preview/` — explicitly ephemeral, never a contract.
- `archive/`, `backups/` — explicitly historical, never read by production code (`archive/README.md:3`).
- `processed-outputs/` — reserved, unused, not yet part of any contract.
- Older `output/run-*` folders with per-category subfolders — explicitly NOT migrated to the current schema (README.md:130-132).
- Everything in `human_dynamic_framing_test_v2.py`'s `calculate_dynamic_framing()` (superseded, only `get_visible_bbox` from that module is live).

## PART 10 — CODE ORGANIZATION

There are **no formal layers, packages, or plugin abstractions** in this repo — it is a flat collection of top-level `.py` files connected purely by direct Python imports. That said, a de facto layering exists by convention:

1. **Orchestration layer**: `batch_generate.py` (and, for standalone use, `prepare_input.py`). Owns stdin interaction, filesystem side effects (folder creation, ZIP move, manifest write), and sequencing.
2. **Domain-logic layer**: `classify_mockups.py` (classification rules), `human_framing.py` + the `human_dynamic_framing_test_v*.py` chain + `hybrid_human_framing_v1.py` (framing math), `shadow.py` (shadow rendering rules). These are pure-ish functions operating on Pillow `Image`/geometry data, largely side-effect-free except for their own optional `main()` demo harnesses.
3. **IO/rendering primitive layer**: `composite.py` (the actual pixel compositing), `pose_landmark_experiment.py` (MediaPipe IO wrapper).

**Separation of concerns is loose, not strict**: domain-logic modules like `human_dynamic_framing_test_v2/v3/v4.py` each carry their OWN `main()` with IO/rendering calls baked in (they call `composite.composite_mockup` directly from their own test harness), so "pure logic" and "demo/test IO" live in the same file. This is explicit and accepted by the repo's own convention — each such module states in its docstring exactly what it does NOT touch (a self-imposed blast-radius declaration, e.g. `human_dynamic_framing_test_v3.py:21-24`: "Does not touch flat_front/back presets, batch_generate.py, composite.py, classify_mockups.py, source images, or the v1/v2 test outputs").

**"Provider" pattern (informal)**: the framing subsystem behaves like a provider chain even though there's no formal interface/ABC — `human_framing.calculate_human_model_framing()` tries Hybrid V1, falls back to V4-baseline, with an optional face-correction "provider" layered on top. This is implemented as sequential try/except + validity checks, not a registered-plugin system (`human_framing.py:251-313`).

**Shared helper conventions**:
- Every framing module that has a `main()` re-derives its own `human_model_front` fixture list via `classify_mockups.{inspect_image, classify}` against `test-assets/*.png` — a repeated (not centralized) pattern across `human_dynamic_framing_test_v2/v3/v4.py` and `hybrid_human_framing_v1.py`.
- "Safe builder" convention: `human_framing.build_pose_landmarker_safe()` / `build_face_detector_safe()` both follow the same pattern (check file exists -> try/except around construction -> return `None` on any failure, never raise) — this is the repo's one deliberate resilience convention, explicitly called out in `human_framing.py`'s docstring (lines 27-35) as something production needed that the underlying experiment code didn't have.
- Docstring-as-changelog convention: nearly every module's top-of-file docstring embeds its own tuning/version history (e.g. `shadow.py:52-78`, `human_dynamic_framing_test_v4.py:14-22`) — this repo uses source-code docstrings as its primary decision/change record rather than a separate `CHANGELOG.md` or `docs/`.

## PART 11 — SEQUENCE DIAGRAM(S)

### Main workflow (happy path, one background choice, straight to approval)

```
Operator          batch_generate.py       prepare_input     classify_mockups   composite/shadow    human_framing/pose      filesystem
   |                     |                       |                  |                  |                    |                  |
   |--run script-------->|                       |                  |                  |                    |                  |
   |                     |--find_first_zip()---->|                  |                  |                    |                  |
   |                     |<--zip_path------------|                  |                  |                    |                  |
   |                     |--scan_backgrounds()-------------------------------------------------------------------------------->|
   |                     |<--backgrounds[]--------------------------------------------------------------------------------------|
   |                     |--build_pose_landmarker_safe()---------------------------------->|                  |                  |
   |                     |--build_face_detector_safe()------------------------------------>|                  |                  |
   |<--print numbered bg list, prompt-------------|                  |                  |                    |                  |
   |--choose "2"-------->|                       |                  |                  |                    |                  |
   |                     |--extract_zip(zip_path)------->|          |                  |                    |                  |
   |                     |                       |--wipe+extract-------------------------------------------------------------->|
   |                     |<--extracted_names-------------|          |                  |                    |                  |
   |                     |--inspect_image()+classify() per PNG------>|                  |                    |                  |
   |                     |<--grouped{}-----------------------------|                  |                    |                  |
   |                     |--generate_category_previews()            |                  |                    |                  |
   |                     |    clear_preview_dir()---------------------------------------------------------------------------->|
   |                     |    render_mockup_to_path() per category  |                  |                    |                  |
   |                     |      [human_model_front] get_visible_bbox()--------------------------------------->|                  |
   |                     |      [human_model_front] calculate_human_model_framing()------------------------->|                  |
   |                     |                                                              (V4 baseline, then Hybrid V1 attempt,   |
   |                     |                                                               then optional face correction)         |
   |                     |      composite_mockup(scale,x,y,shadow_preset)------------->|                  |                    |
   |                     |                                          |--composite_with_shadow()-->|          |                  |
   |                     |                                          |<--flattened opaque image----|          |                  |
   |                     |<--preview PNG written----------------------------------------------------------------------------->|
   |                     |    build_contact_sheet()---------------------------------------------------------------------------->|
   |<--print preview report + point at 00-preview-contact-sheet.png-|                  |                    |                  |
   |<--prompt A/B/C-------|                       |                  |                  |                    |                  |
   |--"A"---------------->|                       |                  |                  |                    |                  |
   |<--prompt design ID---|                       |                  |                  |                    |                  |
   |--"" (skip)---------->|                       |                  |                  |                    |                  |
   |                     |--make_run_id()---------------------------------------------------------------------------------->|
   |                     |--for each category, for each source: render_mockup_to_path()->composite_mockup()->write asset--->|
   |                     |--build manifest, json.dump()--------------------------------------------------------------------->|
   |                     |--compute run_succeeded, move_processed_zip() if true----------------------------------------------->|
   |                     |--json.dump() manifest again (adds processed_input)----------------------------------------------->|
   |<--print final run summary report-------------|                  |                  |                    |                  |
```

### Preview-approval loop (as its own diagram — this is the distinct interaction pattern)

```
Operator                              batch_generate.py
   |                                        |
   |<-- numbered background list -----------|
   |-- pick background N ------------------>|
   |                                        |-- generate_category_previews() (re-render EVERY present category
   |                                        |    through the real production pipeline, overwrite preview/)
   |<-- preview report + contact-sheet path-|
   |<-- "A = Approve / B = Choose another background / C = Cancel" --|
   |                                        |
   |-- "B" -------------------------------->|
   |                                        |  (LOOP BACK to background list -- grouped[] is
   |                                        |   NOT recomputed, only previews regenerate)
   |<-- numbered background list -----------|
   |-- pick DIFFERENT background M -------->|
   |                                        |-- generate_category_previews() (wipes preview/ first,
   |                                        |    fresh renders against new background)
   |<-- preview report + contact-sheet path-|
   |<-- "A / B / C" prompt again ------------|
   |                                        |
   |-- "A" -------------------------------->|
   |                                        |  loop exits, proceeds to design-ID prompt then full batch
   |
   ALTERNATE: |-- "C" or EOF -------------->|  shutil.rmtree(preview/); ZIP left untouched in input/; process exits
```

Key property visible in this diagram: **every preview shown to the operator is generated through the exact same `render_mockup_to_path()` function used for full production output** (`batch_generate.py:270-295`) — there is no separate "cheap preview" rendering path, which is why the README calls these "true final-output previews, not raw extracted mockups" (README.md:72-73).

## PART 12 — CLASS AND SERVICE OVERVIEW

**This repo is entirely function-based. There are no architecturally significant classes defined anywhere in the codebase** (confirmed by reading every root `.py` file in full — zero `class` statements appear in any of `batch_generate.py`, `prepare_input.py`, `classify_mockups.py`, `composite.py`, `shadow.py`, `human_framing.py`, `human_dynamic_framing_test_v2/v3/v4.py`, `hybrid_human_framing_v1.py`, `pose_landmark_experiment.py`).

There are also **no dataclasses, no NamedTuples, no TypedDicts, no Pydantic models** anywhere — every structured value (framing results, manifest entries, classification info, shadow presets) is a **plain Python `dict`** built with dict literals or `{**a, "key": value}` spread syntax. Examples that function as de facto data contracts despite being untyped dicts:
- `classify_mockups.inspect_image()` return: `{"filename", "visible_percent", "aspect_ratio"}`.
- `human_framing.calculate_human_model_framing()` return: `{"scale", "x_offset", "y_offset", "framing_method"}`.
- `hybrid_human_framing_v1.calculate_hybrid_framing()` return: a larger dict with `scale/x_offset/y_offset` plus debug-only fields.
- `shadow.SHADOW_PRESETS[category]`: `{"style": "directional"|"glow_layered", ...style-specific keys}`.
- The `manifest.json` structure itself (PART 7) is the closest thing to a "schema" in the whole repo, and it too is just a dict serialized via `json.dump`.

The **objects that DO carry meaningful state** are third-party, not repo-defined:
- `PIL.Image.Image` instances (passed around extensively as the working representation of pixel data).
- MediaPipe's `PoseLandmarker` and `FaceDetector` objects (`landmarker`, `face_detector` in `batch_generate.main()`) — these are the only objects with an explicit lifecycle (`build_*` constructs them once per `batch_generate.py` process, `.close()` is called in a `finally` block, `batch_generate.py:628-632`) and are threaded through the entire run as parameters rather than held as module/global state.

**Conclusion for the handbook**: any Controller design should NOT expect to find or instantiate any repo-defined class/service object — the entire external contract is "run this script, read these files," and the entire internal contract is "call these functions with these dict shapes."

## PART 13 — CONTROLLER TOUCHPOINTS

**What a Controller should LAUNCH:**
- `python batch_generate.py`, cwd = repo root, with `input/` pre-populated with exactly one ZIP.
- Since it's interactive, the Controller must supply stdin programmatically: it needs to (1) read the printed numbered background list from stdout to pick an index, or always send a predetermined index/name; (2) send `A` once previews look acceptable (there is no way to inspect them programmatically other than opening `00-preview-contact-sheet.png`, since there's no headless-approval flag); (3) send an empty line or a design-ID string.
- **There is currently no flag or environment variable to run headlessly / non-interactively.** A Controller has two real options: drive the subprocess's stdin/stdout as a pseudo-operator (fragile, tied to exact prompt text), or bypass `batch_generate.main()` entirely and call its component functions directly in a custom driver script (`scan_backgrounds`, `generate_category_previews`, `render_mockup_to_path`, `make_run_id`, `move_processed_zip`, etc. are all independently importable and side-effect-scoped) — the module was evidently written with this reuse in mind (its own docstring frames it as "Connects the pieces that already exist and work on their own," `batch_generate.py:4`).

**What a Controller should MONITOR:**
- The existence and mtime of `output/run-<date>-<seq>/manifest.json` as the completion signal — but be aware it's written twice (see PART 6, state 9); a Controller should only trust a manifest read AFTER the process has exited, or should specifically check for the presence of the `processed_input` key as proof the second/final write happened.
- `manifest.json`'s `run_succeeded`, `errors[]`, and `framing_method_counts` for pass/fail and quality signal.
- Whether the ZIP disappeared from `input/` and appeared in `processed-inputs/` as an independent corroborating success signal.

**What a Controller should CONSUME:**
- `output/run-<id>/manifest.json` and `output/run-<id>/assets/*.png` — the two stable, documented artifacts (PART 7, PART 9).
- Nothing else is designed for external consumption.

**What a Controller should NEVER access:**
- `working/extracted/` and `preview/` — explicitly ephemeral scratch, wiped on every run/preview cycle; reading them mid-run is a race condition, and treating their contents as durable is explicitly warned against (README.md:183-186).
- `archive/`, `backups/` — explicitly historical/manual, never read by production code, no schema guarantee.
- `processed-outputs/` — reserved and currently meaningless (always empty).
- Any internal function not listed as reusable in PART 4/PART 9 (e.g. don't call `_build_single_shadow_layer`, `_detect_face_box`, `_apply_face_box_vertical_correction` — leading-underscore names are the closest thing this repo has to "private," and they are consistently used that way).
- Individual tuning constants (thresholds, ratios, blur radii) — treat these as implementation detail even though they're technically importable Python names.

**What should remain encapsulated forever within this repo:**
- The entire framing-formula fallback chain (V4 baseline -> Hybrid V1 -> face correction) and its constants — this is precisely the kind of "don't reimplement, just call" logic the README's own module docstrings repeatedly emphasize (e.g. `human_framing.py:18-21`: "This module wires the validated experimental logic into production. It does NOT reimplement or redesign anything").
- The shadow-compositing alpha-math fix (`Image.alpha_composite` vs. self-masked `paste()`) — a Controller should never attempt to reimplement compositing itself; it is documented as a fixed historical bug class (`shadow.py:1-42`).
- MediaPipe model lifecycle (build/close) — a Controller orchestrating multiple runs should let each `batch_generate.py` invocation manage its own landmarker/detector lifecycle rather than trying to share/pool it externally, since there's no support for that in the current code.

## PART 14 — HIDDEN ASSUMPTIONS

- **Filename convention assumption**: `classify_mockups.classify()` trusts that Printful export filenames literally contain the substring `"-back-"` or `"-front-"` (`classify_mockups.py:86,89`) — there is no validation that this holds; a differently-named export ZIP would silently classify everything as `unknown` rather than erroring.
- **Exactly-one-ZIP assumption**: `find_first_zip()` (`prepare_input.py:13-17`) silently takes the FIRST glob match if multiple ZIPs are ever present in `input/` — there is no warning or error for "more than one ZIP found," despite the README's "exactly one ZIP is expected per run" (README.md:40).
- **Exact-canvas-size assumption**: every background must be EXACTLY 2000x2000 (`composite.CANVAS_SIZE`) — not "at least," not "will be resized," an exact match is required by `Image.alpha_composite`'s size constraint (`batch_generate.py:166-169` explains this is inherited, not a new rule invented at the check site).
- **Ordering assumption — glob sort determines asset numbering**: `sorted(glob.glob(PNG_GLOB))` (`batch_generate.py:560`) determines both classification-group order and therefore the `-01`, `-02`, ... suffixes in output filenames — this means the SAME ZIP re-extracted twice will always number assets identically (good for determinism), but if Printful ever changes its internal file-naming/hash scheme, output numbering could silently shift with no semantic meaning attached to the numbers (they are NOT a stable identifier across runs, only within one run of one ZIP).
- **"Representative preview" assumption**: the preview step only renders the FIRST source file per category (`sources[0]`, `batch_generate.py:364`) — if a ZIP contains 20 `flat_front` shirts in wildly different colors/prints, only ONE of them is shown for approval; approval is a proxy judgment on framing/background quality, not per-item content review.
- **Manifest double-write assumption**: `manifest.json` is written twice per run with different content each time (PART 6) — any process reading it mid-run (rather than after process exit) could see an incomplete version missing `run_succeeded`/`processed_input`. Nothing in the code communicates "this is the final write" other than presence of those keys.
- **No formal "run in progress" marker**: there is no lock file, PID file, or `.inprogress` marker anywhere — if two operators (or a Controller and a human) ran `batch_generate.py` concurrently against the same `input/`/`working/`/`preview/` folders, they would race and corrupt each other's `working/extracted/` and `preview/` (both use unconditional wipe-then-rebuild). The system implicitly assumes single-operator, single-process, sequential use.
- **"Prototype" naming does not mean prototype status**: `human_dynamic_framing_test_v4.py`, `human_dynamic_framing_test_v3.py`, and `hybrid_human_framing_v1.py` all have filenames and internal docstring language ("Prototype v3", "Prototype v4", "Experiment: Hybrid Human Framing v1") suggesting throwaway/experimental status, but they are **live, load-bearing production code** imported directly by `human_framing.py`. Only `human_dynamic_framing_test_v2.py`'s `calculate_dynamic_framing` (not its `get_visible_bbox`) is genuinely superseded/unused for its framing formula. A naive reader relying on filenames alone would misjudge which modules are safe to ignore or delete.
- **EOF-handling asymmetry**: EOF on stdin means "cancel the whole run" at the background-selection and approval prompts, but means "skip, proceed with `design_id=None`" at the design-ID prompt (`batch_generate.py:198-199,246-247,263-267`) — a Controller feeding stdin programmatically must close/EOF stdin only after sending "A" and before/at the design-ID prompt, or risk an unintended cancel.
- **Backup files are NOT restore points a script would ever consult**: the `backups/*.bak*` and `*.zip` snapshots in `backups/` are pure manual artifacts (evidenced by ad hoc naming like `shadow.py.bak-20260717212537`) — no script in the repo reads from or restores from this folder; it exists purely for a human to manually `cp` back if needed.
- **`processed-outputs/` is a documented no-op**: its existence might mislead a Controller into thinking there's a second output-archival step to watch for — the README explicitly states it "is reserved for a later workflow step and is currently unused" (README.md:175).
- **Review assumption baked into the preview loop**: approval is scoped to "does the framing/background combination look right," not "is every individual asset correct" — the full-batch step can still produce framing fallbacks (`v2_fallback_*`) or per-asset errors that were never previewed, since only one representative image per category was shown before approval was granted.

---

**Key files referenced throughout (absolute paths):**
`E:\Vilicity\etsy-mockup-generator\batch_generate.py`, `E:\Vilicity\etsy-mockup-generator\prepare_input.py`, `E:\Vilicity\etsy-mockup-generator\classify_mockups.py`, `E:\Vilicity\etsy-mockup-generator\composite.py`, `E:\Vilicity\etsy-mockup-generator\shadow.py`, `E:\Vilicity\etsy-mockup-generator\human_framing.py`, `E:\Vilicity\etsy-mockup-generator\human_dynamic_framing_test_v2.py`, `E:\Vilicity\etsy-mockup-generator\human_dynamic_framing_test_v3.py`, `E:\Vilicity\etsy-mockup-generator\human_dynamic_framing_test_v4.py`, `E:\Vilicity\etsy-mockup-generator\hybrid_human_framing_v1.py`, `E:\Vilicity\etsy-mockup-generator\pose_landmark_experiment.py`, `E:\Vilicity\etsy-mockup-generator\requirements.txt`, `E:\Vilicity\etsy-mockup-generator\README.md`, `E:\Vilicity\etsy-mockup-generator\archive\README.md`, `E:\Vilicity\etsy-mockup-generator\output\regression-references\README.md`.

agentId: a1ef4359042aa0e63 (use SendMessage with to: 'a1ef4359042aa0e63', summary: '<5-10 word recap>' to continue this agent)
<usage>subagent_tokens: 175942
tool_uses: 21
duration_ms: 353397</usage>

---

# CHAPTER B — Etsy-AI-Image-Generator

# Etsy-AI-Image-Generator — Automation Controller Handbook

All paths below are relative to `E:\Vilicity\Etsy-AI-Image-Generator` unless stated otherwise. Citations are `file:line`.

---

## PART 1 — REPOSITORY MAP

```
Etsy-AI-Image-Generator/
├── src/                          # All application code (flat package, no subfolders)
├── config/                       # Global + per-store + per-campaign configuration ("brand brain")
│   ├── image_generation.json
│   ├── image_generation_system_prompt.txt / system_prompt_metadata.json
│   └── stores/<store_id>/
│       ├── store.json, store_prompt.txt, store_prompt_metadata.json
│       ├── human_philosophy.txt, human_philosophy_metadata.json (optional)
│       └── campaigns/<campaign_id>.json, <campaign_id>_prompt.txt,
│                     <campaign_id>_prompt_metadata.json,
│                     <campaign_id>_creative_dna.json (optional)
├── jobs/<job_name>/              # One folder per product job — all runtime state lives here
│   ├── reference_images/
│   ├── config/  (generation_config.json, creative_brief.json, reference_assets.json)
│   ├── outputs/
│   │   ├── concepts/  (concept_planning_context.json, shot_plan.json, *_concepts.json, ...)
│   │   ├── manual_concept_generation/  (legacy manual-ChatGPT concept path)
│   │   ├── prompts/<ai|lifestyle>/<concept_id>/  (prompt_package.json, user_prompt.txt)
│   │   ├── prompts/system/  (rendered system-prompt snapshots)
│   │   ├── manual_image_generation/<category>/<concept_id>/  (manual ChatGPT image path)
│   │   ├── generated/<category>/<concept_id>/  (generation_metadata.json, image files, snapshots)
│   │   ├── approved/ , rejected/  (copies of reviewed generated assets)
│   │   └── approved_media_handoff.json
│   ├── archive/run_NNN/          # job_reset.py's pre-reset backups
│   └── job_manifest.json         # machine-readable status summary, rebuilt after almost every stage
├── docs/
│   ├── provider_readiness.md     # provider architecture / precedence design notes
│   └── prompt_budget.md          # Prompt Budget Manager design notes
├── tests/                        # unittest-based; one file per module/feature, plus fixtures.py
├── requirements.txt              # openai, anthropic, pyperclip
└── .env.example                  # documents reserved env vars (none required to run today)
```

**`src/`** — the entire application. No subpackages; every module is a flat top-level file imported by short name (`import provider_registry`, etc.). This is deliberate — see PART 10 for the layering convention that substitutes for folder structure.

**`config/`** — the "brand brain": global photography rules (base layer), then per-store identity/voice (store layer), then per-campaign niche direction (campaign layer), plus two optional per-store/campaign assets (Human Philosophy, Creative DNA). Public/stable in the sense that a Controller or a human editor is expected to read and extend it, but its file layout is a hard contract consumed by `system_prompt.py`, `store_config.py`, `creative_dna.py` — renaming a file breaks loading (raises, never silently falls back — `system_prompt.py:43-46`).

**`jobs/`** — one self-contained folder per product being processed. Everything about a job's state, decisions, and outputs lives under its own folder; nothing job-specific is written outside `jobs/<job_name>/`. This is what makes the whole thing safe for a Controller to poll by directory-walking `jobs/*/job_manifest.json` (documented explicitly in `job_manifest.py:13-24`).

**`docs/`** — narrative design notes written by the same discipline that wrote the code; both files were read in full for this handbook and are cited throughout (provider architecture and prompt-budget behavior respectively). `README.md` at the repo root exists but is empty.

**`tests/`** — `unittest`-based (not pytest — confirmed by `tests/__init__.py` presence and per-file `TestCase` conventions), one file per feature area; tests double as the most reliable behavioral spec for edge cases (e.g. `test_job_reset.py`, `test_prompt_budget.py`, `test_physical_schema_and_providers.py`). Not read exhaustively line-by-line for this handbook (module count made that impractical at this effort level), but file names and the modules they mirror are catalogued in PART 2.

---

## PART 2 — MODULE MAP

Grouped by pipeline concern. "Downstream" means what depends on it; "Upstream" means what it depends on.

### Leaf / foundation modules (no imports from stage modules — see PART 5)

**`physical_schema.py`** — Purpose: enums + validation + cross-field guidance for print-side placement and garment physical presentation. Responsibilities: `PRODUCT_PRINT_SIDE_VALUES`, `PRINT_SIDE_VISIBILITY_VALUES`, `GARMENT_PRESENTATION_VALUES`, `SUPPORT_SURFACE_VALUES`, `PHYSICAL_SUPPORT_VALUES`, `CAMERA_ORIENTATION_VALUES` (`physical_schema.py:21-59`); `evaluate_print_side_compatibility()` (`:96-182`); `physical_plausibility_guidance()` (`:241-258`); `believable_presence_guidance()` (`:283-295`); `prop_budget_instruction()` (`:303-316`). Called by: `job_config.py`, `concept_planner.py`, `concept_generator.py`, `concept_finalization.py`, `concept_review.py`, `prompt_builder.py`. Calls: nothing internal. Controller-relevant: yes — a Controller should call `evaluate_print_side_compatibility()` rather than re-derive it (`docs/provider_readiness.md:372-373`).

**`creative_dna.py`** — Purpose: loads/describes campaign-level "visual DNA" (`config/stores/<id>/campaigns/<id>_creative_dna.json`). Responsibilities: `load_creative_dna()` (never raises, all-empty default — `:106-128`), `describe_creative_dna_for_prompt()` (`:151-169`), `summarize_creative_dna_for_controller()` (`:172-180`, explicitly built for a future Controller checklist UI). Called by: `concept_generator.py`, `manual_concept_generation.py`, `prompt_builder.py`.

**`reference_assets.py`** — Purpose: classifies reference images by role (Product Front, Human Model Back, Color Reference, etc.) so prompts describe assets by purpose, not filename. Owns `jobs/<job>/config/reference_assets.json`. Key functions: `infer_role()` (`:197-204`), `classify_reference_assets()` (`:241-274`, idempotent, preserves manual corrections), `describe_reference_assets_for_prompt()` (`:313-355`). Called by: `concept_planner.py`, `concept_generator.py`, `manual_concept_generation.py`, `prompt_builder.py`, `manual_image_generation.py`, `main.py` (Review Reference Assets screen).

**`shot_planner.py`** — Purpose: pre-generation planning layer — decides Marketing Purpose then Shot Role for every concept slot before any concept text is written. Owns `jobs/<job>/outputs/concepts/shot_plan.json`. Key functions: `plan_shot_list()` (`:483-495`), `assign_shot_plan_to_concepts()` (`:516-525`, authoritative — never trusts model output for these fields), `get_planned_shot_list()` (`:616-652`, the 3-tier backward-compat read path), `save_shot_plan()` / `load_shot_plan()`. Called by: `concept_planner.py` (writes the plan), `concept_generator.py` (`attach_planned_shots()`), `manual_concept_generation.py`, `concept_finalization.py`.

**`provider_contracts.py`** — Purpose: normalized request/result dataclass-style objects and error taxonomy shared by every concept and image provider. `ConceptGenerationRequest`/`ConceptGenerationResult` (`:144-278`), `ImageGenerationRequestV2`/`ImageGenerationResultV2` (`:286-441`), `ERROR_CATEGORIES` (`:71-87`), `unknown_capabilities()` (`:119-136`). Pure additive leaf module by design (`:13-16`) — imported by `provider_registry.py` and `image_generator.py`, never the reverse.

### Configuration/persistence modules

**`job_config.py`** — Purpose: builds and persists `generation_config.json` (per-job settings: store/campaign IDs, concept counts, workflow toggles, print-side config). Key: `build_generation_config()` (`:63-90`), `load_workflow_settings()`/`save_workflow_settings()` (`:102-187`), `load_available_print_sides()`/`save_available_print_sides()` (`:123-164`). Called by: `main.py`, `concept_planner.py` (reads via file, not import), `provider_registry.py`(indirectly via its own config reader), `concept_finalization.py`, `claude_concept_generation.py`.

**`store_config.py`** — Purpose: discovers and loads Store/Campaign profile JSON. `discover_stores()`, `discover_campaigns()`, `load_store_profile()`, `load_campaign_profile()`, `select_store_and_campaign()` (interactive). Called by: `main.py`, `concept_planner.py`, `prompt_completeness.py`.

**`system_prompt.py`** — Purpose: the ONE place that loads, validates, versions, and combines the layered system prompt (Base → Store → [Human Philosophy] → Campaign). `load_layered_system_prompt()` (`:273-295`) is the canonical entry point; raises `SystemPromptError` for any missing/empty/malformed layer, never falls back to a partial prompt (`:24-25`). Called by: `prompt_builder.py`, `image_generator.py`, `manual_image_generation.py`, `claude_concept_generation.py`, `manual_concept_generation.py`, `main.py`.

**`creative_brief.py`** — Purpose: assembles/saves `creative_brief.json`, detects reference images on disk. `build_creative_brief()` (`:59-113`), `detect_reference_images()` (`:46-56`). Called by: `main.py`, `concept_planner.py`.

**`clipboard.py`** — Purpose: tiny cross-workflow clipboard helper (`pyperclip`), never raises (`:18-28`). Called by manual-workflow modules.

### Job lifecycle modules

**`job_manifest.py`** — Purpose: computes and writes `jobs/<job>/job_manifest.json`, the single machine-readable status/progress/artifact-path summary. Deliberately imports NO other stage module (`:5-10`) — reads only the plain JSON/text artifacts other stages already wrote. `inspect_job()` (`:593-630`), `rebuild_job_manifest()` (`:641-647`), `print_job_status()` (`:694-731`). This is THE Controller discovery contract (module docstring `:12-24`).

**`job_reset.py`** — Purpose: safely rewinds a job to an earlier pipeline stage, archiving (never deleting) prior state. `reset_job()` (`:223-280`) — archive-copy → stage new outputs/ in a temp dir → atomic swap → cleanup, with rollback on any failure. Never touches `config/` or `reference_images/` (`:11-13`).

**`pipeline_guards.py`** — Purpose: read-only precondition checks for every CLI-exposed stage, so a stage never silently runs on empty placeholders. Never writes anything (`:6-11`). `generate_concepts_precondition()`, `review_concepts_precondition()`, `build_prompts_precondition()`, `generate_images_precondition()`, `import_manual_response_precondition()`, `inspect_concept_state()`.

### Concept pipeline modules

**`concept_planner.py`** — Purpose: Plan Concepts stage — assembles `concept_planning_context.json` (frozen snapshot of store/campaign/brief/counts/rules), writes concept placeholder files, triggers shot planning and reference-asset classification. `build_planning_context()` (`:124-194`), `run_concept_planner()` (`:367-415`). Owns `AI_PRODUCT_MOCKUP_CONCEPT_FIELDS` / `LIFESTYLE_MOCKUP_CONCEPT_FIELDS` — the concept schema single source of truth (`:48-95`).

**`concept_generator.py`** — Purpose: legacy/shared concept-generation machinery: `ConceptProvider` ABC, `OpenAIConceptProvider` (legacy, still present but not wired into the current production menu — superseded by `provider_registry.py`'s providers per `docs/provider_readiness.md:128-157`), `normalize_concept()` (the single normalization seam every concept source converges through — `:389-414`), `load_generation_context()` (`:132-182`), `attach_planned_shots()` (`:185-213`).

**`claude_concept_generation.py`** — Purpose: the real, current "Claude Code — Manual" provider operations. `prepare_claude_instructions()` (`:228-250`, builds the task file for the Claude Code session already in this repo — no subprocess, no API call, module docstring `:6-14`), `check_generated_concepts()` (`:361-442`, read-only validation, never repairs).

**`manual_concept_generation.py`** — Purpose: legacy manual-ChatGPT concept export/import workflow, AND the shared prompt-building/parsing/validation logic reused by the Claude API provider. `build_manual_concept_prompt()` (`:179-349`, reused verbatim by `ClaudeAPIConceptProvider` per `provider_registry.py:321-323`), `parse_concept_response_text()` (`:482-513`, fence-tolerant JSON parser, shared), `find_near_duplicate_warnings()` (`:548-570`, shared near-dup heuristic), `export_manual_package()`, `import_manual_concepts()`.

**`concept_finalization.py`** — Purpose: the ONE shared path every synchronous concept provider converges on after producing content: normalize → assign shot plan → force `status="proposed"` → validate → duplicate check → write → rebuild manifest. `validate_concept_category()` (`:71-151`), `finalize_provider_concepts()` (`:186-295`), `build_concept_response_schema()` (`:163-183`, the JSON schema handed to the Claude API for structured output).

**`concept_review.py`** — Purpose: interactive Approve/Reject/Skip review loop, plus `auto_approve_all()` for the disabled-Review-Concepts path. `_review_concepts()` (`:212-262`), `run_concept_review()` (`:283-336`), `auto_approve_all()` (`:339-371`). `VALID_STATUSES = ["proposed","approved","rejected"]` (`:29`).

### Provider architecture

**`provider_registry.py`** — Purpose: THE single, centralized resolution point for both concept and image providers. `ConceptGenerationProvider` ABC (`:69-108`), `ClaudeCodeManualConceptProvider` (`:260-291`), `ClaudeAPIConceptProvider` (`:319-431`, real Anthropic call), `GeminiImageProvider`/`ManualImageProviderDescriptor` (placeholders/descriptors), `CONCEPT_PROVIDERS`/`IMAGE_PROVIDERS` registries, `get_concept_provider()`/`get_image_provider()` (raise `UnknownProviderError`, never silent fallback — `:456-467`, `:576-584`), `resolve_model()`/`resolve_concept_provider_id()`/`resolve_image_provider_id()` (precedence chains), `has_credentials()`. Called by: `main.py` exclusively for provider resolution.

### Prompt pipeline

**`prompt_builder.py`** — Purpose: turns approved concepts into `prompt_package.json` + `user_prompt.txt` + system-prompt snapshot files. Never calls an image API (`:1-3`). `build_user_prompt_sections()` (`:213-367`, priority-tagged sections consumed by Prompt Budget Manager), `build_prompt_package()` (`:392-468`), `run_prompt_builder()` (`:489-554`), `format_prompt_package_for_humans()` (`:579-653`, human-readable render for the CLI's View Prompt Package screen).

**`prompt_budget.py`** — Purpose: measures the fully-assembled prompt against a character budget (GPT Image 1's ~32,000-char hard limit) and selectively omits/deduplicates lower-priority sections — never rewrites content. `PromptSection` class (`:75-115`), `parse_numbered_sections()` (`:126-139`), `classify_section_priority()` (`:162-176`), `remove_duplicate_lines()` (`:274-335`), `compile_prompt_budget()` (`:375-446`, the full pipeline). `DEFAULT_PROMPT_BUDGET_CHARS = 28000` (`:53`).

**`prompt_completeness.py`** — Purpose: standalone developer tool — reports which authored sections of Base/Store/Campaign prompts exist vs. are missing/placeholder. Never part of the generation pipeline, never invoked automatically (`:9-10`). Not Controller-relevant.

### Image pipeline

**`image_generator.py`** — Purpose: provider-agnostic orchestration of API image generation: eligibility scan, provider call, save bytes, write metadata, update prompt-package status. `ImageGenerator` class (`:387-742`), `OpenAIImageProvider` (`:174-347`, real, live, all OpenAI-specific logic isolated here), `run_image_generation()` (`:745-871`, requires an explicit `provider=` or `provider_id=` — never assumes one, `:770-771`), `find_eligible_packages()` (`:355-384`).

**`manual_image_generation.py`** — Purpose: no-API-key image workflow — export one-concept/one-image ChatGPT packages, import downloaded images back into the identical `generated/` structure the API path produces. `export_manual_image_package()`, `import_manual_images()` (`:503-625`), `_build_manual_metadata()` (`:434-470`, same field shape as the API path so downstream code needs no manual-specific branching).

**`image_review.py`** — Purpose: Approve/Reject generated images, syncs `approved/`/`rejected/` copies, and rebuilds `approved_media_handoff.json` after every decision. `run_image_review()` (`:344-382`), `rebuild_approved_media_handoff()` (`:294-322`, the Controller-facing handoff manifest builder). Deliberately independent of `image_generator.py` (`:4-8`).

### Entry point

**`main.py`** — Purpose: the interactive CLI orchestrator; the only module that talks to the terminal. Owns the main menu, job-creation wizard, stage-advancement/auto-chaining logic (`_continue_to_next_stage()`, `_advance_pipeline_after_concept_generation()`), and Developer/Advanced Tools menu. 2291 lines; see PART 3/4 for its call graph and entry points.

---

## PART 3 — CALL GRAPH

### (a) `main.py`'s interactive menu loop and stage advancement

```
main_menu() [main.py:2238]
 ├─ 1 Create New Job        -> create_new_job() [368]
 ├─ 2 Continue Current Job  -> continue_current_job() [2082] -> _job_action_menu() [2032]
 │                                -> "Continue" -> _launch_recommended_step() [2006]
 │                                     -> _next_recommended_step(pipeline_status) [1960]
 │                                     -> NEXT_STEP_FUNCTIONS[label] via globals()[name]()
 ├─ 3 Change Active Job     -> change_active_job() [620]
 ├─ 4 Workflow Settings     -> workflow_settings_menu() [632]
 ├─ 5 Generate Concepts     -> generate_concepts() [1037]
 ├─ 6 Review Concepts       -> review_concepts() [1165]
 ├─ 7 Review Prompts        -> review_prompts() [1242]
 ├─ 8 Generate Images       -> generate_images() [1539]
 ├─ 9 Review Images         -> review_images() [1583]
 ├─10 Developer/Advanced    -> developer_tools_menu() [1837]
 └─11 Exit
```

Stage-advancement chaining is centralized in `_continue_to_next_stage(job_name)` [2017] which every successful-stage-completion call site invokes: it re-inspects the manifest and calls whatever `NEXT_STEP_FUNCTIONS` maps the newly-current stage to via `globals()[function_name]()` [2014]. `_advance_pipeline_after_concept_generation()` [1139] additionally auto-approves concepts / auto-builds prompts / auto-marks Review Prompts complete when the corresponding Workflow Setting is disabled, reused by every concept-generation entry point.

### (b) `create_new_job()` through concept planning

```
create_new_job() [368]
 ├─ select_store_and_campaign()            (store_config.py)
 ├─ _select_product_type()
 ├─ mkdir JOB_SUBFOLDERS
 ├─ _run_reference_images_step()
 ├─ detect_reference_images() + infer_color_hint()   (creative_brief.py / reference_assets.py)
 ├─ _select_concept_plan() -> _advanced_options_wizard() [optional]
 ├─ build_generation_config()  (job_config.py) -> save_config()
 ├─ build_creative_brief()     (creative_brief.py) -> save_creative_brief()
 ├─ set_active_job()
 ├─ run_concept_planner(job_name)          (concept_planner.py:367)
 │    ├─ build_planning_context()   [124]  -> load store/campaign profiles, creative_brief
 │    ├─ save_planning_context()    [205]  -> concept_planning_context.json
 │    ├─ classify_reference_assets()        (reference_assets.py)
 │    ├─ plan_shot_list() x2 (ai, lifestyle) (shot_planner.py:483)
 │    ├─ save_shot_plan()                   (shot_planner.py:554) -> shot_plan.json
 │    ├─ save_concept_placeholders()        -> ai/lifestyle *_concepts.json (status=awaiting_generation)
 │    └─ build_planning_package_text() -> concept_planning_package.txt
 ├─ _rebuild_manifest_safely()
 └─ _continue_to_next_stage(job_name)   -> generate_concepts()  (since planning just completed)
```

### (c) Three concept-generation provider paths

**Claude Code — Manual** (default, `is_live=True`, no credentials):
```
generate_concepts() -> is_dev_mode=True
 -> "1" prepare_claude_instructions_action() [848] -> prepare_claude_instructions() (claude_concept_generation.py:228)
      -> load_context_for_generation() [100] -> load_generation_context()+attach_planned_shots()
      -> build_claude_code_instruction() [135] -> write instructions file + copy_to_clipboard()
      (Claude Code session, external to this process, reads the files and writes the two concept JSON files itself)
 -> "2" check_generated_concepts_action() [896] -> check_generated_concepts() (claude_concept_generation.py:361)
      -> _validate_concepts_file() -> concept_finalization.validate_concept_category()
      -> find_near_duplicate_warnings()  (manual_concept_generation.py)
      -> _advance_pipeline_after_concept_generation()
```

**Claude API** (`is_live` when `ANTHROPIC_API_KEY` set):
```
generate_concepts_via_api_action() [936]
 -> load_context_for_generation()
 -> provider_registry.get_concept_provider("claude_api")
 -> provider.generate(job_name, JOBS_DIR, context)   (provider_registry.py:364, ClaudeAPIConceptProvider.generate)
      -> build_manual_concept_prompt() (manual_concept_generation.py) -- SAME prompt as manual path
      -> concept_finalization.build_concept_response_schema()
      -> client.messages.stream(... output_config={"format":{"type":"json_schema",...}})
      -> parse_concept_response_text()
 -> finalize_provider_concepts()  (concept_finalization.py:186)
      -> normalize_concept() x N -> force status="proposed" -> assign_shot_plan_to_concepts()
      -> validate_concept_category() (both categories) -> save_concepts() -> rebuild_job_manifest()
 -> _advance_pipeline_after_concept_generation()
```

**Manual ChatGPT** (legacy, not on main menu, reachable but superseded functionally):
```
generate_concepts_manual() [728] -> export_manual_package() (manual_concept_generation.py:400)
 -> build_manual_concept_prompt() -> write + copy_to_clipboard() + copy reference images
import_manual_concepts_action() [807] -> import_manual_concepts() (manual_concept_generation.py:709)
 -> ManualConceptProvider.generate_concepts() -> parse_concept_response_text()
 -> normalize_and_validate_concepts() x2 -> protect_existing_concepts() (backup) -> save_concepts()
```

### (d) Prompt building

```
_ensure_prompts_built() [1085] (automatic, post Review Concepts) OR build_prompts() [1317] (manual/Developer Tools)
 -> run_prompt_builder(job_name)   (prompt_builder.py:489)
      -> load_prompt_building_context()
      -> select_approved_concepts() x2
      -> save_system_prompt() -> system_prompt.load_layered_system_prompt()
      -> save_lifestyle_system_prompt() (adds Human Philosophy layer when store has it)
      -> for each approved concept: _build_and_save_package()
           -> build_user_prompt_text() / build_user_prompt_sections()
           -> build_prompt_package()  -> writes prompt_package.json + user_prompt.txt
```

### (e) Image generation — both paths

**OpenAI API**:
```
generate_images_via_api() [1362] -> run_image_generation(job_name, provider=OpenAIImageProvider())
   (image_generator.py:745)
 -> find_eligible_packages()  [355] -- generation_status == "not_generated"
 -> confirm (input y/N)
 -> provider.validate_ready()
 -> load_layered_system_prompt() (pre-flight, fail whole batch before spending calls)
 -> ImageGenerator.generate_for_package() per package  [494]
      -> resolve reference image paths
      -> load_layered_system_prompt() (live, canonical, ignores stale per-job snapshot)
      -> read user_prompt.txt
      -> _resolve_output_config()  (config/image_generation.json)
      -> _compile_budgeted_prompt() -> prompt_budget.compile_prompt_budget()
      -> ImageGenerationRequestV2(...) -> provider.generate_image(request)
           OpenAIImageProvider.generate_image() -> client.images.generate() or .edit()
      -> write image bytes, generation_metadata.json, snapshots
      -> update prompt_package.json.generation_status = "generated"
```

**Manual ChatGPT**:
```
generate_images_manual_menu() [1515]
 -> export_manual_image_packages_action() -> export_all_manual_image_packages() (manual_image_generation.py:323)
 -> copy_manual_image_prompt_action() -> select_and_copy_concept_prompt()
 -> import_manual_images_action() -> import_manual_images() (manual_image_generation.py:503)
      -> _find_incoming_images() -> move files into outputs/generated/... -> write generation_metadata.json
         (same shape as API path, provider="manual_chatgpt")
```

### (f) Image review and handoff rebuild

```
review_images() [1583] -> run_image_review(job_name)  (image_review.py:344)
 -> discover_generated_concepts() -> _load_review_items()
 -> _review_items() per category -> on Approve/Reject: _set_review_status() [165]
      -> write generation_metadata.json review_status
      -> update prompt_package.json review_status
      -> _sync_approved_rejected_dirs() (rmtree both, then copy into exactly one)
      -> _copy_reviewed_asset()
      -> rebuild_approved_media_handoff()  [294]  -- ALWAYS called after any single decision
           -> scans outputs/approved/ fresh -> writes approved_media_handoff.json
```

### (g) `job_reset.py`'s archive-then-swap sequence

```
reset_job(job_name, target_stage)  (job_reset.py:223)
 -> _next_archive_run_dir()          -- jobs/<job>/archive/run_NNN
 -> mkdir run_dir; copytree(live outputs/, run_dir/outputs)     [archive, non-destructive]
 -> mkdir staging_dir (.reset_staging_run_NNN)
 -> _build_reset_outputs(job_folder, staging_dir, target_stage) [158]
      -- selectively copies forward only what survives the target stage
      -- resets concept review decisions to "proposed" if target==REVIEW_CONCEPTS
      -- resets prompt-package generation_status to "not_generated" if target>=REVIEW_PROMPTS
 -> write reset_metadata.json into run_dir
 -> rename live outputs/ -> backup_dir (.outputs_pre_reset_run_NNN)
 -> rename staging_dir -> outputs/                              [atomic swap]
 -> rmtree backup_dir
 -> on ANY exception before swap completes: rollback (restore backup_dir -> outputs/, discard staging/run dirs)
```

---

## PART 4 — ENTRY POINTS

| Function | Location | Interactive? | Importable/Reusable? | Headless? | Controller-safe |
|---|---|---|---|---|---|
| `main_menu()` | main.py:2238 | Yes (blocking `input()` loop) | No | No | No — CLI only |
| `create_new_job()` | main.py:368 | Yes | Partially (calls `input()` internally) | No | No |
| `run_concept_planner(job_name)` | concept_planner.py:367 | No (raises `ConceptPlannerError`) | Yes | Yes | Yes |
| `prepare_claude_instructions(job_name)` | claude_concept_generation.py:228 | No | Yes | Yes | Yes (never marks stage complete) |
| `check_generated_concepts(job_name)` | claude_concept_generation.py:361 | No | Yes | Yes | Yes — read-only, never raises for a bad file, only for missing job |
| `ClaudeAPIConceptProvider().generate(job_name, jobs_dir, context)` | provider_registry.py:364 | No | Yes | Yes (real network call) | Yes if `ANTHROPIC_API_KEY` present; raises `ConceptGenerationProviderError` otherwise |
| `finalize_provider_concepts(...)` | concept_finalization.py:186 | No | Yes | Yes | Yes |
| `run_concept_review(job_name)` | concept_review.py:283 | Yes (`input()` loop) | No (interactive only) | No | No |
| `auto_approve_all(job_name)` | concept_review.py:339 | No | Yes | Yes | Yes |
| `run_prompt_builder(job_name)` | prompt_builder.py:489 | No | Yes | Yes | Yes |
| `run_image_generation(job_name, provider=..., provider_id=...)` | image_generator.py:745 | Yes (confirms via `input()` unless test harness bypasses) | Yes (requires explicit provider) | Partially — the `input()` confirm blocks true headless use unless refactored/monkeypatched | Yes architecturally, no today (confirm prompt) |
| `run_image_review(job_name)` | image_review.py:344 | Yes | No | No | No |
| `rebuild_approved_media_handoff(job_folder)` | image_review.py:294 | No | Yes | Yes | Yes |
| `reset_job(job_name, target_stage)` | job_reset.py:223 | No | Yes | Yes | Yes |
| `rebuild_job_manifest(job_name)` | job_manifest.py:641 | No | Yes | Yes | Yes — the canonical status refresh |
| `provider_registry.get_concept_provider(id)` / `get_image_provider(id)` | provider_registry.py:456 / 576 | No | Yes | Yes | Yes — raises `UnknownProviderError`, never silent |
| `provider_registry.list_concept_providers()` / `list_image_providers()` | provider_registry.py:486 / 599 | No | Yes | Yes | Yes — the metadata query surface |
| `export_manual_package` / `import_manual_concepts` | manual_concept_generation.py:400 / 709 | Partially (import confirms via `input()` unless `confirm=` callable supplied) | Yes | Yes if `confirm=` supplied | Yes with `confirm=` |
| `export_manual_image_package` / `import_manual_images` | manual_image_generation.py | Partially (same `confirm=` pattern) | Yes | Yes if `confirm=`/`chooser=` supplied | Yes with those args |

General pattern: every "engine" function (planner, finalizer, prompt builder, image generator core, manifest rebuild, reset) is a pure/importable function raising a typed `*Error` exception and returning a result dict — these are the Controller-safe entry points. Every "review"/"menu" function is `input()`-driven CLI-only glue in `main.py`, `concept_review.py`, `image_review.py` — not Controller-safe without a rewrite.

---

## PART 5 — DEPENDENCY MAP

**Third-party dependencies** (`requirements.txt:1-3`): `openai>=1.0.0` (image generation, `image_generator.py`), `anthropic>=0.70.0` (concept generation, `provider_registry.py`'s `ClaudeAPIConceptProvider`), `pyperclip>=1.8.0` (clipboard, `clipboard.py`). Both `openai` and `anthropic` SDKs are imported lazily inside the functions that need them (`image_generator.py:226`, `provider_registry.py:308`, `:381`) — never at module top-level — so the app runs without either installed as long as those specific live paths aren't invoked.

**Deliberate leaf-module architecture** — explicitly documented in the code, not inferred:
- `job_manifest.py` never imports any stage module (`job_manifest.py:5-10`) — reads only artifacts on disk.
- `provider_contracts.py` is "a pure, additive leaf module (no imports from other pipeline-stage modules)" (`provider_contracts.py:13-16`).
- `creative_dna.py`, `reference_assets.py`, `shot_planner.py`, `physical_schema.py` are each explicitly documented as pure additive leaf modules matching this same pattern (each file's own docstring, e.g. `creative_dna.py:15-17`, `shot_planner.py:26-27`, `physical_schema.py:4-6`).

**Layer order (leaf → stage → orchestrator)**:
```
Leaf:        physical_schema, creative_dna, reference_assets, shot_planner, provider_contracts
             system_prompt, store_config, job_config, clipboard
Stage:       creative_brief, concept_planner, concept_generator, claude_concept_generation,
             manual_concept_generation, concept_finalization, concept_review,
             prompt_builder, prompt_budget, image_generator, manual_image_generation, image_review
Registry:    provider_registry  (imports image_generator's ImageProvider/OpenAIImageProvider;
                                  imports provider_contracts; lazily imports claude_concept_generation,
                                  manual_concept_generation, concept_finalization inside methods to
                                  avoid circular imports)
Independent: job_manifest, pipeline_guards, job_reset  (read stage-module OUTPUT FILES, not the modules)
Orchestrator: main.py  (imports nearly everything above)
```

`image_generator.py` and `provider_registry.py` have a circular-import risk resolved by lazy import: `provider_registry.py` imports `ImageProvider`/`ImageProviderError`/`OpenAIImageProvider` from `image_generator.py` at top level (`provider_registry.py:58`), while `image_generator.run_image_generation()` imports `provider_registry` lazily inside the function body only when `provider_id=` is used (`image_generator.py:810-811`, comment explains why).

**Config touched per subsystem**: `store_config.py`/`system_prompt.py`/`creative_dna.py` → `config/stores/**`; `job_config.py`/`provider_registry.py` → `jobs/<job>/config/generation_config.json`; `image_generator.py` → `config/image_generation.json`.

**Architectural vs incidental dependencies**: `openai`/`anthropic` are architectural (core to two pipeline stages). `pyperclip` is incidental convenience (never raises, workflow degrades gracefully to file-based copy/paste if absent — `clipboard.py:18-28`).

---

## PART 6 — WORKFLOW STATE MACHINE

The canonical state object is `pipeline_status` inside `job_manifest.json`, recomputed fresh every call to `inspect_job()` (`job_manifest.py:593-630`) — it is never incrementally mutated, always derived from current on-disk artifacts. This makes the state machine idempotent and crash-safe: state is a pure function of files on disk.

**States** (`job_manifest.py:387-416`, boolean flags forming a progression):

| Flag | Computed from | Trigger to become true |
|---|---|---|
| `job_created` | all `REQUIRED_JOB_SUBFOLDERS` exist | `create_new_job()` mkdir step |
| `creative_brief_complete` | `creative_brief.json` has all `CREATIVE_BRIEF_REQUIRED_FIELDS` non-empty | `save_creative_brief()` |
| `concept_planning_complete` | `concept_planning_context.json` exists | `run_concept_planner()` |
| `manual_concept_package_exported` | manual concept prompt file exists | `export_manual_package()` (legacy path only) |
| `concept_generation_started` | manual package exported OR generation complete | either concept path touching the job |
| `concept_generation_complete` | both concept files have `requested_count` concepts and status != `awaiting_generation` | `check_generated_concepts()`, `finalize_provider_concepts()`, or `import_manual_concepts()` succeeding |
| `concept_review_started` | any concept has status `approved`/`rejected` | first Approve/Reject click |
| `concept_review_complete` | EITHER all concepts decided OR both categories' approved count ≥ their `_to_select` target | `run_concept_review()` or `auto_approve_all()` |
| `prompt_build_complete` | every approved concept_id has a matching built package | `run_prompt_builder()` |
| `prompt_review_complete` | `prompt_build_complete` AND `prompt_review_marker.json` exists | `main.review_prompts()` writing the marker (currently view-only, no per-package decision) |
| `manual_image_packages_exported` | any manual image prompt file exists | `export_manual_image_package()` |
| `image_generation_started` | any prompt package has `generation_status` in (`generated`,`failed`) | first image attempt |
| `image_generation_complete` | at least one package, and NONE still `not_generated` | all eligible packages attempted |
| `image_review_started` | any generated image has a `review_status` set | first Approve/Reject |
| `image_review_complete` | every `generated` image has `review_status` in (`approved`,`rejected`) | full review pass |
| `approved_media_handoff_ready` | `approved_media_handoff.json` exists | first `rebuild_approved_media_handoff()` call |
| `ready_for_controller` | handoff exists AND image_review_complete AND ≥1 approved asset | final condition — Controller ingestion gate |

**Recommended-step resolver**: `main._next_recommended_step()` (`main.py:1960-1964`) walks `NEXT_STEP_ORDER` (`main.py:1897-1910`) and returns the label of the first incomplete stage-completion flag, or `PIPELINE_COMPLETE_LABEL` if all are done. This single function is the ONLY place "what's next" is decided — the main-menu header, Continue Current Job, and every post-stage success message all call it (`main.py:145-155` docstring makes this explicit).

**Resume/recovery points**: `Continue Current Job` → `_select_unfinished_job()` (any job whose next-step != complete) → `_job_action_menu()` → `Continue` launches the exact next-step function. Recovery-specific entries exist for jobs stuck mid-migration: `auto_build_prompts()` (Developer Tools item 7, `main.py:1222-1239`, for a job whose Review Concepts finished under an older workflow version) and manual `plan_concepts()` re-run with explicit confirmation (`main.py:689-726`).

**Reset/rewind semantics**: `job_reset.py`'s `RESET_STAGE_ORDER` = `[plan_concepts, generate_concepts, review_concepts, build_prompts, review_prompts, generate_images]` (`job_reset.py:35-38`). Resetting to stage X archives the full current `outputs/` tree, then rebuilds `outputs/` keeping only what stage X's target state should retain (e.g. reset-to-`review_concepts` keeps concept files but resets every concept's status back to `proposed`; reset-to-`generate_images` keeps built prompt packages but resets `generation_status` to `not_generated`). `generated/`, `approved/`, `rejected/`, `approved_media_handoff.json` are NEVER preserved by any reset target (`job_reset.py:216-220`) — image state is always invalidated by any reset.

**Cancellation**: no explicit "cancel job" state exists; a job is simply left at whatever stage it's at. `_JobCreationCancelled` (`main.py:158-161`) is an internal control-flow-only exception during job creation — if raised, the partially-created job folder is `rmtree`'d (`main.py:456-457`), leaving no trace.

---

## PART 7 — ARTIFACT DOCUMENTATION

### `jobs/<job>/config/generation_config.json`
Producer: `job_config.build_generation_config()` + `save_config()`. Consumer: nearly everything (`concept_planner`, `concept_generator`, `provider_registry`, `system_prompt.get_job_store_campaign()`). Real example fields (`jobs/koi_yin_yang/config/generation_config.json`):
```json
{
  "schema_version": "1.0", "job_name": "...", "product_name": "...", "product_type": "T-Shirt",
  "store_id": "...", "store_name": "...", "campaign_id": "...", "campaign_name": "...",
  "ai_product_mockup_concepts_to_propose": 10, "ai_product_mockup_concepts_to_select": 5,
  "lifestyle_mockup_concepts_to_propose": 10, "lifestyle_mockup_concepts_to_select": 5
}
```
Additive optional keys (older jobs lack them, safe defaults apply): `available_print_sides` (default `"unknown"`), `review_concepts`/`review_prompts` (default `True`), `concept_provider`/`concept_model`/`image_provider`/`image_model` (default to `DEFAULT_CONCEPT_PROVIDER_ID`/`DEFAULT_IMAGE_PROVIDER_ID`). Versioning: `schema_version` field present but never checked/branched on anywhere read. Public contract: yes — a Controller reads `store_id`/`campaign_id`/`product_type`/counts directly.

### `jobs/<job>/config/creative_brief.json`
Producer: `creative_brief.build_creative_brief()`. Schema (real example, `jobs/koi_yin_yang/config/creative_brief.json`): `product_name`, `product_type`, `product_color`, `artwork_description`, `design_meaning`, `niche_theme`, `intended_customer`, `concept_direction`, `preferred_moods`, `preferred_environments`, `must_remain_accurate`, `must_not_appear`, `additional_notes`, `creative_notes`, `etsy_listing_rules: [str]`, `reference_images: [filename]`, `output_format`. Consumer: `concept_planner`, `prompt_builder` (`product_context` block), `main.py` display. `CREATIVE_BRIEF_REQUIRED_FIELDS` for completeness = `product_name, product_type, artwork_description, niche_theme` (`job_manifest.py:48`).

### `jobs/<job>/config/reference_assets.json` (optional)
Producer: `reference_assets.classify_reference_assets()`. Schema: `{schema_version, job_name, assets: [{filename, role, notes}]}` (`reference_assets.py:15-22`). Idempotent, preserves manual role corrections across re-plans.

### `jobs/<job>/outputs/concepts/concept_planning_context.json`
Producer: `concept_planner.save_planning_context()`. A FROZEN snapshot: `store_profile`, `campaign_profile`, `creative_brief`, `reference_images`, `ai_product_mockup_concept_count`, `lifestyle_mockup_concept_count`, `output_format`, `etsy_listing_rules`, `planning_rules`, `concept_item_schema` (`concept_planner.py:176-192`). Presence of this file is literally the `concept_planning_complete` gate. Never modified after being written except by re-running Plan Concepts (which regenerates it wholesale).

### `jobs/<job>/outputs/concepts/shot_plan.json`
Producer: `shot_planner.save_shot_plan()`. Schema v1.1: `{schema_version, job_name, ai_product_mockup_roles: [label], lifestyle_roles: [label], ai_product_mockup_purposes: [label], lifestyle_purposes: [label]}` — parallel-indexed lists, slot i's purpose/role pair goes to concept slot i (`shot_planner.py:32-44`, `:554-572`). Optional/entirely additive — 3-tier backward compatibility read path in `get_planned_shot_list()`.

### `jobs/<job>/outputs/concepts/{ai_product_mockup,lifestyle_mockup}_concepts.json`
Producer: `concept_planner.save_concept_placeholders()` initially (`status: awaiting_generation`, `concepts: []`), then overwritten by whichever concept-generation path ran. Container schema: `{schema_version, job_name, media_category, requested_count, status, concepts: [...]}`. `status` transitions `awaiting_generation` → `awaiting_selection` (once concepts written). Each concept item follows `AI_PRODUCT_MOCKUP_CONCEPT_FIELDS` / `LIFESTYLE_MOCKUP_CONCEPT_FIELDS` (`concept_planner.py:48-95`) — includes `concept_id`, `concept_name`, `concept_summary`, `reasoning`, `environment`, `camera_angle`/`framing`/`composition`, `camera_orientation` (enum), `mood`, `garment_presentation`/`support_surface`/`physical_support`/`print_side_visibility` (enums, default `"unknown"`), `product_visibility`, `maximum_supporting_props` (int, default 2), `niche_relevance`, `variety_notes`, `status` (`proposed`/`approved`/`rejected`), plus lifestyle-only `model_presentation`, `face_presentation`, `activity`, `social_context`, `season`, plus (assigned post-hoc, not model-authored) `purpose` and `role` from `shot_plan.json`. This is the PRIMARY public schema a Controller consumes for concept metadata.

### `jobs/<job>/outputs/prompts/<category>/<concept_id>/prompt_package.json`
Producer: `prompt_builder.build_prompt_package()`. Real example fields (`jobs/koi_yin_yang/outputs/prompts/ai_product_mockups/ai_01/prompt_package.json`): `schema_version`, `job_name`, `media_category`, `concept_id`, `concept_name`, `concept_status`, `prompt_version`, `system_prompt_path`, `user_prompt_path`, `base_system_prompt_path`/`store_prompt_path`/`campaign_prompt_path` + their `*_version` fields, `combined_prompt_version`, `human_philosophy_active` (+ `human_philosophy_path`/`version` when true), `source_concept` (full concept object embedded), `product_context`, `store_context`, `campaign_context`, `reference_images: [filename]`, `output_format`, `prompt_sections: [PromptSection.to_dict()]` (additive, used by Prompt Budget Manager), `generation_status` (`not_generated`→`generating`→`generated`/`failed`), `review_status` (`not_reviewed`→`approved`/`rejected`). Post-generation additive fields: `generated_output_folder`, `generated_files`, `generation_error`.

### `jobs/<job>/outputs/prompts/<category>/<concept_id>/user_prompt.txt`
Producer: `prompt_builder.build_user_prompt_text()`. The exact, human-reviewed, NEVER-budget-trimmed concept execution prompt (`prompt_budget.py`'s docstring `:58`, `prompt_builder.py:383-386`).

### `jobs/<job>/outputs/prompts/system/image_generation_system_prompt{,_lifestyle}.txt`
Producer: `prompt_builder.save_system_prompt()` / `save_lifestyle_system_prompt()`. Snapshot copies of the combined Base+Store+[Human Philosophy]+Campaign text at build time — informational; at actual generation time `image_generator.py` always reloads the LIVE canonical layers instead of trusting this snapshot (`image_generator.py:541-546`, explicit comment).

### `jobs/<job>/outputs/generated/<category>/<concept_id>/generation_metadata.json`
Producer: `ImageGenerator._build_metadata()` (API path) or `_build_manual_metadata()` (manual path — same shape, `provider="manual_chatgpt"`). Fields: `schema_version`, `job_name`, `media_category`, `concept_id`, `concept_name`, `provider`, `model`, `requested_output_format`, `requested_output_target`, `provider_size`, `quality`, `background`, `reference_images`, `generated_files: [filename]`, `generation_started_at`/`generation_completed_at`, `generation_status` (`generating`/`generated`/`failed`), `provider_request_id`, `provider_response_metadata`, `error_message`, `prompt_version`, `base_system_prompt_version`/`store_prompt_version`/`campaign_prompt_version`/`combined_prompt_version`, `prompt_budget` (the compiled budget report — `prompt_budget.PromptBudgetResult.to_dict()`), and post-review `review_status`. Also `generation_metadata.json` alongside `prompt_package_snapshot.json`, `system_prompt_snapshot.txt`, `user_prompt_snapshot.txt` in the same folder — self-contained debugging bundle.

### `jobs/<job>/outputs/approved_media_handoff.json`
Producer: `image_review.rebuild_approved_media_handoff()`, rebuilt from scratch after EVERY single approve/reject decision (never incrementally patched — `image_review.py:294-322`). Schema: `{schema_version, job_name, source_module: "etsy_ai_image_generator", handoff_status: "ready_for_controller_review", generated_at, approved_assets: {ai_product_mockups: [entry], lifestyle_mockups: [entry]}}`. Each entry (`_build_handoff_entry()`, `image_review.py:241-291`): `asset_id`, `media_category`, `concept_id`, `concept_name`, `source_generated_image`, `approved_copy_path`, `generation_metadata_path`, `prompt_package_snapshot_path`, `prompt_version`, `provider`, `model`, `output_format`, `review_status: "approved"`, `intended_usage: ["etsy_listing"]`, `store_id`/`store_name`, `campaign_id`/`campaign_name`, `product_name`/`product_type`/`product_color`, `reference_images`. **This is the PRIMARY, stable Controller-facing artifact** — `job_manifest.py` explicitly says the Controller should read this for candidate asset details rather than duplicate its content (`job_manifest.py:19-21`).

### `jobs/<job>/job_manifest.json`
Producer: `job_manifest.rebuild_job_manifest()`, called after almost every stage action. Full schema documented in PART 6/PART 9. **This is the PRIMARY Controller discovery/status artifact.**

### `jobs/<job>/archive/run_NNN/`
Producer: `job_reset.reset_job()`. Contains a full copy of the pre-reset `outputs/` tree plus `reset_metadata.json` (`{reset_at, target_stage, target_stage_label, previous_stage_label, archive_location}`). Never deleted automatically.

---

## PART 8 — CONFIGURATION SYSTEM

**Global config**: `config/image_generation.json` — provider/model/output defaults for image generation (`provider: "openai"`, `model: "gpt-image-1"`, `output_format: "png"`, `quality: "high"`, `background: "auto"`, `images_per_prompt: 1`, `output_targets.etsy_listing_square: {aspect_ratio, provider_size: "1024x1024"}`, optional `prompt_budget_chars` override). Loaded by `image_generator.load_image_generation_config()` (`image_generator.py:117-120`) — raises if missing, no hardcoded fallback.

**Store/campaign config**: three-layer text+metadata pairs under `config/stores/<store_id>/` and `.../campaigns/<campaign_id>_*`. Every layer requires BOTH a `.txt` (content) and `_metadata.json` (must contain non-empty `prompt_version`) file — missing either raises `SystemPromptError` (`system_prompt.py:54-88`). Two optional per-store/campaign assets: `human_philosophy.txt`/`_metadata.json` (both-or-neither, `system_prompt.py:208-220`) and `<campaign>_creative_dna.json` (fully optional, never raises — `creative_dna.py:106-129`).

**Per-job config**: `jobs/<job>/config/generation_config.json` — see PART 7. Precedence for `available_print_sides`, `review_concepts`/`review_prompts`, `concept_provider`/`concept_model`/`image_provider`/`image_model` all follow the same graceful-default posture: missing key → safe default, never inferred or invented (`job_config.py:102-121`, `:123-141`).

**Environment variables** (every one, from `.env.example` and `provider_registry.PROVIDER_ENV_CONFIG`, `provider_registry.py:129-145`):
| Var | Purpose | Default if unset |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API concept generation | `claude_api` provider reports `configured: False`, raises clean error on `generate()` |
| `OPENAI_API_KEY` | OpenAI image generation | `OpenAIImageProvider.is_configured()` False, raises clean error |
| `GEMINI_API_KEY` | reserved for future Gemini image provider | placeholder, never read for network calls (`GeminiImageProvider.validate_ready()` always raises regardless of key) |
| `CLAUDE_CONCEPT_API_MODEL` | override Claude model | falls back to hardcoded `"claude-opus-4-8"` |
| `OPENAI_IMAGE_MODEL` | override OpenAI model | falls back to `""` (no hardcoded default — image config's own `model` field is actually what's used, see `image_generation.json`) |
| `GEMINI_IMAGE_MODEL` | reserved | `""` |

No key is EVER written to a job file, manifest, log, or generated prompt/task (explicit invariant, `provider_registry.py:124-127`, `docs/provider_readiness.md:350-352`).

**Precedence/override rules** — documented identically for both model and provider-identity resolution (`provider_registry.py:159-195`):
```
per-run override  >  per-job config (generation_config.json)  >  app default (env var override, else hardcoded default)  >  "" (manual/no-model)
```
`resolve_model(provider_id, per_run_override, per_job_model)` (`:159-172`) and `resolve_concept_provider_id()`/`resolve_image_provider_id()` (`:210-227`) implement this identically. `save_provider_selection()` (`:229-252`) persists only identifiers/model names, never credentials.

**How modules discover configuration**: everything goes through the module that "owns" that config file — no module reads a config file another module already owns. `system_prompt.py` owns the three-layer prompt files; `store_config.py` owns store/campaign profile JSON; `job_config.py` owns per-job settings; `provider_registry.py` owns env-var/provider precedence. This is consistent with the leaf-module separation in PART 5.

---

## PART 9 — PUBLIC API SURFACE

Treating this repo as a library for an external Automation Controller, the STABLE surface is:

**Stable folders/launch contract**: `jobs/<job_name>/` as the atomic unit of work; a Controller should create/discover jobs by directory, never by any other index.

**Stable output artifacts** (documented producer/consumer above): `job_manifest.json` (status), `approved_media_handoff.json` (candidate assets), `outputs/generated/**/generation_metadata.json` (per-asset detail), `outputs/prompts/**/prompt_package.json` (per-concept prompt detail).

**Stable completion signal**: `pipeline_status.ready_for_controller == true` inside `job_manifest.json` (`job_manifest.py:352-357`, `:19` doc comment — "should normally ingest jobs where ready_for_controller is true").

**Stable error reporting**: every stage module raises its own typed `*Error` (`ConceptPlannerError`, `ConceptGeneratorError`, `ClaudeConceptGenerationError`, `ManualConceptGenerationError`, `ConceptReviewError`, `PromptBuilderError`, `ImageGeneratorError`/`ImageProviderError`, `ImageReviewError`, `JobResetError`, `JobManifestError`, `StoreConfigError`, `SystemPromptError`, `UnknownProviderError`, `ConceptGenerationProviderError`, `ProviderRequestError`). All are plain `Exception` subclasses with a message; `ProviderRequestError` additionally carries a normalized `error_category` from `provider_contracts.ERROR_CATEGORIES` (`provider_contracts.py:71-104`) — this is the closest thing to a structured, cross-provider error taxonomy and is the recommended one for a Controller to key off.

**Stable provider query surface**: `provider_registry.list_concept_providers()` / `list_image_providers()` (`provider_registry.py:486-487`, `:599-600`) → list of `{identifier, display_name, type, is_live, configured, capabilities}`. Never hardcode a provider's identity or model name as a permanent label (`docs/provider_readiness.md:374-378`).

**Stable functions to call directly** (importable, non-interactive, typed-exception, documented above in PART 4): `run_concept_planner`, `prepare_claude_instructions`, `check_generated_concepts`, `ClaudeAPIConceptProvider.generate` + `finalize_provider_concepts`, `auto_approve_all`, `run_prompt_builder`, `run_image_generation` (with explicit provider), `rebuild_approved_media_handoff`, `reset_job`, `rebuild_job_manifest`.

**Everything else is internal**: `main.py` in its entirety (CLI-only), `concept_review.run_concept_review` / `image_review.run_image_review` (interactive-only — a Controller needs its own approve/reject UI calling `_set_review_status`-equivalent logic, which does not currently exist as a non-interactive function), `prompt_completeness.py` (developer tool only), `concept_generator.OpenAIConceptProvider` (legacy, unused by current menus).

---

## PART 10 — CODE ORGANIZATION

**Layered design** (see PART 5 for the precise dependency graph): leaf modules (pure data/validation, no side effects beyond their own file) → stage modules (one per pipeline phase, each owning specific artifact files, each with a `run_*()` entry point and a typed error) → registry module (provider resolution only) → orchestrator (`main.py`, CLI glue). The absence of subpackages is compensated for by this strict naming/dependency discipline — a new engineer can trust that `physical_schema.py` never calls into `concept_review.py`.

**The provider abstraction pattern, in full**:
1. **Abstract base**: `ConceptGenerationProvider` (`provider_registry.py:69-108`) with one abstract method `generate(job_name, jobs_dir, context)` returning either `{"ready_now": False, ...}` (out-of-band provider, e.g. Claude Code — the caller must separately check for results later) or `{"ready_now": True, "ai_concepts": [...], "lifestyle_concepts": [...]}` (synchronous provider). `ImageProvider` (`image_generator.py:155-171`) with abstract `generate_image(request)`.
2. **Contract normalization**: `provider_contracts.py`'s `ConceptGenerationRequest`/`Result` and `ImageGenerationRequestV2`/`ResultV2` — every provider consumes/produces these exact shapes regardless of vendor. `.to_dict()` on every result makes it JSON-serializable for `generation_metadata.json`.
3. **Registry**: `provider_registry.CONCEPT_PROVIDERS` / `IMAGE_PROVIDERS` dicts, keyed by string identifier, with `get_concept_provider()`/`get_image_provider()` as the ONLY resolution functions in the whole app (enforced by convention + comments, not by a lint rule) — raising `UnknownProviderError` rather than silently defaulting.
4. **Precedence resolution**: `resolve_model()` / `resolve_concept_provider_id()` / `resolve_image_provider_id()` implement the identical 4-tier precedence chain for both "which provider" and "which model" (PART 8).
5. **Finalization seam**: providers never validate, write, or finalize their own output — `concept_finalization.finalize_provider_concepts()` is the ONE shared path any synchronous provider's raw output passes through before touching disk (`concept_finalization.py:1-9`).

**Separation of concerns between concept/prompt/image pipelines**: each pipeline has its own artifact directory (`outputs/concepts/`, `outputs/prompts/`, `outputs/generated/`), its own container schema, its own `run_*()` orchestrator, and its own typed error class — no cross-pipeline module reaches past its own stage's files except by reading already-finalized JSON (e.g. `prompt_builder.py` reads finalized concept files but never reaches back into `concept_generator.py`'s internals).

---

## PART 11 — SEQUENCE DIAGRAMS

### Job creation → concept generation
```
User -> main.create_new_job
main -> store_config.select_store_and_campaign
main -> job_config.build_generation_config -> save_config
main -> creative_brief.build_creative_brief -> save_creative_brief
main -> concept_planner.run_concept_planner
  concept_planner -> store_config.load_store_profile / load_campaign_profile
  concept_planner -> reference_assets.classify_reference_assets
  concept_planner -> shot_planner.plan_shot_list (x2) -> save_shot_plan
  concept_planner -> save_concept_placeholders (status=awaiting_generation)
main -> job_manifest.rebuild_job_manifest
main -> main._continue_to_next_stage -> main.generate_concepts
main -> claude_concept_generation.prepare_claude_instructions
  (external: user asks the live Claude Code session to write the concept files)
User -> main.check_generated_concepts_action
main -> claude_concept_generation.check_generated_concepts
  check_generated_concepts -> concept_finalization.validate_concept_category (both categories)
  check_generated_concepts -> manual_concept_generation.find_near_duplicate_warnings
main -> main._advance_pipeline_after_concept_generation
```

### Concept review → prompt build
```
User -> main.review_concepts -> concept_review.run_concept_review
  loop: user picks A/R/S/N/P/Q -> concept status written to *_concepts.json immediately
main -> main._ensure_prompts_built (if review complete)
main -> prompt_builder.run_prompt_builder
  prompt_builder -> system_prompt.load_layered_system_prompt (product) + (lifestyle variant)
  prompt_builder -> for each approved concept: build_user_prompt_sections -> build_prompt_package
  prompt_builder -> write prompt_package.json + user_prompt.txt per concept
main -> main._advance_prompt_review (marks prompt_review_marker.json if Review Prompts disabled)
```

### Image generation → image review → handoff rebuild
```
User -> main.generate_images_via_api -> image_generator.run_image_generation(provider=OpenAIImageProvider())
  run_image_generation -> find_eligible_packages
  run_image_generation -> provider.validate_ready
  run_image_generation -> system_prompt.load_layered_system_prompt (pre-flight)
  loop per package: ImageGenerator.generate_for_package
    -> prompt_budget.compile_prompt_budget
    -> OpenAIImageProvider.generate_image -> openai SDK images.generate/edit
    -> write image bytes + generation_metadata.json
    -> update prompt_package.json.generation_status
User -> main.review_images -> image_review.run_image_review
  loop: user picks A/R -> image_review._set_review_status
    -> update generation_metadata.json + prompt_package.json review_status
    -> sync approved/ or rejected/ copy
    -> image_review.rebuild_approved_media_handoff  (every single decision)
```

### Job reset
```
User -> main._reset_job_flow -> job_reset.reset_job(job_name, target_stage)
  reset_job -> copytree(outputs/, archive/run_NNN/outputs)
  reset_job -> _build_reset_outputs(staging_dir, target_stage)
  reset_job -> write reset_metadata.json
  reset_job -> rename(outputs/, backup_dir) ; rename(staging_dir, outputs/)  [atomic]
  reset_job -> rmtree(backup_dir)
main -> job_manifest.rebuild_job_manifest
```

---

## PART 12 — CLASS AND SERVICE OVERVIEW

**`ConceptGenerationProvider` (ABC, provider_registry.py:69)** — owns: identity (`provider_id`, `label`, `is_live`), `is_configured()`, `capabilities()`, and the single abstract `generate()`. Implementations: `ClaudeCodeManualConceptProvider` (`:260`, delegates to `claude_concept_generation.prepare_claude_instructions`), `ClaudeAPIConceptProvider` (`:319`, owns the real Anthropic client construction/streaming/error-mapping and its own `model` property resolving through `resolve_model()`).

**`ImageProvider` (ABC, image_generator.py:155)** — owns `validate_ready()` (default no-op) and abstract `generate_image(request)`. Implementations: `OpenAIImageProvider` (`image_generator.py:174`, the real live implementation — owns OpenAI client construction, the generate-vs-edit branch, image extraction from response, error mapping to `ImageProviderError`), `GeminiImageProvider` (`provider_registry.py:495`, inert placeholder), `ManualImageProviderDescriptor` (`provider_registry.py:522`, metadata-only wrapper around the human-in-the-loop manual workflow, its `generate_image()` deliberately raises since the real workflow isn't synchronous).

**`ConceptGenerationRequest`/`ConceptGenerationResult` and `ImageGenerationRequestV2`/`ImageGenerationResultV2` (provider_contracts.py)** — plain constructor classes (not dataclasses) with `.to_dict()`. Results built via `success()`/`partial_success()`/`failed()`/`cancelled()` classmethods (`:231-258`, `:391-420`) rather than direct construction, guaranteeing status/field consistency. `ImageGenerationResultV2.image_bytes_list` deliberately excluded from `.to_dict()` (binary payload, never JSON-serialized — `:360-362`).

**`ImageGenerator` (image_generator.py:387)** — the orchestrator "service": owns `provider` + `config`, and methods `_build_metadata`, `_compile_budgeted_prompt`, `_finalize_failure`, `generate_for_package` (never raises — always returns an outcome dict, PART 4 table). This is the closest thing to a "manager" object in the codebase.

**`PromptSection` / `PromptBudgetResult` (prompt_budget.py:75, :343)** — `PromptSection` is a lightweight value object (`name`, `text`, `priority`, `side`, `layer`, `topic`, `applicable`) with `to_dict()`/`from_dict()` round-tripping through `prompt_package.json`'s `prompt_sections` field. `PromptBudgetResult` is the compiled output of `compile_prompt_budget()` — carries the two final strings actually submitted (`system_prompt_text`, `approved_prompt_text`) plus a full audit trail (`skipped_not_applicable`, `deduplicated`, `omitted_for_budget`, `warnings`).

**`LayeredSystemPrompt` / `SystemPromptLayer` (system_prompt.py:228, :91)** — `SystemPromptLayer` wraps one loaded text+metadata pair (`.version`, `.name`, `.as_dict()`). `LayeredSystemPrompt` is the immutable combined result — `.text` (final prompt string), `.combined_version` (a composite string like `base-1.0__store-1.0__campaign-1.0`), `.human_philosophy_included` property.

No ORM/database classes exist anywhere — the entire persistence layer is plain JSON files written via small local `_write_json`/`_write_text` helpers duplicated per module (a deliberate no-shared-IO-layer convention visible across `job_manifest.py`, `concept_planner.py`, `prompt_builder.py`, etc.).

---

## PART 13 — CONTROLLER TOUCHPOINTS

**What the Controller should launch**: nothing inside `src/` directly for interactive stages (those are `input()`-driven and not designed for programmatic control). For automatable stages, call the importable `run_*()`/`prepare_*`/`check_*`/`finalize_*` functions listed in PART 4/PART 9 directly, or shell out to `python src/main.py` only if a human is actually present to drive the terminal.

**What the Controller should monitor**: poll `jobs/*/job_manifest.json` for `pipeline_status.ready_for_controller`; read `warnings` array for anything to surface to a human before ingesting.

**What the Controller should consume**: `outputs/approved_media_handoff.json` for the list of approved assets (paths, provider/model provenance, store/campaign/product metadata) — this is the one artifact explicitly designed to be handed off (`job_manifest.py:19-21`). For deeper per-asset detail, follow the handoff entry's `generation_metadata_path`/`prompt_package_snapshot_path` pointers rather than re-deriving.

**What the Controller should NEVER access**: raw API keys (never present in any job file — verified invariant, `provider_registry.py:124-127`); it should never write into `jobs/<job>/config/generation_config.json` or `creative_brief.json` directly — those are owned by `job_config.py`/`creative_brief.py`'s save functions, and hand-editing risks violating shape assumptions those modules' loaders don't defensively re-validate everywhere. It should never delete `archive/run_NNN/` folders (job_reset's audit trail) or manipulate `.reset_staging_*`/`.outputs_pre_reset_*` transient directories (these only exist mid-swap and their presence after a crash indicates a failed reset needing investigation, not automated cleanup).

**What should remain encapsulated forever**: the interactive review UX (concept/prompt/image review loops) — a Controller building its own review UI should read/write the same underlying files (`*_concepts.json` `status` field, `generation_metadata.json`/`prompt_package.json` `review_status` field) using the same values (`proposed`/`approved`/`rejected`, `not_reviewed`/`approved`/`rejected`) rather than reimplementing `concept_review.py`/`image_review.py`'s CLI logic, but it must independently call `image_review.rebuild_approved_media_handoff()` after any review-status change it makes, since that rebuild is not automatic outside `_set_review_status()`.

**Provider extension point**: adding a new live concept or image provider means implementing `ConceptGenerationProvider`/`ImageProvider` and registering it in `provider_registry.py`'s `CONCEPT_PROVIDERS`/`IMAGE_PROVIDERS` dict — nothing else in the pipeline needs to change (`docs/provider_readiness.md:382-407` walks through exactly this for OpenAI/Gemini).

---

## PART 14 — HIDDEN ASSUMPTIONS

1. **"Claude Code — Manual" concept generation assumes a human is co-located with an interactive Claude Code session in this same repo.** `prepare_claude_instructions()` writes a task file and copies it to clipboard, but nothing in the codebase can programmatically invoke that session — `claude_concept_generation.py`'s docstring is explicit that there is "no supported mechanism for a standalone Python process to reach into an interactive Claude Code session" (`:6-8`). A Controller cannot automate this path; it can only automate the Claude API path (requires `ANTHROPIC_API_KEY`).

2. **`generation_status` values are an implicit state machine never centrally enumerated** — `not_generated`/`generating`/`generated`/`failed` are scattered string literals across `image_generator.py` and `manual_image_generation.py` rather than a shared constant module (unlike `physical_schema.py`'s enums, which ARE centralized). A Controller parsing these must hardcode the same four strings.

3. **`review_prompts` completion is currently a rubber stamp, not a real per-package decision** — `_prompt_review_complete()` only checks for a marker file written once a human passes through the (currently view-only) screen; there is no approve/reject/regenerate/edit per prompt package yet, despite the manifest schema and CLI already having placeholders for it (`job_manifest.py:299-308` docstring is explicit about this being a known gap).

4. **Reference-image role classification is filename-keyword heuristic, not vision-based.** `infer_role()` (`reference_assets.py:197-204`) guesses roles purely from filename substrings (`"model"`, `"front"`, `"back"`, etc.) — a reference image with an unhelpful filename silently becomes role `"other"`, with no visual verification anywhere in the pipeline.

5. **The concept-count "select" targets (`*_concepts_to_select`) are advisory, not enforced.** `concept_review_complete` becomes true either when every concept is decided OR when the select target is met for BOTH categories — a user can stop reviewing early once enough are approved, leaving some concepts permanently `proposed` and invisible to any later automated pass (`job_manifest.py:247-277`).

6. **The system prompt is always reloaded live at image-generation time, deliberately ignoring the per-job snapshot Build Prompts wrote** (`image_generator.py:541-546`) — if a store/campaign prompt file is edited between Build Prompts and Generate Images, the job silently generates against the NEW prompt text, not what a human reviewer actually saw in `user_prompt.txt`/the snapshot. The system-prompt side of `user_prompt.txt`'s review is therefore only as trustworthy as "the prompt files haven't changed since," which nothing enforces or warns about.

7. **`available_print_sides` defaulting to `"unknown"` means print-side compatibility is a WARNING, never a hard block, for the (likely common) case where a job was created before this field existed or a user never set it** — a Controller relying on `evaluate_print_side_compatibility()` to gate final asset selection needs to treat `"warning"` status as "needs human judgment," not "safe," since it's the default state for old data, not just genuinely ambiguous cases.

8. **Manual image generation accepts "the first file in each incoming folder becomes image_001"** with no verification the downloaded file actually corresponds to the concept it's filed under — `_find_incoming_images()` (`manual_image_generation.py:392-415`) trusts folder placement entirely; a misplaced file is imported as if correct with no cross-check against the prompt that was supposedly used.

9. **`HIDDEN_JOB_NAMES = {"config-test", "example_product", "test-shirt"}`** (`main.py:108`) are hardcoded dev/test job folders kept in the repo and hidden from the production job list but never deleted — a Controller directory-walking `jobs/*` without this same exclusion list will see these as real jobs (though `test-shirt`'s manifest confirms it's an incomplete stub, and `config-test`/`example_product` likely lack real manifests too).

10. **No file locking or concurrency control anywhere** — `job_reset.py`'s archive-then-swap is atomic against crashes but not against a second concurrent process modifying the same job mid-reset; the whole pipeline assumes single-process, single-user access to a given job folder at a time.

agentId: a71d7315e4d59ee80 (use SendMessage with to: 'a71d7315e4d59ee80', summary: '<5-10 word recap>' to continue this agent)
<usage>subagent_tokens: 346683
tool_uses: 39
duration_ms: 306420</usage>

---

# CHAPTER C — etsy-video-generator

# Etsy Video Generator — Engineering Handbook

Root: `E:\Vilicity\etsy-video-generator`. All line numbers below refer to `src/generate_video.py` unless otherwise noted.

## PART 1 — REPOSITORY MAP

```
etsy-video-generator/
├── README.md                     # user-facing feature list + workflow + Claude CLI notes
├── ROADMAP.md                    # "not implemented yet" ideas list, explicitly separated from README
├── order-preview.jpg             # LATEST contact-sheet artifact, overwritten every run (root, not input/)
├── src/
│   └── generate_video.py         # ALL business logic (1567 lines), the only file that matters at runtime
├── tests/
│   ├── __init__.py
│   ├── test_controller_readiness.py   # contract tests for run_video_generation()/VideoGenerationError/manifest
│   ├── test_design_reveal.py          # Design Reveal preset unit + smoke tests
│   └── test_wisp_sweep.py             # Wisp Sweep preset unit + smoke tests
├── input/                         # NOT present in current tree listing (created on demand — see PART 6); user drops 3-5 images here before a run
├── output/                        # generated videos ONLY — the operator's deliverables folder (V1 polish; manifests moved to metadata/)
│   ├── listing-video-001.mp4
│   ├── listing-video-002.mp4
│   ├── listing-video-003.mp4
│   └── listing-video-004.mp4
├── metadata/                      # one JSON generation manifest per video, named after it (manifest_path_for())
│   ├── listing-video-003.json
│   └── listing-video-004.json     # 001/002 are pre-manifest-era output and have none (see PART 14)
├── processed-inputs/              # source images moved here post-success, one subfolder per video
│   ├── listing-video-001/ … listing-video-004/
│   └── listing-video-005/         # ORPHAN: no matching output/listing-video-005.* anywhere (see PART 14)
├── archived-outputs/              # currently empty; read-only from the script's perspective (never written/moved by it)
├── archived-backups/              # six pre-V3 full-file snapshots of generate_video.py, explicitly excluded from src/
│   ├── generate_video_v1_backup.py
│   ├── generate_video_v2_current_backup.py
│   ├── generate_video_v2_final_backup.py
│   ├── generate_video_ordering_backup.py
│   ├── generate_video_numbering_backup.py
│   └── generate_video_cleanup_backup.py
├── docs/superpowers/
│   ├── specs/2026-07-11-wisp-sweep-color-variation-design.md   # approved design spec for the Wisp Sweep preset
│   └── plans/2026-07-11-design-reveal.md                        # TDD task-by-task implementation plan for Design Reveal
└── .claude/settings.local.json    # local permission allowlist (`Bash(ffmpeg -version)`) — not part of the app
```

**Why each folder exists**

- `src/` — deliberately singular. The whole project is one script; there is no package structure, no `__init__.py` at the source level (only under `tests/`). This is a conscious "constants + pure functions + thin orchestrator" design (see PART 10), not an oversight.
- `input/` — the only writable folder a human (or, eventually, a Controller) is expected to populate before a run; `find_input_images()` (line 355) fails loudly if it's missing.
- `output/` — the durable product of every successful run: one `.mp4` per run, named by `get_next_output_file()` (line 584). Videos only, as of V1 polish; its manifest lives in `metadata/` (see `manifest_path_for()`).
- `archived-outputs/` — a manual, human-curated overflow bucket. The code reads it only to avoid re-using numbers (`find_highest_video_number`, line 559) — it is never written, moved into, or otherwise touched (explicitly documented at lines 83-86).
- `processed-inputs/` — a receipt trail: once a video exists, its source images are relocated here into a subfolder that shares the video's filename stem (line 1317), so which raw images produced which delivered video is always reconstructable, and generic filenames (Canva's "1.png" etc.) across different runs never collide (lines 28-35 module docstring, and `move_images_to_processed` docstring lines 1295-1316).
- `archived-backups/` — pure historical reference; the README (lines 40) explicitly calls these "superseded pre-V3 snapshots… excluded from src". They are not imported by anything and are irrelevant to a Controller.
- `docs/superpowers/` — design-process artifacts (spec + TDD plan) for two of the four presets, valuable for understanding *why* certain constants exist but not runtime inputs.
- `.claude/settings.local.json` — Claude Code tooling config, irrelevant to the Controller.

## PART 2 — MODULE MAP (function-by-function)

Each entry: Purpose / Responsibilities / Callers / Callees / Inputs / Outputs / Internal-only vs Controller-relevant / Dependencies.

### `check_ffmpeg_is_available()` (line 341)
- Purpose: verify the `ffmpeg` binary is on PATH.
- Calls: `shutil.which("ffmpeg")`.
- Called by: `main()` (line 1522).
- Inputs: none. Outputs: none (or raises).
- Raises `VideoGenerationError` if `shutil.which` returns `None` (line 347-352).
- Internal-only in the sense that a Controller wouldn't call it directly, but it documents an environment precondition the Controller's host process must satisfy before ever invoking `run_video_generation()` — see PART 14 (this check is NOT re-run inside `run_video_generation()`).

### `find_input_images()` (line 355)
- Purpose: enumerate and validate the `input/` folder.
- Filters files by `ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}` (line 309), case-insensitive.
- Enforces `MIN_IMAGES = 3`, `MAX_IMAGES = 5` (lines 311-312).
- Returns: list of `Path`, order = OS iteration order (unspecified — the caller must sort).
- Raises `VideoGenerationError` if `INPUT_DIR` doesn't exist or count is out of range.
- Called only by `main()` — **not called by `run_video_generation()`**. This is architecturally significant: see PART 14 ("does run_video_generation() trust the caller?").

### `sort_by_best_drop_order_guess(images)` (line 384)
- Purpose: produce a *best guess* image ordering, sorting by `st_ctime` (Windows file-creation time), ported from an earlier investigation script (`test_image_order.py`, not present in this repo — historical reference only).
- Pure function: input list → new sorted list. Never authoritative; always followed by a human confirm/correct step in `main()`.
- Internal-only (interactive-path helper) but conceptually relevant: a Controller supplying its own confirmed order bypasses this entirely.

### `print_numbered_order(images)` (line 396)
- Purpose: console pretty-printer, `1. name.png` etc.
- Pure side effect (stdout). Internal-only, CLI-only.

### `find_font_file()` (line 407)
- Purpose: locate a real `.ttf` on disk from `CANDIDATE_WINDOWS_FONTS` (line 333: Arial Bold, Arial, Calibri Bold, Calibri, hardcoded Windows paths).
- Raises `VideoGenerationError` if none exist.
- Called only by `build_contact_sheet()`. Windows-only dependency (see PART 5).

### `format_font_path_for_ffmpeg(font_path)` (line 423)
- Purpose: escape a Windows path for FFmpeg's `drawtext` filter syntax — swaps `\`→`/`, escapes `:` as `\:` (lines 429-434), since FFmpeg filter option syntax uses `:` as a separator.
- Pure string transform. Called only by `build_contact_sheet()`.

### `build_contact_sheet(images)` (line 437)
- Purpose: invoke FFmpeg to render `order-preview.jpg` — every input image scaled to `PREVIEW_IMAGE_HEIGHT=500` (line 327), labeled with a big position number via `drawtext`, glued left-to-right with `hstack`.
- Only reads image files; only writes `PREVIEW_PATH` (`PROJECT_ROOT / "order-preview.jpg"`, line 323). Never touches video files.
- Raises `VideoGenerationError` on missing ffmpeg or non-zero FFmpeg return code (captures `result.stderr`).
- Called only by `main()`. Not part of `run_video_generation()`'s path — a Controller does not get a preview image unless it separately calls this.

### `ask_yes_no(question)` (line 506) / `ask_for_corrected_order(images)` (line 518) / `ask_for_preset()` (line 1393)
- Purpose: blocking `input()`-based CLI prompts. Loop until valid input.
- `ask_for_corrected_order` parses a comma-separated permutation ("3,1,2,4"), validates it's exactly a permutation of `1..len(images)` (lines 546-552), and remaps to `Path` order.
- `ask_for_preset` is generated generically from the `PRESETS` list (line 1368) — see PART 10.
- **All three are strictly Internal, interactive-only, and MUST NEVER be called by a Controller** (they block on stdin indefinitely).

### `find_highest_video_number(folder)` (line 559)
- Purpose: scan one folder for filenames matching `NUMBERED_VIDEO_PATTERN = r"^listing-video-(\d{3,})\.mp4$"` (line 99, case-insensitive) and return the max NNN found, or `0` if folder missing/empty/no matches.
- Pure(ish) function with filesystem read. Called by `get_next_output_file()`.

### `get_next_output_file()` (line 584)
- Purpose: compute the path for the next video, by taking `max(highest_in_OUTPUT_DIR, highest_in_ARCHIVED_OUTPUTS_DIR) + 1`, zero-padded to at least 3 digits (`f"listing-video-{n:03d}.mp4"`, line 599).
- Called by `main()` only, right before `run_video_generation()`. **Not called internally by `run_video_generation()`** — a Controller must call the equivalent logic itself or reuse this function directly, and must do so at the last responsible moment (see PART 14 — numbering is not "reserved" until a manifest exists).

### The four `compute_image_durations*` functions (pacing layer)
All are pure `int -> list[float]` transforms, each preset's pacing is independently tunable per their docstrings (explicitly documented non-interference):
- `compute_image_durations(num_images)` (line 603) — Standard. Special-cased at `num_images==3` (`THREE_IMAGE_DURATIONS_SECONDS = [1.4, 2.0, 2.0]`, line 164); otherwise `FIRST_IMAGE_SECONDS=1.0` fixed, remaining images split `(total - first) / (n-1)`, where `total = BASE_TOTAL_DURATION_SECONDS(5.0) + DURATION_STEP_PER_EXTRA_IMAGE(0.5) * (n - MIN_IMAGES)`.
- `compute_image_durations_slow_fade(num_images)` (line 627) — first image fixed at `SLOW_FADE_FIRST_IMAGE_SECONDS=0.4`, remaining budget `SLOW_FADE_TOTAL_HOLD_BUDGET_SECONDS(3.4) - 0.4` split evenly.
- `compute_image_durations_wisp_sweep(num_images)` (line 645) — structurally identical shape to Slow Fade but its own constants (`WISP_SWEEP_FIRST_IMAGE_SECONDS=0.4`, `WISP_SWEEP_TOTAL_HOLD_BUDGET_SECONDS=3.4`).
- `compute_image_durations_design_reveal(num_images)` (line 664) — flat, no first-image special case: every image gets `DESIGN_REVEAL_PRE_REVEAL_HOLD_SECONDS(0.7) + DESIGN_REVEAL_REVEAL_DURATION_SECONDS(1.7) + DESIGN_REVEAL_HOLD_DURATION_SECONDS(0.7) = 3.1s`.
- Each is called exactly once, by its matching `build_ffmpeg_command_*` builder.

### The four `build_ffmpeg_command_*` functions — the real complexity
See PART "FFmpeg deep dive" content folded into this section for depth.

**`build_ffmpeg_command(images, output_file)` (line 1016) — "Standard"**
- For each image: `-loop 1 -t <duration> -i <path>` input (line 1034).
- Filter chain per image: `scale=STANDARD_ZOOM_WORKING_SIZE:...` → `pad=...` (letterbox to square, black fill) → `setsar=1` → `fps=30` → `zoompan=z='1+ZOOM_MAX_INCREASE*on/(num_frames-1)':d=1:s=1080x1080:fps=30:x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'` (subtle linear 4% zoom-in over the clip, centered crop) → conditional `fade=t=in`/`fade=t=out` to black (`TRANSITION_HALF_SECONDS=0.35` each) on interior boundaries only (not on the very first/last image edges).
- `STANDARD_ZOOM_WORKING_SIZE = VIDEO_SIZE * 8` (line 146) — an 8x supersample specifically for this preset (separate from the general `ZOOM_WORKING_SIZE` at 4x used by other presets), because zoompan's crop rectangle must round to whole pixels every frame; at native resolution the ~4% zoom range only offers a handful of distinct integer crop widths across a clip's frame count, causing "shake" (documented at length lines 106-146). A code comment (lines 1074-1081) also flags a specific FFmpeg gotcha: `zoompan`'s own `fps=` option defaults to 25 if unset, silently overriding the preceding `fps=30` filter — explicitly set here to avoid frame-rate mismatch artifacts.
- All per-image filtered streams are joined with `concat=n=N:v=1:a=0[outv]` (never `xfade`) — clips sit back-to-back; the fade-out half of one clip and fade-in half of the next abut on the shared timeline so the video only ever blends to/from black, never directly between two images (module docstring lines 1052-1053).
- Output flags: `-map [outv] -r 30 -pix_fmt yuv420p <output_file>`.

**`build_ffmpeg_command_slow_fade(images, output_file)` (line 1108) — "Slow Fade – Color Variation"**
- Each image stays perfectly static (no zoompan) — just `scale=ZOOM_WORKING_SIZE:...,pad=...,setsar=1,fps=30,scale=1080:1080` (down to final size directly, no zoom crop).
- Clip lengths are padded: every image with a transition on one/both sides needs `SLOW_FADE_TRANSITION_SECONDS(1.0)` extra real footage on that side so the crossfade has fresh material (lines 1129-1138): first image `+1×transition`, last `+1×transition`, interior images `+2×transition`.
- Joined with `xfade=transition=fade:duration=1.0:offset=<running_length-transition>` chained left-to-right (`[v0][v1]xfade...[x1]`, `[x1][v2]xfade...[outv]`, etc.) — a *true dissolve* (both images briefly overlap-blended), never a black frame in between. This is explicitly reserved for products whose garment/color changes but shape stays identical (module comment lines 184-197).
- Same output tail (`-map [outv] -r 30 -pix_fmt yuv420p`).

**`build_ffmpeg_command_wisp_sweep(images, output_file)` (line 911) — "Wisp Sweep – Color Variation"** — the most elaborate builder.
1. Same static per-image scale/pad as Slow Fade (no zoompan), clip lengths padded the same way using `WISP_SWEEP_TRANSITION_DURATION_SECONDS=0.9`.
2. Chained with `xfade` exactly like Slow Fade, but **also records each transition's start offset** (`transition_offsets` list, line 963) on the final output timeline — reused to place the sweep overlay at the exact same point.
3. **Sweep geometry** (`compute_wisp_sweep_geometry`, line 793): projects the frame's four corners onto a diagonal axis defined by `WISP_SWEEP_ANGLE_DEGREES=25`; computes `proj_mid` (axis midpoint), `core_half_width`/`glow_half_width` from `WISP_SWEEP_WIDTH_FRACTION=0.18` of the frame diagonal and `WISP_SWEEP_GLOW_WIDTH_MULTIPLIER=2.5`; computes `actual_span` — the total travel distance of the sweep band, padded by `4*glow_half_width` so the band starts/ends fully off-frame (never pops in as a flash) and scaled by `WISP_SWEEP_SPEED_MULTIPLIER=1.0`.
4. **Alpha expression** (`build_wisp_sweep_alpha_expression`, line 840): an FFmpeg `geq` per-pixel expression combining a bright "core" band and a dimmer, wider "glow" halo, each a Hann/raised-cosine falloff (`0.5*(1+cos(PI*diff/half_width))`) that reaches exactly zero at its own half-width — this is what produces feathered edges without any blur filter. `center(T)` linearly interpolates the band's position across the transition's own local clock `T` (0 to `transition_duration`).
5. **Synthetic sweep input** (`build_wisp_sweep_input_args`, line 880): for each transition, a separate `lavfi` `color=c=black:s=1080x1080:d=<transition_duration>:r=30,format=rgba,geq=r=255:g=255:b=255:a='<alpha_expr>'` input is added, positioned on the real output timeline via `-itsoffset <offset>` (the standard FFmpeg trick for compositing a short generated clip at a specific timestamp without manual `setpts` math) — note the source's OWN internal clock still starts at 0, matching what the alpha expression assumes.
6. **Overlay chain** (lines 991-1002): one `overlay=x=0:y=0:enable='between(t,offset,offset+duration)'` stage per transition, each reading the previous stage's output — N transitions → N chained overlay stages. Relies on FFmpeg's `overlay` filter defaulting to `eof_action=repeat` so a sweep input's short frame supply doesn't stall later overlays (explicitly flagged as an "open risk… confirmed empirically" in the design spec, not just assumed).
7. Final `filter_complex` = scale/pad filters `;` xfade filters `;` overlay filters, joined, then `-map [outv] -r 30 -pix_fmt yuv420p`.
- Test evidence: 3 images → 5 `-i` flags (3 image + 2 sweep), 2 `xfade=` + 2 `overlay=` stages (`tests/test_wisp_sweep.py` lines 209-222); 5 images → 9 `-i` flags, 4 overlays.

**`build_ffmpeg_command_design_reveal(images, output_file)` (line 1203) — "Design Reveal"**
- Reuses Standard's fade-through-black + `concat` mechanism (its own independent constants `DESIGN_REVEAL_FADE_DURATION_SECONDS=0.5`/`HALF_SECONDS=0.25`, NOT `TRANSITION_DURATION_SECONDS`), not `xfade`.
- Per-image zoompan crop, driven by a shared "hold → ease → hold" curve (`_design_reveal_hold_ease_hold_expression`, line 684) applied to THREE quantities in lock-step: zoom level, focal-x, focal-y. This is the deepest logic in the file:
  - `pre_hold_frames = round(0.7*30) = 21`, `reveal_frames = round(1.7*30) = 51`.
  - Holds flat at `start_value` for the first `pre_hold_frames`, then eases via **smootherstep** `t³(t(6t−15)+10)` (not smoothstep `3t²−2t³`) over the reveal window, then holds flat at `end_value`. The file explains (lines 690-717) that smootherstep was chosen over smoothstep because smoothstep zeroes velocity but not *acceleration* at the boundaries, producing a perceptible "kink" where the eased motion meets the flat hold; smootherstep zeroes both.
  - Zoom: `DESIGN_REVEAL_START_ZOOM=1.6` (tight crop) → `DESIGN_REVEAL_END_ZOOM=1.0` (full frame).
  - Focal point: `(DESIGN_REVEAL_CENTER_X=0.50, DESIGN_REVEAL_CENTER_Y=0.44)` → `(DESIGN_REVEAL_FINAL_CENTER_X=0.5, DESIGN_REVEAL_FINAL_CENTER_Y=0.5)` — true center is the only valid focal point once zoom reaches 1.0 (a full frame has no room to offset). Driving zoom and focal point off the *same* eased `t` is what makes them arrive at their end state simultaneously; `clip()` in the crop-x/y expressions (`crop_x_expr`/`crop_y_expr`, lines 1251-1252) is now purely a defensive floor/ceiling against float rounding, not an active corrector (this was refactored from an earlier version that DID rely on `clip()` to correct — see the docstring at lines 710-717).
  - `DESIGN_REVEAL_ZOOM_WORKING_SIZE` uses its own 8x supersample factor (matching Standard's fix), independently tunable.
- Test evidence (`tests/test_design_reveal.py` lines 179-272) numerically verifies: strictly increasing step sizes near both hold boundaries (true ease, not linear), no more than 1 duplicate rounded crop-width frame with ≤20px follow-up jump (jitter guard), and zoom/focal curves reach their end value exactly at the same frame with no late correction.

### `move_images_to_processed(images, output_file)` (line 1295)
- Purpose: relocate source images from `input/` into `processed-inputs/<output_file.stem>/`.
- Two-phase: (1) check every destination filename for a clash BEFORE moving anything, raising `VideoGenerationError` if any exists (lines 1320-1333); (2) only then perform all `shutil.move` calls. This prevents a half-moved state on failure.
- Called only by `run_video_generation()`, and only AFTER the output file's existence is confirmed — the docstring is explicit that this ordering is load-bearing (lines 1308-1310).

### `Preset` (NamedTuple, line 1342) + `PRESETS` (line 1368)
- See PART 10/12 for the pattern. Fields: `key: str`, `display_name: str`, `builder: Callable[[list, Path], list]`, `is_default: bool = False`.
- Exactly one entry has `is_default=True` (`"standard"`).

### `ask_for_preset()` (line 1393)
- Purpose: interactive menu generated generically from `PRESETS` — Enter with no input returns the default preset's key; a valid `[1..len(PRESETS)]` number returns that preset's key.
- Internal-only, blocks on `input()`.

### `build_generation_manifest(images, preset_key, preset_display_name, output_file)` (line 1420)
- Purpose: build the JSON-serializable dict (see PART 7 for exact schema).
- Pure function, no I/O. Deliberately flat/unversioned so future fields can be added additively (docstring lines 1423-1425).
- Called by `write_generation_manifest()`.

### `write_generation_manifest(images, preset_key, preset_display_name, output_file)` (line 1437)
- Purpose: write the manifest as `metadata/<output_file.stem>.json` (`manifest_path_for(output_file)`), creating `metadata/` if needed. As of V1 polish this is no longer a sibling of the video — `output/` is the operator's deliverables folder.
- Returns the manifest's `Path`. Called by `run_video_generation()`.

### `run_video_generation(images, preset_key, output_file)` (line 1449) — THE reusable entry point
- Purpose: the entire non-interactive generation workflow: look up the preset, build+run its FFmpeg command, verify the file exists, move source images, write the manifest.
- Callers: `main()` (line 1561); designed for a future Controller module to call directly.
- Calls: `chosen_preset.builder(images, output_file)`, `subprocess.run(command, ...)`, `output_file.exists()`, `move_images_to_processed()`, `write_generation_manifest()`.
- Inputs: `images` (already-confirmed `Path` order — this function does NOT re-derive or re-validate order), `preset_key` (must be a valid `PRESETS` key — looked up via `next(...)`, which raises `StopIteration`, NOT `VideoGenerationError`, if the key is invalid — see PART 14), `output_file` (exact target path, caller's responsibility to have computed via `get_next_output_file()` or equivalent).
- Outputs: returns `output_file` (`Path`) on success.
- Raises `VideoGenerationError` on FFmpeg failure (non-zero return code, embeds `stderr`) or on the file existing check failing.
- No `input()` calls anywhere in this function — fully headless. Confirmed by `tests/test_controller_readiness.py`'s `TestRunVideoGenerationSuccess`/`Failures` classes, which mock `subprocess.run`, `move_images_to_processed`, and `write_generation_manifest` to exercise this in isolation.

### `main()` (line 1513)
- Purpose: the interactive CLI shell — orchestrates every human-facing step then hands off to `run_video_generation()`.
- Sequence: `check_ffmpeg_is_available()` → `find_input_images()` → print found images → `sort_by_best_drop_order_guess()` → print detected order → `build_contact_sheet()` → `ask_yes_no("Is this order correct?")` → (if no) `ask_for_corrected_order()` → `OUTPUT_DIR.mkdir(parents=True, exist_ok=True)` → `get_next_output_file()` → `ask_for_preset()` → `run_video_generation(confirmed_order, preset_key, output_file)`.
- The entire body is wrapped in `try/except VideoGenerationError as exc: sys.exit(str(exc))` (lines 1521-1563) — this is the ONLY place in the module that converts the module's internal exception type back into a process exit, preserving the script's historical CLI behavior while keeping every business-logic function importable/catchable.

## PART 3 — CALL GRAPH

**(a) Full interactive `main()` run:**
```
main()
├─ check_ffmpeg_is_available()
├─ find_input_images()
├─ print(...) / loop over images (stdout)
├─ sort_by_best_drop_order_guess(images)
├─ print_numbered_order(detected_order)
├─ build_contact_sheet(detected_order)
│    ├─ shutil.which("ffmpeg")
│    ├─ find_font_file()
│    ├─ format_font_path_for_ffmpeg(font_path)
│    └─ subprocess.run(ffmpeg ... -> order-preview.jpg)
├─ ask_yes_no("Is this order correct?")          [BLOCKS on stdin]
│    └─ (if "N") ask_for_corrected_order(detected_order)   [BLOCKS on stdin]
├─ OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
├─ get_next_output_file()
│    ├─ find_highest_video_number(OUTPUT_DIR)
│    └─ find_highest_video_number(ARCHIVED_OUTPUTS_DIR)
├─ ask_for_preset()                               [BLOCKS on stdin]
└─ run_video_generation(confirmed_order, preset_key, output_file)
     ├─ lookup: next(p for p in PRESETS if p.key == preset_key)
     ├─ chosen_preset.builder(images, output_file)   # one of the four build_ffmpeg_command_* fns
     ├─ subprocess.run(ffmpeg command)
     ├─ output_file.exists() check
     ├─ move_images_to_processed(images, output_file)
     └─ write_generation_manifest(images, preset_key, display_name, output_file)
          └─ build_generation_manifest(...)
except VideoGenerationError as exc: sys.exit(str(exc))
```

**(b) `run_video_generation()` in isolation — the Controller path:**
```
# Controller has already: validated image count/type, confirmed order, chosen a preset_key,
# and computed output_file (e.g. by calling get_next_output_file() itself, or its own equivalent).

run_video_generation(images: list[Path], preset_key: str, output_file: Path) -> Path
├─ chosen_preset = next(p for p in PRESETS if p.key == preset_key)   # raises StopIteration if bad key — NOT caught
├─ command = chosen_preset.builder(images, output_file)
├─ result = subprocess.run(command, capture_output=True, text=True)
├─ if result.returncode != 0: raise VideoGenerationError(...)        # <-- Controller must catch this
├─ if not output_file.exists(): raise VideoGenerationError(...)      # <-- and this
├─ move_images_to_processed(images, output_file)                     # can also raise VideoGenerationError (name clash)
├─ manifest_path = write_generation_manifest(images, preset_key, chosen_preset.display_name, output_file)
└─ return output_file
```

## PART 4 — ENTRY POINTS

| Function | Purpose | Args | Returns | Exceptions | Interactive? | Importable? | Reusable? | Headless? | Controller-safe? |
|---|---|---|---|---|---|---|---|---|---|
| `run_video_generation` (1449) | Full non-interactive generation | `images: list[Path]`, `preset_key: str`, `output_file: Path` | `Path` (output_file) | `VideoGenerationError`; **`StopIteration`** if `preset_key` invalid (unguarded `next()` at line 1473) | No | Yes | Yes | Yes | **Yes** — the intended integration point |
| `main` (1513) | Interactive CLI | none | `None` (calls `sys.exit` on error path) | none escapes (converts to `sys.exit`) | Yes, three `input()` prompts | Yes (as a function) | No | No | **No — blocks on stdin; never call from a Controller** |
| `build_generation_manifest` (1420) | Build manifest dict | `images, preset_key, preset_display_name, output_file` | `dict` | none | No | Yes | Yes, standalone-callable | Yes | Yes, but only useful alongside a real generation — a Controller inspecting a *past* run should read the on-disk JSON, not recompute it (this recomputes `timestamp` as "now") |
| `write_generation_manifest` (1437) | Write manifest to disk | same + writes file | `Path` | Python I/O errors (unguarded — not wrapped in `VideoGenerationError`) | No | Yes | Yes | Yes | Callable standalone but normally only invoked internally by `run_video_generation` |
| `get_next_output_file` (584) | Compute next numbered path | none | `Path` | none | No | Yes | Yes | Yes | Usable by a Controller that wants to precompute a filename, but there is a TOCTOU race — see PART 14 |
| `check_ffmpeg_is_available` (341) | Verify ffmpeg on PATH | none | `None` | `VideoGenerationError` | No | Yes | Yes | Yes | Useful as a Controller-side preflight check |
| `find_input_images` (355) | Enumerate+validate `input/` | none | `list[Path]` | `VideoGenerationError` | No | Yes | Yes | Yes | Only meaningful if the Controller uses this project's own `INPUT_DIR` convention; **not called by `run_video_generation`**, so calling it separately is optional, not required |
| The four `build_ffmpeg_command_*` (1016, 1108, 911, 1203) | Build raw FFmpeg argv | `images: list[Path]`, `output_file: Path` | `list[str]` (argv) | none (pure) | No | Yes | Technically yes | Yes | **No — internal.** These encode filter-graph internals a Controller should never depend on directly; use `run_video_generation` which selects the right builder via `PRESETS` |
| `move_images_to_processed` (1295) | Move source images | `images, output_file` | `None` | `VideoGenerationError` (name clash) | No | Yes | Yes | Yes | Internal — only safe to call after confirming the video exists; calling it standalone bypasses that safety invariant unless the caller reimplements it |

## PART 5 — DEPENDENCY MAP

**Python imports** (lines 51-59): `json`, `math`, `re`, `shutil`, `subprocess`, `sys`, `datetime.datetime`, `pathlib.Path`, `typing.Callable, NamedTuple`. **Confirmed 100% standard library** — the module docstring states this explicitly (lines 37-40) and `requirements.txt`/`pyproject.toml` are absent from the repo (only `Etsy-AI-Image-Generator` in this workspace has a `requirements.txt`; this project has none).

**External binary dependency: FFmpeg**
- Located via `shutil.which("ffmpeg")` (line 347 and again at line 446) — relies entirely on the OS PATH; no bundled binary, no version pinning, no version check at all. Any FFmpeg version that accepts the filter syntax used (`zoompan`, `xfade`, `geq`, `concat`, `fade`, `drawtext`, `hstack`, `overlay`, `lavfi`) will work; nothing in the code asserts a minimum version.
- Invoked via `subprocess.run(command, capture_output=True, text=True)` in three places: `build_contact_sheet` (line 497), and inside `run_video_generation` (line 1479) for the actual video render. Always checked via `result.returncode != 0`, with `result.stderr` embedded in the raised `VideoGenerationError`.
- This is the single most architecturally load-bearing external dependency — `run_video_generation()` cannot function without it, and there is no fallback, mock, or pure-Python rendering path.

**Windows font-path dependency**
- `CANDIDATE_WINDOWS_FONTS` (line 333) hardcodes four `C:/Windows/Fonts/*.ttf` paths (Arial Bold, Arial, Calibri Bold, Calibri). This is used ONLY by `build_contact_sheet()` → `find_font_file()`, i.e. only the interactive order-preview path. **`run_video_generation()` never touches fonts** — none of the four `build_ffmpeg_command_*` filter graphs use `drawtext`. This means the font dependency is Windows-specific and incidental to the Controller-relevant core, but load-bearing for `main()`'s interactive flow (confirmed by the module comment lines 328-332 and by the fact that `find_font_file` raises `VideoGenerationError` with an explicit "edit this list" remediation if none are found — no cross-platform fallback exists).

**Architectural (load-bearing for `run_video_generation`) vs incidental**
| Dependency | Used by | Category |
|---|---|---|
| FFmpeg binary | every builder + `run_video_generation` | Architectural |
| `subprocess` | invoking FFmpeg | Architectural |
| `shutil.move`/`shutil.which` | image relocation + ffmpeg lookup | Architectural (`which`), Architectural (`move`, inside `move_images_to_processed`) |
| `json` | manifest write | Architectural |
| `re` (`NUMBERED_VIDEO_PATTERN`) | output numbering | Architectural |
| `math` | Wisp Sweep geometry trig | Architectural (only for that one preset) |
| `datetime` | manifest timestamp | Architectural |
| Windows font paths | `build_contact_sheet`/`find_font_file` | Incidental — interactive-only path |
| `input()`/stdin | `ask_yes_no`, `ask_for_corrected_order`, `ask_for_preset` | Incidental to Controller — CLI-only |

## PART 6 — WORKFLOW STATE MACHINE

States, as actually implemented (there is no explicit state variable anywhere — this is a reconstruction from control flow):

```
[no images]
   │ find_input_images() succeeds (3-5 valid files present)
   ▼
[images found, order unvalidated]
   │ sort_by_best_drop_order_guess() + build_contact_sheet() (writes order-preview.jpg — persisted, but always overwritten next run)
   ▼
[order preview shown]
   │ ask_yes_no() == True  ──────────────► [order confirmed] (uses detected order)
   │ ask_yes_no() == False → ask_for_corrected_order() ──► [order confirmed] (uses corrected order)
   ▼
[order confirmed]
   │ get_next_output_file()  (reads output/ + archived-outputs/ — NOT persisted; a fresh number is recomputed every call)
   ▼
[output filename computed, NOT reserved]
   │ ask_for_preset()
   ▼
[preset chosen]
   │ run_video_generation() begins → builder() builds argv → subprocess.run(ffmpeg)
   ▼
[rendering]
   │ FFmpeg returncode == 0  AND  output_file.exists()
   ▼
[rendered]                                          (if FFmpeg fails, or file missing despite rc==0: VideoGenerationError,
   │                                                  NO images moved, NO manifest written — clean rollback to "order confirmed")
   │ move_images_to_processed() — two-phase clash-check then move
   ▼
[images moved]                                      (if a clash exists: VideoGenerationError raised AFTER the video
   │                                                  already exists on disk — see failure-mode note below)
   │ write_generation_manifest()
   ▼
[manifest written] == [complete]
```

**Persistence at each state**: Nothing is persisted as "workflow state" per se — the only durable artifacts are `order-preview.jpg` (overwritten every run, no run-to-run identity), the eventual `.mp4`, `.json` manifest, and the moved image folder. There is no lock file, no `.in-progress` marker, no journal.

**Recovery / resume**: None. If the process is killed at any point before "complete", there is no resumption mechanism — a fresh `main()` invocation restarts from `[no images]` and would re-detect whatever remains in `input/`. The workflow is NOT idempotent across partial failures in isolation (see the exact failure-mode gap below).

**Cancellation paths**: Only via the two `input()` prompts (Ctrl+C at any point kills the process uncontrolled — no cleanup handler; a corrected-order loop or preset-menu loop can be exited by the user only via interrupt, not by any documented "cancel" input). `run_video_generation()` itself has no cancellation hook at all once called (no timeout, no way to interrupt a running `subprocess.run` from the API).

**The specific failure-mode state: video exists, images NOT moved, manifest NOT written.**
This is real and reachable: `move_images_to_processed` (line 1321-1333) raises `VideoGenerationError` if a destination filename in `processed-inputs/<stem>/` already exists — but this check happens strictly AFTER the video file has already been confirmed to exist (`output_file.exists()` check at line 1491, which happens before `move_images_to_processed` is even called at line 1503). So the reachable failure sequence is: video renders successfully and is written to `output/`, `output_file.exists()` passes, `move_images_to_processed` is called, and if it raises (name clash), the function propagates `VideoGenerationError` UP through `run_video_generation` — meaning **no manifest is ever written**, but **the video file remains on disk**, and **the source images are still sitting in `input/`** (name-clash check happens before ANY move in that call, so it's all-or-nothing for the move step — but the video itself was already an irreversible side effect before that check ran). A Controller catching `VideoGenerationError` from `run_video_generation()` cannot distinguish "FFmpeg never ran" from "video exists but images/manifest are in an inconsistent state" purely from the exception — it must inspect the filesystem (does `output_file` exist? does a manifest exist?) to know which failure sub-state occurred. This is corroborated by the orphan evidence in PART 7/14 (`processed-inputs/listing-video-005/` with no matching output anywhere).

## PART 7 — ARTIFACT DOCUMENTATION

### 1. The output MP4
- Producer: FFmpeg, invoked by `run_video_generation()` via the chosen preset's builder.
- Consumer: the Etsy seller (uploads manually to a listing); a future Controller.
- Lifecycle: written to `output/listing-video-NNN.mp4`; may later be manually relocated to `archived-outputs/` by a human (the script never does this itself — confirmed by lines 83-86 and by `archived-outputs/` being empty in the current tree despite `ARCHIVED_OUTPUTS_DIR` existing as a constant).
- Schema: 1080×1080 (`VIDEO_SIZE=1080`), 30fps (`FRAMES_PER_SECOND=30`), `yuv420p` pixel format (all four builders end with `-pix_fmt yuv420p`), H.264/AAC-less MP4 container (FFmpeg's default encoder for `.mp4` output with no explicit `-c:v`/codec flags — codec choice is left entirely to FFmpeg's own default, NOT pinned in code).
- Versioning: none — filename numbering is the only "version" concept, and it is global across the whole project, not per-listing.
- Contract: `-y` flag means any pre-existing file at that exact path is silently overwritten (relevant since `get_next_output_file()` is recomputed fresh each call — see PART 14 TOCTOU risk).

### 2. The JSON generation manifest
- Producer: `write_generation_manifest()` / `build_generation_manifest()` (lines 1420-1446).
- Consumer: intended for a future Controller (per README/ROADMAP); currently nothing in-repo reads it back except the tests.
- Lifecycle: written once, immediately after a successful move of source images, named `<output_file.stem>.json` (e.g. `listing-video-004.json`) — into `metadata/`, NOT beside the video. **Changed during V1 polish**: the manifest used to be a `.with_suffix(".json")` sibling in `output/`, which made the operator's deliverables folder half videos and half bookkeeping. `manifest_path_for(output_file)` is now the single definition of the location; `output/` holds finished MP4s only. The manifest itself is unchanged and still written on every success.
- **Exact schema** (verified against both the source in `build_generation_manifest` and two real on-disk files):
```json
{
  "success": true,
  "timestamp": "2026-07-20T22:34:45.354759",   // datetime.now().isoformat() — naive, LOCAL time, no timezone offset (see PART 14)
  "preset_key": "wisp-sweep-color-variation",   // one of PRESETS[i].key
  "preset_display_name": "Wisp Sweep - Color Variation",
  "output_video": "listing-video-004.mp4",      // output_file.name only, not a full/relative path
  "image_order": ["flat-front-01.png", "flat-front-19.png", "flat-front-17.png", "flat-front-13.png"],  // filenames only (image_path.name), in confirmed display order
  "image_count": 4
}
```
  Cross-checked live against `listing-video-003.json` and `listing-video-004.json` (now under `metadata/`) — both match this shape exactly, no extra/missing fields.
- Versioning: explicitly none — "a flat, simple structure — not versioned or namespaced" (docstring lines 1421-1425) so future fields can be added additively without a migration story. A Controller must treat unknown extra keys as forward-compatible and must not assume the field set is closed.
- Public contract vs internal: the **currently-tested field set** (`success`, `timestamp`, `preset_key`, `preset_display_name`, `output_video`, `image_order`, `image_count` — enforced by `tests/test_controller_readiness.py`'s `TestBuildGenerationManifest`) is the only part a Controller should treat as stable. `success` is always `true` in the current implementation (there is no code path that writes a manifest with `success: false` — a failure never produces a manifest at all, it raises instead).

### 3. `order-preview.jpg`
- Producer: `build_contact_sheet()`.
- Consumer: the human at the interactive prompt only.
- Lifecycle: lives at project root (`PROJECT_ROOT / "order-preview.jpg"`, NOT inside `input/`), overwritten every run (`-y` flag), so it carries no run identity and should never be treated as a durable per-run artifact by a Controller.
- Schema: a JPEG, N images tall `PREVIEW_IMAGE_HEIGHT=500`px each, stacked horizontally, each labeled `1, 2, 3...` in the top-left with a black-boxed white number.
- Not part of the `run_video_generation()` path at all — irrelevant to headless Controller operation.

### 4. Moved images in `processed-inputs/<stem>/`
- Producer: `move_images_to_processed()`.
- Consumer: human audit trail; potentially a Controller reconciling which images produced which video.
- Lifecycle: created once per successful (or partially successful — see PART 6 failure mode) run, named after the video's filename stem, never renamed or cleaned up automatically.
- Schema: just the original image files, original filenames preserved, no manifest of its own inside the folder (all metadata about this batch lives in the sibling video's JSON manifest, not in the folder itself).
- Public contract: informal — a Controller should correlate `processed-inputs/<stem>/` with `output/<stem>.mp4`/`<stem>.json` by filename stem, but must be defensive: `processed-inputs/listing-video-005/` currently exists on disk with **no matching `output/listing-video-005.*` anywhere** (not in `output/`, not in `archived-outputs/`, which is empty) — see PART 14 for analysis. A Controller must not assume every `processed-inputs/` subfolder has a corresponding surviving video.

## PART 8 — CONFIGURATION SYSTEM

There is no config file, no environment variable, no CLI flag parsing (`sys.argv` is never read), and no `.env`/YAML/JSON config anywhere in the module. Confirmed: the only uses of `sys` are `sys.exit` (line 1563) and importing itself (line 56) — nothing reads `sys.argv`.

**Constants-as-config pattern**: every tunable value is a module-level constant, grouped by concern with heavy inline commentary explaining tuning history/rationale:
- Path constants (lines 79-91): `PROJECT_ROOT`, `INPUT_DIR`, `OUTPUT_DIR`, `ARCHIVED_OUTPUTS_DIR`, `PROCESSED_INPUTS_DIR`, `PREVIEW_PATH` — all derived from `Path(__file__).resolve().parent.parent`, i.e. always relative to where `generate_video.py` itself lives, NOT the current working directory.
- Video spec constants: `VIDEO_SIZE=1080`, `FRAMES_PER_SECOND=30`, `ALLOWED_EXTENSIONS`, `MIN_IMAGES=3`, `MAX_IMAGES=5`.
- Per-preset pacing/motion constant blocks, each explicitly documented as independent of every other preset's block (repeated pattern throughout: "computed independently... so [other preset]'s pacing is never affected").
- `PRESETS` itself is a constant list acting as a registry/config table (see PART 10).

**How a Controller must work around the total absence of runtime configurability**: Since none of `VIDEO_SIZE`, `FRAMES_PER_SECOND`, `MIN_IMAGES`/`MAX_IMAGES`, per-preset pacing, or preset selection logic is parameterizable at call time (beyond choosing which `preset_key` to pass), a Controller that needs different values must either (a) accept the current fixed values as-is (1080×1080, 30fps, 3-5 images, exactly these four presets), or (b) fork/monkeypatch the module's constants before import (fragile, and explicitly against the spirit of the "stable" freeze), or (c) request a code change upstream. There is no supported extension point for per-job configuration — `run_video_generation()`'s only per-call knobs are `images`, `preset_key`, `output_file`.

## PART 9 — PUBLIC API SURFACE

Treating this repo as a library, an external caller should rely on exactly:

- **`run_video_generation(images, preset_key, output_file) -> Path`** — the one stable, reusable function. Explicitly described in README (lines 20-22, 98-103) and ROADMAP (lines 16-19) as the Controller integration point.
- **`VideoGenerationError`** (line 62) — the one stable exception type; every business-logic failure path raises this instead of calling `sys.exit()`.
- **The manifest's tested field set** — `success`, `timestamp`, `preset_key`, `preset_display_name`, `output_video`, `image_order`, `image_count` (locked in by `tests/test_controller_readiness.py`).
- **`PRESETS` keys as a stable enum** — currently exactly `"standard"` (default), `"slow-fade-color-variation"`, `"wisp-sweep-color-variation"`, `"design-reveal"`. Tests assert `len(gv.PRESETS) == 4` and each key's presence/uniqueness, so these four strings are effectively a contract, though nothing prevents future keys being appended (per the `Preset` NamedTuple docstring, line 1363-1367, this is the designed extension mechanism).
- **The output filename convention** — `listing-video-{NNN:03d}.mp4` (matched by `NUMBERED_VIDEO_PATTERN`), with its manifest at `metadata/<stem>.json` via **`manifest_path_for(output_file)`** — call that rather than reconstructing the path, which is exactly what the Controller's `_launch_worker.py` does.

**Everything else is internal**, explicitly including:
- `main()` — interactive shell, blocks on stdin, must never be invoked programmatically.
- Every `ask_*`/`input()`-based function.
- All four `build_ffmpeg_command_*` FFmpeg filter-graph builders, plus their helper builders (`compute_wisp_sweep_geometry`, `build_wisp_sweep_alpha_expression`, `build_design_reveal_zoom_expression`, `_design_reveal_hold_ease_hold_expression`, etc.) — a Controller should select behavior only via `preset_key`, never by importing or reasoning about these directly.
- `find_input_images()`, `sort_by_best_drop_order_guess()`, `build_contact_sheet()` — part of the interactive discovery/ordering flow a Controller is expected to have already replaced with its own logic before calling `run_video_generation()`.
- `get_next_output_file()` / `find_highest_video_number()` — usable but carry a real TOCTOU race (PART 14); not guaranteed safe under concurrent Controller-driven runs.

## PART 10 — CODE ORGANIZATION

**"Constants + pure functions + thin orchestrator" pattern**: The file is laid out top-to-bottom as (1) a large constants section (lines 70-338) — every tunable number named and commented with its tuning history, (2) a long sequence of small, mostly-pure functions each doing one job (file discovery, ordering, font lookup, contact sheet, numbering, pacing math, filter-graph string building, manifest building), and (3) two orchestrators at the very bottom — `run_video_generation()` (the reusable, non-interactive orchestrator) and `main()` (the interactive shell that layers human prompts on top of the same pieces). No class hierarchy, no dependency injection framework — every function takes plain values (`Path`, `list`, `str`, `int`, `float`) and returns plain values or raises `VideoGenerationError`.

**The preset-registry pattern**: `Preset` (NamedTuple, line 1342) binds `key` (a stable machine id), `display_name` (human label), `builder` (a `Callable[[list, Path], list]` — every builder function shares this exact signature by convention), and `is_default`. `PRESETS` (line 1368) is a flat list of `Preset` instances — "the single source of truth for what presets exist" (comment lines 1363-1367). Both `ask_for_preset()` (menu display + selection) and `run_video_generation()` (lookup + dispatch via `next(p for p in PRESETS if p.key == preset_key)`, line 1473) are written generically against this list — neither function contains a single hardcoded preset name or per-preset `if/elif`. Adding a fifth preset requires only (a) writing a new `build_ffmpeg_command_*` function elsewhere in the file and (b) appending one more `Preset(...)` entry — no other code changes, confirmed by both the Wisp Sweep design spec and the Design Reveal implementation plan following exactly this recipe, and by tests asserting `len(gv.PRESETS)` grows by exactly one and the new key/builder pair registers correctly with no regression to existing presets' behavior.

**The `main()` / `run_video_generation()` separation** (the "V3 refactor" commit `ac5b30c`, "freeze Controller-readiness architecture for V3 Stable"): Before this refactor (see `archived-backups/` snapshots), the equivalent logic presumably called `sys.exit()` directly from deep inside business logic and had no manifest. The V3 refactor's explicit goal (README lines 23-30, ROADMAP lines 16-19) was: (1) make every business-logic failure raise `VideoGenerationError` instead of terminating the process, so an importer can catch it; (2) extract the entire "given a confirmed order and chosen preset, produce a video" sequence into `run_video_generation()`, callable with no `input()` in its call graph; (3) leave `main()` as strictly the interactive shell — gathering images, contact sheet, confirmation, preset choice — that then hands off to the exact same `run_video_generation()` a future Controller would call. `main()`'s own `try/except VideoGenerationError: sys.exit(str(exc))` (lines 1521-1563) is the only place in the module where the "old" sys.exit-on-error CLI behavior survives, preserving backward compatibility for the human workflow while making the core logic embeddable.

## PART 11 — SEQUENCE DIAGRAM(S)

**(a) Interactive CLI flow, both human gates:**
```
User          main()                      FFmpeg (subprocess)      Filesystem
 │              │                               │                     │
 │  run script  │                               │                     │
 ├─────────────►│ check_ffmpeg_is_available()   │                     │
 │              │ find_input_images() ─────────────────────────────► read input/
 │              │ sort_by_best_drop_order_guess()                     │
 │              │ build_contact_sheet() ───────►render order-preview.jpg
 │              │                               │                     ├─► order-preview.jpg written
 │◄─────────────┤ "Is this order correct? (Y/N)"│                     │
 │  [GATE 1]    │                               │                     │
 ├─Y/N─────────►│ (if N) "type correct order:"  │                     │
 │◄─────────────┤                               │                     │
 ├─"3,1,2"─────►│                               │                     │
 │              │ get_next_output_file() ─────────────────────────► read output/, archived-outputs/
 │◄─────────────┤ preset menu                   │                     │
 │  [GATE 2]    │                               │                     │
 ├─number/Enter►│ run_video_generation(order, preset_key, output_file)│
 │              │   builder(images, output_file)│                     │
 │              │   subprocess.run(ffmpeg) ─────►render video          │
 │              │                               │                     ├─► output/listing-video-NNN.mp4
 │              │   output_file.exists()? ────────────────────────► check
 │              │   move_images_to_processed() ─────────────────────► move files
 │              │                                                     ├─► processed-inputs/<stem>/*
 │              │   write_generation_manifest() ────────────────────► write JSON
 │              │                                                     ├─► metadata/listing-video-NNN.json
 │◄─────────────┤ "Success! Video saved to: ..."                     │
```

**(b) Controller calling `run_video_generation()` directly:**
```
Controller                          run_video_generation()          FFmpeg / Filesystem
    │                                        │                              │
    │ (already has: validated 3-5 images,    │                              │
    │  confirmed order as list[Path],        │                              │
    │  chosen preset_key,                    │                              │
    │  computed output_file — e.g. via       │                              │
    │  gv.get_next_output_file() or its own  │                              │
    │  numbering logic, and has separately   │                              │
    │  confirmed ffmpeg is on PATH)          │                              │
    │                                        │                              │
    ├─ call run_video_generation(images,     │                              │
    │     preset_key, output_file) ─────────►│                              │
    │                                        │ next(p in PRESETS if key)    │
    │                                        │   ⚠ raises StopIteration     │
    │                                        │     (NOT VideoGenerationError)│
    │                                        │     if preset_key is invalid │
    │                                        │ chosen_preset.builder(...)   │
    │                                        │ subprocess.run(ffmpeg cmd)──►│ render
    │                                        │◄──────────────────────────── returncode
    │                                        │ if rc != 0:                  │
    │                                        │   raise VideoGenerationError │
    │                                        │     (embeds ffmpeg stderr)   │
    │                                        │ if not output_file.exists(): │
    │                                        │   raise VideoGenerationError │
    │                                        │ move_images_to_processed()   │
    │                                        │   ⚠ may raise                │
    │                                        │     VideoGenerationError     │
    │                                        │     (name clash) — AFTER the │
    │                                        │     video already exists     │
    │                                        │ write_generation_manifest()  │
    │◄─── return output_file ────────────────│  (unguarded I/O — file/perm  │
    │       OR                               │   errors are NOT wrapped in │
    │◄─── VideoGenerationError raised ───────│   VideoGenerationError)      │
    │                                        │                              │
    │ Controller must: catch                 │                              │
    │  VideoGenerationError AND StopIteration;│                             │
    │  on catch, inspect filesystem to        │                             │
    │  determine which sub-state was reached  │                             │
    │  (video exists? manifest exists?)       │                             │
```

## PART 12 — CLASS AND SERVICE OVERVIEW

Only **two** class-like constructs exist in this file, plus one more used only for Wisp Sweep internals:

- **`VideoGenerationError(Exception)`** (line 62) — the one custom exception class in the entire codebase. No subclasses, no error codes/fields, just a plain `Exception` carrying a human-readable message string. It is the sole mechanism for signaling business-logic failure to a caller (module docstring lines 62-68 explicitly frames this as existing FOR a future Controller).
- **`Preset(NamedTuple)`** (line 1342) — the one architecturally significant data structure, described fully in PART 10.
- **`WispSweepGeometry(NamedTuple)`** (line 769) — a small internal value object (6 floats: `cos_a`, `sin_a`, `proj_mid`, `core_half_width`, `glow_half_width`, `actual_span`) used only inside the Wisp Sweep builder's own helper functions. Not exposed as part of any public contract; purely an implementation detail for one preset's filter-graph math.

There are **no other classes, no services, no ORM models, no dataclasses beyond the two `NamedTuple`s above**. This is a pure function-based script — stating this explicitly matters for a Controller design: there is nothing to instantiate, no session/state object to hold, no service client to configure. Every capability is a plain function call against module-level constants.

## PART 13 — CONTROLLER TOUCHPOINTS

**Launch**: call `run_video_generation(images, preset_key, output_file)` directly, after the Controller has independently: verified FFmpeg availability (mirroring `check_ffmpeg_is_available()`, or just calling that function), obtained/validated 3-5 image `Path`s in a confirmed order, chosen a valid key from `PRESETS`, and computed a non-colliding `output_file` path (via `get_next_output_file()` or equivalent numbering logic run at the last responsible moment — see PART 14 for the TOCTOU risk this doesn't fully close).

**Monitor**: the function's return value (`Path` to the video on success) or the exception it raises. Concretely the Controller must catch **both** `VideoGenerationError` (documented, business-logic failures) **and** `StopIteration` (undocumented — arises if `preset_key` doesn't match any `PRESETS[i].key`, from the unguarded `next()` at line 1473). No other Controller-facing signal exists (no progress callback, no streaming status, no partial-completion object) — the call is fully synchronous and opaque until it returns or raises.

**Consume**: the JSON manifest, but defensively — treat the field set as additive-only (new keys may appear, per its explicit "not versioned" design), and do not assume `output_video`'s value is a full path (it's `output_file.name` only — the Controller must know/reconstruct the containing directory itself). Do not assume every `processed-inputs/<stem>/` folder has a live manifest/video (orphan case exists — PART 14).

**Never access**: `main()`, and any `input()`-based function (`ask_yes_no`, `ask_for_corrected_order`, `ask_for_preset`) — all three block indefinitely on stdin and have no timeout or non-interactive mode. Also avoid depending on `build_contact_sheet()`/`find_font_file()` for anything Controller-critical — that path is Windows-font-dependent and produces a single overwritten, non-run-identified JPEG with no durable value to a Controller.

**Remain encapsulated forever**: the FFmpeg filter-graph internals — all four `build_ffmpeg_command_*` functions and their sub-builders (`compute_wisp_sweep_geometry`, `build_wisp_sweep_alpha_expression`, `build_wisp_sweep_input_args`, `build_design_reveal_zoom_expression`, `build_design_reveal_focal_expression`, `_design_reveal_hold_ease_hold_expression`). These are dense, tightly-coupled to specific FFmpeg filter syntax and version behavior (e.g. the documented `zoompan`'s implicit `fps=25` default gotcha, the `overlay` filter's `eof_action=repeat` reliance), and are explicitly designed to be swappable/extensible only through the `PRESETS` registry — a Controller reasoning about "what will this preset visually do" should treat `preset_key` as an opaque selector, not attempt to interpret or reconstruct the filter graph.

## PART 14 — HIDDEN ASSUMPTIONS

1. **`run_video_generation()` does NOT re-validate the caller's inputs.** It never calls `find_input_images()`, never re-checks image count is within `MIN_IMAGES`/`MAX_IMAGES`, never re-verifies each `Path` in `images` actually exists or has an allowed extension. It trusts the caller completely — the only validation happening inside the function itself is the FFmpeg-return-code check and the post-render `output_file.exists()` check (lines 1481-1496). A Controller that passes 2 images, or 8 images, or a mix of `.gif`/`.webp` files, will get whatever FFmpeg does with that input — likely a cryptic FFmpeg stderr wrapped in `VideoGenerationError`, not a clean "invalid input" message. This is a deliberate design ("images: the confirmed image order... this uses exactly the order it's given", docstring lines 1459-1461) but a Controller author must not assume any implicit re-validation safety net exists.

2. **The preset-key lookup is unguarded.** `next(preset for preset in PRESETS if preset.key == preset_key)` (line 1473) raises Python's `StopIteration`, not `VideoGenerationError`, if `preset_key` doesn't match any registered key. This is an inconsistency with the rest of the module's exception discipline and is NOT covered by any test in `test_controller_readiness.py` — a Controller must defensively validate `preset_key in {p.key for p in gv.PRESETS}` before calling, or wrap the call to catch `StopIteration` as well as `VideoGenerationError`.

3. **`-y` means unconditional silent overwrite.** Every builder's FFmpeg command includes `-y` (e.g. line 1028, "overwrite the output file if it exists"). Combined with hidden-assumption #4 below, this means a race between two concurrent generation calls that land on the same computed filename will result in one silently overwriting the other's video with no warning, no error, no manifest conflict detection.

4. **Numbering is never reserved — pure TOCTOU race.** `get_next_output_file()` (line 584) recomputes the next number fresh every call by scanning `output/` and `archived-outputs/` for existing files — there is no lock, no ticket/reservation file, no atomic "claim number N" step. Nothing marks a number as "in use" until the video file actually lands on disk at the very end of the FFmpeg render. Two concurrent Controller-driven runs (or a single run invoked twice in quick succession before the first's video is written) can both compute the same "next" number and collide, with the second's `-y` silently clobbering the first's in-progress or completed output. A Controller MUST serialize its own calls to `run_video_generation()` (or apply its own external locking/numbering scheme) — the module provides no protection against this itself.

5. **Timestamps are timezone-naive.** `datetime.now().isoformat()` (line 1428) uses local system time with no UTC conversion and no timezone offset suffix — confirmed by the actual manifest content: `"2026-07-20T22:34:45.354759"` has no `+00:00`/`Z` suffix. A Controller aggregating manifests across machines/timezones cannot compare or sort these timestamps reliably without knowing each generating machine's local timezone out-of-band.

6. **The `processed-inputs/listing-video-005/` orphan is live evidence of the failure mode in PART 6.** Confirmed on disk: `processed-inputs/listing-video-005/` exists (containing `Bookstore Back Yin Yang Design.png`, `Hanging.png`, `Koin Yin Yang Flat.png`, moved Jul 18 20:41-20:43) but there is no `output/listing-video-005.mp4`, no `listing-video-005.json` in `metadata/`, and `archived-outputs/` (the only other place a video could legitimately have gone) is completely empty. Since `move_images_to_processed()` is only ever called AFTER `output_file.exists()` is confirmed true (line 1491, then line 1503), video 005 must have existed at some point — its current total absence from both folders it could legitimately live in means it was manually deleted outside the script's control sometime after generation. This proves in practice (not just in theory) that the artifact set (`processed-inputs/<stem>/`, `output/<stem>.mp4`, `metadata/<stem>.json`) is NOT guaranteed to stay consistent over the artifacts' lifetime — a Controller reconciling these three locations must tolerate any one of them being missing for a given stem and must not treat "images were moved" as proof "the video still exists."

7. **`listing-video-001.mp4`/`002.mp4` predate the manifest feature and have no `.json` sibling at all** (confirmed on disk — only 003 and 004 have manifests). This is consistent with the V3 freeze commit `ac5b30c` having added `write_generation_manifest()`/`build_generation_manifest()` as new functionality on top of already-existing videos. A Controller scanning `output/` for manifests must handle numbered videos with no manifest gracefully (treat as "generated by a pre-Controller-readiness run" rather than as an error condition).

8. **`write_generation_manifest()`'s file I/O is unguarded.** Unlike almost every other failure point in the module, `open(manifest_path, "w")` / `json.dump(...)` (lines 1444-1445) is not wrapped in a try/except that converts filesystem errors (permissions, disk full, path-too-long on Windows) into `VideoGenerationError`. If this raises, it will be a raw `OSError`/`PermissionError` propagating out of `run_video_generation()` — meaning by this point the video AND the moved images are already durable, but the function's public contract (raises only `VideoGenerationError`, per its own docstring line 1466-1471) is silently violated. A Controller's exception handling must be prepared for arbitrary uncaught exceptions from this one call, not just `VideoGenerationError`.

9. **`PROJECT_ROOT`/`INPUT_DIR`/etc. are resolved relative to the SCRIPT's location, not the process's current working directory** (`Path(__file__).resolve().parent.parent`, line 79) — deliberately so the script "works correctly no matter what folder you run it from" (comment lines 75-78). A Controller that imports this module from a different working directory, or that expects to redirect `input`/`output` to per-job directories via an argument, cannot do so — the paths are hardcoded constants relative to `src/generate_video.py`'s own filesystem location, with no override mechanism (confirmed: tests achieve isolation only by monkeypatching `gv.INPUT_DIR`/`gv.PROCESSED_INPUTS_DIR` directly via `mock.patch.object`, e.g. `test_controller_readiness.py` lines 171, 187 — there is no supported public API for redirecting these paths).

10. **Font lookup failure is a hard stop only for the interactive path, never for `run_video_generation()`.** Since no builder function uses `drawtext`, a machine with none of the four `CANDIDATE_WINDOWS_FONTS` present can still successfully call `run_video_generation()` end-to-end — but `main()` would fail earlier at `build_contact_sheet()`. This means a Controller-only deployment (e.g. a Linux container, or a stripped-down Windows box without Office/those specific fonts) is viable for the reusable core even though the interactive CLI would not run there at all — worth knowing if a Controller is being containerized separately from human-facing tooling.

agentId: a888920453ed0078e (use SendMessage with to: 'a888920453ed0078e', summary: '<5-10 word recap>' to continue this agent)
<usage>subagent_tokens: 151336
tool_uses: 15
duration_ms: 280836</usage>
---

# PART 15 — ARCHITECTURAL GLOSSARY (cross-repository)

Terms are defined per how they are actually used in the code, with per-repo variants noted where the same word means different things in different repositories. This is the single place to resolve vocabulary confusion when reading across all three chapters above.

**Approved / Approval** — The outcome of a human review decision. In `Etsy-AI-Image-Generator`, "approved" is a formal `status`/`review_status` value (`concept.status == "approved"`, `generation_metadata.review_status == "approved"`) that gates whether an asset can appear in `approved_media_handoff.json`. In `etsy-mockup-generator`, "approval" refers only to the single preview-approval gate (`preview_approval_status: "approved"` in the manifest) — it does not gate individual assets, only whether the chosen background/framing combination proceeds to full-batch generation. `etsy-video-generator` has no "approval" concept at all; its closest analog is "confirmation" (see **Confirmation**).

**Artifact** — Any file written by one of these repos that is meant to be read by something else (a human, another stage, or eventually a Controller). Distinguished throughout each chapter from **scratch** (ephemeral, safe to ignore/delete) and **internal debug output** (stable in shape but not part of any public contract).

**Asset** — A finished, listing-usable image or video file. In `etsy-mockup-generator`, an asset is one composited PNG in `output/run-<id>/assets/`. In `Etsy-AI-Image-Generator`, an asset is one generated image that has passed Image Review into `outputs/approved/`. In `etsy-video-generator`, the asset is the single output MP4 per run — this repo has no concept of multiple assets per run.

**Contact sheet / Preview** — A human-review-only rendering, never a production artifact. `etsy-mockup-generator`'s `00-preview-contact-sheet.png` and `etsy-video-generator`'s `order-preview.jpg` both serve this exact same purpose (let a human visually confirm something before committing) despite different names, different content, and no code-level relationship to each other.

**Controller** — The not-yet-built external orchestrator this entire document exists to inform. All three repos refer to it, by name or by clear implication, as a future consumer they are trying not to break: `Etsy-AI-Image-Generator`'s `job_manifest.py` module docstring explicitly names a "FUTURE CONTROLLER CONTRACT"; `etsy-video-generator`'s commit history names a "Controller-readiness" refactor; `etsy-mockup-generator`'s README describes a "Controller Review and Selection" stage in its intended pipeline. None of the three repos has ever been called by an actual Controller — every reference to one is forward-looking.

**Concept** (Etsy-AI-Image-Generator only) — A structured, textual description of one possible image to generate: environment, camera angle, mood, garment presentation, etc. A concept is not an image; it is the plan for one. Concepts move through `status` values `proposed` → `approved`/`rejected` before a prompt (and eventually an image) is built from an approved one. See **Shot Plan**, **Prompt**.

**Confirmation** (etsy-video-generator) — The human-in-the-loop act of validating the auto-detected image order, or correcting it. Functionally the same role as "Approval" in the other two repos, but implemented as a single Y/N + optional free-text correction rather than a multi-way menu.

**Design ID** — A free-text, optional, never-validated string present in `etsy-mockup-generator`'s manifest (`design_id`) explicitly described in the repo's own code as existing "only so that knowledge isn't lost once the ZIP is archived… until a future Controller owns linking runs to designs." `etsy-video-generator`'s manifest docstring anticipates an analogous field (`design_id`) that does not yet exist in its actual schema. Neither repo currently uses this field for anything functional — it is a forward-compatibility placeholder in both places, not a working cross-repo identifier today.

**Engine** — Used throughout this document (and implied by the user's own stated philosophy) to mean one of these three repositories, considered as a self-contained, black-box unit of execution that owns its own logic, provider selection, and validation — as opposed to the Controller, which only orchestrates.

**Framing** (etsy-mockup-generator only) — The computed scale/x-offset/y-offset placement of a mockup image onto its background, specific to `human_model_front` category images. Framing is produced by a tiered fallback chain (baseline geometry → pose-corrected "Hybrid V1" → optional face-position correction) and recorded per-asset via the `framing_method` field. Not to be confused with "Framing" in a general software-architecture sense — in this repo it always means this specific image-placement computation.

**Generation** — Overloaded across repos. In `Etsy-AI-Image-Generator`, "Generate Concepts" and "Generate Images" are two distinct pipeline stages; "generation_status" tracks an individual image's lifecycle (`not_generated`/`generating`/`generated`/`failed`). In `etsy-mockup-generator`, "generation" refers to the compositing step that produces one output PNG. In `etsy-video-generator`, "generation" (as in `run_video_generation()`) refers to the entire FFmpeg-invoking process that produces one video.

**Handoff** (Etsy-AI-Image-Generator) — Short for `approved_media_handoff.json`, the single artifact explicitly designed to be the hand-off point to a future Controller. This is the only one of the three repos with a file literally named for this purpose.

**Job** (Etsy-AI-Image-Generator only) — The unit of work: one product being taken through the full concept→prompt→image→review pipeline. A job is a folder (`jobs/<job_name>/`), never a database row, never an in-memory-only object. The other two repos have no equivalent persistent unit-of-work concept — their unit of work is implicitly "one process invocation" (a **Run**).

**Manifest** — The single most overloaded and most important term across all three repos. Each repo has independently converged on the idea of one JSON file summarizing what happened, but the three manifests are structurally, semantically, and lexically different (see Part 2 of the prior readiness report for the full inconsistency analysis; this glossary entry exists to flag the term itself as a false-cognate trap). `etsy-mockup-generator`'s `manifest.json` is per-run, written twice, describes composited assets. `Etsy-AI-Image-Generator`'s `job_manifest.json` is per-job, fully rebuilt on every call (never trusted as a persisted source of truth in itself), describes pipeline stage completion. `etsy-video-generator`'s `listing-video-NNN.json` (never actually called a "manifest" in its own field names, only in code/docs prose) is per-run, success-only, describes what video was produced from what images.

**Pipeline** — The ordered sequence of stages a unit of work passes through. Only `Etsy-AI-Image-Generator` formalizes this with a named `NEXT_STEP_ORDER` list and per-stage boolean flags (`pipeline_status`). The other two repos have an implicit, code-only pipeline (the literal order of statements in their one orchestrating function) with no data structure representing it.

**Preset** (etsy-video-generator only) — A named, registered rendering style (`"standard"`, `"slow-fade-color-variation"`, `"wisp-sweep-color-variation"`, `"design-reveal"`), each bound to its own FFmpeg-command-building function via the `Preset` NamedTuple registry. Not used by the other two repos, though `etsy-mockup-generator`'s `PRESETS` dict (placement scale/offset per category) is a different, unrelated use of the same English word — do not conflate the two.

**Prompt** (Etsy-AI-Image-Generator only) — The compiled, character-budget-managed text actually submitted to an image-generation provider, built from an approved Concept plus the layered System Prompt. Distinguished from a Concept (the plan) and from the System Prompt (the brand/store/campaign voice layer that wraps every prompt).

**Provider** (Etsy-AI-Image-Generator only) — A pluggable backend for either concept generation (`ConceptGenerationProvider`: `claude_code_manual`, `claude_api`) or image generation (`ImageProvider`: OpenAI live, Gemini inert placeholder, manual-ChatGPT descriptor). The other two repos have no provider abstraction — `etsy-mockup-generator` has exactly one compositing method, and `etsy-video-generator` has exactly one renderer (FFmpeg), invoked directly with no abstraction layer.

**Review Gate** — Any point in a workflow where a human decision is required before the process continues. Present in all three repos in different forms and different strengths: `Etsy-AI-Image-Generator` has three (Concept Review, "Review Prompts" — not yet a real gate, Image Review — the only one with no disable toggle); `etsy-mockup-generator` has one true gate (preview approval) plus one optional metadata prompt (design ID) that is not really a gate; `etsy-video-generator` has two (order confirmation, preset selection), both living only in its interactive CLI shell and bypassable entirely by calling its reusable function directly.

**Run** — The unit of work in `etsy-mockup-generator` (`run-<date>-<seq>`) and `etsy-video-generator` (`listing-video-<NNN>`, though the word "run" is used only in prose/comments, not in the filename itself). A run corresponds to exactly one process invocation producing exactly one manifest (or, in video-generator's case, one video). Not used as a formal noun in `Etsy-AI-Image-Generator`, whose equivalent concept is the **Job** — except inside `job_reset.py`, where "run" (`archive/run_NNN/`) refers narrowly to one reset event, a different and smaller unit than a mockup-generator or video-generator "run."

**Schema Version** — A field intended to let a reader know which shape of a JSON artifact they're looking at. Present and meaningfully exercised only in `Etsy-AI-Image-Generator` (`schema_version: "1.0"` on `generation_config.json`, `approved_media_handoff.json`, etc.). Present but effectively broken in `etsy-mockup-generator` (`manifest_version: 1`, never incremented despite a real schema change already having happened). Absent entirely from `etsy-video-generator`'s manifest (a deliberate design choice per its own docstring, favoring a flat, additive-only shape over versioning).

**Shot Plan** (Etsy-AI-Image-Generator only) — A pre-generation assignment of Marketing Purpose and Shot Role to each concept slot, computed before any concept text is written and treated as authoritative (never overwritten by what a generation provider returns). Internal guidance only — not part of any Controller-facing artifact.

**State Machine** — None of the three repos implements a literal state-machine class or library. All three are described as state machines in this handbook (Part 6 of each chapter) as a reconstruction from control flow and on-disk artifact presence, not because the code itself models state that way. `Etsy-AI-Image-Generator` comes closest to a real state model via its `pipeline_status` boolean-flag dict, which is recomputed fresh every time rather than stored and mutated.

**System Prompt** (Etsy-AI-Image-Generator only) — The layered (Base → Store → optional Human Philosophy → Campaign) text that establishes brand voice/rules for every image-generation request, combined and versioned by `system_prompt.py`. Has no equivalent in the other two repos.

---

# PART 16 — FINAL KNOWLEDGE TRANSFER

*Written as if the original developer is leaving permanently and this is the one document a new engineer gets to read before being handed ownership of the "design an Automation Controller" problem.*

## The one-paragraph version

You have three independent, unrelated-at-the-code-level Python tools that each turn some input material into a finished Etsy asset: `etsy-mockup-generator` composites garment photography onto backgrounds, `Etsy-AI-Image-Generator` plans and generates AI/lifestyle product imagery through a multi-stage reviewed pipeline, and `etsy-video-generator` assembles 3–5 finished stills into one polished listing video via FFmpeg. None of the three has ever been called by anything other than a human at a terminal. All three were clearly built with a future orchestrator in mind — you can see it in the code comments, the manifest designs, and even the git commit messages — but "built with it in mind" is not the same as "ready for it today." Your job, if you're picking this up, is not to redesign any of the three; it's to build the fourth thing (the Controller) that sits on top of them, using exactly the seams they've already prepared, and to either work around or (with the original engineer's blessing) patch the seams they haven't finished preparing.

## What to actually go read, in order, if you have one hour

1. `etsy-video-generator/src/generate_video.py`'s `run_video_generation()` function (around line 1449) and `tests/test_controller_readiness.py`. This is the cleanest example in the whole workspace of "here is exactly what a Controller-ready function looks like" — a typed exception, no interactive dependency, a tested contract. Use it as your mental template for what you're eventually trying to get the other two repos to look like, even though neither is there yet.
2. `Etsy-AI-Image-Generator/src/job_manifest.py`'s module docstring, then `outputs/approved_media_handoff.json`'s shape (documented fully in Chapter B, Part 7 above). This is the richest artifact design in the workspace and the best evidence that "stable interfaces over shared internals" is a philosophy this codebase already understands, not one you're introducing.
3. `etsy-mockup-generator/README.md`'s "Future architectural direction" section and its manifest schema (Chapter A, Part 7). This is the weakest link of the three, both because its manifest has already silently drifted once with no version bump, and because there is no reusable non-interactive function anywhere in the file the way `run_video_generation()` exists for video.

## The shape of the problem, restated precisely

Every one of these three repos already answers "what happened" with a file. None of them can currently be *told to start* by anything other than a human typing at a terminal, except `etsy-video-generator`, which has exactly one clean, tested, importable entry point that skips the terminal entirely. This asymmetry — good completion signals everywhere, good launch signals in exactly one place — is the single most important fact in this whole handbook. It means your Controller's design cannot be symmetric across the three repos on day one. For video, you can genuinely launch-and-control. For the other two, you can genuinely discover-and-report (poll their manifests, know their state, surface it to a human) but not genuinely launch-and-control without either scripting fragile stdin automation against their existing CLIs, or asking the original engineer to add a real headless entry point to each — which, per the user's own architectural philosophy, should be a scoped, additive change to those repos (a new function, not a rewrite), not something the Controller reaches around by importing internals.

## What "the engine owns its own logic" actually looks like in practice, per repo

In `Etsy-AI-Image-Generator`, this principle is *already* implemented, not aspirational: the provider registry means the Controller never needs to know whether a concept came from Claude or an image from OpenAI — it reads `approved_media_handoff.json` and gets `provider`/`model` as metadata, not as something it had to orchestrate itself. In `etsy-video-generator`, the principle is implemented at the level of "which preset" — the Controller picks a `preset_key` string, and everything about *how* that preset actually renders (FFmpeg filter graphs, zoom curves, transition math) is permanently the video-generator's business, never the Controller's. In `etsy-mockup-generator`, the principle holds but is under-supported by tooling: the Controller genuinely should never need to know how framing math works, but today the only way to trigger a run at all is to *be* the human answering the framing-relevant approval prompt, which blurs the boundary in practice even though it's clean in intent.

## The three failure modes worth designing around from day one

**Silent overwrite / TOCTOU races.** Both `etsy-video-generator` (numbering scan with no reservation, `-y` flag on every FFmpeg call) and `etsy-mockup-generator` (single-ZIP-per-run assumption with no lock file) will misbehave, not error, if the Controller ever runs two jobs concurrently against the same repo checkout. If your Controller design involves parallelism, this is not a hypothetical edge case — it is a race you can trigger today by literally running either tool twice at once. The safest posture for a V1 Controller is to serialize all work against a given repo checkout (one job at a time per engine), and only revisit parallelism once each engine has its own locking story.

**Manifests you can only half-trust.** `etsy-mockup-generator`'s manifest schema has already drifted once, silently, with an unbumped version field — meaning "trust the schema because the version field says so" is actively false advice for this one repo. `etsy-video-generator`'s manifest is written only on success, so "no manifest" is ambiguous between "never ran" and "ran and failed" unless you also check for the presence of the output video file itself. `Etsy-AI-Image-Generator` avoids this class of problem structurally by never persisting `job_manifest.json` as trusted state — it's rebuilt fresh from the real artifacts every time it's read, which is the single best pattern in the whole workspace and worth understanding deeply if you ever advise the other two repos on how to fix their manifest situations.

**Orphaned artifacts after partial failure.** `etsy-video-generator` has a real, on-disk, currently-existing example of this (`processed-inputs/listing-video-005/` with no matching video anywhere) — proof that "images moved" does not imply "video still exists." `etsy-mockup-generator`'s two-pass manifest write means a crash between passes leaves a manifest missing the exact field (`run_succeeded`) a Controller most needs. Build your Controller's "is this job actually done" check around cross-referencing at least two independent signals (e.g., manifest content AND output-file existence AND source-material having moved) rather than trusting any single file's presence.

## What NOT to spend time on

Do not try to unify the three manifest schemas into one shape before building anything — that's a nice-to-have refactor of someone else's code, not a Controller requirement, and it isn't yours to do unilaterally per the "don't redesign" instruction governing this whole exercise. Do not try to make `etsy-mockup-generator`'s or `Etsy-AI-Image-Generator`'s interactive stages "just work" by feeding them scripted stdin — it's technically possible but brittle (tied to exact prompt wording, which none of these repos treats as a stable contract), and every one of the CLI functions involved is explicitly documented in this handbook as internal, not Controller-safe. Do not import any function marked internal in the Parts-4/Parts-9 tables above, even if it would be convenient — that's exactly the coupling the black-box-engine philosophy exists to prevent, and the whole point of this handbook is to make it unnecessary: everything you actually need from these three repos is already named, in the Public API Surface (Part 9) section of whichever chapter you're working from.

## The honest bottom line

You can build a real, useful, non-toy Automation Controller V1 today against `etsy-video-generator` alone. You can build a real, useful *read-only* Controller layer today against `Etsy-AI-Image-Generator` — discovering jobs, surfacing their status, ingesting their approved assets — without being able to make them start doing new work on their own. You cannot yet build any kind of automated launch path against `etsy-mockup-generator` without either new code in that repo or a fragile stdin-automation shim you'd be right to distrust. None of this is a surprise to the code you inherited — it already told you, in its own docstrings and commit messages, exactly which of these three problems it had solved and which it hadn't gotten to yet.

