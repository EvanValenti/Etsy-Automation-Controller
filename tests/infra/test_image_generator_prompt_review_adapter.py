"""Adapter-level tests for mark_prompt_review_complete() (Prompt Review
confirmation). Mocks _run_worker -- the point here is verifying the
adapter shapes the correct job_spec and relays the worker's outcome
correctly, not re-testing the subprocess/headless.py plumbing itself
(covered by Etsy-AI-Image-Generator's own test suite, and by this
integration's live smoke test)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.adapters.engine_adapter import EngineLaunchError  # noqa: E402
from infra.adapters.image_generator import adapter as image_generator_adapter  # noqa: E402


class MarkPromptReviewCompleteAdapterTests(unittest.TestCase):
    def setUp(self):
        self.repo_root_patch = patch.object(
            image_generator_adapter, "_require_repo_root", return_value=Path("/fake/Etsy-AI-Image-Generator")
        )
        self.repo_root_patch.start()
        self.addCleanup(self.repo_root_patch.stop)

    def test_sends_mark_prompt_review_complete_action_with_job_name(self):
        with patch.object(image_generator_adapter, "_run_worker") as fake_run_worker:
            fake_run_worker.return_value = {"success": True, "result": {"job_name": "demo_job"}}
            image_generator_adapter.mark_prompt_review_complete("demo_job")

        job_spec = fake_run_worker.call_args[0][1]
        self.assertEqual(job_spec["action"], "mark_prompt_review_complete")
        self.assertEqual(job_spec["job_name"], "demo_job")

    def test_returns_worker_result_on_success(self):
        refreshed_status = {
            "job_name": "demo_job",
            "pipeline_status": {"prompt_review_complete": True},
            "next_step": "Generate Images",
        }
        with patch.object(image_generator_adapter, "_run_worker") as fake_run_worker:
            fake_run_worker.return_value = {"success": True, "result": refreshed_status}
            result = image_generator_adapter.mark_prompt_review_complete("demo_job")

        self.assertEqual(result, refreshed_status)

    def test_raises_engine_launch_error_on_worker_failure(self):
        with patch.object(image_generator_adapter, "_run_worker") as fake_run_worker:
            fake_run_worker.return_value = {
                "success": False,
                "error": {"category": "engine_error", "message": "Job not found: jobs/no_such_job", "detail": {}},
            }
            with self.assertRaises(EngineLaunchError) as ctx:
                image_generator_adapter.mark_prompt_review_complete("no_such_job")

        self.assertIn("Job not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
