"""Controller-owned staging for etsy-video-generator image uploads.

Design decision (Step 12, "operator workflow" pass): the browser never
sends file *paths* — it uploads the actual bytes the user selected/
dropped, in their final chosen order, only at Launch time (nothing is
staged merely because a file was picked in the UI). This module is where
those bytes land on disk, in a Controller-owned location the video
generator adapter's existing `images: list[str]` config contract already
expects (see infra/adapters/video_generator/adapter.py — unchanged by
this module; it still just receives absolute file paths, with no idea
they came from an HTTP upload rather than a hand-typed path).

Lifecycle: one staging directory per launch attempt, named by a fresh
uuid4 (NOT the eventual Job id, which doesn't exist yet when files are
written — see stage_uploaded_images()). Once a Job is successfully
created for that directory, a `job_id.txt` marker is written into it
(see mark_job_id()) so a later, unrelated request can find out which Job
"owns" an otherwise-anonymous staging directory and check whether it's
safe to delete yet (see sweep_terminal_staging_dirs()).

Cleanup happens two ways, both in api/video_generator_routes.py:
  1. Inline, same-request: after the Job this staging directory was
     created for reaches a terminal status (normally true by the time
     the request returns, since ExecutionCoordinator.evaluate() runs
     synchronously — see coordinator.py), the directory is removed
     immediately. On SUCCEEDED, the engine's own run_video_generation()
     has already *moved* the staged files into its processed-inputs/
     folder as its last step (verified by reading
     etsy-video-generator/src/generate_video.py directly) — so this is
     usually just removing an already-empty directory. On
     FAILED/TIMED_OUT/CANCELLED, the staged copies are still there and
     this is a real cleanup.
  2. Opportunistic, next-request: if a Job was left QUEUED (a second
     launch fired while one was still running — max_concurrent_runs=1
     for this engine, so this is a real possible race), its staging
     directory can't be cleaned up in that same request since the files
     are still needed by whatever later evaluate() call actually runs
     it. sweep_terminal_staging_dirs() is called at the START of every
     new video-generator launch request and removes any staging
     directory whose owning Job has since reached a terminal status (or
     whose owning Job no longer exists at all, e.g. deleted).

Never mutates or deletes the user's original files — everything here
operates only on Controller-owned copies under STAGING_ROOT.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.domain.job import Job

# automation-controller/var/staging/video_generator — sibling of the
# project's own automation_controller.db, never inside any engine repo.
STAGING_ROOT = Path(__file__).resolve().parents[2] / "var" / "staging" / "video_generator"

MIN_IMAGES = 3
MAX_IMAGES = 5

_ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg")
_MAGIC_SIGNATURES: tuple[tuple[bytes, ...], ...] = (
    (b"\x89PNG\r\n\x1a\n",),  # PNG
    (b"\xff\xd8\xff",),  # JPEG
)
# A marker-less staging directory this old is either an orphan from a
# request that crashed before writing job_id.txt, or (harmlessly) one
# currently being written to — 5 minutes is generous enough to never
# race a real in-flight upload.
_ORPHAN_GRACE_SECONDS = 5 * 60

_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class StagingValidationError(Exception):
    """Raised for any problem with the uploaded batch itself — wrong count,
    unsupported format, unreadable bytes — before any Job is created.
    Carries per-item messages, mirroring JobValidationError's shape
    (api/main.py already knows how to render `errors: list[str]`).
    """

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _sanitize_filename(original_name: str) -> str:
    """Strip any directory components and disallowed characters from a
    client-supplied filename before it ever touches the filesystem —
    multipart filenames are attacker-controlled input, not trusted paths.
    """
    base = Path(original_name or "image").name
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in base)
    return safe or "image"


def _looks_like_supported_image(filename: str, head: bytes) -> bool:
    if not filename.lower().endswith(_ALLOWED_EXTENSIONS):
        return False
    return any(head.startswith(sig) for group in _MAGIC_SIGNATURES for sig in group)


def stage_uploaded_images(uploads: list[tuple[str, bytes]]) -> tuple[Path, list[Path]]:
    """Validate and write an ordered batch of (filename, bytes) uploads into
    a brand-new staging directory. Returns (staging_dir, staged_paths) with
    staged_paths in the exact same order as `uploads` — order preservation
    end-to-end is the whole point (see adapter's `images` contract: "in
    display order").

    Raises StagingValidationError (no directory left behind) if the count
    is out of range or any file isn't a real, supported image. Every file
    is fully validated before anything is written to disk, so a batch
    either stages completely or not at all.
    """
    errors: list[str] = []
    if not (MIN_IMAGES <= len(uploads) <= MAX_IMAGES):
        errors.append(f"expected {MIN_IMAGES} to {MAX_IMAGES} images, got {len(uploads)}")
    for filename, content in uploads:
        if not _looks_like_supported_image(filename, content[:16]):
            errors.append(f"{filename!r} is not a supported image (need .png/.jpg/.jpeg with matching content)")
    if errors:
        raise StagingValidationError(errors)

    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    staging_dir = STAGING_ROOT / str(uuid.uuid4())
    staging_dir.mkdir(parents=True, exist_ok=False)

    staged_paths: list[Path] = []
    for index, (filename, content) in enumerate(uploads, start=1):
        dest = staging_dir / f"{index:02d}_{_sanitize_filename(filename)}"
        dest.write_bytes(content)
        staged_paths.append(dest)

    return staging_dir, staged_paths


def mark_job_id(staging_dir: Path, job_id: str) -> None:
    (staging_dir / "job_id.txt").write_text(job_id, encoding="utf-8")


def cleanup_staging_dir(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir, ignore_errors=True)


def sweep_terminal_staging_dirs(get_job) -> None:
    """Called at the start of every new launch request. `get_job` is
    JobService.get_job — takes a job_id, returns Job | None. Removes any
    staging directory whose owning Job is terminal, deleted, or (as a
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
        job: Job | None = get_job(job_id)
        if job is None or job.status.value in _TERMINAL_STATUSES:
            cleanup_staging_dir(entry)
