"""HTTP contract tests for Design ID support on POST /video-generator/jobs.

Design ID is Controller-side listing metadata, exactly as it already is for
the Mockup Generator: it ties a generated video to the design it was made
for, so the Jobs list can identify it and Listing Assets can find a
design's video, mockups, and AI images under one search. The engine never
sees it -- the adapter reads only images/preset_key -- so what matters here
is that it lands on the Job's own config, unchanged, and that omitting it
leaves the config exactly as it was before the field existed.

Uses TestClient against a minimal app wrapping just this router, with
staging and JobService/coordinator faked: this verifies the request/config
contract, not staging or orchestration behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api import video_generator_routes  # noqa: E402
from api.dependencies import get_execution_coordinator, get_job_service  # noqa: E402
from api.video_generator_routes import router as video_generator_router  # noqa: E402


class _FakeJob:
    id = "job-1"
    engine_id = "etsy-video-generator"
    status = "succeeded"


class _FakeJobService:
    """Records the config it was asked to create a Job with."""

    def __init__(self) -> None:
        self.created_config: dict | None = None

    def create_job(self, engine_id: str, config: dict):
        self.created_config = config
        return _FakeJob()

    def get_job(self, job_id: str):
        return _FakeJob()


class _FakeCoordinator:
    def evaluate(self, engine_id: str) -> None:
        return None


@pytest.fixture()
def client_and_service(tmp_path: Path):
    app = FastAPI()
    app.include_router(video_generator_router)
    service = _FakeJobService()
    app.dependency_overrides[get_job_service] = lambda: service
    app.dependency_overrides[get_execution_coordinator] = lambda: _FakeCoordinator()

    staged = [tmp_path / f"{i}.png" for i in range(3)]
    for p in staged:
        p.write_bytes(b"x")

    with patch.object(video_generator_routes, "sweep_terminal_staging_dirs"), patch.object(
        video_generator_routes, "stage_uploaded_images", return_value=(tmp_path, staged)
    ), patch.object(video_generator_routes, "mark_job_id"), patch.object(
        video_generator_routes, "cleanup_staging_dir"
    ):
        yield TestClient(app), service


def _files():
    return [("images", (f"{i}.png", b"x", "image/png")) for i in range(3)]


def test_design_id_lands_on_the_job_config(client_and_service):
    client, service = client_and_service
    response = client.post(
        "/video-generator/jobs",
        data={"preset_key": "standard", "design_id": "Mushroom Log"},
        files=_files(),
    )

    assert response.status_code == 200
    assert service.created_config["design_id"] == "Mushroom Log"
    # ...alongside, never instead of, what the engine actually reads.
    assert service.created_config["preset_key"] == "standard"
    assert len(service.created_config["images"]) == 3


def test_design_id_is_trimmed(client_and_service):
    client, service = client_and_service
    client.post(
        "/video-generator/jobs",
        data={"preset_key": "standard", "design_id": "  Mushroom Log  "},
        files=_files(),
    )

    assert service.created_config["design_id"] == "Mushroom Log"


@pytest.mark.parametrize("design_id", ["", "   "])
def test_a_blank_design_id_is_not_recorded_at_all(client_and_service, design_id):
    """An absent Design ID must leave no key behind, so nothing downstream
    has to distinguish "" from "not set" -- the Jobs list and Listing Assets
    both treat presence of the key as meaningful."""
    client, service = client_and_service
    client.post(
        "/video-generator/jobs",
        data={"preset_key": "standard", "design_id": design_id},
        files=_files(),
    )

    assert "design_id" not in service.created_config


def test_omitting_design_id_leaves_the_original_config_shape(client_and_service):
    client, service = client_and_service
    client.post("/video-generator/jobs", data={"preset_key": "standard"}, files=_files())

    assert set(service.created_config) == {"images", "preset_key"}
