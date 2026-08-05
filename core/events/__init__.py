"""In-process, framework-independent event bus — the seam between "something
happened" and "something reacts to it".

Design spec Section 2. The EventBus protocol is what's stable; the
in-memory implementation can be swapped later (e.g. for a multi-process
Controller) without touching publishers or subscribers.
"""

from core.events.event_bus import EventBus, InMemoryEventBus
from core.events.events import (
    DomainEvent,
    JobCreated,
    JobStarted,
    JobProgressUpdated,
    ApprovalRequested,
    ApprovalResolved,
    EngineCompleted,
    EngineFailed,
    PipelineAdvanced,
    PipelineCompleted,
    RetryScheduled,
)

__all__ = [
    "EventBus",
    "InMemoryEventBus",
    "DomainEvent",
    "JobCreated",
    "JobStarted",
    "JobProgressUpdated",
    "ApprovalRequested",
    "ApprovalResolved",
    "EngineCompleted",
    "EngineFailed",
    "PipelineAdvanced",
    "PipelineCompleted",
    "RetryScheduled",
]
