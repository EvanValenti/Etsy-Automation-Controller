"""ImageGeneratorAdapter -- launch()/collect_results() now real, via a
subprocess worker calling headless.py (Etsy-AI-Image-Generator's new
Controller-integration public interface, src/headless.py).
discover()/validate() updated to reflect that launch() is no longer
stubbed.

One-action-per-Job workflow, generalizing etsy-mockup-generator's
two-phase design to an N-stage pipeline: config={"action": "advance",
"job_name": <engine-side job name>} drives the pipeline forward by
exactly one automatic stage per Job (concept planning, concept
generation, prompt building, or image generation -- whichever is next),
stopping the instant a stage genuinely needs a human (a review gate, or
Claude Code/Manual concept generation). Calling advance again for the
same job_name later -- whether the operator is continuing right away or
resuming a job days later -- is exactly the same action; there is no
separate "resume" verb, matching how headless.py itself has no concept of
"resuming" distinct from "advancing whatever's next." `action`/`job_name`
are adapter-private config keys, invisible to the Core, exactly like
`phase` is for MockupGeneratorAdapter.

Creating a new engine-side job (headless.create_job()) is cheap,
synchronous, local file-writing with no external API calls -- like
list_mockup_backgrounds(), it is NOT modeled as a Job at all. See
list_ai_image_jobs()/create_ai_image_job()/get_ai_image_job_status()/
list_ai_image_stores() below, called directly from
api/image_generator_routes.py the same way MockupGeneratorAdapter's
list_mockup_backgrounds() is.

Subprocess worker rationale: identical to VideoGeneratorAdapter/
MockupGeneratorAdapter (see those adapters' module docstrings) -- a real
Anthropic/OpenAI API call is network I/O we don't control the latency of;
running it in a process we spawn and fully control keeps a stall there
from ever blocking the Controller's own process. Etsy-AI-Image-Generator's
own relative-path assumptions (jobs/, config/stores/, .env) require the
worker's cwd to actually BE the engine repo root.
"""

from __future__ import annotations

import json
import shutil
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
from infra.adapters.discovery_utils import WORKSPACE_ROOT, read_text_safely, resolve_engine_repo_root

ENGINE_ID = "etsy-ai-image-generator"
REPO_DIR_NAME = "Etsy-AI-Image-Generator"
_WORKER_SCRIPT_PATH = Path(__file__).resolve().parent / "_headless_worker.py"
# -- Advance timeout budget --------------------------------------------------
# run_image_generation() (image_generator.py) generates every eligible
# concept sequentially inside ONE subprocess call, so this single timeout
# has to cover the whole batch.
#
# Live timing from a real 7-image run (job "stop_there_s_mushrooms",
# 2026-07-25): each OpenAI gpt-image-1 call took ~43-48s, all succeeded,
# none hung -- the 7th was hard-killed by a then-300s timeout mid-request
# even though images 1-6 were already on disk. A job configured for the
# full 10 AI product + 10 lifestyle concepts is 20 sequential calls, which
# at ~48s each is ~960s -- past the previous 600s budget for a legitimate,
# non-hanging run.
#
# The budget is expressed as base + per-image even though the per-image
# term is 0 today, so moving to the intended model (2 minutes base + 60s
# per image) is a two-constant change here and nothing else: every caller
# already goes through advance_timeout_seconds(). Retry behaviour is
# untouched -- there is none, and this does not add any.
ADVANCE_TIMEOUT_BASE_SECONDS = 1200.0
ADVANCE_TIMEOUT_PER_IMAGE_SECONDS = 0.0

# Kept as the public name other modules/tests may reference. Equals the
# flat budget an advance gets when the image count isn't known.
DEFAULT_WORKER_TIMEOUT_SECONDS = ADVANCE_TIMEOUT_BASE_SECONDS


def advance_timeout_seconds(expected_image_count: int | None = None) -> float:
    """Subprocess timeout for one advance() call.

    Today this is a flat budget: ADVANCE_TIMEOUT_PER_IMAGE_SECONDS is 0,
    so the base covers a worst-case full batch and expected_image_count
    changes nothing. The parameter exists so the call sites and the
    signature are already shaped for the per-image model; switching over
    means setting the two constants above (e.g. 120.0 base / 60.0 per
    image) and passing the real count in, without touching this function's
    callers or its contract.
    """
    if expected_image_count is None or ADVANCE_TIMEOUT_PER_IMAGE_SECONDS <= 0:
        return ADVANCE_TIMEOUT_BASE_SECONDS
    return ADVANCE_TIMEOUT_BASE_SECONDS + (ADVANCE_TIMEOUT_PER_IMAGE_SECONDS * max(0, expected_image_count))


def _resolve_worker_python(repo_root: Path) -> str:
    """Same resolution order as MockupGeneratorAdapter -- this engine also
    has no dedicated venv; its real dependencies (anthropic, openai,
    python-dotenv, pyperclip) live in the shared workspace-root venv
    (E:\\Vilicity\\.venv), not the Controller's own minimal venv."""
    engine_venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if engine_venv_python.is_file():
        return str(engine_venv_python)
    workspace_venv_python = WORKSPACE_ROOT / ".venv" / "Scripts" / "python.exe"
    if workspace_venv_python.is_file():
        return str(workspace_venv_python)
    return sys.executable


def _run_worker(repo_root: Path, job_spec: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    work_dir = Path(tempfile.mkdtemp(prefix="automation_controller_image_generator_"))
    spec_path = work_dir / "spec.json"
    result_path = work_dir / "result.json"
    spec_path.write_text(json.dumps(job_spec), encoding="utf-8")

    try:
        process = subprocess.run(
            [_resolve_worker_python(repo_root), str(_WORKER_SCRIPT_PATH), str(spec_path), str(result_path)],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(work_dir, ignore_errors=True)
        return {
            "success": False,
            "error": {
                "category": "launch_timeout",
                "message": f"etsy-ai-image-generator did not complete within {timeout_seconds:.0f} seconds.",
                "detail": {"timeout_seconds": timeout_seconds},
            },
        }

    if not result_path.is_file():
        returncode = process.returncode
        shutil.rmtree(work_dir, ignore_errors=True)
        return {
            "success": False,
            "error": {
                "category": "worker_no_result",
                "message": "Worker process exited without writing a result file.",
                "detail": {"worker_returncode": returncode},
            },
        }

    outcome = json.loads(result_path.read_text(encoding="utf-8"))
    shutil.rmtree(work_dir, ignore_errors=True)
    return outcome


def _require_repo_root() -> Path:
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    if repo_root is None:
        raise EngineLaunchError(
            {"category": "engine_unreachable", "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.", "detail": {}}
        )
    return repo_root


def list_ai_image_jobs() -> list[dict[str, Any]]:
    """Direct, synchronous read -- not a Job. Every existing engine-side
    job plus its current pipeline state, for the Controller's "resume an
    existing job" list."""
    repo_root = _require_repo_root()
    outcome = _run_worker(repo_root, {"repo_root": str(repo_root), "action": "list_jobs"}, timeout_seconds=30.0)
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def get_ai_image_job_status(job_name: str) -> dict[str, Any]:
    """Direct, synchronous read -- not a Job. The full manifest (pipeline
    status, counts, next step) for one engine-side job."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "get_status", "job_name": job_name}, timeout_seconds=30.0
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def list_ai_image_stores() -> list[dict[str, Any]]:
    """Direct, synchronous read -- not a Job. Every configured store plus
    its campaigns, for the Controller's "Create New Job" form."""
    repo_root = _require_repo_root()
    outcome = _run_worker(repo_root, {"repo_root": str(repo_root), "action": "list_stores"}, timeout_seconds=30.0)
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def create_ai_image_job(
    product_name: str, store_id: str, campaign_id: str, product_type: str,
    concept_counts: dict[str, int] | None = None, creative_notes: str = "", product_color: str = "",
) -> dict[str, Any]:
    """Direct, synchronous write -- not a Job (mirrors
    list_mockup_backgrounds()'s "cheap, local, no external API call"
    reasoning; headless.create_job() only writes two small JSON files).
    Returns {"job_name": ..., "job_folder": ...} -- job_name is what every
    subsequent advance_ai_image_job() Job's config.job_name must use.
    """
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root),
            "action": "create_job",
            "product_name": product_name,
            "store_id": store_id,
            "campaign_id": campaign_id,
            "product_type": product_type,
            "concept_counts": concept_counts,
            "creative_notes": creative_notes,
            "product_color": product_color,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


# -- Manual Mode (Prompt 2) --------------------------------------------------
# Every function below is a direct, synchronous call, not a Job -- exactly
# like create_ai_image_job() above: they're local file reads/writes (no
# external API call), so there's nothing to queue or poll. The Controller
# never interprets what's copied or imported here; it only moves text/bytes
# between the browser and whatever file headless.py's own real business
# logic (manual_concept_generation.py / manual_image_generation.py) expects
# -- see infra/adapters/image_generator/_headless_worker.py's action
# handlers for the exact pass-through.


def export_manual_concepts(job_name: str) -> dict[str, Any]:
    """Prepares this job's manual concept-generation request package and
    returns the prompt text + response-JSON template for the Controller
    UI's "Copy Concepts JSON" action to hand to the browser clipboard."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "export_manual_concepts", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def import_manual_concepts(job_name: str, response_json_text: str) -> dict[str, Any]:
    """Writes response_json_text verbatim to this job's
    manual_concept_response.json and runs the engine's real import
    (validation, duplicate detection, overwrite backups) -- for the
    Controller UI's "Import Concepts JSON" action. The Controller never
    parses or reshapes response_json_text itself."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root),
            "action": "import_manual_concepts",
            "job_name": job_name,
            "response_json_text": response_json_text,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def list_manual_prompts(job_name: str) -> list[dict[str, Any]]:
    """Every prompt package still eligible for manual (one-at-a-time)
    image generation -- for the Controller UI's per-prompt "Copy Prompt"
    list. Excludes concepts that already have a generated image,
    regardless of whether that generation came from OpenAI or a prior
    manual import."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "list_manual_prompts", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def copy_manual_prompt(job_name: str, category: str, concept_id: str) -> dict[str, Any]:
    """Prepares (or refreshes) ONE concept's manual image prompt and
    returns its text -- for the Controller UI's per-prompt "Copy Prompt"
    button. Never bundles every prompt together; one call copies exactly
    one concept's prompt, so generating images one at a time is the
    natural way to use this, not a special mode."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root),
            "action": "copy_manual_prompt",
            "job_name": job_name,
            "category": category,
            "concept_id": concept_id,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def import_manual_image(job_name: str, category: str, concept_id: str, staged_image_path: str, original_filename: str) -> dict[str, Any]:
    """Moves one already-staged, already-validated image file into this
    concept's incoming_images/ folder and runs the engine's real
    import_manual_images() -- for the Controller UI's "Import Image"
    action. staged_image_path must already exist on disk (the route
    handler stages the uploaded bytes there before calling this) --
    this function, like every other one in this module, never accepts
    raw upload bytes itself; only the API route layer touches the HTTP
    request body."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root),
            "action": "import_manual_image",
            "job_name": job_name,
            "category": category,
            "concept_id": concept_id,
            "staged_image_path": staged_image_path,
            "original_filename": original_filename,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def import_finished_images(job_name: str, staged_image_paths: list[str], category: str) -> dict[str, Any]:
    """Import a batch of already-finished images as one job's final
    deliverables, completing the job.

    The manual counterpart of the whole OpenAI image-generation step. Like
    import_manual_image() above, every path must already be staged on disk
    by the route handler -- this module never accepts raw upload bytes.

    A longer timeout than the single-image import: this copies and registers
    a whole batch (and rebuilds the approved-media handoff once per image),
    so it scales with the number of files rather than being one fixed
    operation.
    """
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root),
            "action": "import_finished_images",
            "job_name": job_name,
            "staged_image_paths": staged_image_paths,
            "category": category,
        },
        timeout_seconds=120.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


# -- Concept Review (Step: wire Controller to concept_review.py) -------------
# Every function here is a direct, synchronous call, not a Job -- same
# reasoning as Manual Mode above: local file reads/writes, no external API
# call, so there's nothing to queue or poll.


def list_concepts(job_name: str, category: str) -> list[dict[str, Any]]:
    """Every concept currently on disk for one category of one job."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "list_concepts", "job_name": job_name, "category": category},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def approve_concept(job_name: str, category: str, concept_id: str) -> dict[str, Any]:
    """Approve one concept by id. Returns {"concept": ..., "counts": ...}."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "approve_concept",
            "job_name": job_name, "category": category, "concept_id": concept_id,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def reject_concept(job_name: str, category: str, concept_id: str) -> dict[str, Any]:
    """Reject one concept by id. Returns {"concept": ..., "counts": ...}."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "reject_concept",
            "job_name": job_name, "category": category, "concept_id": concept_id,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


# -- Reference Image Management (Step: wire Controller to reference_images.py) -
# Same direct-synchronous-call posture as everything else in this file:
# local file reads/writes, no external API call.


def list_reference_images(job_name: str) -> list[str]:
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "list_reference_images", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def get_reference_image_roles(job_name: str) -> list[dict[str, Any]]:
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "get_reference_image_roles", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def add_reference_image(job_name: str, filename: str, staged_image_path: str) -> dict[str, Any]:
    """staged_image_path must already exist on disk (the route handler
    stages the uploaded bytes there before calling this) -- this function
    never accepts raw upload bytes itself, same posture as
    import_manual_image() above. Every real validation (content format,
    size, filename safety, duplicate-name rejection) happens inside the
    engine's reference_images.add_reference_image(), not here."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "add_reference_image",
            "job_name": job_name, "filename": filename, "staged_image_path": staged_image_path,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def remove_reference_image(job_name: str, filename: str) -> dict[str, Any]:
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {"repo_root": str(repo_root), "action": "remove_reference_image", "job_name": job_name, "filename": filename},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def correct_reference_image_role(job_name: str, filename: str, role: str) -> list[dict[str, Any]]:
    """Returns the full, updated role assignment for every reference image
    in the job, not just the one corrected -- the engine's own
    set_asset_role() already returns this shape."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "correct_reference_image_role",
            "job_name": job_name, "filename": filename, "role": role,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


# -- Provider Configuration ---------------------------------------------
# Read-only: which concept provider job_name currently resolves to, and
# whether it's actually usable right now (e.g. an API key configured) --
# for the Controller to explain *why* before attempting an automatic
# action, not just after it fails. Forcing the automatic provider itself
# happens inside _headless_worker.py's own "advance" branch, immediately
# before launching -- not exposed as a separate adapter action, since it's
# not a standalone operator-facing action, just part of what "advance"
# now always does.


def get_concept_provider(job_name: str) -> dict[str, Any]:
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "get_concept_provider", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


# -- Prompt Review ---------------------------------------------------------
# mark_prompt_review_complete() below calls the engine's own reusable
# prompt_builder.mark_prompt_review_complete() (via headless.py) -- the
# same function main.py's interactive Review Prompts screen now calls.
# This function writes nothing itself; it only relays the worker's result.


def list_prompts(job_name: str) -> list[dict[str, Any]]:
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "list_prompts", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def get_prompt_text(job_name: str, category: str, concept_id: str) -> str:
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "get_prompt_text",
            "job_name": job_name, "category": category, "concept_id": concept_id,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def mark_prompt_review_complete(job_name: str) -> dict[str, Any]:
    """Confirm Review Prompts for this job. Returns the refreshed job
    status (same shape as get_ai_image_job_status()) so the caller sees
    prompt_review_complete=True and next_step="Generate Images" without a
    second round trip."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "mark_prompt_review_complete", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


# -- Prompt Review + Image Review (operator approval stages) ----------------
# Same posture as everything above: one thin call per engine function, no
# interpretation of what comes back. The engine decides what a prompt
# contains, what may be deleted, and what approving an image does to the
# approved-media set (see Etsy-AI-Image-Generator/src/headless.py).


def get_prompt_detail(job_name: str, category: str, concept_id: str) -> dict[str, Any]:
    """One prompt as structure: operator-facing system/user prompt and
    reference images, plus a technical_metadata block a UI can collapse."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "get_prompt_detail",
            "job_name": job_name, "category": category, "concept_id": concept_id,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def delete_prompt(job_name: str, category: str, concept_id: str) -> dict[str, Any]:
    """Drop one prompt from this job's generation set. The engine refuses
    a prompt whose image already exists; that refusal is surfaced as-is."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "delete_prompt",
            "job_name": job_name, "category": category, "concept_id": concept_id,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def list_generated_images(job_name: str) -> list[dict[str, Any]]:
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root, {"repo_root": str(repo_root), "action": "list_generated_images", "job_name": job_name},
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def set_image_review_status(job_name: str, category: str, concept_id: str, status: str) -> dict[str, Any]:
    """Approve or reject one generated image. The engine's own image
    review logic owns what that means for outputs/approved|rejected/ and
    the approved-media handoff."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "set_image_review_status",
            "job_name": job_name, "category": category, "concept_id": concept_id, "status": status,
        },
        timeout_seconds=60.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


def resolve_generated_image_path(job_name: str, category: str, concept_id: str, filename: str) -> str:
    """Validated absolute path to one generated image file, for serving
    its bytes. The engine performs the containment check -- the Controller
    never builds this path itself from client input."""
    repo_root = _require_repo_root()
    outcome = _run_worker(
        repo_root,
        {
            "repo_root": str(repo_root), "action": "resolve_generated_image_path",
            "job_name": job_name, "category": category, "concept_id": concept_id, "filename": filename,
        },
        timeout_seconds=30.0,
    )
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]["path"]


class ImageGeneratorAdapter(BaseEngineAdapter):
    engine_id = ENGINE_ID

    def discover(self) -> EngineCapabilities:
        """Grounded, read-only inspection -- now checks for headless.py's
        expected public functions (this integration's new headless seam)
        rather than the old interactive-CLI-only markers.
        """
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        repo_exists = repo_root is not None

        headless_text = read_text_safely(repo_root / "src" / "headless.py") if repo_root else None
        has_advance = bool(headless_text and "def advance_job(" in headless_text)
        has_create = bool(headless_text and "def create_job(" in headless_text)
        has_status = bool(headless_text and "def get_job_status(" in headless_text)

        supports_launch = has_advance and has_create and has_status
        supports_monitoring = supports_launch  # collect_results() reads the worker's own result, not a poll

        notes: list[str] = []
        if not repo_exists:
            health_status = "unreachable"
            notes.append(f"Sibling repo '{REPO_DIR_NAME}' was not found on disk during discovery.")
        elif supports_launch:
            health_status = "healthy"
            notes.append(
                "launch() is real: config={'action': 'advance', 'job_name': ...} drives the pipeline "
                "forward by one automatic stage per Job (concept planning, concept generation via the "
                "Claude API, prompt building, or image generation via OpenAI), stopping at the first "
                "stage that needs a human (a review gate, or Claude Code/Manual concept generation). "
                "Job creation and status reads are direct synchronous calls, not Jobs -- see "
                "api/image_generator_routes.py."
            )
        else:
            health_status = "degraded"
            notes.append("src/headless.py did not contain the expected advance_job()/create_job()/get_job_status() markers.")

        implementation_status = ImplementationStatus.REAL if supports_launch else ImplementationStatus.STUB

        return EngineCapabilities(
            engine_id=ENGINE_ID,
            display_name="Etsy AI Image Generator",
            version=None,
            supports_launch=supports_launch,
            cancel_support=CancelSupport.UNSUPPORTED,
            supports_monitoring=supports_monitoring,
            supports_approvals=False,
            supports_pipelines=False,
            max_concurrent_runs=1,
            launch_config_schema=(
                {
                    "action": "str -- always 'advance'",
                    "job_name": "str -- an existing engine-side job name (see GET /image-generator/jobs)",
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
        if config.get("action") != "advance":
            errors.append(f"'action' must be 'advance', got {config.get('action')!r}")
        if not config.get("job_name"):
            errors.append("'job_name' is required")

        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        if repo_root is None:
            errors.append(f"{ENGINE_ID}: sibling repo not found on disk")
        elif config.get("job_name") and not (repo_root / "jobs" / config["job_name"]).is_dir():
            errors.append(f"job_name does not exist: {config['job_name']!r} -- create it first via POST /image-generator/jobs")

        return ValidationResult(is_valid=not errors, errors=errors)

    def launch(self, config: dict[str, Any], on_run_reference: OnRunReference | None = None) -> EngineRunReference:
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        if repo_root is None:
            raise EngineLaunchError(
                {"category": "engine_unreachable", "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.", "detail": {}}
            )

        job_spec = {"repo_root": str(repo_root), "action": "advance", "job_name": config["job_name"]}
        # expected_image_count is not passed yet -- the Controller doesn't
        # know how many concepts this advance will generate for until the
        # engine decides. Once it does, that count goes here and the
        # per-image budget starts applying; see advance_timeout_seconds().
        outcome = _run_worker(repo_root, job_spec, timeout_seconds=advance_timeout_seconds())
        if not outcome["success"]:
            raise EngineLaunchError(outcome["error"])

        result = outcome["result"]
        return EngineRunReference(
            engine_id=ENGINE_ID,
            reference_type="headless_result",
            reference_value=config["job_name"],
            created_at=datetime.now(timezone.utc),
            extra=result,
        )

    def monitor(self, ref: EngineRunReference) -> EngineStatus:
        """Not needed: launch() blocks in-process (via the worker
        subprocess) until the one requested stage is fully terminal,
        exactly like the other two adapters."""
        raise NotImplementedError

    def collect_results(self, ref: EngineRunReference) -> EngineResult:
        """Real: launch() already ran the worker to completion and stashed
        its full result dict ({"advance": {...}, "job_status": {...}}) on
        ref.extra -- this just re-shapes it into an EngineResult, no
        second read."""
        return EngineResult(success=True, artifacts=dict(ref.extra), error=None)
