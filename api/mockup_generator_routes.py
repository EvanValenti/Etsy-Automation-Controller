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
    cleanup_staged_zip_if_owned,
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


def _resolve_assets_dir(job) -> Path:
    """The validated generated-assets directory (run_dir/assets) --
    what operator-facing "open folder" actions should actually open.
    run_dir itself only contains assets/ and manifest.json (see
    batch_generate.py's execute_full_batch()/headless.py's
    generate_full_batch(), both of which already compute and return an
    absolute assets_dir alongside run_dir) -- opening run_dir just makes
    the operator click through one more folder to reach the actual
    generated mockups every single time.

    Derived entirely from the engine's own persisted result_summary
    (assets_dir, computed server-side in
    infra/adapters/mockup_generator/adapter.py::launch() and never
    accepted from the client), then re-validated against the engine's
    real repo root exactly like every other artifact path in this file --
    never trusted blindly.
    """
    if job.status != JobStatus.SUCCEEDED or not job.result_summary or job.result_summary.get("phase") != "batch":
        raise HTTPException(status_code=409, detail="Job has no output yet (not a succeeded batch job).")
    assets_dir = job.result_summary.get("assets_dir")
    if not assets_dir:
        raise HTTPException(status_code=409, detail="Batch job result has no assets_dir.")
    path = Path(assets_dir).resolve()
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    engine_output_dir = (repo_root / "output").resolve() if repo_root else None
    if engine_output_dir is None or engine_output_dir not in path.parents:
        raise HTTPException(status_code=409, detail="Recorded assets_dir is not inside the engine's output directory.")
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Assets folder no longer exists on disk.")
    return path


@router.get("/mockup-generator/jobs/{job_id}/assets/{filename}/file")
def get_mockup_asset_file(job_id: str, filename: str, service: JobService = Depends(get_job_service)):
    """One generated mockup's bytes, by filename within that job's own
    validated assets/ directory -- what lets the Listing Assets page show
    and select individual mockups instead of one thumbnail per batch.

    `filename` is re-resolved and re-validated against the assets folder
    before use rather than trusted as-is, the same path-safety pattern
    get_reference_image_file() and the listing workspace asset route use.
    Only a file this job actually generated can ever be served.
    """
    job = _require_mockup_job(job_id, service)
    assets_dir = _resolve_assets_dir(job)
    candidate = (assets_dir / filename).resolve()
    if assets_dir.resolve() not in candidate.parents or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Asset not found for this job: {filename!r}")
    return FileResponse(candidate)


@router.post("/mockup-generator/jobs/{job_id}/open-output-folder")
def open_mockup_output_folder(job_id: str, service: JobService = Depends(get_job_service)) -> dict[str, str]:
    """Deliberately NOT /jobs/{job_id}/open-output-folder -- that exact
    path is already claimed by video_generator_routes.py's own route of
    the same name. FastAPI resolves routes by path+method only, with no
    concept of "the right router for this job's engine" -- two routers
    registering the identical path means the FIRST-included router's
    handler always wins, silently swallowing every other engine's
    request. Caught live: clicking this button returned "Job ... does not
    belong to 'etsy-video-generator'", from VIDEO's handler, not this
    one. Every route here must be uniquely prefixed per engine; there is
    no generic, shared /jobs/{id}/... action route in this API today.

    Opens assets/ (the validated generated-assets directory), not the
    run root -- see _resolve_assets_dir()'s docstring.
    """
    job = _require_mockup_job(job_id, service)
    folder = _resolve_assets_dir(job)
    os.startfile(str(folder))  # noqa: S606 -- local desktop app, user-triggered, path validated above
    return {"opened": str(folder)}


@router.post("/mockup-generator/jobs/{job_id}/open-listing-outputs")
def open_mockup_listing_outputs(job_id: str, service: JobService = Depends(get_job_service)) -> dict[str, str]:
    """V1: identical destination to open-output-folder above (this engine
    has no separate "current listing" concept yet) -- also opens assets/,
    not the run root, for the same reason.
    """
    job = _require_mockup_job(job_id, service)
    folder = _resolve_assets_dir(job)
    os.startfile(str(folder))  # noqa: S606
    return {"opened": str(folder)}


@router.post("/mockup-generator/jobs/preview")
def launch_preview_job(
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

    Deliberately a SYNC `def`, not `async def` — see the same note on
    api/video_generator_routes.py's launch_video_job(). coordinator.evaluate()
    below blocks for the whole preview generation, and on the event loop that
    would freeze every other request (the Web UI reports "Request timed
    out"/"API Unreachable" mid-job). Found while fixing the video route: this
    is the same defect in the same shape, the only other `async def` launch
    route. launch_batch_job() below was already sync and never had it.
    """
    sweep_terminal_staging_dirs(job_service.get_job)

    staging_dir = None
    if zip_file is not None:
        content = zip_file.file.read()
        try:
            staging_dir, zip_path = stage_uploaded_zip(zip_file.filename or "upload.zip", content)
        except StagingValidationError as exc:
            raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc
    elif staged_zip_path:
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
            JobStatus.FAILED, JobStatus.CANCELLED,
        }:
            # A succeeded preview Job still needs its staged zip_path for
            # the LATER batch phase (and for a possible "choose a
            # different background" rerun) -- never clean up here on
            # success, only on failure/cancellation. Success cleanup
            # happens opportunistically via sweep_terminal_staging_dirs()
            # once the eventual batch Job (or an abandoned rerun) reaches
            # a terminal status.
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
    final_job = job_service.get_job(job.id)

    # The preview job's staged zip_path is no longer needed once its
    # batch phase has been launched (successfully or not) -- the engine
    # already extracted/archived its own copy during the preview phase,
    # and generate_full_batch() never re-reads this staged path. See
    # mockup_generator_staging.py::cleanup_staged_zip_if_owned() for why
    # this can't just happen at sweep time for a succeeded preview job.
    zip_path = preview_job.config.get("zip_path")
    if zip_path:
        cleanup_staged_zip_if_owned(zip_path)

    return final_job
