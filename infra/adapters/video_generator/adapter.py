"""VideoGeneratorAdapter — real launch(), real timeout, real cancel().

Per the handbook (CONTROLLER-V1-ARCHITECTURE-HANDBOOK.md, Chapter C):
etsy-video-generator exposes `run_video_generation(images, preset_key,
output_file) -> Path`, a genuinely non-interactive, tested, importable
function that raises `VideoGenerationError` on failure and never calls
`sys.exit()` or blocks on stdin.

Step 7 rewrite, prompted directly by a real operational gap Step 6's own
live verification exposed: run_video_generation() calls subprocess.run()
internally with NO timeout, and offers no way to interrupt it once
started. A corrupted input image made this concrete — FFmpeg's decoder
retried indefinitely, the calling request hung, and killing the parent
Python process left the FFmpeg child running as a genuine orphan (we only
found this by checking `tasklist` directly, since `pkill` reported
success without it actually being true).

We cannot fix this by editing etsy-video-generator's own source — it
remains a separate, independently-owned engine we integrate with only
through its real, public run_video_generation() function. The only way
to make this genuinely timeout-able and cancel-able from outside is to
run it in a process WE spawn and fully control (see _launch_worker.py),
so this adapter has a real OS process handle — and, critically, kills the
whole process TREE on timeout/cancel (Windows `taskkill /T /F`), not just
the immediate child, which is exactly what orphaned the FFmpeg process in
Step 6.

Ownership design (decided before writing this file, per Step 7's
instructions):
  - Timeout MECHANICS (spawn, poll with a deadline, kill on expiry) are
    fully self-contained in this adapter's launch() — the Coordinator is
    never involved in timeout at all, never sees a deadline, a PID, or a
    process concept of any kind.
  - Cancel MECHANICS (kill process tree, verify death, clean partial
    output) are equally adapter-owned, in cancel().
  - Cancel TRIGGERING is inherently cross-request (something external
    decides "stop this now") and requires core/execution/coordinator.py's
    new cancel_job() to exist — but that method only ever calls the
    generic adapter.cancel(ref) -> bool, never touches a PID or signal
    itself.
  - This requires ONE narrow, disclosed Protocol extension: launch() now
    accepts an optional on_run_reference callback (see
    core/adapters/engine_adapter.py), which this adapter calls the moment
    it has a live, killable process handle — BEFORE it blocks waiting for
    that process — so a concurrent cancel() request has something durable
    to act on. Without this, a Job's run_reference would only ever be
    persisted once the whole run is already over, making real concurrent
    cancellation impossible without a larger redesign.

Configurable timeout (Step 7 requirement: "Do not hardcode magic values
unless unavoidable"): DEFAULT_LAUNCH_TIMEOUT_SECONDS below, overridable
via the AUTOMATION_CONTROLLER_VIDEO_GENERATOR_TIMEOUT_SECONDS environment
variable, read fresh on every launch() call (not cached at import time),
so tests can exercise a short timeout without restarting the process with
a different environment... actually it still requires a restart since
env vars are read once per process in the common case, but reading it
per-call rather than at module import time at least allows a test to
monkeypatch os.environ within the same process if ever needed.

Step 6 config-schema note carried forward unchanged: `output_file` is not
part of a video-generator Job's config — the engine's own
get_next_output_file() remains the sole numbering authority (now called
inside the worker subprocess rather than in-process, but the principle is
identical).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.adapters.engine_adapter import (
    BaseEngineAdapter,
    CancelSupport,
    EngineCapabilities,
    EngineLaunchError,
    EngineResult,
    EngineStatus,
    ImplementationStatus,
    OnRunReference,
    ValidationResult,
)
from core.domain.engine import EngineRunReference
from infra.adapters.discovery_utils import read_text_safely, resolve_engine_repo_root

ENGINE_ID = "etsy-video-generator"
REPO_DIR_NAME = "etsy-video-generator"
_VERSION_LINE_PATTERN = re.compile(r"\*\*Current Version:\*\*\s*(.+)")
_VALID_PRESET_KEYS = {
    "standard",
    "slow-fade-color-variation",
    "wisp-sweep-color-variation",
    "design-reveal",
}
_ENGINE_MODULE_NAME = "_etsy_video_generator_engine_module"

DEFAULT_LAUNCH_TIMEOUT_SECONDS = 120.0
_TIMEOUT_ENV_VAR = "AUTOMATION_CONTROLLER_VIDEO_GENERATOR_TIMEOUT_SECONDS"
_WORKER_SCRIPT_PATH = Path(__file__).resolve().parent / "_launch_worker.py"
_PROCESS_DEATH_POLL_INTERVAL_SECONDS = 0.2
_PROCESS_DEATH_POLL_ATTEMPTS = 15  # ~3 seconds total, generous for Windows process teardown


def _load_engine_module():
    """Import the real etsy-video-generator src/generate_video.py module
    by file path (it is not an installed package — it is a sibling repo).
    Used by validate() for cheap, in-process checks only (module
    reachability, check_ffmpeg_is_available()) — the actual generation
    call always happens in the separate worker process (see launch()),
    never via this cached in-process import.
    """
    if _ENGINE_MODULE_NAME in sys.modules:
        return sys.modules[_ENGINE_MODULE_NAME]

    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    if repo_root is None:
        raise EngineLaunchError(
            {
                "category": "engine_unreachable",
                "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.",
                "detail": {},
            }
        )

    module_path = repo_root / "src" / "generate_video.py"
    spec = importlib.util.spec_from_file_location(_ENGINE_MODULE_NAME, module_path)
    if spec is None or spec.loader is None:
        raise EngineLaunchError(
            {
                "category": "engine_unreachable",
                "message": f"Could not build an import spec for {module_path}",
                "detail": {"module_path": str(module_path)},
            }
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[_ENGINE_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _configured_timeout_seconds() -> float:
    raw = os.environ.get(_TIMEOUT_ENV_VAR)
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_LAUNCH_TIMEOUT_SECONDS


def _is_pid_alive(pid: int) -> bool:
    """Windows-specific liveness check via `tasklist`. Deliberately never
    trusts a kill command's own reported success — Step 6 discovered
    `pkill` claiming success while a process was still alive; every
    termination this adapter performs is followed by an explicit,
    separate liveness re-check like this one, not just a trusted exit code.
    """
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
    )
    return str(pid) in result.stdout


def _kill_process_tree(pid: int) -> bool:
    """Kill pid AND all of its descendants (`taskkill /T`) — a bare
    kill()/terminate() of just the immediate PID is exactly what orphaned
    the FFmpeg process in Step 6, since run_video_generation() spawns
    FFmpeg as a grandchild of this adapter (worker process -> FFmpeg).

    Returns False if the process was already dead before this was called
    (idempotent — safe to call on an already-finished run). Raises
    EngineLaunchError if taskkill runs but the process tree is still
    verifiably alive afterward (a genuine, reportable failure, not
    something to silently retry forever).
    """
    if not _is_pid_alive(pid):
        return False

    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)

    for _ in range(_PROCESS_DEATH_POLL_ATTEMPTS):
        if not _is_pid_alive(pid):
            return True
        time.sleep(_PROCESS_DEATH_POLL_INTERVAL_SECONDS)

    raise EngineLaunchError(
        {
            "category": "process_kill_failed",
            "message": f"Process {pid} (and/or its process tree) did not terminate after taskkill /T /F.",
            "detail": {"pid": pid},
        }
    )


def _read_claimed_paths(work_dir: Path) -> list[Path]:
    """The output paths a (possibly killed) worker claimed before running —
    the video file and the manifest the engine would have written for it.

    Both come from claimed_output.json, i.e. from the ENGINE's own
    get_next_output_file()/manifest_path_for(), never from a naming
    convention reconstructed here: the manifest is not a ".json" sibling of
    the video any more (the engine keeps it in its own metadata/ folder so
    output/ stays a deliverables folder), and this adapter deliberately
    does not encode that layout.
    """
    text = read_text_safely(work_dir / "claimed_output.json")
    if text is None:
        return []
    try:
        claimed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [Path(claimed[key]) for key in ("output_file", "manifest_path") if claimed.get(key)]


def _cleanup_partial_output(work_dir: Path) -> None:
    """A killed worker may have left a partially-written (or fully
    0-byte) video file at whatever path it claimed via
    claimed_output.json (see _launch_worker.py) — the real engine's own
    manifest is only ever written on success, so a video file existing
    with no matching manifest at this point is, by construction, never a
    legitimate completed output. Removing it is what "partial output
    cleanup" (Step 7 requirement) means concretely for this engine.
    """
    for candidate in _read_claimed_paths(work_dir):
        if candidate.is_file():
            candidate.unlink(missing_ok=True)


class VideoGeneratorAdapter(BaseEngineAdapter):
    engine_id = ENGINE_ID

    def discover(self) -> EngineCapabilities:
        """Grounded, read-only inspection of the real sibling repo on disk
        at call time — never hardcoded — per the Step 2 objective that the
        Engine Registry must never hardcode engine capabilities.

        Step 7 change: cancel_support is now CancelSupport.IMMEDIATE
        (previously UNSUPPORTED) — this adapter genuinely implements
        cancel() now. The configured launch timeout is surfaced in notes
        for operator visibility.
        """
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        repo_exists = repo_root is not None

        source_text = read_text_safely(repo_root / "src" / "generate_video.py") if repo_root else None
        readme_text = read_text_safely(repo_root / "README.md") if repo_root else None

        has_run_function = bool(source_text and "def run_video_generation(" in source_text)
        has_error_type = bool(source_text and "class VideoGenerationError" in source_text)
        has_manifest_writer = bool(source_text and "def write_generation_manifest(" in source_text)
        # The engine owns WHERE its manifests live (metadata/<stem>.json
        # today, no longer beside the video) and exposes that as
        # manifest_path_for(); the worker asks the engine rather than
        # rebuilding the convention, so this marker is part of what makes
        # results collectable at all.
        has_manifest_locator = bool(source_text and "def manifest_path_for(" in source_text)

        supports_launch = has_run_function and has_error_type
        supports_monitoring = has_manifest_writer and has_manifest_locator

        version_match = _VERSION_LINE_PATTERN.search(readme_text) if readme_text else None
        version = version_match.group(1).strip() if version_match else None

        notes: list[str] = []
        if not repo_exists:
            health_status = "unreachable"
            notes.append(f"Sibling repo '{REPO_DIR_NAME}' was not found on disk during discovery.")
        elif supports_launch:
            health_status = "healthy"
        else:
            health_status = "degraded"
            notes.append(
                "src/generate_video.py did not contain the expected run_video_generation()/"
                "VideoGenerationError markers — repo may have moved or been restructured "
                "since the last architecture extraction."
            )

        if supports_launch:
            notes.append(
                f"launch() runs the engine in a supervised worker process with a "
                f"{_configured_timeout_seconds():.0f}s timeout (configurable via "
                f"{_TIMEOUT_ENV_VAR}); on timeout or cancel(), the full process tree is "
                "terminated (Windows taskkill /T /F) and any partial output file removed."
            )

        implementation_status = ImplementationStatus.REAL if supports_launch else ImplementationStatus.STUB

        return EngineCapabilities(
            engine_id=ENGINE_ID,
            display_name="Etsy Video Generator",
            version=version,
            supports_launch=supports_launch,
            cancel_support=CancelSupport.IMMEDIATE if supports_launch else CancelSupport.UNSUPPORTED,
            supports_monitoring=supports_monitoring,
            supports_approvals=False,
            supports_pipelines=False,
            max_concurrent_runs=1,
            launch_config_schema=(
                {
                    "images": "list[str] — 3 to 5 confirmed image file paths that exist on disk, "
                    "in display order",
                    "preset_key": "str — one of the engine's registered preset keys "
                    "(standard, slow-fade-color-variation, wisp-sweep-color-variation, design-reveal)",
                }
                if supports_launch
                else {}
            ),
            possible_approval_types=[],
            health_status=health_status,
            implementation_status=implementation_status,
            notes=notes,
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        """Unchanged from Step 6: schema-level checks (image count/
        extension/existence, preset_key membership) plus a real,
        in-process FFmpeg-availability check via the engine's own
        check_ffmpeg_is_available(). Timeout/cancel readiness isn't
        something validate() can usefully pre-check — it's a launch-time
        property, not a config-shape property.
        """
        errors: list[str] = []

        images = config.get("images")
        allowed_extensions = (".png", ".jpg", ".jpeg")
        if not isinstance(images, list) or not (3 <= len(images) <= 5):
            errors.append("'images' must be a list of 3 to 5 file paths")
        else:
            for image_path in images:
                if not isinstance(image_path, str) or not image_path.lower().endswith(allowed_extensions):
                    errors.append(f"invalid image path or extension: {image_path!r}")
                elif not Path(image_path).is_file():
                    errors.append(f"image path does not exist on disk: {image_path!r}")

        preset_key = config.get("preset_key")
        if preset_key not in _VALID_PRESET_KEYS:
            errors.append(f"'preset_key' must be one of {sorted(_VALID_PRESET_KEYS)}, got {preset_key!r}")

        try:
            engine_module = _load_engine_module()
        except EngineLaunchError as exc:
            errors.append(f"engine module could not be loaded: {exc.error.get('message')}")
        else:
            try:
                engine_module.check_ffmpeg_is_available()
            except engine_module.VideoGenerationError as exc:
                errors.append(str(exc))

        return ValidationResult(is_valid=not errors, errors=errors)

    def launch(
        self, config: dict[str, Any], on_run_reference: OnRunReference | None = None
    ) -> EngineRunReference:
        """Real: spawns _launch_worker.py as a genuinely separate,
        supervisable OS process (not a thread — see this module's
        docstring for why a real process is required), reports an
        in-flight EngineRunReference (with the worker's PID) via
        on_run_reference() the moment that process exists, then waits up
        to the configured timeout for it to finish.

        On timeout: kills the full process tree, cleans up any partial
        output the worker had already claimed, raises EngineLaunchError
        (category "launch_timeout") — never leaves the Job permanently
        running (the Coordinator's mark_failed() always follows this).

        On the worker completing normally: reads its result file. A
        worker-reported engine failure (VideoGenerationError, caught
        inside the worker) or a worker-process-level crash both surface
        as EngineLaunchError here, exactly as a synchronous failure would
        have in Step 6 — callers cannot tell the difference between "ran
        in a subprocess and failed" and "failed directly," which is the
        point: this is purely a supervision mechanism, invisible to
        collect_results()/the Coordinator.
        """
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        if repo_root is None:
            raise EngineLaunchError(
                {
                    "category": "engine_unreachable",
                    "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.",
                    "detail": {},
                }
            )

        images = config["images"]
        preset_key = config["preset_key"]
        timeout_seconds = _configured_timeout_seconds()

        work_dir = Path(tempfile.mkdtemp(prefix="automation_controller_video_launch_"))
        spec_path = work_dir / "spec.json"
        result_path = work_dir / "result.json"
        spec_path.write_text(
            json.dumps({"repo_root": str(repo_root), "images": images, "preset_key": preset_key}),
            encoding="utf-8",
        )

        process = subprocess.Popen(
            [sys.executable, str(_WORKER_SCRIPT_PATH), str(spec_path), str(result_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if on_run_reference is not None:
            on_run_reference(
                EngineRunReference(
                    engine_id=ENGINE_ID,
                    reference_type="worker_process",
                    reference_value=str(result_path),
                    created_at=datetime.now(timezone.utc),
                    extra={
                        "worker_pid": process.pid,
                        "work_dir": str(work_dir),
                        "result_path": str(result_path),
                    },
                )
            )

        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process.pid)
            _cleanup_partial_output(work_dir)
            shutil.rmtree(work_dir, ignore_errors=True)
            raise EngineLaunchError(
                {
                    "category": "launch_timeout",
                    "message": (
                        f"etsy-video-generator did not complete within {timeout_seconds:.0f} seconds "
                        "and was terminated (process tree killed, any partial output removed)."
                    ),
                    "detail": {"timeout_seconds": timeout_seconds, "worker_pid": process.pid},
                }
            )

        if not result_path.is_file():
            shutil.rmtree(work_dir, ignore_errors=True)
            raise EngineLaunchError(
                {
                    "category": "worker_no_result",
                    "message": "Worker process exited without writing a result file.",
                    "detail": {"worker_returncode": process.returncode},
                }
            )

        result_data = json.loads(result_path.read_text(encoding="utf-8"))
        shutil.rmtree(work_dir, ignore_errors=True)

        if not result_data.get("success"):
            raise EngineLaunchError(
                result_data.get(
                    "error", {"category": "video_generation_failed", "message": "unknown worker failure"}
                )
            )

        output_file = Path(result_data["output_file"])
        # The manifest's location comes from the worker, which got it from
        # the engine's own manifest_path_for() — it is no longer derivable
        # from the video's path (the engine writes manifests into its own
        # metadata/ folder now, keeping output/ a folder of finished videos
        # only). Reading it back from the result is what keeps this adapter
        # from hardcoding the engine's folder layout a second time.
        return EngineRunReference(
            engine_id=ENGINE_ID,
            reference_type="output_file_path",
            reference_value=str(output_file),
            created_at=datetime.now(timezone.utc),
            extra={"manifest_path": result_data["manifest_path"]},
        )

    def monitor(self, ref: EngineRunReference) -> EngineStatus:
        """Still not naturally required: launch() itself supervises the
        worker process to completion (or timeout) before ever returning,
        so by the time a caller has any EngineRunReference at all, the
        run has already reached a terminal outcome. The one exception —
        the EARLY, in-flight reference passed to on_run_reference() — is
        meant for cancel(), not for polling status through monitor(),
        which remains unimplemented.
        """
        raise NotImplementedError

    def cancel(self, ref: EngineRunReference) -> bool:
        """Real: kills the process tree referenced by ref.extra["worker_pid"],
        verifies death (never trusts taskkill's exit code alone — see
        _kill_process_tree()), cleans up partial output and temp files,
        and returns whether a live process was actually found and killed.

        Idempotent and safe to call multiple times: if the process is
        already gone (already finished naturally, or already cancelled by
        an earlier call), returns False without raising. Requirements
        this satisfies directly: "terminate running process tree,"
        "clean up temporary files," "clean up partial outputs when
        appropriate," "cancellation must be safe if called multiple times."

        Does NOT touch Job state — that remains
        core/execution/coordinator.py's ExecutionCoordinator.cancel_job()
        responsibility (JobService is the single writer of Job state, per
        the design spec's division of ownership); this method only ever
        reports True/False about the underlying process.
        """
        pid = ref.extra.get("worker_pid")
        if pid is None:
            return False

        work_dir_str = ref.extra.get("work_dir")
        work_dir = Path(work_dir_str) if work_dir_str else None

        was_alive = _is_pid_alive(pid)
        if not was_alive:
            if work_dir is not None:
                shutil.rmtree(work_dir, ignore_errors=True)
            return False

        _kill_process_tree(pid)

        if work_dir is not None:
            _cleanup_partial_output(work_dir)
            shutil.rmtree(work_dir, ignore_errors=True)

        return True

    def collect_results(self, ref: EngineRunReference) -> EngineResult:
        """Unchanged from Step 6: reads the sibling JSON manifest that
        run_video_generation() wrote (only ever written on success) and
        normalizes it into an EngineResult. Only ever called by the
        Coordinator after launch() has already returned a FINAL
        (post-completion) reference — never called against the early,
        in-flight reference on_run_reference() reports.
        """
        manifest_path = Path(ref.extra["manifest_path"])
        manifest_text = read_text_safely(manifest_path)

        if manifest_text is None:
            return EngineResult(
                success=False,
                artifacts={},
                error={
                    "category": "manifest_missing",
                    "message": f"No generation manifest found at {manifest_path}",
                    "detail": {"expected_path": str(manifest_path)},
                },
            )

        try:
            manifest = json.loads(manifest_text)
        except json.JSONDecodeError as exc:
            return EngineResult(
                success=False,
                artifacts={},
                error={
                    "category": "manifest_unreadable",
                    "message": f"Generation manifest at {manifest_path} is not valid JSON: {exc}",
                    "detail": {"manifest_path": str(manifest_path)},
                },
            )

        return EngineResult(
            success=True,
            artifacts={
                "output_video_path": ref.reference_value,
                "manifest_path": str(manifest_path),
                "manifest": manifest,
            },
            error=None,
        )
