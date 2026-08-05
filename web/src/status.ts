import type { EngineState, JobStatus } from "./api/types";

interface StatusInfo {
  color: string;
  label: string;
  pulse?: boolean;
}

// Operator-friendly vocabulary -- consistent with the Dashboard's own
// engine-level states (engineOperatorState.ts). Pre-execution states
// (pending/validated/queued) all read as "Waiting" -- an operator doesn't
// need to distinguish internal pipeline substates that all mean "hasn't
// started running yet." Colors are unchanged; only labels were normalized.
//
// waiting_on_approval reads as "Ready for Review", not "Needs Attention":
// an approval gate is a designed stage of the workflow, so it uses the
// accent "your turn" cue and leaves the degraded amber to mean something
// actually went wrong.
//
// This is a JOB status, and it is the only place a pending review is
// reported. The engine badge (engineOperatorState.ts) deliberately says
// nothing about review gates -- it answers "can I launch work on this
// engine right now?", which an open review never changes.
export const JOB_STATUS: Record<JobStatus, StatusInfo> = {
  pending: { color: "var(--state-idle)", label: "Waiting" },
  validated: { color: "var(--state-idle)", label: "Waiting" },
  queued: { color: "var(--state-queued)", label: "Waiting" },
  running: { color: "var(--state-running)", label: "Running", pulse: true },
  waiting_on_approval: { color: "var(--accent)", label: "Ready for Review" },
  succeeded: { color: "var(--state-success)", label: "Completed" },
  failed: { color: "var(--state-failure)", label: "Failed" },
  cancelled: { color: "var(--state-cancelled)", label: "Cancelled" },
};

export const ENGINE_STATE: Record<EngineState, StatusInfo> = {
  idle: { color: "var(--state-idle)", label: "Idle" },
  busy: { color: "var(--state-running)", label: "Busy", pulse: true },
  launching: { color: "var(--state-launching)", label: "Launching", pulse: true },
  degraded: { color: "var(--state-degraded)", label: "Degraded" },
  offline: { color: "var(--state-offline)", label: "Offline" },
};

export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return iso;
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** True if an ISO timestamp falls on today's local calendar date --
 * used for "today" Dashboard summaries (Jobs Completed Today, Listings
 * Built Today), never for anything that decides workflow state. */
export function isToday(iso: string | null): boolean {
  if (!iso) return false;
  const date = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(date.getTime())) return false;
  const now = new Date();
  return (
    date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate()
  );
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}
