"""PipelineService — owns a PipelineDefinition's shape and a PipelineRun's persisted state.

Design spec Section 3a division-of-ownership table: "A Pipeline definition's
shape; a PipelineRun's persisted state" -> PipelineService. Step
*advancement timing* belongs to the Execution Coordinator, not here —
PipelineService only persists whatever state the Coordinator tells it to
record, the same relationship JobService has with the Coordinator.
"""

from __future__ import annotations

from core.domain.pipeline import PipelineDefinition, PipelineRun, PipelineStepSpec
from core.events.event_bus import EventBus
from core.repositories.pipeline_repository import PipelineRepository


class PipelineService:
    def __init__(self, pipeline_repository: PipelineRepository, event_bus: EventBus) -> None:
        self._pipelines = pipeline_repository
        self._events = event_bus

    def create_definition(self, name: str, steps: list[PipelineStepSpec]) -> PipelineDefinition:
        raise NotImplementedError

    def get_definition(self, definition_id: str) -> PipelineDefinition | None:
        raise NotImplementedError

    def list_definitions(self) -> list[PipelineDefinition]:
        raise NotImplementedError

    def start_run(self, definition_id: str) -> PipelineRun:
        """Persist a new PipelineRun in PENDING status. Does not launch any
        Job — the Execution Coordinator does that upon seeing this run's
        first step become eligible.
        """
        raise NotImplementedError

    def get_run(self, run_id: str) -> PipelineRun | None:
        raise NotImplementedError

    def list_runs(self) -> list[PipelineRun]:
        raise NotImplementedError

    def record_step_advanced(self, run_id: str, next_step_id: str | None) -> None:
        """Called only by the Execution Coordinator, never by delivery-layer
        code, in response to its own EngineCompleted handling.
        """
        raise NotImplementedError

    def record_completed(self, run_id: str) -> None:
        raise NotImplementedError
