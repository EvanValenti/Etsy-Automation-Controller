"""Cleanup APIs -- a Controller-owned lifecycle feature, not an engine.

The Controller owns workflow lifecycle (when a Job's record and its own
Controller-side artifacts go away); the generators own asset creation
(their code, templates, and reusable backgrounds are never touched here).
Safety over convenience: every delete here refuses an active Job outright,
and every filesystem removal goes through the Windows Recycle Bin via
send2trash -- nothing in this module ever permanently deletes a file.

Two concrete operations exist today, both thin wrappers around
recycle_path() plus a bit of per-engine path resolution:
  - delete_job(): recycles a terminal Job's own generated output (never a
    shared/template/background path -- resolved the same validated way
    ListingWorkspaceBuilder resolves sources) plus any Controller staging
    directory still owned by that Job, then removes the Job row.
  - delete_listing_workspace(): recycles a built workspace folder. Always
    safe -- ListingWorkspaceBuilder only ever COPIES into that folder, so
    the generators' real outputs are untouched no matter what.

Both are deliberately generic enough for a future Storage Manager to reuse
without changes: recycle_path() is the one primitive any "delete temp
files" / "cleanup old jobs" feature would also call, and delete_job()'s
per-engine path resolution is the one place that logic needs to live.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import send2trash

from core.domain.job import Job, JobStatus
from core.services.job_service import JobNotFoundError, JobService
from infra.adapters.discovery_utils import resolve_engine_repo_root
from infra.listing_workspace import ListingWorkspaceBuilder, manifest_path_for as listing_workspace_manifest_path
from infra.storage import mockup_generator_staging, video_generator_staging

VIDEO_REPO_DIR_NAME = "etsy-video-generator"
MOCKUP_REPO_DIR_NAME = "etsy-mockup-generator"
IMAGE_REPO_DIR_NAME = "Etsy-AI-Image-Generator"

# A Job in any of these statuses is still active work -- deleting it would
# either orphan an in-flight engine process or silently discard a pending
# approval. Only a terminal Job is ever a valid delete target.
_ACTIVE_STATUSES = {
    JobStatus.PENDING,
    JobStatus.VALIDATED,
    JobStatus.QUEUED,
    JobStatus.RUNNING,
    JobStatus.WAITING_ON_APPROVAL,
}


class CleanupError(Exception):
    """A genuine failure that isn't "not found" -- e.g. a Job that's still
    active, or a listing_id that's invalid. Routes map this to 409 rather
    than 500. "Not found" cases raise the existing JobNotFoundError /
    ListingWorkspaceError instead, so callers keep getting the same 404
    they already handle."""


def recycle_path(path: Path) -> None:
    """Send one file or directory to the Windows Recycle Bin. No-op if the
    path doesn't already exist -- "nothing to clean up" is not an error.
    """
    if not path.exists():
        return
    send2trash.send2trash(str(path))


def _resolve_job_output_paths(job: Job) -> list[Path]:
    """The real filesystem paths that make up THIS job's own generated
    output -- never a shared resource, template, or reusable background.
    Every path is resolved from the Job's own persisted result_summary and
    validated to stay inside the owning engine's repo before being
    returned, mirroring ListingWorkspaceBuilder's source resolution.
    Returns an empty list if the job never produced output (e.g. it
    failed before generating anything).
    """
    summary = job.result_summary or {}
    paths: list[Path] = []

    if job.engine_id == "etsy-video-generator":
        repo_root = resolve_engine_repo_root(VIDEO_REPO_DIR_NAME)
        if repo_root is not None:
            candidates = [summary.get(key) for key in ("output_video_path", "manifest_path")]
            # Jobs that completed before the engine moved its manifests out
            # of output/ recorded a manifest_path that no longer exists
            # (their .json was relocated to metadata/ with the rest). Also
            # offering the current location, derived from the video's own
            # name, is what keeps "delete this job" from leaving an orphaned
            # metadata file behind for those older jobs. A path that isn't
            # actually there is skipped by the is_file() check below, so
            # naming both locations is safe either way.
            video_raw = summary.get("output_video_path")
            if video_raw:
                candidates.append(str(repo_root / "metadata" / f"{Path(video_raw).stem}.json"))
            for raw in candidates:
                if not raw:
                    continue
                candidate = Path(raw).resolve()
                if repo_root.resolve() in candidate.parents and candidate.is_file():
                    paths.append(candidate)

    elif job.engine_id == "etsy-mockup-generator":
        repo_root = resolve_engine_repo_root(MOCKUP_REPO_DIR_NAME)
        raw = summary.get("run_dir")
        if repo_root is not None and raw:
            candidate = Path(raw).resolve()
            if (repo_root / "output").resolve() in candidate.parents and candidate.is_dir():
                paths.append(candidate)

    elif job.engine_id == "etsy-ai-image-generator":
        repo_root = resolve_engine_repo_root(IMAGE_REPO_DIR_NAME)
        job_name = (job.config or {}).get("job_name")
        if repo_root is not None and job_name:
            jobs_dir = (repo_root / "jobs").resolve()
            candidate = (jobs_dir / job_name).resolve()
            if jobs_dir in candidate.parents and candidate.is_dir():
                paths.append(candidate)

    return paths


def _other_jobs_reference_path(job_service: JobService, job: Job, path: Path) -> bool:
    """True if some OTHER Job for the same engine still resolves to this
    exact output path. Real, observed case: repeated "advance" calls
    against one AI Image Generator job_name create a fresh Controller Job
    row each time, but they all resolve to the SAME job_folder; the same
    thing happens for etsy-mockup-generator when a batch is re-launched
    against an existing run_dir. Deleting one such Job must never recycle
    a path another still-existing Job depends on.
    """
    for other in job_service.list_jobs(engine_id=job.engine_id):
        if other.id == job.id:
            continue
        if path in _resolve_job_output_paths(other):
            return True
    return False


def _find_owned_staging_dir(job_id: str) -> Path | None:
    """A Controller-owned upload-staging directory (video/mockup launch
    uploads) still marked with this Job's id, if one exists. Normally
    already cleaned up by the same-request/opportunistic sweeps in
    api/video_generator_routes.py and api/mockup_generator_routes.py --
    this is the backstop for whatever those sweeps haven't caught yet.
    """
    for staging_root in (video_generator_staging.STAGING_ROOT, mockup_generator_staging.STAGING_ROOT):
        if not staging_root.is_dir():
            continue
        for entry in staging_root.iterdir():
            marker = entry / "job_id.txt"
            if marker.is_file() and marker.read_text(encoding="utf-8").strip() == job_id:
                return entry
    return None


def delete_job(job_service: JobService, job_id: str) -> dict[str, Any]:
    """Delete a Job: recycle its own generated output (if any) and any
    Controller staging directory still owned by it, then remove the Job
    row. Refuses outright if the Job is still active -- see
    _ACTIVE_STATUSES -- so an in-flight or pending-approval Job can never
    be accidentally deleted.

    Before recycling any output path, checks whether another still-
    existing Job for the same engine resolves to that same path (see
    _other_jobs_reference_path) and skips recycling it if so -- the path
    is reported back under "skipped_paths", not "recycled_paths". This is
    a real, observed situation (not theoretical): e.g. re-running "advance"
    against one AI Image Generator job_name creates a new Controller Job
    row each time, but every one of them points at the same job folder.
    """
    job = job_service.get_job(job_id)
    if job is None:
        raise JobNotFoundError(job_id)
    if job.status in _ACTIVE_STATUSES:
        raise CleanupError(f"Job {job_id} is still {job.status.value} -- cancel it before deleting.")

    recycled: list[str] = []
    skipped: list[str] = []
    for path in _resolve_job_output_paths(job):
        if _other_jobs_reference_path(job_service, job, path):
            skipped.append(str(path))
            continue
        recycle_path(path)
        recycled.append(str(path))

    staging_dir = _find_owned_staging_dir(job_id)
    if staging_dir is not None:
        recycle_path(staging_dir)
        recycled.append(str(staging_dir))

    job_service.delete_job(job_id)
    return {"job_id": job_id, "recycled_paths": recycled, "skipped_paths": skipped}


def delete_listing_workspace(builder: ListingWorkspaceBuilder, listing_id: str) -> dict[str, Any]:
    """Recycle a built Listing Workspace folder. Always safe regardless of
    what it contains -- ListingWorkspaceBuilder only ever COPIES assets
    into this folder (see its module docstring), so the generators' real
    outputs are never at risk here. Raises ListingWorkspaceError (already
    mapped to 404/422 by api/listing_workspace_routes.py) if the
    listing_id is invalid or was never built -- not wrapped in
    CleanupError, since that's a "not found" case, not an active-work
    conflict.
    """
    folder = builder.workspace_folder(listing_id)  # raises ListingWorkspaceError
    recycle_path(folder)
    # The manifest lives outside the workspace folder now (it is Controller
    # bookkeeping, not an operator deliverable), so recycling the folder no
    # longer takes it with it -- without this, a deleted workspace would keep
    # appearing in list_workspaces(), which reads the manifests.
    manifest_path = listing_workspace_manifest_path(folder.name)
    recycle_path(manifest_path)
    return {"listing_id": folder.name, "recycled_path": str(folder)}
