"""SQLite implementations of the core/repositories/ interfaces.

Each class below implements the corresponding Protocol from
core/repositories/ against the ORM rows defined in models.py. Application
Services depend only on the Protocols (core/repositories/__init__.py),
never on these concrete classes directly, so the storage backend can
change without touching JobService/PipelineService/ApprovalService/
EngineRegistryService.

Scope note (Step 4 — Job domain pass): SqliteJobRepository below is now a
real, working implementation (add/get/update/delete/list_* against the
`jobs` table), alongside SqliteEngineRepository from Step 2.
SqlitePipeline*Repository and SqliteApprovalRepository remain UNCHANGED:
every method body still `raise NotImplementedError`, in scope for a
future step.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from core.domain.approval import Approval, ApprovalStatus
from core.domain.engine import Engine, EngineRunReference
from core.domain.job import Job, JobStatus
from core.domain.job_event import JobEvent, JobEventType
from core.domain.pipeline import PipelineDefinition, PipelineRun
from infra.db.models import EngineRow, JobEventRow, JobRow


def _job_to_row(job: Job, row: JobRow | None = None) -> JobRow:
    if row is None:
        row = JobRow(id=job.id)
    row.engine_id = job.engine_id
    row.pipeline_run_id = job.pipeline_run_id
    row.status = job.status.value
    row.config = job.config
    row.run_reference = (
        {
            "engine_id": job.run_reference.engine_id,
            "reference_type": job.run_reference.reference_type,
            "reference_value": job.run_reference.reference_value,
            "created_at": job.run_reference.created_at.isoformat() if job.run_reference.created_at else None,
            "extra": job.run_reference.extra,
        }
        if job.run_reference is not None
        else None
    )
    row.result_summary = job.result_summary
    row.error_summary = job.error_summary
    row.created_at = job.created_at
    row.updated_at = job.updated_at
    return row


def _row_to_job(row: JobRow) -> Job:
    run_reference = None
    if row.run_reference is not None:
        stored_created_at = row.run_reference.get("created_at")
        run_reference = EngineRunReference(
            engine_id=row.run_reference["engine_id"],
            reference_type=row.run_reference["reference_type"],
            reference_value=row.run_reference["reference_value"],
            created_at=datetime.fromisoformat(stored_created_at) if stored_created_at else None,
            extra=row.run_reference.get("extra", {}),
        )
    return Job(
        id=row.id,
        engine_id=row.engine_id,
        config=row.config,
        status=JobStatus(row.status),
        pipeline_run_id=row.pipeline_run_id,
        run_reference=run_reference,
        result_summary=row.result_summary,
        error_summary=row.error_summary,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqliteJobRepository:
    """Implements core.repositories.job_repository.JobRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, job: Job) -> None:
        row = _job_to_row(job)
        self._session.add(row)
        self._session.commit()

    def get(self, job_id: str) -> Job | None:
        row = self._session.get(JobRow, job_id)
        return _row_to_job(row) if row is not None else None

    def update(self, job: Job) -> None:
        row = self._session.get(JobRow, job.id)
        if row is None:
            raise ValueError(f"Cannot update non-existent job {job.id!r}")
        _job_to_row(job, row)
        self._session.add(row)
        self._session.commit()

    def delete(self, job_id: str) -> None:
        row = self._session.get(JobRow, job_id)
        if row is not None:
            self._session.delete(row)
            self._session.commit()

    def list_by_status(self, status: JobStatus) -> list[Job]:
        rows = self._session.exec(select(JobRow).where(JobRow.status == status.value)).all()
        return [_row_to_job(row) for row in rows]

    def list_by_pipeline_run(self, pipeline_run_id: str) -> list[Job]:
        rows = self._session.exec(select(JobRow).where(JobRow.pipeline_run_id == pipeline_run_id)).all()
        return [_row_to_job(row) for row in rows]

    def list_all(self) -> list[Job]:
        rows = self._session.exec(select(JobRow)).all()
        return [_row_to_job(row) for row in rows]


class SqlitePipelineDefinitionRepository:
    """Implements core.repositories.pipeline_repository.PipelineDefinitionRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, definition: PipelineDefinition) -> None:
        raise NotImplementedError

    def get(self, definition_id: str) -> PipelineDefinition | None:
        raise NotImplementedError

    def list_all(self) -> list[PipelineDefinition]:
        raise NotImplementedError


class SqlitePipelineRunRepository:
    """Implements core.repositories.pipeline_repository.PipelineRunRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: PipelineRun) -> None:
        raise NotImplementedError

    def get(self, run_id: str) -> PipelineRun | None:
        raise NotImplementedError

    def update(self, run: PipelineRun) -> None:
        raise NotImplementedError

    def list_all(self) -> list[PipelineRun]:
        raise NotImplementedError


class SqlitePipelineRepository(SqlitePipelineDefinitionRepository, SqlitePipelineRunRepository):
    """Implements the unioned core.repositories.pipeline_repository.PipelineRepository
    Protocol by combining both halves — mirrors the Protocol-level split in
    core/repositories/pipeline_repository.py exactly, so there is no
    method-name collision between definition and run persistence.

    __init__ is inherited from SqlitePipelineDefinitionRepository via MRO
    (both parents' __init__ are identical: store the session), so it is
    deliberately not redefined here.
    """


class SqliteApprovalRepository:
    """Implements core.repositories.approval_repository.ApprovalRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, approval: Approval) -> None:
        raise NotImplementedError

    def get(self, approval_id: str) -> Approval | None:
        raise NotImplementedError

    def update(self, approval: Approval) -> None:
        raise NotImplementedError

    def list_by_status(self, status: ApprovalStatus) -> list[Approval]:
        raise NotImplementedError

    def list_by_job(self, job_id: str) -> list[Approval]:
        raise NotImplementedError


def _row_to_engine(row: EngineRow) -> Engine:
    return Engine(
        id=row.id,
        name=row.name,
        adapter_binding=row.adapter_binding,
        capabilities=row.capabilities,
        health_status=row.health_status,
        registered_at=row.registered_at,
    )


class SqliteEngineRepository:
    """Implements core.repositories.engine_repository.EngineRepository.

    The only repository with real query logic this step — Job/Pipeline/
    Approval repositories above remain stubs, in scope for a future step.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, engine: Engine) -> None:
        """Insert a new engines row, or update the existing one for
        engine.id. Engine ids are statically known (video/image/mockup
        generator) and re-registered on every process startup, so this is
        always an update after the first run — never grows unboundedly.
        """
        row = self._session.get(EngineRow, engine.id)
        if row is None:
            row = EngineRow(id=engine.id)
        row.name = engine.name
        row.adapter_binding = engine.adapter_binding
        row.capabilities = engine.capabilities
        row.health_status = engine.health_status
        row.registered_at = engine.registered_at
        self._session.add(row)
        self._session.commit()

    def get(self, engine_id: str) -> Engine | None:
        row = self._session.get(EngineRow, engine_id)
        return _row_to_engine(row) if row is not None else None

    def list_all(self) -> list[Engine]:
        rows = self._session.exec(select(EngineRow)).all()
        return [_row_to_engine(row) for row in rows]


def _row_to_job_event(row: JobEventRow) -> JobEvent:
    return JobEvent(
        id=row.id,
        job_id=row.job_id,
        engine_id=row.engine_id,
        event_type=JobEventType(row.event_type),
        occurred_at=row.occurred_at,
        metadata=row.event_metadata,
    )


class SqliteJobEventRepository:
    """Implements core.repositories.job_event_repository.JobEventRepository.
    Real from the start (Step 8) — events are append-only, so there is no
    update()/delete() to implement.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: JobEvent) -> None:
        row = JobEventRow(
            id=event.id,
            job_id=event.job_id,
            engine_id=event.engine_id,
            event_type=event.event_type.value,
            occurred_at=event.occurred_at,
            event_metadata=event.metadata,
        )
        self._session.add(row)
        self._session.commit()

    def list_by_job(self, job_id: str) -> list[JobEvent]:
        rows = self._session.exec(
            select(JobEventRow).where(JobEventRow.job_id == job_id).order_by(JobEventRow.occurred_at)
        ).all()
        return [_row_to_job_event(row) for row in rows]

    def list_by_engine(self, engine_id: str) -> list[JobEvent]:
        rows = self._session.exec(
            select(JobEventRow).where(JobEventRow.engine_id == engine_id).order_by(JobEventRow.occurred_at)
        ).all()
        return [_row_to_job_event(row) for row in rows]

    def list_recent(self, limit: int) -> list[JobEvent]:
        """Step 9: most recent events across every job/engine, newest
        first, for the dashboard's global activity timeline.
        """
        rows = self._session.exec(
            select(JobEventRow).order_by(JobEventRow.occurred_at.desc()).limit(limit)
        ).all()
        return [_row_to_job_event(row) for row in rows]
