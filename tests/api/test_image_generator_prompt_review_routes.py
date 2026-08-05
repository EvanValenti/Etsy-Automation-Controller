"""HTTP contract tests for POST /image-generator/jobs/{job_name}/prompts/
review-complete -- the Prompt Review confirmation route. Uses FastAPI's
TestClient against a minimal app wrapping just this router (no DB/lifespan
needed for this route), with the adapter-level call mocked -- these tests
verify request/response shapes and status codes, not adapter or engine
behavior (covered by tests/infra/test_image_generator_prompt_review_adapter.py
and Etsy-AI-Image-Generator's own test suite, respectively)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.image_generator_routes import router as image_generator_router  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(image_generator_router)
    return TestClient(app)


def test_confirm_success_returns_refreshed_status(client):
    refreshed_status = {
        "job_name": "demo_job",
        "pipeline_status": {"prompt_build_complete": True, "prompt_review_complete": True},
        "next_step": "Generate Images",
    }
    with patch(
        "api.image_generator_routes._mark_prompt_review_complete", return_value=refreshed_status
    ) as fake_mark:
        response = client.post("/image-generator/jobs/demo_job/prompts/review-complete")

    assert response.status_code == 200
    assert response.json() == refreshed_status
    fake_mark.assert_called_once_with("demo_job")


def test_confirming_an_already_complete_review_is_still_a_normal_success(client):
    """The engine's mark_prompt_review_complete() is idempotent -- the
    Controller does not special-case "already complete" locally, it just
    relays whatever the engine returns (still prompt_review_complete=True)."""
    refreshed_status = {
        "job_name": "demo_job",
        "pipeline_status": {"prompt_build_complete": True, "prompt_review_complete": True},
        "next_step": "Generate Images",
    }
    with patch("api.image_generator_routes._mark_prompt_review_complete", return_value=refreshed_status):
        response = client.post("/image-generator/jobs/demo_job/prompts/review-complete")

    assert response.status_code == 200
    assert response.json()["pipeline_status"]["prompt_review_complete"] is True


def test_ineligible_job_surfaces_engine_error_message_verbatim(client):
    with patch(
        "api.image_generator_routes._mark_prompt_review_complete",
        side_effect=Exception("Job not found: jobs/no_such_job"),
    ):
        response = client.post("/image-generator/jobs/no_such_job/prompts/review-complete")

    assert response.status_code == 422
    assert "Job not found: jobs/no_such_job" in response.json()["detail"]


def test_engine_error_is_never_reported_as_success(client):
    with patch(
        "api.image_generator_routes._mark_prompt_review_complete",
        side_effect=Exception("No prompt package found for this job"),
    ):
        response = client.post("/image-generator/jobs/demo_job/prompts/review-complete")

    assert response.status_code != 200
    assert response.status_code == 422
