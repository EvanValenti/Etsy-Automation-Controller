"""EventBus protocol and the V1 in-memory pub/sub implementation.

Design spec Section 2 / Section "Guiding principles" #5 ("Don't overbuild
V1 ... e.g. in-memory event bus, not a message broker"). The protocol is
the stable contract; InMemoryEventBus is the simplest correct
implementation behind it and is expected to be swappable later without
touching any publisher or subscriber.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Protocol, Type, TypeVar

from core.events.events import DomainEvent

E = TypeVar("E", bound=DomainEvent)
EventHandler = Callable[[DomainEvent], None]


class EventBus(Protocol):
    """Minimal publish/subscribe contract. No delivery guarantees beyond
    "handlers registered for this event type are called, in-process,
    synchronously, in registration order" for the V1 implementation —
    a future implementation may change this without changing the protocol.
    """

    def publish(self, event: DomainEvent) -> None: ...

    def subscribe(self, event_type: Type[DomainEvent], handler: EventHandler) -> None: ...


class InMemoryEventBus:
    """Synchronous, in-process EventBus. No external broker, no persistence
    of undelivered events, no retry/redelivery. Sufficient for a
    single-process V1 Controller; intentionally not sufficient for a
    future multi-process deployment (see EventBus protocol docstring).
    """

    def __init__(self) -> None:
        self._handlers: dict[Type[DomainEvent], list[EventHandler]] = defaultdict(list)

    def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers.get(type(event), []):
            handler(event)

    def subscribe(self, event_type: Type[DomainEvent], handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)
