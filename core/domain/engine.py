"""Engine and EngineRunReference domain entities.

Design spec Section 3:

    Engine — registry entry: name, adapter binding, capability flags (from
    discover()), current health/status. Statically registered at startup,
    not user-created.

    EngineRunReference — a generic abstraction over "however this engine
    represents one of its own executions" (manifest path + run ID today;
    could be a job folder, an external API identifier, or something else
    for a future engine). Stores pointers, never a copy of engine state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Engine:
    """A statically registered automation engine known to the Controller.

    `capabilities` is populated by the bound adapter's `discover()` call at
    startup (see core/adapters/engine_adapter.py::EngineCapabilities) and
    cached here rather than re-queried on every use.
    """

    id: str
    name: str
    adapter_binding: str  # dotted import path or registry key for the concrete adapter
    capabilities: dict[str, Any] = field(default_factory=dict)
    health_status: str = "unknown"  # e.g. "healthy" / "degraded" / "unreachable" / "unknown"
    registered_at: datetime | None = None


@dataclass
class EngineRunReference:
    """A pointer to one execution of an engine, owned by that engine.

    The Controller never persists a copy of engine business state — only
    enough here to ask the adapter to look the run up again later
    (manifest path, run ID, job folder, external identifier, etc., depending
    on the engine). `extra` holds engine-specific pointer fields that don't
    warrant a first-class column.
    """

    engine_id: str
    reference_type: str  # e.g. "manifest_path", "job_folder", "external_id"
    reference_value: str
    created_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)
