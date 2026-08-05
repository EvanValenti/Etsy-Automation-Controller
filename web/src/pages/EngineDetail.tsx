import { useCallback, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Ban, CircleCheck, CircleX, Timer } from "lucide-react";
import {
  ApiError,
  createJob,
  getEngineHealth,
  getEngineMetrics,
  listAiImageJobs,
  listEngines,
  listJobs,
  openEngineOutputFolder,
} from "../api/client";
import { buildManifestLookup, groupJobs } from "../jobPresentation";
import { EmptyBlock, ErrorBlock } from "../components/AsyncState";
import { BackLink } from "../components/BackLink";
import { MetricTile } from "../components/MetricTile";
import { PageHeader } from "../components/PageHeader";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { JobTable } from "../components/JobTable";
import { Panel } from "../components/Panel";
import { SkeletonLine, SkeletonTable } from "../components/Skeleton";
import { EngineStateBadge } from "../components/StatusBadges";
import { ImageLaunchWorkflow } from "../components/image-generator/ImageLaunchWorkflow";
import { MockupLaunchWorkflow } from "../components/mockup-generator/MockupLaunchWorkflow";
import { VideoLaunchWorkflow } from "../components/video-generator/VideoLaunchWorkflow";
import { usePolling } from "../hooks/usePolling";

const VIDEO_GENERATOR_ENGINE_ID = "etsy-video-generator";
const MOCKUP_GENERATOR_ENGINE_ID = "etsy-mockup-generator";
const IMAGE_GENERATOR_ENGINE_ID = "etsy-ai-image-generator";

const textareaStyle: React.CSSProperties = {
  background: "var(--panel-raised)",
  border: "1px solid var(--border-bright)",
  color: "var(--text-primary)",
  borderRadius: "var(--radius)",
  padding: 10,
  fontSize: 12,
  resize: "vertical",
};

export function EngineDetail() {
  const { engineId } = useParams<{ engineId: string }>();
  const navigate = useNavigate();
  const [configText, setConfigText] = useState("{\n  \n}");
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingConfig, setPendingConfig] = useState<Record<string, unknown> | null>(null);
  const [openingFolder, setOpeningFolder] = useState(false);
  const [folderError, setFolderError] = useState<string | null>(null);

  const engines = usePolling(listEngines, 15000);
  const health = usePolling(useCallback(() => getEngineHealth(engineId!), [engineId]), 3000);
  const metrics = usePolling(useCallback(() => getEngineMetrics(engineId!), [engineId]), 5000);
  const recentJobs = usePolling(useCallback(() => listJobs({ engine_id: engineId! }), [engineId]), 5000);
  // Readable names come from the AI Image Generator's own manifests
  // (product / store / campaign). Only that engine publishes them today;
  // for anything else the lookup is empty and the table falls back to
  // identifiers rather than inventing a title.
  const aiImageJobs = usePolling(
    useCallback(
      () => (engineId === IMAGE_GENERATOR_ENGINE_ID ? listAiImageJobs().catch(() => []) : Promise.resolve([])),
      [engineId],
    ),
    15000,
  );
  const manifestLookup = useMemo(() => buildManifestLookup(aiImageJobs.data), [aiImageJobs.data]);
  const jobCount = useMemo(() => (recentJobs.data ? groupJobs(recentJobs.data).length : null), [recentJobs.data]);

  async function handleOpenEngineFolder() {
    setOpeningFolder(true);
    setFolderError(null);
    try {
      await openEngineOutputFolder(engineId!);
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : "Could not open the output folder.");
    } finally {
      setOpeningFolder(false);
    }
  }

  if (!engineId) return null;
  const engine = engines.data?.find((e) => e.id === engineId);

  function handleReviewLaunch(e: React.FormEvent) {
    e.preventDefault();
    setLaunchError(null);
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(configText);
    } catch {
      setLaunchError("Config must be valid JSON.");
      return;
    }
    setPendingConfig(config);
    setConfirmOpen(true);
  }

  async function handleConfirmedLaunch() {
    if (!pendingConfig) return;
    setLaunching(true);
    setLaunchError(null);
    try {
      const job = await createJob(engineId!, pendingConfig);
      setConfirmOpen(false);
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      setConfirmOpen(false);
      if (err instanceof ApiError && err.status === 422 && err.detail && typeof err.detail === "object" && "errors" in err.detail) {
        setLaunchError((err.detail as { errors: string[] }).errors.join(" · "));
      } else {
        setLaunchError(err instanceof Error ? err.message : "Launch failed");
      }
    } finally {
      setLaunching(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
      <BackLink to="/" />

      <PageHeader
        title={engine?.name ?? engineId}
        actions={
          <>
            {/* Right at the top, before any workflow chrome: the single most
                common thing an operator wants on an engine page is to look at
                what it produced. */}
            <Button variant="outline" onClick={handleOpenEngineFolder} loading={openingFolder}>
              Open Output Folder
            </Button>
            {health.data ? <EngineStateBadge state={health.data.state} /> : <SkeletonLine width={90} height={22} />}
          </>
        }
      />

      {folderError && <div style={{ fontSize: "var(--text-base)", color: "var(--state-failure)" }}>{folderError}</div>}
      {health.error && <ErrorBlock error={health.error} onRetry={health.refetch} />}

      {/* Outcome counts, in the order a run resolves: succeeded, then the
          three ways it can fail. Icons come from the same lucide set as the
          Dashboard tiles so the two pages read as one system. */}
      <div className="grid-stats">
        <MetricTile index={0} icon={CircleCheck} label="Completed" value={metrics.data ? String(metrics.data.success_count) : "—"} accent="var(--state-success)" />
        <MetricTile index={1} icon={CircleX} label="Failed" value={metrics.data ? String(metrics.data.failure_count) : "—"} accent="var(--state-failure)" />
        <MetricTile index={2} icon={Timer} label="Timed Out" value={metrics.data ? String(metrics.data.timeout_count) : "—"} accent="var(--state-degraded)" />
        <MetricTile index={3} icon={Ban} label="Cancelled" value={metrics.data ? String(metrics.data.cancel_count) : "—"} accent="var(--state-cancelled)" />
      </div>

      {/* The Queue and Capabilities panels used to sit here. Both were
          engine-internals readouts (wait_reason codes, discover()'s
          cancel_support/max_concurrent_runs/implementation_status) that an
          operator never acts on — they belong in developer documentation,
          not on the page someone uses to get work done. The underlying
          /queue and /metrics endpoints are untouched. */}

      <Panel index={4} title="Launch Job">
        {engineId === VIDEO_GENERATOR_ENGINE_ID ? (
          <VideoLaunchWorkflow />
        ) : engineId === MOCKUP_GENERATOR_ENGINE_ID ? (
          <MockupLaunchWorkflow />
        ) : engineId === IMAGE_GENERATOR_ENGINE_ID ? (
          <ImageLaunchWorkflow />
        ) : engine && !engine.capabilities.supports_launch ? (
          <EmptyBlock>
            This engine does not support launch yet ({engine.capabilities.implementation_status}) — see notes above.
          </EmptyBlock>
        ) : (
          <form onSubmit={handleReviewLaunch} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {engine && Object.keys(engine.capabilities.launch_config_schema).length > 0 && (
              <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
                {Object.entries(engine.capabilities.launch_config_schema).map(([k, v]) => (
                  <div key={k}>
                    <span style={{ color: "var(--accent)" }}>{k}</span>: {v}
                  </div>
                ))}
              </div>
            )}
            <textarea value={configText} onChange={(e) => setConfigText(e.target.value)} rows={6} className="mono" style={textareaStyle} />
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Button type="submit" variant="primary">
                Review + Launch
              </Button>
              {launchError && <span style={{ color: "var(--state-failure)", fontSize: "var(--text-xs)" }}>{launchError}</span>}
            </div>
          </form>
        )}
      </Panel>

      <Panel
        index={5}
        title="Job History"
        // Real jobs, not workflow-stage rows: one listing pushed through
        // five stages is "1 Job" here, matching what the table shows.
        eyebrow={jobCount !== null ? `${jobCount} Job${jobCount === 1 ? "" : "s"}` : undefined}
        lastUpdated={recentJobs.lastUpdated}
        refreshing={recentJobs.refreshing}
        onRefresh={recentJobs.refetch}
      >
        {recentJobs.loading && !recentJobs.hasLoadedOnce && <SkeletonTable rows={3} />}
        {recentJobs.error && <ErrorBlock error={recentJobs.error} onRetry={recentJobs.refetch} />}
        {recentJobs.data && (
          <JobTable
            jobs={recentJobs.data}
            emptyLabel="No jobs yet for this engine."
            manifests={manifestLookup}
            selectable
            onJobsDeleted={recentJobs.refetch}
          />
        )}
      </Panel>

      <ConfirmDialog
        open={confirmOpen}
        title={`Launch ${engine?.name ?? engineId}?`}
        description="This creates a Job and, if the engine is free, immediately calls its adapter's launch() — a real subprocess/engine run, not a dry run."
        confirmLabel="Launch"
        variant="primary"
        loading={launching}
        onConfirm={handleConfirmedLaunch}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}


