"""The EngineAdapter Protocol and its supporting value types.

Design spec Section 4 (verbatim contract):

    class EngineAdapter(Protocol):
        def discover(self) -> EngineCapabilities: ...
        def validate(self, config: dict) -> ValidationResult: ...
        def launch(self, config: dict) -> EngineRunReference: ...
        def monitor(self, ref: EngineRunReference) -> EngineStatus: ...
        def cancel(self, ref: EngineRunReference) -> bool: ...
        def collect_results(self, ref: EngineRunReference) -> EngineResult: ...

`cancel()` is always best-effort, never guaranteed — capability-gated by
what `discover()` reports for a given engine. `launch()` may raise
NotImplementedCapability for engines whose launch path is genuinely
stubbed today (image-generator, mockup-generator per the V1 adapters
table) — this is a first-class, expected outcome, not an error condition
callers should treat as a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable

from core.domain.engine import EngineRunReference

# Step 7 addition. See EngineAdapter.launch()'s docstring below for the
# full rationale — this is the one narrow, disclosed Protocol extension
# this step introduces, needed specifically to make real, cross-request
# cancellation possible without a larger redesign of the execution model.
OnRunReference = Callable[[EngineRunReference], None]


class CancelSupport(str, Enum):
    """How much a given engine can honor a cancel request, per discover()."""

    UNSUPPORTED = "unsupported"
    STOP_AFTER_CURRENT_STEP = "stop_after_current_step"
    GRACEFUL = "graceful"
    # Step 7 addition: a hard, forceful process-tree termination — the
    # process is killed outright, not given a chance to shut down cleanly.
    # Distinct from GRACEFUL (which implies the engine gets a chance to
    # wind down) — video-generator's real cancel() is IMMEDIATE, since the
    # only externally-imposable control over a subprocess.run()-based
    # engine we don't own the source of is termination, not a cooperative
    # shutdown signal it doesn't listen for.
    IMMEDIATE = "immediate"


class ImplementationStatus(str, Enum):
    """How much of an engine's Controller-facing integration is genuinely
    real today, per the deep architecture extraction
    (CONTROLLER-V1-ARCHITECTURE-HANDBOOK.md /
    CONTROLLER-V1-ARCHITECTURE-INSPECTION.md) — not aspirational, not a
    statement about how much of this adapter's own Python code is
    written. A "real" engine can still have a mostly-stubbed adapter
    (launch/monitor/cancel/collect_results) if the underlying engine
    repo itself has a genuine non-interactive entry point; a "stub"
    engine has neither.
    """

    REAL = "real"
    PARTIAL = "partial"
    STUB = "stub"


@dataclass
class EngineCapabilities:
    """Static capability metadata returned by discover(), cached on the
    Engine registry row at startup rather than re-queried on every use.

    Every boolean/status field here is meant to describe the underlying
    engine repo's actual, currently-verifiable integration readiness —
    grounded in what discover() can determine by inspecting that repo's
    real files at startup — never a hardcoded guess. Where discover()
    cannot determine something (e.g. no version marker exists), the
    conservative default (None / False / "unknown") is used rather than
    an optimistic invented value.
    """

    engine_id: str
    display_name: str
    supports_launch: bool
    version: str | None = None
    cancel_support: CancelSupport = CancelSupport.UNSUPPORTED
    supports_monitoring: bool = False
    supports_approvals: bool = False
    supports_pipelines: bool = False
    max_concurrent_runs: int = 1
    launch_config_schema: dict[str, Any] = field(default_factory=dict)
    possible_approval_types: list[str] = field(default_factory=list)
    health_status: str = "unknown"  # "healthy" / "degraded" / "unreachable" / "unknown"
    implementation_status: ImplementationStatus = ImplementationStatus.STUB
    notes: list[str] = field(default_factory=list)

    @property
    def supports_cancel(self) -> bool:
        """Derived from cancel_support so both the coarse boolean the Step 2
        capability list asks for and the richer enum detail are available
        without the two ever drifting out of sync.
        """
        return self.cancel_support != CancelSupport.UNSUPPORTED


@dataclass
class ValidationResult:
    """Result of a pre-flight validate() call, before a Job is even created."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)


class RunState(str, Enum):
    RUNNING = "running"
    WAITING_ON_APPROVAL = "waiting_on_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class ApprovalRequest:
    """Payload an adapter attaches to EngineStatus when it needs a human
    decision to proceed. Shape is engine-specific and opaque to the Core.
    """

    type: str
    prompt_payload: dict[str, Any]


@dataclass
class EngineStatus:
    """Point-in-time status returned by monitor(). JobService translates
    this into JobProgressUpdated / ApprovalRequested events.
    """

    state: RunState
    progress: dict[str, Any] = field(default_factory=dict)
    approval_request: ApprovalRequest | None = None


@dataclass
class EngineResult:
    """Normalized, terminal result returned by collect_results(). This is
    where defensive parsing of each engine's real (sometimes drifted)
    manifest schema lives — scoped per adapter, never in the Core.
    """

    success: bool
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


class NotImplementedCapability(Exception):
    """Raised by an adapter method for a capability its engine genuinely
    does not have yet (e.g. launch() for image-generator/mockup-generator
    today, per the V1 adapters table in the design spec Section 4). Callers
    (the Execution Coordinator) must treat this as an expected, capability-
    gated outcome rather than an unexpected failure.
    """


class EngineLaunchError(Exception):
    """Raised by an adapter's launch() when the underlying engine genuinely
    attempted to run and failed — as opposed to NotImplementedCapability
    (the engine has no launch path at all). Step 6 introduces this: it is
    the one generic, Protocol-level signal the Execution Coordinator is
    allowed to catch and act on (recording a Job failure), specifically so
    it never needs to import or know about any engine-specific exception
    type (e.g. etsy-video-generator's own VideoGenerationError never
    leaves that engine's adapter — see infra/adapters/video_generator/adapter.py).

    `error` is a normalized, JSON-serializable payload
    ({"category": str, "message": str, "detail": dict}) matching the shape
    core/domain/job.py::JobError.to_dict() produces, so a caller can build
    a JobError straight from it without any engine-specific translation.
    """

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", "engine launch failed"))
        self.error = error


@runtime_checkable
class EngineAdapter(Protocol):
    """The one contract the Core depends on; everything engine-specific
    hides behind it. The Core never knows *how* an adapter talks to its
    engine (in-process import, subprocess, future HTTP call).

    Marked @runtime_checkable (Step 4) so DynamicAdapterFactory can
    isinstance()-check a freshly-constructed object before handing it back
    to a caller that now genuinely trusts it for more than discover() —
    e.g. JobService.create_job() calling .validate() (per Step 3 finding
    #3: "worth adding before Jobs start trusting get_adapter()'s return
    value for anything beyond discover()"). A runtime_checkable Protocol
    isinstance() check only verifies method presence, not signatures — that
    is sufficient here: it catches a corrupted/misdirected adapter_binding
    string pointing at some unrelated class, which is the actual risk this
    guards against.
    """

    def discover(self) -> EngineCapabilities: ...

    def validate(self, config: dict[str, Any]) -> ValidationResult: ...

    def launch(
        self, config: dict[str, Any], on_run_reference: OnRunReference | None = None
    ) -> EngineRunReference:
        """Starts the engine and returns its final EngineRunReference once
        the run is terminal (this Protocol method's return contract is
        unchanged from Step 1/6: launch() still ultimately returns one
        EngineRunReference, synchronously, from the caller's point of view).

        `on_run_reference` (Step 7, optional, default None) is the one
        narrow Protocol extension this step introduces: if provided, an
        adapter that spawns a real, independently-supervisable OS process
        MUST call it exactly once, as soon as it has a live process handle
        to report — BEFORE it blocks waiting for that process to finish —
        passing a partial EngineRunReference containing whatever pointer
        (e.g. a PID in `extra`) a later cancel(ref) call would need. This
        is what makes real, cross-request cancellation possible: without
        it, nothing durable exists to cancel until launch() has already
        returned, by which point the run is already over. Adapters that
        don't support real cancellation (image/mockup-generator, or any
        adapter whose launch() still raises NotImplementedCapability) are
        never required to call it — it is optional for exactly that reason.
        """
        ...

    def monitor(self, ref: EngineRunReference) -> EngineStatus: ...

    def cancel(self, ref: EngineRunReference) -> bool: ...

    def collect_results(self, ref: EngineRunReference) -> EngineResult: ...


class BaseEngineAdapter:
    """Convenience base class for concrete adapters. Not required by the
    Protocol (structural typing means any object with the right methods
    satisfies EngineAdapter), but gives concrete adapters a shared place
    for `cancel()`'s default best-effort-unsupported behavior so each
    adapter only needs to override what actually differs.
    """

    engine_id: str

    def discover(self) -> EngineCapabilities:
        raise NotImplementedError

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        raise NotImplementedError

    def launch(
        self, config: dict[str, Any], on_run_reference: OnRunReference | None = None
    ) -> EngineRunReference:
        raise NotImplementedError

    def monitor(self, ref: EngineRunReference) -> EngineStatus:
        raise NotImplementedError

    def cancel(self, ref: EngineRunReference) -> bool:
        """Default: unsupported. Override only if the engine's discover()
        reports a CancelSupport value other than UNSUPPORTED.
        """
        return False

    def collect_results(self, ref: EngineRunReference) -> EngineResult:
        raise NotImplementedError
