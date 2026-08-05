import { useState } from "react";
import { getAiImageJobStatus, openAiImageOutputFolder, type OpenTarget } from "../../api/client";
import type { AiImageJobManifest, Job } from "../../api/types";
import { Button } from "../Button";
import { ConceptReviewPanel } from "./ConceptReviewPanel";
import { ImageReviewPanel } from "./ImageReviewPanel";
import { PipelineChecklist } from "./PipelineChecklist";
import { PromptReviewPanel } from "./PromptReviewPanel";
import { ReferenceImagesPanel } from "./ReferenceImagesPanel";
import { describeAdvanceOutcome, TONE_COLOR, workflowView } from "./workflowStatus";

interface Props {
  job: Job;
}

/** Result view for an etsy-ai-image-generator "advance" Job -- presents
 * the engine's own job_status (pipeline_status, counts, next_step)
 * verbatim, the way PipelineChecklist already does for
 * ImageLaunchWorkflow, plus the persistent Reference Images panel and (once
 * concepts exist) Concept Review -- both thin views over the engine's own
 * concept_review.py/reference_images.py, not a reimplementation. Still
 * does not render prompts/generated images -- that stays out of scope
 * here, same as before.
 */
export function ImageJobResult({ job }: Props) {
  const [folderMessage, setFolderMessage] = useState<string | null>(null);
  const [folderError, setFolderError] = useState<string | null>(null);
  // Overrides the static job.result_summary snapshot whenever a review
  // performed from THIS view changes the job's stage, so the checklist and
  // next-step reflect it immediately rather than looking stale until the
  // page is reloaded.
  //
  // job.result_summary.job_status is frozen at the moment the advance Job
  // ran, so on its own it can never show anything the operator does
  // afterwards. Prompt Review had this override from the start; Concept
  // Review did not, which is why approving every concept here left
  // "Concepts Reviewed" unticked with no next action -- the engine had
  // already recorded concept_review_complete.
  const [liveStatus, setLiveStatus] = useState<AiImageJobManifest | null>(null);

  function refreshStatus(jobName: string) {
    getAiImageJobStatus(jobName)
      .then(setLiveStatus)
      .catch(() => {});
  }

  const summary = job.result_summary as { advance?: { status: string; stage: string | null; detail: string | null }; job_status?: AiImageJobManifest } | null;
  const advance = summary?.advance;
  const status = liveStatus ?? summary?.job_status;

  async function handleOpenFolder(target: OpenTarget) {
    if (!status) return;
    setFolderError(null);
    setFolderMessage(null);
    try {
      const result = await openAiImageOutputFolder(status.job_name, target);
      setFolderMessage(`Opened ${result.opened}`);
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : "Could not open folder");
    }
  }

  if (!status) {
    return <span style={{ color: "var(--text-dim)", fontSize: 12 }}>No result yet.</span>;
  }

  const view = workflowView(status.next_step, status.pipeline_status);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <div className="label">Engine Job</div>
        <div className="mono" style={{ fontSize: 13, marginTop: 2 }}>
          {status.job_name}
        </div>
        <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", marginTop: 2 }}>
          {status.product.product_name} — {status.store.store_name} / {status.campaign.campaign_name}
        </div>
      </div>

      {(() => {
        const workflow = describeAdvanceOutcome(advance, status.pipeline_status);
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontSize: 11, color: "var(--text-dim)" }}>
              The badge above reflects only this one Controller request. Actual job progress:
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, color: TONE_COLOR[workflow.tone] }}>{workflow.label}</div>
            {workflow.detail && <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>{workflow.detail}</div>}
          </div>
        );
      })()}

      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
        <div className="label" style={{ marginBottom: 8 }}>
          Reference Images
        </div>
        <ReferenceImagesPanel jobName={status.job_name} />
      </div>

      <PipelineChecklist status={status.pipeline_status} />

      {/* Same single-source-of-truth rule as the launch workflow: show
          the one review the job is actually waiting on, never a stack of
          finished ones alongside it. */}
      <div>
        <div className="label">Next Step</div>
        <div style={{ fontSize: "var(--text-base)", marginTop: 2 }}>{view.headline}</div>
      </div>

      {view.gate === "concept_review" && (
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, display: "flex", flexDirection: "column", gap: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>Concept Review</div>
          <div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>AI Product Mockup</div>
            <ConceptReviewPanel
              jobName={status.job_name}
              category="ai_product_mockup"
              categoryLabel="AI Product Mockup"
              refreshSignal={JSON.stringify(status.pipeline_status)}
              onDecision={() => refreshStatus(status.job_name)}
            />
          </div>
          <div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>Lifestyle</div>
            <ConceptReviewPanel
              jobName={status.job_name}
              category="lifestyle_mockup"
              categoryLabel="Lifestyle"
              refreshSignal={JSON.stringify(status.pipeline_status)}
              onDecision={() => refreshStatus(status.job_name)}
            />
          </div>
        </div>
      )}

      {view.gate === "prompt_review" && (
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Prompt Review</div>
          <PromptReviewPanel
            jobName={status.job_name}
            reviewComplete={status.pipeline_status.prompt_review_complete}
            onConfirmed={setLiveStatus}
          />
        </div>
      )}

      {view.gate === "image_review" && (
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Image Review</div>
          <ImageReviewPanel
            jobName={status.job_name}
            refreshSignal={JSON.stringify(status.pipeline_status)}
          />
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {/* Two destinations, one endpoint: "Open Images" lands on the
            actual PNGs, "Open Job Files" on the whole job package. */}
        <Button variant="outline" onClick={() => handleOpenFolder("images")}>
          Open Images
        </Button>
        <Button variant="outline" onClick={() => handleOpenFolder("job_files")}>
          Open Job Files
        </Button>
      </div>
      {folderMessage && <div style={{ fontSize: 11, color: "var(--text-dim)" }}>{folderMessage}</div>}
      {folderError && <div style={{ fontSize: 11, color: "var(--state-failure)" }}>{folderError}</div>}
    </div>
  );
}
