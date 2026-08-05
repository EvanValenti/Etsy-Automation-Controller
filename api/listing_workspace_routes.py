"""Listing Workspace routes -- a Controller-owned feature, not an engine.
See infra/listing_workspace.py's module docstring for the full design
(workspace layout, source resolution, rebuild semantics). This module is
the thin HTTP layer only: request/response mapping, delegating every real
decision to ListingWorkspaceBuilder.

No engine-prefix collision risk here (unlike the per-engine operator
routes) -- /listing-workspace/... is a route namespace no engine module
touches, since this feature only ever reads already-public Job/engine
artifacts, never anything routed through an engine's own API surface.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.dependencies import get_job_service
from core.services.job_service import JobService
from infra.cleanup import delete_listing_workspace as cleanup_delete_listing_workspace
from infra.listing_workspace import ListingWorkspaceBuilder, ListingWorkspaceError

router = APIRouter()


def _builder(job_service: JobService = Depends(get_job_service)) -> ListingWorkspaceBuilder:
    return ListingWorkspaceBuilder(job_service)


class ListingSourcesRequest(BaseModel):
    """The jobs a listing is being assembled from. Plural throughout: an
    operator assembles one listing from whatever mix of batches, jobs, and
    videos actually belongs to it (see infra/listing_workspace.py)."""

    video_job_ids: list[str] = []
    mockup_job_ids: list[str] = []
    ai_image_job_names: list[str] = []


class BuildListingWorkspaceRequest(ListingSourcesRequest):
    listing_id: str
    # The workspace filenames (as returned by /listing-workspace/candidates)
    # the operator kept selected. null/omitted means "everything these
    # sources offer" -- see ListingWorkspaceBuilder.build().
    selected_filenames: list[str] | None = None


@router.post("/listing-workspace/candidates")
def list_listing_workspace_candidates(
    payload: ListingSourcesRequest, builder: ListingWorkspaceBuilder = Depends(_builder)
) -> dict:
    """Every individually selectable asset behind these sources, so the
    operator can pick assets rather than whole jobs. Read-only -- nothing
    is copied, created, or modified; POST only because the sources are
    lists, not because it changes anything.

    Deliberately the same plan() the build below copies from, so what the
    operator sees selectable is exactly what a build would produce.
    """
    try:
        return builder.list_candidates(
            video_job_ids=payload.video_job_ids,
            mockup_job_ids=payload.mockup_job_ids,
            ai_image_job_names=payload.ai_image_job_names,
        )
    except ListingWorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/listing-workspace/build")
def build_listing_workspace(payload: BuildListingWorkspaceRequest, builder: ListingWorkspaceBuilder = Depends(_builder)) -> dict:
    """Builds (or rebuilds -- always regenerates Images/Videos fresh from
    the current sources and selection) the workspace. Fast, local file
    copying only -- no external API call, so this is a plain synchronous
    route, not a Controller Job."""
    if not payload.listing_id.strip():
        raise HTTPException(status_code=422, detail="listing_id is required")
    try:
        return builder.build(
            listing_id=payload.listing_id,
            video_job_ids=payload.video_job_ids,
            mockup_job_ids=payload.mockup_job_ids,
            ai_image_job_names=payload.ai_image_job_names,
            selected_filenames=payload.selected_filenames,
        )
    except ListingWorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/listing-workspace")
def list_listing_workspaces(builder: ListingWorkspaceBuilder = Depends(_builder)) -> list[dict]:
    return builder.list_workspaces()


@router.get("/listing-workspace/{listing_id}")
def get_listing_workspace(listing_id: str, builder: ListingWorkspaceBuilder = Depends(_builder)) -> dict:
    manifest = builder.get_workspace(listing_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Listing workspace not found: {listing_id!r}")
    return manifest


@router.get("/listing-workspace/{listing_id}/assets/{filename}/file")
def get_listing_workspace_asset_file(listing_id: str, filename: str, builder: ListingWorkspaceBuilder = Depends(_builder)):
    """Serves one already-built workspace asset's raw bytes -- a file this
    module itself copied there, never anything from a generator's own
    output tree directly (see module docstring: this only ever reads
    Controller-owned data). Mirrors image_generator_routes.py's
    get_reference_image_file() path-safety pattern: filename is re-resolved
    and re-validated against the workspace folder before use, never trusted
    as-is.

    One flat lookup, since the workspace is now one flat deliverables
    folder -- images and videos sit side by side in it."""
    try:
        folder = builder.workspace_folder(listing_id)
    except ListingWorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    candidate = (folder / filename).resolve()
    if folder.resolve() == candidate.parent and candidate.is_file():
        return FileResponse(candidate)
    raise HTTPException(status_code=404, detail=f"Asset not found in this workspace: {filename!r}")


@router.post("/listing-workspace/{listing_id}/open")
def open_listing_workspace(listing_id: str, builder: ListingWorkspaceBuilder = Depends(_builder)) -> dict[str, str]:
    try:
        folder = builder.workspace_folder(listing_id)
    except ListingWorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    os.startfile(str(folder))  # noqa: S606 -- local desktop app, user-triggered, path validated above
    return {"opened": str(folder)}


@router.delete("/listing-workspace/{listing_id}", status_code=204)
def delete_listing_workspace(listing_id: str, builder: ListingWorkspaceBuilder = Depends(_builder)) -> None:
    """Prompt 4 (Cleanup & Job Lifecycle): recycles the built workspace
    folder to the Windows Recycle Bin -- see infra/cleanup.py. Always safe
    to call again after a rebuild (POST .../build), since the workspace is
    disposable and never the source of truth for any asset."""
    try:
        cleanup_delete_listing_workspace(builder, listing_id)
    except ListingWorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return None
