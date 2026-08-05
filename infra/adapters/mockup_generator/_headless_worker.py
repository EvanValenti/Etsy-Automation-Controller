"""Standalone worker process for MockupGeneratorAdapter.launch(). Mirrors
infra/adapters/video_generator/_launch_worker.py's shape: spawned as its
own OS process so a hang in mediapipe never blocks the Controller's own
process, cwd set to the engine repo root by the caller (Popen(cwd=...))
so headless.py's/batch_generate.py's relative-path constants (backgrounds/,
output/, working/, preview/, processed-inputs/, runs/) resolve exactly as
they do for the interactive CLI.

Usage: `python _headless_worker.py <spec_path> <result_path>`
  spec_path: JSON {"repo_root": str, "phase": "preview"|"batch"|"list_backgrounds", ...phase-specific fields}
  result_path: JSON {"success": true, "result": {...}} or
               {"success": false, "error": {"category": str, "message": str, "detail": dict}}
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_headless_module(repo_root: Path):
    module_path = repo_root / "headless.py"
    spec = importlib.util.spec_from_file_location("_etsy_mockup_generator_headless_worker", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build an import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(repo_root))  # headless.py imports sibling modules (batch_generate, etc.) by bare name
    spec.loader.exec_module(module)
    return module


def main() -> int:
    spec_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])

    try:
        job_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        repo_root = Path(job_spec["repo_root"])
        phase = job_spec["phase"]

        headless = _load_headless_module(repo_root)

        try:
            if phase == "list_backgrounds":
                result = headless.list_backgrounds()
            elif phase == "preview":
                result = headless.prepare_preview(
                    job_spec["zip_path"], job_spec["background_path"], job_spec.get("design_id")
                )
            elif phase == "batch":
                result = headless.generate_full_batch(job_spec["run_token"])
            else:
                raise RuntimeError(f"Unknown phase: {phase!r}")
        except headless.MockupEngineError as exc:
            result_path.write_text(
                json.dumps(
                    {
                        "success": False,
                        "error": {"category": exc.category, "message": exc.message, "detail": exc.detail},
                    }
                ),
                encoding="utf-8",
            )
            return 0  # the worker itself ran fine; the ENGINE reported a failure -- distinct outcomes

        result_path.write_text(json.dumps({"success": True, "result": result}), encoding="utf-8")
        return 0

    except Exception as exc:  # noqa: BLE001 - a worker-process-level crash, not an engine-level failure
        try:
            result_path.write_text(
                json.dumps(
                    {
                        "success": False,
                        "error": {
                            "category": "worker_process_crashed",
                            "message": f"{type(exc).__name__}: {exc}",
                            "detail": {},
                        },
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
