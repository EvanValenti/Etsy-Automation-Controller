"""EngineAdapter protocol + supporting types + base class + AdapterFactory.

Design spec Section 4: "The one contract the Core depends on; everything
engine-specific hides behind it."
"""

from core.adapters.adapter_factory import AdapterFactory, UnknownAdapterBindingError
from core.adapters.engine_adapter import (
    EngineAdapter,
    BaseEngineAdapter,
    CancelSupport,
    EngineCapabilities,
    EngineLaunchError,
    ImplementationStatus,
    OnRunReference,
    ValidationResult,
    EngineStatus,
    EngineResult,
    NotImplementedCapability,
)

__all__ = [
    "AdapterFactory",
    "UnknownAdapterBindingError",
    "EngineAdapter",
    "BaseEngineAdapter",
    "CancelSupport",
    "EngineCapabilities",
    "EngineLaunchError",
    "ImplementationStatus",
    "OnRunReference",
    "ValidationResult",
    "EngineStatus",
    "EngineResult",
    "NotImplementedCapability",
]
