"""PipelineRepository interface. SQLite implementation: infra/db/sqlite_repositories.py.

Covers both PipelineDefinition and PipelineRun persistence — split here
into two Protocols so a caller that only ever reads definitions (e.g. a
"list available pipelines" UI action) doesn't need a dependency capable of
mutating runs, and vice versa.
"""

from __future__ import annotations

from typing import Protocol

from core.domain.pipeline import PipelineDefinition, PipelineRun


class PipelineDefinitionRepository(Protocol):
    def add(self, definition: PipelineDefinition) -> None: ...

    def get(self, definition_id: str) -> PipelineDefinition | None: ...

    def list_all(self) -> list[PipelineDefinition]: ...


class PipelineRunRepository(Protocol):
    def add(self, run: PipelineRun) -> None: ...

    def get(self, run_id: str) -> PipelineRun | None: ...

    def update(self, run: PipelineRun) -> None: ...

    def list_all(self) -> list[PipelineRun]: ...


class PipelineRepository(PipelineDefinitionRepository, PipelineRunRepository, Protocol):
    """Convenience union for callers (e.g. PipelineService) that need both halves."""
