"""MockupGeneratorAdapter -- launch()/collect_results() now real, via a
subprocess worker calling headless.py (etsy-mockup-generator's new
Step-13 public interface). discover()/validate() updated to reflect that
launch() is no longer stubbed.

Two-phase workflow (design spec 2026-07-23, Section 3): the Controller
never calls launch() expecting a single "start to finish" run the way
VideoGeneratorAdapter does. Instead two separate Jobs are created against
this engine -- config={"phase": "preview", ...} then, after operator
approval, config={"phase": "batch", "run_token": ...} -- each a complete,
synchronous launch()+collect_results() cycle through the same
ExecutionCoordinator.evaluate() bridge every engine already uses. `phase`
is an adapter-private config key, invisible to the Core, exactly like
`preset_key` is for VideoGeneratorAdapter.

Subprocess worker rationale: identical to VideoGeneratorAdapter (see that
adapter's module docstring) -- mediapipe pose/face detection is real,
possibly slow, CPU-bound work; running it in a process WE spawn and fully
control keeps a stall or crash there from ever affecting the Controller's
own process. Unlike the video adapter, this repo's own relative-path
assumptions (backgrounds/, output/, working/, preview/, processed-inputs/,
runs/) require the worker's cwd to actually BE the engine repo root --
Popen(cwd=repo_root) below, not just an import by file path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
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
from infra.adapters.discovery_utils import WORKSPACE_ROOT, read_text_safely, resolve_engine_repo_root

ENGINE_ID = "etsy-mockup-generator"
REPO_DIR_NAME = "etsy-mockup-generator"
_WORKER_SCRIPT_PATH = Path(__file__).resolve().parent / "_headless_worker.py"
_VALID_PHASES = {"preview", "batch"}
DEFAULT_WORKER_TIMEOUT_SECONDS = 300.0  # generous: mediapipe model load + a full batch of renders


def _resolve_worker_python(repo_root: Path) -> str:
    """etsy-mockup-generator has no dedicated venv of its own -- its real
    dependencies (Pillow, mediapipe) are installed in the shared
    workspace-root venv (E:\\Vilicity\\.venv), NOT in the Controller's own
    venv (automation-controller/.venv, which only has fastapi/uvicorn/
    sqlmodel/pydantic -- deliberately minimal per pyproject.toml). Using
    sys.executable (the Controller's own interpreter) here would spawn a
    worker that can't `import PIL`/`import mediapipe` at all -- caught
    live during this task's own verification. Prefers a per-engine venv
    if one is ever added later (repo_root/.venv), falls back to the
    shared workspace venv, and only falls back to sys.executable as a
    last resort (e.g. if neither venv exists on some other machine).
    """
    engine_venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if engine_venv_python.is_file():
        return str(engine_venv_python)
    workspace_venv_python = WORKSPACE_ROOT / ".venv" / "Scripts" / "python.exe"
    if workspace_venv_python.is_file():
        return str(workspace_venv_python)
    return sys.executable


def _run_worker(repo_root: Path, job_spec: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    work_dir = Path(tempfile.mkdtemp(prefix="automation_controller_mockup_launch_"))
    spec_path = work_dir / "spec.json"
    result_path = work_dir / "result.json"
    spec_path.write_text(json.dumps(job_spec), encoding="utf-8")

    try:
        process = subprocess.run(
            [_resolve_worker_python(repo_root), str(_WORKER_SCRIPT_PATH), str(spec_path), str(result_path)],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(work_dir, ignore_errors=True)
        return {
            "success": False,
            "error": {
                "category": "launch_timeout",
                "message": f"etsy-mockup-generator did not complete within {timeout_seconds:.0f} seconds.",
                "detail": {"timeout_seconds": timeout_seconds},
            },
        }

    if not result_path.is_file():
        returncode = process.returncode
        shutil.rmtree(work_dir, ignore_errors=True)
        return {
            "success": False,
            "error": {
                "category": "worker_no_result",
                "message": "Worker process exited without writing a result file.",
                "detail": {"worker_returncode": returncode},
            },
        }

    outcome = json.loads(result_path.read_text(encoding="utf-8"))
    shutil.rmtree(work_dir, ignore_errors=True)
    return outcome


def list_mockup_backgrounds() -> list[dict[str, Any]]:
    """Direct, synchronous read -- not a Job, not a launch(). Runs
    headless.list_backgrounds() in the same kind of worker subprocess
    (cwd=repo_root) so it never depends on the Controller's own process
    having Pillow importable in a particular way, and stays consistent
    with how every other engine call in this adapter is made. Raises
    EngineLaunchError on any failure (unreachable repo, worker crash),
    for api/mockup_generator_routes.py to translate into an HTTP error.
    """
    repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
    if repo_root is None:
        raise EngineLaunchError(
            {"category": "engine_unreachable", "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.", "detail": {}}
        )
    outcome = _run_worker(repo_root, {"repo_root": str(repo_root), "phase": "list_backgrounds"}, timeout_seconds=30.0)
    if not outcome["success"]:
        raise EngineLaunchError(outcome["error"])
    return outcome["result"]


class MockupGeneratorAdapter(BaseEngineAdapter):
    engine_id = ENGINE_ID

    def discover(self) -> EngineCapabilities:
        """Grounded, read-only inspection -- now checks for headless.py's
        three public functions (Step 13's new headless seam) rather than
        the old interactive-CLI-only markers.
        """
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        repo_exists = repo_root is not None

        headless_text = read_text_safely(repo_root / "headless.py") if repo_root else None
        has_prepare_preview = bool(headless_text and "def prepare_preview(" in headless_text)
        has_generate_full_batch = bool(headless_text and "def generate_full_batch(" in headless_text)
        has_list_backgrounds = bool(headless_text and "def list_backgrounds(" in headless_text)

        supports_launch = has_prepare_preview and has_generate_full_batch
        supports_monitoring = supports_launch  # collect_results() reads the worker's own result, not a poll

        notes: list[str] = []
        if not repo_exists:
            health_status = "unreachable"
            notes.append(f"Sibling repo '{REPO_DIR_NAME}' was not found on disk during discovery.")
        elif supports_launch and has_list_backgrounds:
            health_status = "healthy"
            notes.append(
                "launch() is real (Step 13): two phases, config={'phase': 'preview'|'batch', ...}, each run in a "
                "supervised worker subprocess (cwd=engine repo root)."
            )
        else:
            health_status = "degraded"
            notes.append("headless.py did not contain the expected prepare_preview()/generate_full_batch() markers.")

        implementation_status = ImplementationStatus.REAL if supports_launch else ImplementationStatus.STUB

        return EngineCapabilities(
            engine_id=ENGINE_ID,
            display_name="Etsy Mockup Generator",
            version=None,
            supports_launch=supports_launch,
            cancel_support=CancelSupport.UNSUPPORTED,
            supports_monitoring=supports_monitoring,
            supports_approvals=False,
            supports_pipelines=False,
            max_concurrent_runs=1,
            launch_config_schema=(
                {
                    "phase": "str -- 'preview' or 'batch'",
                    "zip_path": "str -- absolute path to a staged ZIP (phase=preview only)",
                    "background_path": "str -- absolute path to a background image (phase=preview only)",
                    "design_id": "str | None -- optional operator reference (phase=preview only)",
                    "run_token": "str -- opaque token from a prior phase=preview result (phase=batch only)",
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
        errors: list[str] = []
        phase = config.get("phase")
        if phase not in _VALID_PHASES:
            errors.append(f"'phase' must be one of {sorted(_VALID_PHASES)}, got {phase!r}")
        elif phase == "preview":
            if not config.get("zip_path"):
                errors.append("'zip_path' is required for phase='preview'")
            elif not Path(config["zip_path"]).is_file():
                errors.append(f"zip_path does not exist on disk: {config['zip_path']!r}")
            if not config.get("background_path"):
                errors.append("'background_path' is required for phase='preview'")
            elif not Path(config["background_path"]).is_file():
                errors.append(f"background_path does not exist on disk: {config['background_path']!r}")
        elif phase == "batch":
            if not config.get("run_token"):
                errors.append("'run_token' is required for phase='batch'")

        if resolve_engine_repo_root(REPO_DIR_NAME) is None:
            errors.append(f"{ENGINE_ID}: sibling repo not found on disk")

        return ValidationResult(is_valid=not errors, errors=errors)

    def launch(
        self, config: dict[str, Any], on_run_reference: OnRunReference | None = None
    ) -> EngineRunReference:
        repo_root = resolve_engine_repo_root(REPO_DIR_NAME)
        if repo_root is None:
            raise EngineLaunchError(
                {"category": "engine_unreachable", "message": f"Sibling repo '{REPO_DIR_NAME}' was not found on disk.", "detail": {}}
            )

        phase = config["phase"]
        job_spec: dict[str, Any] = {"repo_root": str(repo_root), "phase": phase}
        if phase == "preview":
            job_spec["zip_path"] = config["zip_path"]
            job_spec["background_path"] = config["background_path"]
            job_spec["design_id"] = config.get("design_id")
        else:
            job_spec["run_token"] = config["run_token"]

        outcome = _run_worker(repo_root, job_spec, timeout_seconds=DEFAULT_WORKER_TIMEOUT_SECONDS)
        if not outcome["success"]:
            raise EngineLaunchError(outcome["error"])

        result = outcome["result"]
        result["phase"] = phase
        # The worker's cwd is repo_root (so headless.py's/batch_generate.py's
        # relative constants like OUTPUT_ROOT_DIR="output" resolve correctly
        # there) -- but this Controller process has its own, different cwd,
        # so any path the worker returned relative to repo_root must be
        # absolutized here before it's ever persisted on a Job or served
        # over HTTP. preview_artifacts paths are already absolute (built
        # with os.path.abspath() inside headless.prepare_preview()).
        for path_key in ("run_dir", "manifest_path", "assets_dir"):
            if path_key in result and not Path(result[path_key]).is_absolute():
                result[path_key] = str((repo_root / result[path_key]).resolve())
        return EngineRunReference(
            engine_id=ENGINE_ID,
            reference_type="headless_result",
            reference_value=phase,
            created_at=datetime.now(timezone.utc),
            extra=result,
        )

    def monitor(self, ref: EngineRunReference) -> EngineStatus:
        """Not needed: each phase's launch() blocks in-process (via
        subprocess.run(), not Popen+poll) until that phase is fully
        terminal, exactly like VideoGeneratorAdapter's synchronous
        collect_results() path."""
        raise NotImplementedError

    def collect_results(self, ref: EngineRunReference) -> EngineResult:
        """Real: launch() already ran the worker to completion and stashed
        its full result dict on ref.extra -- this just re-shapes it into
        an EngineResult, no second read."""
        return EngineResult(success=True, artifacts=dict(ref.extra), error=None)
