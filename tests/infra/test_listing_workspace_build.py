"""Tests for ListingWorkspaceBuilder's planning and rebuild safety.

Three concerns live here.

The workspace is a deliverables folder. What "Open Listing Workspace"
shows the operator is exactly the assets they will upload to Etsy, flat --
no manifest among them, no Images//Videos/ to descend into. See
TestTheWorkspaceIsADeliverablesFolder.

Per-asset selection. The operator assembles a listing from individual
assets, not whole jobs -- so plan() enumerates every approved image,
mockup, and video the chosen sources offer, and build() copies whichever
subset stayed selected. The selection key is the workspace filename plan()
itself produced, which is what keeps a client from ever naming a path of
its own, and what lets two mockup batches that both contain
"flat-front-01.png" coexist in one listing.

Rebuild safety. A built workspace is disposable (see
infra/listing_workspace.py's module docstring) -- but a *failed* rebuild
must never be what disposes of it. Two real failure modes are locked in
below, both observed live against the running Controller:

  1. Resolve-before-mutate. build() used to wipe the workspace first and
     resolve its sources afterwards, so rebuilding with one bad source
     (a relocated mockup folder, a deleted Job row) destroyed the previous
     good build and returned an error -- the operator lost assets they
     still had a moment earlier.

  2. Never delete-and-recreate the workspace directory itself. On Windows a
     directory removal completes asynchronously while any handle is still
     open on it, so `shutil.rmtree(dir)` immediately followed by
     `dir.mkdir()` raises PermissionError [WinError 5]. That is the normal
     operator flow, not an edge case: "Open Folder" leaves Explorer holding
     the workspace open, and the next Rebuild then 500'd *and* left the
     workspace destroyed. Clearing the folder's contents has no such
     problem, so the folder itself is now never removed.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra import listing_workspace  # noqa: E402
from infra.listing_workspace import ListingWorkspaceBuilder, ListingWorkspaceError  # noqa: E402

# Realistic Job ids: the first 8 characters become the per-job filename
# prefix, which is what keeps two batches' identically-named mockups apart.
BATCH_A = "9da8b0e6-fbd7-4fcd-8df5-a8dd4436e4c6"
BATCH_B = "7cfa41c4-9978-4228-a4cb-f1e7afecc88b"
VIDEO_JOB = "11111111-2222-3333-4444-555555555555"


@dataclass
class _Job:
    """Only the three fields ListingWorkspaceBuilder actually reads."""

    id: str
    engine_id: str
    result_summary: dict[str, Any] = field(default_factory=dict)


class _FakeJobService:
    def __init__(self, jobs: dict[str, _Job]) -> None:
        self._jobs = jobs

    def get_job(self, job_id: str) -> _Job | None:
        return self._jobs.get(job_id)


@pytest.fixture
def mockup_repo(tmp_path: Path) -> Path:
    """A stand-in etsy-mockup-generator repo with two batches that both
    contain the same filenames -- the collision every real pair of runs
    has."""
    repo = tmp_path / "etsy-mockup-generator"
    for run in ("run-001", "run-002"):
        assets = repo / "output" / run / "assets"
        assets.mkdir(parents=True)
        (assets / "flat-front-01.png").write_bytes(f"{run}-first".encode())
        (assets / "flat-front-02.png").write_bytes(f"{run}-second".encode())
    return repo


@pytest.fixture
def builder(tmp_path: Path, mockup_repo: Path):
    """A builder whose workspaces root and engine repo both live in tmp."""
    jobs = {
        BATCH_A: _Job(
            id=BATCH_A,
            engine_id="etsy-mockup-generator",
            result_summary={"assets_dir": str(mockup_repo / "output" / "run-001" / "assets")},
        ),
        BATCH_B: _Job(
            id=BATCH_B,
            engine_id="etsy-mockup-generator",
            result_summary={"assets_dir": str(mockup_repo / "output" / "run-002" / "assets")},
        ),
    }
    workspaces_root = tmp_path / "listing_workspaces"
    manifests_root = tmp_path / "listing_workspace_manifests"
    with patch.object(listing_workspace, "WORKSPACES_ROOT", workspaces_root), patch.object(
        listing_workspace, "MANIFESTS_ROOT", manifests_root
    ), patch.object(listing_workspace, "resolve_engine_repo_root", return_value=mockup_repo):
        yield ListingWorkspaceBuilder(_FakeJobService(jobs)), workspaces_root


@pytest.fixture
def builder_with_video(tmp_path: Path, mockup_repo: Path):
    """Adds a video job, so the flat layout can be checked with both an
    image source and a video source in the same listing."""
    video_repo = tmp_path / "etsy-video-generator"
    (video_repo / "output").mkdir(parents=True)
    video_file = video_repo / "output" / "listing-video-001.mp4"
    video_file.write_bytes(b"fake video bytes")

    jobs = {
        BATCH_A: _Job(
            id=BATCH_A,
            engine_id="etsy-mockup-generator",
            result_summary={"assets_dir": str(mockup_repo / "output" / "run-001" / "assets")},
        ),
        VIDEO_JOB: _Job(
            id=VIDEO_JOB,
            engine_id="etsy-video-generator",
            result_summary={"output_video_path": str(video_file)},
        ),
    }

    def repo_for(dir_name):
        return video_repo if dir_name == "etsy-video-generator" else mockup_repo

    workspaces_root = tmp_path / "listing_workspaces"
    manifests_root = tmp_path / "listing_workspace_manifests"
    with patch.object(listing_workspace, "WORKSPACES_ROOT", workspaces_root), patch.object(
        listing_workspace, "MANIFESTS_ROOT", manifests_root
    ), patch.object(listing_workspace, "resolve_engine_repo_root", side_effect=repo_for):
        yield ListingWorkspaceBuilder(_FakeJobService(jobs)), workspaces_root


def _built(root: Path, listing_id: str = "demo") -> list[str]:
    """Everything in the operator-facing workspace folder. It is flat now,
    so this is also the complete list of what they would see."""
    return sorted(p.name for p in (root / listing_id).iterdir())


def _manifest_file(listing_id: str = "demo") -> Path:
    return listing_workspace.manifest_path_for(listing_id)


class TestPlanningIndividualAssets:
    def test_candidates_list_every_asset_not_every_job(self, builder):
        b, _ = builder
        candidates = b.list_candidates(mockup_job_ids=[BATCH_A])

        assert [c["filename"] for c in candidates["images"]] == [
            "mockup-9da8b0e6-flat-front-01.png",
            "mockup-9da8b0e6-flat-front-02.png",
        ]
        assert candidates["videos"] == []
        # Enough for the UI to render and preview one asset on its own.
        first = candidates["images"][0]
        assert first["source"] == "mockup"
        assert first["source_job"] == BATCH_A
        assert first["preview"] == {"kind": "mockup", "job": BATCH_A, "file": "flat-front-01.png"}

    def test_two_batches_with_identical_filenames_do_not_collide(self, builder):
        """The reason filenames carry their owning job: mixing batches is
        now normal, and every real pair of runs shares filenames."""
        b, root = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A, BATCH_B])

        assert _built(root) == [
            "mockup-7cfa41c4-flat-front-01.png",
            "mockup-7cfa41c4-flat-front-02.png",
            "mockup-9da8b0e6-flat-front-01.png",
            "mockup-9da8b0e6-flat-front-02.png",
        ]
        # ...and they are genuinely the two different runs' bytes.
        workspace = root / "demo"
        assert (workspace / "mockup-9da8b0e6-flat-front-01.png").read_bytes() == b"run-001-first"
        assert (workspace / "mockup-7cfa41c4-flat-front-01.png").read_bytes() == b"run-002-first"

    def test_candidate_filenames_are_exactly_what_a_build_produces(self, builder):
        """The contract that makes filenames usable as the selection key:
        one plan() feeds both, so they can never drift apart."""
        b, root = builder
        candidates = b.list_candidates(mockup_job_ids=[BATCH_A, BATCH_B])
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A, BATCH_B])

        assert sorted(c["filename"] for c in candidates["images"]) == _built(root)

    def test_listing_candidates_creates_nothing(self, builder):
        b, root = builder
        b.list_candidates(mockup_job_ids=[BATCH_A])
        assert not root.exists()


class TestSelection:
    def test_build_copies_only_the_selected_assets(self, builder):
        b, root = builder
        b.build(
            listing_id="demo",
            mockup_job_ids=[BATCH_A, BATCH_B],
            selected_filenames=["mockup-9da8b0e6-flat-front-02.png", "mockup-7cfa41c4-flat-front-01.png"],
        )

        assert _built(root) == [
            "mockup-7cfa41c4-flat-front-01.png",
            "mockup-9da8b0e6-flat-front-02.png",
        ]

    def test_omitting_the_selection_builds_everything(self, builder):
        """"All of it" stays the default -- and is what every caller
        predating per-asset selection meant."""
        b, root = builder
        manifest = b.build(listing_id="demo", mockup_job_ids=[BATCH_A])
        assert len(manifest["images"]) == 2
        assert len(_built(root)) == 2

    def test_clearing_every_asset_builds_an_empty_workspace(self, builder):
        """Distinct from omitting the selection: an explicit empty list
        means the operator deselected everything, not "give me all of it"."""
        b, root = builder
        manifest = b.build(listing_id="demo", mockup_job_ids=[BATCH_A], selected_filenames=[])

        assert manifest["images"] == []
        assert _built(root) == []

    def test_a_filename_the_planner_never_produced_is_ignored(self, builder):
        """The selection is a filter over Controller-produced filenames, so
        a client cannot use it to name a file of its own."""
        b, root = builder
        b.build(
            listing_id="demo",
            mockup_job_ids=[BATCH_A],
            selected_filenames=["../../../etc/passwd", "mockup-9da8b0e6-flat-front-01.png", "not-a-real-asset.png"],
        )

        assert _built(root) == ["mockup-9da8b0e6-flat-front-01.png"]

    def test_manifest_records_the_plural_sources(self, builder):
        b, _ = builder
        manifest = b.build(listing_id="demo", mockup_job_ids=[BATCH_A, BATCH_B])

        assert manifest["sources"] == {
            "video_job_ids": [],
            "mockup_job_ids": [BATCH_A, BATCH_B],
            "ai_image_job_names": [],
        }


class TestFailedBuildIsNonDestructive:
    def test_failed_rebuild_leaves_the_previous_build_intact(self, builder):
        """The regression that motivated this module: one bad source id
        must not cost the operator the build they already had."""
        b, root = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A])
        assert _built(root) == [
            "mockup-9da8b0e6-flat-front-01.png",
            "mockup-9da8b0e6-flat-front-02.png",
        ]

        with pytest.raises(ListingWorkspaceError):
            b.build(listing_id="demo", mockup_job_ids=["does-not-exist"])

        # Every file, and the manifest, survive the failure untouched.
        assert _built(root) == [
            "mockup-9da8b0e6-flat-front-01.png",
            "mockup-9da8b0e6-flat-front-02.png",
        ]
        assert _manifest_file().is_file()
        assert b.get_workspace("demo") is not None

    def test_failed_first_build_leaves_no_empty_workspace_behind(self, builder):
        """Nothing was there to protect, but a half-made folder with no
        manifest is still a workspace that shows up nowhere and cleans up
        after nobody -- so it must not be created at all."""
        b, root = builder
        with pytest.raises(ListingWorkspaceError):
            b.build(listing_id="never-built", mockup_job_ids=["does-not-exist"])

        assert not (root / "never-built").exists()
        assert b.list_workspaces() == []


class TestRebuildKeepsTheFolderItself:
    def test_rebuild_does_not_remove_and_recreate_the_workspace_directory(self, builder):
        """Guards the Windows delete-pending failure: on Windows a removed
        directory only disappears once every handle closes, so rmtree()
        followed by mkdir() raises PermissionError whenever Explorer still
        has the folder open -- the ordinary flow, since "Open Folder" is
        what an operator does right before Rebuild.

        Asserted against rmtree directly rather than via a marker file left
        at the workspace root: the workspace is a flat deliverables folder
        now, so a rebuild legitimately clears every file in it, and a
        surviving marker is no longer the property to protect. What must
        stay true is that the DIRECTORY ITSELF is never the thing removed.
        """
        b, root = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A])
        workspace_dir = root / "demo"

        real_rmtree = listing_workspace.shutil.rmtree
        removed: list[Path] = []

        def spy(path, *args, **kwargs):
            removed.append(Path(path))
            return real_rmtree(path, *args, **kwargs)

        with patch.object(listing_workspace.shutil, "rmtree", spy):
            b.build(listing_id="demo", mockup_job_ids=[BATCH_A])

        assert workspace_dir.resolve() not in [p.resolve() for p in removed]
        assert workspace_dir.is_dir()

    def test_rebuild_still_clears_stale_assets(self, builder):
        """The no-stale-files contract the docstring promises is unchanged
        by the above -- a rebuild from fewer sources drops what's gone."""
        b, root = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A])
        (root / "demo" / "mockup-from-an-older-batch.png").write_bytes(b"stale")

        manifest = b.build(listing_id="demo")  # no sources at all this time

        assert _built(root) == []
        assert manifest["images"] == []
        assert manifest["videos"] == []


class TestTheWorkspaceIsADeliverablesFolder:
    """What "Open Listing Workspace" puts in front of the operator must be
    exactly the set of files they will upload to Etsy -- so that opening it,
    pressing Ctrl+A and dragging into Etsy is correct with no filtering and
    no descending into subfolders.

    It used to be Images/ + Videos/ + listing.json, which failed that on
    both counts: two folders to visit, and a file among the assets that
    isn't one.
    """

    def test_workspace_contains_only_assets(self, builder):
        b, root = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A])

        names = _built(root)
        assert names == [
            "mockup-9da8b0e6-flat-front-01.png",
            "mockup-9da8b0e6-flat-front-02.png",
        ]
        # Nothing an operator would have to skip past or open.
        assert "listing.json" not in names
        assert not (root / "demo" / "Images").exists()
        assert not (root / "demo" / "Videos").exists()

    def test_every_entry_is_a_file_not_a_folder(self, builder):
        """Ctrl+A then drag only works if there is nothing to descend into."""
        b, root = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A, BATCH_B])

        assert all(p.is_file() for p in (root / "demo").iterdir())

    def test_images_and_videos_sit_side_by_side(self, builder_with_video):
        b, root = builder_with_video
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A], video_job_ids=[VIDEO_JOB])

        assert _built(root) == [
            "mockup-9da8b0e6-flat-front-01.png",
            "mockup-9da8b0e6-flat-front-02.png",
            "video-11111111-listing-video-001.mp4",
        ]

    def test_manifest_is_kept_outside_the_workspace(self, builder):
        """Still written, still complete -- just not where the operator
        collects their uploads from."""
        b, root = builder
        manifest = b.build(listing_id="demo", mockup_job_ids=[BATCH_A])

        assert not (root / "demo" / "listing.json").exists()
        assert _manifest_file().is_file()
        assert json.loads(_manifest_file().read_text(encoding="utf-8")) == manifest
        assert len(manifest["images"]) == 2

    def test_the_controller_still_reads_the_workspace_back(self, builder):
        """Moving the manifest must not cost the Controller anything it
        had before -- both read paths still work."""
        b, _ = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A])

        assert b.get_workspace("demo") is not None
        assert [w["listing_id"] for w in b.list_workspaces()] == ["demo"]

    def test_rebuild_clears_an_older_builds_subfolders(self, builder):
        """A workspace built by the previous layout is migrated by its next
        rebuild rather than keeping Images/ and Videos/ around forever."""
        b, root = builder
        b.build(listing_id="demo", mockup_job_ids=[BATCH_A])
        legacy_images = root / "demo" / "Images"
        legacy_images.mkdir()
        (legacy_images / "mockup-from-the-old-layout.png").write_bytes(b"stale")
        (root / "demo" / "listing.json").write_text("{}", encoding="utf-8")

        b.build(listing_id="demo", mockup_job_ids=[BATCH_A])

        assert not legacy_images.exists()
        assert "listing.json" not in _built(root)
