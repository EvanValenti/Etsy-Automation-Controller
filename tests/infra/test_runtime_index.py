"""Tests for infra/runtime_index.py -- the shared-runtime read side.
Every scenario here is built directly on disk (no publisher involved) so
each test can construct exactly the on-disk shape it needs, including
shapes a real publish would never produce on its own (a half-copied
temp directory, a zero-byte "still syncing" file) -- these ARE the
failure modes this module exists to tolerate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra.runtime_index import compute_shared_metrics_totals, list_shared_jobs  # noqa: E402

MOCKUP = "etsy-mockup-generator"


def _publish_fake_job(root: Path, engine_id: str, job_key: str, *, run_succeeded: bool = True, complete: bool = True) -> Path:
    job_dir = root / "jobs" / engine_id / job_key
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "manifest.json").write_text("{}", encoding="utf-8")
    status = {
        "schema_version": 1,
        "job_id": job_key,
        "engine_id": engine_id,
        "completed_at": "2026-07-27T12:00:00+00:00",
        "run_succeeded": run_succeeded,
        "published_by_machine": "desktop-test",
        "errors": [],
    }
    (job_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if complete:
        (job_dir / "COMPLETE").write_text("2026-07-27T12:00:01+00:00", encoding="utf-8")
    return job_dir


def _write_metrics_record(root: Path, engine_id: str, job_key: str, **overrides) -> Path:
    record = {
        "schema_version": 1,
        "job_id": job_key,
        "module": engine_id,
        "completed_at": "2026-07-27T12:00:00+00:00",
        "run_succeeded": True,
        "assets_generated": 20,
        "automation_seconds": 240.0,
        "estimated_manual_minutes": 64.0,
        "estimated_minutes_saved": 5,
    }
    record.update(overrides)
    metrics_dir = root / "metrics" / engine_id
    metrics_dir.mkdir(parents=True, exist_ok=True)
    path = metrics_dir / f"{job_key}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


class TestListingSharedJobs:
    def test_empty_or_missing_root_yields_no_jobs_not_an_error(self, tmp_path):
        assert list_shared_jobs(tmp_path / "does-not-exist") == []
        (tmp_path / "jobs").mkdir()
        assert list_shared_jobs(tmp_path) == []

    def test_a_fully_published_job_is_listed(self, tmp_path):
        _publish_fake_job(tmp_path, MOCKUP, "run-001__desktop")

        jobs = list_shared_jobs(tmp_path)

        assert len(jobs) == 1
        assert jobs[0].job_id == "run-001__desktop"
        assert jobs[0].engine_id == MOCKUP
        assert jobs[0].run_succeeded is True

    def test_a_job_without_a_complete_marker_is_not_indexed(self, tmp_path):
        """The exact scenario a publish interrupted mid-copy (or another
        machine's publish still in progress) would produce."""
        _publish_fake_job(tmp_path, MOCKUP, "run-002__laptop", complete=False)

        assert list_shared_jobs(tmp_path) == []

    def test_a_dot_tmp_directory_is_never_indexed(self, tmp_path):
        tmp_dir = tmp_path / "jobs" / MOCKUP / "run-003__desktop.tmp-abcd1234"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "COMPLETE").write_text("x", encoding="utf-8")  # even if one somehow existed

        assert list_shared_jobs(tmp_path) == []

    def test_complete_marker_present_but_status_json_unreadable_is_skipped_not_crashed_on(self, tmp_path):
        """The OneDrive-still-syncing case: COMPLETE synced down before
        status.json finished (files within one folder don't sync
        atomically together)."""
        job_dir = tmp_path / "jobs" / MOCKUP / "run-004__desktop"
        job_dir.mkdir(parents=True)
        (job_dir / "COMPLETE").write_text("x", encoding="utf-8")
        (job_dir / "status.json").write_text("", encoding="utf-8")  # zero-byte placeholder

        assert list_shared_jobs(tmp_path) == []

    def test_can_filter_by_engine_id(self, tmp_path):
        _publish_fake_job(tmp_path, MOCKUP, "run-005__desktop")
        _publish_fake_job(tmp_path, "etsy-ai-image-generator", "job-abc__desktop")

        assert len(list_shared_jobs(tmp_path)) == 2
        assert len(list_shared_jobs(tmp_path, engine_id=MOCKUP)) == 1

    def test_a_second_simulated_machine_reading_the_same_root_sees_the_same_job(self, tmp_path):
        """Publishing and reading are decoupled -- this proves the read
        side needs nothing machine-specific to see a job another
        "machine" (a different machine_id, here) published."""
        _publish_fake_job(tmp_path, MOCKUP, "run-006__desktop")

        # A second reader, simulating a laptop, just points at the same
        # shared root -- no machine identity needed to read.
        jobs_as_seen_from_laptop = list_shared_jobs(tmp_path)

        assert len(jobs_as_seen_from_laptop) == 1
        assert jobs_as_seen_from_laptop[0].job_id == "run-006__desktop"


class TestMetricsAggregation:
    def test_empty_or_missing_root_yields_zeroed_totals(self, tmp_path):
        totals = compute_shared_metrics_totals(tmp_path / "does-not-exist")
        assert totals["total_jobs_completed"] == 0
        assert totals["total_assets_generated"] == 0

    def test_totals_sum_across_records(self, tmp_path):
        _write_metrics_record(tmp_path, MOCKUP, "run-001__desktop", assets_generated=20, automation_seconds=240.0, estimated_minutes_saved=5)
        _write_metrics_record(tmp_path, MOCKUP, "run-002__desktop", assets_generated=10, automation_seconds=120.0, estimated_minutes_saved=5)

        totals = compute_shared_metrics_totals(tmp_path)

        assert totals["total_jobs_completed"] == 2
        assert totals["total_assets_generated"] == 30
        assert totals["total_automation_seconds"] == 360.0
        assert totals["total_estimated_minutes_saved"] == 10

    def test_the_same_job_id_is_never_double_counted(self, tmp_path):
        """The exact scenario the design brief calls out: the same job_id
        appearing more than once (e.g. a stray copy under a differently-
        named engine folder) must still count once."""
        _write_metrics_record(tmp_path, MOCKUP, "run-001__desktop", assets_generated=20)
        # A second, identically-keyed record filed under a different
        # engine subfolder -- a real stray-copy scenario, not something
        # publish_job() would do itself, but the reader must still
        # collapse it to one, since dedup is keyed on the record's own
        # job_id field, not on its file path.
        _write_metrics_record(tmp_path, "etsy-mockup-generator-copy", "run-001__desktop", assets_generated=20)

        totals = compute_shared_metrics_totals(tmp_path)

        assert totals["total_jobs_completed"] == 1
        assert totals["total_assets_generated"] == 20

    def test_a_zero_byte_still_syncing_record_is_ignored_not_crashed_on(self, tmp_path):
        metrics_dir = tmp_path / "metrics" / MOCKUP
        metrics_dir.mkdir(parents=True)
        (metrics_dir / "run-001__desktop.json").write_text("", encoding="utf-8")

        totals = compute_shared_metrics_totals(tmp_path)

        assert totals["total_jobs_completed"] == 0

    def test_a_failed_runs_record_would_not_count_toward_totals(self, tmp_path):
        """Defense in depth: publish_job() never writes a metrics record
        for a failed run in the first place, but the reader stays
        correct even if one somehow existed (e.g. hand-edited)."""
        _write_metrics_record(tmp_path, MOCKUP, "run-fail__desktop", run_succeeded=False)

        totals = compute_shared_metrics_totals(tmp_path)

        assert totals["total_jobs_completed"] == 0
        assert totals["records_considered"] == 1
