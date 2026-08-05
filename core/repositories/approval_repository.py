"""ApprovalRepository interface. SQLite implementation: infra/db/sqlite_repositories.py."""

from __future__ import annotations

from typing import Protocol

from core.domain.approval import Approval, ApprovalStatus


class ApprovalRepository(Protocol):
    def add(self, approval: Approval) -> None: ...

    def get(self, approval_id: str) -> Approval | None: ...

    def update(self, approval: Approval) -> None: ...

    def list_by_status(self, status: ApprovalStatus) -> list[Approval]: ...

    def list_by_job(self, job_id: str) -> list[Approval]: ...
