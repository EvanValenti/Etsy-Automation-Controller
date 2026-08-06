"""Reads what infra/runtime_publisher.py has published to the shared
runtime root -- the Controller's read side of the shared-runtime feature.

Every function here is defensive about the root being a OneDrive-backed
folder: a directory can exist with its files still mid-download (a
"cloud-only" placeholder, zero bytes, or a transient PermissionError from
the sync client holding a lock), and any of that must be tolerated as
"not ready yet," never surfaced as a crash. Nothing here ever raises for
a missing/unreadable shared root -- an offline OneDrive, a laptop that
has never configured VILICITY_RUNTIME_ROOT, and a perfectly healthy empty
root all look the same: an empty result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infra.runtime_root import resolve_runtime_root


@dataclass
class SharedJobSummary:
    job_id: str
    engine_id: str
    completed_at: str | None
    run_succeeded: bool
    published_by_machine: str | None
    path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "engine_id": self.engine_id,
            "completed_at": self.completed_at,
            "run_succeeded": self.run_succeeded,
            "published_by_machine": self.published_by_machine,
            "path": self.path,
        }


def _safe_read_json(path: Path) -> dict[str, Any] | None:
    """Returns None (never raises) for anything that looks like a
    still-syncing or otherwise not-yet-ready file: missing, empty,
    unreadable, or not valid JSON. All four are normal, expected states
    for a file inside a OneDrive folder that hasn't finished syncing."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def list_shared_jobs(runtime_root: Path | None = None, engine_id: str | None = None) -> list[SharedJobSummary]:
    """Every job under jobs/ that has actually finished publishing (has a
    COMPLETE marker) -- a job directory without one is mid-publish or was
    interrupted and is silently skipped, not reported as broken.
    """
    root = runtime_root if runtime_root is not None else resolve_runtime_root()
    jobs_root = root / "jobs"
    if not jobs_root.is_dir():
        return []

    summaries: list[SharedJobSummary] = []
    try:
        engine_dirs = sorted(p for p in jobs_root.iterdir() if p.is_dir())
    except OSError:
        return []

    for engine_dir in engine_dirs:
        if engine_id is not None and engine_dir.name != engine_id:
            continue
        try:
            job_dirs = sorted(p for p in engine_dir.iterdir() if p.is_dir())
        except OSError:
            continue
        for job_dir in job_dirs:
            if ".tmp-" in job_dir.name:
                continue  # an in-progress publish's temp directory -- never index mid-copy
            if not (job_dir / "COMPLETE").is_file():
                continue  # not yet fully published (or publish was interrupted) -- never index a partial job
            status = _safe_read_json(job_dir / "status.json")
            if status is None:
                continue  # COMPLETE exists but status.json isn't readable yet -- treat as still-syncing
            summaries.append(
                SharedJobSummary(
                    job_id=status.get("job_id", job_dir.name),
                    engine_id=status.get("engine_id", engine_dir.name),
                    completed_at=status.get("completed_at"),
                    run_succeeded=bool(status.get("run_succeeded")),
                    published_by_machine=status.get("published_by_machine"),
                    path=str(job_dir),
                )
            )
    return summaries


def compute_shared_metrics_totals(runtime_root: Path | None = None) -> dict[str, Any]:
    """Aggregate totals derived from every per-job metrics record under
    metrics/ -- never from a single mutable running total, per the design
    brief. Deduplicates by job_id (the record's own filename stem is also
    the job_id by construction, but this re-checks the field itself
    rather than trusting the filename, in case a record was ever copied
    or renamed by hand) so the same job being visible to two machines
    never double-counts.
    """
    root = runtime_root if runtime_root is not None else resolve_runtime_root()
    metrics_root = root / "metrics"
    records_by_job_id: dict[str, dict[str, Any]] = {}

    if metrics_root.is_dir():
        try:
            engine_dirs = sorted(p for p in metrics_root.iterdir() if p.is_dir())
        except OSError:
            engine_dirs = []
        for engine_dir in engine_dirs:
            try:
                record_files = sorted(engine_dir.glob("*.json"))
            except OSError:
                continue
            for record_file in record_files:
                record = _safe_read_json(record_file)
                if record is None or not record.get("job_id"):
                    continue
                records_by_job_id[record["job_id"]] = record

    records = list(records_by_job_id.values())
    succeeded = [r for r in records if r.get("run_succeeded")]

    def _sum(field: str) -> float:
        return sum((r.get(field) or 0) for r in succeeded)

    return {
        "total_jobs_completed": len(succeeded),
        "total_assets_generated": int(_sum("assets_generated")),
        "total_automation_seconds": _sum("automation_seconds"),
        "total_estimated_manual_minutes": _sum("estimated_manual_minutes"),
        "total_estimated_minutes_saved": _sum("estimated_minutes_saved"),
        "records_considered": len(records),
    }
