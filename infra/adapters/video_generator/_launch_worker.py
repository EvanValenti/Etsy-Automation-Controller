"""Standalone worker process for VideoGeneratorAdapter.launch().

Why this exists (Step 7): the real etsy-video-generator engine's
run_video_generation() calls subprocess.run() internally with no timeout
and no way for a caller to interrupt it once started (confirmed the hard
way in Step 6 — a corrupted input image caused FFmpeg to retry decoding
indefinitely, and the resulting hang could not be cleanly stopped; killing
the parent Python process left the FFmpeg child running as an orphan).
We cannot fix this by editing etsy-video-generator's own source — that
repo is a separate, independently-owned engine we integrate with only
through its real public function, never by modifying it.

The only way to make this call genuinely timeout-able and cancel-able
from outside is to run it in a process WE spawn and fully control, so we
have a real OS process handle (a PID) to supervise, and — critically — a
process TREE we can terminate as a whole (this worker's own child, the
FFmpeg process, would otherwise be orphaned exactly like Step 6, which is
why the adapter's timeout/cancel logic kills with taskkill's /T flag, not
a bare kill of this worker's own PID alone).

Usage: `python _launch_worker.py <spec_path> <result_path>`
  spec_path:   a JSON file containing {"repo_root": str, "images": [str],
               "preset_key": str}
  result_path: where this script writes its JSON outcome:
               {"success": true, "output_file": str} or
               {"success": false, "error": {"category": str, "message": str}}

Also writes `claimed_output.json` (`{"output_file": str, "manifest_path":
str}`) in the SAME directory as result_path, immediately after computing
the output path but BEFORE calling the (potentially hanging) real
generation function. This lets a parent that has to kill this worker on a
timeout/cancel find out which path might now contain a partial/corrupt
video file — the real engine's own manifest is only ever written on
success, so without this marker the parent would have no way to know what
to clean up.

Both paths come from the ENGINE, never from a convention this file
reconstructs: `manifest_path` is whatever the engine's own
manifest_path_for() says (today "metadata/<stem>.json", deliberately no
longer a ".json" sibling of the video, so the engine's output/ folder
holds finished videos only). Reporting it back means the adapter never has
to know where the engine keeps its metadata.

This script deliberately duplicates the small dynamic-import helper also
used by adapter.py (rather than importing across the process boundary,
which isn't meaningful here) — it must be a fully standalone, dependency-free
script since it runs as its own process, not inside the automation-controller
package's own import graph.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_engine_module(repo_root: Path):
    module_path = repo_root / "src" / "generate_video.py"
    spec = importlib.util.spec_from_file_location("_etsy_video_generator_engine_worker", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build an import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    spec_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    claimed_output_path = result_path.parent / "claimed_output.json"

    try:
        job_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        repo_root = Path(job_spec["repo_root"])
        images = [Path(p) for p in job_spec["images"]]
        preset_key = job_spec["preset_key"]

        engine_module = _load_engine_module(repo_root)
        output_file = engine_module.get_next_output_file()
        manifest_path = engine_module.manifest_path_for(output_file)

        # Report the claimed paths BEFORE the call that can hang/take a
        # long time — see module docstring.
        claimed_output_path.write_text(
            json.dumps({"output_file": str(output_file), "manifest_path": str(manifest_path)}),
            encoding="utf-8",
        )

        try:
            result_file = engine_module.run_video_generation(images, preset_key, output_file)
        except engine_module.VideoGenerationError as exc:
            result_path.write_text(
                json.dumps(
                    {
                        "success": False,
                        "error": {
                            "category": "video_generation_failed",
                            "message": str(exc),
                            "detail": {"preset_key": preset_key, "output_file": str(output_file)},
                        },
                    }
                ),
                encoding="utf-8",
            )
            return 0  # the worker itself ran successfully; the ENGINE reported a failure — distinct outcomes

        result_path.write_text(
            json.dumps(
                {"success": True, "output_file": str(result_file), "manifest_path": str(manifest_path)}
            ),
            encoding="utf-8",
        )
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
            pass  # if we can't even write the result file, the parent's own timeout will catch this
        return 1


if __name__ == "__main__":
    sys.exit(main())
