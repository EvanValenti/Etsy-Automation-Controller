import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  advanceAiImageJob,
  createAiImageJob,
  getAiImageConceptProvider,
  getAiImageJobStatus,
  getJob,
  listAiImageJobs,
  listAiImageStores,
  listJobs,
} from "../../api/client";
import type { AiImageConceptProviderInfo, AiImageJobManifest, AiImageStore } from "../../api/types";
import { Button } from "../Button";
import { EmptyBlock, Spinner } from "../AsyncState";
import { ConceptReviewPanel } from "./ConceptReviewPanel";
import { ImageReviewPanel } from "./ImageReviewPanel";
import { ManualModePanel } from "./ManualModePanel";
import { PipelineChecklist } from "./PipelineChecklist";
import { PromptReviewPanel } from "./PromptReviewPanel";
import { ReferenceImagesPanel } from "./ReferenceImagesPanel";
import { describeAdvanceOutcome, describeNextStep, workflowView } from "./workflowStatus";

const ENGINE_ID = "etsy-ai-image-generator";
const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const DISCOVERY_POLL_MS = 1500;
const DISCOVERY_MAX_ATTEMPTS = 20;
const TERMINAL_POLL_MS = 2000;

// Mirrors Etsy-AI-Image-Generator/src/job_config.py's PRODUCT_TYPE_PRESETS
// -- a friendlier picker over the same free-text field the engine already
// accepts, not a new engine capability. "Other" always falls back to free
// text, and the engine never branches on which one was chosen.
const PRODUCT_TYPE_PRESETS = ["T-Shirt", "Sweatshirt", "Hoodie", "Mug", "Tote Bag", "Other"];

// Mirrors Etsy-AI-Image-Generator/src/job_config.py's CONCEPT_DEFAULTS --
// the engine's own default concept_counts, shown pre-filled so an operator
// only needs to change what they actually want to change.
const CONCEPT_COUNT_DEFAULTS = {
  ai_product_mockup_concepts_to_propose: 10,
  ai_product_mockup_concepts_to_select: 5,
  lifestyle_mockup_concepts_to_propose: 10,
  lifestyle_mockup_concepts_to_select: 5,
};

/** How many concepts Claude is asked to propose per category. Presented
 * as an explicit choice up front rather than silently defaulting to 10:
 * the count directly drives how much Claude API usage the Generate
 * Concepts step costs, so an operator should decide it knowingly. */
const CONCEPT_COUNT_PRESETS = [4, 6, 8, 10] as const;
const RECOMMENDED_CONCEPT_COUNT = 10;
const MIN_CONCEPT_COUNT = 1;
const MAX_CONCEPT_COUNT = 24;

/** Roughly how many concepts get proposed in total (both categories) and
 * what that means for one Generate Concepts call. Deliberately expressed
 * as a magnitude, not a dollar figure -- the Controller has no pricing
 * data and must not invent one. */
function describeClaudeUsage(perCategory: number): string {
  const total = perCategory * 2;
  return `${total} concepts total (${perCategory} AI product + ${perCategory} lifestyle) in one Claude request.`;
}

type Mode = "browse" | "create";

function friendlyError(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}

export function ImageLaunchWorkflow() {
  const [mode, setMode] = useState<Mode>("browse");

  const [existingJobs, setExistingJobs] = useState<AiImageJobManifest[] | null>(null);
  const [jobsError, setJobsError] = useState<string | null>(null);
  const [selectedJobName, setSelectedJobName] = useState<string | null>(null);
  const [selectedManifest, setSelectedManifest] = useState<AiImageJobManifest | null>(null);

  const [stores, setStores] = useState<AiImageStore[] | null>(null);
  const [storesError, setStoresError] = useState<string | null>(null);
  const [productName, setProductName] = useState("");
  const [storeId, setStoreId] = useState("");
  const [campaignId, setCampaignId] = useState("");
  const [productType, setProductType] = useState("T-Shirt");
  const [customProductType, setCustomProductType] = useState("");
  const [productColor, setProductColor] = useState("");
  const [creativeNotes, setCreativeNotes] = useState("");
  // One operator-facing number ("how many concepts?") expanded into the
  // engine's four-field concept_counts shape at create time -- the engine
  // schema is unchanged; only how the choice is asked for changed.
  const [conceptsPerCategory, setConceptsPerCategory] = useState<number>(RECOMMENDED_CONCEPT_COUNT);
  const [customCountActive, setCustomCountActive] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [advancing, setAdvancing] = useState(false);
  const [advanceMessage, setAdvanceMessage] = useState<string | null>(null);
  const [advanceError, setAdvanceError] = useState<string | null>(null);
  const [conceptProvider, setConceptProvider] = useState<AiImageConceptProviderInfo | null>(null);
  // Reference Images defaults open while it's still actionable (before
  // Build Prompts has run for any concept) and collapses afterward to
  // reduce clutter -- but only as a DEFAULT at selection time, never
  // re-asserted mid-session, so toggling it never fights the operator.
  const [referenceImagesOpen, setReferenceImagesOpen] = useState(true);
  // Manual Workflow is an alternate path, not the primary flow -- starts
  // collapsed so it doesn't compete with Automatic Workflow for attention.
  const [manualWorkflowOpen, setManualWorkflowOpen] = useState(false);

  const activeTimers = useRef<number[]>([]);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      activeTimers.current.forEach((id) => window.clearInterval(id));
      activeTimers.current = [];
    };
  }, []);

  function refreshJobList() {
    listAiImageJobs()
      .then(setExistingJobs)
      .catch((err) => setJobsError(friendlyError(err)));
  }

  useEffect(() => {
    refreshJobList();
  }, []);

  useEffect(() => {
    if (mode !== "create" || stores) return;
    listAiImageStores()
      .then(setStores)
      .catch((err) => setStoresError(friendlyError(err)));
  }, [mode, stores]);

  /**
   * Re-read the selected job's workflow state, and nothing else.
   *
   * Distinct from selectJob() on purpose: selectJob() also resets view state
   * (clears messages, collapses the Manual Workflow panel, recomputes the
   * Reference Images default) because it means "the operator moved to a
   * different job". Using it as a plain refresh made the panel the operator
   * was working in snap shut underneath them and threw away the success
   * message they had just earned. This is the refresh for "the same job's
   * state changed": the manifest is re-read, the operator's place is left
   * alone.
   */
  function refreshSelectedJobState(jobName: string) {
    getAiImageJobStatus(jobName)
      .then(setSelectedManifest)
      .catch(() => {});
    refreshJobList();
  }

  function selectJob(jobName: string) {
    setSelectedJobName(jobName);
    setAdvanceError(null);
    setAdvanceMessage(null);
    setConceptProvider(null);
    setManualWorkflowOpen(false);
    getAiImageJobStatus(jobName)
      .then((manifest) => {
        setSelectedManifest(manifest);
        setReferenceImagesOpen(!manifest.pipeline_status.prompt_build_complete);
      })
      .catch((err) => setAdvanceError(friendlyError(err)));
    // Cheap config read, not a network/provider call -- lets the button
    // below warn before the operator clicks it, rather than only after a
    // failed attempt.
    getAiImageConceptProvider(jobName)
      .then(setConceptProvider)
      .catch(() => setConceptProvider(null));
  }

  const resolvedProductType = productType === "Other" ? customProductType.trim() : productType;

  async function handleCreate() {
    if (!productName.trim() || !storeId || !campaignId || !resolvedProductType) return;
    setCreating(true);
    setCreateError(null);
    try {
      const created = await createAiImageJob({
        product_name: productName.trim(),
        store_id: storeId,
        campaign_id: campaignId,
        product_type: resolvedProductType,
        // "Select" stays at the engine's own default ratio unless the
        // operator asked for fewer concepts than that default, in which
        // case it can't exceed what was proposed.
        concept_counts: {
          ai_product_mockup_concepts_to_propose: conceptsPerCategory,
          ai_product_mockup_concepts_to_select: Math.min(
            CONCEPT_COUNT_DEFAULTS.ai_product_mockup_concepts_to_select,
            conceptsPerCategory,
          ),
          lifestyle_mockup_concepts_to_propose: conceptsPerCategory,
          lifestyle_mockup_concepts_to_select: Math.min(
            CONCEPT_COUNT_DEFAULTS.lifestyle_mockup_concepts_to_select,
            conceptsPerCategory,
          ),
        },
        creative_notes: creativeNotes.trim(),
        product_color: productColor.trim(),
      });
      setMode("browse");
      refreshJobList();
      selectJob(created.job_name);
      setProductName("");
      setProductColor("");
      setCreativeNotes("");
      setConceptsPerCategory(RECOMMENDED_CONCEPT_COUNT);
      setCustomCountActive(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setCreateError(err.message);
      } else {
        setCreateError(friendlyError(err));
      }
    } finally {
      setCreating(false);
    }
  }

  /**
   * Same discovery-poll-then-terminal-poll shape as
   * MockupLaunchWorkflow.tsx's handleApprove() -- see that function's
   * extensive comments for the full rationale. advanceAiImageJob()'s own
   * promise is not what determines the outcome here; the persisted Job
   * (found via polling GET /jobs?engine_id=, then followed via GET
   * /jobs/{id}) is.
   */
  function handleAdvance() {
    if (!selectedJobName) return;
    const jobName = selectedJobName;
    setAdvancing(true);
    setAdvanceError(null);
    setAdvanceMessage(null);

    listJobs({ engine_id: ENGINE_ID })
      .then((jobs) => new Set(jobs.map((j) => j.id)))
      .catch(() => new Set<string>())
      .then((existingIds) => {
        let discoveredJobId: string | null = null;
        let discoveryAttempts = 0;

        function finish() {
          setAdvancing(false);
          getAiImageJobStatus(jobName)
            .then(setSelectedManifest)
            .catch(() => {});
          getAiImageConceptProvider(jobName)
            .then(setConceptProvider)
            .catch(() => {});
          refreshJobList();
        }

        function startTerminalPoll(jobId: string) {
          const terminalTimer = window.setInterval(async () => {
            if (unmountedRef.current) return;
            try {
              const job = await getJob(jobId);
              if (!TERMINAL.has(job.status)) return;
              window.clearInterval(terminalTimer);
              activeTimers.current = activeTimers.current.filter((id) => id !== terminalTimer);
              if (unmountedRef.current) return;

              if (job.status === "succeeded") {
                const advance = (
                  job.result_summary as { advance?: { status: string; stage: string | null; detail: string | null } } | null
                )?.advance;
                if (advance && selectedManifest) {
                  const workflow = describeAdvanceOutcome(advance, selectedManifest.pipeline_status);
                  setAdvanceMessage(workflow.label + (workflow.detail ? ` — ${workflow.detail}` : ""));
                } else {
                  setAdvanceMessage("Moved forward.");
                }
              } else {
                setAdvanceError(job.error_summary ? JSON.stringify(job.error_summary) : `Job ${job.status}.`);
              }
              finish();
            } catch {
              /* transient -- try again next tick */
            }
          }, TERMINAL_POLL_MS);
          activeTimers.current.push(terminalTimer);
        }

        const discoveryTimer = window.setInterval(async () => {
          if (unmountedRef.current || discoveredJobId) return;
          discoveryAttempts += 1;
          if (discoveryAttempts > DISCOVERY_MAX_ATTEMPTS) {
            window.clearInterval(discoveryTimer);
            activeTimers.current = activeTimers.current.filter((id) => id !== discoveryTimer);
            if (!discoveredJobId && !unmountedRef.current) {
              setAdvanceError("Could not confirm the advance job was created — check the Jobs list.");
              finish();
            }
            return;
          }
          try {
            const current = await listJobs({ engine_id: ENGINE_ID });
            const created = current.find((j) => !existingIds.has(j.id) && j.config.job_name === jobName);
            if (created) {
              discoveredJobId = created.id;
              window.clearInterval(discoveryTimer);
              activeTimers.current = activeTimers.current.filter((id) => id !== discoveryTimer);
              startTerminalPoll(created.id);
            }
          } catch {
            /* transient — try again next tick */
          }
        }, DISCOVERY_POLL_MS);
        activeTimers.current.push(discoveryTimer);

        advanceAiImageJob(jobName).catch((err) => {
          const isClientSideOnly = err instanceof ApiError && (err.kind === "timeout" || err.kind === "network");
          if (!isClientSideOnly && !discoveredJobId && !unmountedRef.current) {
            window.clearInterval(discoveryTimer);
            activeTimers.current = activeTimers.current.filter((id) => id !== discoveryTimer);
            setAdvanceError(friendlyError(err));
            finish();
          }
        });
      });
  }

  // The one derivation every workflow section keys off: which review is
  // being asked for, what can run, and what to tell the operator. See
  // workflowStatus.workflowView().
  const view = selectedManifest
    ? workflowView(selectedManifest.next_step, selectedManifest.pipeline_status)
    : { gate: null, advance: null, complete: false, headline: "" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <Button variant={mode === "browse" ? "primary" : "outline"} onClick={() => setMode("browse")}>
          Existing Jobs
        </Button>
        <Button variant={mode === "create" ? "primary" : "outline"} onClick={() => setMode("create")}>
          Create New Job
        </Button>
      </div>

      {mode === "browse" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {jobsError && <EmptyBlock>{jobsError}</EmptyBlock>}
          {!existingJobs && !jobsError && <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Loading jobs…</div>}
          {existingJobs && existingJobs.length === 0 && <EmptyBlock>No engine-side jobs exist yet — create one.</EmptyBlock>}
          {existingJobs && existingJobs.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {existingJobs.map((job) => {
                const selected = job.job_name === selectedJobName;
                return (
                  <button
                    key={job.job_name}
                    type="button"
                    onClick={() => selectJob(job.job_name)}
                    style={{
                      padding: "8px 12px",
                      borderRadius: "var(--radius)",
                      border: `1px solid ${selected ? "var(--accent)" : "var(--border-bright)"}`,
                      background: selected ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "var(--panel)",
                      cursor: "pointer",
                      textAlign: "left",
                      minWidth: 160,
                    }}
                  >
                    <div style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--text-primary)" }}>{job.job_name}</div>
                    <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>
                      {job.status === "unreadable" ? "Unreadable" : describeNextStep(job.next_step)}
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {selectedJobName && selectedManifest && selectedManifest.status !== "unreadable" && (
            <div
              style={{
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              <div>
                <div className="label">Selected Job</div>
                <div className="mono" style={{ fontSize: 13, marginTop: 2 }}>
                  {selectedManifest.job_name}
                </div>
                <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", marginTop: 2 }}>
                  {selectedManifest.product.product_name} — {selectedManifest.store.store_name} /{" "}
                  {selectedManifest.campaign.campaign_name}
                </div>
              </div>

              {/* Orientation first: where is this job, what's next -- before
                  any action section, matching the same principle applied to
                  Job Detail in the Jobs pass. */}
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                <PipelineChecklist status={selectedManifest.pipeline_status} />
                {/* One statement of what to do next, from the same
                    workflowView() that decides which section renders --
                    so the guidance and the UI below it can't disagree. */}
                <div style={{ marginTop: 12, fontSize: "var(--text-base)", color: "var(--text-primary)" }}>{view.headline}</div>
              </div>

              {selectedManifest.pipeline_status.ready_for_controller ? (
                <div
                  style={{
                    border: "1px solid var(--state-success)",
                    background: "color-mix(in srgb, var(--state-success) 8%, transparent)",
                    borderRadius: "var(--radius)",
                    padding: "12px 14px",
                    fontSize: "var(--text-sm)",
                    color: "var(--state-success)",
                  }}
                >
                  ✓ Workflow complete for this job — every stage has finished. See Listing Assets to assemble this
                  job's approved images into a listing folder.
                </div>
              ) : (
                <>
                  {/* Exactly ONE of these renders: the review the operator
                      is being asked for, or the action that can run. Which
                      it is comes from workflowView() alone, so a completed
                      stage can never sit on screen next to a pending one.
                      Finished stages are represented by the checklist
                      above, which is the completion indicator. */}
                  {view.gate === "concept_review" && (
                    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 16 }}>
                      <div style={{ fontSize: 14, fontWeight: 700 }}>Concept Review</div>
                      <div>
                        <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", marginBottom: 8 }}>AI Product Mockup</div>
                        <ConceptReviewPanel
                          jobName={selectedManifest.job_name}
                          category="ai_product_mockup"
                          categoryLabel="AI Product Mockup"
                          refreshSignal={JSON.stringify(selectedManifest.pipeline_status)}
                          onDecision={() => refreshSelectedJobState(selectedManifest.job_name)}
                        />
                      </div>
                      <div>
                        <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", marginBottom: 8 }}>Lifestyle</div>
                        <ConceptReviewPanel
                          jobName={selectedManifest.job_name}
                          category="lifestyle_mockup"
                          categoryLabel="Lifestyle"
                          refreshSignal={JSON.stringify(selectedManifest.pipeline_status)}
                          onDecision={() => refreshSelectedJobState(selectedManifest.job_name)}
                        />
                      </div>
                    </div>
                  )}

                  {view.gate === "prompt_review" && (
                    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Prompt Review</div>
                      <PromptReviewPanel
                        jobName={selectedManifest.job_name}
                        reviewComplete={selectedManifest.pipeline_status.prompt_review_complete}
                        onConfirmed={(manifest) => {
                          setSelectedManifest(manifest);
                          refreshJobList();
                        }}
                      />
                    </div>
                  )}

                  {/* Generated images are reviewed here, inline -- an
                      operator should never have to go find this job in Job
                      History to see what it made. */}
                  {view.gate === "image_review" && (
                    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Image Review</div>
                      <ImageReviewPanel
                        jobName={selectedManifest.job_name}
                        refreshSignal={JSON.stringify(selectedManifest.pipeline_status)}
                        onReviewed={() => {
                          getAiImageJobStatus(selectedManifest.job_name).then(setSelectedManifest).catch(() => {});
                          refreshJobList();
                        }}
                      />
                    </div>
                  )}

                  {/* Only rendered when there IS something to run. At a
                      review gate this section doesn't exist at all, rather
                      than showing a stale "Continue" next to the review
                      that's actually blocking. */}
                  {view.advance && (
                    <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                      <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Next Step</div>
                      {!selectedManifest.pipeline_status.concept_generation_complete &&
                        selectedManifest.pipeline_status.concept_planning_complete &&
                        conceptProvider &&
                        !conceptProvider.automatic_provider_configured && (
                          <div style={{ fontSize: 13, color: "var(--state-degraded)", marginBottom: 8 }}>
                            Automatic concept generation isn't configured (no Claude API key set) — this will fail if
                            you continue. Use Manual Workflow below instead, or configure the API key first.
                          </div>
                        )}
                      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                        <Button variant="primary" onClick={handleAdvance} disabled={advancing} loading={advancing}>
                          {advancing ? view.advance.runningLabel : view.advance.label}
                        </Button>
                        {advancing && view.advance.provider !== "local" && (
                          <span style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
                            <Spinner size={12} /> {view.advance.runningLabel}
                          </span>
                        )}
                      </div>
                      {view.advance.notes.length > 0 && (
                        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 8, display: "flex", flexDirection: "column", gap: 2 }}>
                          {view.advance.notes.map((note) => (
                            <span key={note}>{note}</span>
                          ))}
                        </div>
                      )}
                      {advanceMessage && <div style={{ fontSize: 13, color: "var(--state-success)", marginTop: 6 }}>{advanceMessage}</div>}
                      {advanceError && <div style={{ fontSize: 13, color: "var(--state-failure)", marginTop: 6 }}>{advanceError}</div>}
                    </div>
                  )}
                </>
              )}

              {/* Supporting/reference material below the active workflow --
                  collapses once it's no longer actionable (see
                  referenceImagesOpen's default in selectJob()). */}
              <details
                open={referenceImagesOpen}
                onToggle={(e) => setReferenceImagesOpen((e.target as HTMLDetailsElement).open)}
                style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}
              >
                <summary className="label" style={{ cursor: "pointer", marginBottom: 8 }}>
                  Reference Images
                </summary>
                <div style={{ marginTop: 8 }}>
                  <ReferenceImagesPanel jobName={selectedManifest.job_name} />
                </div>
              </details>

              <details
                open={manualWorkflowOpen}
                onToggle={(e) => setManualWorkflowOpen((e.target as HTMLDetailsElement).open)}
                style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}
              >
                <summary className="label" style={{ cursor: "pointer", marginBottom: 4 }}>
                  Manual Workflow (alternate path)
                </summary>
                <div style={{ fontSize: 11, color: "var(--text-dim)", margin: "8px 0" }}>
                  Do this step yourself outside the Controller (e.g. with an external LLM), then bring the result back
                  in.
                </div>
                <ManualModePanel
                  jobName={selectedManifest.job_name}
                  refreshSignal={JSON.stringify(selectedManifest.pipeline_status)}
                  onChanged={() => refreshSelectedJobState(selectedManifest.job_name)}
                />
              </details>
            </div>
          )}
        </div>
      )}

      {mode === "create" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {storesError && <EmptyBlock>{storesError}</EmptyBlock>}
          {!stores && !storesError && <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Loading stores…</div>}

          <div>
            <div className="label" style={{ marginBottom: 6 }}>
              Product Name
            </div>
            <input
              value={productName}
              onChange={(e) => setProductName(e.target.value)}
              placeholder="e.g. Cozy Cabin Sweatshirt"
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

          {stores && (
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <div>
                <div className="label" style={{ marginBottom: 6 }}>
                  Store
                </div>
                <select
                  value={storeId}
                  onChange={(e) => {
                    setStoreId(e.target.value);
                    setCampaignId("");
                  }}
                  style={{
                    background: "var(--panel-raised)",
                    border: "1px solid var(--border-bright)",
                    color: "var(--text-primary)",
                    borderRadius: "var(--radius)",
                    padding: "7px 10px",
                    fontSize: 12,
                  }}
                >
                  <option value="">— select —</option>
                  {stores.map((s) => (
                    <option key={s.store_id} value={s.store_id}>
                      {s.store_name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <div className="label" style={{ marginBottom: 6 }}>
                  Campaign
                </div>
                <select
                  value={campaignId}
                  onChange={(e) => setCampaignId(e.target.value)}
                  disabled={!storeId}
                  style={{
                    background: "var(--panel-raised)",
                    border: "1px solid var(--border-bright)",
                    color: "var(--text-primary)",
                    borderRadius: "var(--radius)",
                    padding: "7px 10px",
                    fontSize: 12,
                  }}
                >
                  <option value="">— select —</option>
                  {stores
                    .find((s) => s.store_id === storeId)
                    ?.campaigns.map((c) => (
                      <option key={c.campaign_id} value={c.campaign_id}>
                        {c.campaign_name}
                      </option>
                    ))}
                </select>
              </div>

              <div>
                <div className="label" style={{ marginBottom: 6 }}>
                  Product Type
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <select
                    value={productType}
                    onChange={(e) => setProductType(e.target.value)}
                    style={{
                      background: "var(--panel-raised)",
                      border: "1px solid var(--border-bright)",
                      color: "var(--text-primary)",
                      borderRadius: "var(--radius)",
                      padding: "7px 10px",
                      fontSize: 12,
                    }}
                  >
                    {PRODUCT_TYPE_PRESETS.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  {productType === "Other" && (
                    <input
                      value={customProductType}
                      onChange={(e) => setCustomProductType(e.target.value)}
                      placeholder="e.g. Phone Case"
                      style={{
                        background: "var(--panel-raised)",
                        border: "1px solid var(--border-bright)",
                        color: "var(--text-primary)",
                        borderRadius: "var(--radius)",
                        padding: "7px 10px",
                        fontSize: 12,
                        width: 160,
                      }}
                    />
                  )}
                </div>
              </div>

              <div>
                <div className="label" style={{ marginBottom: 6 }}>
                  Product Color (optional)
                </div>
                <input
                  value={productColor}
                  onChange={(e) => setProductColor(e.target.value)}
                  placeholder="e.g. Heather Grey"
                  style={{
                    background: "var(--panel-raised)",
                    border: "1px solid var(--border-bright)",
                    color: "var(--text-primary)",
                    borderRadius: "var(--radius)",
                    padding: "7px 10px",
                    fontSize: 12,
                    width: 160,
                  }}
                />
              </div>
            </div>
          )}

          {stores && (
            <div>
              <div className="label" style={{ marginBottom: 6 }}>
                Creative Notes (optional)
              </div>
              <textarea
                value={creativeNotes}
                onChange={(e) => setCreativeNotes(e.target.value)}
                placeholder="Anything the concept generator should keep in mind for this product"
                rows={3}
                style={{
                  background: "var(--panel-raised)",
                  border: "1px solid var(--border-bright)",
                  color: "var(--text-primary)",
                  borderRadius: "var(--radius)",
                  padding: 10,
                  fontSize: 12,
                  width: "100%",
                  maxWidth: 480,
                  resize: "vertical",
                }}
              />
            </div>
          )}

          {stores && (
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Concepts to generate</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {CONCEPT_COUNT_PRESETS.map((count) => {
                  const selected = !customCountActive && conceptsPerCategory === count;
                  return (
                    <button
                      key={count}
                      type="button"
                      onClick={() => {
                        setCustomCountActive(false);
                        setConceptsPerCategory(count);
                      }}
                      style={presetButtonStyle(selected)}
                    >
                      {count} Concepts{count === RECOMMENDED_CONCEPT_COUNT ? " (Recommended)" : ""}
                    </button>
                  );
                })}
                <button type="button" onClick={() => setCustomCountActive(true)} style={presetButtonStyle(customCountActive)}>
                  Custom
                </button>
                {customCountActive && (
                  <input
                    type="number"
                    min={MIN_CONCEPT_COUNT}
                    max={MAX_CONCEPT_COUNT}
                    value={conceptsPerCategory}
                    onChange={(e) => {
                      const raw = Number(e.target.value);
                      if (Number.isNaN(raw)) return;
                      setConceptsPerCategory(Math.min(MAX_CONCEPT_COUNT, Math.max(MIN_CONCEPT_COUNT, Math.round(raw))));
                    }}
                    aria-label="Custom concept count per category"
                    style={{
                      width: 90,
                      background: "var(--panel-raised)",
                      border: "1px solid var(--border-bright)",
                      color: "var(--text-primary)",
                      borderRadius: "var(--radius)",
                      padding: "8px 10px",
                      fontSize: "var(--text-base)",
                    }}
                  />
                )}
              </div>
              {/* Selected quantity and what it costs, stated before the
                  job is created rather than discovered at Generate time. */}
              <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 10, display: "flex", flexDirection: "column", gap: 2 }}>
                <span>
                  <strong style={{ color: "var(--text-primary)" }}>{conceptsPerCategory} concepts</strong> per category.
                </span>
                <span>Estimated Claude usage: {describeClaudeUsage(conceptsPerCategory)}</span>
              </div>
            </div>
          )}

          <div>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={!productName.trim() || !storeId || !campaignId || !resolvedProductType || creating}
              loading={creating}
            >
              Create Job
            </Button>
            {!creating && (!productName.trim() || !storeId || !campaignId || !resolvedProductType) && (
              <span style={{ fontSize: 11, color: "var(--text-dim)", marginLeft: 10 }}>
                {!productName.trim()
                  ? "Enter a product name"
                  : !storeId
                    ? "Select a store"
                    : !campaignId
                      ? "Select a campaign"
                      : "Select a product type"}
              </span>
            )}
            {createError && <div style={{ fontSize: "var(--text-xs)", color: "var(--state-failure)", marginTop: 8 }}>{createError}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function presetButtonStyle(selected: boolean): React.CSSProperties {
  return {
    padding: "9px 14px",
    borderRadius: "var(--radius)",
    border: `1px solid ${selected ? "var(--accent)" : "var(--border-bright)"}`,
    background: selected ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "var(--panel-raised)",
    color: selected ? "var(--accent)" : "var(--text-secondary)",
    fontSize: "var(--text-base)",
    fontWeight: selected ? 600 : 400,
    cursor: "pointer",
  };
}
