import { useCallback, useState } from "react";
import { CircleCheck, Clock, Layers, PackageCheck, type LucideIcon } from "lucide-react";
import {
  getActivity,
  getEngineHealth,
  getLifetimeMetrics,
  listEngines,
  listJobs,
  listListingWorkspaces,
} from "../api/client";
import type { Engine, EngineHealth, Job, JobEvent } from "../api/types";
import { ActivityFeed } from "../components/ActivityFeed";
import { ErrorBlock } from "../components/AsyncState";
import { EngineCard } from "../components/EngineCard";
import { deriveOperatorState, engineDisplayOrder, operatorStatePriority } from "../engineOperatorState";
import { JobTable } from "../components/JobTable";
import { Panel } from "../components/Panel";
import { SkeletonCards, SkeletonFeed, SkeletonTable } from "../components/Skeleton";
import { isToday } from "../status";
import { formatTimeSaved } from "../timeSaved";
import { usePolling } from "../hooks/usePolling";
import { useCountUp } from "../hooks/useCountUp";
import { MetricTile } from "../components/MetricTile";
import { PageHeader } from "../components/PageHeader";

interface EngineCardData {
  engine: Engine;
  health: EngineHealth;
  /** Powers the card's "Last Job" shortcut only. The status badge is a
   * function of `health` alone -- see deriveOperatorState(). */
  mostRecentJob: Job | null;
}

// Only these event types represent something an operator would actually
// care about seeing in a cross-engine feed -- mirrors
// monitoring_service.py's own _TERMINAL_EVENT_TYPES (COMPLETED,
// LAUNCH_FAILED, TIMED_OUT, CANCELLED) plus CREATED (a job starting is
// as meaningful as one finishing). Excludes QUEUED/ENGINE_RESERVED/
// LAUNCH_STARTED/LAUNCH_SUCCEEDED -- real events, just internal
// plumbing a job detail page's own full ActivityFeed still shows.
const MEANINGFUL_EVENT_TYPES = new Set(["created", "completed", "launch_failed", "timed_out", "cancelled"]);

async function fetchEngineCards(): Promise<EngineCardData[]> {
  const engines = await listEngines();
  // No engine-side job manifests are fetched here any more: they were read
  // solely to count review gates for the status badge, which no longer
  // describes job workflow state. One fewer request per poll, per engine.
  const [healths, allJobs] = await Promise.all([
    Promise.all(engines.map((e) => getEngineHealth(e.id))),
    listJobs(),
  ]);

  const mostRecentByEngine = new Map<string, Job>();
  for (const job of allJobs) {
    const existing = mostRecentByEngine.get(job.engine_id);
    const jobTime = job.updated_at ?? job.created_at ?? "";
    const existingTime = existing ? existing.updated_at ?? existing.created_at ?? "" : "";
    if (!existing || jobTime > existingTime) mostRecentByEngine.set(job.engine_id, job);
  }

  return engines.map((engine, i) => ({
    engine,
    health: healths[i],
    mostRecentJob: mostRecentByEngine.get(engine.id) ?? null,
  }));
}

function filterMeaningfulEvents(events: JobEvent[]): JobEvent[] {
  return events.filter((e) => MEANINGFUL_EVENT_TYPES.has(e.event_type));
}

export function Dashboard() {
  const engineCards = usePolling(useCallback(fetchEngineCards, []), 5000);
  const runningJobs = usePolling(useCallback(() => listJobs({ status: "running" }), []), 4000);
  const lifetime = usePolling(useCallback(getLifetimeMetrics, []), 15000);
  const listingWorkspaces = usePolling(useCallback(listListingWorkspaces, []), 15000);
  const activity = usePolling(useCallback(() => getActivity(30), []), 5000);
  const [activityOpen, setActivityOpen] = useState(false);

  // The "today" in Completed Today / Listings Built Today, stated once so
  // the operator knows which day the page is counting.
  const todayLabel = new Date().toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });

  // Time Saved, Lifetime Production and Completed Today all come from the
  // server's permanent completed-workflow ledger (GET /metrics/lifetime),
  // not from the succeeded-Job list this page used to group itself. That is
  // what makes them cumulative: deleting old job history is housekeeping,
  // so it changes what the Jobs page SHOWS and nothing about what the
  // Controller has accomplished. See infra/lifetime_metrics.py.
  const minutesSaved = lifetime.data?.minutes_saved ?? null;
  const lifetimeProduction = lifetime.data?.lifetime_production ?? null;
  const completedTodayCount = lifetime.data?.completed_today ?? null;
  // Not ledger-backed, and doesn't need to be: this counts built listing
  // workspace folders, which deleting a Job never affected either way.
  const listingsBuiltTodayCount = listingWorkspaces.data?.filter((w) => isToday(w.built_at)).length ?? null;

  const meaningfulActivity = activity.data ? filterMeaningfulEvents(activity.data) : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
      {/* One line. The old header spent a 26px mono title and a marketing
          tagline on saying where you already are -- two rows of vertical
          space above the numbers the operator actually came for. */}
      <PageHeader title="Dashboard" meta={todayLabel} />

      {/* Business results first, engines second, activity last. These four
          numbers are the Controller's KPIs, so they are what an operator
          sees before anything they could launch -- the page used to open on
          the engine launch section, which answered "what can I start?"
          before "what has this produced?". Still the same .grid-stats
          auto-fit grid, so the responsive collapse is unchanged. */}
      {/* --i drives the stagger delay (see .rise-in in index.css). The four
          KPIs cascade in left to right, then the panels below continue the
          same sequence, so the whole page reads as one entrance rather than
          four independent animations that happen to start together. */}
      <div className="grid-stats">
        <CountUpTile
          index={0}
          icon={Clock}
          accent="var(--metric-gold)"
          label="Time Saved"
          value={minutesSaved}
          format={formatTimeSaved}
          hint="Across every completed job"
        />
        <CountUpTile
          index={1}
          icon={Layers}
          accent="var(--metric-indigo)"
          label="Lifetime Production"
          value={lifetimeProduction}
          format={formatWholeNumber}
          hint="Workflows completed all time"
        />
        <CountUpTile
          index={2}
          icon={CircleCheck}
          accent="var(--metric-emerald)"
          label="Completed Today"
          value={completedTodayCount}
          format={formatWholeNumber}
          hint="Since midnight"
        />
        <CountUpTile
          index={3}
          icon={PackageCheck}
          accent="var(--metric-emerald)"
          label="Listings Built Today"
          value={listingsBuiltTodayCount}
          format={formatWholeNumber}
          hint="Listing folders assembled"
        />
      </div>

      <Panel
        index={4}
        title="Engines"
        eyebrow="Launch work"
        lastUpdated={engineCards.lastUpdated}
        refreshing={engineCards.refreshing}
        onRefresh={engineCards.refetch}
      >
        {engineCards.loading && !engineCards.hasLoadedOnce && <SkeletonCards />}
        {engineCards.error && <ErrorBlock error={engineCards.error} onRetry={engineCards.refetch} />}
        {engineCards.data && (
          <div className="grid-cards">
            {engineCards.data
              // An engine the operator CANNOT use sorts first, so a
              // broken or unreachable one is never buried below healthy
              // ones. Pending reviews no longer affect this order --
              // they say nothing about whether an engine can be launched.
              // Engines in the same state then fall back to the deliberate
              // ENGINE_DISPLAY_ORDER rather than to whatever order the
              // engines table happened to be populated in.
              .slice()
              .sort((a, b) => {
                const byState =
                  operatorStatePriority(deriveOperatorState(a.health)) -
                  operatorStatePriority(deriveOperatorState(b.health));
                if (byState !== 0) return byState;
                return engineDisplayOrder(a.engine.id) - engineDisplayOrder(b.engine.id);
              })
              .map((c, i) => (
                <EngineCard
                  key={c.engine.id}
                  engine={c.engine}
                  health={c.health}
                  mostRecentJob={c.mostRecentJob}
                  // Offset past the Engines panel's own delay (index 4) so
                  // the cards arrive after the frame they sit in, not
                  // through it.
                  index={5 + i}
                />
              ))}
          </div>
        )}
      </Panel>

      {runningJobs.data && runningJobs.data.length > 0 && (
        <Panel
          index={5}
          title="Running Jobs"
          eyebrow="Live"
          lastUpdated={runningJobs.lastUpdated}
          refreshing={runningJobs.refreshing}
          onRefresh={runningJobs.refetch}
        >
          <JobTable jobs={runningJobs.data} emptyLabel="No jobs currently running." />
        </Panel>
      )}
      {runningJobs.loading && !runningJobs.hasLoadedOnce && <SkeletonTable rows={2} />}
      {runningJobs.error && <ErrorBlock error={runningJobs.error} onRetry={runningJobs.refetch} />}

      <Panel
        index={6}
        title="Recent Activity"
        eyebrow="Timeline"
        lastUpdated={activity.lastUpdated}
        refreshing={activity.refreshing}
        onRefresh={activity.refetch}
        collapsible
        open={activityOpen}
        onToggle={() => setActivityOpen((v) => !v)}
        collapsedSummary={
          meaningfulActivity
            ? `${meaningfulActivity.length} recent event${meaningfulActivity.length === 1 ? "" : "s"} hidden`
            : undefined
        }
      >
        {activity.loading && !activity.hasLoadedOnce && <SkeletonFeed />}
        {activity.error && <ErrorBlock error={activity.error} onRetry={activity.refetch} />}
        {meaningfulActivity && <ActivityFeed events={meaningfulActivity} />}
      </Panel>
    </div>
  );
}

/** Count-up passes through fractional intermediates; a production count has
 * no decimals, so round every frame rather than showing 41.7 workflows. */
function formatWholeNumber(n: number): string {
  return String(Math.round(n));
}

function CountUpTile({
  value,
  format,
  ...rest
}: {
  icon: LucideIcon;
  label: string;
  /** The raw metric. Null while the request is still in flight -- the tile
   * shows an em dash rather than counting up to a number nobody has yet. */
  value: number | null;
  /** Applied per frame, so the animation formats as it climbs (58 min ->
   * 1.2 hrs) instead of counting in raw units and reformatting at the end. */
  format: (n: number) => string;
  hint?: string;
  /** This metric's permanent colour. See --metric-* in index.css: it marks
   * identity, never status, and is confined to the icon and its chip. */
  accent: string;
  index: number;
}) {
  const animated = useCountUp(value);
  return <MetricTile {...rest} value={animated === null ? "—" : format(animated)} />;
}
