"""etsy-ai-image-generator operator workflow routes. Mirrors
api/mockup_generator_routes.py's shape: this module owns turning a
Create-Job form submission into an engine-side job, listing engine-side
jobs/stores for the UI, and safe, narrowly-scoped access to this engine's
own output folder -- it never duplicates or bypasses
JobService.create_job() / ExecutionCoordinator.evaluate(), the same pair
every other engine's routes use.

One-action-per-Job workflow (see infra/adapters/image_generator/adapter.py's
module docstring): POST .../jobs creates an engine-side job directly
(cheap, synchronous, no Controller Job involved -- like
POST /mockup-generator/jobs/preview's zip staging, but even simpler since
there's no file upload). POST .../jobs/{job_name}/advance is what creates
+ launches a real Controller Job, driving the pipeline forward by exactly
one automatic stage. Calling advance again for the same job_name is both
"continue" and "resume" -- there is no separate resume verb.

Route-naming note (see mockup_generator_routes.py's docstring for the
full story): every route here is prefixed /image-generator/... -- FastAPI
has no per-engine routing concept, so reusing a bare /jobs/{id}/... path
already claimed by another engine's router would silently misroute
requests to the wrong handler.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.dependencies import get_execution_coordinator, get_job_service
from core.execution.coordinator import ExecutionCoordinator
from core.services.job_service import JobService
from infra.adapters.discovery_utils import resolve_engine_repo_root
from infra.storage.ai_image_flat_view import build_flat_image_view
from infra.adapters.image_generator.adapter import (
    add_reference_image as _add_reference_image,
    approve_concept as _approve_concept,
    copy_manual_prompt as _copy_manual_prompt,
    correct_reference_image_role as _correct_reference_image_role,
    create_ai_image_job,
    delete_prompt as _delete_prompt,
    export_manual_concepts as _export_manual_concepts,
    get_ai_image_job_status,
    get_concept_provider as _get_concept_provider,
    get_prompt_detail as _get_prompt_detail,
    get_prompt_text as _get_prompt_text,
    get_reference_image_roles as _get_reference_image_roles,
    import_manual_concepts as _import_manual_concepts,
    import_finished_images as _import_finished_images,
    import_manual_image as _import_manual_image,
    list_ai_image_jobs,
    list_ai_image_stores,
    list_concepts as _list_concepts,
    list_generated_images as _list_generated_images,
    list_manual_prompts as _list_manual_prompts,
    list_prompts as _list_prompts,
    list_reference_images as _list_reference_images,
    mark_prompt_review_complete as _mark_prompt_review_complete,
    reject_concept as _reject_concept,
    remove_reference_image as _remove_reference_image,
    resolve_generated_image_path as _resolve_generated_image_path,
    set_image_review_status as _set_image_review_status,
)

ENGINE_ID = "etsy-ai-image-generator"
REPO_DIR_NAME = "Etsy-AI-Image-Generator"

router = APIRouter()


@router.get("/image-generator/stores")
def list_stores() -> list[dict]:
    try:
        return list_ai_image_stores()
    except Exception as exc:  # noqa: BLE001 -- EngineLaunchError from the adapter, or a genuine crash
        raise HTTPException(status_code=502, detail=f"Could not list stores: {exc}") from exc


@router.get("/image-generator/jobs")
def list_jobs() -> list[dict]:
    """Every engine-side job plus its current pipeline state -- the
    Controller's "resume an existing job" list."""
    try:
        return list_ai_image_jobs()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not list jobs: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/status")
def get_job_status(job_name: str) -> dict:
    """The engine's own manifest for one job -- pipeline_status, counts,
    next_step -- presented verbatim, never reinterpreted."""
    try:
        return get_ai_image_job_status(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Could not read job status: {exc}") from exc


# -- Concept Review -----------------------------------------------------
# Direct, synchronous calls (like GET .../status above) -- concept read/
# approve/reject are all local file reads/writes with no external API
# call, so there's nothing to queue or poll. Every real decision (which
# file, how to find a concept by id, how to persist a status change)
# happens inside concept_review.py; this module only maps HTTP <-> those
# functions.


@router.get("/image-generator/jobs/{job_name}/concepts/{category}")
def list_concepts(job_name: str, category: str) -> list[dict]:
    """Every concept currently on disk for one category, exactly as the
    engine stores it -- presented verbatim, never reinterpreted."""
    try:
        return _list_concepts(job_name, category)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not list concepts: {exc}") from exc


@router.post("/image-generator/jobs/{job_name}/concepts/{category}/{concept_id}/approve")
def approve_concept(job_name: str, category: str, concept_id: str) -> dict:
    try:
        return _approve_concept(job_name, category, concept_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not approve concept: {exc}") from exc


@router.post("/image-generator/jobs/{job_name}/concepts/{category}/{concept_id}/reject")
def reject_concept(job_name: str, category: str, concept_id: str) -> dict:
    try:
        return _reject_concept(job_name, category, concept_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not reject concept: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/concept-provider")
def get_concept_provider(job_name: str) -> dict:
    """Which concept provider this job currently resolves to, and whether
    it's actually usable right now -- read-only, so the UI can explain
    *before* "Continue Automatically" is clicked whether it will genuinely
    run automatic generation."""
    try:
        return _get_concept_provider(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read concept provider: {exc}") from exc


# -- Prompt Review -----------------------------------------------------
# Same direct-synchronous-call posture as Concept Review above: local file
# reads/writes, no external API call. Confirming review delegates to the
# engine's own reusable prompt_builder.mark_prompt_review_complete() (via
# the headless adapter) -- this route never writes the marker file itself.


@router.get("/image-generator/jobs/{job_name}/prompts")
def list_prompts(job_name: str) -> list[dict]:
    try:
        return _list_prompts(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not list prompts: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/prompts/{category}/{concept_id}/text")
def get_prompt_text(job_name: str, category: str, concept_id: str) -> dict:
    try:
        return {"text": _get_prompt_text(job_name, category, concept_id)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read prompt text: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/prompts/{category}/{concept_id}/detail")
def get_prompt_detail(job_name: str, category: str, concept_id: str) -> dict:
    """The structured form of one prompt: system/user prompt text and
    reference images (what an operator reviews) separated from
    technical_metadata (what only a developer needs). The /text route
    above still returns the engine's single human-formatted blob and is
    unchanged -- this is additive."""
    try:
        return _get_prompt_detail(job_name, category, concept_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read prompt: {exc}") from exc


@router.delete("/image-generator/jobs/{job_name}/prompts/{category}/{concept_id}")
def delete_prompt(job_name: str, category: str, concept_id: str) -> dict:
    """Remove one prompt from this job's generation set, so only the
    remaining prompts are sent to OpenAI. Returns the refreshed job status
    so Prompt Review updates immediately."""
    try:
        return _delete_prompt(job_name, category, concept_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not delete prompt: {exc}") from exc


@router.post("/image-generator/jobs/{job_name}/prompts/review-complete")
def mark_prompt_review_complete(job_name: str) -> dict:
    """Confirm Review Prompts for this job. Returns the refreshed job
    status (pipeline_status.prompt_review_complete=True, next_step
    updated to "Generate Images") so the frontend doesn't need a second
    request to reflect the change."""
    try:
        return _mark_prompt_review_complete(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not confirm prompt review: {exc}") from exc


# -- Image Review ----------------------------------------------------------
# The in-app replacement for the engine's interactive Review Images
# screen. Approving/rejecting delegates entirely to the engine's own
# image_review logic (which owns outputs/approved|rejected/ and the
# approved-media handoff); these routes only shuttle the decision.


@router.get("/image-generator/jobs/{job_name}/generated-images")
def list_generated_images(job_name: str) -> list[dict]:
    try:
        return _list_generated_images(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not list generated images: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/generated-images/{category}/{concept_id}/{filename}/file")
def get_generated_image_file(job_name: str, category: str, concept_id: str, filename: str) -> FileResponse:
    """Serves one generated image for the in-app gallery. The path is
    resolved and containment-checked by the engine, never assembled here
    from the client's filename."""
    try:
        path = _resolve_generated_image_path(job_name, category, concept_id, filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"Could not read generated image: {exc}") from exc
    return FileResponse(path, content_disposition_type="inline")


class ImageReviewDecision(BaseModel):
    status: str  # "approved" | "rejected" -- validated by the engine


@router.post("/image-generator/jobs/{job_name}/generated-images/{category}/{concept_id}/review")
def set_image_review_status(job_name: str, category: str, concept_id: str, body: ImageReviewDecision) -> dict:
    try:
        return _set_image_review_status(job_name, category, concept_id, body.status)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not record image review: {exc}") from exc


# -- Reference Image Management -------------------------------------------
# Reference images are a persistent job resource, not a workflow stage --
# these routes are available regardless of pipeline status. Same
# direct-synchronous-call posture as everything else in this file; every
# real validation (content format, size, filename safety, duplicate-name
# rejection) happens inside the engine's reference_images.py, not here.


@router.get("/image-generator/jobs/{job_name}/reference-images")
def list_reference_images(job_name: str) -> list[str]:
    try:
        return _list_reference_images(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not list reference images: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/reference-images/roles")
def get_reference_image_roles(job_name: str) -> list[dict]:
    """The current (saved-or-inferred) role for every reference image in
    the job -- read-only, never writes anything."""
    try:
        return _get_reference_image_roles(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not read reference image roles: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/reference-images/{filename}/file")
def get_reference_image_file(job_name: str, filename: str):
    """Serves one reference image's raw bytes so the browser can render an
    actual thumbnail -- the one place a job_name/filename pair turns into
    something served over HTTP, always re-validated against the engine's
    real repo root before use, mirroring
    mockup_generator_routes.py's _validated_artifact_path()."""
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    if repo_root is None:
        raise HTTPException(status_code=502, detail="Engine repo is not reachable.")
    jobs_dir = (repo_root / "jobs").resolve()
    job_dir = (jobs_dir / job_name).resolve()
    if jobs_dir not in job_dir.parents:
        raise HTTPException(status_code=409, detail="job_name resolves outside the engine's jobs directory.")
    ref_dir = (job_dir / "reference_images").resolve()
    path = (ref_dir / filename).resolve()
    if ref_dir not in path.parents:
        raise HTTPException(status_code=409, detail="Invalid filename.")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Reference image not found.")
    return FileResponse(path)


@router.post("/image-generator/jobs/{job_name}/reference-images")
async def add_reference_image(job_name: str, file: UploadFile = File(...)) -> dict:
    """Stages the uploaded bytes to a temp file, then hands only that
    staged path to the engine's real add_reference_image() -- the
    Controller performs no content/format/size validation of its own here;
    every one of those checks (and the resulting error message, if any)
    comes from the engine. Mirrors the manual-image-upload route's staging
    shape, minus the pre-validation that route does -- reference-image
    validation is deliberately left entirely to the engine."""
    content = await file.read()
    original_filename = file.filename or "upload.png"

    staging_dir = Path(tempfile.gettempdir()) / "automation_controller_reference_image_uploads"
    staging_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_filename).suffix or ".png"
    staged_path = staging_dir / f"{uuid.uuid4()}{suffix}"
    staged_path.write_bytes(content)

    try:
        return _add_reference_image(job_name, original_filename, str(staged_path))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not add reference image: {exc}") from exc
    finally:
        staged_path.unlink(missing_ok=True)


@router.delete("/image-generator/jobs/{job_name}/reference-images/{filename}")
def remove_reference_image(job_name: str, filename: str) -> dict:
    try:
        return _remove_reference_image(job_name, filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not remove reference image: {exc}") from exc


class CorrectReferenceImageRoleRequest(BaseModel):
    role: str


@router.post("/image-generator/jobs/{job_name}/reference-images/{filename}/role")
def correct_reference_image_role(job_name: str, filename: str, payload: CorrectReferenceImageRoleRequest) -> list[dict]:
    """Returns the full, updated role assignment for every reference image
    in the job, not just the one corrected -- the engine's own response
    shape, unchanged."""
    try:
        return _correct_reference_image_role(job_name, filename, payload.role)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not correct reference image role: {exc}") from exc


class CreateAiImageJobRequest(BaseModel):
    product_name: str
    store_id: str
    campaign_id: str
    product_type: str
    concept_counts: dict[str, int] | None = None
    creative_notes: str = ""
    product_color: str = ""


@router.post("/image-generator/jobs")
def create_job(payload: CreateAiImageJobRequest) -> dict:
    """Direct, synchronous engine-side job creation -- not a Controller
    Job. Returns {"job_name": ..., "job_folder": ...}; job_name is what
    POST .../jobs/{job_name}/advance uses to drive this job forward.
    """
    try:
        return create_ai_image_job(
            product_name=payload.product_name,
            store_id=payload.store_id,
            campaign_id=payload.campaign_id,
            product_type=payload.product_type,
            concept_counts=payload.concept_counts,
            creative_notes=payload.creative_notes,
            product_color=payload.product_color,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not create job: {exc}") from exc


@router.post("/image-generator/jobs/{job_name}/advance")
def advance_job(
    job_name: str,
    job_service: JobService = Depends(get_job_service),
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
):
    """Create + launch a real Controller Job that drives job_name's
    pipeline forward by exactly one automatic stage, through the same
    create_job()+evaluate() pair every other engine's routes use. May
    legitimately take a while (a real Claude/OpenAI API call) -- callers
    must not depend on this HTTP request's own timeout to know the
    outcome; poll the returned Job (GET /jobs/{id}) the same way
    MockupLaunchWorkflow.tsx already does, not this response.
    """
    config = {"action": "advance", "job_name": job_name}
    job = job_service.create_job(engine_id=ENGINE_ID, config=config)
    coordinator.evaluate(job.engine_id)
    return job_service.get_job(job.id)


def _resolve_job_dir(job_name: str) -> Path:
    """The one place a job_name becomes a real directory: always under the
    engine's own jobs/ root, never a client-supplied path. Every open-*
    target below starts here, so path validation happens exactly once."""
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    if repo_root is None:
        raise HTTPException(status_code=502, detail="Engine repo is not reachable.")
    jobs_dir = (repo_root / "jobs").resolve()
    job_dir = (jobs_dir / job_name).resolve()
    if jobs_dir not in job_dir.parents:
        raise HTTPException(status_code=409, detail="job_name resolves outside the engine's jobs directory.")
    if not job_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Job not found: {job_name!r}")
    return job_dir


# What an operator drags into a launcher. Everything else a concept
# package contains -- generation_metadata.json, prompt_package_snapshot.json,
# system/user prompt snapshots -- is developer material, and must never be
# what an "open the images" action lands somebody on.
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})


def _holds_images(directory: Path) -> bool:
    """True when `directory` DIRECTLY contains at least one image file."""
    try:
        return any(entry.is_file() and entry.suffix.lower() in _IMAGE_SUFFIXES for entry in directory.iterdir())
    except OSError:
        return False


def _is_assets_folder(directory: Path) -> bool:
    """True when `directory` is a folder of deliverables rather than a
    concept package that happens to hold one.

    Both shapes contain images, so "contains an image" cannot tell them
    apart. The proportion can:

        mockup assets/       19 images,  0 other  -> deliverables
        imported_005/         1 image,   2 JSON   -> package
        ai_01/                1 image,   4 meta   -> package

    Images having to OUTNUMBER everything else is what keeps a one-image
    concept package -- which is always outnumbered by its own metadata --
    from being mistaken for somewhere worth landing an operator.
    """
    images = others = 0
    try:
        for entry in directory.iterdir():
            if entry.is_file():
                if entry.suffix.lower() in _IMAGE_SUFFIXES:
                    images += 1
                else:
                    others += 1
    except OSError:
        return False
    return images > others


def _descend_to_image_folder(start: Path) -> Path:
    """Walk down from `start` to the folder an operator should be looking
    at to pick images.

    This used to stop at "the deepest directory holding ANY file", which
    was wrong for how this engine actually writes output. image_review.py
    copies every approved concept into its OWN package folder --
    outputs/approved/ai_product_mockups/imported_003/ -- holding exactly
    one image plus its generation_metadata.json and
    prompt_package_snapshot.json. So the old rule descended past the
    collection into a single concept, picked by most-recent mtime, and
    landed the operator on:

        imported_003/  ->  1 image + 2 JSON files

    which is both the wrong depth (the other approved concepts are hidden
    one level up) and the wrong content (JSON as the primary thing on
    screen). Selecting 3-5 images for a video meant navigating up and back
    down once per image.

    The rule now has two stopping conditions instead of one:

      1. The directory directly holds images -> that IS the assets folder,
         return it. This is the Mockup Generator's shape (assets/ full of
         PNGs, manifest.json deliberately one level up) and it is what
         "open outputs" should always land on when it exists.

      2. Its SUBFOLDERS hold the images, one package each -> return THIS
         directory, the collection. Descending would arbitrarily pick one
         concept and hide its siblings.

    Otherwise keep descending via the most recently modified subfolder --
    the branch the operator most likely just produced. Still depth-capped,
    and still returns the deepest directory reached rather than raising,
    because landing a level too high beats an error dialog.
    """
    current = start
    for _ in range(10):
        # 1. Already standing in a folder of deliverables. Best destination.
        if _is_assets_folder(current):
            return current

        try:
            subdirs = [entry for entry in current.iterdir() if entry.is_dir()]
        except OSError:
            return current
        if not subdirs:
            return current

        # 2. Exactly one child is a deliverables folder -- the Mockup shape,
        #    where run-root holds assets/ next to a manifest.json. Descend:
        #    every image is in there, so stopping here would only cost a
        #    click. Ambiguous when there are several, which case 3 handles.
        assets_children = [subdir for subdir in subdirs if _is_assets_folder(subdir)]
        if len(assets_children) == 1:
            current = assets_children[0]
            continue

        # 3. Children are concept packages, one image each -- this level is
        #    the collection. Descending would pick one concept by mtime and
        #    hide its siblings, which is the bug this function had.
        if any(_holds_images(subdir) for subdir in subdirs):
            return current

        current = max(subdirs, key=lambda p: p.stat().st_mtime)
    return current


# Named open targets. Keeping these as data (rather than one route per
# destination) is what lets "Open Images" and "Open Job Files" ship as two
# buttons against one endpoint, and lets a future third target be added
# here alone -- no new route, no new client function.
OPEN_TARGET_IMAGES = "images"
OPEN_TARGET_JOB_FILES = "job_files"
_OPEN_TARGETS = (OPEN_TARGET_IMAGES, OPEN_TARGET_JOB_FILES)


def _resolve_open_target(job_name: str, target: str) -> Path:
    """Resolve one named target to the folder an operator should actually
    land in.

    images     -- a flat, Controller-built folder of this job's images, so
                  the operator gets a selectable thumbnail grid exactly
                  like the Mockup Generator's assets/. Sources from
                  outputs/approved/ once review has produced approved
                  assets, else outputs/generated/. Never a concept
                  package, whose contents are mostly developer metadata.
    job_files  -- the whole job package (jobs/<job_name>/): reference
                  images, config, concepts, prompts and outputs together.
                  This is the developer-facing target, and the only one
                  that deliberately exposes metadata.
    """
    job_dir = _resolve_job_dir(job_name)

    if target == OPEN_TARGET_JOB_FILES:
        return job_dir

    for candidate in (job_dir / "outputs" / "approved", job_dir / "outputs" / "generated"):
        if candidate.is_dir() and any(candidate.iterdir()):
            collection = _descend_to_image_folder(candidate)
            # This engine writes one image per concept package, so the
            # collection folder is a folder-of-folders -- correct, but
            # still a round trip per image when picking 3-5 for a video.
            # The flat view is the folder that layout never produced:
            # hardlinks to the same bytes, named by concept, nothing else
            # in it. Rebuilt per request so it cannot go stale against an
            # approval made since the last click.
            flat = build_flat_image_view(job_name, collection)
            # If nothing linked (unreadable source, or a layout with no
            # images under it), fall back to the real folder rather than
            # opening an empty directory and implying the job produced
            # nothing.
            if any(flat.iterdir()):
                return flat
            return collection
    raise HTTPException(status_code=409, detail="No generated or approved output exists yet for this job.")


@router.post("/image-generator/jobs/{job_name}/open-output-folder")
def open_output_folder(job_name: str, target: str = OPEN_TARGET_IMAGES) -> dict[str, str]:
    """?target=images (default) or ?target=job_files. The default keeps
    every existing caller working unchanged while landing them deeper
    than it used to."""
    if target not in _OPEN_TARGETS:
        raise HTTPException(status_code=422, detail=f"Unknown target {target!r}. Expected one of: {', '.join(_OPEN_TARGETS)}.")
    folder = _resolve_open_target(job_name, target)
    os.startfile(str(folder))  # noqa: S606 -- local desktop app, user-triggered, path validated above
    return {"opened": str(folder)}


# -- Manual Mode (Prompt 2) --------------------------------------------------
# Every route below is a direct, synchronous call (like create_job() above),
# not a Controller Job -- these are local file reads/writes with no
# external API call. The Controller never interprets what's copied or
# imported; it only shuttles text/bytes between the browser and the
# engine's own real manual-mode business logic (see
# infra/adapters/image_generator/adapter.py's "Manual Mode" section).


@router.post("/image-generator/jobs/{job_name}/manual/concepts/export")
def export_manual_concepts(job_name: str) -> dict:
    """Prepares this job's manual concept-generation request and returns
    the prompt text + response-JSON template for the "Copy Concepts JSON"
    button to hand to the browser clipboard. No concept is read or
    written by this call."""
    try:
        return _export_manual_concepts(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not export manual concept package: {exc}") from exc


class ImportManualConceptsRequest(BaseModel):
    response_json_text: str


@router.post("/image-generator/jobs/{job_name}/manual/concepts/import")
def import_manual_concepts(job_name: str, payload: ImportManualConceptsRequest) -> dict:
    """Writes payload.response_json_text verbatim to this job's
    manual_concept_response.json and runs the engine's real import --
    the Controller never parses or reshapes it first."""
    try:
        return _import_manual_concepts(job_name, payload.response_json_text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not import manual concepts: {exc}") from exc


@router.get("/image-generator/jobs/{job_name}/manual/prompts")
def list_manual_prompts(job_name: str) -> list[dict]:
    """Every prompt package still eligible for manual (one-at-a-time)
    image generation, for the per-prompt "Copy Prompt" list."""
    try:
        return _list_manual_prompts(job_name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not list manual prompt packages: {exc}") from exc


@router.post("/image-generator/jobs/{job_name}/manual/prompts/{category}/{concept_id}/copy")
def copy_manual_prompt(job_name: str, category: str, concept_id: str) -> dict:
    """Prepares (or refreshes) ONE concept's manual image prompt and
    returns its text for the "Copy Prompt" button -- never bundles every
    prompt together."""
    try:
        return _copy_manual_prompt(job_name, category, concept_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not copy prompt: {exc}") from exc


_ALLOWED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_IMAGE_MAGIC_SIGNATURES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
    b"RIFF",  # WEBP (RIFF....WEBP)
)
_MAX_MANUAL_IMAGE_BYTES = 25 * 1024 * 1024  # 25MB -- generous for a single ChatGPT-downloaded image


def _looks_like_supported_image(filename: str, head: bytes) -> bool:
    if not filename.lower().endswith(_ALLOWED_IMAGE_EXTENSIONS):
        return False
    return any(head.startswith(sig) for sig in _IMAGE_MAGIC_SIGNATURES)


@router.post("/image-generator/jobs/{job_name}/manual/images/{category}/{concept_id}")
async def import_manual_image_upload(job_name: str, category: str, concept_id: str, file: UploadFile = File(...)) -> dict:
    """Stages the uploaded image to a temp file (validated by real
    content, not just extension/filename -- client-supplied multipart
    input is never trusted), then hands only that staged, validated path
    to the engine's real import_manual_images(). Once imported, the
    result is indistinguishable from an OpenAI-generated image to every
    downstream stage (advance_job(), Review Images) -- the Controller
    never records or branches on how the image was produced."""
    content = await file.read()
    if len(content) > _MAX_MANUAL_IMAGE_BYTES:
        raise HTTPException(status_code=422, detail=f"Image too large ({len(content)} bytes, max {_MAX_MANUAL_IMAGE_BYTES}).")
    original_filename = file.filename or "upload.png"
    if not _looks_like_supported_image(original_filename, content[:16]):
        raise HTTPException(status_code=422, detail=f"{original_filename!r} is not a supported image (.png/.jpg/.jpeg/.webp).")

    suffix = Path(original_filename).suffix or ".png"
    staging_dir = Path(tempfile.gettempdir()) / "automation_controller_manual_image_uploads"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / f"{uuid.uuid4()}{suffix}"
    staged_path.write_bytes(content)

    try:
        return _import_manual_image(job_name, category, concept_id, str(staged_path), original_filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not import image: {exc}") from exc
    finally:
        staged_path.unlink(missing_ok=True)

_VALID_MEDIA_CATEGORIES = ("ai_product_mockup", "lifestyle_mockup")


@router.post("/image-generator/jobs/{job_name}/manual/finished-images")
def import_finished_images_upload(
    job_name: str,
    category: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict:
    """Import a batch of already-finished images as this job's deliverables
    and complete the job -- the manual equivalent of the whole OpenAI image
    generation step.

    Every file is validated by real content (not just its extension --
    client-supplied multipart input is never trusted) and staged to a temp
    path BEFORE the engine is called, and the whole batch is rejected if any
    one file fails. A partially-imported batch would leave the job marked
    complete while silently missing deliverables.

    Sync `def`, not `async def`: the engine call runs in a worker subprocess
    and blocks for the length of the import, which on the event loop would
    stall every other request -- see api/video_generator_routes.py's
    launch_video_job() for the full account of that failure mode.
    """
    if category not in _VALID_MEDIA_CATEGORIES:
        raise HTTPException(status_code=422, detail=f"'category' must be one of {list(_VALID_MEDIA_CATEGORIES)}")
    if not files:
        raise HTTPException(status_code=422, detail="Select at least one image to import.")

    staging_dir = Path(tempfile.gettempdir()) / "automation_controller_manual_image_uploads"
    staging_dir.mkdir(parents=True, exist_ok=True)

    staged_paths: list[Path] = []
    errors: list[str] = []
    try:
        for upload in files:
            content = upload.file.read()
            original_filename = upload.filename or "upload.png"
            if len(content) > _MAX_MANUAL_IMAGE_BYTES:
                errors.append(f"{original_filename}: too large ({len(content)} bytes, max {_MAX_MANUAL_IMAGE_BYTES})")
                continue
            if not _looks_like_supported_image(original_filename, content[:16]):
                errors.append(f"{original_filename}: not a supported image (.png/.jpg/.jpeg/.webp)")
                continue
            staged_path = staging_dir / f"{uuid.uuid4()}{Path(original_filename).suffix or '.png'}"
            staged_path.write_bytes(content)
            staged_paths.append(staged_path)

        if errors:
            raise HTTPException(status_code=422, detail={"errors": errors})

        try:
            return _import_finished_images(job_name, [str(p) for p in staged_paths], category)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"Could not import images: {exc}") from exc
    finally:
        for staged_path in staged_paths:
            staged_path.unlink(missing_ok=True)
