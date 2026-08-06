"""Tests for infra/runtime_publisher.py -- the shared-runtime publish
side. Everything here is synthetic: a fake JobService/MonitoringService
pair standing in for the real ones, and hand-built mockup-generator
manifest.json content shaped exactly like batch_generate.py's real
output (manifest_version, run_succeeded, output_counts, assets) -- no
real generator run, no proprietary production assets, per the task's own
"use synthetic or existing non-sensitive test data" instruction.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.domain.job import Job, JobStatus  # noqa: E402
from core.domain.monitoring import JobMetrics  # noqa: E402
from infra.runtime_publisher import PublishStatus, RuntimePublisher  # noqa: E402

MOCKUP = "etsy-mockup-generator"


class FakeJobService:
    def __init__(self, jobs: dict[str, Job]):
        self._jobs = jobs

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


class FakeMonitoringService:
    def __init__(self, seconds: float | None = 240.0):
        self._seconds = seconds

    def get_job_metrics(self, job_id: str) -> JobMetrics:
        return JobMetrics(total_execution_seconds=self._seconds)


def _write_manifest(run_dir: Path, *, run_id: str, design_id: str, run_succeeded: bool = True, asset_count: int = 3) -> Path:
    assets_dir = run_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    for i in range(asset_count):
        filename = f"flat-front-{i + 1:02d}.png"
        (assets_dir / filename).write_bytes(f"fake-png-bytes-{i}".encode())
        assets.append({"filename": filename, "path": f"assets/{filename}", "category": "flat_front"})
    manifest = {
        "manifest_version": 1,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "design_id": design_id,
        "output_counts": {"flat_front": asset_count},
        "assets_dir": str(assets_dir),
        "assets": assets,
        "errors": [],
        "run_succeeded": run_succeeded,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _mockup_job(job_id: str, run_dir: Path, *, run_id: str, design_id: str, run_succeeded: bool = True, asset_count: int = 3) -> Job:
    manifest_path = _write_manifest(run_dir, run_id=run_id, design_id=design_id, run_succeeded=run_succeeded, asset_count=asset_count)
    manifest = json.loads(manifest_path.read_text())
    when = datetime.now(timezone.utc)
    return Job(
        id=job_id,
        engine_id=MOCKUP,
        config={"phase": "batch", "design_id": design_id},
        status=JobStatus.SUCCEEDED,
        created_at=when,
        updated_at=when,
        result_summary={
            "phase": "batch",
            "run_id": run_id,
            "run_dir": str(run_dir),
            "manifest_path": str(manifest_path),
            "assets_dir": str(run_dir / "assets"),
            "run_succeeded": run_succeeded,
            "manifest": manifest,
        },
    )


@pytest.fixture
def runtime_root(tmp_path) -> Path:
    return tmp_path / "shared_runtime"


@pytest.fixture(autouse=True)
def _pin_machine_id(monkeypatch):
    monkeypatch.setenv("VILICITY_MACHINE_ID", "desktop-test")


class TestPublishingASucceededJob:
    def test_publishes_manifest_assets_preview_and_metrics(self, tmp_path, runtime_root):
        run_dir = tmp_path / "engine_output" / "run-2026-07-27-001"
        job = _mockup_job("ctrl-job-1", run_dir, run_id="run-2026-07-27-001", design_id="mushroom-log")
        publisher = RuntimePublisher(FakeJobService({job.id: job}), FakeMonitoringService(240.0), runtime_root)

        result = publisher.publish_job(job.id)

        assert result.status == PublishStatus.PUBLISHED
        job_key = "run-2026-07-27-001__desktop-test"
        assert result.job_key == job_key

        job_dir = runtime_root / "jobs" / MOCKUP / job_key
        assert (job_dir / "COMPLETE").is_file()
        assert (job_dir / "manifest.json").is_file()
        status = json.loads((job_dir / "status.json").read_text())
        assert status["run_succeeded"] is True
        assert status["published_by_machine"] == "desktop-test"

        assets_dir = runtime_root / "approved-assets" / MOCKUP / job_key
        assert len(list(assets_dir.iterdir())) == 3

        preview_dir = runtime_root / "previews" / MOCKUP / job_key
        assert len(list(preview_dir.iterdir())) == 1

        metrics_path = runtime_root / "metrics" / MOCKUP / f"{job_key}.json"
        record = json.loads(metrics_path.read_text())
        assert record["job_id"] == job_key
        assert record["module"] == MOCKUP
        assert record["run_succeeded"] is True
        assert record["assets_generated"] == 3
        assert record["automation_seconds"] == 240.0
        assert record["estimated_minutes_saved"] == 5  # MINUTES_SAVED_PER_COMPLETED_WORKFLOW[etsy-mockup-generator]
        assert record["estimated_manual_minutes"] == pytest.approx(4.0 + 5)  # 240s automation + the 5-minute estimate
        assert record["schema_version"] == 1

    def test_republishing_the_same_job_is_a_no_op(self, tmp_path, runtime_root):
        run_dir = tmp_path / "engine_output" / "run-2026-07-27-001"
        job = _mockup_job("ctrl-job-1", run_dir, run_id="run-2026-07-27-001", design_id="mushroom-log")
        publisher = RuntimePublisher(FakeJobService({job.id: job}), FakeMonitoringService(), runtime_root)

        first = publisher.publish_job(job.id)
        second = publisher.publish_job(job.id)

        assert first.status == PublishStatus.PUBLISHED
        assert second.status == PublishStatus.ALREADY_PUBLISHED
        assert second.path == first.path
        # No conflict siblings, no duplicate metrics record.
        assert list((runtime_root / "jobs" / MOCKUP).iterdir()) == [Path(first.path)]

    def test_a_failed_run_is_never_published(self, tmp_path, runtime_root):
        run_dir = tmp_path / "engine_output" / "run-2026-07-27-002"
        job = _mockup_job("ctrl-job-2", run_dir, run_id="run-2026-07-27-002", design_id="mushroom-log", run_succeeded=False)
        publisher = RuntimePublisher(FakeJobService({job.id: job}), FakeMonitoringService(), runtime_root)

        result = publisher.publish_job(job.id)

        assert result.status == PublishStatus.RUN_FAILED
        assert not (runtime_root / "jobs").exists()
        assert not (runtime_root / "metrics").exists()

    def test_only_succeeded_controller_jobs_are_publishable(self, tmp_path, runtime_root):
        run_dir = tmp_path / "engine_output" / "run-2026-07-27-003"
        job = _mockup_job("ctrl-job-3", run_dir, run_id="run-2026-07-27-003", design_id="mushroom-log")
        job.status = JobStatus.RUNNING
        publisher = RuntimePublisher(FakeJobService({job.id: job}), FakeMonitoringService(), runtime_root)

        result = publisher.publish_job(job.id)

        assert result.status == PublishStatus.NOT_SUCCEEDED

    def test_unknown_job_id(self, runtime_root):
        publisher = RuntimePublisher(FakeJobService({}), FakeMonitoringService(), runtime_root)
        result = publisher.publish_job("does-not-exist")
        assert result.status == PublishStatus.NOT_SUCCEEDED

    def test_unsupported_engine_is_reported_not_silently_skipped(self, runtime_root):
        when = datetime.now(timezone.utc)
        job = Job(id="v1", engine_id="etsy-video-generator", config={}, status=JobStatus.SUCCEEDED, created_at=when, updated_at=when)
        publisher = RuntimePublisher(FakeJobService({job.id: job}), FakeMonitoringService(), runtime_root)

        result = publisher.publish_job(job.id)

        assert result.status == PublishStatus.UNSUPPORTED_ENGINE


class TestConflictAndCollisionSafety:
    def test_same_job_key_different_content_is_a_conflict_not_an_overwrite(self, tmp_path, runtime_root):
        run_dir_a = tmp_path / "engine_output" / "run-2026-07-27-001"
        job_a = _mockup_job("ctrl-a", run_dir_a, run_id="run-2026-07-27-001", design_id="mushroom-log", asset_count=3)
        publisher = RuntimePublisher(FakeJobService({job_a.id: job_a}), FakeMonitoringService(), runtime_root)
        first = publisher.publish_job(job_a.id)
        assert first.status == PublishStatus.PUBLISHED

        # Same run_id (so the SAME job_key, since job_key is derived from
        # run_id+machine, and _pin_machine_id keeps machine_id constant) but
        # genuinely different content -- simulates two machines racing to
        # publish a same-day sequential run_id for two DIFFERENT designs.
        run_dir_b = tmp_path / "engine_output" / "run-2026-07-27-001-other"
        job_b = _mockup_job("ctrl-b", run_dir_b, run_id="run-2026-07-27-001", design_id="totally-different-design", asset_count=5)
        second_publisher = RuntimePublisher(FakeJobService({job_b.id: job_b}), FakeMonitoringService(), runtime_root)

        second = second_publisher.publish_job(job_b.id)

        assert second.status == PublishStatus.CONFLICT
        # The original publish is untouched.
        original_manifest = json.loads((Path(first.path) / "manifest.json").read_text())
        assert original_manifest["design_id"] == "mushroom-log"
        # The conflicting attempt was preserved separately, not discarded.
        assert second.path is not None
        conflict_manifest = json.loads((Path(second.path) / "manifest.json").read_text())
        assert conflict_manifest["design_id"] == "totally-different-design"

    def test_different_machines_never_collide_on_the_same_run_id(self, tmp_path, runtime_root, monkeypatch):
        run_dir_a = tmp_path / "engine_output" / "run-2026-07-27-001"
        job_a = _mockup_job("ctrl-a", run_dir_a, run_id="run-2026-07-27-001", design_id="desktop-design")
        monkeypatch.setenv("VILICITY_MACHINE_ID", "desktop")
        RuntimePublisher(FakeJobService({job_a.id: job_a}), FakeMonitoringService(), runtime_root).publish_job(job_a.id)

        run_dir_b = tmp_path / "engine_output" / "run-2026-07-27-001-laptop"
        job_b = _mockup_job("ctrl-b", run_dir_b, run_id="run-2026-07-27-001", design_id="laptop-design")
        monkeypatch.setenv("VILICITY_MACHINE_ID", "laptop")
        result_b = RuntimePublisher(FakeJobService({job_b.id: job_b}), FakeMonitoringService(), runtime_root).publish_job(job_b.id)

        # Same engine-native run_id, different machines -- both publish
        # cleanly under distinct job_keys, no conflict.
        assert result_b.status == PublishStatus.PUBLISHED
        published = list((runtime_root / "jobs" / MOCKUP).iterdir())
        assert {p.name for p in published} == {
            "run-2026-07-27-001__desktop",
            "run-2026-07-27-001__laptop",
        }
