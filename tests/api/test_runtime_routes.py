"""HTTP contract tests for the shared-runtime routes: /runtime/config,
POST /runtime/jobs/{id}/publish, GET /runtime/jobs, GET /runtime/metrics.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import main as api_main  # noqa: E402
from api.dependencies import get_runtime_publisher  # noqa: E402
from core.domain.job import Job, JobStatus  # noqa: E402
from core.domain.monitoring import JobMetrics  # noqa: E402
from infra.runtime_publisher import RuntimePublisher  # noqa: E402

MOCKUP = "etsy-mockup-generator"


class _FakeJobService:
    def __init__(self, jobs: dict[str, Job]):
        self._jobs = jobs

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


class _FakeMonitoringService:
    def get_job_metrics(self, job_id: str) -> JobMetrics:
        return JobMetrics(total_execution_seconds=120.0)


def _mockup_job(tmp_path: Path, job_id: str, run_id: str, design_id: str) -> Job:
    run_dir = tmp_path / "engine_output" / run_id
    assets_dir = run_dir / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "flat-front-01.png").write_bytes(b"fake-png")
    manifest = {
        "manifest_version": 1,
        "run_id": run_id,
        "design_id": design_id,
        "output_counts": {"flat_front": 1},
        "assets_dir": str(assets_dir),
        "assets": [{"filename": "flat-front-01.png", "path": "assets/flat-front-01.png", "category": "flat_front"}],
        "errors": [],
        "run_succeeded": True,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
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
            "assets_dir": str(assets_dir),
            "run_succeeded": True,
            "manifest": manifest,
        },
    )


@pytest.fixture()
def isolated_runtime_root(tmp_path, monkeypatch):
    """Every read-only route (/runtime/config, /runtime/jobs,
    /runtime/metrics) resolves the root itself from the env var -- point
    it at an isolated tmp_path so these tests never touch the repo's own
    var/vilicity_runtime/."""
    root = tmp_path / "shared_runtime"
    monkeypatch.setenv("VILICITY_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("VILICITY_MACHINE_ID", "test-machine")
    return root


@pytest.fixture()
def client():
    with TestClient(api_main.app) as c:
        yield c
    api_main.app.dependency_overrides.clear()


class TestRuntimeConfig:
    def test_reports_the_configured_root_and_machine_id(self, isolated_runtime_root, client):
        body = client.get("/runtime/config").json()

        assert body["runtime_root"] == str(isolated_runtime_root)
        assert body["configured"] is True
        assert body["machine_id"] == "test-machine"


class TestPublishRoute:
    def test_publishing_a_succeeded_job_and_then_seeing_it_listed(self, tmp_path, isolated_runtime_root, client):
        job = _mockup_job(tmp_path, "ctrl-1", "run-2026-07-27-001", "mushroom-log")
        publisher = RuntimePublisher(_FakeJobService({job.id: job}), _FakeMonitoringService(), isolated_runtime_root)
        api_main.app.dependency_overrides[get_runtime_publisher] = lambda: publisher

        publish_body = client.post(f"/runtime/jobs/{job.id}/publish").json()
        assert publish_body["status"] == "published"

        jobs_body = client.get("/runtime/jobs").json()
        assert len(jobs_body) == 1
        assert jobs_body[0]["job_id"] == "run-2026-07-27-001__test-machine"

        metrics_body = client.get("/runtime/metrics").json()
        assert metrics_body["total_jobs_completed"] == 1
        assert metrics_body["total_assets_generated"] == 1

    def test_publishing_twice_is_reported_as_already_published(self, tmp_path, isolated_runtime_root, client):
        job = _mockup_job(tmp_path, "ctrl-1", "run-2026-07-27-002", "mushroom-log")
        publisher = RuntimePublisher(_FakeJobService({job.id: job}), _FakeMonitoringService(), isolated_runtime_root)
        api_main.app.dependency_overrides[get_runtime_publisher] = lambda: publisher

        client.post(f"/runtime/jobs/{job.id}/publish")
        second = client.post(f"/runtime/jobs/{job.id}/publish").json()

        assert second["status"] == "already_published"
        # Reading metrics again still reports exactly one job -- no
        # double count from the second publish attempt.
        assert client.get("/runtime/metrics").json()["total_jobs_completed"] == 1

    def test_publishing_an_unknown_job_id_reports_not_succeeded_not_a_500(self, isolated_runtime_root, client):
        publisher = RuntimePublisher(_FakeJobService({}), _FakeMonitoringService(), isolated_runtime_root)
        api_main.app.dependency_overrides[get_runtime_publisher] = lambda: publisher

        response = client.post("/runtime/jobs/does-not-exist/publish")

        assert response.status_code == 200
        assert response.json()["status"] == "not_succeeded"


class TestReadRoutesToleratesAnUnconfiguredOrEmptyRoot:
    def test_no_jobs_and_zeroed_metrics_when_nothing_has_been_published_yet(self, isolated_runtime_root, client):
        assert client.get("/runtime/jobs").json() == []
        totals = client.get("/runtime/metrics").json()
        assert totals["total_jobs_completed"] == 0
