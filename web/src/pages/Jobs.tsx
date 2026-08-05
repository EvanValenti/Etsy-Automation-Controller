import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { listAiImageJobs, listEngines, listJobs } from "../api/client";
import type { JobStatus } from "../api/types";
import { ErrorBlock } from "../components/AsyncState";
import { Button } from "../components/Button";
import { JobTable } from "../components/JobTable";
import { Panel } from "../components/Panel";
import { Input, Select } from "../components/Select";
import { PageHeader } from "../components/PageHeader";
import { SkeletonTable } from "../components/Skeleton";
import { usePolling } from "../hooks/usePolling";
import { buildManifestLookup, groupJobs, manifestTitle } from "../jobPresentation";
import { JOB_STATUS } from "../status";

const STATUSES: JobStatus[] = [
  "pending",
  "validated",
  "queued",
  "running",
  "waiting_on_approval",
  "succeeded",
  "failed",
  "cancelled",
];

// JOB_STATUS's shared "Waiting" label (used for the compact badge
// everywhere else) reads identically for these three pre-execution
// states -- fine on a badge next to other context, but three
// indistinguishable options in a flat filter dropdown is genuinely
// confusing (which "Waiting" did I just pick?). Disambiguated only
// here, where the ambiguity actually shows up.
const STATUS_FILTER_LABEL: Partial<Record<JobStatus, string>> = {
  pending: "Waiting (Pending)",
  validated: "Waiting (Validated)",
  queued: "Waiting (Queued)",
};

export function Jobs() {
  // Deep-link support only -- read once on mount so a link like
  // /jobs?engine_id=etsy-ai-image-generator (e.g. from a Dashboard engine
  // card) arrives pre-filtered. Not synced back to the URL as filters
  // change; that's Jobs-table-redesign territory, out of scope here.
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState<JobStatus | "">((searchParams.get("status") as JobStatus | null) ?? "");
  const [engineId, setEngineId] = useState<string>(searchParams.get("engine_id") ?? "");
  const [search, setSearch] = useState("");

  const engines = usePolling(listEngines, 30000);
  const aiImageJobs = usePolling(useCallback(() => listAiImageJobs().catch(() => []), []), 15000);
  const manifestLookup = useMemo(() => buildManifestLookup(aiImageJobs.data), [aiImageJobs.data]);
  const fetchJobs = useCallback(
    () => listJobs({ status: status || undefined, engine_id: engineId || undefined }),
    [status, engineId],
  );
  const jobs = usePolling(fetchJobs, 4000, [status, engineId]);

  const engineOptions = useMemo(() => engines.data ?? [], [engines.data]);
  const hasFilters = status !== "" || engineId !== "" || search !== "";

  // Filtering only. Sorting and pagination now belong to JobTable, which
  // owns them for every table in the app (click-to-sort headers, 20 jobs
  // per page) rather than each page re-implementing its own.
  const visibleJobs = useMemo(() => {
    if (!jobs.data) return null;
    const term = search.trim().toLowerCase();
    if (!term) return jobs.data;
    return jobs.data.filter((j) => {
      const config = j.config as Record<string, unknown>;
      const identifier = [config.job_name, config.design_id, config.preset_key].find((v) => typeof v === "string") as
        | string
        | undefined;
      const manifest = typeof config.job_name === "string" ? manifestLookup.get(config.job_name) : undefined;
      const title = manifest ? manifestTitle(manifest) : null;
      return (
        j.id.toLowerCase().includes(term) ||
        (identifier ?? "").toLowerCase().includes(term) ||
        (title ?? "").toLowerCase().includes(term)
      );
    });
  }, [jobs.data, search, manifestLookup]);

  const jobCount = useMemo(() => (visibleJobs ? groupJobs(visibleJobs).length : null), [visibleJobs]);

  function updateFilter(setter: (v: string) => void, value: string) {
    setter(value);
  }

  return (
    // space-5 rather than space-6 between blocks: this page is a filter bar
    // over a long table, and the extra 8px per gap was pushing the last rows
    // of a full page below the fold for no compositional gain.
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-5)" }}>
      <PageHeader title="Jobs" meta="Every job the Controller has created, across every engine." />

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Select value={status} onChange={(e) => updateFilter(setStatus as (v: string) => void, e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_FILTER_LABEL[s] ?? JOB_STATUS[s].label}
            </option>
          ))}
        </Select>
        <Select value={engineId} onChange={(e) => updateFilter(setEngineId, e.target.value)}>
          <option value="">All engines</option>
          {engineOptions.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name}
            </option>
          ))}
        </Select>
        <Input
          value={search}
          onChange={(e) => updateFilter(setSearch, e.target.value)}
          placeholder="Search by job id or name…"
          aria-label="Search jobs"
          style={{ minWidth: 200 }}
        />
        {hasFilters && (
          <Button
            variant="ghost"
            onClick={() => {
              setStatus("");
              setEngineId("");
              setSearch("");
            }}
          >
            Clear filters
          </Button>
        )}
        {jobCount !== null && (
          <span style={{ marginLeft: "auto", color: "var(--text-secondary)", fontSize: 13 }}>
            {jobCount} Job{jobCount === 1 ? "" : "s"}
          </span>
        )}
      </div>

      <Panel index={1} title="Job History" lastUpdated={jobs.lastUpdated} refreshing={jobs.refreshing} onRefresh={jobs.refetch}>
        {jobs.loading && !jobs.hasLoadedOnce && <SkeletonTable rows={5} />}
        {jobs.error && <ErrorBlock error={jobs.error} onRetry={jobs.refetch} />}
        {visibleJobs && (
          <JobTable
            jobs={visibleJobs}
            emptyLabel={hasFilters ? "No jobs match this filter." : "No jobs yet — launch one from an engine's page."}
            // Cross-engine view: Engine is real information here, unlike on
            // a single engine's own Job History.
            showEngine
            manifests={manifestLookup}
            selectable
            onJobsDeleted={jobs.refetch}
          />
        )}
      </Panel>
    </div>
  );
}
