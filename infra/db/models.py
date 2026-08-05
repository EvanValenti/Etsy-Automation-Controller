"""SQLModel ORM row definitions for Controller-owned orchestration state
(jobs, approvals, pipeline definitions/runs, engine registry).

Design spec Section 6: "The Controller never persists a copy of engine
business state — only references (EngineRunReference fields: paths, run
IDs, manifest locations, timestamps)." Every table below stores Controller
orchestration state only; no engine artifact content is ever duplicated
here.

V1 polish addition: CompletedWorkflowRow (see its own docstring) is the
one table whose rows deliberately survive the deletion of the Job rows
they were derived from — lifetime accomplishment, not retained records.

Scope note (Step 4 — Job domain pass): JobRow now matches the Job domain
entity's real field set (core/domain/job.py) — `error` renamed to
`error_summary`, `result_summary` added, `started_at`/`completed_at`
replaced by a single `updated_at`. No Job rows existed under the
previous column set (every Job method was an unimplemented stub through
Step 3), so this is a safe schema refinement, not a breaking migration of
real data. Approval/Pipeline rows remain unchanged, still backing
stubbed repositories.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlmodel import JSON, Column, Field, SQLModel


class JobRow(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True)
    engine_id: str
    pipeline_run_id: Optional[str] = None
    status: str
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    run_reference: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    result_summary: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    error_summary: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ApprovalRow(SQLModel, table=True):
    __tablename__ = "approvals"

    id: str = Field(primary_key=True)
    job_id: str
    type: str
    prompt_payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str
    decision: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class PipelineDefinitionRow(SQLModel, table=True):
    __tablename__ = "pipeline_definitions"

    id: str = Field(primary_key=True)
    name: str
    steps: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: Optional[datetime] = None


class PipelineRunRow(SQLModel, table=True):
    __tablename__ = "pipeline_runs"

    id: str = Field(primary_key=True)
    definition_id: str
    status: str
    current_step_id: Optional[str] = None
    job_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class EngineRow(SQLModel, table=True):
    __tablename__ = "engines"

    id: str = Field(primary_key=True)
    name: str
    adapter_binding: str
    capabilities: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    health_status: str = "unknown"
    registered_at: Optional[datetime] = None


class JobEventRow(SQLModel, table=True):
    """Step 8 — persistent Job timeline. `engine_id` is denormalized from
    the owning Job (see core/domain/job_event.py's docstring for why) so
    per-engine aggregate queries never need a join.
    """

    __tablename__ = "job_events"

    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    engine_id: str = Field(index=True)
    event_type: str
    occurred_at: datetime
    event_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class CompletedWorkflowRow(SQLModel, table=True):
    """One finished unit of operator work, recorded permanently.

    The one table here that deliberately OUTLIVES the Job rows it was
    derived from. Every other table's rows disappear with the work they
    describe; these do not, because they answer a different question:
    "what has this Controller accomplished," not "what jobs are currently
    retained." Deleting job history is housekeeping — it must never reduce
    Time Saved or Lifetime Production (see infra/lifetime_metrics.py for
    the full rationale and how rows get written).

    `workflow_key` is the primary key, so re-recording the same workflow
    (which happens constantly — see the ledger's sync-on-read) updates one
    row instead of inflating the count. It is NOT a Job id: engines that
    create a fresh Job row per stage-advance collapse into a single key.

    Still no engine business state (design spec Section 6): minutes_saved
    is a Controller-side estimate, and the timestamps are Controller Job
    timestamps. Nothing an engine produced is copied here.
    """

    __tablename__ = "completed_workflows"

    workflow_key: str = Field(primary_key=True)
    engine_id: str = Field(index=True)
    minutes_saved: int = 0
    first_completed_at: Optional[datetime] = None
    last_completed_at: Optional[datetime] = None
