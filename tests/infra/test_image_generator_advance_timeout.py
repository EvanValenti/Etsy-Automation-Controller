"""Tests for the image generator's advance timeout budget.

Two things are locked in here: the budget an advance actually gets today
(a flat 1200s, enough for a full 20-image job at observed ~48s/image), and
that the base + per-image seam works, so converting to the intended
"2 minutes + 60s per image" model is a two-constant change rather than a
refactor of every call site.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra.adapters.image_generator import adapter  # noqa: E402


class TestTodaysBudget:
    def test_advance_gets_1200_seconds(self):
        assert adapter.advance_timeout_seconds() == 1200.0

    def test_per_image_term_is_inactive_today(self):
        # A count must not change the budget while the per-image term is 0
        # -- today's behaviour is deliberately flat.
        assert adapter.ADVANCE_TIMEOUT_PER_IMAGE_SECONDS == 0.0
        assert adapter.advance_timeout_seconds(20) == adapter.advance_timeout_seconds()

    def test_budget_covers_a_full_twenty_image_job(self):
        # 10 AI product + 10 lifestyle at the ~48s/image measured against
        # real gpt-image-1 calls.
        observed_seconds_per_image = 48
        assert adapter.advance_timeout_seconds() >= 20 * observed_seconds_per_image

    def test_public_default_matches_the_base(self):
        assert adapter.DEFAULT_WORKER_TIMEOUT_SECONDS == adapter.ADVANCE_TIMEOUT_BASE_SECONDS


class TestFutureConversion:
    """Flipping the two constants is all the future model should require."""

    def test_base_plus_per_image_applies_once_enabled(self):
        with patch.object(adapter, "ADVANCE_TIMEOUT_BASE_SECONDS", 120.0), patch.object(
            adapter, "ADVANCE_TIMEOUT_PER_IMAGE_SECONDS", 60.0
        ):
            assert adapter.advance_timeout_seconds(20) == 120.0 + 20 * 60.0
            assert adapter.advance_timeout_seconds(1) == 180.0

    def test_unknown_count_still_falls_back_to_the_base(self):
        with patch.object(adapter, "ADVANCE_TIMEOUT_BASE_SECONDS", 120.0), patch.object(
            adapter, "ADVANCE_TIMEOUT_PER_IMAGE_SECONDS", 60.0
        ):
            assert adapter.advance_timeout_seconds(None) == 120.0

    def test_a_negative_count_never_shrinks_the_budget(self):
        with patch.object(adapter, "ADVANCE_TIMEOUT_BASE_SECONDS", 120.0), patch.object(
            adapter, "ADVANCE_TIMEOUT_PER_IMAGE_SECONDS", 60.0
        ):
            assert adapter.advance_timeout_seconds(-5) == 120.0


class TestRetryBehaviourUnchanged:
    def test_launch_makes_exactly_one_worker_call(self):
        """No retry was added: one advance is one subprocess run."""
        fake_result = {
            "success": True,
            "result": {"job_name": "demo", "advance": {"status": "advanced", "stage": "prompt_build", "detail": None}},
        }
        with patch.object(adapter, "resolve_engine_repo_root", return_value=Path("/fake/repo")), patch.object(
            adapter, "_run_worker", return_value=fake_result
        ) as run_worker:
            adapter.ImageGeneratorAdapter().launch({"job_name": "demo"})

        assert run_worker.call_count == 1
        assert run_worker.call_args.kwargs["timeout_seconds"] == 1200.0
