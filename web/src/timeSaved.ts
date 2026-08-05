/**
 * Presentation of the Dashboard's Time Saved figure. Formatting only.
 *
 * The number itself is no longer computed here. It comes from GET
 * /metrics/lifetime (see api/client.ts's getLifetimeMetrics), which reads a
 * permanent server-side ledger of completed workflows -- so deleting old
 * job history no longer reduces Time Saved or Lifetime Production, which is
 * what a lifetime statistic should mean.
 *
 * This file previously owned three more things, all deliberately removed
 * rather than left behind:
 *   - MINUTES_SAVED_PER_COMPLETED_JOB, the per-engine estimate. Now the
 *     server's, in infra/lifetime_metrics.py. Two copies of a pricing table
 *     drift, and the browser's copy could only ever price the jobs it could
 *     still see.
 *   - countCompletedWorkflows()/totalMinutesSaved(), which grouped
 *     succeeded Job rows to avoid counting one product's six stage-advances
 *     as six completions. The server ledger does that grouping now, keyed on
 *     the same engine-side identity (job_name / design_id).
 * groupJobs() itself is untouched and still very much in use -- the Jobs
 * table's "Engine Job -> Workflow History" grouping is a separate concern
 * (see jobPresentation.ts).
 */

/**
 * Under an hour reads in whole minutes; an hour or more reads in hours.
 * Whole hours drop the ".0" ("2 hrs", not "2.0 hrs") and a single hour is
 * singular -- a headline stat should read like a sentence, not like a
 * float.
 */
export function formatTimeSaved(minutes: number): string {
  if (minutes <= 0) return "0 min";
  if (minutes < 60) return `${Math.round(minutes)} min`;

  const hours = minutes / 60;
  const rounded = Math.round(hours * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${text} ${rounded === 1 ? "hr" : "hrs"}`;
}
