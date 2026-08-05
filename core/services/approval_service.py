"""ApprovalService — owns an Approval's payload, resolution, and persistence.

Design spec Section 3: "An engine reports 'I need a decision' plus the info
needed to make it; the Controller owns presenting it, persisting the
outcome, and returning the result back through the adapter. No
engine-specific approval logic lives in the Core."
"""

from __future__ import annotations

from typing import Any

from core.domain.approval import Approval, ApprovalStatus
from core.events.event_bus import EventBus
from core.repositories.approval_repository import ApprovalRepository


class ApprovalService:
    def __init__(self, approval_repository: ApprovalRepository, event_bus: EventBus) -> None:
        self._approvals = approval_repository
        self._events = event_bus

    def request_approval(self, job_id: str, type: str, prompt_payload: dict[str, Any]) -> Approval:
        """Persist a new pending Approval and publish ApprovalRequested.
        Called by JobService when a monitor() poll reports an
        ApprovalRequest on the adapter's EngineStatus.
        """
        raise NotImplementedError

    def get_approval(self, approval_id: str) -> Approval | None:
        raise NotImplementedError

    def list_pending(self) -> list[Approval]:
        raise NotImplementedError

    def resolve(self, approval_id: str, decision: dict[str, Any]) -> Approval:
        """Persist the decision, publish ApprovalResolved. Does not itself
        resume the Job — the Execution Coordinator reacts to
        ApprovalResolved and routes the decision back through the adapter.
        """
        raise NotImplementedError
