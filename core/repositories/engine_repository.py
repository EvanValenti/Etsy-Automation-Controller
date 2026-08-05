"""EngineRepository interface. SQLite implementation: infra/db/sqlite_repositories.py.

Engines are statically registered at startup (design spec Section 3), so
this repository is read-mostly in practice — `upsert` exists to support
re-registration on every Controller startup (capabilities may have
changed since the last run, e.g. a stubbed launch() becoming real).
"""

from __future__ import annotations

from typing import Protocol

from core.domain.engine import Engine


class EngineRepository(Protocol):
    def upsert(self, engine: Engine) -> None: ...

    def get(self, engine_id: str) -> Engine | None: ...

    def list_all(self) -> list[Engine]: ...
