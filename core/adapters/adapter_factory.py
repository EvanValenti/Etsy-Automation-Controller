"""AdapterFactory — the contract for constructing a fresh EngineAdapter
instance from its persisted `adapter_binding` string, on demand.

Step 3 objective: adapter instances must never live only inside one
service instance's memory. The Engine registry (see
core/domain/engine.py::Engine.adapter_binding) stores a dotted
"module.ClassName" string, never a live adapter object — this Protocol is
what turns that string back into a real, usable EngineAdapter, fresh,
every time it's asked. EngineRegistryService depends only on this
Protocol, never on a concrete factory implementation (the concrete
DynamicAdapterFactory lives in infra/adapters/, mirroring the existing
EngineAdapter-interface-in-core / concrete-adapters-in-infra split).
"""

from __future__ import annotations

from typing import Protocol

from core.adapters.engine_adapter import EngineAdapter


class UnknownAdapterBindingError(Exception):
    """Raised when an adapter_binding string cannot be resolved to a real,
    importable EngineAdapter class — e.g. a typo, or the referenced
    module/class was renamed or removed since the binding was persisted.
    Distinct from EngineRegistryService.UnknownEngineError (which means
    "no such engine_id exists at all"): this means "the engine exists, but
    its recorded binding no longer resolves to real code."
    """

    def __init__(self, adapter_binding: str, reason: str) -> None:
        super().__init__(f"Cannot construct adapter from binding {adapter_binding!r}: {reason}")
        self.adapter_binding = adapter_binding
        self.reason = reason


class AdapterFactory(Protocol):
    """Construct a brand-new EngineAdapter instance from a binding string.

    Implementations must be stateless and must not cache/return the same
    instance across calls — adapters are stateless by design (Step 3), so
    there is never a correctness reason to share one.
    """

    def create(self, adapter_binding: str) -> EngineAdapter: ...
