"""EngineRegistryService — owns the static Engine registry.

Design spec Section 3: "Engine — registry entry ... Statically registered
at startup, not user-created." This service is the only place adapters
are bound to Engine rows and discover() is invoked to populate
capabilities.

Step 3 scope note: get_adapter() no longer relies on an in-memory,
per-instance adapter map (the Step 2 limitation). It now resolves the
persisted Engine row's `adapter_binding` string through an injected
AdapterFactory and constructs a fresh adapter instance on every call —
this removes any dependency on process-lifetime adapter instances and
keeps both adapters and this service itself free of adapter-lifetime
state. discover() behavior is unchanged: register() still calls it
exactly once per registration, on an adapter instance the caller already
constructed (see api/main.py's startup routine, which now also goes
through the same AdapterFactory rather than importing adapter classes
directly).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

from core.adapters.adapter_factory import AdapterFactory
from core.adapters.engine_adapter import EngineAdapter
from core.domain.engine import Engine
from core.repositories.engine_repository import EngineRepository


class UnknownEngineError(Exception):
    """Raised when no Engine row exists for the given engine_id. Distinct
    from AdapterFactory's UnknownAdapterBindingError (which means "the
    engine exists, but its recorded binding no longer resolves").
    """

    def __init__(self, engine_id: str) -> None:
        super().__init__(f"No engine registered under engine_id={engine_id!r}")
        self.engine_id = engine_id


class EngineRegistryService:
    def __init__(self, engine_repository: EngineRepository, adapter_factory: AdapterFactory) -> None:
        self._engines = engine_repository
        self._adapter_factory = adapter_factory

    def register(self, engine_id: str, name: str, adapter: EngineAdapter) -> Engine:
        """Call discover() on the given (already-constructed) adapter
        instance and persist the resulting Engine row, including the
        adapter_binding string derived from its class. That string is
        exactly what get_adapter() later hands to the AdapterFactory to
        reconstruct an equivalent, freshly-built instance on demand — no
        reference to `adapter` itself is retained after this call returns.
        """
        capabilities = adapter.discover()
        engine = Engine(
            id=engine_id,
            name=name,
            adapter_binding=f"{type(adapter).__module__}.{type(adapter).__qualname__}",
            capabilities=dataclasses.asdict(capabilities),
            health_status=capabilities.health_status,
            registered_at=datetime.now(timezone.utc),
        )
        self._engines.upsert(engine)
        return engine

    def get_adapter(self, engine_id: str) -> EngineAdapter:
        """Resolve a fresh EngineAdapter instance for engine_id by reading
        its persisted adapter_binding from the Engine repository and
        asking the AdapterFactory to construct it. Never reads any
        in-memory map tied to this or any other service instance — two
        completely independent EngineRegistryService instances (e.g. two
        separate HTTP requests' worth of dependency injection) resolve
        the same engine_id identically, because the shared source of
        truth is the persisted `adapter_binding` string, not process
        memory.
        """
        engine = self._engines.get(engine_id)
        if engine is None:
            raise UnknownEngineError(engine_id)
        return self._adapter_factory.create(engine.adapter_binding)

    def get_engine(self, engine_id: str) -> Engine | None:
        return self._engines.get(engine_id)

    def list_engines(self) -> list[Engine]:
        return self._engines.list_all()
