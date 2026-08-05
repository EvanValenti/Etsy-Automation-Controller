"""Tests for the permanent completed-workflow ledger.

The one behavior everything here exists to protect: Time Saved and
Lifetime Production are lifetime accomplishments, so deleting old job
history must never reduce them. That used to be false -- both numbers were
recomputed in the browser from whatever succeeded Job rows the API still
returned, so housekeeping looked like undoing work.

The grouping tests matter just as much: two engines create a fresh
Controller Job row per stage-advance against the SAME engine-side job, so
a ledger keyed on Job ids would report one product as five or six
completions. See infra/lifetime_metrics.py.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.domain.job import Job, JobStatus  # noqa: E402
from infra.lifetime_metrics import (  # noqa: E402
    MINUTES_SAVED_PER_COMPLETED_WORKFLOW,
    LifetimeMetricsLedger,
    workflow_key,
)

VIDEO = "etsy-video-generator"
MOCKUP = "etsy-mockup-generator"
IMAGE = "etsy-ai-image-generator"


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as open_session:
        yield open_session


def make_job(job_id: str, engine_id: str, config: dict | None = None, *, completed_at: datetime | None = None) -> Job:
    when = completed_at or datetime.now(timezone.utc)
    return Job(
        id=job_id,
        engine_id=engine_id,
        config=config or {},
        status=JobStatus.SUCCEEDED,
        created_at=when,
        updated_at=when,
    )


class TestDeletingJobsNeverReducesLifetimeTotals:
    """The V1 polish requirement, stated as tests."""

    def test_totals_survive_every_job_row_disappearing(self, session):
        ledger = LifetimeMetricsLedger(session)
        jobs = [
            make_job("v1", VIDEO, {"preset_key": "standard"}),
            make_job("v2", VIDEO, {"preset_key": "design-reveal"}),
            make_job("m1", MOCKUP, {"design_id": "mushroom-log", "phase": "batch"}),
        ]
        ledger.record_completed_workflows(jobs)
        before = ledger.totals()

        # Every Job row is gone -- the operator cleaned out job history.
        # A later read syncs from an empty job list.
        ledger.record_completed_workflows([])

        assert ledger.totals() == before
        assert before["lifetime_production"] == 3
        assert before["minutes_saved"] == (
            MINUTES_SAVED_PER_COMPLETED_WORKFLOW[VIDEO] * 2 + MINUTES_SAVED_PER_COMPLETED_WORKFLOW[MOCKUP]
        )

    def test_deleting_one_job_leaves_the_others_untouched(self, session):
        ledger = LifetimeMetricsLedger(session)
        kept = make_job("v1", VIDEO)
        deleted = make_job("v2", VIDEO)
        ledger.record_completed_workflows([kept, deleted])

        ledger.record_completed_workflows([kept])  # a read after v2 was deleted

        assert ledger.totals()["lifetime_production"] == 2

    def test_completed_today_is_also_deletion_proof(self, session):
        """Deliberate: the whole metrics block behaves consistently, so one
        delete can't leave Lifetime Production intact while dropping
        Completed Today beside it."""
        ledger = LifetimeMetricsLedger(session)
        ledger.record_completed_workflows([make_job("v1", VIDEO)])
        assert ledger.totals()["completed_today"] == 1

        ledger.record_completed_workflows([])

        assert ledger.totals()["completed_today"] == 1


class TestWorkflowGrouping:
    def test_ai_image_stage_advances_count_as_one_workflow(self, session):
        """The real, observed case: one job_name produces a fresh Job row
        per advance (concepts, prompts, images, ...)."""
        ledger = LifetimeMetricsLedger(session)
        stages = [
            make_job(f"job-{stage}", IMAGE, {"job_name": "orb_shirt", "action": "advance", "stage": stage})
            for stage in ("concept_planning", "prompt_build", "image_generation")
        ]

        ledger.record_completed_workflows(stages)

        totals = ledger.totals()
        assert totals["lifetime_production"] == 1
        assert totals["minutes_saved"] == MINUTES_SAVED_PER_COMPLETED_WORKFLOW[IMAGE]

    def test_mockup_preview_and_batch_for_one_design_count_once(self, session):
        ledger = LifetimeMetricsLedger(session)
        ledger.record_completed_workflows(
            [
                make_job("m1", MOCKUP, {"design_id": "mushroom-log", "phase": "preview"}),
                make_job("m2", MOCKUP, {"design_id": "mushroom-log", "phase": "batch"}),
            ]
        )

        assert ledger.totals()["lifetime_production"] == 1

    def test_videos_sharing_a_preset_are_separate_workflows(self, session):
        """preset_key is a config CHOICE, not an identity -- grouping by it
        would merge unrelated videos (same distinction infra/cleanup.py and
        the web UI's groupIdentifier() make)."""
        ledger = LifetimeMetricsLedger(session)
        ledger.record_completed_workflows(
            [
                make_job("v1", VIDEO, {"preset_key": "standard"}),
                make_job("v2", VIDEO, {"preset_key": "standard"}),
            ]
        )

        assert ledger.totals()["lifetime_production"] == 2

    def test_missing_or_blank_identifier_falls_back_to_the_job_id(self):
        assert workflow_key(make_job("abc", IMAGE, {})) == "solo:abc"
        assert workflow_key(make_job("abc", IMAGE, {"job_name": "   "})) == "solo:abc"
        assert workflow_key(make_job("abc", MOCKUP, {"design_id": "  spaced  "})) == f"{MOCKUP}:spaced"

    def test_unknown_engine_contributes_no_estimated_minutes(self, session):
        """A newly registered engine can never silently inflate Time Saved."""
        ledger = LifetimeMetricsLedger(session)
        ledger.record_completed_workflows([make_job("x1", "some-future-engine")])

        totals = ledger.totals()
        assert totals["lifetime_production"] == 1
        assert totals["minutes_saved"] == 0


class TestRecordingIsIdempotent:
    def test_repeated_syncs_do_not_inflate_the_count(self, session):
        ledger = LifetimeMetricsLedger(session)
        jobs = [make_job("v1", VIDEO), make_job("v2", VIDEO)]

        first_new = ledger.record_completed_workflows(jobs)
        second_new = ledger.record_completed_workflows(jobs)
        ledger.record_completed_workflows(jobs)

        assert (first_new, second_new) == (2, 0)
        assert ledger.totals()["lifetime_production"] == 2

    def test_only_succeeded_jobs_are_recorded(self, session):
        ledger = LifetimeMetricsLedger(session)
        running = make_job("v1", VIDEO)
        running.status = JobStatus.RUNNING
        failed = make_job("v2", VIDEO)
        failed.status = JobStatus.FAILED

        ledger.record_completed_workflows([running, failed])

        assert ledger.totals()["lifetime_production"] == 0

    def test_a_later_advance_moves_the_workflow_into_today(self, session):
        """A workflow belongs to the day it last moved -- matching how the
        browser-side version read job.updated_at."""
        ledger = LifetimeMetricsLedger(session)
        two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
        ledger.record_completed_workflows(
            [make_job("job-1", IMAGE, {"job_name": "orb_shirt"}, completed_at=two_days_ago)]
        )
        assert ledger.totals()["completed_today"] == 0

        ledger.record_completed_workflows([make_job("job-2", IMAGE, {"job_name": "orb_shirt"})])

        totals = ledger.totals()
        assert totals["completed_today"] == 1
        assert totals["lifetime_production"] == 1
