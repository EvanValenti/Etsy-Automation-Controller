"""Approval domain entity.

Design spec Section 3:

    Approval — a pending or resolved human decision, generic across engines
    and decision kinds. An engine reports "I need a decision" plus the info
    needed to make it; the Controller owns presenting it, persisting the
    outcome, and returning the result back through the adapter. No
    engine-specific approval logic lives in the Core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"


@dataclass
class Approval:
    """A human decision point raised by an engine, owned generically by the Core.

    `type` is deliberately a free-form string (not an enum) so new decision
    kinds (confirmation, selection, review, rejection, retry, ...) never
    require a schema change. `prompt_payload` is whatever the engine needs
    shown to a human (candidate images, an order list, a background choice,
    etc.) — opaque to the Core.
    """

    id: str
    job_id: str
    type: str
    prompt_payload: dict[str, Any]
    status: ApprovalStatus = ApprovalStatus.PENDING
    decision: dict[str, Any] | None = None
    created_at: datetime | None = None
    resolved_at: datetime | None = None
