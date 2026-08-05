# Automation Controller V1 — Architecture Inspection Report

**Scope:** `etsy-mockup-generator`, `Etsy-AI-Image-Generator`, `etsy-video-generator`
**Date:** 2026-07-21
**Method:** Read-only inspection of current on-disk code (not prior summaries, which were used only as a starting hint and independently re-verified). No files in any of the five repos in this workspace were modified to produce this report.
**Purpose:** Ground the design of a separate Automation Controller application that orchestrates these three repos as black-box engines, communicating only through their documented public artifacts (manifests, structured files, launch contracts, exit/error behavior) — never by importing their internals.

---

# PART 1 — INDIVIDUAL REPOSITORY ANALYSIS

## 1.A — `etsy-mockup-generator`

### 1. Executive Summary

**Purpose.** Takes one Printful-style garment-PNG export ZIP plus one reusable background image, composites each classified garment PNG onto that background with category-appropriate framing and shadow, and writes results to a versioned `output/run-<date>-<seq>/` folder with a `manifest.json`.

**Responsibilities.** ZIP discovery/extraction (`prepare_input.py`); pixel-based classification into `flat_front` / `back` / `human_model_front` / `unknown` (`classify_mockups.py`); background selection with an interactive human-approval gate via low-res previews (`batch_generate.py`); full-batch compositing with per-category shadow (`composite.py`, `shadow.py`); dynamic per-image framing for human-model shots using MediaPipe pose/face detection with tiered fallback (`human_framing.py`, `human_dynamic_framing_test_v2/v3/v4.py`, `hybrid_human_framing_v1.py`); manifest/report writing and ZIP archival (`batch_generate.py:main`).

**Explicit non-responsibilities.** No product-type breadth beyond clothing ("new product types should be added only when a real use case requires them, not speculatively" — `README.md:26`); no design/listing metadata beyond an optional free-text `design_id`; no Etsy upload/listing creation; `processed-outputs/` exists but is explicitly "reserved for a later workflow step and is currently unused" (`README.md:175`; `batch_generate.py:76`); no downstream asset-library/Controller/Draft-Editor logic — all explicitly "not built yet" (`README.md:223-226`).

**Architectural philosophy.** Deliberately small and hand-tunable rather than general. The `PRESETS` dict is documented as "simple starting points to visually test and adjust by hand — not an automatic placement system." Heavy emphasis on regression safety over pure automation: multiple human-in-the-loop review gates, extensive inline documentation of *why* thresholds were chosen empirically, and a dedicated `output/regression-references/` folder of hand-curated known-good outputs for manual visual diffing. Every ML-dependent code path is fallback-first: MediaPipe failures degrade to a deterministic baseline rather than raising.

**Current maturity.** README states "Mockup Generator V1 is complete and approved." In practice there is no automated test suite anywhere (no `tests/`, no `pytest.ini`, no `unittest` files) — "testing" is manual, via one-off scripts now in `archive/` and via visual comparison against `output/regression-references/`. It is real production-used (genuine `output/run-*` folders with real garment images exist) but single-operator, single-machine, filesystem-based, with no logging framework (plain `print()`), no config file, and notable backup-file hygiene debt (timestamped `.bak-*` files and full-repo `.zip` snapshots checked into `backups/`).

**Primary workflows.** (1) Normal batch run: `python batch_generate.py`, fully interactive (background pick → preview approval loop → optional design-ID prompt → full batch → manifest → ZIP archival). (2) Ad hoc single-image compositing via `composite.py`'s own `main()`, a standalone proof-of-concept harness independent of the batch pipeline. (3) Standalone classification/extraction dry-runs via `prepare_input.py`/`classify_mockups.py`'s own `main()`s.

### 2. Internal Architecture

**Major subsystems:** input acquisition (`prepare_input.py`); classification (`classify_mockups.py`); framing math, four generations all still live (`human_dynamic_framing_test_v2/v3/v4.py`, `hybrid_human_framing_v1.py`, `pose_landmark_experiment.py`, orchestrated by `human_framing.py`); compositing/shadow (`composite.py`, `shadow.py`); orchestration/CLI/manifest (`batch_generate.py`, the only module tying everything together — its own docstring says it "connects the pieces that already exist and work on their own").

**Workflow orchestration.** Entirely procedural, top-to-bottom in `batch_generate.py:main()` (lines ~510-757). No orchestration framework, no task graph, no async, no retries beyond a ZIP-move fallback. A `while True` loop implements the background-selection/preview-approval cycle.

**State management.** No persistent state file or database between runs. All "state" is the filesystem: `input/` (pending ZIP), `working/extracted/` (scratch, wiped every run), `preview/` (scratch, wiped every preview generation), `output/run-*/` (immutable once written), `processed-inputs/` (append-only ZIP archive). In-process state lives only in local variables inside `main()` — a crash mid-run loses all progress; there is no checkpoint.

**Configuration.** None externally settable. No config file, no env vars, no CLI arguments anywhere (confirmed: no `argparse`/`sys.argv` usage). Everything is a hardcoded Python module constant (`PRESETS`, `SHADOW_PRESETS`, `CANVAS_SIZE`, folder names, classification thresholds, framing tuning constants).

**Dependency structure.** `requirements.txt` lists exactly `Pillow`, `mediapipe`, unpinned. MediaPipe model weights (`models/pose_landmarker_lite.task`, `models/blaze_face_short_range.tflite`) are checked into the repo as binaries, originally fetched manually (per `.claude/settings.local.json` allow-list entries) — there is no bootstrap/download script.

**Lifecycle.** `main()` opens MediaPipe landmarker/detector once at the top and closes them in a `finally` block regardless of success/failure — the one place resource lifecycle is handled carefully. No daemon/server mode.

**Public vs internal APIs.** No declared public API surface at all — no package, no `__init__.py`, no `setup.py`/`pyproject.toml`. Every function in every module is importable by bare module name only if invoked with CWD at the repo root.

### 3. Public Interface

**Inputs:** exactly one `.zip` in `input/` per run; one or more background images in `backgrounds/`, each required to be exactly 2000×2000px; interactive stdin (three `input()` calls) — the tool's only other input channel.

**Outputs:** `output/run-<date>-<seq>/assets/*.png` (flat, category-prefixed filenames) plus `output/run-<date>-<seq>/manifest.json`. Transient: `preview/*.png` (review-only, wiped every cycle). Console text report to stdout only, no log file.

**Folder structure:**
```
input/                         – drop the one ZIP here
backgrounds/                    – reusable background library (2000x2000 required)
working/extracted/              – scratch, wiped every run
preview/                         – scratch, wiped every preview regeneration
output/run-<date>-NNN/          – one immutable folder per completed run
  assets/*.png
  manifest.json
output/regression-references/   – hand-curated, must not be casually deleted
processed-inputs/               – ZIPs moved here after a fully successful run
processed-outputs/              – reserved, currently unused
models/                          – MediaPipe model weight files
```

**Launch method.** `python batch_generate.py`, no arguments, no env vars read anywhere. **Fully interactive** — blocks on `input()` three separate times (background selection, approval menu, design-ID prompt). This is the single largest integration obstacle (see Risks).

**Exit behavior.** **No `sys.exit()` call anywhere in the repository** (confirmed by search), and no custom exception types raised/caught for top-level control flow. `main()` always falls off the end (implicit exit code 0) whether the run succeeded, was cancelled, hit a missing-ZIP/missing-background early return, or completed with per-image errors recorded only in the manifest. **A Controller cannot distinguish success from failure via process exit code.**

**Manifest format** (current schema, `batch_generate.py` ~lines 642-696):
```json
{
  "manifest_version": 1,
  "run_id": "run-2026-07-19-002",
  "timestamp": "<ISO datetime>",
  "design_id": "<string or null>",
  "zip_filename": "<basename>",
  "background_filename": "<basename>",
  "preview_approval_status": "approved",
  "preview_categories_generated": ["flat_front", "human_model_front", "back"],
  "preview_sources": {"flat_front": "<source filename>"},
  "total_files_extracted": 20,
  "category_counts": {"human_model_front": 0, "flat_front": 20, "back": 0, "unknown": 0},
  "output_counts": {"human_model_front": 0, "flat_front": 20, "back": 0},
  "assets_dir": "assets",
  "assets": [
    {"filename": "flat-front-01.png", "path": "assets/flat-front-01.png",
     "category": "flat_front", "source_filename": "<original png>",
     "framing_method": null, "background_filename": "<basename>"}
  ],
  "errors": ["<source_filename>: <exception message>"],
  "framing_method_counts": {"hybrid_v1": 6, "v2_fallback_low_confidence": 1},
  "run_succeeded": true,
  "processed_input": {
    "original_filename": "...", "attempted": true, "move_succeeded": true,
    "method": "move|copy_then_delete_fallback", "destination_path": "...",
    "run_id": "...", "timestamp": "...", "reason": null
  }
}
```
**Important:** this schema has already drifted, undetected by its own version field. On-disk manifests from earlier runs (`run-2026-07-11-003`, `run-2026-07-19-002`) lack `manifest_version` and `design_id` entirely and instead carry an obsolete `background_path` (absolute Windows path) field current code no longer writes. `manifest_version` (currently `1`) has never been bumped despite this already-happened schema change. A Controller reading historical runs must defensively handle missing/extra keys.

**Success/failure conditions.** `run_succeeded = expected_vs_generated_ok and not errors`, where `expected_vs_generated_ok` requires generated-count == classified-count per category. This boolean gates whether the ZIP is archived to `processed-inputs/` (success) or left in `input/` (failure) — the single most Controller-relevant signal in the repo, but it is file-based, not exit-code-based, and is written to the manifest in two passes (see Risks).

**Recovery/Resumability.** None. No checkpoint/resume. A crash mid-run leaves `working/extracted/` and possibly a partial `output/run-*/assets/` with no manifest; re-running re-extracts and re-classifies from scratch (idempotent because `extract_zip` wipes its target first, but the orphaned partial output folder is not cleaned up or reused).

### 4. Workflow

1. **Bootstrap** — create workflow folders if missing. Automatic, no gate.
2. **ZIP discovery** — if none found, print message and stop with **no artifacts written at all**. Automatic.
3. **Background scan** — if none found, stop, ZIP untouched, no artifacts. Automatic.
4. **MediaPipe init** — landmarker/detector built once; degrade to `None` silently on any failure. Automatic, no visible signal.
5. **Background-selection / preview-approval loop** — **human gate #1 and #2**: operator picks a background or cancels; on first pass, ZIP is extracted and every PNG classified (cached for the rest of the run); one representative image per present category is rendered through the *real* production pipeline into `preview/`; operator reviews and chooses Approve / Another background / Cancel. Cancel deletes `preview/` and exits with no output artifacts, ZIP left in `input/`.
6. **Design-ID prompt** — **human gate #3**, optional free text, not validated.
7. **Full-batch generation** — automatic: composites every classified image in every category, records per-image manifest data or an error string per image (batch continues on per-image error).
8. **Manifest write #1** — written *before* `run_succeeded`/`processed_input` are computed.
9. **Success determination + ZIP archival decision** — automatic: move on success, verified copy+delete fallback on Windows lock errors, or a failure reason recorded.
10. **Manifest write #2** — rewrites the same file with `run_succeeded` and `processed_input` added; this is the final, complete manifest.
11. **Console report** — human-readable only, no machine-readable equivalent beyond the manifest already written.

### 5. Structured Artifacts

| Artifact | Purpose | Producer | Consumer | Lifecycle | Stability |
|---|---|---|---|---|---|
| `output/run-*/manifest.json` | Authoritative run record | `batch_generate.py:main()` (written twice) | Human only today; no in-repo reader | Immutable after write; never migrated for older runs | **Ad hoc.** Confirmed schema drift; unbumped version field. |
| `output/run-*/assets/*.png` | Final listing-ready mockups | compositing pipeline | Human (manual Etsy use today) | Immutable | Filenames deterministic; but two incompatible physical layouts coexist under `output/` (old per-category-subfolder runs are never migrated). |
| `preview/*.png` | Human review only | preview generator | Human only | Wiped/regenerated every cycle | Explicitly scratch, not a contract. |
| `working/extracted/*.png` | Scratch extraction | `prepare_input.extract_zip` | same-run classification/render | Wiped every run | Pure scratch. |
| `processed-inputs/<zip>` | Archived source ZIP | `move_processed_zip` | none in-repo | Append-only | Stable behavior; linkage back to its run is only by filename convention/manifest fields. |
| `output/regression-references/**` | Hand-curated visual regression snapshots | manual/one-off scripts | human diffing only | Frozen, must not be casually deleted | Out-of-band from production manifest schema; not pipeline output. |
| `models/*.tflite`, `*.task` | MediaPipe weights | manually fetched, not by any script | `human_framing.py` | Static | Undeclared external dependency-fetch step — a fresh clone without these silently degrades every human-model image to fallback framing rather than failing loudly. |

### 6. Stable Integration Contracts

**Stable enough to depend on:** the folder-based state machine (one ZIP in `input/` → one `output/run-<date>-NNN/` per success → ZIP relocated to `processed-inputs/`); the `run_succeeded` boolean + `processed_input` block (the cleanest, most deliberately-designed Controller-facing signal in the repo, explicitly documented as reusing "the same success determination that decides whether the ZIP moves to `processed-inputs/`"); the `assets` array shape for current-code runs; the category taxonomy (`flat_front`, `back`, `human_model_front`, `unknown`).

**Implementation details, not contracts:** exact framing math/thresholds and shadow presets (already swapped baselines once, v2→v3→v4); `framing_method` string values (diagnostic labels, not a stable enum — the code itself keeps a "v2_*" label for "manifest continuity" despite the underlying math no longer being V2); the legacy per-category-subfolder output layout.

**Internal-only:** all MediaPipe usage, model paths, confidence thresholds, the entire interactive prompt flow.

**Designed for future-safe integration but not yet wired up:** `manifest_version` (intended as a schema-version field, though not yet exercised correctly); `design_id` (explicitly present "only so that knowledge isn't lost once the ZIP is archived... until a future Controller owns linking runs to designs"); the README's own "Future architectural direction" section describing an intended richer asset-metadata shape, none of which is implemented yet.

### 7. Extension Points (existing, not proposed)

- New backgrounds: drop-in, auto-discovered by `scan_backgrounds()`, no code change.
- New shadow style: `build_shadow_layer` dispatches on a `preset["style"]` tag; a new style could be added to `SHADOW_PRESETS` plus one new branch.
- New review-gate choice: `prompt_approval_menu`'s A/B/C pattern (with re-prompt-on-invalid-input) is an established, reused convention for adding another menu-driven gate.
- New framing tier: `calculate_human_model_framing` is an explicit ordered fallback chain (baseline → hybrid → face-correction), each guarded by its own validity check — a new correction layer can be inserted following the same compute→validate→fallback pattern.
- New classification category: `classify()`'s filename-check → pixel-metric-check → default-unknown shape is a template, though the README explicitly discourages adding categories speculatively.
- New preset-driven placement: `PRESETS` is keyed by category and could accept a new category's `{scale, x_offset, y_offset}` without other code changes, for categories that don't need dynamic framing.

### 8. Architectural Risks

1. **Fully interactive CLI, no headless mode.** Three `input()` calls block the process; no `--yes`/flag/env override anywhere. This is the single largest integration obstacle — a Controller cannot drive this process without a PTY-automation shim or a code fork adding a non-interactive path.
2. **No machine-readable exit code.** Confirmed zero `sys.exit()` calls anywhere. The process always returns 0 whether it succeeded, was cancelled, or found no ZIP/backgrounds. For the "no ZIP"/"no backgrounds" paths, **no artifact is written at all** — the only signal is stdout text.
3. **Manifest schema has already silently drifted, with no migration**, and the version field has never been bumped despite this.
4. **Manifest is written twice, non-atomically** (first without `run_succeeded`/`processed_input`, then rewritten with them). A crash between the two writes (e.g. during ZIP move, which does real file I/O and can hit Windows file-lock errors) could leave a manifest on disk missing the exact field a Controller depends on. Not an atomic temp-file+rename write.
5. **CWD-relative hardcoded paths everywhere**, no env-var/argument override; only works correctly if invoked with CWD at repo root. One historical manifest even embeds a machine-specific absolute Windows path.
6. **Single-ZIP-per-run assumption is unguarded** — extra ZIPs in `input/` are silently ignored with no warning and no manifest note; a Controller queuing multiple jobs against the same folder would get silently-wrong behavior, not a clear failure.
7. **Silent ML degradation with no proactive signal** — MediaPipe init failures are swallowed entirely; only visible after the fact via `framing_method_counts` (the README even tells the human operator to manually check this isn't "a sea of fallbacks").
8. **No package/module boundary** — everything is import-by-bare-module-name at repo root; there is no packaged public API for a Controller to call into even if it wanted to (which the stated philosophy rules out anyway).
9. **No automated test suite of any kind** — correctness is guaranteed only by manual visual diffing against `output/regression-references/`, which is not CI-integrable as-is.
10. **Repo hygiene debt** — numerous timestamped `.bak`/`.bak-*` files and full-repo `.zip` snapshots committed under `backups/`, suggesting ad hoc non-git versioning habits alongside real git history.
11. **No warm-pool/daemon mode** — MediaPipe model loading happens fresh every process invocation; a Controller invoking this per-job as a subprocess pays full init cost every run.

---

## 1.B — `Etsy-AI-Image-Generator`

### 1. Executive Summary

**Purpose.** Generates Etsy listing images (AI-only product mockups and human "lifestyle" mockups) for one product/job at a time: turns a Store + Campaign + product/creative-brief configuration into reviewed, approved image concepts, then structured image-generation prompts, then generated images, then a human-reviewed set of "approved" assets ready for a downstream consumer.

**Responsibilities.** Job creation/identity (`job_config.py`, `main.create_new_job`); concept planning (`concept_planner.py`) and generation (Claude Code direct-write, or live Claude API via `provider_registry.ClaudeAPIConceptProvider`); human concept review (`concept_review.py`); prompt building (`prompt_builder.py`) with a character-budget compiler (`prompt_budget.py`); image generation (`image_generator.py`, live via OpenAI, plus a genuinely-supported "manual ChatGPT" human-in-the-loop path); image review and Approved Media Handoff assembly (`image_review.py`); job-level machine-readable status (`job_manifest.py`); job reset/archival (`job_reset.py`).

**Explicit non-responsibilities.** No final visual selection/categorization/ordering across jobs — explicitly deferred to "a future Controller" (`job_manifest.py` module docstring). No mockup compositing (that's `etsy-mockup-generator`) and no video (that's `etsy-video-generator`). Does not orchestrate multiple jobs, does not schedule/queue anything. Per `docs/provider_readiness.md`, Gemini image generation is an inert placeholder with zero live capability.

**Architectural philosophy.** Heavy investment in a "provider" abstraction so concept- and image-generation backends are swappable without touching orchestration code (`provider_registry.py`, `provider_contracts.py`). Strong "never silently degrade" posture: unknown provider IDs raise (`UnknownProviderError`); missing system-prompt layers raise (`SystemPromptError`); finalization only writes concept files when *both* categories validate. Everything destructive is archived-first, never deleted (`job_reset.py`). `job_manifest.json` is explicitly treated as the primary Controller interface, documented as such in its own module docstring ("FUTURE CONTROLLER CONTRACT").

**Current maturity.** Concept generation is production-live via two providers: `claude_code_manual` (primary, in-use path — direct write by the Claude Code session itself, no API call) and `claude_api` (live when `ANTHROPIC_API_KEY` is set). Image generation is production-live via OpenAI and a genuinely-used Manual ChatGPT workflow; the *registry's* `openai`/`gemini` image-provider entries are separate, deliberately inert placeholders not yet wired to the main menu. The CLI (`main.py`) is a synchronous, single-process, `input()`-driven terminal menu — explicitly described in its own code as "a backend/development interface pending the future Controller."

**Primary workflows.** (1) Interactive human operator running `python src/main.py`, working one job at a time through a linear pipeline with optional auto-chaining past disabled review gates. (2) A Claude Code session working in this repository directly writing concept files per job — a documented, intentional non-API integration path, not a stopgap.

### 2. Internal Architecture

**Major subsystems:** job lifecycle & identity (`job_config.py`, `job_manifest.py`, `job_reset.py`, `pipeline_guards.py`); concept pipeline (`concept_planner.py` → `concept_generator.py`/`claude_concept_generation.py`/`manual_concept_generation.py` → `concept_finalization.py` → `concept_review.py`); prompt pipeline (`prompt_builder.py`, `prompt_budget.py`, `system_prompt.py`, `creative_dna.py`, `physical_schema.py`, `reference_assets.py`, `shot_planner.py`); image pipeline (`image_generator.py`, `manual_image_generation.py`, `image_review.py`); provider architecture (`provider_registry.py`, `provider_contracts.py`); config (`store_config.py` + `config/stores/<store_id>/...`); CLI shell (`main.py`).

Each stage module follows a consistent shape: a `*Error` exception class, `_load_json_object`/`_write_json` helpers, a `run_*()` entry point, and a docstring stating what it explicitly does *not* do (e.g. `prompt_builder.py`: "Builds prompts only... never calls an image API and never generates images").

**Workflow orchestration.** `main.py` is the sole orchestrator; no scheduler/queue. Every stage function is called synchronously from a menu choice or from `_continue_to_next_stage()`/`_advance_pipeline_after_concept_generation()`, both resolving "what's next" purely by reading `job_manifest.inspect_job()`'s `pipeline_status` dict against a static ordered list (`NEXT_STEP_ORDER`).

**Execution flow.** Job Created → Creative Brief → Concept Planning (auto, on job creation) → Concept Generation (Claude Code direct-write, or Claude API) → Concept Review (human gate, or auto-approve if disabled) → Prompt Build (automatic once Concept Review finishes) → Prompt Review (currently view-only, a marker file stands in for real review) → Image Generation (OpenAI API, or manual ChatGPT) → Image Review (human gate; approvals rebuild the handoff artifact) → `ready_for_controller`.

**State management.** No database. Durable state is the job's on-disk JSON tree under `jobs/<job_name>/`. `job_manifest.json` is a *derived*, rebuildable cache of that state — never a source of truth — fully recomputed by `rebuild_job_manifest()` every time it's called. In-memory process state is limited to two module globals in `main.py` (`_active_job_name`, `_active_provider_id`) that live only for the process lifetime.

**Configuration.** Per-job: `config/generation_config.json` (identity, store/campaign, concept counts, `available_print_sides`, `review_concepts`/`review_prompts` workflow flags, optionally provider/model overrides) and `config/creative_brief.json`. Global: `config/stores/**` (store/campaign prompt layers) and `config/image_generation.json` (output-target → provider size/quality/format mapping, plus `prompt_budget_chars`).

**Dependency structure.** Deliberately layered to avoid cycles: `job_manifest.py` and `provider_contracts.py` are documented pure leaf modules that never import other pipeline-stage modules, so they can be called safely from anywhere and reflect ground truth regardless of internal stage-module changes. `provider_registry.py` imports `image_generator.py`/`provider_contracts.py`; `image_generator.py` lazily imports `provider_registry` only inside `run_image_generation()` to avoid a circular import.

**Lifecycle.** A job is a folder created once and mutated in place through the pipeline; `job_reset.py` supports rewinding a job non-destructively (archive-then-swap). No job-deletion function exists — jobs persist until manually removed from disk.

**Public vs internal APIs.** No HTTP/RPC surface at all. The closest thing to a "public API" is (a) the on-disk artifact contracts and (b) the `provider_registry`/`provider_contracts` Python interfaces — explicitly designed with a future consumer in mind but not exposed over any process boundary. A Controller would need to shell out to `python src/main.py` non-interactively (not currently supported) or import this repo's modules directly (which the "black-box engine" philosophy rules out).

### 3. Public Interface

**Inputs.** Interactive stdin through `main.py` (no CLI-flag/argv interface at all — the `__main__` block just calls `main_menu()`). Job-level file inputs: `jobs/<job_name>/reference_images/*.{png,jpg,jpeg,webp,gif,bmp,tiff}`. Environment variables (resolved centrally in `provider_registry.PROVIDER_ENV_CONFIG`): `ANTHROPIC_API_KEY`, `CLAUDE_CONCEPT_API_MODEL`, `OPENAI_API_KEY`, `OPENAI_IMAGE_MODEL`, `GEMINI_API_KEY` (reserved, unused), `GEMINI_IMAGE_MODEL` (reserved, unused).

**Outputs.** The full job-folder tree under `jobs/<job_name>/`; most important for integration: `job_manifest.json` (machine status) and `outputs/approved_media_handoff.json` (final deliverable).

**Folder structure** (per job):
```
jobs/<job_name>/
  reference_images/
  config/{generation_config.json, creative_brief.json}
  outputs/
    concepts/{concept_planning_context.json, ai_product_mockup_concepts.json,
              lifestyle_mockup_concepts.json, shot_plan.json,
              claude_code_concept_instructions.txt, concept_planning_package.txt}
    prompts/{system/*.txt, ai_product_mockups/<concept_id>/{prompt_package.json,user_prompt.txt},
              lifestyle_mockups/<concept_id>/..., prompt_review_marker.json}
    generated/{ai_product_mockups|lifestyle_mockups}/<concept_id>/{generation_metadata.json,
              prompt_package_snapshot.json, system_prompt_snapshot.txt, user_prompt_snapshot.txt, image_*.png}
    approved/... , rejected/...  (mirrors generated/, populated on review decision)
    approved_media_handoff.json
    logs/  (required subfolder; not observed to be written to by any module)
  job_manifest.json
  archive/run_NNN/{outputs/, reset_metadata.json}   (written only by job_reset.py)
```

**Launch method.** `python src/main.py` — purely interactive, no argv parsing anywhere.

**Exit behavior.** No `sys.exit()` calls found anywhere in `src/` (confirmed via search). The process only terminates via the "Exit" menu choice (falls off `main()`, implicit code 0) or an uncaught exception (arbitrary non-zero code, raw traceback). **No documented exit-code taxonomy exists at all.**

**Status information.** `job_manifest.json`'s `pipeline_status` dict (18 boolean flags) is the canonical machine-readable status.

**Logs.** No structured logging module; all diagnostics are interleaved `print()` statements. `outputs/logs/` is a required folder that nothing writes to.

**Error reporting.** Consistent per-module `*Error` exception taxonomy (`ConceptPlannerError`, `PromptBuilderError`, `ImageGeneratorError`, `ImageReviewError`, `JobResetError`, `JobManifestError`, `SystemPromptError`, `ConceptGenerationProviderError`, `ImageProviderError`, `UnknownProviderError`, `ManualConceptGenerationError`, `ManualImageGenerationError`, `ClaudeConceptGenerationError`). All are caught in `main.py` and converted to human-readable `print()` output — none propagate to a machine-readable channel.

**Recovery/Resumability.** Deliberately strong: every stage function is idempotent/resumable by re-reading on-disk state (`pipeline_guards.py` precondition checks); `job_reset.py` provides staged, archived, atomic-swap rewinds to any of six stage boundaries; image generation resumes only over `not_generated` packages after a crash.

**Success conditions.** Per `job_manifest.py`'s own documented contract: `pipeline_status.ready_for_controller == true` — requires `approved_media_handoff.json` to exist, image review to be fully complete, and at least one approved asset.

**Failure conditions.** No single "job failed" flag exists. Failure is per-artifact (`generation_status == "failed"` with `error_message`) and per-stage (`JobManifestError` if the job folder vanishes). `warnings` in `job_manifest.json` surfaces cross-artifact inconsistencies without ever blocking or crashing.

### 4. Workflow

1. **Create Job** — inputs: product name, store/campaign pick, product type, reference images (optional), creative notes, concept-plan counts. Outputs: job folder skeleton, `generation_config.json`, `creative_brief.json`. No review gate.
2. **Plan Concepts** (auto-chained) — outputs `concept_planning_context.json`, `shot_plan.json`, placeholder concept files, `concept_planning_package.txt`. No gate.
3. **Generate Concepts** — three acquisition paths converging on `concept_finalization.finalize_provider_concepts` (API path) or `claude_concept_generation.check_generated_concepts` (manual/Claude-Code path): (a) Claude Code — Manual (default provider); (b) Claude API (live if key set); (c) legacy manual ChatGPT paste path, still present/functional but described as "legacy." Automatic decision: exact-count + schema + print-side-compatibility validation before any write — nothing partially finalizes.
4. **Review Concepts** (human gate, or auto-approved if `review_concepts` is `false`) — per-concept Approve/Reject/Skip/Next/Previous/Quit loop; decisions written immediately, not batched.
5. **Build Prompts** (automatic once Concept Review finishes) — for every approved concept, builds `user_prompt.txt` + `prompt_package.json`, saves the layered system prompt once per job.
6. **Review Prompts** — currently a view-only screen; "review" is recorded solely via a marker file (`prompt_review_marker.json`). No actual approve/reject/edit exists yet — explicitly documented as future work.
7. **Generate Images** — operator picks OpenAI API or Manual ChatGPT (export prompt package → paste externally → import downloaded image). Per-package eligibility filter, Prompt Budget Manager compilation, provider call, image bytes written, metadata recorded, status updated. Failure isolated per-package; batch continues.
8. **Review Images** (human gate; no disable flag exists for this one) — Approve/Reject per generated image; approving copies the image + snapshots into `outputs/approved/...` and immediately rebuilds the handoff artifact.
9. **Ready for Controller** — no dedicated stage function; purely a derived `pipeline_status` boolean once `approved_media_handoff.json` exists with ≥1 approved asset.

Cross-cutting: **Reset Job** (any point) — operator picks a target stage, confirms, `job_reset.reset_job` archives current outputs then atomically rewinds.

### 5. Structured Artifacts

| Artifact | Purpose | Producer | Consumer | Lifecycle | Stability |
|---|---|---|---|---|---|
| `config/generation_config.json` | Per-job identity, store/campaign, concept counts, print sides, workflow toggles, optional provider selection | `job_config.py` | nearly every stage module | Created once, mutated by settings screens | Stable, versioned (`schema_version: "1.0"`), additive-only field history |
| `config/creative_brief.json` | Free-text creative direction + Etsy listing rules | `creative_brief.py` | `concept_planner.py`, `prompt_builder.py` | Created once | Stable |
| `outputs/concepts/concept_planning_context.json` | Frozen snapshot for concept generation | `concept_planner.py` | providers, `job_manifest.py` | Written once, rewritten only on explicit re-plan | Stable |
| `outputs/concepts/{ai,lifestyle}_..._concepts.json` | Concept records with per-concept `status` | `concept_planner.py` → provider paths → `concept_finalization.py`/`claude_concept_generation.py` → `concept_review.py`/`job_reset.py` | `concept_review.py`, `prompt_builder.py`, `job_manifest.py` | Placeholder → generated → reviewed | Stable core schema; enum fields additive/backward-compatible by design |
| `outputs/concepts/shot_plan.json` | Marketing Purpose + Shot Role per slot | `shot_planner.py` | prompt text only (internal guidance) | Written once | Internal-only, not Controller-facing |
| `outputs/prompts/<category>/<id>/prompt_package.json` | Complete, self-contained image-generation request | `prompt_builder.py` | `image_generator.py`, `image_review.py`, `job_manifest.py` | not_generated → generating → generated/failed | Explicitly designed as a stable future-facing contract |
| `outputs/prompts/prompt_review_marker.json` | Stand-in for "operator passed through Review Prompts" | `main._mark_prompts_reviewed` | `job_manifest.py`, `main.review_prompts` | Written once | Explicit placeholder, will be replaced |
| `outputs/generated/<category>/<id>/generation_metadata.json` | Full generation record (provider, model, prompt versions, budget report, status, error) | `image_generator.py` | `image_review.py`, `job_manifest.py` | interim → final | Stable, versioned |
| `.../prompt_package_snapshot.json`, `system_prompt_snapshot.txt`, `user_prompt_snapshot.txt` | Frozen copies of exactly what was submitted | `image_generator.py` | `image_review.py` (copied forward on approval) | Written once per attempt | Stable, debug-oriented |
| `outputs/approved/` / `outputs/rejected/` | Human-reviewed copies of assets + snapshots | `image_review.py` | `job_manifest.py`, `rebuild_approved_media_handoff` | Mutually exclusive per concept; rebuilt on every decision | Stable |
| `outputs/approved_media_handoff.json` | **The final deliverable** — every currently-approved asset with full context and paths | `image_review.rebuild_approved_media_handoff` | Explicitly documented as the artifact a future Controller reads | Fully rebuilt from scratch on every approve/reject | Explicitly designed as the primary Controller-facing artifact; `schema_version: "1.0"`, `handoff_status: "ready_for_controller_review"` |
| `job_manifest.json` | The single machine-readable summary | `job_manifest.py` | Explicitly documented as the primary Controller discovery artifact | Fully recomputed every call, never trusted as durable truth itself | Explicitly designed, versioned, documented — the most stable artifact in the repo |
| `archive/run_NNN/{outputs/, reset_metadata.json}` | Pre-reset snapshot | `job_reset.py` | Human inspection only | Written once per reset | Ad hoc, no `schema_version` |

### 6. Stable Integration Contracts

**Genuinely stable today:** `job_manifest.json` (explicitly designed and documented for this exact purpose, field-stable, versioned); `outputs/approved_media_handoff.json` (explicitly the artifact a Controller should read for asset details); `provider_registry.list_concept_providers()`/`list_image_providers()`/`describe_*_provider()` (a designed, queryable metadata surface, explicitly recommended over hardcoding provider identity); `provider_contracts.py` normalized shapes (`ConceptGenerationResult.to_dict()`, `ImageGenerationResultV2.to_dict()`); `physical_schema.evaluate_print_side_compatibility()` (explicitly the one place this logic should be consulted).

**Implementation details, not yet a contract:** `prompt_package.json` (internally stable and self-contained, but an internal handoff between prompt_builder and image_generator, not clearly meant for direct Controller consumption); `generation_metadata.json`/prompt-budget report (rich, stable, but internal/debug-oriented); the CLI menu structure itself (explicitly called out in its own code as "backend/development interface pending the future Controller").

**Internal-only:** `shot_plan.json`, `claude_code_concept_instructions.txt`, `concept_planning_package.txt`; the `_active_job_name`/`_active_provider_id` module globals; the transactional `.reset_staging_*`/`.outputs_pre_reset_*` temp directories used inside `job_reset.reset_job` (cleaned up on success/failure but could theoretically survive a hard crash mid-swap).

**Designed explicitly for future-safe integration:** the entire `provider_registry.py`/`provider_contracts.py` pair — normalized request/result shapes, a stable error-category taxonomy, `correlation_id`/`retry_count` fields anticipating retry logic not yet implemented, and an explicit precedence model (per-run override > per-job config > app default) for both provider identity and model selection.

### 7. Extension Points

- **New concept providers:** implement `ConceptGenerationProvider` (`generate()`, `is_configured()`, `capabilities()`) and register in `CONCEPT_PROVIDERS`. Two exist today (`claude_code_manual`, `claude_api`).
- **New image providers:** implement `ImageProvider` (`generate_image()`, optional `validate_ready()`) and register in `IMAGE_PROVIDERS`. **Gemini exists today only as a genuine, inert placeholder class** that always raises and makes zero network calls, reachable only from a separate developer preview screen, not the primary menu. The registry's `openai` entry is likewise a separate, not-wired-up class from the real, already-working `OpenAIImageProvider` that drives the actual menu.
- **New output targets:** add an entry to `config/image_generation.json`'s `output_targets` map — no code change needed.
- **New review gates:** `WORKFLOW_SETTINGS_DEFAULTS` currently supports exactly two togglable gates (`review_concepts`, `review_prompts`); the pattern (boolean in config + an `_advance_*` helper) is reusable for a third gate, but nothing generic exists yet — Review Images has no disable toggle at all.
- **New reset stages:** would need a coordinated three-place update (`RESET_STAGE_ORDER`, `INVALIDATED_DESCRIPTIONS`, `_build_reset_outputs()`).
- **New stores/campaigns:** purely data-driven — drop a new `config/stores/<store_id>/` folder; `discover_stores()`/`discover_campaigns()` pick it up with no code change.

### 8. Architectural Risks

- **No non-interactive/headless entry point.** Every stage function that matters calls `input()` directly, and several gate real side effects behind an interactive `[y/N]` prompt (image generation, re-plan confirmation, prompt-rebuild confirmation). **A Controller cannot drive this repo as a subprocess today** — there is no `--yes`/non-interactive flag anywhere.
- **No machine-readable exit signal.** Confirmed zero `sys.exit()` calls; the process only exits via the "Exit" menu (implicit code 0) or an uncaught exception (arbitrary non-zero code, raw traceback). A Controller cannot distinguish "job succeeded," "job needs a human," and "the program crashed" from exit code alone.
- **Global mutable process state** for job/provider selection (`main._active_job_name`/`_active_provider_id`) — fine for one interactive human, but means the CLI cannot safely be driven concurrently or treated as a stateless service.
- **Prints as the only diagnostic channel** — no separation between "status output a Controller could parse" and "please type something here," all on the same stream.
- **`outputs/logs/` is a required folder that nothing writes to** — a Controller looking there for structured logs will find nothing.
- **Review Prompts is not a real review yet** — its own docstring states the stage only mirrors prompt-build-complete via a marker file, with no actual approve/reject captured. A Controller trusting `prompt_review_complete: true` as "a human approved these prompts" would be trusting something the code itself says isn't true yet.
- **Reset relies on `Path.rename()` for the final swap without a true atomic guarantee across every failure mode** — a process killed mid-swap (not just an exception) could leave stale `.reset_staging_*`/`.outputs_pre_reset_*` directories that no code path scans for and cleans up on a subsequent run.
- **Hardcoded relative paths throughout** (`JOBS_DIR`, `STORES_DIR`, `IMAGE_GENERATION_CONFIG_PATH`, etc., computed relative to CWD, not to the repo/package root) — running from any directory other than repo root would silently look in the wrong place.
- **Suspicious default model string** — `provider_registry.PROVIDER_ENV_CONFIG["claude_api"]["default_model"] = "claude-opus-4-8"` does not match any publicly documented Anthropic model identifier as of this repo's knowledge; if this is a stale placeholder rather than deliberately updated, `claude_api` concept generation would fail unless `CLAUDE_CONCEPT_API_MODEL` overrides it. Worth flagging for verification rather than treating as confirmed-working.
- **No test coverage for `main.py`'s non-interactive/exit-code behavior** — the exact behavior under non-interactive conditions (empty/redirected stdin) is unverified anywhere in the repo.
- **`HIDDEN_JOB_NAMES` allow-list is a hardcoded CLI-only cosmetic filter** — a Controller enumerating jobs directly via `jobs/*/job_manifest.json` (as recommended) would see test/dev jobs the CLI hides, since the hiding logic lives only in `main.py`.

---

## 1.C — `etsy-video-generator`

### 1. Executive Summary

**Purpose.** A single-purpose, single-file tool (`src/generate_video.py`, ~1567 lines, stdlib-only) that turns 3–5 confirmed-order product still images into one polished square MP4 "listing video" for Etsy, using FFmpeg as an external subprocess renderer.

**Responsibilities.** Discover/validate candidate images in `input/` (3–5 count, `.png/.jpg/.jpeg`); guess drag/drop order via file creation time and produce a human-reviewable contact sheet; get human confirmation/correction of that order; offer a preset menu and build the exact FFmpeg command for the chosen preset; invoke FFmpeg, verify the output file actually exists, move consumed source images into `processed-inputs/<video-stem>/`, and write a JSON generation manifest next to the video; guarantee never to overwrite an existing numbered video.

**Explicit non-responsibilities.** No pixel manipulation in Python — the "Python's job vs. FFmpeg's job" split is stated explicitly in the module docstring; never touches `archived-outputs/` except to read filenames for numbering; no image-content analysis (focal points are hardcoded constants, not detected); no batch/queue processing (one run = one video); no web UI/API server/daemon/watch mode (explicitly listed as unimplemented roadmap ideas); no config-file system, no CLI argument parsing, no environment-variable configuration; no dependency manifest exists at all (consistent with the "stdlib only" claim).

**Architectural philosophy.** "Constants + pure functions + a thin orchestrator." Every tunable knob is a named module-level constant with an explanatory comment, explicitly isolated per-preset so tuning one preset can never affect another (a repeated invariant in the code comments). Business logic raises a single custom exception (`VideoGenerationError`) rather than terminating the process; only `main()` converts that exception to a process exit, preserving CLI behavior while keeping the same logic safely importable.

**Current maturity — "V3 Stable"** per README/ROADMAP and commit `ac5b30c` ("freeze Controller-readiness architecture for V3 Stable"). The Controller-readiness refactor (adding `run_video_generation()`, `VideoGenerationError`, the JSON manifest) is committed, with dedicated test coverage in `tests/test_controller_readiness.py` (231 lines). Legacy pre-V3 snapshots of the whole script were moved to `archived-backups/*.py` and are explicitly excluded from `src/`, kept for reference only.

**Primary workflows.** (1) Interactive CLI run (`python src/generate_video.py` → `main()`) — the only entry point exercised by a human operator, includes both human-in-the-loop review gates. (2) Programmatic call (`run_video_generation(images, preset_key, output_file)`) — the entire non-interactive back half of the workflow, intended for a future Controller to call directly once it already has a confirmed image order and preset choice.

### 2. Internal Architecture

**Major subsystems (all in the one file):** path/config constants; input discovery & validation (`check_ffmpeg_is_available`, `find_input_images`); order detection & human confirmation (`sort_by_best_drop_order_guess`, `build_contact_sheet`, `ask_yes_no`, `ask_for_corrected_order`); output numbering (`find_highest_video_number`, `get_next_output_file`); pacing math, one pure function per preset (`compute_image_durations*`); FFmpeg command builders, one per preset; preset registry (`Preset` NamedTuple + `PRESETS` list — single source of truth binding key/display-name/builder, generic `ask_for_preset()`); post-render side effects (`move_images_to_processed`); manifest (`build_generation_manifest`, `write_generation_manifest`); orchestration (`run_video_generation`, `main`); error type (`VideoGenerationError`).

**Workflow orchestration.** `main()` is the sole orchestrator of the interactive path: input discovery, order confirmation, output-path resolution, preset selection, then hands off to `run_video_generation()` for everything after. `run_video_generation()` is a linear, non-branching sequence: build command → subprocess.run → verify file exists → move images → write manifest → return path.

**State management.** No in-memory state object or session/job object. State is entirely implicit in the filesystem (which folder files sit in, and the monotonically-increasing number in filenames). No lock file, no database, no in-memory job queue.

**Configuration.** Purely hardcoded module-level constants. No config file, no CLI flags, no environment variables. The only "runtime-configurable" surface is what the human types at the two `input()` prompts.

**Dependency structure.** Zero third-party Python packages — stdlib only (`json, math, re, shutil, subprocess, sys, datetime, pathlib, typing`). One external OS-level dependency: the `ffmpeg` binary must be on PATH. One Windows-specific dependency: hardcoded Windows font paths for the contact-sheet drawtext step (not required for the final render itself).

**Lifecycle.** Stateless, single-shot CLI process; no daemon/watcher/persistent process. Each invocation is a complete "one folder of images in → one video out" transaction with no cross-run memory beyond what's derivable from the filesystem.

**Public vs internal APIs.** No enforced public/private boundary in the language sense (no `__all__`, only one leading-underscore helper). The README and code comments *designate* `run_video_generation()`, `VideoGenerationError`, `build_generation_manifest()`, and `write_generation_manifest()` as the intended external/Controller-facing surface, but nothing enforces this — a Controller importing the module can call any function, including presumed-internal ones.

### 3. Public Interface

**Inputs.** Filesystem: `input/` with 3–5 image files. Interactive: order-confirmation Y/N, optional corrected-order string (e.g. `"3,1,2,4"`), preset-selection number. Programmatic (`run_video_generation`): `images: list[Path]` (confirmed order), `preset_key: str` (must be one of `"standard"`, `"slow-fade-color-variation"`, `"wisp-sweep-color-variation"`, `"design-reveal"`), `output_file: Path`. Implicit environment dependency: `ffmpeg` on PATH; on Windows, one of four hardcoded font paths for the contact-sheet step only (not required for the final video).

**Outputs.** One MP4 at `output/listing-video-NNN.mp4` (zero-padded ≥3 digits, sequential, never reused, scanning both `output/` and `archived-outputs/`). One JSON manifest at `metadata/listing-video-NNN.json` (same stem; `output/` itself stays videos-only as of V1 polish). `order-preview.jpg` at project root (interactive path only, overwritten every run). Side effect: source images physically moved from `input/` to `processed-inputs/listing-video-NNN/`.

**Folder structure:**
```
input/                                – drop zone for source images
output/                                – generated videos + manifests
processed-inputs/listing-video-NNN/   – moved source images per run
archived-outputs/                     – read-only from this script's perspective; still counted for numbering
src/generate_video.py                 – the entire implementation
tests/                                 – test_controller_readiness.py, test_design_reveal.py, test_wisp_sweep.py
archived-backups/                      – 6 superseded full-script snapshots, out of src/
docs/superpowers/{plans,specs}/        – design-spec docs for presets
```

**Launch method.** CLI: `python src/generate_video.py` → `main()` via `if __name__ == "__main__":`, no flags/subcommands. Programmatic: `import generate_video as gv; gv.run_video_generation(images, preset_key, output_file)` — fully importable with no import-time side effects.

**Exit behavior.** `sys.exit(str(exc))` at the very end of `main()`'s `except VideoGenerationError` block is **the only remaining `sys.exit()` call in the file** (confirmed via repo-wide search) — the V3-stabilization goal of removing bare `sys.exit()` from business logic is genuinely satisfied. Passing a string exits with code 1 and prints to stderr; success falls through to implicit code 0. Programmatically, `run_video_generation()` and everything it calls raises `VideoGenerationError` (a bare `Exception` subclass with only a docstring — no structured fields, no error code, no chaining) on any failure; it never calls `sys.exit()`.

**Manifest format** (`build_generation_manifest`, verified against a live example on disk):
```json
{
  "success": true,
  "timestamp": "2026-07-20T22:34:45.354765",
  "preset_key": "wisp-sweep-color-variation",
  "preset_display_name": "Wisp Sweep - Color Variation",
  "output_video": "listing-video-004.mp4",
  "image_order": ["flat-front-01.png", "flat-front-19.png", "flat-front-17.png", "flat-front-13.png"],
  "image_count": 4
}
```
`timestamp` has no timezone info. `output_video` is a bare filename (no directory component). `image_order` holds only filenames, not full paths. **No manifest-version/schema field, no run/job ID, no listing/store ID, and no error field for failure cases — the manifest is only ever written on success.** The code's own docstring states the flat/unversioned shape is deliberate to allow additive fields later (`design_id`, `listing_id`, `job_id`), but this is a design intent, not an enforced or validated contract.

**Status information.** None beyond stdout prints during the run and the manifest file after success — no in-progress status/state file, so a Controller polling the filesystem mid-run sees nothing distinguishing "in progress" from "not started."

**Logs.** No structured logging module anywhere. FFmpeg's own stderr is captured and only surfaced by embedding it into a `VideoGenerationError` message on failure; on success it is silently discarded, not persisted anywhere.

**Error reporting.** Exclusively via the `VideoGenerationError` message string (human-readable prose, sometimes embedding raw FFmpeg stderr verbatim). No error codes, no machine-parseable taxonomy/category field.

**Recovery/Resumability.** None as a mechanism, but failure is safely a no-op by construction: images only move after the output file's existence is verified, so a crash before that point leaves `input/` untouched and a retry is safe from the top. `move_images_to_processed()` checks all destination name clashes before moving any file specifically to avoid a half-moved state — but if a clash is detected, the video has *already* been rendered (video creation happens before the move), leaving an orphaned video+manifest in `output/` with no matching moved-images folder; the run is not automatically retriable without manual cleanup.

**Success conditions.** FFmpeg returns exit code 0 AND the expected output file actually exists on disk afterward (an explicit belt-and-suspenders check) AND images move without name clashes AND the manifest writes successfully.

**Failure conditions.** FFmpeg missing from PATH; wrong image count/type in `input/`; no usable font for the contact sheet (interactive path only); FFmpeg returns nonzero; FFmpeg reports success but the output file is missing; a destination name clash during the image-move step.

### 4. Workflow

| # | Stage | Input | Output | Human gate? | Automatic decision? |
|---|-------|-------|--------|---|---|
| 1 | FFmpeg availability check | PATH | none (or raises) | No | Hard fail if absent |
| 2 | Image discovery/validation | `input/` contents | image list | No | Hard fail outside 3–5 count |
| 3 | Order detection | image list | sorted list (by creation time) | No | Best-guess heuristic |
| 4 | Contact sheet build | detected order | `order-preview.jpg` | No | No |
| 5 | **Order confirmation gate** | detected order + typed answer | confirmed order | **Yes** | No |
| 6 | Output path resolution | `output/` + `archived-outputs/` contents | next numbered path | No | Always increments past both folders' max |
| 7 | **Preset selection gate** | `PRESETS` + typed number/blank | preset key | **Yes** (blank = default `"standard"`) | Partial |
| 8 | FFmpeg command build | confirmed images + output path | argv list | No | Yes |
| 9 | FFmpeg execution | argv | `CompletedProcess` | No | Fail-fast on nonzero returncode |
| 10 | Output existence verification | output path | pass/fail | No | Hard fail if file absent despite returncode 0 |
| 11 | Image relocation | confirmed images + output path | files moved | No | Two-phase clash-check-then-move |
| 12 | Manifest write | images/preset/output | JSON manifest | No | Yes |

**State transitions.** No formal state machine; the only externally observable "state" is: images present in `input/` (not started) → video+manifest present in `output/` and images moved to `processed-inputs/` (done). Nothing in between is ever durably recorded.

**Review gates.** Exactly two, both inside `main()` only — order confirmation and preset selection. **Neither gate exists in `run_video_generation()`** — a Controller calling that function directly must supply both the confirmed order and preset key itself; there is no mechanism for a Controller to defer either decision back to a human via this module's own code.

### 5. Structured Artifacts

| Artifact | Producer | Consumer | Lifecycle | Stability |
|---|---|---|---|---|
| `output/listing-video-NNN.mp4` | `run_video_generation` via FFmpeg subprocess | Human (Etsy upload); read by numbering logic | Created once, never rewritten; may be manually moved to `archived-outputs/`, still counted | **Stable** — filename pattern is enforced and load-bearing for numbering logic. |
| `metadata/listing-video-NNN.json` | `write_generation_manifest` | Intended future Controller; no consumer exists in-repo today | Written once, immediately after image move; never updated | **Semi-stable.** Field set is test-locked, but has no schema/version field and no documented no-removal guarantee. Only written on success. |
| `order-preview.jpg` | `build_contact_sheet` | Human, interactive review only | Overwritten every interactive run at a fixed path | Interactive-only; **not part of the programmatic/Controller contract** — never produced by `run_video_generation()`. |
| `processed-inputs/listing-video-NNN/` + images | `move_images_to_processed` | Human traceability only | Created once per successful run; name permanently tied to a video stem | Stable naming convention, but a folder can exist with no matching video if a human later deletes the video (not cross-checked by any code). |
| `input/` contents | upstream (human or another repo) | `find_input_images`, `move_images_to_processed` | Transient — leaves the moment a run succeeds | Not this repo's output; its expected hand-off point from upstream. |
| `archived-outputs/` contents | Human (manual) | `find_highest_video_number` (read-only) | Never written to by this script | Explicitly manual/out-of-band; only read for numbering. |

### 6. Stable Integration Contracts

**Stable enough to depend on today:** `run_video_generation(images: list[Path], preset_key: str, output_file: Path) -> Path` as a directly-importable callable — the closest thing this repo has to a documented, tested, non-interactive entry point, with dedicated test coverage for both success and every distinct failure path; `VideoGenerationError` as the single exception type raised by every business-logic function on failure (confirmed: the *only* remaining `sys.exit()` in the file is inside `main()`'s except block); the manifest's currently-documented field set (test-locked); the output filename convention and matching `.json` stem; the `PRESETS` keys as the valid `preset_key` values.

**Implementation details, not safe to depend on:** all FFmpeg filter-graph construction and the constants feeding it (explicitly designed to be independently tunable per preset); the image-ordering heuristic (best-guess, and only meaningful in the interactive flow since `run_video_generation()` takes an already-confirmed order); `order-preview.jpg` and the whole contact-sheet/font-lookup subsystem (interactive-only, Windows-font-path-dependent); `move_images_to_processed`'s internal two-phase clash-check implementation.

**Internal-only, never meant to be called by a Controller:** `ask_yes_no`, `ask_for_corrected_order`, `ask_for_preset`, `print_numbered_order` (all `input()`-blocking — a Controller process invoking these would hang waiting for stdin); `main()` itself (calls `sys.exit()`, would terminate a Controller's own in-process call); all the low-level FFmpeg expression builders (pure internal plumbing for specific presets).

### 7. Extension Points

- **New presets:** the `Preset` NamedTuple + `PRESETS` list is an explicit, actively-used extension point (four presets exist today, added incrementally per git history) — adding one requires only writing a new `build_ffmpeg_command_*` function and appending one `Preset(...)` entry; `ask_for_preset()` and `main()` are already generic over the list.
- **Independent per-preset pacing functions:** each preset has its own `compute_image_durations*` function with an explicit non-interference guarantee, making it straightforward to add a new pacing scheme without touching existing ones.
- **`run_video_generation()` as a programmatic seam:** designed and documented specifically so a Controller module can plug in upstream (supplying images+preset) and downstream (consuming the returned path / reading the manifest) without needing any of `main()`'s interactive machinery.
- **Manifest additive fields:** the manifest builder's docstring explicitly anticipates future keys (`design_id`, `listing_id`, `job_id`) being added without breaking the existing flat structure — an intended, if unenforced, extension point for Controller metadata.

No plugin system, no renderer abstraction (exactly one renderer, FFmpeg, invoked directly), and no new "execution method" extension point beyond CLI vs. direct import exists in code today.

### 8. Architectural Risks

1. **`run_video_generation()` has no timeout** on the underlying `subprocess.run(..., capture_output=True, text=True)` call — a hung or extremely slow FFmpeg process would block a Controller's calling thread/process indefinitely with no way to cancel via this API.
2. **No idempotency/dedup protection at the `run_video_generation()` level.** Nothing prevents a Controller from calling it twice with the same `output_file`; FFmpeg's `-y` flag means a second call would silently overwrite the first video with no warning. The "never overwrite a video" guarantee is only true when `get_next_output_file()` picks a *fresh* number — it is not enforced by `run_video_generation()` itself if a Controller passes a stale/reused path directly.
3. **Manifest has no schema version, run ID, or failure record.** A Controller shelling out to this as a subprocess (rather than importing it) has no JSON error artifact to parse on failure — only the CLI exit code (1) plus an unstructured stderr string.
4. **Windows-only contact-sheet dependency** — irrelevant to `run_video_generation()` itself, but blocks portability of the interactive path to non-Windows hosts.
5. **Filesystem-only state with no locking.** Two concurrent invocations computing `get_next_output_file()` before either writes could race and choose the same video number — a genuine concurrency risk if a Controller ever parallelizes video jobs against the same checkout.
6. **`VideoGenerationError` is an unstructured bare exception** — a Controller wanting to distinguish failure categories (missing FFmpeg vs. bad input count vs. render failure vs. move-clash) must string-match on message text, since there are no subclasses, error codes, or structured fields.
7. **Orphaned artifacts possible on partial failure.** The video is written and verified to exist *before* the image move is attempted; if the move then raises (e.g. a name clash), the function raises without writing a manifest, leaving a real video file with no corresponding manifest. A Controller retrying naively would produce a *second* video for the same images, since the numbering scan only looks at existing filenames, not manifest completeness.
8. **Concrete evidence of this orphaning already exists on disk**: `processed-inputs/listing-video-005/` exists with no corresponding video in either `output/` or `archived-outputs/` — a Controller inferring job history purely from `processed-inputs/` folder presence would be misled.
9. **No repo-level dependency manifest** (no `requirements.txt`) — the code is genuinely stdlib-only today, but nothing prevents a future edit from silently introducing a third-party import with no manifest to catch it.

(Non-risk, explicitly verified: no bare `sys.exit()` remains outside `main()` — the specific stated goal of the V3 refactor is genuinely satisfied.)

---

# PART 2 — CROSS-REPOSITORY ANALYSIS

## Shared Architectural Patterns

- **Filesystem-as-database.** All three repos use plain directories and JSON/PNG/MP4 files as their entire persistence layer. None uses a database, a message queue, or an in-memory server process. State is always "what files currently exist in which folder."
- **A designated "manifest" artifact as the intended machine-readable summary.** Each repo has converged independently on the same idea — `manifest.json` (mockup-generator), `job_manifest.json` (image-generator), `listing-video-NNN.json` (video-generator) — a single JSON file meant to answer "what happened, and did it succeed" without a consumer needing to re-derive it from raw outputs.
- **A `run_succeeded`/`success`-style boolean as the top-level status signal.** Mockup-generator's `run_succeeded`, video-generator's `success`, and image-generator's `pipeline_status.ready_for_controller` are the same idea at different granularities (per-run vs. per-job).
- **Human review gates implemented as blocking `input()` prompts inline in the main orchestration function**, not as a separable/pluggable review-service abstraction. All three repos hard-code "ask a human right here, block until they answer" directly in the control-flow function that also does everything else.
- **Custom exception types for domain errors, but no exit-code or error-code taxonomy at the process boundary.** Image-generator has the richest exception hierarchy (a dozen distinct `*Error` classes); video-generator has exactly one (`VideoGenerationError`); mockup-generator has none. None of the three maps its exceptions to a documented, stable set of process exit codes.
- **"Never delete, always archive" as a recurring safety instinct** — image-generator's `job_reset.py` (archive-then-swap), video-generator's `archived-outputs/` convention, mockup-generator's `processed-inputs/`/`backups/` folders. None of the three performs destructive deletion of prior work as part of normal operation.
- **Per-run/per-job immutable output folders with a numeric or dated identifier** — mockup-generator's `output/run-<date>-NNN/`, video-generator's `output/listing-video-NNN.{mp4,json}`, image-generator's `jobs/<job_name>/` (name-based rather than numeric, but the same "one folder per unit of work" idea).
- **A documented split between "engine" and future "Controller"** appears explicitly in two of the three repos' own code/docs (image-generator's `job_manifest.py` docstring names a "FUTURE CONTROLLER CONTRACT"; video-generator's commit history and `ROADMAP.md` explicitly describe a "Controller-readiness" refactor). Mockup-generator's README also gestures at this via its "Future architectural direction" section describing a `Controller Review and Selection` stage, even though its code has had no corresponding refactor.
- **Config as static files, not a config service** — all three read JSON/text config from disk at fixed relative paths; none uses a config server, feature-flag service, or centralized settings store.
- **No structured/leveled logging in any of the three** — all diagnostic output across all three repos is plain `print()` to stdout, with no `logging` module usage found anywhere.

## Shared Assumptions

- Single-operator, single-machine, single-run-at-a-time usage. None of the three was built with concurrent multi-job execution in mind (mockup-generator's single-ZIP assumption, video-generator's unlocked numbering scan, image-generator's process-global active-job state).
- CWD-relative path resolution rather than package-root-relative — all three assume they are invoked with the working directory set to their own repo root; none resolves paths relative to `__file__`/package location consistently (video-generator does derive some paths from `Path(__file__)`, but mockup-generator and image-generator use bare relative strings).
- A human is present and attentive at a terminal for the entire run. All three assume synchronous, blocking human interaction is acceptable and design no alternative.
- Success is filesystem-observable, not process-observable. All three expect a downstream reader to inspect files after the process exits, not to trust the process's own exit code.

## Shared Terminology / Artifact Concepts

- "Manifest" is used by two of the three explicitly (`manifest.json`, `job_manifest.json`) and is the conceptual (if not literally named) role played by video-generator's `listing-video-NNN.json`.
- "Approved" / review-gate vocabulary recurs: mockup-generator's preview "approval," image-generator's Concept/Prompt/Image "Review" stages and `outputs/approved/`, video-generator's order/preset "confirmation." The concept of an explicit human sign-off step before proceeding is shared, but the vocabulary ("approve" vs. "confirm" vs. "review") and the granularity differ.
- "Processed inputs" as a folder name is used almost identically by mockup-generator (`processed-inputs/`) and video-generator (`processed-inputs/<video-stem>/`) for the same purpose (archived source material after a successful run) — this is the single closest naming convergence across the three repos.

## Inconsistencies

**Naming:**
- The archived-source-material folder is `processed-inputs/` in both mockup-generator and video-generator (a genuine convergence), but image-generator has no equivalent concept at all — reference images are never archived/relocated after use, they simply remain in `jobs/<job_name>/reference_images/` indefinitely.
- "Manifest" naming differs: `manifest.json` vs. `job_manifest.json` vs. an unnamed-but-manifest-shaped `listing-video-NNN.json`. There is no shared filename convention a Controller could rely on across all three.
- The top-level success flag is named differently in each: `run_succeeded` (mockup-generator), `success` (video-generator), and there is no single top-level boolean at all in image-generator — success is a derived, multi-condition boolean (`pipeline_status.ready_for_controller`) computed from several other flags.

**Folder conventions:**
- Mockup-generator and video-generator both key their output-folder identity on a generated identifier embedded in the folder/file name (`run-<date>-NNN`, `listing-video-NNN`). Image-generator instead keys identity on a human-chosen job name/slug with no numeric run counter — the closest image-generator gets to a "run number" is `job_reset.py`'s `archive/run_NNN/`, which numbers *resets*, not the job itself.
- Only mockup-generator distinguishes `processed-outputs/` (reserved, unused) from `processed-inputs/` (active) — video-generator and image-generator have no equivalent "reserved for later" folder.

**Manifest conventions:**
- Only image-generator's manifests carry an explicit `schema_version`/`manifest_version`-style field that is actually exercised consistently across artifacts (`generation_config.json`'s `schema_version: "1.0"`, `approved_media_handoff.json`'s `schema_version: "1.0"`). Mockup-generator has a `manifest_version` field but it has already fallen out of sync with a real schema change that happened without bumping it. Video-generator's manifest has no version field at all.
- Only video-generator's manifest is success-only (no artifact at all is written on failure). Mockup-generator writes a manifest in both success and failure cases (recording `errors`/`run_succeeded: false`). Image-generator has no single "job manifest write" event at all — status is always a live computation over the current file tree, never written down as a frozen point-in-time record (except within `archive/run_NNN/`).
- Only mockup-generator's manifest is known to have already drifted incompatibly between historical runs with no migration; video-generator's manifest is newer and test-locked; image-generator's manifest is explicitly rebuilt fresh every time rather than persisted-and-trusted, sidestepping the drift problem structurally.

**Configuration:**
- Image-generator has the richest, most deliberate configuration system (per-job JSON config with a documented schema version, provider/model precedence rules). Mockup-generator and video-generator have zero external configuration surface — both are 100% hardcoded module constants.
- Only image-generator supports environment-variable configuration (API keys/models); the other two read no environment variables at all.

**Lifecycle / resumability:**
- Image-generator has by far the most mature resumability story (idempotent stage re-runs, staged archive-then-swap resets, per-package generation-status resume). Video-generator has partial, structural resumability (failure is a safe no-op because images don't move until success is verified) but no true resume-from-partial-progress mechanism and at least one confirmed orphaning failure mode. Mockup-generator has no resumability at all — a crash mid-run requires a full restart and leaves orphaned partial output.

**Status reporting:**
- Image-generator: a rich, multi-boolean `pipeline_status` dict plus a `warnings` array for soft inconsistencies. Mockup-generator: a single `run_succeeded` boolean plus a free-text `errors` array. Video-generator: a single `success` boolean with no warnings/partial-failure concept at all (a run either fully succeeds or raises before any manifest exists).

**Error reporting:**
- Image-generator: a dozen distinct typed exceptions, none surfaced beyond `print()`. Video-generator: exactly one exception type, used consistently, surfaced via `sys.exit(str(exc))` at the CLI boundary — closer to a real (if minimal) machine-observable failure contract than the other two. Mockup-generator: no custom exceptions at all; failures are either swallowed into the manifest's `errors` array (per-image) or left as raw, unstructured Python tracebacks (everything else).

**Workflow terminology:**
- Image-generator's pipeline has named, numbered "stages" with a formal `NEXT_STEP_ORDER` and per-stage completion booleans — the most formalized workflow model of the three. Video-generator and mockup-generator have no named-stage concept in code; their workflows are just the literal sequence of statements in `main()`.

## Common Root Cause Behind Most Inconsistencies

The three repos were evidently built independently, at different times, by (or for) the same author working iteratively rather than against a shared platform spec — each repo reinvents the "how do I tell a future Controller what happened" problem from scratch, arriving at structurally similar but lexically and mechanically different answers. This is a normal and expected outcome for engine-first, Controller-later development, not a sign of carelessness — image-generator in particular shows clear, deliberate anticipation of this exact integration problem.

---

# PART 3 — INTEGRATION READINESS

**Module boundaries / separation of concerns.** All three repos are cleanly separated *from each other* — there is no code-level coupling between the three (no repo imports from another, no shared library, no shared config format). Each does exactly one stage of the pipeline (mockup compositing / concept-and-image generation / video assembly) and none reaches into another's folders or files. This is a strong positive for Controller integration: the three are already black boxes with respect to each other.

**Internal separation of concerns is uneven.** Image-generator has the cleanest internal layering (leaf modules `job_manifest.py`/`provider_contracts.py`, an explicit provider abstraction, per-stage modules with declared non-responsibilities). Video-generator is a single 1567-line file, but its internal organization by section (constants → discovery → ordering → numbering → pacing → command-building → registry → side-effects → manifest → orchestration) is still legible and the one designed seam (`run_video_generation()`) is genuinely separable from the interactive CLI shell around it. Mockup-generator has the weakest separation: `batch_generate.py` mixes interactive prompting, business orchestration, and manifest writing in one function with no reusable non-interactive entry point at all.

**Coupling.** Coupling to human presence (via blocking `input()`) is the dominant coupling problem across all three, and it is total in mockup-generator and image-generator (there is no way to invoke either without a live terminal answering prompts) and partial in video-generator (the two interactive gates are cleanly separable from the reusable `run_video_generation()` function, which has neither gate hard-coded into it).

**Hidden assumptions.** CWD-must-be-repo-root (all three); single-run-at-a-time with no locking (all three, most acutely mockup-generator's single-ZIP assumption and video-generator's unlocked numbering scan); a human is watching stdout in real time (all three).

**Public interfaces.** Image-generator is the only repo with an interface explicitly designed and documented as public/Controller-facing (`job_manifest.json`, `approved_media_handoff.json`, `provider_registry`'s query functions). Video-generator has one genuinely reusable function (`run_video_generation()`) but no formally "declared" public interface beyond code comments and README prose. Mockup-generator has no declared public interface at all — everything is incidentally public because there is no packaging.

**Manifest quality.** Ranked from most to least Controller-ready: image-generator (versioned, rebuilt-not-trusted, richest status model) > video-generator (test-locked schema, but success-only and unversioned) > mockup-generator (already-drifted schema with an unbumped version field, written non-atomically in two passes).

**Workflow clarity.** Image-generator's is the clearest (named stages, an explicit next-step-order list, per-stage completion flags). Video-generator's is clear but implicit (a fixed linear sequence in code, not modeled as data). Mockup-generator's is the least formalized (a single long function with an embedded retry loop for the review gate).

**Launch independence.** None of the three can currently be launched non-interactively end-to-end. Video-generator comes closest because its non-interactive half (`run_video_generation()`) is a real, tested, already-separated function — a Controller only needs to solve "how do I obtain a confirmed order and a preset choice" (which could be answered by a Controller-side UI) rather than needing to modify video-generator itself. Image-generator and mockup-generator would each need actual code changes (a new non-interactive entry point / a flag-driven mode) to be launched by a Controller without a human at a terminal.

**Artifact stability / versioning.** Only image-generator has a real (if partial) versioning discipline. The other two either have no version field (video-generator) or an unmaintained one (mockup-generator).

**Portability.** All three assume Windows-esque path handling is fine (none is confirmed cross-platform-tested); mockup-generator additionally depends on binary model-weight files with no bootstrap script; video-generator's contact-sheet step is Windows-font-path-specific (though this doesn't affect the Controller-relevant `run_video_generation()` path).

**Controller compatibility (summary ranking):** Image-generator has the best-designed artifacts but the worst launch story (fully interactive, no reusable non-interactive function exists at all — even its "automatic" stages are still just steps inside the interactive CLI's control flow, not a standalone callable). Video-generator has the best launch story (a genuine, tested, non-interactive callable) but the thinnest artifact (no versioning, no failure record). Mockup-generator is the least Controller-ready on both axes at once (fully interactive, no reusable function, and a manifest schema that has already silently drifted).

---

# PART 4 — CONTROLLER DEPENDENCY REPORT

## `etsy-mockup-generator`

**Safe assumptions:** a successful run produces `output/run-<date>-NNN/manifest.json` with `run_succeeded: true` and moves the source ZIP to `processed-inputs/`; the `assets` array in a fresh manifest lists every generated PNG with its category; the category taxonomy (`flat_front`, `back`, `human_model_front`, `unknown`) is stable.

**Unsafe assumptions:** that `manifest_version` reflects the actual schema in use (it does not — it's stuck at `1` despite a real, already-happened schema change); that all keys observed in the current schema will be present in every manifest on disk (older runs are missing `manifest_version`/`design_id` and carry an obsolete `background_path`); that the process exit code communicates anything (it never does — always 0); that `framing_method` values form a stable enum to branch on (they are diagnostic labels only); that only one ZIP will ever be dropped in `input/` (the code takes the first match and silently ignores the rest).

**Expected launch behavior:** requires a live terminal; a Controller cannot launch this today without either (a) a code change adding a non-interactive mode, or (b) scripted stdin injection (fragile, undocumented, not supported by the repo's own design).

**Expected completion behavior:** the process exits 0 regardless of outcome; completion must be detected by watching for `manifest.json` to appear (or for the ZIP to disappear from `input/`), not by process exit.

**Expected outputs / manifests:** `output/run-<date>-NNN/assets/*.png` + `manifest.json`, per the schema in Section 1.A.3, defensively parsed for missing/extra keys.

**Expected user interaction:** three points (background choice, preview approval, design-ID text) — all currently unavoidable without a code change.

**Expected review stages:** exactly one meaningful gate (preview approval); the design-ID prompt is not really a review, just optional metadata capture.

**Expected recovery behavior:** none — a crash requires a full manual restart; the Controller should not assume any partial-progress state is salvageable.

**Information the Controller should monitor:** `run_succeeded`, `errors[]`, `framing_method_counts` (as a soft health signal — a run dominated by fallback framing methods indicates the MediaPipe models may be missing or broken, without the process itself ever raising an error for this).

**Information the Controller should ignore:** `manifest_version` (until the repo actually re-establishes discipline around it); the exact framing-math internals; the legacy per-category-subfolder output layout in old runs.

## `Etsy-AI-Image-Generator`

**Safe assumptions:** `job_manifest.json` is always rebuildable and reflects current ground truth (never stale, since it's a derived cache, never a source of truth itself); `outputs/approved_media_handoff.json` is the correct artifact to read for final deliverable assets, exactly as its own docstring recommends; `pipeline_status.ready_for_controller == true` reliably means at least one approved asset exists and image review is complete; the provider registry's `describe_*_provider()`/`is_configured()` functions correctly reflect live-vs-placeholder status (confirmed: Gemini genuinely is inert today, not just documented-as-inert).

**Unsafe assumptions:** that `prompt_review_complete: true` means a human actually reviewed prompts (it does not — it's a marker-file stand-in with no real review implemented yet); that the process's exit code communicates anything (it never does); that concurrent Controller-driven job processing is safe (the CLI holds job/provider selection in process-global variables, not per-invocation state); that `outputs/logs/` contains anything (nothing writes to it); that the `claude_api` default model string (`claude-opus-4-8`) is verified-correct rather than a possibly-stale placeholder — a Controller relying on this path should independently confirm the model resolves before trusting it in production.

**Expected launch behavior:** requires a live terminal today; no non-interactive entry point exists for any stage, and several stages gate real side effects behind interactive `[y/N]` confirmations. A Controller cannot drive this repo as a subprocess without new code being added to this repo (a headless/flag-driven mode) — this is the single largest gap of the three repos, precisely because it's paired with the best-designed artifacts.

**Expected completion behavior:** there is no single "job done" event — "done" is a continuously-recomputable derived state (`ready_for_controller`), which a Controller should poll/re-derive rather than expect to be told about via a completion signal.

**Expected outputs / manifests:** `job_manifest.json` (status/discovery) and `outputs/approved_media_handoff.json` (deliverable) — both explicitly designed for this purpose and the two artifacts a Controller should actually read; everything else in `outputs/` is a stable internal contract between this repo's own stages, not necessarily meant for Controller consumption (though `prompt_package.json`/`generation_metadata.json` are safe to read for diagnostics).

**Expected user interaction:** potentially at every stage — Concept Review and Image Review are true human gates by design (Image Review has no disable flag at all); Concept Generation via `claude_code_manual` is itself a human-initiated (or Claude-Code-session-initiated) direct-write action, not an automatic step.

**Expected review stages:** Concept Review (toggle-able), "Review Prompts" (not yet real, view-only), Image Review (mandatory, no toggle).

**Expected recovery behavior:** strong — idempotent stage re-runs, `job_reset.py`'s archive-then-swap rewind to any of six stage boundaries, per-package resume for image generation. A Controller can safely assume a partially-completed job is resumable by re-invoking the appropriate stage.

**Information the Controller should monitor:** `pipeline_status` (all 18 flags, not just `ready_for_controller`, to understand exactly where a job is stuck), `warnings[]` (soft cross-artifact inconsistencies that never block but indicate drift worth surfacing to a human).

**Information the Controller should ignore:** `outputs/logs/` (empty by construction today); `shot_plan.json`/`claude_code_concept_instructions.txt` (internal generation guidance, not status); the CLI's own menu text/structure (explicitly disposable, per its own code comment).

## `etsy-video-generator`

**Safe assumptions:** `run_video_generation(images, preset_key, output_file)` is a genuinely stable, tested, non-interactive callable — this is the one clean seam across all three repos where a Controller can call directly into the engine's code today without needing new code written in that repo; `VideoGenerationError` is the only exception type raised by this function and everything it calls; on success, a video exists at `output_file` and a matching manifest exists at the same stem with `.json`; on success, the source images have been moved out of the Controller's provided list's original location into `processed-inputs/<stem>/`.

**Unsafe assumptions:** that a manifest exists after any failure (it does not — manifests are success-only); that calling `run_video_generation()` twice with the same `output_file` is safe (it is not — FFmpeg's `-y` flag means silent overwrite); that `processed-inputs/<stem>/` existing implies a corresponding video still exists (already falsified on disk — `listing-video-005`'s folder exists with no matching video); that the call has any timeout protection (it does not — a hung FFmpeg process blocks indefinitely).

**Expected launch behavior:** a Controller should call `run_video_generation()` directly (in-process import, or via a small wrapper subprocess) rather than driving `main()`/the CLI — `main()` is interactive and calls `sys.exit()`, both of which are wrong for Controller use. The Controller itself must supply the confirmed image order and preset key; this repo provides no mechanism for surfacing "please confirm this order" back to a human — that human-facing UI would need to live in the Controller, not here.

**Expected completion behavior:** function return (a `Path`) on success; a raised `VideoGenerationError` on any failure — this is the cleanest completion signal of the three repos when integrated via direct import rather than subprocess.

**Expected outputs / manifests:** the JSON manifest schema in Section 1.C.3, treated as append-only/informational rather than authoritative (no version field, no run ID) — a Controller should generate and track its own run/job identifier externally rather than relying on this manifest to supply one.

**Expected user interaction:** none, if integrated via `run_video_generation()` directly — but the two decisions that interaction normally provides (image order, preset choice) must come from somewhere; the Controller (or a human via the Controller's own UI) must supply them.

**Expected review stages:** none exist inside `run_video_generation()`; both of this repo's own review gates (order confirmation, preset selection) live only in `main()` and are bypassed entirely by direct integration.

**Expected recovery behavior:** failure before the output-file-exists check is a safe no-op (nothing moved, retry from scratch is fine); failure during/after the image-move step can leave an orphaned video+no-manifest state requiring manual cleanup — a Controller should treat any `VideoGenerationError` raised during/after image relocation as needing a numbering-conflict check before blind retry.

**Information the Controller should monitor:** the returned path / raised exception from a direct call; the manifest's `image_count/image_order` as a cross-check against what was actually rendered.

**Information the Controller should ignore:** `order-preview.jpg` (interactive-only, never produced by the programmatic path); the Windows-font-path lookup logic (irrelevant to the Controller-relevant function); the internal FFmpeg filter-graph construction for any given preset.

---

# PART 5 — ECOSYSTEM ARCHITECTURE

**Conceptual role of each module.**

- **`etsy-mockup-generator`** is the *visual asset compositor* — it takes raw garment photography (a Printful-style export) and a chosen background, and turns them into finished, on-brand product images. It is the first visual-production stage.
- **`Etsy-AI-Image-Generator`** is the *creative concept and AI-image production* engine — it takes a Store/Campaign/product brief and produces both the *idea* (concepts) and the *execution* (rendered AI images) for lifestyle and AI-product-mockup imagery, ending in a curated, human-approved set of assets.
- **`etsy-video-generator`** is the *final-format assembler* — it takes a small, already-chosen set of finished still images (3–5) and turns them into one polished video asset, the last visual-production stage before an Etsy listing is created.

**How work is intended to flow between them (per each repo's own documented "future direction," not yet wired up in code anywhere):** Both `etsy-mockup-generator`'s README ("Generators → Asset Library → Controller Review and Selection → Approved Media Manifest → Draft Editor → Etsy Draft") and `Etsy-AI-Image-Generator`'s `job_manifest.py` docstring (naming itself feeding "a future Controller") describe the same shape: independent generator engines each produce their own kind of finished asset into their own output area; a not-yet-built Controller is the layer that would collect, curate, and select across all of them; a further not-yet-built Draft Editor would then assemble the final Etsy listing from the Controller's selections.

**Where responsibility changes hands today (all currently manual, human-mediated):**
1. A human runs `etsy-mockup-generator` against a Printful export → gets composited mockup PNGs in `output/run-*/assets/`.
2. A human (or Claude Code session) runs `Etsy-AI-Image-Generator` against a Store/Campaign brief → gets AI-and-lifestyle mockup PNGs in `jobs/<job>/outputs/approved/`.
3. A human manually selects 3–5 finished stills (from either or both of the above, or elsewhere) and drops them into `etsy-video-generator`'s `input/` folder → gets one listing video in `output/`.
4. A human manually assembles all of the above into an actual Etsy listing (via the separate `Etsy-Draft-Editor` repo, out of scope for this inspection except as a downstream reference point).

**What is genuinely missing today, not because it's unbuilt-but-planned but because no code path anywhere connects it:** there is no automated hand-off of assets *between* these three repos — image-generator's approved PNGs are not automatically fed into video-generator's `input/`; mockup-generator's approved assets are not automatically fed anywhere either. The only existing linkage across all three repos is conceptual (shared vocabulary, shared philosophy, similar-shaped manifests) and organizational (they live as sibling folders in the same workspace) — there is zero code-level or file-level cross-repo wiring today.

---

# PART 6 — INTEGRATION QUESTIONS

These cannot be answered from the repositories alone and should be resolved before Controller development begins.

1. **What identifies "a unit of work" across all three repos consistently?** Mockup-generator uses a dated run ID, video-generator uses a numeric video ID, image-generator uses a human-chosen job name — none of these three identifiers is designed to correlate with the others. Should the Controller mint its own cross-repo job/run ID and pass it in somehow (e.g. via `design_id`/`listing_id` fields the repos already anticipate but don't yet populate), or is correlation expected to happen by convention/timing/human judgment?
2. **Is the Controller expected to invoke these repos in-process (Python import) or out-of-process (subprocess/CLI)?** This materially changes the integration design for each repo differently — video-generator's `run_video_generation()` is genuinely ready for in-process import today; mockup-generator and image-generator are not, and would need new headless entry points built either way.
3. **Who builds the non-interactive/headless mode for `etsy-mockup-generator` and `Etsy-AI-Image-Generator`?** Is that in scope for "Controller V1" work (i.e., should the Controller project itself add a thin `--non-interactive` flag or callable wrapper to these sibling repos), or is it assumed these repos will remain human-driven indefinitely and the Controller only ever discovers/reads their *output*, never triggers their execution?
4. **How should the Controller surface the human-in-the-loop decisions that only exist inside `main()` today** (mockup-generator's background/approval/design-ID prompts; image-generator's concept/prompt/image reviews; video-generator's order/preset confirmation)? Is the intent for the Controller to build its own UI for these decisions and call into engine-level functions directly (bypassing each repo's own interactive CLI), or to keep driving each repo's existing CLI via some automation shim?
5. **What should happen with `etsy-mockup-generator`'s and `etsy-video-generator`'s unversioned/drifted manifest schemas going forward?** Should each repo be asked to adopt image-generator's `schema_version` discipline before Controller V1 depends on them, or should the Controller be built defensively (tolerant of missing/extra keys) from day one regardless?
6. **Is concurrent/parallel job execution against a single repo checkout a real V1 requirement?** All three repos currently assume single-run-at-a-time (unguarded), and this inspection found concrete unguarded races (video-generator's numbering scan, mockup-generator's single-ZIP assumption). If the Controller intends to run multiple jobs in parallel against the same checkouts, each repo needs locking/isolation that doesn't exist today.
7. **What is the intended lifecycle for `Etsy-AI-Image-Generator`'s "Review Prompts" stage** — is a Controller expected to wait for this to become a real review before depending on `prompt_review_complete`, or should the Controller simply never rely on that flag meaning anything until the repo's own TODO is resolved?
8. **Where does the "Prompt Budget Manager" and prompt/character-budget enforcement fit for a Controller** — is budget-exceeded ever surfaced as a hard failure the Controller needs to react to, or is it purely an internal compilation detail with no externally-visible failure mode? (Not fully resolved by this inspection; would benefit from direct confirmation.)
9. **Is the `claude_api` provider's default model string (`claude-opus-4-8`) intentional and current, or a stale placeholder?** This affects whether the Controller can treat `claude_api` concept generation as reliably "live" without an explicit model override supplied at call time.
10. **What is the intended relationship between `etsy-mockup-generator`'s `processed-outputs/` (reserved, currently unused) folder and any future Controller hand-off?** The repo's own README flags it as reserved for a later workflow step but does not specify what that step is.
11. **Should each engine repo eventually emit a manifest even on early-return/no-op paths** (e.g. mockup-generator's "no ZIP found"/"no backgrounds found" cases, which currently produce zero artifacts at all)? This matters for whether a Controller can distinguish "nothing to do" from "silently failed to start" without scraping stdout.
12. **Is there an intended relationship between `Etsy-Research-Assistant`/`Etsy-Draft-Editor` and this Controller's V1 scope** beyond "ignore them"? The user's brief explicitly scopes V1 to these three repos only, but the ecosystem diagram in mockup-generator's own README implies a longer chain (`... → Draft Editor → Etsy Draft`) that a Controller V1 boundary decision should acknowledge even if V1 doesn't implement it.

---

# PART 7 — FINAL ASSESSMENT

**Overall readiness for Version 1 of the Automation Controller: partial, and unevenly distributed across the three repos.** None of the three repositories can be driven end-to-end by a Controller today without either new code in that repo or a fragile automation shim around its interactive CLI. However, all three already express — in code, comments, or documentation — a clear intent to eventually support exactly this kind of external orchestration, and the artifacts they produce are, to varying degrees, already close to what a Controller needs.

**Strengths:**
- All three repos are already genuinely decoupled from each other at the code level — there is no cross-repo import or shared internal dependency to untangle. This is the single most important precondition for the "orchestrator over black-box engines" philosophy, and it is already true today.
- `Etsy-AI-Image-Generator` has designed, documented, versioned, Controller-facing artifacts (`job_manifest.json`, `approved_media_handoff.json`) and a genuinely extensible provider architecture — this is the clearest evidence in the whole workspace that the underlying philosophy (stable interfaces over shared internals) is understood and being actively practiced, not just aspired to.
- `etsy-video-generator`'s V3-stabilization work produced exactly the kind of seam a Controller needs: a tested, non-interactive, importable function (`run_video_generation()`) with a single, consistent exception type. This is the most Controller-ready *launch* mechanism of the three repos today.
- All three repos share an instinct toward safety over cleverness — archive-don't-delete, verify-before-declaring-success, fail-per-item rather than fail-the-whole-batch. This reduces the risk that Controller integration will surface silent data loss.

**Weaknesses:**
- No repo currently offers a genuinely headless, non-interactive execution path for its *interactive* stages (mockup-generator has none at all; image-generator's most valuable stages — Concept/Image Review — are irreducibly human gates by design, and even its automatic stages are only reachable through the interactive CLI's control flow, not a standalone function).
- No repo maps its internal error taxonomy to a stable, documented process exit code — all three would require a Controller to parse files or scrape stdout to determine outcome, not trust the process's own exit status.
- Manifest schema discipline is inconsistent: image-generator does it well (versioned, rebuilt-not-persisted-stale); mockup-generator has already silently drifted with an unbumped version; video-generator has no version field and is success-only.
- Concurrency/locking is absent everywhere it would matter for a Controller running multiple jobs — this is a design gap that would need to be closed either in each repo or entirely in the Controller layer (e.g. by never running two jobs against the same checkout simultaneously, which is a real constraint the Controller must then enforce itself).

**Clean abstractions worth building on directly:** image-generator's `provider_registry`/`provider_contracts` pair; image-generator's `job_manifest.py`/`approved_media_handoff.json` contract; video-generator's `run_video_generation()`/`VideoGenerationError` pair; video-generator's `Preset` registry pattern as a model for how "pluggable variants" can be expressed cleanly even in a single-file script.

**Potential integration friction, ranked by severity:**
1. Getting human-in-the-loop decisions (mockup-generator's background/approval/design-ID; image-generator's concept/prompt/image reviews; video-generator's order/preset confirmation) out of each repo's own blocking `input()` calls and into a form the Controller can present and collect asynchronously — this is the largest single body of work implied by this inspection, and it touches all three repos differently.
2. Reconciling identity/correlation across repos — none of the three repos' identifiers (dated run ID, numeric video ID, human job name) are designed to reference each other.
3. Establishing and enforcing manifest schema versioning discipline in the two repos that lack it today (mockup-generator, video-generator), ideally before the Controller starts depending on their exact field sets in production.
4. Deciding and implementing a concurrency/locking story, since none exists today and multiple repos have concrete unguarded race conditions.

**Long-term maintainability.** All three repos are readable, well-commented for their internal logic, and (with the partial exception of mockup-generator's backup-file hygiene) reasonably tidy. The biggest long-term maintainability risk is not within any one repo but at the Controller boundary itself: if the Controller is built by directly importing internals (rather than only the small set of contracts each repo has designed to be public), then every future refactor inside these engines becomes a potential Controller-breaking change — which is precisely the failure mode the user's stated architectural philosophy is trying to avoid, and precisely why the "safe vs. unsafe assumptions" distinctions in Part 4 matter more than any other single section of this report.

**Version 1 readiness verdict.** A Controller V1 can be reasonably built *today* against `etsy-video-generator`'s `run_video_generation()` function with minimal additional engineering in that repo. Building Controller V1 against `Etsy-AI-Image-Generator` and `etsy-mockup-generator` will require either (a) new, scoped, non-interactive entry points added to those two repos first (a real but bounded piece of engineering, not a redesign), or (b) an initial V1 Controller that only *reads* their output artifacts and status (discovery/reporting) without being able to *trigger* their execution — deferring the "drive the engine" capability to a later version once those repos gain headless modes. Given the user's own instruction that engines "own their own execution logic," option (b) — a read-and-orchestrate-around, not launch-and-control, first version for these two repos — is the interpretation most consistent with the stated philosophy, while `etsy-video-generator` alone is ready for genuine launch-and-control today.

**Version 2 considerations already implied by the current architecture** (not proposed here, only noted because the repos themselves already gesture at them): image-generator's `correlation_id`/`retry_count` fields in `provider_contracts.py` anticipate retry orchestration the Controller could eventually own; mockup-generator's `design_id` field and README explicitly anticipate a future Controller "owning linking runs to designs"; video-generator's manifest docstring explicitly anticipates `design_id`/`listing_id`/`job_id` fields for exactly this kind of cross-repo correlation once a Controller exists to populate them.
