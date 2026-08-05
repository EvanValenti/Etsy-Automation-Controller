"""ListingWorkspaceBuilder — a Controller-owned feature, not an engine.

Assembles every approved asset for one Etsy listing into one temporary,
human-browsable folder, by COPYING (never moving/renaming) from each
generator's own real, already-validated output:
  - etsy-video-generator:  Job.result_summary["output_video_path"]
  - etsy-mockup-generator: Job.result_summary["assets_dir"] (every file in it)
  - etsy-ai-image-generator: each named job's real
    outputs/approved_media_handoff.json -- only assets a human has
    actually approved (review_status == "approved"), never the raw
    generated/ folder.

The generators never know this exists -- this reads only Controller-side
Job rows (for video/mockup) and one engine's own already-public JSON
artifact (for AI images, via the same adapter helper
api/image_generator_routes.py already uses for status reads). Nothing
here writes back to any generator's output tree; every file under
generator output/ or jobs/ directories is read-only from this module's
perspective.

The unit of selection is one ASSET, not one job. plan() enumerates every
individual approved image, mockup, and video the chosen sources offer, and
the operator builds from whichever subset of those they keep selected --
so one listing can freely mix, say, 3 approved AI images, 17 mockups drawn
from more than one batch, and 1 video. Sources are therefore plural
throughout (video_job_ids, mockup_job_ids, ai_image_job_names).

Workspace layout -- the workspace folder is the operator's DELIVERABLES
folder, and holds nothing else:
    var/listing_workspaces/<listing_id>/
        every final asset, flat: approved mockups, AI images and videos,
        each source-prefixed so names can't collide (mockup-..., ai-<job>-...,
        video-...).

That is the whole folder. Open it, Ctrl+A, drag into Etsy -- no subfolders
to descend into and no files to skip past. It previously split assets into
Images/ and Videos/ and left listing.json sitting among them, which meant
the operator had to visit two folders and avoid a file that isn't an
uploadable asset.

The manifest still exists and is unchanged in content; it just lives
outside the folder the operator opens:
    var/listing_workspace_manifests/<listing_id>.json
        lightweight manifest: sources, every copied file, built_at --
        enough for a future Draft Editor to consume without re-deriving
        anything, never more than that.

Rebuilding (calling build() again for the same listing_id) regenerates the
assets and the manifest from scratch -- no stale files left over from a
previous build with different sources. It does so in two phases: every
source is resolved first, and only then is anything on disk touched, so a
rebuild that fails (a deleted Job, a relocated output folder) leaves the
previous good build completely intact. The workspace directory itself is
cleared, never removed and recreated -- see the Phase 2 comment in build().
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.services.job_service import JobService
from infra.adapters.discovery_utils import resolve_engine_repo_root
from infra.adapters.image_generator.adapter import get_ai_image_job_status

VIDEO_REPO_DIR_NAME = "etsy-video-generator"
MOCKUP_REPO_DIR_NAME = "etsy-mockup-generator"
IMAGE_REPO_DIR_NAME = "Etsy-AI-Image-Generator"

WORKSPACES_ROOT = Path(__file__).resolve().parent.parent / "var" / "listing_workspaces"

# The manifests live OUTSIDE the workspace folders, not inside them: the
# workspace folder is what "Open Listing Workspace" puts in front of the
# operator, and it must contain only assets they can upload. A sibling root
# rather than a hidden subfolder, so nothing has to filter it back out when
# listing workspaces or clearing one for a rebuild.
MANIFESTS_ROOT = Path(__file__).resolve().parent.parent / "var" / "listing_workspace_manifests"


def manifest_path_for(slug: str) -> Path:
    """Where one workspace's manifest lives. The single definition of that
    mapping, so a reader never reconstructs it (mirrors the same move made
    for the video generator's per-video metadata)."""
    return MANIFESTS_ROOT / f"{slug}.json"


class ListingWorkspaceError(Exception):
    """A genuine failure (bad Job id, missing/relocated source file,
    invalid listing_id) -- never raised for "nothing to build", which
    just yields an empty Images/Videos folder."""


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "listing"


def _sanitize_filename(name: str) -> str:
    base = Path(name).name
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in base)
    return safe or "file"


def _clear_directory(directory: Path) -> None:
    """Empty a directory without removing the directory itself -- see the
    Phase 2 comment in build() for why that distinction matters on Windows.
    """
    for entry in directory.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


@dataclass
class BuiltAsset:
    """One asset as recorded in a built workspace's listing.json."""

    filename: str
    source: str  # "mockup" | "ai_image" | "video"
    source_job: str
    original_path: str


@dataclass
class PlannedAsset:
    """One individually selectable asset, before anything is copied.

    `asset.filename` is the workspace filename this would be copied to --
    unique across every source by construction (each is prefixed with its
    source and owning job), which is exactly what makes it usable as the
    selection key the operator's picks are expressed in. The client only
    ever chooses from filenames the Controller itself produced here; it
    can never introduce a path of its own.

    `label` and `preview` exist purely so the UI can show a recognizable,
    previewable asset before a build happens. `preview` names an already-
    public per-engine endpoint (never a filesystem path): the client turns
    it into a URL, the same one that engine's own pages already use.
    """

    source_path: Path
    asset: BuiltAsset
    label: str
    preview: dict[str, str]

    def as_candidate(self) -> dict[str, Any]:
        return {
            "filename": self.asset.filename,
            "source": self.asset.source,
            "source_job": self.asset.source_job,
            "label": self.label,
            "preview": self.preview,
        }


class ListingWorkspaceBuilder:
    def __init__(self, job_service: JobService) -> None:
        self._jobs = job_service

    # -- Source resolution (read-only against each generator's real output) --

    def _resolve_video_path(self, video_job_id: str) -> tuple[Path, str]:
        job = self._jobs.get_job(video_job_id)
        if job is None:
            raise ListingWorkspaceError(f"Video job not found: {video_job_id}")
        if job.engine_id != "etsy-video-generator":
            raise ListingWorkspaceError(f"Job {video_job_id} does not belong to etsy-video-generator")
        raw_path = (job.result_summary or {}).get("output_video_path")
        if not raw_path:
            raise ListingWorkspaceError(f"Video job {video_job_id} has no output_video_path in its result.")
        path = Path(raw_path).resolve()
        repo_root = resolve_engine_repo_root(VIDEO_REPO_DIR_NAME)
        if repo_root is None or repo_root.resolve() not in path.parents:
            raise ListingWorkspaceError("Video output path is not inside the video engine's repo -- refusing to copy.")
        if not path.is_file():
            raise ListingWorkspaceError(f"Video file no longer exists on disk: {path}")
        return path, job.id

    def _resolve_mockup_assets_dir(self, mockup_job_id: str) -> tuple[Path, str]:
        job = self._jobs.get_job(mockup_job_id)
        if job is None:
            raise ListingWorkspaceError(f"Mockup job not found: {mockup_job_id}")
        if job.engine_id != "etsy-mockup-generator":
            raise ListingWorkspaceError(f"Job {mockup_job_id} does not belong to etsy-mockup-generator")
        raw_dir = (job.result_summary or {}).get("assets_dir")
        if not raw_dir:
            raise ListingWorkspaceError(f"Mockup job {mockup_job_id} has no assets_dir in its result.")
        path = Path(raw_dir).resolve()
        repo_root = resolve_engine_repo_root(MOCKUP_REPO_DIR_NAME)
        if repo_root is None or (repo_root / "output").resolve() not in path.parents:
            raise ListingWorkspaceError("Mockup assets_dir is not inside the mockup engine's output directory -- refusing to copy.")
        if not path.is_dir():
            raise ListingWorkspaceError(f"Mockup assets folder no longer exists on disk: {path}")
        return path, job.id

    def _resolve_approved_ai_images(self, job_name: str) -> list[tuple[Path, dict[str, Any]]]:
        try:
            status = get_ai_image_job_status(job_name)
        except Exception as exc:  # noqa: BLE001 -- EngineLaunchError from the adapter, or a genuine crash
            raise ListingWorkspaceError(f"Could not read AI Image Generator job {job_name!r}: {exc}") from exc

        repo_root = resolve_engine_repo_root(IMAGE_REPO_DIR_NAME)
        if repo_root is None:
            raise ListingWorkspaceError("Etsy-AI-Image-Generator repo is not reachable.")
        job_folder = (repo_root / "jobs" / job_name).resolve()
        jobs_dir = (repo_root / "jobs").resolve()
        if jobs_dir not in job_folder.parents:
            raise ListingWorkspaceError(f"job_name resolves outside the engine's jobs directory: {job_name!r}")

        handoff_rel = status.get("artifacts", {}).get("approved_media_handoff")
        if not handoff_rel:
            return []  # nothing approved yet for this job -- not an error, just nothing to collect
        handoff_path = (repo_root / handoff_rel).resolve()
        if jobs_dir not in handoff_path.parents or not handoff_path.is_file():
            raise ListingWorkspaceError(f"approved_media_handoff.json path is invalid for job {job_name!r}.")

        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        found: list[tuple[Path, dict[str, Any]]] = []
        for entries in handoff.get("approved_assets", {}).values():
            for entry in entries:
                rel = entry.get("approved_copy_path")
                if not rel:
                    continue
                candidate = (job_folder / rel).resolve()
                if job_folder not in candidate.parents:
                    continue  # never trust a path that escapes this job's own folder
                if candidate.is_file():
                    # The entry travels with the path so the UI can name and
                    # preview this one image (concept_name, media_category,
                    # concept_id) without re-reading the handoff itself.
                    found.append((candidate, entry))
        return found

    # -- Planning ------------------------------------------------------------

    def plan(
        self,
        video_job_ids: list[str] | None = None,
        mockup_job_ids: list[str] | None = None,
        ai_image_job_names: list[str] | None = None,
    ) -> tuple[list[PlannedAsset], list[PlannedAsset]]:
        """Every individually selectable asset these sources offer, as
        (images, videos) -- resolved and validated, but nothing copied.

        This is the single source of truth for "what assets exist here":
        list_candidates() shows it to the operator and build() copies from
        it, so the filenames the operator selects are always exactly the
        filenames a build would produce. There is no second enumeration
        that could drift from this one.

        Filenames are prefixed with the owning job because an operator may
        now mix several mockup batches (or several videos) into one
        listing, and two batches routinely contain the same
        "flat-front-01.png".
        """
        images: list[PlannedAsset] = []
        videos: list[PlannedAsset] = []

        for mockup_job_id in mockup_job_ids or []:
            assets_dir, real_job_id = self._resolve_mockup_assets_dir(mockup_job_id)
            short = _sanitize_filename(real_job_id[:8])
            for src in sorted(p for p in assets_dir.iterdir() if p.is_file()):
                filename = f"mockup-{short}-{_sanitize_filename(src.name)}"
                images.append(
                    PlannedAsset(
                        source_path=src,
                        asset=BuiltAsset(filename, "mockup", real_job_id, str(src)),
                        label=src.stem,
                        preview={"kind": "mockup", "job": real_job_id, "file": src.name},
                    )
                )

        for job_name in ai_image_job_names or []:
            for src, entry in self._resolve_approved_ai_images(job_name):
                concept_id = str(entry.get("concept_id") or src.parent.name)
                category = str(entry.get("media_category") or "")
                filename = (
                    f"ai-{_sanitize_filename(job_name)}-{_sanitize_filename(concept_id)}-{_sanitize_filename(src.name)}"
                )
                images.append(
                    PlannedAsset(
                        source_path=src,
                        asset=BuiltAsset(filename, "ai_image", job_name, str(src)),
                        label=str(entry.get("concept_name") or concept_id),
                        # The engine's own already-public generated-image
                        # endpoint, which the AI Image pages already serve
                        # from -- no new file-serving surface for this.
                        preview={
                            "kind": "ai_image",
                            "job": job_name,
                            "category": category,
                            "concept_id": concept_id,
                            "file": src.name,
                        },
                    )
                )

        for video_job_id in video_job_ids or []:
            src, real_job_id = self._resolve_video_path(video_job_id)
            short = _sanitize_filename(real_job_id[:8])
            filename = f"video-{short}-{_sanitize_filename(src.name)}"
            videos.append(
                PlannedAsset(
                    source_path=src,
                    asset=BuiltAsset(filename, "video", real_job_id, str(src)),
                    label=src.stem,
                    preview={"kind": "video", "job": real_job_id},
                )
            )

        return images, videos

    def list_candidates(
        self,
        video_job_ids: list[str] | None = None,
        mockup_job_ids: list[str] | None = None,
        ai_image_job_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """The selectable assets behind these sources, for the operator to
        pick from before building. Read-only: nothing is copied or created.
        """
        images, videos = self.plan(video_job_ids, mockup_job_ids, ai_image_job_names)
        return {
            "images": [p.as_candidate() for p in images],
            "videos": [p.as_candidate() for p in videos],
        }

    # -- Build / rebuild -----------------------------------------------------

    def build(
        self,
        listing_id: str,
        video_job_ids: list[str] | None = None,
        mockup_job_ids: list[str] | None = None,
        ai_image_job_names: list[str] | None = None,
        selected_filenames: list[str] | None = None,
    ) -> dict[str, Any]:
        """Copies the selected source assets, flat, into
        var/listing_workspaces/<listing_id>/ -- the operator's deliverables
        folder. Safe to call again for the same listing_id: the folder is
        emptied and rebuilt from the current sources each time, so a rebuild
        after new approvals never leaves stale files from an earlier build
        behind (including the Images/ and Videos/ subfolders an older build
        of this same listing may have created).

        `selected_filenames` narrows the plan to exactly the assets the
        operator kept selected, named by the workspace filenames plan()
        produced. Omitting it (or passing None) builds everything these
        sources offer -- the natural "I want all of it" default, and what
        every caller predating per-asset selection meant.

        Runs in two phases (see the comments below): every source is
        resolved before anything on disk is touched, so a build that fails
        leaves the previous build untouched rather than half-destroyed.
        """
        slug = _slugify(listing_id)
        workspace_dir = WORKSPACES_ROOT / slug

        # -- Phase 1: resolve every source BEFORE touching anything on disk.
        # Each _resolve_* call raises ListingWorkspaceError for a bad Job id,
        # a wrong-engine Job, or a source file that has since moved -- and a
        # rebuild that fails that way must leave the previous good build
        # exactly as it was, rather than costing the operator assets they
        # still had a moment ago.
        planned_images, planned_videos = self.plan(video_job_ids, mockup_job_ids, ai_image_job_names)

        if selected_filenames is not None:
            # A filter over Controller-produced filenames, never a source of
            # paths: anything the client sends that plan() didn't produce
            # simply matches nothing.
            keep = set(selected_filenames)
            planned_images = [p for p in planned_images if p.asset.filename in keep]
            planned_videos = [p for p in planned_videos if p.asset.filename in keep]

        # -- Phase 2: commit. Only the CONTENTS of the workspace are cleared;
        # the workspace directory itself is never removed and recreated. On
        # Windows a directory removal only completes once every handle on it
        # closes, so rmtree() immediately followed by mkdir() raises
        # PermissionError [WinError 5] whenever anything still has the folder
        # open -- which is the ordinary flow here, since "Open Folder" leaves
        # it open in Explorer and Rebuild is the very next thing an operator
        # does. Clearing contents has no such constraint, and still honours
        # the no-stale-files contract above.
        images = [p.asset for p in planned_images]
        videos = [p.asset for p in planned_videos]

        try:
            workspace_dir.mkdir(parents=True, exist_ok=True)
            _clear_directory(workspace_dir)

            # Everything lands flat, in one folder: images and videos are
            # both just assets to upload, and asking the operator to open two
            # folders to collect one listing bought nothing.
            for planned in planned_images + planned_videos:
                shutil.copy2(planned.source_path, workspace_dir / planned.asset.filename)
        except OSError as exc:
            # A locked or unwritable workspace is a real, actionable operator
            # problem ("close whatever has this folder open, then rebuild"),
            # not a Controller crash -- so it surfaces as a 422 like every
            # other build failure rather than a 500 traceback.
            raise ListingWorkspaceError(
                f"Could not write the listing workspace at {workspace_dir}: {exc}. "
                "Close anything using those files and build again."
            ) from exc

        manifest = {
            "listing_id": slug,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "sources": {
                "video_job_ids": video_job_ids or [],
                "mockup_job_ids": mockup_job_ids or [],
                "ai_image_job_names": ai_image_job_names or [],
            },
            "images": [vars(a) for a in images],
            "videos": [vars(a) for a in videos],
        }
        # Written outside the workspace folder -- it is Controller
        # bookkeeping, not something the operator uploads to Etsy.
        manifest_file = manifest_path_for(slug)
        try:
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
            manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except OSError as exc:
            raise ListingWorkspaceError(
                f"Could not write the listing manifest at {manifest_file}: {exc}. "
                "Close anything using those files and build again."
            ) from exc
        return manifest

    # -- Read / open -----------------------------------------------------------

    def get_workspace(self, listing_id: str) -> dict[str, Any] | None:
        manifest_path = manifest_path_for(_slugify(listing_id))
        if not manifest_path.is_file():
            return None
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def list_workspaces(self) -> list[dict[str, Any]]:
        """Every built workspace, read from the manifests rather than by
        scanning the workspace folders -- those now contain only assets, so
        there is nothing in them to identify a workspace by."""
        if not MANIFESTS_ROOT.is_dir():
            return []
        manifests = []
        for manifest_path in sorted(MANIFESTS_ROOT.glob("*.json")):
            try:
                manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return manifests

    def workspace_folder(self, listing_id: str) -> Path:
        """The validated workspace root for "Open Listing Workspace" --
        raises if it doesn't exist (has never been built)."""
        workspace_dir = (WORKSPACES_ROOT / _slugify(listing_id)).resolve()
        if WORKSPACES_ROOT.resolve() not in workspace_dir.parents:
            raise ListingWorkspaceError("Invalid listing_id.")
        if not workspace_dir.is_dir():
            raise ListingWorkspaceError(f"Listing workspace {listing_id!r} has not been built yet.")
        return workspace_dir
