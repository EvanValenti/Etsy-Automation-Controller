"""Pipeline domain entities: PipelineDefinition and PipelineRun.

Design spec Section 3:

    Pipeline — split into two:
      PipelineDefinition: a reusable template — an ordered/graph sequence of
      steps, each naming an engine plus how its inputs map from a prior
      step's outputs. Contains orchestration only, never engine-specific logic.
      PipelineRun: one execution of a definition — tracks which Jobs belong
      to it, current step, overall status. PipelineAdvanced / PipelineCompleted
      events fire off its transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


@dataclass
class PipelineStepSpec:
    """One step in a PipelineDefinition.

    `input_mapping` describes how this step's launch config is derived from
    a prior step's EngineResult — orchestration-only, never engine-specific
    business logic (the Core never interprets engine output content, only
    routes references to it).
    """

    step_id: str
    engine_id: str
    input_mapping: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class PipelineDefinition:
    """A reusable, named template describing an ordered/graph sequence of steps."""

    id: str
    name: str
    steps: list[PipelineStepSpec] = field(default_factory=list)
    created_at: datetime | None = None


class PipelineRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PipelineRun:
    """One execution of a PipelineDefinition.

    `job_ids` tracks every Job launched as part of this run, in launch order.
    `current_step_id` is advanced only by the Execution Coordinator reacting
    to EngineCompleted / PipelineAdvanced events — never mutated directly by
    PipelineService.
    """

    id: str
    definition_id: str
    status: PipelineRunStatus = PipelineRunStatus.PENDING
    current_step_id: str | None = None
    job_ids: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    completed_at: datetime | None = None
