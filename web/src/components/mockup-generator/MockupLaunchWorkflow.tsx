import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchMockupBackgrounds,
  getJob,
  getJobMetrics,
  getMockupPreviewImageUrl,
  launchMockupBatchJob,
  launchMockupPreviewJob,
  launchMockupPreviewJobFromStaged,
  listJobs,
} from "../../api/client";
import type { Job, JobMetrics, MockupBackground } from "../../api/types";
import { Button } from "../Button";
import { EmptyBlock, Spinner } from "../AsyncState";
import { MockupJobResult } from "./MockupJobResult";
import { Check } from "lucide-react";

const ENGINE_ID = "etsy-mockup-generator";
const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
// The batch Job row is created (JobService.create_job()) synchronously,
// well before the long coordinator.evaluate() call that actually renders
// the batch -- it should appear within ~1-2s. This cap is a generous
// safety net for "the server never even got that far" (e.g. down), not a
// bound on how long a real batch can take -- once found, the Job is
// polled until terminal with no cap of its own.
const DISCOVERY_POLL_MS = 1500;
const DISCOVERY_MAX_ATTEMPTS = 20;
const TERMINAL_POLL_MS = 2000;

type Stage =
  | "select"
  | "uploading"
  | "generating_preview"
  | "waiting_on_approval"
  | "generating_batch"
  | "completed"
  | "failed";

const STAGE_LABEL: Record<Stage, string> = {
  select: "Select ZIP + background",
  uploading: "Uploading ZIP",
  generating_preview: "Generating Preview",
  waiting_on_approval: "Waiting for Approval",
  generating_batch: "Generating Mockups",
  completed: "Completed",
  failed: "Failed",
};

// Five-step workflow shape the page always communicates, regardless of
// exact Stage — see MOCKUP GENERATOR BACKLOG "Clarify two-stage workflow"
// (really five steps end-to-end: preview, review, approve, batch, output).
const WORKFLOW_STEPS = ["Create Preview", "Review", "Approve", "Run Batch", "Review Output"];
const STAGE_STEP_INDEX: Record<Stage, number> = {
  select: 0,
  uploading: 0,
  generating_preview: 0,
  waiting_on_approval: 1,
  generating_batch: 3,
  completed: 4,
  failed: 0,
};

const dropzoneBase: React.CSSProperties = {
  border: "1px dashed var(--border-bright)",
  borderRadius: "var(--radius)",
  padding: "22px 16px",
  textAlign: "center",
  color: "var(--text-dim)",
  fontSize: "var(--text-sm)",
  transition: "border-color 0.15s ease, background 0.15s ease",
};

function friendlyError(err: unknown): string {
  if (err instanceof ApiError && err.status === 422 && err.detail && typeof err.detail === "object") {
    const detail = (err.detail as { detail?: unknown }).detail ?? err.detail;
    if (detail && typeof detail === "object" && "errors" in detail) {
      return (detail as { errors: string[] }).errors.join(" · ");
    }
  }
  return err instanceof Error ? err.message : "Something went wrong";
}

export function MockupLaunchWorkflow() {
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [backgrounds, setBackgrounds] = useState<MockupBackground[] | null>(null);
  const [backgroundsError, setBackgroundsError] = useState<string | null>(null);
  const [selectedBackground, setSelectedBackground] = useState<string | null>(null);
  const [designId, setDesignId] = useState("");

  const [stage, setStage] = useState<Stage>("select");
  const [error, setError] = useState<string | null>(null);
  const [previewJob, setPreviewJob] = useState<Job | null>(null);
  const [batchJob, setBatchJob] = useState<Job | null>(null);
  const [batchMetrics, setBatchMetrics] = useState<JobMetrics | null>(null);
  const [devDetailsOpen, setDevDetailsOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  // Tracks in-flight setInterval timers for the discovery/terminal polling
  // loops started by handleApprove(), so they can be torn down both on a
  // normal terminal transition and on unmount (the operator navigating
  // away mid-batch shouldn't leave a dangling poller calling setState on
  // an unmounted component).
  const activeTimers = useRef<number[]>([]);
  const activeVisibilityCleanups = useRef<Array<() => void>>([]);
  const unmountedRef = useRef(false);

  function clearVisibilityListeners() {
    activeVisibilityCleanups.current.forEach((fn) => fn());
    activeVisibilityCleanups.current = [];
  }

  useEffect(() => {
    // Root cause of an earlier live-testing bug: React StrictMode (dev
    // only) double-invokes this effect on initial mount -- setup, then
    // an immediate synthetic cleanup, then setup again -- specifically
    // to catch effects that don't clean up/reset properly. A
    // cleanup-only effect (nothing here before this fix) sets
    // unmountedRef.current = true during that synthetic cleanup and
    // NEVER resets it, so every unmountedRef.current guard in
    // handleApprove()'s discovery/terminal polling silently short-
    // circuited from the very first render onward -- the batch would
    // complete server-side but the UI would never notice, because the
    // polling callbacks were bailing out before doing anything. Resetting
    // to false on setup (covering both the real mount and StrictMode's
    // synthetic remount) fixes it.
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      clearVisibilityListeners();
      activeTimers.current.forEach((id) => window.clearInterval(id));
      activeTimers.current = [];
    };
  }, []);

  useEffect(() => {
    fetchMockupBackgrounds()
      .then(setBackgrounds)
      .catch((err) => setBackgroundsError(friendlyError(err)));
  }, []);

  const canGeneratePreview = zipFile !== null && selectedBackground !== null && stage === "select";
  const selectedBackgroundName = backgrounds?.find((b) => b.path === selectedBackground)?.name ?? "—";

  async function handleGeneratePreview() {
    if (!canGeneratePreview || !zipFile || !selectedBackground) return;
    setError(null);
    setStage("uploading");
    try {
      setStage("generating_preview");
      const job = await launchMockupPreviewJob(zipFile, selectedBackground, designId || null);
      if (job.status === "succeeded") {
        setPreviewJob(job);
        setStage("waiting_on_approval");
      } else {
        setError(job.error_summary ? JSON.stringify(job.error_summary) : "Preview job did not succeed.");
        setStage("failed");
      }
    } catch (err) {
      setError(friendlyError(err));
      setStage("failed");
    }
  }

  async function handleChooseDifferentBackground(newBackgroundPath: string) {
    if (!previewJob) return;
    const stagedZipPath = typeof previewJob.config.zip_path === "string" ? previewJob.config.zip_path : null;
    if (!stagedZipPath) return;
    setError(null);
    setStage("generating_preview");
    try {
      const job = await launchMockupPreviewJobFromStaged(stagedZipPath, newBackgroundPath, designId || null);
      if (job.status === "succeeded") {
        setPreviewJob(job);
        setSelectedBackground(newBackgroundPath);
        setStage("waiting_on_approval");
      } else {
        setError(job.error_summary ? JSON.stringify(job.error_summary) : "Preview job did not succeed.");
        setStage("failed");
      }
    } catch (err) {
      setError(friendlyError(err));
      setStage("failed");
    }
  }

  function handleCancel() {
    setPreviewJob(null);
    setStage("select");
    setError(null);
  }

  /**
   * Root-cause fix for the false-timeout bug (see client.ts's
   * launchMockupBatchJob docstring for the full diagnosis): this no
   * longer trusts launchMockupBatchJob()'s own promise to tell us
   * whether the batch succeeded. A full batch legitimately runs well
   * past any reasonable client-side fetch timeout, so instead this:
   *   1. Snapshots this engine's current Job ids.
   *   2. Fires the batch-launch POST, but does not gate any UI state on
   *      it resolving -- only a real HTTP error response (not a
   *      client-side timeout/network hiccup) is treated as
   *      authoritative here, and only if the Job-polling side hasn't
   *      already found the real Job (see below).
   *   3. Polls GET /jobs?engine_id=... (the same Job-list endpoint
   *      VideoLaunchWorkflow already uses for this exact "did my launch
   *      create a Job yet" problem) for a new Job with
   *      config.phase === "batch" -- the create_job() call happens
   *      synchronously, seconds before the actual render, so this
   *      resolves almost immediately regardless of how long the batch
   *      itself takes.
   *   4. Once found, polls that specific Job (GET /jobs/{id} -- the same
   *      persisted-Job architecture JobDetail already polls) until it
   *      reaches a terminal status, then renders the real outcome.
   * This means the engine page stays correct even if the original POST
   * times out client-side, the tab is backgrounded, or the network
   * blips mid-request -- the persisted Job is the single source of
   * truth, not any one HTTP response.
   */
  async function handleApprove() {
    if (!previewJob) return;
    clearVisibilityListeners(); // defensive: no stale listener from an earlier run
    setError(null);
    setBatchJob(null);
    setBatchMetrics(null);
    setStage("generating_batch");

    let existingIds: Set<string>;
    try {
      existingIds = new Set((await listJobs({ engine_id: ENGINE_ID })).map((j) => j.id));
    } catch {
      existingIds = new Set();
    }

    let discoveredJobId: string | null = null;
    let discoveryAttempts = 0;
    let discoveryTimer: number | null = null;
    let terminalTimer: number | null = null;

    function clearDiscoveryTimer() {
      if (discoveryTimer !== null) {
        window.clearInterval(discoveryTimer);
        activeTimers.current = activeTimers.current.filter((id) => id !== discoveryTimer);
        discoveryTimer = null;
      }
    }

    function clearTerminalTimer() {
      if (terminalTimer !== null) {
        window.clearInterval(terminalTimer);
        activeTimers.current = activeTimers.current.filter((id) => id !== terminalTimer);
        terminalTimer = null;
      }
    }

    async function runTerminalCheck(jobId: string) {
      if (unmountedRef.current) return;
      try {
        const job = await getJob(jobId);
        if (!TERMINAL.has(job.status)) return;
        clearTerminalTimer();
        clearVisibilityListeners();
        if (unmountedRef.current) return;

        if (job.status === "succeeded") {
          let metrics: JobMetrics | null = null;
          try {
            metrics = await getJobMetrics(jobId);
          } catch {
            /* non-fatal -- result panel just shows "—" for duration */
          }
          if (unmountedRef.current) return;
          setBatchJob(job);
          setBatchMetrics(metrics);
          setStage("completed");
        } else {
          setError(job.error_summary ? JSON.stringify(job.error_summary) : `Batch job ${job.status}.`);
          setStage("failed");
        }
      } catch {
        /* transient read error mid-poll -- try again next tick */
      }
    }

    function startTerminalPoll(jobId: string) {
      terminalTimer = window.setInterval(() => runTerminalCheck(jobId), TERMINAL_POLL_MS);
      activeTimers.current.push(terminalTimer);
    }

    async function runDiscoveryCheck() {
      if (unmountedRef.current || discoveredJobId) return;
      discoveryAttempts += 1;
      if (discoveryAttempts > DISCOVERY_MAX_ATTEMPTS) {
        clearDiscoveryTimer();
        clearVisibilityListeners();
        if (!discoveredJobId && !unmountedRef.current) {
          setError("Could not confirm the batch job was created — check the Jobs list.");
          setStage("failed");
        }
        return;
      }
      try {
        const current = await listJobs({ engine_id: ENGINE_ID });
        const created = current.find((j) => !existingIds.has(j.id) && j.config.phase === "batch");
        if (created) {
          discoveredJobId = created.id;
          clearDiscoveryTimer();
          startTerminalPoll(created.id);
        }
      } catch {
        /* transient — try again next tick */
      }
    }

    discoveryTimer = window.setInterval(runDiscoveryCheck, DISCOVERY_POLL_MS);
    activeTimers.current.push(discoveryTimer);

    // Defense-in-depth against exactly the throttling/suspension real
    // browsers apply to setInterval in backgrounded tabs (see
    // usePolling.ts for the same pattern): if the operator switches away
    // and back mid-batch, don't wait for the next tick -- check
    // immediately the moment this page is visible again. discoveryTimer
    // being non-null vs. terminalTimer non-null tells us which phase
    // we're in without needing separate stage tracking here.
    function onVisibilityChange() {
      if (document.visibilityState !== "visible") return;
      if (discoveryTimer !== null) runDiscoveryCheck();
      else if (terminalTimer !== null && discoveredJobId) runTerminalCheck(discoveredJobId);
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    activeVisibilityCleanups.current.push(() => document.removeEventListener("visibilitychange", onVisibilityChange));

    try {
      await launchMockupBatchJob(previewJob.id);
      // Deliberately not inspecting the resolved Job here -- the
      // discovery/terminal polling above is authoritative for UI state.
    } catch (err) {
      const isClientSideOnly = err instanceof ApiError && (err.kind === "timeout" || err.kind === "network");
      if (!isClientSideOnly && !discoveredJobId && !unmountedRef.current) {
        // A real HTTP error (409/422/502/...) from the route itself --
        // this happens BEFORE any Job would be created, so there is
        // nothing for the discovery poll to ever find. Authoritative.
        clearDiscoveryTimer();
        clearVisibilityListeners();
        setError(friendlyError(err));
        setStage("failed");
      }
      // Otherwise: a client-side timeout/network hiccup, or the
      // discovery poll already found the real Job -- either way, Job
      // polling (not this rejected promise) determines what the
      // operator sees next.
    }
  }

  function handleLaunchAnother() {
    setZipFile(null);
    setSelectedBackground(null);
    setDesignId("");
    setPreviewJob(null);
    setBatchJob(null);
    setBatchMetrics(null);
    setError(null);
    setStage("select");
  }

  const devPayloadPreview = {
    zip_file: zipFile?.name ?? null,
    background_path: selectedBackground,
    design_id: designId || null,
    stage,
    preview_job_id: previewJob?.id ?? null,
    batch_job_id: batchJob?.id ?? null,
  };

  const currentStepIndex = STAGE_STEP_INDEX[stage];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
          {WORKFLOW_STEPS.map((label, i) => {
            const isDone = stage !== "failed" && i < currentStepIndex;
            const isCurrent = stage !== "failed" && i === currentStepIndex;
            return (
              <span
                key={label}
                className="mono"
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: "var(--text-xs)",
                  padding: "3px 8px",
                  // The system's pill token, not a bare 999 -- this was the
                  // only hardcoded radius left outside the scale.
                  borderRadius: "var(--radius-pill)",
                  border: `1px solid ${isCurrent ? "var(--accent)" : "var(--border)"}`,
                  color: isCurrent ? "var(--accent)" : isDone ? "var(--text-secondary)" : "var(--text-dim)",
                  background: isCurrent ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "transparent",
                  transition: "background 200ms var(--ease), border-color 200ms var(--ease), color 200ms var(--ease)",
                }}
              >
                {/* A checked step used to swap its number for a "✓"
                    character, which is both a different typeface and a
                    different width -- so every completed chip visibly
                    reflowed the row. A fixed-width slot holds the position
                    whether it contains a numeral or the icon. */}
                <span
                  aria-hidden
                  style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 11 }}
                >
                  {isDone ? <Check size={11} strokeWidth={2.6} /> : `${i + 1}`}
                </span>
                {label}
              </span>
            );
          })}
        </div>
        <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: "0.05em" }}>
          {STAGE_LABEL[stage]}
        </div>
      </div>

      {stage === "select" && (
        <>
          <div>
            <div className="label" style={{ marginBottom: 8 }}>
              ZIP file
            </div>
            <div
              style={{
                ...dropzoneBase,
                borderColor: dragOver ? "var(--accent)" : "var(--border-bright)",
                background: dragOver ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "transparent",
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                const file = e.dataTransfer.files[0];
                if (file) setZipFile(file);
              }}
            >
              {zipFile ? (
                <div>
                  <div className="mono" style={{ marginBottom: 10 }}>
                    {zipFile.name}
                  </div>
                  <Button type="button" variant="outline" onClick={() => setZipFile(null)}>
                    Remove
                  </Button>
                </div>
              ) : (
                <>
                  <div style={{ marginBottom: 10 }}>Drag and drop a ZIP here, or</div>
                  <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()}>
                    Upload ZIP
                  </Button>
                </>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) setZipFile(file);
                  e.target.value = "";
                }}
              />
            </div>
          </div>

          <div>
            <div className="label" style={{ marginBottom: 8 }}>
              Background
            </div>
            {backgroundsError && <EmptyBlock>{backgroundsError}</EmptyBlock>}
            {!backgrounds && !backgroundsError && <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Loading backgrounds…</div>}
            {backgrounds && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                {backgrounds.map((bg) => {
                  const selected = bg.path === selectedBackground;
                  return (
                    <button
                      key={bg.path}
                      type="button"
                      disabled={!bg.usable}
                      onClick={() => setSelectedBackground(bg.path)}
                      title={bg.usable ? bg.name : bg.reason ?? "Not usable"}
                      style={{
                        width: 120,
                        padding: 8,
                        borderRadius: "var(--radius)",
                        border: `1px solid ${selected ? "var(--accent)" : "var(--border-bright)"}`,
                        background: selected ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "var(--panel)",
                        opacity: bg.usable ? 1 : 0.4,
                        cursor: bg.usable ? "pointer" : "not-allowed",
                        textAlign: "left",
                      }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>{bg.name}</div>
                      {!bg.usable && <div style={{ fontSize: 10, color: "var(--state-failure)", marginTop: 4 }}>{bg.reason}</div>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div>
            <div className="label" style={{ marginBottom: 8 }}>
              Design ID (optional)
            </div>
            <input
              value={designId}
              onChange={(e) => setDesignId(e.target.value)}
              placeholder="Optional reference"
              style={{
                background: "var(--panel-raised)",
                border: "1px solid var(--border-bright)",
                color: "var(--text-primary)",
                borderRadius: "var(--radius)",
                padding: "8px 10px",
                fontSize: 12,
                width: "100%",
                maxWidth: 320,
              }}
            />
          </div>

          <div>
            <Button variant="primary" onClick={handleGeneratePreview} disabled={!canGeneratePreview}>
              Generate Preview
            </Button>
            {!canGeneratePreview && (
              <span style={{ fontSize: 11, color: "var(--text-dim)", marginLeft: 10 }}>
                {!zipFile ? "Select a ZIP" : !selectedBackground ? "Select a background" : ""}
              </span>
            )}
          </div>
        </>
      )}

      {(stage === "uploading" || stage === "generating_preview") && (
        <div style={{ fontSize: "var(--text-sm)", color: "var(--text-dim)" }}>Working…</div>
      )}

      {stage === "generating_batch" && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: "var(--text-sm)", color: "var(--text-dim)" }}>
          <Spinner size={14} />
          <span>
            Generating the full batch — this can take a while for larger ZIPs. This page updates automatically when
            it finishes; no need to navigate away or refresh.
          </span>
        </div>
      )}

      {stage === "completed" && batchJob && (
        <MockupJobResult
          job={batchJob}
          totalExecutionSeconds={batchMetrics?.total_execution_seconds ?? null}
          onLaunchAnother={handleLaunchAnother}
        />
      )}

      {stage === "waiting_on_approval" && previewJob && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontSize: "var(--text-xs)",
              color: "var(--text-dim)",
            }}
          >
            <span>Review the representative preview below.</span>
            <span>
              Background: <span style={{ color: "var(--text-primary)" }}>{selectedBackgroundName}</span>
            </span>
          </div>
          <img
            src={getMockupPreviewImageUrl(previewJob.id)}
            alt="Representative mockup preview"
            style={{
              maxWidth: "100%",
              maxHeight: 380,
              width: "auto",
              objectFit: "contain",
              background: "#000",
              borderRadius: "var(--radius)",
              border: "1px solid var(--border-bright)",
              display: "block",
              margin: "0 auto",
            }}
          />
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
            <Button variant="primary" onClick={handleApprove}>
              Approve and Generate Full Batch
            </Button>
            <Button variant="ghost" onClick={handleCancel}>
              Cancel
            </Button>
          </div>
          {backgrounds && backgrounds.some((b) => b.usable && b.path !== selectedBackground) && (
            <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
              Not this background?{" "}
              <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 6, marginLeft: 4 }}>
                {backgrounds
                  .filter((b) => b.usable && b.path !== selectedBackground)
                  .map((bg) => (
                    <Button key={bg.path} variant="outline" onClick={() => handleChooseDifferentBackground(bg.path)}>
                      Use {bg.name}
                    </Button>
                  ))}
              </span>
            </div>
          )}
        </div>
      )}

      {stage === "failed" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ color: "var(--state-failure)", fontSize: "var(--text-sm)" }}>{error}</div>
          <Button variant="outline" onClick={handleCancel}>
            Start Over
          </Button>
        </div>
      )}

      <details open={devDetailsOpen} onToggle={(e) => setDevDetailsOpen((e.target as HTMLDetailsElement).open)}>
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
        <pre
          className="mono"
          style={{ marginTop: 8, fontSize: 11, color: "var(--text-secondary)", whiteSpace: "pre-wrap", wordBreak: "break-all" }}
        >
          {JSON.stringify(devPayloadPreview, null, 2)}
        </pre>
      </details>
    </div>
  );
}
