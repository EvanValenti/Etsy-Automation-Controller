"""Standalone worker process for ImageGeneratorAdapter.launch(). Mirrors
infra/adapters/mockup_generator/_headless_worker.py's shape exactly:
spawned as its own OS process so a real Anthropic/OpenAI API call (network
I/O, possibly slow) never blocks the Controller's own process, cwd set to
the engine repo root by the caller (subprocess.run(cwd=...)) so
headless.py's/main.py's/job_manifest.py's relative-path constants
(jobs/, config/stores/) resolve exactly as they do for the interactive
CLI.

Unlike etsy-mockup-generator, Etsy-AI-Image-Generator's headless.py lives
under src/, not the repo root -- both the module path and the sys.path
entry account for that.

Usage: `python _headless_worker.py <spec_path> <result_path>`
  spec_path: JSON {"repo_root": str, "action": "create_job"|"advance"|"list_jobs"|
             "get_status"|"list_stores"|"export_manual_concepts"|
             "import_manual_concepts"|"list_manual_prompts"|"copy_manual_prompt"|
             "import_manual_image"|"import_finished_images"|"list_concepts"|"approve_concept"|
             "reject_concept"|"list_reference_images"|"get_reference_image_roles"|
             "add_reference_image"|"remove_reference_image"|
             "correct_reference_image_role"|"get_concept_provider"|
             "list_prompts"|"get_prompt_text"|"mark_prompt_review_complete"|
             "get_prompt_detail"|"delete_prompt"|"list_generated_images"|
             "set_image_review_status"|"resolve_generated_image_path",
             ...action-specific fields}
  result_path: JSON {"success": true, "result": {...}} or
               {"success": false, "error": {"category": str, "message": str, "detail": dict}}
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_headless_module(repo_root: Path):
    src_dir = repo_root / "src"
    module_path = src_dir / "headless.py"
    spec = importlib.util.spec_from_file_location("_etsy_ai_image_generator_headless_worker", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build an import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(src_dir))  # headless.py imports sibling modules (main, job_manifest, ...) by bare name
    spec.loader.exec_module(module)
    return module


def main() -> int:
    spec_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])

    try:
        job_spec = json.loads(spec_path.read_text(encoding="utf-8"))
        repo_root = Path(job_spec["repo_root"])
        action = job_spec["action"]

        headless = _load_headless_module(repo_root)

        try:
            if action == "list_stores":
                import store_config  # already on sys.path via _load_headless_module

                stores = store_config.discover_stores()
                result = [
                    {
                        "store_id": s["store_id"],
                        "store_name": s["store_name"],
                        "campaigns": store_config.discover_campaigns(s["path"]),
                    }
                    for s in stores
                ]
            elif action == "list_jobs":
                result = headless.list_jobs()
            elif action == "get_status":
                result = headless.get_job_status(job_spec["job_name"])
            elif action == "create_job":
                result = headless.create_job(
                    product_name=job_spec["product_name"],
                    store_id=job_spec["store_id"],
                    campaign_id=job_spec["campaign_id"],
                    product_type=job_spec["product_type"],
                    concept_counts=job_spec.get("concept_counts"),
                    creative_notes=job_spec.get("creative_notes") or "",
                    product_color=job_spec.get("product_color") or "",
                )
            elif action == "advance":
                job_name = job_spec["job_name"]
                # "Continue Automatically" must genuinely mean automatic:
                # ensure this job is configured for the real API concept
                # provider before advancing, every call -- a job with no
                # explicit provider selection otherwise silently resolves
                # to provider_registry's safe manual default
                # (DEFAULT_CONCEPT_PROVIDER_ID = "claude_code_manual"),
                # which is what previously made this button return
                # waiting_on_human instead of attempting real generation.
                # Idempotent and harmless for a job already past concept
                # generation -- resolve_concept_provider_id() is only ever
                # consulted during that one stage. Manual Mode (Copy/Import
                # Concepts JSON) is untouched by this: it never reads
                # concept_provider at all.
                headless.set_concept_provider(job_name, "claude_api")
                advance_result = headless.advance_job(job_name)
                if advance_result["status"] == "error":
                    result_path.write_text(
                        json.dumps(
                            {
                                "success": False,
                                "error": {
                                    "category": "engine_error",
                                    "message": advance_result["detail"] or "advance_job() reported an error",
                                    "detail": {"stage": advance_result["stage"]},
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    return 0
                result = {"advance": advance_result, "job_status": headless.get_job_status(job_name)}
            elif action == "export_manual_concepts":
                result = headless.export_manual_concept_package(job_spec["job_name"])
            elif action == "import_manual_concepts":
                result = headless.import_manual_concept_response(job_spec["job_name"], job_spec["response_json_text"])
            elif action == "list_manual_prompts":
                result = headless.list_manual_prompt_packages(job_spec["job_name"])
            elif action == "copy_manual_prompt":
                result = headless.copy_manual_prompt(job_spec["job_name"], job_spec["category"], job_spec["concept_id"])
            elif action == "import_manual_image":
                result = headless.import_manual_image(
                    job_spec["job_name"], job_spec["category"], job_spec["concept_id"],
                    staged_image_path=job_spec["staged_image_path"],
                    original_filename=job_spec["original_filename"],
                )
            elif action == "import_finished_images":
                result = headless.import_finished_job_images(
                    job_spec["job_name"],
                    job_spec["staged_image_paths"],
                    job_spec["category"],
                )
            elif action == "list_concepts":
                result = headless.list_concepts(job_spec["job_name"], job_spec["category"])
            elif action == "approve_concept":
                result = headless.approve_concept(job_spec["job_name"], job_spec["category"], job_spec["concept_id"])
            elif action == "reject_concept":
                result = headless.reject_concept(job_spec["job_name"], job_spec["category"], job_spec["concept_id"])
            elif action == "list_reference_images":
                result = headless.list_reference_images(job_spec["job_name"])
            elif action == "get_reference_image_roles":
                result = headless.get_reference_image_roles(job_spec["job_name"])
            elif action == "add_reference_image":
                result = headless.add_reference_image(
                    job_spec["job_name"], job_spec["filename"], job_spec["staged_image_path"],
                )
            elif action == "remove_reference_image":
                result = headless.remove_reference_image(job_spec["job_name"], job_spec["filename"])
            elif action == "correct_reference_image_role":
                result = headless.correct_reference_image_role(
                    job_spec["job_name"], job_spec["filename"], job_spec["role"],
                )
            elif action == "get_concept_provider":
                result = headless.get_concept_provider(job_spec["job_name"])
            elif action == "list_prompts":
                result = headless.list_prompts(job_spec["job_name"])
            elif action == "get_prompt_text":
                result = headless.get_prompt_text(job_spec["job_name"], job_spec["category"], job_spec["concept_id"])
            elif action == "mark_prompt_review_complete":
                result = headless.mark_prompt_review_complete(job_spec["job_name"])
            elif action == "get_prompt_detail":
                result = headless.get_prompt_detail(
                    job_spec["job_name"], job_spec["category"], job_spec["concept_id"]
                )
            elif action == "delete_prompt":
                result = headless.delete_prompt(
                    job_spec["job_name"], job_spec["category"], job_spec["concept_id"]
                )
            elif action == "list_generated_images":
                result = headless.list_generated_images(job_spec["job_name"])
            elif action == "set_image_review_status":
                result = headless.set_image_review_status(
                    job_spec["job_name"], job_spec["category"], job_spec["concept_id"], job_spec["status"]
                )
            elif action == "resolve_generated_image_path":
                result = {
                    "path": str(
                        headless.resolve_generated_image_path(
                            job_spec["job_name"],
                            job_spec["category"],
                            job_spec["concept_id"],
                            job_spec["filename"],
                        )
                    )
                }
            else:
                raise RuntimeError(f"Unknown action: {action!r}")
        except headless.HeadlessError as exc:
            result_path.write_text(
                json.dumps(
                    {"success": False, "error": {"category": "engine_error", "message": str(exc), "detail": {}}}
                ),
                encoding="utf-8",
            )
            return 0  # the worker itself ran fine; the ENGINE reported a failure -- distinct outcomes

        result_path.write_text(json.dumps({"success": True, "result": result}, default=str), encoding="utf-8")
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
