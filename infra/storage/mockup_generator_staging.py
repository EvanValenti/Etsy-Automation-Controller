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
# Deliberately NOT "succeeded" -- unlike video_generator_staging.py (whose
# engine moves/consumes the staged files itself as the last step of a
# successful run), a succeeded MOCKUP PREVIEW job's staged zip_path must
# stay on disk: it's exactly what a later "choose a different background"
# rerun (POST .../jobs/preview with staged_zip_path) needs to reference.
# Sweeping it here the moment the FIRST preview succeeds broke that flow
# (caught live: "staged_zip_path no longer exists"). A succeeded preview's
# staging dir is instead cleaned up explicitly, once it's actually done
# being needed -- see api/mockup_generator_routes.py::launch_batch_job().
_TERMINAL_STATUSES = {"failed", "cancelled"}


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


def cleanup_staged_zip_if_owned(zip_path: str) -> None:
    """Clean up a succeeded preview Job's staged zip once it's genuinely
    done being needed (called from launch_batch_job() once the batch
    phase no longer needs to reference it -- headless.generate_full_batch()
    only reads zip_path from its own already-persisted run state, not
    from this staged copy again). Only ever removes a directory that is
    actually a direct child of STAGING_ROOT -- a no-op for a zip_path that
    was never staged (e.g. one the operator hand-typed via the JSON API),
    never a generic "delete this path's parent" primitive.
    """
    parent = Path(zip_path).resolve().parent
    if parent.parent == STAGING_ROOT.resolve():
        cleanup_staging_dir(parent)


def sweep_terminal_staging_dirs(get_job) -> None:
    """Called at the start of every new preview-launch request. `get_job`
    is JobService.get_job -- takes a job_id, returns Job | None. Removes
    any staging directory whose owning Job is terminal, deleted, or (as a
    last-resort fallback) has no job_id.txt marker at all and is old
    enough to be an orphan rather than a request currently mid-upload.
    """
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
