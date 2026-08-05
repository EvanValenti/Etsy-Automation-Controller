import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, cancelJob, deleteJob, getJob, getJobEvents, getJobMetrics, getJobProgress, listAiImageJobs, listJobs } from "../api/client";
import { ActivityFeed } from "../components/ActivityFeed";
import { ErrorBlock } from "../components/AsyncState";
import { Activity, Hourglass, Rocket, Timer } from "lucide-react";
import { BackLink } from "../components/BackLink";
import { MetricTile } from "../components/MetricTile";
import { PageHeader } from "../components/PageHeader";
import { Button } from "../components/Button";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Panel } from "../components/Panel";
import { SkeletonFeed, SkeletonLine } from "../components/Skeleton";
import { JobStatusBadge } from "../components/StatusBadges";
import { ImageJobResult } from "../components/image-generator/ImageJobResult";
import { MockupJobResult } from "../components/mockup-generator/MockupJobResult";
import { VideoJobResult } from "../components/video-generator/VideoJobResult";
import { usePolling } from "../hooks/usePolling";
import { buildManifestLookup, groupIdentifier, jobDisplayName } from "../jobPresentation";
import { formatDuration, formatTimestamp } from "../status";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const VIDEO_GENERATOR_ENGINE_ID = "etsy-video-generator";
const MOCKUP_GENERATOR_ENGINE_ID = "etsy-mockup-generator";
const IMAGE_GENERATOR_ENGINE_ID = "etsy-ai-image-generator";

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const job = usePolling(useCallback(() => getJob(jobId!), [jobId]), 3000);
  const events = usePolling(useCallback(() => getJobEvents(jobId!), [jobId]), 4000);
  const progress = usePolling(useCallback(() => getJobProgress(jobId!), [jobId]), 3000);
  const metrics = usePolling(useCallback(() => getJobMetrics(jobId!), [jobId]), 5000);
  // Needed only for delete-confirmation wording: whether this Controller
  // Job record is one of several still referencing the same engine-side
  // job (see infra/cleanup.py's _other_jobs_reference_path) -- deleting a
  // non-last one recycles nothing, deleting the last one does.
  const engineId = job.data?.engine_id;
  const siblingJobs = usePolling(
    useCallback(() => (engineId ? listJobs({ engine_id: engineId }) : Promise.resolve([])), [engineId]),
    15000,
    [engineId],
  );

  // Same readable-name resolution the job tables use, so a job is called
  // the same thing everywhere an operator sees it.
  const aiImageJobs = usePolling(
    useCallback(
      () => (engineId === IMAGE_GENERATOR_ENGINE_ID ? listAiImageJobs().catch(() => []) : Promise.resolve([])),
      [engineId],
    ),
    15000,
    [engineId],
  );
  const displayName = job.data
    ? jobDisplayName(job.data, buildManifestLookup(aiImageJobs.data))
    : { title: null, identifier: "" };

  if (!jobId) return null;

  if (job.error instanceof ApiError && job.error.status === 404) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
        <BackLink to="/jobs" />
        <ErrorBlock error={job.error} />
      </div>
    );
  }

  async function handleCancel() {
    setCancelError(null);
    setCancelling(true);
    try {
      await cancelJob(jobId!);
      setConfirmOpen(false);
      job.refetch();
      events.refetch();
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : "Cancel failed");
      setConfirmOpen(false);
    } finally {
      setCancelling(false);
    }
  }

  async function handleDelete() {
    setDeleteError(null);
    setDeleting(true);
    try {
      await deleteJob(jobId!);
      navigate("/jobs");
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed");
      setDeleteConfirmOpen(false);
    } finally {
      setDeleting(false);
    }
  }

  const isTerminal = job.data ? TERMINAL.has(job.data.status) : false;

  // Deleting a Controller Job never deletes another still-existing Job's
  // output (infra/cleanup.py checks this server-side too) -- but the
  // confirmation copy should say so plainly rather than leave the
  // operator guessing whether "recycles its generated output" means the
  // whole engine-side job or just this one workflow record.
  const identifier = job.data ? groupIdentifier(job.data) : null;
  const siblingCount =
    identifier && siblingJobs.data
      ? siblingJobs.data.filter((j) => j.id !== job.data!.id && groupIdentifier(j) === identifier).length
      : 0;
  const deleteDescription =
    siblingCount > 0
      ? `This deletes only this workflow record (one of ${siblingCount + 1} for "${identifier}") — its files are untouched, since ${siblingCount} other workflow record${siblingCount === 1 ? "" : "s"} still ${siblingCount === 1 ? "references" : "reference"} them. This does NOT touch shared resources, templates, reusable backgrounds, or engine code.`
      : "This removes the job record and moves its generated output (video/mockups/images) to the Windows Recycle Bin — not a permanent delete, and it's recoverable from there. This does NOT touch shared resources, templates, reusable backgrounds, or engine code.";
  const deleteHint =
    siblingCount > 0
      ? `Removes only this workflow record — "${identifier}" and its files stay untouched.`
      : "Removes this job record and recycles its generated output.";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
      <BackLink to="/jobs" />

      {job.loading && !job.hasLoadedOnce && (
        <div>
          <SkeletonLine width={200} height={18} />
          <SkeletonLine width={280} height={12} />
        </div>
      )}
      {job.error && !(job.error instanceof ApiError && job.error.status === 404) && (
        <ErrorBlock error={job.error} onRetry={job.refetch} />
      )}

      {job.data && (
        <>
          {/* Readable name large, identifiers small: the product/store/
              campaign an operator recognizes leads, with the slug and
              the raw job id demoted to supporting detail. */}
          <PageHeader
            title={displayName.title ?? displayName.identifier}
            subtitle={
              <span className="mono" style={{ wordBreak: "break-all", color: "var(--text-dim)" }}>
                {displayName.title ? `${displayName.identifier} · ` : ""}
                {job.data.id}
              </span>
            }
            actions={
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
                <Button
                  variant="danger"
                  onClick={() => setConfirmOpen(true)}
                  disabled={isTerminal}
                  title={isTerminal ? "Job is already in a terminal state" : "Best-effort — 409 if the engine can't cancel"}
                >
                  Cancel Job
                </Button>
                {cancelError && (
                  <span style={{ color: "var(--state-failure)", fontSize: "var(--text-xs)" }}>{cancelError}</span>
                )}
              </div>
            }
          >
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <JobStatusBadge status={job.data.status} />
              {job.data.status === "failed" && job.data.error_summary?.category === "launch_timeout" && (
                <span style={{ color: "var(--state-degraded)", fontSize: "var(--text-sm)" }}>(timed out)</span>
              )}
              <span style={{ color: "var(--text-secondary)", fontSize: "var(--text-base)" }}>{job.data.engine_id}</span>
            </div>
          </PageHeader>

          {job.data.status === "running" && progress.data?.percentage != null && (
            <JobProgressBar percentage={progress.data.percentage} />
          )}

          {job.data.engine_id === VIDEO_GENERATOR_ENGINE_ID && job.data.status === "succeeded" ? (
            <Panel index={0} title="Result" eyebrow="Generated video">
              <VideoJobResult job={job.data} totalExecutionSeconds={metrics.data?.total_execution_seconds ?? null} />
              <DeveloperDetails config={job.data.config} result={job.data.result_summary} error={job.data.error_summary} />
            </Panel>
          ) : job.data.engine_id === MOCKUP_GENERATOR_ENGINE_ID &&
            job.data.status === "succeeded" &&
            job.data.result_summary?.phase === "batch" ? (
            <Panel index={0} title="Result" eyebrow="Generated mockups">
              <MockupJobResult job={job.data} totalExecutionSeconds={metrics.data?.total_execution_seconds ?? null} />
              <DeveloperDetails config={job.data.config} result={job.data.result_summary} error={job.data.error_summary} />
            </Panel>
          ) : job.data.engine_id === IMAGE_GENERATOR_ENGINE_ID && job.data.status === "succeeded" ? (
            <Panel index={0} title="Result" eyebrow="Pipeline state">
              <ImageJobResult job={job.data} />
              <DeveloperDetails config={job.data.config} result={job.data.result_summary} error={job.data.error_summary} />
            </Panel>
          ) : (
            <div className="grid-split-even">
              <Panel index={1} title="Config" eyebrow="Launch parameters">
                <pre
                  className="mono"
                  style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}
                >
                  {JSON.stringify(job.data.config, null, 2)}
                </pre>
              </Panel>

              <Panel index={1} title="Result / Error" eyebrow="Terminal state">
                {job.data.error_summary ? (
                  <pre className="mono" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--state-failure)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                    {JSON.stringify(job.data.error_summary, null, 2)}
                  </pre>
                ) : job.data.result_summary ? (
                  <pre className="mono" style={{ margin: 0, fontSize: "var(--text-xs)", color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                    {JSON.stringify(job.data.result_summary, null, 2)}
                  </pre>
                ) : (
                  <span style={{ color: "var(--text-dim)", fontSize: 12 }}>No result yet.</span>
                )}
              </Panel>
            </div>
          )}

          <div className="grid-stats">
            <MetricTile index={0} icon={Activity} label="Phase" value={progress.data?.phase ?? "—"} />
            <MetricTile index={1} icon={Hourglass} label="Queue wait" value={formatDuration(metrics.data?.queue_wait_seconds ?? null)} />
            <MetricTile index={2} icon={Rocket} label="Launch duration" value={formatDuration(metrics.data?.launch_duration_seconds ?? null)} />
            <MetricTile index={3} icon={Timer} label="Total execution" value={formatDuration(metrics.data?.total_execution_seconds ?? null)} />
          </div>

          <div style={{ display: "flex", gap: 20, flexWrap: "wrap", fontSize: "var(--text-xs)", color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
            <span>Created {formatTimestamp(job.data.created_at)}</span>
            <span>Updated {formatTimestamp(job.data.updated_at)}</span>
          </div>

          <Panel
            index={5}
            title="Event Timeline"
            eyebrow="History"
            lastUpdated={events.lastUpdated}
            refreshing={events.refreshing}
            onRefresh={events.refetch}
          >
            {events.loading && !events.hasLoadedOnce && <SkeletonFeed />}
            {events.error && <ErrorBlock error={events.error} onRetry={events.refetch} />}
            {events.data && <ActivityFeed events={events.data} />}
          </Panel>

          {isTerminal && (
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: 10,
                paddingTop: 14,
                borderTop: "1px solid var(--border)",
              }}
            >
              <span style={{ fontSize: 11, color: "var(--text-dim)" }}>{deleteHint}</span>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
                <Button variant="danger" onClick={() => setDeleteConfirmOpen(true)}>
                  Delete Job
                </Button>
                {deleteError && <span style={{ color: "var(--state-failure)", fontSize: 11 }}>{deleteError}</span>}
              </div>
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="Cancel this job?"
        description="This is best-effort — the engine's cancel() may not support every stage. Completed jobs can never be cancelled."
        confirmLabel="Cancel Job"
        variant="danger"
        loading={cancelling}
        onConfirm={handleCancel}
        onCancel={() => setConfirmOpen(false)}
      />

      <ConfirmDialog
        open={deleteConfirmOpen}
        title="Delete this job?"
        description={deleteDescription}
        confirmLabel="Delete Job"
        variant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setDeleteConfirmOpen(false)}
      />
    </div>
  );
}

// The engine reports a `percentage` next to its phase, but only a running
// job's is meaningful -- so the bar arrives and leaves with the run rather
// than sitting frozen at whatever the last poll saw.
function JobProgressBar({ percentage }: { percentage: number }) {
  const pct = Math.min(100, Math.max(0, percentage));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div className="label">Progress</div>
        <span className="mono" style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)" }}>
          {Math.round(pct)}%
        </span>
      </div>
      <div
        className="progress-track"
        style={{ height: 6 }}
        role="progressbar"
        aria-label="Job progress"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-fill" style={{ width: `${pct}%`, background: "var(--state-running)" }} />
      </div>
    </div>
  );
}

function DeveloperDetails({
  config,
  result,
  error,
}: {
  config: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
}) {
  return (
    <details style={{ marginTop: 16 }}>
      <summary
        style={{
          cursor: "pointer",
          fontSize: 13,
          fontWeight: 600,
          color: "var(--text-secondary)",
        }}
      >
        Developer Details
      </summary>
      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 10 }}>
        <div>
          <div className="label">Config</div>
          <pre className="mono" style={{ margin: "4px 0 0", fontSize: 11, color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {JSON.stringify(config, null, 2)}
          </pre>
        </div>
        {error && (
          <div>
            <div className="label">Error</div>
            <pre className="mono" style={{ margin: "4px 0 0", fontSize: 11, color: "var(--state-failure)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {JSON.stringify(error, null, 2)}
            </pre>
          </div>
        )}
        {result && (
          <div>
            <div className="label">Result</div>
            <pre className="mono" style={{ margin: "4px 0 0", fontSize: 11, color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
              {JSON.stringify(result, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </details>
  );
}

