"""etsy-video-generator operator workflow routes (Step 12).

Everything in this module is scoped to ONE engine (etsy-video-generator)
and exists purely to give a normal operator a real UI in front of the
same, unchanged execution architecture — JobService.create_job() and
ExecutionCoordinator.evaluate() below are called exactly the way
api/main.py's generic POST /jobs already calls them (that route is
untouched). This module owns three things the generic JSON API
deliberately doesn't: turning a browser file upload into the
`images: list[str]` paths the adapter's config contract already expects
(infra/storage/video_generator_staging.py), human-readable preset
metadata for the UI (GET /video-generator/presets), and safe, narrowly
scoped access to this engine's own output artifacts (video streaming,
"open folder" — never arbitrary filesystem browsing).

Engine independence: nothing here changes infra/adapters/video_generator/
adapter.py or the sibling etsy-video-generator repo. Preset behavior,
timing, transitions, and the generated video remain entirely engine-owned;
this module only stages inputs and reads already-persisted Job results.
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
from infra.adapters.video_generator.adapter import _VALID_PRESET_KEYS  # noqa: PLC2701 — deliberate single source of truth, see module docstring
from infra.storage.video_generator_staging import (
    StagingValidationError,
    cleanup_staging_dir,
    mark_job_id,
    stage_uploaded_images,
    sweep_terminal_staging_dirs,
)

ENGINE_ID = "etsy-video-generator"
REPO_DIR_NAME = "etsy-video-generator"

# Human-readable metadata for the real preset keys, mirrored from the
# engine's own README.md "## Presets" section (etsy-video-generator's
# authoritative, human-facing description of each preset) — not invented
# here. Keys are validated against _VALID_PRESET_KEYS below so this can
# never silently drift from what the adapter actually accepts.
_PRESET_METADATA: list[dict[str, str]] = [
    {
        "key": "standard",
        "label": "Standard",
        "description": "Balanced general-purpose listing video.",
    },
    {
        "key": "slow-fade-color-variation",
        "label": "Slow Fade (Same Mockup, Different Colors)",
        "description": "Best for identical mockups where only the garment color changes.",
    },
    {
        "key": "wisp-sweep-color-variation",
        "label": "Wisp Sweep (Same Mockup, Different Colors)",
        "description": "Premium soft sweep transition for color variations.",
    },
    {
        "key": "design-reveal",
        "label": "Design Reveal",
        "description": "Begins with the artwork before revealing the entire product.",
    },
]
assert {p["key"] for p in _PRESET_METADATA} == _VALID_PRESET_KEYS

router = APIRouter()


@router.get("/video-generator/presets")
def list_video_presets() -> list[dict[str, str]]:
    return _PRESET_METADATA


def _require_video_job(job_id: str, service: JobService):
    job = service.get_job(job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    if job.engine_id != ENGINE_ID:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} does not belong to {ENGINE_ID!r}")
    return job


def _resolve_output_video_path(job) -> Path:
    """The one place a job_id turns into a real filesystem path for
    serving/opening — always derived from that Job's OWN persisted result
    artifact (never a client-supplied path), and always re-checked against
    the engine's real output/ directory before use. This is what keeps
    every route below from being an arbitrary-file-access primitive.
    """
    if job.status != JobStatus.SUCCEEDED or not job.result_summary:
        raise HTTPException(status_code=409, detail="Job has no output yet (not succeeded).")

    raw_path = job.result_summary.get("output_video_path")
    if not raw_path:
        raise HTTPException(status_code=409, detail="Job result has no output_video_path.")

    output_path = Path(raw_path).resolve()
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    engine_output_dir = (repo_root / "output").resolve() if repo_root else None
    if engine_output_dir is None or engine_output_dir not in output_path.parents:
        raise HTTPException(status_code=409, detail="Recorded output path is not inside the engine's output directory.")
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output video file no longer exists on disk.")
    return output_path


@router.get("/jobs/{job_id}/output-video")
def get_output_video(job_id: str, service: JobService = Depends(get_job_service)):
    job = _require_video_job(job_id, service)
    path = _resolve_output_video_path(job)
    return FileResponse(path, media_type="video/mp4", filename=path.name, content_disposition_type="inline")


@router.post("/jobs/{job_id}/open-output-folder")
def open_output_folder(job_id: str, service: JobService = Depends(get_job_service)) -> dict[str, str]:
    """Opens Windows Explorer at the folder containing this Job's actual
    generated video — today, the engine's flat output/ directory. Local,
    single-operator app only; never exposes a listable/browsable API.
    """
    job = _require_video_job(job_id, service)
    folder = _resolve_output_video_path(job).parent
    os.startfile(str(folder))  # noqa: S606 — local desktop app, user-triggered, path validated above
    return {"opened": str(folder)}


@router.post("/jobs/{job_id}/open-listing-outputs")
def open_listing_outputs(job_id: str, service: JobService = Depends(get_job_service)) -> dict[str, str]:
    """Distinct route from open-output-folder on purpose, even though it
    resolves to the SAME folder today: this is the intended future seam
    for a unified per-listing output workspace shared across generators
    (video/image/mockup) once that concept exists. Keeping it as its own
    endpoint now means that future change only touches this function's
    body, not the frontend contract or open_output_folder() above.
    """
    job = _require_video_job(job_id, service)
    folder = _resolve_output_video_path(job).parent
    os.startfile(str(folder))  # noqa: S606
    return {"opened": str(folder)}


@router.post("/video-generator/jobs")
def launch_video_job(
    preset_key: str = Form(...),
    images: list[UploadFile] = File(...),
    design_id: str | None = Form(None),
    job_service: JobService = Depends(get_job_service),
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
):
    """Multipart counterpart to POST /jobs, scoped to this one engine.

    Stages the uploaded images (in the order the browser sent them —
    preserving whatever order the operator arranged in the UI), then calls
    the exact same JobService.create_job() + ExecutionCoordinator.evaluate()
    pair api/main.py's generic POST /jobs uses. No orchestration behavior
    is duplicated or bypassed — this is purely "how the config's `images`
    paths get onto disk before the generic path takes over."

    Deliberately a SYNC `def`, not `async def` — do not "modernize" this.
    coordinator.evaluate() below blocks for the entire FFmpeg render (~8-30s;
    up to the adapter's 120s timeout). FastAPI runs a sync handler in a
    threadpool, but an `async def` handler runs ON the event loop, where a
    blocking call freezes EVERY other request for its whole duration.

    That was a real, reproducible bug, not a theoretical one: while a video
    rendered, the Web UI's polling (and /health) got no answer at all and
    the operator saw "Request timed out"/"API Unreachable" during a job that
    was in fact succeeding normally. Measured before the fix, /health went
    from 0ms idle to a full 8s client timeout mid-render; after, it stays
    at ~0ms throughout. Same reason every other route in this codebase is
    a sync `def`.
    """
    sweep_terminal_staging_dirs(job_service.get_job)

    if preset_key not in _VALID_PRESET_KEYS:
        raise HTTPException(status_code=422, detail=f"'preset_key' must be one of {sorted(_VALID_PRESET_KEYS)}")

    # upload.file.read() (sync) rather than await upload.read(): the same
    # bytes off the same SpooledTemporaryFile, readable from the threadpool
    # thread this handler already runs on.
    uploads: list[tuple[str, bytes]] = []
    for upload in images:
        uploads.append((upload.filename or "image", upload.file.read()))

    try:
        staging_dir, staged_paths = stage_uploaded_images(uploads)
    except StagingValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc

    # design_id is Controller-side listing metadata, exactly as it is for the
    # Mockup Generator (api/mockup_generator_routes.py's preview launch): it
    # ties this video to the design it was made for, so the Jobs list can
    # identify it and Listing Assets can find it by the same Design ID that
    # finds that design's mockups and AI images. The engine never sees it --
    # the adapter reads only images/preset_key (its validate() ignores any
    # other key), so nothing about video generation itself changes.
    config: dict = {"images": [str(p) for p in staged_paths], "preset_key": preset_key}
    if design_id and design_id.strip():
        config["design_id"] = design_id.strip()

    try:
        job = job_service.create_job(engine_id=ENGINE_ID, config=config)
    except JobValidationError as exc:
        cleanup_staging_dir(staging_dir)
        raise HTTPException(status_code=422, detail={"engine_id": exc.engine_id, "errors": exc.errors}) from exc
    except Exception:
        cleanup_staging_dir(staging_dir)
        raise

    mark_job_id(staging_dir, job.id)

    try:
        coordinator.evaluate(job.engine_id)
    finally:
        final_job = job_service.get_job(job.id)
        if final_job is not None and final_job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            cleanup_staging_dir(staging_dir)
        # else: left QUEUED/RUNNING by a concurrent launch — the staging
        # directory is needed until whatever later evaluate() call finishes
        # it; the next launch's sweep_terminal_staging_dirs() call cleans
        # it up once that happens (see infra/storage/video_generator_staging.py).

    return job_service.get_job(job.id)
