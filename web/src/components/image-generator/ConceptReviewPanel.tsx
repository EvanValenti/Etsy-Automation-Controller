import { useEffect, useState } from "react";
import { ApiError, approveAiImageConcept, listAiImageConcepts, rejectAiImageConcept } from "../../api/client";
import type { AiImageConcept, AiImageMediaCategory } from "../../api/types";
import { Button } from "../Button";
import { EmptyBlock, Spinner } from "../AsyncState";

function friendlyError(err: unknown): string {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Something went wrong";
}

const STATUS_COLOR: Record<string, string> = {
  proposed: "var(--text-dim)",
  approved: "var(--state-success)",
  rejected: "var(--state-failure)",
};

/** Next index at or after `after` whose concept is still "proposed",
 * wrapping around to search from the start -- mirrors
 * concept_review.py's _next_undecided_index() exactly, so Approve/Reject
 * auto-advance the same way the CLI's review loop already does. Returns
 * `after` itself (no movement) if nothing remains undecided. */
function nextUndecidedIndex(concepts: AiImageConcept[], after: number): number {
  for (let i = after; i < concepts.length; i++) {
    if (concepts[i].status === "proposed") return i;
  }
  for (let i = 0; i < after; i++) {
    if (concepts[i].status === "proposed") return i;
  }
  return after;
}

/**
 * Concept review for one category (AI Product Mockup or Lifestyle) of one
 * job -- View/Previous/Next/Approve/Reject, calling the engine's own
 * concept_review.py functions via the Controller's thin routes. The
 * Controller never edits a concept file or decides approval logic itself;
 * every decision here is a real POST to the engine, and this component
 * only reflects whatever comes back.
 */
export function ConceptReviewPanel({
  jobName,
  category,
  categoryLabel,
  refreshSignal,
  onDecision,
}: {
  jobName: string;
  category: AiImageMediaCategory;
  categoryLabel: string;
  /** Bumped by the parent whenever the job's pipeline state changes for
   * any reason -- e.g. a fresh "Continue Automatically" call generating
   * concepts for the first time. */
  refreshSignal: string;
  /**
   * Called after every approve/reject that the engine actually persisted.
   *
   * Required, not optional: an approve/reject can complete the Concept
   * Review STAGE (job_manifest.py's _concept_review_complete), which moves
   * the job's next_step to "Build Prompts". The parent owns that state and
   * decides from it whether to show this review or the next action -- so
   * without this call the parent never re-reads it, and the job soft-locks:
   * this panel says "✓ All concepts reviewed" while the checklist beside it
   * still shows Concepts Reviewed incomplete and no next action appears.
   * That was a real, reproducible bug, with the ENGINE already reporting
   * concept_review_complete=true the whole time.
   */
  onDecision: () => void;
}) {
  const [concepts, setConcepts] = useState<AiImageConcept[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [index, setIndex] = useState(0);
  const [actionLoading, setActionLoading] = useState<"approve" | "reject" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Once every concept in this category has a decision, the full
  // browsable list is no longer the primary thing an operator needs --
  // collapse to a compact "done" summary, with an explicit way back in
  // to re-browse/double-check decisions.
  const [showAfterComplete, setShowAfterComplete] = useState(false);

  function refresh() {
    setError(null);
    listAiImageConcepts(jobName, category)
      .then((data) => {
        setConcepts(data);
        setIndex((prev) => Math.min(prev, Math.max(0, data.length - 1)));
      })
      .catch((err) => setError(friendlyError(err)));
  }

  // Position resets only when this panel is actually pointed at something
  // else. Deliberately NOT keyed on refreshSignal: the parent now re-reads
  // pipeline_status after every decision (see onDecision), so a signal
  // change is a routine mid-review event -- resetting on it would throw the
  // operator back to concept 1 the moment they judged concept 2.
  useEffect(() => {
    setIndex(0);
    setShowAfterComplete(false);
  }, [jobName, category]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobName, category, refreshSignal]);

  async function handleDecision(action: "approve" | "reject") {
    if (!concepts || concepts.length === 0) return;
    const target = concepts[index];
    setActionLoading(action);
    setActionError(null);
    try {
      const call = action === "approve" ? approveAiImageConcept : rejectAiImageConcept;
      const result = await call(jobName, category, target.concept_id);
      setConcepts((prev) => {
        if (!prev) return prev;
        const updated = prev.map((c) => (c.concept_id === result.concept.concept_id ? result.concept : c));
        setIndex(nextUndecidedIndex(updated, index + 1));
        return updated;
      });
      // The engine has persisted this decision, so the job's stage state may
      // have just changed. Tell the parent to re-read it -- see onDecision.
      onDecision();
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setActionLoading(null);
    }
  }

  if (error) {
    return <EmptyBlock>{error}</EmptyBlock>;
  }
  if (!concepts) {
    return (
      <div style={{ fontSize: 12, color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 8 }}>
        <Spinner size={14} /> Loading {categoryLabel} concepts…
      </div>
    );
  }
  if (concepts.length === 0) {
    return <EmptyBlock>No {categoryLabel} concepts yet — generate concepts first.</EmptyBlock>;
  }

  const total = concepts.length;
  const counts = concepts.reduce(
    (acc, c) => ({ ...acc, [c.status]: (acc[c.status] ?? 0) + 1 }),
    {} as Record<string, number>,
  );
  const isComplete = (counts.proposed ?? 0) === 0;

  if (isComplete && !showAfterComplete) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          flexWrap: "wrap",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: "10px 12px",
        }}
      >
        <span style={{ fontSize: "var(--text-sm)", color: "var(--state-success)" }}>
          ✓ All {total} {categoryLabel} concepts reviewed — {counts.approved ?? 0} approved, {counts.rejected ?? 0} rejected.
        </span>
        <Button variant="outline" onClick={() => setShowAfterComplete(true)}>
          Review Again
        </Button>
      </div>
    );
  }

  const concept = concepts[index];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <span className="mono" style={{ fontSize: 11, color: "var(--text-dim)" }}>
          {index + 1} of {total}
        </span>
        <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
          {counts.approved ?? 0} approved · {counts.rejected ?? 0} rejected · {counts.proposed ?? 0} remaining
        </span>
        {isComplete && (
          <Button variant="ghost" onClick={() => setShowAfterComplete(false)}>
            Done reviewing
          </Button>
        )}
      </div>

      <div
        style={{
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>{concept.concept_name || concept.concept_id}</div>
          <span
            className="mono"
            style={{
              fontSize: "var(--text-xs)",
              color: STATUS_COLOR[concept.status] ?? "var(--text-dim)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              whiteSpace: "nowrap",
            }}
          >
            {concept.status}
          </span>
        </div>
        {(concept.purpose || concept.role) && (
          <div className="mono" style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)" }}>
            {[concept.purpose, concept.role].filter(Boolean).join(" — ")}
          </div>
        )}
        {concept.concept_summary && (
          <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>{concept.concept_summary}</div>
        )}
        {(concept.environment || concept.mood) && (
          <div style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)" }}>
            {concept.environment && <div>Environment: {concept.environment}</div>}
            {concept.mood && <div>Mood: {concept.mood}</div>}
          </div>
        )}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "center" }}>
        <div style={{ display: "flex", gap: 8 }}>
          <Button variant="outline" onClick={() => setIndex((i) => Math.max(0, i - 1))} disabled={index === 0}>
            Previous
          </Button>
          <Button variant="outline" onClick={() => setIndex((i) => Math.min(total - 1, i + 1))} disabled={index === total - 1}>
            Next
          </Button>
        </div>
        <div style={{ display: "flex", gap: 8, paddingLeft: 14, borderLeft: "1px solid var(--border)" }}>
        <Button
          variant="primary"
          onClick={() => handleDecision("approve")}
          loading={actionLoading === "approve"}
          disabled={actionLoading !== null}
        >
          Approve
        </Button>
        <Button
          variant="danger"
          onClick={() => handleDecision("reject")}
          loading={actionLoading === "reject"}
          disabled={actionLoading !== null}
        >
          Reject
        </Button>
        </div>
      </div>
      {actionError && <div style={{ fontSize: 11, color: "var(--state-failure)" }}>{actionError}</div>}
    </div>
  );
}
