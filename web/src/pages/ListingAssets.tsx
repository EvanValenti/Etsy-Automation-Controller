import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  buildListingWorkspace,
  deleteListingWorkspace,
  getListingWorkspaceAssetFileUrl,
  listAiImageJobs,
  listListingAssetCandidates,
  listJobs,
  listListingWorkspaces,
  listingAssetPreviewUrl,
  openListingWorkspace,
} from "../api/client";
import type {
  AiImageJobManifest,
  Job,
  ListingAssetCandidate,
  ListingSources,
  ListingWorkspaceManifest,
} from "../api/types";
import { Button } from "../components/Button";
import { EmptyBlock, ErrorBlock } from "../components/AsyncState";
import { PageHeader } from "../components/PageHeader";
import { SkeletonCards, SkeletonFeed } from "../components/Skeleton";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Panel } from "../components/Panel";
import { buildManifestLookup, jobIdentifier, manifestTitle } from "../jobPresentation";
import { AssetCard, SafeThumb } from "../components/listing-assets/AssetCard";
import { VideoPreviewOverlay } from "../components/listing-assets/VideoPreviewOverlay";
import { matchesQuery, matchScore } from "../components/listing-assets/assetMatching";
import { formatRelative, formatTimestamp } from "../status";
import { usePolling } from "../hooks/usePolling";

const VIDEO_GENERATOR_ENGINE_ID = "etsy-video-generator";
const MOCKUP_GENERATOR_ENGINE_ID = "etsy-mockup-generator";

// Readable names for the Video Generator's preset keys, so a video card
// leads with what the video IS rather than a config slug. Mirrors
// api/video_generator_routes.py's own preset list; an unknown key falls
// back to the raw slug rather than being hidden.
const PRESET_TITLES: Record<string, string> = {
  standard: "Standard",
  "slow-fade-color-variation": "Slow Fade (Color Variations)",
  "wisp-sweep-color-variation": "Wisp Sweep (Color Variations)",
  "design-reveal": "Design Reveal",
};

function friendlyError(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}

function jobTime(job: Job): number {
  const iso = job.updated_at ?? job.created_at;
  return iso ? new Date(iso).getTime() : 0;
}

/**
 * Every field one asset can be found by. Collected per asset type so the
 * Listing ID query searches the same things everywhere: the human title,
 * the engine's slug, the campaign, and whatever identifier the engine
 * records. Passed straight to matchesQuery()/matchScore().
 */
function aiJobSearchFields(job: AiImageJobManifest): (string | null)[] {
  return [
    job.product.product_name,
    job.job_name,
    job.store.store_name,
    job.campaign.campaign_name,
    job.product.product_type,
    job.product.product_color,
  ];
}

/**
 * A Mockup Generator BATCH job carries no design_id in its launch config
 * -- only a run_token -- but its result manifest does (design_id, and the
 * uploaded zip's filename). Reading those is what makes a finished batch
 * findable and nameable by something other than a uuid.
 */
function mockupManifestFields(job: Job): { designId: string | null; zipName: string | null } {
  const manifest = ((job.result_summary as Record<string, unknown> | null)?.manifest ?? {}) as Record<string, unknown>;
  const designId = typeof manifest.design_id === "string" && manifest.design_id.trim() ? manifest.design_id : null;
  const zip = typeof manifest.zip_filename === "string" && manifest.zip_filename.trim() ? manifest.zip_filename : null;
  return { designId, zipName: zip };
}

function controllerJobSearchFields(job: Job): (string | null)[] {
  const config = job.config as Record<string, unknown>;
  const { designId, zipName } = mockupManifestFields(job);
  return [
    jobIdentifier(job),
    // Video jobs now carry a design_id too (see
    // api/video_generator_routes.py), so one Design ID finds a design's
    // video, mockups, and AI images together.
    typeof config.design_id === "string" ? config.design_id : null,
    typeof config.job_name === "string" ? config.job_name : null,
    typeof config.preset_key === "string" ? config.preset_key : null,
    designId,
    zipName,
    job.id,
  ];
}

function videoJobTitle(job: Job | undefined): string {
  const presetKey = job ? (job.config as Record<string, unknown>).preset_key : null;
  if (typeof presetKey !== "string") return "Listing video";
  return PRESET_TITLES[presetKey] ?? presetKey;
}

/**
 * Listing Assets: collect, organize, preview, and assemble the approved
 * outputs of the three engines for one Etsy listing into a single folder
 * for manual upload. This page is a Controller-owned feature, not an
 * engine -- see infra/listing_workspace.py's module docstring. It never
 * creates or publishes anything on Etsy; the Draft Editor (out of V1
 * scope) is a separate, later concern.
 *
 * The unit of selection is one ASSET, not one job. Typing a Listing ID
 * finds the jobs related to it (unchanged), and every individual approved
 * image, mockup, and video those jobs produced is then shown as its own
 * selectable thumbnail -- all selected by default, because the normal case
 * is "use what I approved". The operator subtracts what they don't want.
 *
 * Selection is stored as the set of DESELECTED filenames rather than the
 * selected ones, which is what makes "everything is selected by default"
 * survive a background refresh: a newly-arrived asset is selected because
 * nothing has deselected it, and an asset the operator turned off stays
 * off when the candidate list reloads.
 */
export function ListingAssets() {
  const [listingId, setListingId] = useState("");
  const [deselected, setDeselected] = useState<Set<string>>(new Set());
  const [videoPreview, setVideoPreview] = useState<{ url: string; title: string } | null>(null);

  const [candidates, setCandidates] = useState<{ images: ListingAssetCandidate[]; videos: ListingAssetCandidate[] } | null>(null);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesError, setCandidatesError] = useState<Error | null>(null);

  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);
  const [built, setBuilt] = useState<ListingWorkspaceManifest | null>(null);

  const [openingId, setOpeningId] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const videoJobs = usePolling(() => listJobs({ status: "succeeded", engine_id: VIDEO_GENERATOR_ENGINE_ID }), 30000);
  const mockupJobs = usePolling(() => listJobs({ status: "succeeded", engine_id: MOCKUP_GENERATOR_ENGINE_ID }), 30000);
  const aiImageJobs = usePolling(listAiImageJobs, 30000);
  const workspaces = usePolling(listListingWorkspaces, 15000);

  const manifestLookup = useMemo(() => buildManifestLookup(aiImageJobs.data), [aiImageJobs.data]);
  /** A listing's readable title, taken from the AI image job it was built
   * from. Null when that job can't be resolved (a listing built from
   * mockups/video alone, or whose source job has since been deleted) --
   * callers fall back to the listing id rather than showing a guess. */
  function listingTitle(w: ListingWorkspaceManifest): string | null {
    for (const jobName of w.sources.ai_image_job_names) {
      const manifest = manifestLookup.get(jobName);
      if (manifest) {
        const title = manifestTitle(manifest);
        if (title) return title;
      }
    }
    return null;
  }

  // Only jobs with at least one approved image are useful sources -- an
  // unapproved job has nothing this page can legally collect yet.
  const usableAiJobs = useMemo(
    () => (aiImageJobs.data ?? []).filter((j): j is AiImageJobManifest => !j.status && j.counts.images_approved > 0),
    [aiImageJobs.data],
  );
  const sortedVideoJobs = useMemo(() => (videoJobs.data ?? []).slice().sort((a, b) => jobTime(b) - jobTime(a)), [videoJobs.data]);
  // Only mockup jobs that actually produced a collectable assets folder.
  // A "succeeded" preview-phase job has no assets_dir, so
  // infra/listing_workspace.py refuses to build from it -- offering those
  // as sources meant an operator could pick one and only find out at
  // Build time. This is the same "is it a usable source" filter already
  // applied to AI image jobs via images_approved.
  const sortedMockupJobs = useMemo(
    () =>
      (mockupJobs.data ?? [])
        .filter((j) => Boolean((j.result_summary as Record<string, unknown> | null)?.assets_dir))
        .sort((a, b) => jobTime(b) - jobTime(a)),
    [mockupJobs.data],
  );
  const sortedAiJobs = useMemo(
    () => usableAiJobs.slice().sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()),
    [usableAiJobs],
  );

  // -- Live filtering ------------------------------------------------------
  // The Listing ID field IS the search query. Only assets related to what
  // was typed are shown; unrelated assets are never padded in to keep the
  // page looking full. Best matches sort first, then newest.
  const query = listingId.trim();

  const matchedAiJobs = useMemo(
    () =>
      sortedAiJobs
        .filter((j) => matchesQuery(aiJobSearchFields(j), query))
        .sort((a, b) => matchScore(aiJobSearchFields(b), query) - matchScore(aiJobSearchFields(a), query)),
    [sortedAiJobs, query],
  );
  const matchedMockupJobs = useMemo(
    () =>
      sortedMockupJobs
        .filter((j) => matchesQuery(controllerJobSearchFields(j), query))
        .sort((a, b) => matchScore(controllerJobSearchFields(b), query) - matchScore(controllerJobSearchFields(a), query)),
    [sortedMockupJobs, query],
  );
  const matchedVideoJobs = useMemo(
    () =>
      sortedVideoJobs
        .filter((j) => matchesQuery(controllerJobSearchFields(j), query))
        .sort((a, b) => matchScore(controllerJobSearchFields(b), query) - matchScore(controllerJobSearchFields(a), query)),
    [sortedVideoJobs, query],
  );

  const loadedAll = aiImageJobs.hasLoadedOnce && mockupJobs.hasLoadedOnce && videoJobs.hasLoadedOnce;
  const totalAvailable = sortedAiJobs.length + sortedMockupJobs.length + sortedVideoJobs.length;

  // -- Candidate assets ----------------------------------------------------
  // Every matching job becomes a SOURCE automatically; there is no separate
  // "pick the jobs" step any more. The Controller expands those sources
  // into the individual assets below, using the same plan() a build copies
  // from -- so what is selectable here is exactly what a build produces.
  const sources: ListingSources = useMemo(
    () => ({
      video_job_ids: matchedVideoJobs.map((j) => j.id),
      mockup_job_ids: matchedMockupJobs.map((j) => j.id),
      ai_image_job_names: matchedAiJobs.map((j) => j.job_name),
    }),
    [matchedVideoJobs, matchedMockupJobs, matchedAiJobs],
  );
  const sourcesKey = JSON.stringify(sources);
  const hasSources = sources.video_job_ids.length + sources.mockup_job_ids.length + sources.ai_image_job_names.length > 0;

  useEffect(() => {
    if (!hasSources) {
      setCandidates(null);
      setCandidatesError(null);
      return;
    }
    let cancelled = false;
    // Debounced so a query still being typed doesn't fire a request per
    // keystroke -- the same 500ms settle the filtering itself uses.
    const timer = window.setTimeout(() => {
      setCandidatesLoading(true);
      setCandidatesError(null);
      listListingAssetCandidates(JSON.parse(sourcesKey) as ListingSources)
        .then((result) => {
          if (!cancelled) setCandidates(result);
        })
        .catch((err) => {
          if (!cancelled) setCandidatesError(err instanceof Error ? err : new Error(friendlyError(err)));
        })
        .finally(() => {
          if (!cancelled) setCandidatesLoading(false);
        });
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [sourcesKey, hasSources]);

  const aiAssets = useMemo(() => (candidates?.images ?? []).filter((c) => c.source === "ai_image"), [candidates]);
  const mockupAssets = useMemo(() => (candidates?.images ?? []).filter((c) => c.source === "mockup"), [candidates]);
  const videoAssets = useMemo(() => candidates?.videos ?? [], [candidates]);

  // -- Selection -----------------------------------------------------------
  const isSelected = useCallback((filename: string) => !deselected.has(filename), [deselected]);

  function toggleAsset(filename: string) {
    setDeselected((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  }

  function selectAll(assets: ListingAssetCandidate[]) {
    setDeselected((prev) => {
      const next = new Set(prev);
      for (const a of assets) next.delete(a.filename);
      return next;
    });
  }

  function clearAll(assets: ListingAssetCandidate[]) {
    setDeselected((prev) => {
      const next = new Set(prev);
      for (const a of assets) next.add(a.filename);
      return next;
    });
  }

  const selectedCount = useCallback(
    (assets: ListingAssetCandidate[]) => assets.filter((a) => isSelected(a.filename)).length,
    [isSelected],
  );

  const selectedAiCount = selectedCount(aiAssets);
  const selectedMockupCount = selectedCount(mockupAssets);
  const selectedVideoCount = selectedCount(videoAssets);
  const totalSelected = selectedAiCount + selectedMockupCount + selectedVideoCount;
  const totalCandidates = aiAssets.length + mockupAssets.length + videoAssets.length;

  const selectedFilenames = useMemo(
    () =>
      [...aiAssets, ...mockupAssets, ...videoAssets].filter((a) => isSelected(a.filename)).map((a) => a.filename),
    [aiAssets, mockupAssets, videoAssets, isSelected],
  );

  // Mirrors infra/listing_workspace.py's _slugify() closely enough to
  // detect "this listing_id already has a built workspace" client-side,
  // purely so the Build button can warn about replacing it -- the actual
  // slug is always computed authoritatively server-side.
  function slugify(text: string): string {
    return text.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "listing";
  }

  const existingWorkspaceForListingId = query
    ? (workspaces.data ?? []).find((w) => w.listing_id === slugify(listingId))
    : undefined;

  const canBuild = query.length > 0 && totalSelected > 0;

  async function handleBuild() {
    if (!canBuild) return;
    setBuilding(true);
    setBuildError(null);
    try {
      const manifest = await buildListingWorkspace({
        listing_id: query,
        ...sources,
        selected_filenames: selectedFilenames,
      });
      setBuilt(manifest);
      workspaces.refetch();
    } catch (err) {
      setBuildError(err instanceof ApiError ? err.message : friendlyError(err));
    } finally {
      setBuilding(false);
    }
  }

  async function handleOpen(id: string) {
    setOpeningId(id);
    setOpenError(null);
    try {
      await openListingWorkspace(id);
    } catch (err) {
      setOpenError(friendlyError(err));
    } finally {
      setOpeningId(null);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteListingWorkspace(deleteTarget);
      setDeleteTarget(null);
      if (built?.listing_id === deleteTarget) setBuilt(null);
      workspaces.refetch();
    } catch (err) {
      setDeleteError(friendlyError(err));
    } finally {
      setDeleting(false);
    }
  }

  const showingAssets = hasSources && (candidates !== null || candidatesLoading);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
      <PageHeader
        title="Listing Assets"
        subtitle="Choose exactly which approved AI images, mockups, and videos go into one Etsy listing, then collect them into a single folder you upload yourself. This assembles media only — it does not create or publish an Etsy draft."
      />

      {/* Step 1: Listing */}
      <Panel index={0} title="1. Listing" eyebrow="What are you assembling?">
        <div style={{ maxWidth: 480 }}>
          <div className="label" style={{ marginBottom: 6 }}>
            Listing ID
          </div>
          <input
            value={listingId}
            onChange={(e) => setListingId(e.target.value)}
            placeholder="e.g. Ceramic Mug — Mountain Fox"
            style={{
              width: "100%",
              background: "var(--panel-raised)",
              border: "1px solid var(--border-bright)",
              color: "var(--text-primary)",
              borderRadius: "var(--radius)",
              padding: "8px 10px",
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-sm)",
            }}
          />
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 8 }}>
            Names this build <em>and</em> finds your assets. Type any part of a product name, Design ID, job slug, or
            campaign — every matching asset is shown below, selected and ready.
          </div>
        </div>
      </Panel>

      {!query ? (
        <EmptyBlock>Enter a Listing ID above to see available assets and start building.</EmptyBlock>
      ) : (
        <>
          {/* Step 2: Assets. Everything matching is selected by default --
              the operator deselects what they don't want rather than
              hunting down what they do. */}
          <Panel index={1} title="2. Assets" eyebrow="Everything matching, selected by default">
            {candidatesError && <ErrorBlock error={candidatesError} />}

            {loadedAll && !hasSources ? (
              <EmptyBlock>
                <div style={{ fontSize: "var(--text-base)", color: "var(--text-secondary)", lineHeight: 1.6 }}>
                  No assets found matching “{query}”.
                  <br />
                  {totalAvailable === 0 ? (
                    <>
                      Generate AI images, mockups, or videos first — start from the{" "}
                      <Link to="/" style={{ color: "var(--accent)" }}>
                        Dashboard
                      </Link>
                      .
                    </>
                  ) : (
                    <>Try a shorter or different search — {totalAvailable} asset{totalAvailable === 1 ? "" : "s"} exist across the engines.</>
                  )}
                </div>
              </EmptyBlock>
            ) : (
              showingAssets && (
                <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
                  <AssetSection
                    title="AI Images"
                    assets={aiAssets}
                    selectedCount={selectedAiCount}
                    loading={candidatesLoading && candidates === null}
                    onSelectAll={() => selectAll(aiAssets)}
                    onClearAll={() => clearAll(aiAssets)}
                  >
                    {aiAssets.map((asset) => (
                      <AssetCard
                        key={asset.filename}
                        selected={isSelected(asset.filename)}
                        onToggle={() => toggleAsset(asset.filename)}
                        title={asset.label}
                        campaign={aiJobLabel(matchedAiJobs, asset.source_job)}
                        identifier={asset.source_job}
                        thumbnail={<SafeThumb src={listingAssetPreviewUrl(asset.preview)} />}
                      />
                    ))}
                  </AssetSection>

                  <AssetSection
                    title="Mockups"
                    assets={mockupAssets}
                    selectedCount={selectedMockupCount}
                    loading={candidatesLoading && candidates === null}
                    onSelectAll={() => selectAll(mockupAssets)}
                    onClearAll={() => clearAll(mockupAssets)}
                  >
                    {mockupAssets.map((asset) => {
                      const job = matchedMockupJobs.find((j) => j.id === asset.source_job);
                      return (
                        <AssetCard
                          key={asset.filename}
                          selected={isSelected(asset.filename)}
                          onToggle={() => toggleAsset(asset.filename)}
                          title={asset.label}
                          campaign={job ? mockupManifestFields(job).designId : null}
                          identifier={asset.source_job.slice(0, 8)}
                          thumbnail={<SafeThumb src={listingAssetPreviewUrl(asset.preview)} />}
                        />
                      );
                    })}
                  </AssetSection>

                  <AssetSection
                    title="Videos"
                    assets={videoAssets}
                    selectedCount={selectedVideoCount}
                    loading={candidatesLoading && candidates === null}
                    onSelectAll={() => selectAll(videoAssets)}
                    onClearAll={() => clearAll(videoAssets)}
                  >
                    {videoAssets.map((asset) => {
                      const job = matchedVideoJobs.find((j) => j.id === asset.source_job);
                      const title = videoJobTitle(job);
                      const url = listingAssetPreviewUrl(asset.preview);
                      return (
                        <AssetCard
                          key={asset.filename}
                          selected={isSelected(asset.filename)}
                          onToggle={() => toggleAsset(asset.filename)}
                          title={title}
                          date={job ? formatRelative(job.updated_at) : null}
                          identifier={asset.source_job.slice(0, 8)}
                          thumbnail={
                            // Muted, preload-metadata poster frame: enough to
                            // recognize the video without autoplaying a wall
                            // of them.
                            <video
                              src={url}
                              muted
                              preload="metadata"
                              style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                            />
                          }
                          onPreview={() => setVideoPreview({ url, title })}
                        />
                      );
                    })}
                  </AssetSection>
                </div>
              )
            )}
          </Panel>

          {/* Step 3: Selected Assets + Build */}
          <Panel index={2} title="3. Selected Assets & Build" eyebrow="Review, then assemble">
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ display: "flex", gap: 22, flexWrap: "wrap" }}>
                <SelectionCount label="AI Images" count={selectedAiCount} total={aiAssets.length} />
                <SelectionCount label="Mockups" count={selectedMockupCount} total={mockupAssets.length} />
                <SelectionCount label="Videos" count={selectedVideoCount} total={videoAssets.length} />
              </div>

              {totalCandidates > 0 && totalSelected === 0 && (
                <div style={{ fontSize: 13, color: "var(--text-dim)" }}>
                  Nothing is selected — select at least one asset above to build.
                </div>
              )}

              <div>
                <Button variant="primary" onClick={handleBuild} disabled={!canBuild} loading={building}>
                  {existingWorkspaceForListingId ? "Rebuild Listing Assets" : "Build Listing Assets"}
                </Button>
                <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 6 }}>
                  Copies the {totalSelected} selected file{totalSelected === 1 ? "" : "s"} into one folder for you to
                  review and upload manually. It does not create, edit, or publish an Etsy draft.
                </div>
                {existingWorkspaceForListingId && (
                  <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 4 }}>
                    A folder for this listing already exists — building again replaces its contents with the assets
                    selected above.
                  </div>
                )}
              </div>
              {buildError && <div style={{ fontSize: 11, color: "var(--state-failure)" }}>{buildError}</div>}
              {built && (
                <div style={{ fontSize: 12, color: "var(--state-success)" }}>
                  <div>
                    Built {built.listing_id}: {built.images.filter((i) => i.source === "ai_image").length} approved AI
                    image(s), {built.images.filter((i) => i.source === "mockup").length} mockup(s), {built.videos.length}{" "}
                    video(s) — {built.images.length + built.videos.length} file(s) total.
                  </div>
                  <button
                    onClick={() => handleOpen(built.listing_id)}
                    style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", textDecoration: "underline", fontFamily: "inherit", fontSize: "inherit", padding: 0, marginTop: 4 }}
                  >
                    Open folder
                  </button>
                </div>
              )}
            </div>
          </Panel>
        </>
      )}

      <Panel index={3} title="Previously Built" eyebrow="Existing" lastUpdated={workspaces.lastUpdated} refreshing={workspaces.refreshing} onRefresh={workspaces.refetch}>
        {workspaces.loading && !workspaces.hasLoadedOnce && <SkeletonFeed rows={2} />}
        {workspaces.error && <ErrorBlock error={workspaces.error} onRetry={workspaces.refetch} />}
        {workspaces.data && workspaces.data.length === 0 && <EmptyBlock>Nothing built yet — assemble your first listing above.</EmptyBlock>}
        {workspaces.data && workspaces.data.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {workspaces.data.map((w) => {
              const previewImage = w.images[0];
              return (
                <div
                  key={w.listing_id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius)",
                    padding: "8px 10px",
                    flexWrap: "wrap",
                  }}
                >
                  {previewImage ? (
                    <img
                      src={getListingWorkspaceAssetFileUrl(w.listing_id, previewImage.filename)}
                      alt=""
                      style={{ width: 44, height: 44, objectFit: "cover", borderRadius: "var(--radius)", border: "1px solid var(--border)", flexShrink: 0 }}
                    />
                  ) : (
                    <div
                      style={{
                        width: 44,
                        height: 44,
                        borderRadius: "var(--radius)",
                        border: "1px dashed var(--border)",
                        flexShrink: 0,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        fontSize: 9,
                        color: "var(--text-dim)",
                        textAlign: "center",
                      }}
                    >
                      no image
                    </div>
                  )}
                  {/* Readable listing title leads; the listing id drops to
                      secondary text alongside the counts. The title comes
                      from the AI image job this listing was built from --
                      never invented when that job isn't resolvable. */}
                  <div style={{ flex: 1, minWidth: 160 }}>
                    <div style={{ fontSize: "var(--text-lg)", fontWeight: 600 }}>{listingTitle(w) ?? w.listing_id}</div>
                    <div className="mono" style={{ fontSize: 12, color: "var(--text-dim)" }}>
                      {listingTitle(w) ? `${w.listing_id} · ` : ""}
                      {w.images.length} image(s), {w.videos.length} video(s) — built {formatTimestamp(w.built_at)}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button variant="outline" onClick={() => handleOpen(w.listing_id)} loading={openingId === w.listing_id}>
                      Open Folder
                    </Button>
                    <Button variant="danger" onClick={() => setDeleteTarget(w.listing_id)}>
                      Delete
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {openError && <div style={{ fontSize: 11, color: "var(--state-failure)", marginTop: 8 }}>{openError}</div>}
        {deleteError && <div style={{ fontSize: 11, color: "var(--state-failure)", marginTop: 8 }}>{deleteError}</div>}
      </Panel>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete this listing folder?"
        description="This moves the built folder (its copied images/video) to the Windows Recycle Bin — not a permanent delete. The original mockups, AI images, and video in their generators are never touched; you can rebuild this folder from them again at any time."
        confirmLabel="Delete"
        variant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* Preview stays inside the Controller -- never a download, an
          Explorer window, or a new tab. */}
      {videoPreview && (
        <VideoPreviewOverlay url={videoPreview.url} title={videoPreview.title} onClose={() => setVideoPreview(null)} />
      )}
    </div>
  );
}

/** The store/campaign line for one AI image, from the job that produced
 * it. Null when that job isn't in the current match set. */
function aiJobLabel(jobs: AiImageJobManifest[], jobName: string): string | null {
  const job = jobs.find((j) => j.job_name === jobName);
  if (!job) return null;
  return [job.store.store_name, job.campaign.campaign_name].filter(Boolean).join(" / ") || null;
}

/**
 * One asset type's thumbnails, with its own Select All / Clear All. The
 * bulk actions act on this section only, which is what makes "keep every
 * mockup but drop the video" a two-click operation.
 */
function AssetSection({
  title,
  assets,
  selectedCount,
  loading,
  onSelectAll,
  onClearAll,
  children,
}: {
  title: string;
  assets: { filename: string }[];
  selectedCount: number;
  loading: boolean;
  onSelectAll: () => void;
  onClearAll: () => void;
  children: React.ReactNode;
}) {
  const total = assets.length;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ fontSize: "var(--text-lg)", fontWeight: 700 }}>{title}</span>
        <span style={{ fontSize: "var(--text-sm)", color: "var(--text-dim)" }}>
          {total === 0 ? "none" : `${selectedCount} of ${total} selected`}
        </span>
        {total > 0 && (
          <div style={{ display: "flex", gap: 8, marginLeft: "auto" }}>
            <Button variant="outline" onClick={onSelectAll} disabled={selectedCount === total}>
              Select All
            </Button>
            <Button variant="outline" onClick={onClearAll} disabled={selectedCount === 0}>
              Clear All
            </Button>
          </div>
        )}
      </div>
      {loading ? (
        // A skeleton rather than a spinner: this section resolves into a
        // grid of thumbnails, so holding that shape while it loads stops
        // the page height jumping when the assets land.
        <SkeletonCards count={3} />
      ) : total === 0 ? (
        // Keeps the section's own capitalization ("AI Images", not
        // "ai images") rather than case-folding a proper noun.
        <div style={{ fontSize: 13, color: "var(--text-dim)", padding: "2px 0 6px" }}>
          No matching {title} for this listing.
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>{children}</div>
      )}
    </div>
  );
}

/** One asset type's live selection count, in the wording the operator is
 * deciding in: how many of this type end up in the listing. */
function SelectionCount({ label, count, total }: { label: string; count: number; total: number }) {
  return (
    <div>
      <div style={{ fontSize: "var(--text-base)", fontWeight: 700 }}>
        {label} <span style={{ color: count > 0 ? "var(--accent)" : "var(--text-dim)" }}>({count} selected)</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
        {total === 0 ? "none available" : `of ${total} available`}
      </div>
    </div>
  );
}
