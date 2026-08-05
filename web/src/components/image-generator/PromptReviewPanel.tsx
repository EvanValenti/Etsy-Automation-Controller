import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  deleteAiImagePrompt,
  getAiImagePromptDetail,
  listAiImagePrompts,
  markAiImagePromptReviewComplete,
} from "../../api/client";
import type { AiImageJobManifest, AiImageMediaCategory, AiImagePromptSummary } from "../../api/types";
import { Button } from "../Button";
import { ConfirmDialog } from "../ConfirmDialog";
import { EmptyBlock, Spinner } from "../AsyncState";
import { DisclosureArrow } from "../DisclosureArrow";
import { PromptViewer } from "./PromptViewer";

function friendlyError(err: unknown): string {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Something went wrong";
}

const CATEGORY_LABEL: Record<AiImageMediaCategory, string> = {
  ai_product_mockup: "AI Product Mockup",
  lifestyle_mockup: "Lifestyle",
};

/**
 * Prompt Review as a real approval stage rather than a read-only listing.
 *
 * Each prompt supports View (in-app read-only viewer), Copy (straight to
 * the clipboard, no viewer needed) and Delete (drops it from the
 * generation set, so only the prompts left here are sent to OpenAI).
 * Deleting refreshes the list immediately, so the count an operator is
 * about to approve is always the count that will actually run.
 *
 * Per-prompt actions are deliberately a list this component maps over --
 * adding "Regenerate" later means adding one entry and one handler, not
 * restructuring the row.
 */
export function PromptReviewPanel({
  jobName,
  reviewComplete,
  onConfirmed,
}: {
  jobName: string;
  /** From the job's own pipeline_status.prompt_review_complete -- this
   * component never infers completion on its own. */
  reviewComplete: boolean;
  /** Called with the engine's refreshed job status right after a
   * successful confirmation, so the caller can update its own next-step
   * display without a second request. */
  onConfirmed?: (manifest: AiImageJobManifest) => void;
}) {
  const [prompts, setPrompts] = useState<AiImagePromptSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [justConfirmed, setJustConfirmed] = useState(false);
  const [showListAfterComplete, setShowListAfterComplete] = useState(false);

  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [copyingKey, setCopyingKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AiImagePromptSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const refresh = useCallback(() => {
    listAiImagePrompts(jobName)
      .then(setPrompts)
      .catch((err) => setError(friendlyError(err)));
  }, [jobName]);

  useEffect(() => {
    setError(null);
    setExpanded(null);
    setConfirmError(null);
    setJustConfirmed(false);
    refresh();
  }, [jobName, refresh]);

  const complete = reviewComplete || justConfirmed;

  async function handleConfirm() {
    setConfirming(true);
    setConfirmError(null);
    try {
      const manifest = await markAiImagePromptReviewComplete(jobName);
      setJustConfirmed(true);
      onConfirmed?.(manifest);
    } catch (err) {
      setConfirmError(friendlyError(err));
    } finally {
      setConfirming(false);
    }
  }

  /** Copies without opening the viewer: fetches the prompt detail and
   * writes the user prompt straight to the clipboard. */
  async function handleCopy(p: AiImagePromptSummary) {
    const key = `${p.category}/${p.concept_id}`;
    setCopyingKey(key);
    setActionError(null);
    try {
      const detail = await getAiImagePromptDetail(jobName, p.category, p.concept_id);
      await navigator.clipboard.writeText(detail.user_prompt);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((k) => (k === key ? null : k)), 2000);
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setCopyingKey(null);
    }
  }

  async function handleDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    setActionError(null);
    try {
      const result = await deleteAiImagePrompt(jobName, pendingDelete.category, pendingDelete.concept_id);
      setPendingDelete(null);
      setExpanded(null);
      // Immediate refresh so the remaining-prompt count is accurate the
      // moment the dialog closes.
      refresh();
      onConfirmed?.(result.job_status);
    } catch (err) {
      setPendingDelete(null);
      setActionError(friendlyError(err));
    } finally {
      setDeleting(false);
    }
  }

  if (error) return <EmptyBlock>{error}</EmptyBlock>;
  if (!prompts) {
    return (
      <div style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 8 }}>
        <Spinner size={14} /> Loading prompts…
      </div>
    );
  }
  if (prompts.length === 0) {
    return <EmptyBlock>No prompts built yet for this job.</EmptyBlock>;
  }

  const grouped = (Object.keys(CATEGORY_LABEL) as AiImageMediaCategory[])
    .map((category) => ({ category, items: prompts.filter((p) => p.category === category) }))
    .filter((g) => g.items.length > 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {complete ? (
        // A completed review stays collapsible -- the prompts remain
        // reachable without re-expanding the whole stage by default.
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--state-success)" }}>
            ✓ Prompt review complete — {prompts.length} prompt{prompts.length === 1 ? "" : "s"} ready to generate.
          </span>
          <Button variant="outline" onClick={() => setShowListAfterComplete((v) => !v)}>
            {showListAfterComplete ? "Hide Prompts" : "View Prompts"}
          </Button>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
          <div style={{ fontSize: "var(--text-base)", color: "var(--text-secondary)" }}>
            <strong style={{ color: "var(--text-primary)" }}>{prompts.length} prompt{prompts.length === 1 ? "" : "s"}</strong>{" "}
            will be sent to OpenAI. Delete any you don't want generated.
          </div>
          <Button variant="primary" onClick={handleConfirm} loading={confirming} disabled={confirming}>
            Approve {prompts.length} Prompt{prompts.length === 1 ? "" : "s"}
          </Button>
        </div>
      )}
      {confirmError && <div style={{ fontSize: 13, color: "var(--state-failure)" }}>{confirmError}</div>}
      {actionError && <div style={{ fontSize: 13, color: "var(--state-failure)" }}>{actionError}</div>}

      {(!complete || showListAfterComplete) &&
        grouped.map(({ category, items }) => (
          <div key={category} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", fontWeight: 700 }}>
              {CATEGORY_LABEL[category]} ({items.length})
            </div>
            {items.map((p) => {
              const key = `${p.category}/${p.concept_id}`;
              const isOpen = expanded === key;
              return (
                <div
                  key={key}
                  style={{
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius)",
                    padding: "10px 12px",
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                    <div
                      role="button"
                      tabIndex={0}
                      aria-expanded={isOpen}
                      onClick={() => setExpanded(isOpen ? null : key)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setExpanded(isOpen ? null : key);
                        }
                      }}
                      style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer", flex: 1, minWidth: 0 }}
                    >
                      <DisclosureArrow open={isOpen} />
                      <div>
                        <div style={{ fontSize: 14, fontWeight: 600 }}>{p.concept_name || p.concept_id}</div>
                        <div className="mono" style={{ fontSize: 12, color: "var(--text-dim)" }}>
                          {p.concept_id} · {p.generation_status ?? "not generated"}
                        </div>
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <Button variant="outline" onClick={() => setExpanded(isOpen ? null : key)}>
                        {isOpen ? "Hide Prompt" : "View Prompt"}
                      </Button>
                      <Button variant="outline" onClick={() => handleCopy(p)} loading={copyingKey === key}>
                        {copiedKey === key ? "Copied ✓" : "Copy Prompt"}
                      </Button>
                      <Button variant="danger" onClick={() => setPendingDelete(p)}>
                        Delete
                      </Button>
                    </div>
                  </div>
                  {isOpen && <PromptViewer jobName={jobName} category={p.category} conceptId={p.concept_id} />}
                </div>
              );
            })}
          </div>
        ))}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`Delete the prompt for ${pendingDelete?.concept_name || pendingDelete?.concept_id}?`}
        description="Removes this prompt from the generation set, so it is not sent to OpenAI. The concept it came from stays approved, and Build Prompts can create this prompt again. A prompt whose image has already been generated cannot be deleted — reject the image in Image Review instead."
        confirmLabel="Delete Prompt"
        variant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
