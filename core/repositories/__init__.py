"""Repository interfaces. SQLite implementations live in infra/db/.

Design spec Section 6: "Orchestration logic depends only on the
interfaces, so the storage backend (e.g. a future Postgres) can change
without touching Application Services."
"""

from core.repositories.job_repository import JobRepository
from core.repositories.job_event_repository import JobEventRepository
from core.repositories.pipeline_repository import PipelineRepository
from core.repositories.approval_repository import ApprovalRepository
from core.repositories.engine_repository import EngineRepository

__all__ = [
    "JobRepository",
    "JobEventRepository",
    "PipelineRepository",
    "ApprovalRepository",
    "EngineRepository",
]
