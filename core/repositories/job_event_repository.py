"""JobEventRepository interface. SQLite implementation: infra/db/sqlite_repositories.py.

Deliberately minimal: add() + list methods. Aggregation (counts, "most
recent event of type X") is done in core/services/monitoring_service.py
over the results of list_by_job()/list_by_engine() rather than here — at
this project's data scale, specialized aggregate-query methods on the
repository would be premature (Step 8 objective 5's own "avoid premature
analytics" instruction applies equally to the repository shape, not just
what gets persisted).

Step 9 addition: list_recent(limit) is the one exception to "no
specialized aggregate queries" — it backs GET /activity, the dashboard's
global activity timeline across every job/engine. Unlike list_by_job()/
list_by_engine(), there is no in-memory way to build this from the
existing methods without first loading the entire event table, which is
exactly the kind of thing a repository-level ORDER BY ... LIMIT exists to
avoid.
"""

from __future__ import annotations

from typing import Protocol

from core.domain.job_event import JobEvent


class JobEventRepository(Protocol):
    def add(self, event: JobEvent) -> None: ...

    def list_by_job(self, job_id: str) -> list[JobEvent]: ...

    def list_by_engine(self, engine_id: str) -> list[JobEvent]: ...

    def list_recent(self, limit: int) -> list[JobEvent]: ...
