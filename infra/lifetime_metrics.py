"""Lifetime metrics -- a Controller-owned feature, not an engine.

Same shape as infra/cleanup.py: a Controller lifecycle concern that needs
per-engine knowledge of what a Job's config means, which is why it lives
in infra/ rather than core/ (core/domain/job.py: "`config` is opaque JSON
to the Core"). infra/cleanup.py already reads config["job_name"] for the
same reason.

WHY THIS EXISTS
Time Saved and Lifetime Production are accomplishment statistics: they
report what the Controller has produced over its lifetime. They used to be
computed live, in the browser, by grouping whatever succeeded Job rows the
API still returned -- which meant deleting old job history silently
reduced them. Deleting history is housekeeping; the work still happened.

So completions are recorded once, permanently, into a ledger keyed by
WORKFLOW (see workflow_key()) that no delete path ever removes. Job rows
remain freely deletable and job history stays exactly as visible (or not)
as the operator wants -- deletion now only affects visibility, which is
all it ever meant to affect.

HOW ROWS GET WRITTEN
record_completed_workflows() is idempotent by workflow_key, and is called
from exactly two places (api/main.py):
  - GET /metrics/lifetime, over every currently-succeeded Job, immediately
    before totals are read. The Dashboard polls this, so a completion is
    normally in the ledger within seconds of happening.
  - DELETE /jobs/{id}, BEFORE the Job row goes away. This is what closes
    the only hole the read-time sync leaves open: a job that completed and
    was deleted without the Dashboard ever being open. A deleted job's
    accomplishment is banked first, then the row is removed.

Deliberately NOT wired into the Execution Coordinator: recording an
accomplishment isn't an orchestration decision, and a Coordinator that
had to know about minutes-saved estimates would be doing the Dashboard's
job. The two call sites above cover every way a Job can reach SUCCEEDED
(including the AI Image Generator's per-stage advance routes) without any
engine-specific completion hook.

WHAT IS NOT CUMULATIVE
"Listings Built Today" is not here -- it counts built workspace folders,
which job deletion never touched in the first place.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from core.domain.job import Job, JobStatus
from infra.db.models import CompletedWorkflowRow

# Estimated minutes of manual operator work each completed workflow
# replaces. Operator-supplied estimates of the hand-work an engine
# removes -- not measured runtimes, and not anything the Controller
# computes from job durations. An engine absent from this map contributes
# 0 rather than a guessed default, so a newly registered engine can never
# silently inflate the headline number.
#
# This map is the SINGLE source of truth for the estimate. The web UI used
# to carry its own copy (web/src/timeSaved.ts) and now only formats the
# number this module produces -- two copies of a pricing table drift.
MINUTES_SAVED_PER_COMPLETED_WORKFLOW: dict[str, int] = {
    "etsy-ai-image-generator": 7,
    "etsy-mockup-generator": 5,
    "etsy-video-generator": 5,
}

# Engines that create a fresh Controller Job row for every "advance"/
# re-batch call against the SAME persistent engine-side job: the AI Image
# Generator's job_name and the Mockup Generator's design_id each identify
# ONE underlying unit of work across many Job rows. Counting rows would
# report one shirt as five or six completions.
#
# The Video Generator is deliberately absent: its preset_key is a shared
# config CHOICE, not an identity, so grouping by it would merge unrelated
# videos into one. Kept consistent with infra/cleanup.py's
# _other_jobs_reference_path() and the web UI's groupIdentifier(), which
# make the same distinction for the same reason.
_WORKFLOW_IDENTITY_CONFIG_KEYS: dict[str, str] = {
    "etsy-ai-image-generator": "job_name",
    "etsy-mockup-generator": "design_id",
}


def workflow_key(job: Job) -> str:
    """The stable identity of the one unit of work this Job belongs to.

    Two Job rows sharing a key are two stages of a single workflow and
    count once between them. A Job with no engine-side identity (or an
    engine that has no per-row identity concept at all) keys on its own
    id, so it counts as exactly one workflow of its own.
    """
    identity_key = _WORKFLOW_IDENTITY_CONFIG_KEYS.get(job.engine_id)
    if identity_key:
        identifier = (job.config or {}).get(identity_key)
        if isinstance(identifier, str) and identifier.strip():
            return f"{job.engine_id}:{identifier.strip()}"
    return f"solo:{job.id}"


class LifetimeMetricsLedger:
    """Reads and appends to the permanent completed-workflow ledger.

    Constructed per request from the same Session every repository uses
    (see api/dependencies.py). Deliberately not a core/ Application
    Service and deliberately not behind a repository Protocol: it is one
    concrete Controller-owned feature over one table, exactly like
    ListingWorkspaceBuilder is one concrete feature over one folder tree.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_completed_workflows(self, jobs: list[Job]) -> int:
        """Record every SUCCEEDED job in `jobs` as a completed workflow.

        Idempotent: a workflow already in the ledger keeps its original
        first_completed_at and its single row -- only last_completed_at
        (and the minutes estimate, should the map above ever change) is
        refreshed. Safe to call on every read, which is exactly what the
        metrics route does.

        Returns the number of workflow rows newly created, purely so a
        caller can log/report it -- the totals are read separately.
        """
        newly_recorded = 0
        for job in jobs:
            if job.status != JobStatus.SUCCEEDED:
                continue
            key = workflow_key(job)
            completed_at = _as_naive_utc(job.updated_at or job.created_at)
            row = self._session.get(CompletedWorkflowRow, key)
            if row is None:
                row = CompletedWorkflowRow(
                    workflow_key=key,
                    engine_id=job.engine_id,
                    minutes_saved=MINUTES_SAVED_PER_COMPLETED_WORKFLOW.get(job.engine_id, 0),
                    first_completed_at=completed_at,
                    last_completed_at=completed_at,
                )
                newly_recorded += 1
            else:
                row.minutes_saved = MINUTES_SAVED_PER_COMPLETED_WORKFLOW.get(job.engine_id, 0)
                if completed_at is not None and (
                    row.last_completed_at is None or completed_at > row.last_completed_at
                ):
                    row.last_completed_at = completed_at
                if completed_at is not None and (
                    row.first_completed_at is None or completed_at < row.first_completed_at
                ):
                    row.first_completed_at = completed_at
            self._session.add(row)

        self._session.commit()
        return newly_recorded

    def totals(self) -> dict[str, int]:
        """The Dashboard's headline numbers, straight from the ledger.

        minutes_saved / lifetime_production are cumulative by
        construction: nothing ever deletes a ledger row.

        completed_today reads from the ledger too, rather than from live
        Job rows, so the whole metrics block behaves consistently -- one
        delete cannot leave "Lifetime Production" intact while dropping
        "Completed Today" beside it. A workflow counts for today if its
        MOST RECENT completion was today (a workflow advanced across
        midnight belongs to the day it last moved), matching how the
        browser-side version read job.updated_at.
        """
        rows = self._session.exec(select(CompletedWorkflowRow)).all()
        today = datetime.now().date()
        return {
            "minutes_saved": sum(row.minutes_saved for row in rows),
            "lifetime_production": len(rows),
            "completed_today": sum(1 for row in rows if _local_date(row.last_completed_at) == today),
        }


def _as_naive_utc(value: datetime | None) -> datetime | None:
    """Normalize a Job timestamp to naive UTC before it is stored or
    compared against a stored one.

    Not cosmetic: JobService creates timestamps with
    datetime.now(timezone.utc) (tz-AWARE), while the same value read back
    out of SQLite comes back tz-NAIVE, and comparing the two raises
    TypeError. Both real call sites happen to hand over
    repository-loaded (already naive) Jobs, so this normalization is what
    keeps that from being a latent trap for the next caller who passes a
    freshly-constructed Job -- see the second-sync test in
    tests/infra/test_lifetime_metrics.py.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _local_date(value: datetime | None):
    """The local calendar date of a stored completion timestamp.

    Stored values are naive UTC (see _as_naive_utc), so a naive value is
    interpreted as UTC -- exactly what the web UI does when it appends "Z"
    to a timestamp string that carries no offset (web/src/status.ts).
    "Today" is then the operator's own local date; the Controller is a
    single-operator local desktop app, so the API process and the browser
    always share a clock and a timezone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().date()
