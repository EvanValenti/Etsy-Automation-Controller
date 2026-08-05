"""HTTP contract tests for cumulative lifetime statistics.

Two routes carry the whole guarantee, and both are exercised here against
a real ledger over an in-memory database:

  GET /metrics/lifetime  -- syncs every currently-succeeded Job into the
    permanent ledger, then reports totals FROM the ledger. Reporting from
    the ledger rather than from the Job rows is what makes the numbers
    cumulative.
  DELETE /jobs/{id}      -- banks the Job's completion before the row goes
    away, which is what covers a job completed and deleted without the
    Dashboard ever having been open.

The behavior being locked in: deleting job history is housekeeping. It
changes what the Jobs page shows and nothing about Time Saved or Lifetime
Production. See infra/lifetime_metrics.py.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import main as api_main  # noqa: E402
from api.dependencies import get_job_service, get_lifetime_metrics_ledger  # noqa: E402
from core.domain.job import Job, JobStatus  # noqa: E402
from infra.lifetime_metrics import LifetimeMetricsLedger  # noqa: E402

VIDEO = "etsy-video-generator"


def make_job(job_id: str, engine_id: str = VIDEO, status: JobStatus = JobStatus.SUCCEEDED) -> Job:
    now = datetime.now(timezone.utc)
    return Job(id=job_id, engine_id=engine_id, config={}, status=status, created_at=now, updated_at=now)


class _FakeJobService:
    """Holds Jobs in a dict so a test can delete one the way the real
    delete route does, without a real repository or engine registry."""

    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = {job.id: job for job in jobs}

    def list_jobs(self, status: JobStatus | None = None, engine_id: str | None = None) -> list[Job]:
        return [j for j in self.jobs.values() if status is None or j.status == status]

    def get_job(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def delete_job(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


@pytest.fixture()
def client_and_service():
    # StaticPool: TestClient serves requests on a worker thread, and the
    # default in-memory-SQLite pool hands each thread its own fresh (empty)
    # database -- so the ledger table would exist only on the thread that
    # created it.
    db = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(db)
    service = _FakeJobService([make_job("v1"), make_job("v2"), make_job("v3")])

    with Session(db) as session:
        api_main.app.dependency_overrides[get_job_service] = lambda: service
        api_main.app.dependency_overrides[get_lifetime_metrics_ledger] = lambda: LifetimeMetricsLedger(session)
        # The real cleanup path recycles engine output to the Recycle Bin;
        # this test is about the metrics guarantee, so only the Job-row
        # removal it performs is kept.
        with patch.object(api_main, "cleanup_delete_job", side_effect=lambda svc, job_id: svc.delete_job(job_id)):
            yield TestClient(api_main.app), service
        api_main.app.dependency_overrides.clear()


def test_lifetime_metrics_report_completed_workflows(client_and_service):
    client, _ = client_and_service

    body = client.get("/metrics/lifetime").json()

    assert body["lifetime_production"] == 3
    assert body["minutes_saved"] == 15  # 3 video workflows x 5 min
    assert body["completed_today"] == 3


def test_deleting_a_job_does_not_reduce_lifetime_metrics(client_and_service):
    client, _ = client_and_service
    before = client.get("/metrics/lifetime").json()

    assert client.delete("/jobs/v2").status_code == 204

    assert client.get("/metrics/lifetime").json() == before


def test_deleting_every_job_does_not_reduce_lifetime_metrics(client_and_service):
    """The operator clears out all job history at once -- the Jobs page ends
    up empty, the headline numbers do not move."""
    client, service = client_and_service
    before = client.get("/metrics/lifetime").json()

    for job_id in ("v1", "v2", "v3"):
        assert client.delete(f"/jobs/{job_id}").status_code == 204

    assert service.list_jobs() == []
    assert client.get("/metrics/lifetime").json() == before
    assert before["lifetime_production"] == 3


def test_a_job_deleted_before_any_metrics_read_still_counts(client_and_service):
    """The case the read-time sync alone cannot catch: a job completed and
    deleted without the Dashboard ever being open. DELETE banks it first."""
    client, _ = client_and_service

    assert client.delete("/jobs/v1").status_code == 204
    body = client.get("/metrics/lifetime").json()

    # v1 is banked by the delete; v2/v3 by the read itself.
    assert body["lifetime_production"] == 3


def test_repeated_reads_do_not_inflate_the_totals(client_and_service):
    client, _ = client_and_service

    first = client.get("/metrics/lifetime").json()
    client.get("/metrics/lifetime")
    third = client.get("/metrics/lifetime").json()

    assert first == third


def test_unfinished_jobs_are_not_counted(client_and_service):
    client, service = client_and_service
    service.jobs["running"] = make_job("running", status=JobStatus.RUNNING)
    service.jobs["failed"] = make_job("failed", status=JobStatus.FAILED)

    body = client.get("/metrics/lifetime").json()

    assert body["lifetime_production"] == 3
