import type { AiImagePipelineStatus } from "../../api/types";

/**
 * Operator-facing relabeling of the engine's own state values -- never a
 * new state machine. Every label here is derived from data the engine
 * already computes (pipeline_status, next_step, advance.status/stage);
 * this module only chooses clearer words for the same facts, the same
 * way describeNextStep() below relabels main.py's own NEXT_STEP_ORDER
 * strings without changing what job is actually next.
 */

// Mirrors Etsy-AI-Image-Generator/src/main.py's NEXT_STEP_ORDER labels.
const NEXT_STEP_LABELS: Record<string, string> = {
  "Plan Concepts": "Ready to plan concepts",
  "Generate Concepts (Claude Code)": "Ready to generate concepts",
  "Review Concepts": "Concepts ready for your review",
  "Build Prompts": "Ready to build prompts",
  "Review Prompts": "Prompts ready for your review",
  "Generate Images": "Ready to generate images",
  "Review Images": "Images ready for your review",
};

// Mirrors main.py's own PIPELINE_COMPLETE_LABEL exactly.
const COMPLETE_NEXT_STEP = "View Approved Media Handoff or continue to the Controller workflow";

/** Friendlier text for the engine's own next_step value. Falls back to
 * the engine's raw string for anything not in the known list, so a future
 * engine-side label change never breaks rendering -- just shows the
 * engine's own (already human-oriented) text unchanged. */
export function describeNextStep(nextStep: string): string {
  if (nextStep === COMPLETE_NEXT_STEP) return "Complete";
  return NEXT_STEP_LABELS[nextStep] ?? nextStep;
}

// The three raw next_step values that mean "a human needs to look at
// this job" -- used by the Dashboard's engine cards to surface a review
// count without re-deriving pipeline_status itself.
const REVIEW_GATE_NEXT_STEPS = new Set(["Review Concepts", "Review Prompts", "Review Images"]);

export function isAwaitingReview(nextStep: string): boolean {
  return REVIEW_GATE_NEXT_STEPS.has(nextStep);
}

// Mirrors headless.py's advance_job() stage identifiers.
const STAGE_LABELS: Record<string, string> = {
  concept_planning: "planning concepts",
  concept_generation: "generating concepts",
  concept_review: "concept review",
  prompt_build: "building prompts",
  prompt_review: "prompt review",
  image_generation: "generating images",
  image_review: "image review",
  ready_for_controller: "workflow completion",
};

export function describeStage(stage: string | null): string {
  if (!stage) return "the next step";
  return STAGE_LABELS[stage] ?? stage;
}

export interface WorkflowStatusInfo {
  /** The primary, human-readable headline. */
  label: string;
  /** Engine-supplied supporting detail, shown secondarily. */
  detail?: string;
  tone: "progress" | "action-needed" | "complete" | "failed";
}

/**
 * What actually happened, distinct from the Controller's own generic
 * "Succeeded" badge (which only ever reflects "the orchestration request
 * completed without crashing" -- see JobStatusBadge). A Controller Job can
 * be Controller-status "succeeded" while this reports "action-needed"
 * (waiting_on_human) or even "failed" reasoning is never conflated with
 * Controller-request success -- only advance_job()'s own reported outcome
 * and the job's persistent pipeline_status decide this.
 */
export function describeAdvanceOutcome(
  advance: { status: string; stage: string | null; detail: string | null } | undefined,
  pipelineStatus: AiImagePipelineStatus,
): WorkflowStatusInfo {
  if (pipelineStatus.ready_for_controller) {
    return { label: "Workflow complete", tone: "complete" };
  }
  if (!advance) {
    return { label: "Not yet started", tone: "progress" };
  }
  const stageLabel = describeStage(advance.stage);
  if (advance.status === "error") {
    return { label: `Failed during ${stageLabel}`, detail: advance.detail ?? undefined, tone: "failed" };
  }
  if (advance.status === "waiting_on_human") {
    return { label: `Waiting for you — ${stageLabel}`, detail: advance.detail ?? undefined, tone: "action-needed" };
  }
  if (advance.status === "complete") {
    return { label: "Workflow complete", tone: "complete" };
  }
  return { label: `Moved forward — ${stageLabel}`, detail: advance.detail ?? undefined, tone: "progress" };
}

/**
 * What the "advance this job" button should actually say and warn about,
 * for the stage the engine reports as next.
 *
 * Generic "Continue Automatically" hid the only distinction an operator
 * cares about before clicking: whether this step is a free local file
 * operation that finishes instantly, or a paid API call that takes
 * minutes. Both were the same button with the same warning, which meant
 * the warning was noise on local steps and easy to miss on paid ones.
 *
 * Keyed on the engine's own next_step values (main.py's NEXT_STEP_ORDER),
 * so this never invents a stage. An unrecognized value falls back to the
 * neutral local action rather than claiming an API cost it can't verify.
 */
export interface NextStepAction {
  /** Button text. */
  label: string;
  /** Text shown while the request is in flight. */
  runningLabel: string;
  /** Cost/duration expectations. Empty for local steps -- a local file
   * operation must never show an API-credit message. */
  notes: string[];
  provider: "claude" | "openai" | "local";
}

const LOCAL_ACTION: NextStepAction = {
  label: "Continue",
  runningLabel: "Working…",
  notes: [],
  provider: "local",
};

const NEXT_STEP_ACTIONS: Record<string, NextStepAction> = {
  "Plan Concepts": { ...LOCAL_ACTION, label: "Plan Concepts", runningLabel: "Planning concepts…" },
  "Generate Concepts (Claude Code)": {
    label: "Generate Concepts via Claude",
    runningLabel: "Generating concepts via Claude…",
    notes: ["Uses Claude API credits.", "This may take about a minute.", "Progress updates automatically."],
    provider: "claude",
  },
  "Build Prompts": { ...LOCAL_ACTION, label: "Build Prompts", runningLabel: "Building prompts…" },
  "Generate Images": {
    label: "Generate Images via OpenAI",
    runningLabel: "Generating images with OpenAI…",
    notes: ["Uses OpenAI API credits.", "This may take several minutes.", "Progress updates automatically."],
    provider: "openai",
  },
};

export function nextStepAction(nextStep: string): NextStepAction {
  return NEXT_STEP_ACTIONS[nextStep] ?? { ...LOCAL_ACTION, label: "Next Step" };
}

/**
 * The single answer to "what should the operator do right now?", derived
 * from the engine's own next_step.
 *
 * Everything the workflow UI renders is keyed off this one value, so
 * stage status, helper text, buttons, waiting messages and completion
 * indicators can never disagree with each other. Previously each of those
 * was decided independently from pipeline_status booleans, which is how a
 * job ended up showing "Prompts are ready for review" next to a Generate
 * Images button next to a completed prompt-review panel -- three true
 * statements about three different moments in the workflow, on screen at
 * once.
 *
 * Exactly one of `gate` and `advance` is ever non-null:
 *  - gate    -- the workflow is waiting on a human decision. There is no
 *               advance action; the operator's job is the review itself.
 *  - advance -- the workflow can move on its own. No review panel is
 *               shown, because nothing is waiting on the operator.
 *  - neither -- the workflow is finished.
 */
export type WorkflowGate = "concept_review" | "prompt_review" | "image_review";

export interface WorkflowView {
  /** The review the operator is being asked for, if any. */
  gate: WorkflowGate | null;
  /** The runnable next step, if any. */
  advance: NextStepAction | null;
  complete: boolean;
  /** One line answering "what should I do next?". */
  headline: string;
}

const GATE_BY_NEXT_STEP: Record<string, WorkflowGate> = {
  "Review Concepts": "concept_review",
  "Review Prompts": "prompt_review",
  "Review Images": "image_review",
};

const GATE_HEADLINE: Record<WorkflowGate, string> = {
  concept_review: "Review the generated concepts and approve the ones worth building prompts for.",
  prompt_review: "Review the built prompts, delete any you don't want, then approve to generate images.",
  image_review: "Review the generated images — approve the ones to keep, reject the rest.",
};

export function workflowView(nextStep: string, pipelineStatus: AiImagePipelineStatus): WorkflowView {
  if (pipelineStatus.ready_for_controller || nextStep === COMPLETE_NEXT_STEP) {
    return {
      gate: null,
      advance: null,
      complete: true,
      headline: "Workflow complete — this job's approved media is ready to assemble into a listing.",
    };
  }

  const gate = GATE_BY_NEXT_STEP[nextStep];
  if (gate) {
    return { gate, advance: null, complete: false, headline: GATE_HEADLINE[gate] };
  }

  const advance = nextStepAction(nextStep);
  return {
    gate: null,
    advance,
    complete: false,
    // Button labels carry proper nouns ("OpenAI", "Claude") -- never
    // case-fold them into the sentence.
    headline: advance.provider === "local" ? `Ready to continue: ${advance.label}.` : `Ready to run: ${advance.label}.`,
  };
}

export const TONE_COLOR: Record<WorkflowStatusInfo["tone"], string> = {
  progress: "var(--text-secondary)",
  "action-needed": "var(--state-degraded)",
  complete: "var(--state-success)",
  failed: "var(--state-failure)",
};
