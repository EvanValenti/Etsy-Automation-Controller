import type { EngineHealth } from "./api/types";

// Non-generic primary action per engine -- what an operator is actually
// trying to do, not "Open Engine." Falls back to a generic verb for any
// engine registered later that isn't in this list yet, so a new engine
// never breaks the Dashboard.
export const PRIMARY_ACTION_LABEL: Record<string, string> = {
  "etsy-ai-image-generator": "Create AI Images",
  "etsy-mockup-generator": "Generate Mockups",
  "etsy-video-generator": "Create Listing Video",
};

/**
 * What the ENGINE is, right now. Four states, and every one of them is an
 * answer to the only question this badge exists to answer: "can I launch
 * work on this engine right now?"
 *
 * Deliberately has no state for work in progress elsewhere in the system.
 * There used to be `awaiting_review`, `recently_completed` and
 * `needs_attention`, derived from job workflow stages -- so an engine sat
 * there reading "Reviewing" purely because some job had a concept review
 * open. That described a job, not the engine, and the engine was in fact
 * idle and launchable the whole time. Jobs report their own progress on
 * the job cards and the workflow checklists; this badge is a status light
 * on the engine.
 */
export type OperatorState = "ready" | "running" | "offline" | "error";

// Display order for a list of engine cards: an engine the operator CANNOT
// use sorts above one they can. No workflow state participates -- a queue
// of pending reviews no longer pushes a perfectly healthy engine up the
// list ahead of a broken one.
const OPERATOR_STATE_PRIORITY: Record<OperatorState, number> = {
  error: 0,
  offline: 1,
  running: 2,
  ready: 3,
};

export function operatorStatePriority(state: OperatorState): number {
  return OPERATOR_STATE_PRIORITY[state];
}

// Which engine comes first when two of them are equally launchable. The
// server returns engines in the order their rows were inserted into the
// engines table -- a persistence detail, not a decision about what an
// operator should reach for first -- so the deliberate order lives here.
// Subordinate to OPERATOR_STATE_PRIORITY above: a broken engine still
// sorts above a healthy one regardless of where it sits in this list.
const ENGINE_DISPLAY_ORDER = ["etsy-mockup-generator", "etsy-ai-image-generator", "etsy-video-generator"];

/** Rank within one operator state. Any engine registered later that isn't
 * named above sorts after the ones that are, keeping the server's order
 * among themselves (the callers' sort is stable), so a fourth engine
 * appears on the Dashboard without this list having to know about it. */
export function engineDisplayOrder(engineId: string): number {
  const index = ENGINE_DISPLAY_ORDER.indexOf(engineId);
  return index === -1 ? ENGINE_DISPLAY_ORDER.length : index;
}

/**
 * The badge vocabulary is deliberately one word per state: the badge says
 * what the ENGINE is doing, not how much work is queued behind it.
 *
 * Counts used to be folded in here ("9 Jobs Ready for Review"), which made
 * the badge a second, competing job list -- long, variable-width, and
 * saying nothing about the engine itself. The count belongs to the Jobs
 * view, which the card now links to with a real button; the badge is a
 * status light.
 */
export const OPERATOR_STATE_STYLE: Record<OperatorState, { label: string; color: string; pulse?: boolean }> = {
  // Ready is a good state, not a neutral one -- an engine standing by with
  // nothing wrong reads green, the same vocabulary as a completed job.
  ready: { label: "Ready", color: "var(--state-success)" },
  running: { label: "Running", color: "var(--state-running)", pulse: true },
  offline: { label: "Offline", color: "var(--state-offline)" },
  error: { label: "Error", color: "var(--state-failure)" },
};

/** The badge label for one engine card: the engine's state, nothing else. */
export function operatorStateLabel(state: OperatorState): string {
  return OPERATOR_STATE_STYLE[state].label;
}

/**
 * Operator-facing engine state, and a pure function of the engine's own
 * health -- nothing else. Jobs are deliberately not a parameter: taking
 * one would be the door through which workflow state walked back into a
 * badge that is supposed to describe the engine.
 *
 * The mapping is one-to-one with what MonitoringService.get_engine_health()
 * already reports, so this invents nothing:
 *   idle              -> ready    (online, launchable -- the default)
 *   busy / launching  -> running  (actively executing a job)
 *   offline / missing -> offline  (engine unreachable)
 *   degraded          -> error    (the engine itself reported unhealthy)
 *
 * An engine with nine jobs sitting at review gates is `ready`: those
 * reviews are the operator's business with those JOBS, and the engine will
 * happily launch new work regardless.
 */
export function deriveOperatorState(health: EngineHealth | undefined): OperatorState {
  if (!health || health.state === "offline") return "offline";
  if (health.state === "degraded") return "error";
  if (health.state === "busy" || health.state === "launching") return "running";
  return "ready";
}
