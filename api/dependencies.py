"""Composition root — wires concrete infra/ implementations into
Depends()-injectable Application Service instances for route handlers.

Design spec Section 1: "Dependencies point inward: core/ ... has zero
imports from api/ or infra/." This module is the one place in api/ that
is expected to import from infra/db (the reverse would violate that
rule) — route handlers in api/main.py depend only on the functions here,
never on infra/ directly.

Scope note (Step 1 foundation pass): this module only *constructs* and
*wires together* objects — it never calls a method that performs real
orchestration logic. Every constructed service's methods still
`raise NotImplementedError` internally (JobService, PipelineService,
ApprovalService, EngineRegistryService are all unchanged from the
previous pass). Constructing them is safe because none of their
`__init__` bodies do anything beyond storing their dependencies (verified
during the previous pass — see core/services/*.py).

Step 3 addition: get_adapter_factory() provides the AdapterFactory
EngineRegistryService now requires to resolve adapters on demand (see
core/services/engine_registry_service.py). DynamicAdapterFactory is
itself stateless, so returning a fresh instance per call (rather than a
cached singleton) costs nothing and keeps the whole chain — repository,
factory, service — equally request-scoped and equally free of
process-lifetime state.

Step 5 addition: get_execution_coordinator() is now wired, since
api/main.py's POST /jobs and POST /jobs/{id}/launch routes call
coordinator.evaluate() directly (a synchronous bridge — see
core/execution/coordinator.py's module docstring for why events aren't
used yet). A fresh ExecutionCoordinator per request, exactly like every
other service here — no process-lifetime singleton. FastAPI caches each
Depends() result per request, so get_job_service()'s own internal
get_engine_registry_service() call and get_execution_coordinator()'s
engine_registry parameter resolve to the same instance within one
request, not two separate EngineRegistryService objects.
"""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends
from sqlmodel import Session

from core.adapters.adapter_factory import AdapterFactory
from core.events.event_bus import EventBus, InMemoryEventBus
from core.execution.coordinator import ExecutionCoordinator
from core.repositories.job_event_repository import JobEventRepository
from core.services.approval_service import ApprovalService
from core.services.engine_registry_service import EngineRegistryService
from core.services.job_service import JobService
from core.services.monitoring_service import MonitoringService
from core.services.pipeline_service import PipelineService
from infra.adapters.adapter_factory import DynamicAdapterFactory
from infra.db.session import engine
from infra.db.sqlite_repositories import (
    SqliteApprovalRepository,
    SqliteEngineRepository,
    SqliteJobEventRepository,
    SqliteJobRepository,
    SqlitePipelineRepository,
)
from infra.lifetime_metrics import LifetimeMetricsLedger
from infra.runtime_publisher import RuntimePublisher

# One in-process Event Bus for the whole application (design spec Section 2:
# "an in-process, framework-independent event bus"). A single module-level
# instance, not re-created per request, so subscribers registered once
# (e.g. a future ExecutionCoordinator) see every published event.
_event_bus = InMemoryEventBus()


def get_event_bus() -> EventBus:
    return _event_bus


def get_session() -> Generator[Session, None, None]:
    """Per-request SQLModel Session, closed after the request completes."""
    with Session(engine) as session:
        yield session


# -- Repositories ----------------------------------------------------------
# Each factory below actually depends on get_session() through FastAPI's
# Depends() — the previous version of this file wrongly used a bare
# `= None` default, which FastAPI would never have filled in with a real
# Session. Fixed to use Depends() throughout, which is what actually
# makes this a composition root rather than a set of functions that would
# blow up with AttributeError the moment any repository touched
# self._session.

def get_job_repository(session: Session = Depends(get_session)) -> SqliteJobRepository:
    return SqliteJobRepository(session)


def get_pipeline_repository(session: Session = Depends(get_session)) -> SqlitePipelineRepository:
    return SqlitePipelineRepository(session)


def get_approval_repository(session: Session = Depends(get_session)) -> SqliteApprovalRepository:
    return SqliteApprovalRepository(session)


def get_engine_repository(session: Session = Depends(get_session)) -> SqliteEngineRepository:
    return SqliteEngineRepository(session)


def get_job_event_repository(session: Session = Depends(get_session)) -> SqliteJobEventRepository:
    return SqliteJobEventRepository(session)


# -- Application Services ---------------------------------------------------
# get_adapter_factory()/get_engine_registry_service() must be defined before
# get_job_service() below, since Step 4's JobService now depends on
# EngineRegistryService (to resolve+validate against the target engine at
# creation time) and Python evaluates Depends(get_engine_registry_service)
# as a default-argument value at function-definition time, not lazily.

def get_adapter_factory() -> AdapterFactory:
    return DynamicAdapterFactory()


def get_engine_registry_service(
    repository: SqliteEngineRepository = Depends(get_engine_repository),
    adapter_factory: AdapterFactory = Depends(get_adapter_factory),
) -> EngineRegistryService:
    return EngineRegistryService(repository, adapter_factory)


def get_job_service(
    repository: SqliteJobRepository = Depends(get_job_repository),
    event_bus: EventBus = Depends(get_event_bus),
    engine_registry: EngineRegistryService = Depends(get_engine_registry_service),
    job_event_repository: JobEventRepository = Depends(get_job_event_repository),
) -> JobService:
    return JobService(repository, event_bus, engine_registry, job_event_repository)


def get_pipeline_service(
    repository: SqlitePipelineRepository = Depends(get_pipeline_repository),
    event_bus: EventBus = Depends(get_event_bus),
) -> PipelineService:
    return PipelineService(repository, event_bus)


def get_approval_service(
    repository: SqliteApprovalRepository = Depends(get_approval_repository),
    event_bus: EventBus = Depends(get_event_bus),
) -> ApprovalService:
    return ApprovalService(repository, event_bus)


def get_execution_coordinator(
    job_service: JobService = Depends(get_job_service),
    pipeline_service: PipelineService = Depends(get_pipeline_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    engine_registry: EngineRegistryService = Depends(get_engine_registry_service),
    event_bus: EventBus = Depends(get_event_bus),
) -> ExecutionCoordinator:
    return ExecutionCoordinator(job_service, pipeline_service, approval_service, engine_registry, event_bus)


def get_lifetime_metrics_ledger(session: Session = Depends(get_session)) -> LifetimeMetricsLedger:
    """The permanent completed-workflow ledger behind the Dashboard's
    Time Saved / Lifetime Production tiles. Shares the per-request Session
    with every repository above -- see infra/lifetime_metrics.py for why
    this is a concrete Controller feature rather than a core/ service
    behind a repository Protocol.
    """
    return LifetimeMetricsLedger(session)


def get_monitoring_service(
    job_service: JobService = Depends(get_job_service),
    job_event_repository: JobEventRepository = Depends(get_job_event_repository),
    engine_registry: EngineRegistryService = Depends(get_engine_registry_service),
    coordinator: ExecutionCoordinator = Depends(get_execution_coordinator),
) -> MonitoringService:
    return MonitoringService(job_service, job_event_repository, engine_registry, coordinator)


def get_runtime_publisher(
    job_service: JobService = Depends(get_job_service),
    monitoring_service: MonitoringService = Depends(get_monitoring_service),
) -> RuntimePublisher:
    """Publishes completed Jobs into the shared runtime root (see
    infra/runtime_root.py / infra/runtime_publisher.py). Reads
    VILICITY_RUNTIME_ROOT itself (via resolve_runtime_root()'s default
    argument) rather than taking a path here, matching how every other
    factory in this module resolves its own configuration.
    """
    return RuntimePublisher(job_service, monitoring_service)
