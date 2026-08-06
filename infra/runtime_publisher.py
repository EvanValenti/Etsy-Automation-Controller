"""Publishes a completed Job's stable artifacts into the shared runtime
root (see infra/runtime_root.py) so another machine sharing that root
(via OneDrive or any other synced folder) can see the same finished
work.

ARCHITECTURE
Generators are not touched by this module at all -- it only ever reads
data the Controller already has (a Job's result_summary, already-computed
JobMetrics) and copies already-finished files the generator already wrote
to its own local output tree. This is "(1) Controller publishes completed
generator outputs into the shared runtime root" from the design brief,
the least-coupled of the three options offered there. If a second engine
needs this later, it gets its own _EXTRACTORS entry (see below) -- no
change to the atomic-publish/dedup machinery.

WHY MOCKUP GENERATOR ONLY, FOR NOW
Its own versioned manifest.json (manifest_version, run_succeeded) is
already the authoritative, engine-owned completion signal -- exactly what
the design brief asks this module to key off of. Wiring in the AI Image
Generator or Video Generator is a mechanical repeat of the same pattern
once needed; deferred here to keep this a "smallest clean implementation
path" pass rather than broader unrelated work.

ATOMIC PUBLISH
    1. Copy everything into jobs/<engine_id>/<job_key>.tmp-<uuid>/
    2. Copy assets into approved-assets/, a preview into previews/,
       write the per-job metrics record into metrics/
    3. Verify every file that was supposed to be copied actually exists
       at its destination with the expected size
    4. Rename the temp job directory to its final name and write
       COMPLETE inside it LAST
A reader (infra/runtime_index.py) only ever trusts a jobs/<engine_id>/<key>/
directory that contains a COMPLETE file -- a directory without one is
either mid-publish or was interrupted, and is treated as not-yet-published,
never as a broken job.

DEDUPLICATION / CONFLICTS
job_key is the engine's own per-workflow identifier (e.g. the Mockup
Generator's run_id) plus this machine's id (infra.runtime_root.machine_id()),
since run_id alone is only sequential-per-machine-per-day (see
batch_generate.py's make_run_id()) and two machines can produce the same
one on the same day. If jobs/<engine_id>/<key>/COMPLETE already exists:
  - same manifest content (by hash)  -> ALREADY_PUBLISHED, no-op
  - different manifest content       -> CONFLICT, nothing is overwritten;
    the new attempt is written to a clearly-suffixed sibling directory
    instead so both are preserved for a human to reconcile
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from core.domain.job import Job, JobStatus
from core.services.monitoring_service import MonitoringService
from infra.lifetime_metrics import MINUTES_SAVED_PER_COMPLETED_WORKFLOW
from infra.runtime_root import machine_id, resolve_runtime_root

SCHEMA_VERSION = 1


class PublishStatus(str, Enum):
    PUBLISHED = "published"
    ALREADY_PUBLISHED = "already_published"
    CONFLICT = "conflict"
    NOT_SUCCEEDED = "not_succeeded"
    UNSUPPORTED_ENGINE = "unsupported_engine"
    RUN_FAILED = "run_failed"


@dataclass
class PublishResult:
    status: PublishStatus
    job_key: str | None = None
    path: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "job_key": self.job_key, "path": self.path, "detail": self.detail}


@dataclass
class _Extraction:
    """What publish_job() needs from one engine's result_summary -- the
    one place engine-specific field names are read, kept separate from
    the atomic-publish mechanics below."""

    job_key: str
    run_succeeded: bool
    manifest_path: Path
    asset_paths: list[Path]
    assets_generated: int
    errors: list[str]


def _extract_mockup(job: Job) -> _Extraction | None:
    result = job.result_summary or {}
    if result.get("phase") != "batch":
        return None  # a preview-phase Job has no run_succeeded/manifest of its own
    manifest = result.get("manifest") or {}
    run_id = result.get("run_id") or manifest.get("run_id")
    if not run_id:
        return None
    manifest_path = result.get("manifest_path")
    assets_dir = result.get("assets_dir")
    asset_entries = manifest.get("assets") or []
    asset_paths = [Path(assets_dir) / entry["filename"] for entry in asset_entries if assets_dir and entry.get("filename")]
    return _Extraction(
        job_key=f"{run_id}__{machine_id()}",
        run_succeeded=bool(result.get("run_succeeded")),
        manifest_path=Path(manifest_path) if manifest_path else Path(),
        asset_paths=asset_paths,
        assets_generated=sum((manifest.get("output_counts") or {}).values()),
        errors=list(manifest.get("errors") or []),
    )


# Engine id -> extractor. Add a new engine here (and nowhere else) to
# support publishing its completed jobs.
_EXTRACTORS: dict[str, Callable[[Job], "_Extraction | None"]] = {
    "etsy-mockup-generator": _extract_mockup,
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RuntimePublisher:
    def __init__(self, job_service, monitoring_service: MonitoringService, runtime_root: Path | None = None) -> None:
        self._jobs = job_service
        self._monitoring = monitoring_service
        self._root = runtime_root if runtime_root is not None else resolve_runtime_root()

    def publish_job(self, job_id: str) -> PublishResult:
        job = self._jobs.get_job(job_id)
        if job is None:
            return PublishResult(PublishStatus.NOT_SUCCEEDED, detail=f"Job not found: {job_id}")
        if job.status != JobStatus.SUCCEEDED:
            return PublishResult(PublishStatus.NOT_SUCCEEDED, detail=f"Job status is {job.status.value}, not succeeded.")

        extractor = _EXTRACTORS.get(job.engine_id)
        if extractor is None:
            return PublishResult(PublishStatus.UNSUPPORTED_ENGINE, detail=f"No shared-runtime publisher for engine {job.engine_id!r} yet.")

        extraction = extractor(job)
        if extraction is None:
            return PublishResult(PublishStatus.NOT_SUCCEEDED, detail="This Job has no completed-run artifacts to publish (e.g. a preview-phase Job).")
        if not extraction.run_succeeded:
            return PublishResult(
                PublishStatus.RUN_FAILED,
                job_key=extraction.job_key,
                detail="The engine's own manifest reports run_succeeded=false; not publishing a failed run.",
            )
        if not extraction.manifest_path.is_file():
            return PublishResult(PublishStatus.NOT_SUCCEEDED, detail=f"Manifest file is missing on disk: {extraction.manifest_path}")

        return self._publish(job, extraction)

    # -- Atomic publish ------------------------------------------------------

    def _publish(self, job: Job, extraction: _Extraction) -> PublishResult:
        engine_dir = self._root / "jobs" / job.engine_id
        final_dir = engine_dir / extraction.job_key
        complete_marker = final_dir / "COMPLETE"

        manifest_bytes = extraction.manifest_path.read_bytes()
        new_hash = hashlib.sha256(manifest_bytes).hexdigest()

        if complete_marker.is_file():
            existing_manifest = final_dir / "manifest.json"
            try:
                existing_hash = _sha256_file(existing_manifest) if existing_manifest.is_file() else None
            except OSError:
                existing_hash = None  # unreadable (e.g. still syncing) -- treat as unknown, not a match
            if existing_hash == new_hash:
                return PublishResult(PublishStatus.ALREADY_PUBLISHED, job_key=extraction.job_key, path=str(final_dir))
            conflict_dir = engine_dir / f"{extraction.job_key}__conflict-{uuid.uuid4().hex[:8]}"
            self._write_job_dir(conflict_dir, job, extraction, manifest_bytes)
            return PublishResult(
                PublishStatus.CONFLICT,
                job_key=extraction.job_key,
                path=str(conflict_dir),
                detail=(
                    f"jobs/{job.engine_id}/{extraction.job_key} already exists with different content. "
                    f"This attempt was preserved separately at {conflict_dir} rather than overwriting it."
                ),
            )

        tmp_dir = engine_dir / f"{extraction.job_key}.tmp-{uuid.uuid4().hex[:8]}"
        self._write_job_dir(tmp_dir, job, extraction, manifest_bytes, write_complete_marker=False)

        # Verify before publishing: every file the manifest promises must
        # actually be present at its destination with a matching size --
        # an OneDrive-not-fully-synced source file must never result in a
        # job that LOOKS complete but is silently missing assets.
        ok, problem = self._verify(tmp_dir, extraction)
        if not ok:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return PublishResult(PublishStatus.NOT_SUCCEEDED, job_key=extraction.job_key, detail=f"Publish verification failed: {problem}")

        final_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir.rename(final_dir)
        (final_dir / "COMPLETE").write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")

        self._write_metrics_record(job, extraction)
        return PublishResult(PublishStatus.PUBLISHED, job_key=extraction.job_key, path=str(final_dir))

    def _write_job_dir(
        self, target_dir: Path, job: Job, extraction: _Extraction, manifest_bytes: bytes, write_complete_marker: bool = True,
    ) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "manifest.json").write_bytes(manifest_bytes)

        completed_at = (job.updated_at or job.created_at) or datetime.now(timezone.utc)
        status = {
            "schema_version": SCHEMA_VERSION,
            "job_id": extraction.job_key,
            "controller_job_id": job.id,
            "engine_id": job.engine_id,
            "completed_at": completed_at.isoformat(),
            "run_succeeded": extraction.run_succeeded,
            "published_by_machine": machine_id(),
            "errors": extraction.errors,
        }
        (target_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")

        assets_dest = self._root / "approved-assets" / job.engine_id / extraction.job_key
        assets_dest.mkdir(parents=True, exist_ok=True)
        for src in extraction.asset_paths:
            if src.is_file():
                shutil.copy2(src, assets_dest / src.name)

        if extraction.asset_paths:
            preview_dest = self._root / "previews" / job.engine_id / extraction.job_key
            preview_dest.mkdir(parents=True, exist_ok=True)
            representative = extraction.asset_paths[0]
            if representative.is_file():
                shutil.copy2(representative, preview_dest / representative.name)

        if write_complete_marker:
            (target_dir / "COMPLETE").write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")

    def _verify(self, job_dir: Path, extraction: _Extraction) -> tuple[bool, str | None]:
        if not (job_dir / "manifest.json").is_file():
            return False, "manifest.json missing at destination"
        if not (job_dir / "status.json").is_file():
            return False, "status.json missing at destination"
        assets_dest = self._root / "approved-assets" / job_dir.parent.name / extraction.job_key
        for src in extraction.asset_paths:
            dest = assets_dest / src.name
            if not dest.is_file():
                return False, f"asset {src.name} did not copy to {dest}"
            if src.is_file() and dest.stat().st_size != src.stat().st_size:
                return False, f"asset {src.name} size mismatch after copy"
        return True, None

    def _write_metrics_record(self, job: Job, extraction: _Extraction) -> None:
        automation_seconds = None
        try:
            metrics = self._monitoring.get_job_metrics(job.id)
            automation_seconds = metrics.total_execution_seconds
        except Exception:  # noqa: BLE001 -- metrics are best-effort; never block a publish over them
            automation_seconds = None

        minutes_saved = MINUTES_SAVED_PER_COMPLETED_WORKFLOW.get(job.engine_id, 0)
        automation_minutes = (automation_seconds or 0) / 60
        completed_at = (job.updated_at or job.created_at) or datetime.now(timezone.utc)

        record = {
            "schema_version": SCHEMA_VERSION,
            "job_id": extraction.job_key,
            "module": job.engine_id,
            "completed_at": completed_at.isoformat(),
            "run_succeeded": extraction.run_succeeded,
            "assets_generated": extraction.assets_generated,
            "automation_seconds": automation_seconds,
            "estimated_manual_minutes": round(automation_minutes + minutes_saved, 2),
            "estimated_minutes_saved": minutes_saved,
        }
        metrics_dir = self._root / "metrics" / job.engine_id
        metrics_dir.mkdir(parents=True, exist_ok=True)
        record_path = metrics_dir / f"{extraction.job_key}.json"
        if not record_path.is_file():
            record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        # If it already exists, it was written by an earlier successful
        # publish of this exact job_key -- immutable by design (see module
        # docstring), so a re-publish of the same job never rewrites it.
