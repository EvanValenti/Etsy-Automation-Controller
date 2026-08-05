"""Domain entities: Engine, Job, Approval, PipelineDefinition, PipelineRun, EngineRunReference.

Per the design spec Section 3, these are the four first-class entities persisted
in SQLite behind repository interfaces (Pipeline is split into two: Definition + Run).
"""

from core.domain.engine import Engine, EngineRunReference
from core.domain.job import Job, JobStatus
from core.domain.approval import Approval, ApprovalStatus
from core.domain.pipeline import PipelineDefinition, PipelineRun, PipelineStepSpec

__all__ = [
    "Engine",
    "EngineRunReference",
    "Job",
    "JobStatus",
    "Approval",
    "ApprovalStatus",
    "PipelineDefinition",
    "PipelineRun",
    "PipelineStepSpec",
]
