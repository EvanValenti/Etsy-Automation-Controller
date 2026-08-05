import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronDown, ChevronUp } from "lucide-react";
import { deleteJob } from "../api/client";
import type { Job } from "../api/types";
import {
  groupJobs,
  jobDisplayName,
  jobDuration,
  jobStageLabel,
  jobOutputSummary,
  type JobGroup,
  type ManifestsByJobName,
} from "../jobPresentation";
import { formatRelative } from "../status";
import { EmptyBlock } from "./AsyncState";
import { Button } from "./Button";
import { ConfirmDialog } from "./ConfirmDialog";
import { DisclosureArrow } from "./DisclosureArrow";
import { JobStatusBadge } from "./StatusBadges";

/** After this many jobs the table paginates. Counted in JOBS (groups), the
 * same unit the operator sees in the header count -- never in raw
 * Controller Job rows. */
const PAGE_SIZE = 20;

type SortKey = "job" | "status" | "duration" | "updated";
type SortDirection = "asc" | "desc";

interface Props {
  jobs: Job[];
  emptyLabel?: string;
  /** Cross-engine views (the Jobs page) need the Engine column; a
   * single-engine Job History does not -- every row would say the same
   * thing. */
  showEngine?: boolean;
  /** Human-readable titles, keyed by engine job_name. Without this the
   * table shows identifiers alone rather than inventing a name. */
  manifests?: ManifestsByJobName;
  /** Selection + Delete Selected / Delete All. Off by default so read-only
   * embeddings (the Dashboard's Running Jobs) stay read-only. */
  selectable?: boolean;
  /** Called after any successful delete so the caller can refetch. */
  onJobsDeleted?: () => void;
}

function groupTime(group: JobGroup): number {
  const iso = group.jobs[0].updated_at ?? group.jobs[0].created_at;
  return iso ? new Date(iso).getTime() : 0;
}

function groupDurationSeconds(group: JobGroup): number {
  const job = group.jobs[0];
  if (!job.created_at || !job.updated_at) return -1;
  const start = new Date(job.created_at).getTime();
  const end = new Date(job.updated_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return -1;
  return (end - start) / 1000;
}

/**
 * "Engine Job -> Workflow History": Controller Job rows representing
 * repeated stage-advances against the same persistent engine-side job
 * (see jobPresentation.ts's groupJobs()) collapse into one primary row,
 * expandable to the individual records underneath.
 *
 * Everything an operator counts, sorts, paginates and deletes here is a
 * GROUP -- one real job -- not a raw Controller row. That's the whole
 * point of the grouping: a listing pushed through five workflow stages is
 * one job in the count, one row on the page, and one thing to delete.
 */
export function JobTable({ jobs, emptyLabel = "No jobs.", showEngine = false, manifests, selectable = false, onJobsDeleted }: Props) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("updated");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [page, setPage] = useState(0);
  const [pendingDelete, setPendingDelete] = useState<{ groups: JobGroup[]; label: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const groups = useMemo(() => groupJobs(jobs), [jobs]);

  // Output is a column only when at least one row actually produced a
  // deliverable. For an engine whose result is a workflow stage rather
  // than an artifact, the column simply doesn't exist instead of showing
  // a page of em-dashes.
  const showOutput = useMemo(() => groups.some((g) => jobOutputSummary(g.jobs[0]) !== null), [groups]);

  const sortedGroups = useMemo(() => {
    const factor = sortDirection === "asc" ? 1 : -1;
    return groups.slice().sort((a, b) => {
      switch (sortKey) {
        case "job": {
          const an = jobDisplayName(a.jobs[0], manifests);
          const bn = jobDisplayName(b.jobs[0], manifests);
          return factor * (an.title ?? an.identifier).localeCompare(bn.title ?? bn.identifier);
        }
        case "status":
          return factor * a.jobs[0].status.localeCompare(b.jobs[0].status);
        case "duration":
          return factor * (groupDurationSeconds(a) - groupDurationSeconds(b));
        case "updated":
        default:
          return factor * (groupTime(a) - groupTime(b));
      }
    });
  }, [groups, sortKey, sortDirection, manifests]);

  const totalPages = Math.max(1, Math.ceil(sortedGroups.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages - 1);
  const pageGroups = sortedGroups.slice(currentPage * PAGE_SIZE, currentPage * PAGE_SIZE + PAGE_SIZE);

  function toggleSort(key: SortKey) {
    // First click on a column sorts ascending; clicking the same column
    // again flips to descending.
    if (sortKey === key) {
      setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDirection("asc");
    }
    setPage(0);
  }

  function toggleExpand(key: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleSelect(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function runDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    setDeleteError(null);
    // One Controller Job row at a time through the existing DELETE /jobs/
    // {id} route -- which refuses active jobs (409) and recycles output to
    // the Recycle Bin. Deleting a "job" here means deleting every row in
    // its group, because that's the one unit of work the operator sees.
    const failures: string[] = [];
    for (const group of pendingDelete.groups) {
      for (const job of group.jobs) {
        try {
          await deleteJob(job.id);
        } catch (err) {
          failures.push(err instanceof Error ? err.message : `Could not delete ${job.id.slice(0, 8)}`);
        }
      }
    }
    setDeleting(false);
    setPendingDelete(null);
    setSelected(new Set());
    if (failures.length > 0) {
      const unique = [...new Set(failures)];
      setDeleteError(
        `${failures.length} job record${failures.length === 1 ? "" : "s"} could not be deleted: ${unique.slice(0, 2).join(" · ")}`,
      );
    }
    onJobsDeleted?.();
  }

  if (jobs.length === 0) return <EmptyBlock>{emptyLabel}</EmptyBlock>;

  const selectedGroups = sortedGroups.filter((g) => selected.has(g.key));
  const columns: { key: SortKey | null; label: string }[] = [
    { key: "job", label: "Job" },
    ...(showEngine ? [{ key: null, label: "Engine" }] : []),
    { key: "status", label: "Status" },
    { key: "duration", label: "Duration" },
    ...(showOutput ? [{ key: null, label: "Output" }] : []),
    { key: "updated", label: "Updated" },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {selectable && (
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <Button
            variant="outline"
            disabled={selectedGroups.length === 0}
            onClick={() =>
              setPendingDelete({
                groups: selectedGroups,
                label: `${selectedGroups.length} selected job${selectedGroups.length === 1 ? "" : "s"}`,
              })
            }
          >
            Delete Selected{selectedGroups.length > 0 ? ` (${selectedGroups.length})` : ""}
          </Button>
          <Button
            variant="danger"
            onClick={() => setPendingDelete({ groups: sortedGroups, label: `all ${sortedGroups.length} jobs` })}
          >
            Delete All
          </Button>
          {deleteError && <span style={{ fontSize: "var(--text-sm)", color: "var(--state-failure)" }}>{deleteError}</span>}
        </div>
      )}

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "var(--text-base)" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              {selectable && <th style={{ ...headerStyle, width: 34 }} />}
              {columns.map((c) => (
                <th key={c.label} style={headerStyle}>
                  {c.key ? (
                    <button
                      onClick={() => toggleSort(c.key!)}
                      aria-label={`Sort by ${c.label}`}
                      style={{
                        background: "none",
                        border: "none",
                        padding: "4px 0",
                        cursor: "pointer",
                        font: "inherit",
                        color: sortKey === c.key ? "var(--accent)" : "var(--text-secondary)",
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                      }}
                    >
                      {c.label}
                      {/* Real chevrons rather than ▲/▼ -- the Unicode
                          triangles render solid and oversized next to a
                          10px label, and sat off the text's optical centre. */}
                      <span
                        aria-hidden
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          opacity: sortKey === c.key ? 1 : 0.35,
                        }}
                      >
                        {sortKey === c.key && sortDirection === "desc" ? (
                          <ChevronDown size={12} strokeWidth={2.5} />
                        ) : (
                          <ChevronUp size={12} strokeWidth={2.5} />
                        )}
                      </span>
                    </button>
                  ) : (
                    c.label
                  )}
                </th>
              ))}
              {selectable && <th style={{ ...headerStyle, width: 70 }} />}
            </tr>
          </thead>
          <tbody>
            {pageGroups.map((group, rowIndex) => (
              <GroupRows
                key={group.key}
                rowIndex={rowIndex}
                group={group}
                isOpen={expanded.has(group.key)}
                onToggle={() => toggleExpand(group.key)}
                navigate={navigate}
                manifests={manifests}
                showEngine={showEngine}
                showOutput={showOutput}
                selectable={selectable}
                isSelected={selected.has(group.key)}
                onSelect={() => toggleSelect(group.key)}
                onDelete={() => {
                  const name = jobDisplayName(group.jobs[0], manifests);
                  setPendingDelete({ groups: [group], label: name.title ?? name.identifier });
                }}
              />
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <Pagination currentPage={currentPage} totalPages={totalPages} onChange={setPage} />
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`Delete ${pendingDelete?.label ?? "this job"}?`}
        description="Deletes the Controller's record of this work and moves its generated output to the Windows Recycle Bin — not a permanent delete. Jobs that are still running or waiting on approval are refused rather than interrupted."
        confirmLabel="Delete"
        variant="danger"
        loading={deleting}
        onConfirm={runDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

const headerStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderBottom: "1px solid var(--border-bright)",
  // Column headings read as labels, not as data: uppercase and tracked
  // separates them from the values beneath at a glance, and the size bump
  // stops them disappearing into the first row.
  fontSize: "var(--text-xs)",
  fontWeight: 600,
  letterSpacing: "0.07em",
  textTransform: "uppercase",
  color: "var(--text-secondary)",
  whiteSpace: "nowrap",
};

/** Numbered pages plus Previous/Next. Long histories collapse to a window
 * around the current page so the control never wraps across the panel. */
function Pagination({
  currentPage,
  totalPages,
  onChange,
}: {
  currentPage: number;
  totalPages: number;
  onChange: (page: number) => void;
}) {
  const windowSize = 7;
  let start = Math.max(0, currentPage - Math.floor(windowSize / 2));
  const end = Math.min(totalPages, start + windowSize);
  start = Math.max(0, end - windowSize);
  const pages = Array.from({ length: end - start }, (_, i) => start + i);

  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <Button variant="outline" onClick={() => onChange(Math.max(0, currentPage - 1))} disabled={currentPage === 0}>
        Previous
      </Button>
      {start > 0 && <span style={{ color: "var(--text-dim)", fontSize: 13 }}>…</span>}
      {pages.map((p) => (
        <button
          key={p}
          onClick={() => onChange(p)}
          aria-current={p === currentPage ? "page" : undefined}
          className="page-btn"
          style={{
            minWidth: 32,
            height: 32,
            borderRadius: "var(--radius)",
            border: `1px solid ${p === currentPage ? "var(--accent)" : "var(--border)"}`,
            background: p === currentPage ? "color-mix(in srgb, var(--accent) 14%, transparent)" : "transparent",
            color: p === currentPage ? "var(--accent)" : "var(--text-secondary)",
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          {p + 1}
        </button>
      ))}
      {end < totalPages && <span style={{ color: "var(--text-dim)", fontSize: 13 }}>…</span>}
      <Button
        variant="outline"
        onClick={() => onChange(Math.min(totalPages - 1, currentPage + 1))}
        disabled={currentPage >= totalPages - 1}
      >
        Next
      </Button>
    </div>
  );
}

function GroupRows({
  group,
  isOpen,
  onToggle,
  navigate,
  manifests,
  showEngine,
  showOutput,
  selectable,
  isSelected,
  onSelect,
  onDelete,
  rowIndex,
}: {
  group: JobGroup;
  isOpen: boolean;
  onToggle: () => void;
  navigate: (path: string) => void;
  manifests?: ManifestsByJobName;
  showEngine: boolean;
  showOutput: boolean;
  selectable: boolean;
  isSelected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  /** Position in the table's entrance cascade. */
  rowIndex: number;
}) {
  const primary = group.jobs[0];
  const hasHistory = group.jobs.length > 1;

  return (
    <>
      <JobRow
        job={primary}
        manifests={manifests}
        navigate={navigate}
        onToggle={hasHistory ? onToggle : undefined}
        isOpen={isOpen}
        historyCount={hasHistory ? group.jobs.length : undefined}
        showEngine={showEngine}
        showOutput={showOutput}
        selectable={selectable}
        isSelected={isSelected}
        onSelect={onSelect}
        onDelete={onDelete}
        rowIndex={rowIndex}
      />
      {isOpen &&
        group.jobs.map((job, i) => (
          <JobRow
            key={job.id}
            job={job}
            manifests={manifests}
            navigate={navigate}
            indent
            showEngine={showEngine}
            showOutput={showOutput}
            selectable={selectable}
            // Expanded history cascades from the row that opened it, so the
            // sub-rows read as unfolding out of their parent rather than
            // appearing as an unrelated block.
            rowIndex={i}
          />
        ))}
    </>
  );
}

function JobRow({
  job,
  manifests,
  navigate,
  onToggle,
  isOpen,
  historyCount,
  indent,
  showEngine,
  showOutput,
  selectable,
  isSelected,
  onSelect,
  onDelete,
  rowIndex = 0,
}: {
  job: Job;
  manifests?: ManifestsByJobName;
  navigate: (path: string) => void;
  onToggle?: () => void;
  isOpen?: boolean;
  historyCount?: number;
  indent?: boolean;
  showEngine: boolean;
  showOutput: boolean;
  selectable: boolean;
  isSelected?: boolean;
  onSelect?: () => void;
  onDelete?: () => void;
  /** Position in the table's entrance cascade. */
  rowIndex?: number;
}) {
  const name = jobDisplayName(job, manifests);
  const expandable = onToggle !== undefined;
  const stageLabel = jobStageLabel(job);
  const output = jobOutputSummary(job);

  return (
    <tr
      onClick={() => navigate(`/jobs/${job.id}`)}
      // The row is the primary way into a job, but a bare <tr onClick> is
      // mouse-only: not focusable, not in the tab order, invisible to a
      // screen reader as a control. These four attributes make it reachable
      // and operable by keyboard without changing the markup structure.
      tabIndex={0}
      role="link"
      aria-label={`Open job ${name.title ?? name.identifier}`}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          navigate(`/jobs/${job.id}`);
        }
      }}
      // Rows slide in from the left rather than rising: in a table, a
      // horizontal entrance reads as rows being dealt into place. Capped at
      // 12 so page 1 of 200 jobs doesn't spend six seconds dealing itself in.
      //
      // Hover and focus both live in CSS now. They used to be onMouseEnter/
      // onMouseLeave handlers writing inline styles, which meant mouseleave
      // left `boxShadow: none` on the element -- and an inline style beats
      // any stylesheet rule, so a keyboard focus ring could never show on a
      // row the mouse had previously touched.
      className={["slide-in", "job-row", indent ? "job-row--indent" : ""].filter(Boolean).join(" ")}
      style={{
        ["--i" as string]: Math.min(rowIndex, 12),
      }}
    >
      {selectable && (
        <td style={{ padding: "6px 10px" }} onClick={(e) => e.stopPropagation()}>
          {!indent && onSelect && (
            <input
              type="checkbox"
              checked={!!isSelected}
              onChange={onSelect}
              aria-label={`Select ${name.title ?? name.identifier}`}
              style={{ width: 16, height: 16, cursor: "pointer", accentColor: "var(--accent)" }}
            />
          )}
        </td>
      )}

      {/* Whole first column is the disclosure target on an expandable row. */}
      <td
        style={{ padding: indent ? "6px 10px" : "2px 10px 2px 4px", paddingLeft: indent ? 28 : undefined }}
        onClick={expandable ? (e) => { e.stopPropagation(); onToggle!(); } : undefined}
        role={expandable ? "button" : undefined}
        tabIndex={expandable ? 0 : undefined}
        aria-expanded={expandable ? isOpen : undefined}
        aria-label={expandable ? `${isOpen ? "Hide" : "Show"} workflow history (${historyCount} steps)` : undefined}
        onKeyDown={
          expandable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onToggle!();
                }
              }
            : undefined
        }
      >
        <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
          {expandable && <DisclosureArrow open={!!isOpen} />}
          {/* What this row of the workflow history actually did, in the
              Controller's own stage vocabulary. Falls back to nothing --
              never to internal numbering -- when the engine reported no
              stage for this row. Styling unchanged from the old label. */}
          {indent && (
            <span style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>{stageLabel ?? ""}</span>
          )}
          {!indent && (
            // Readable name large, technical identifier small underneath.
            <div style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                {name.title ?? name.identifier}
              </span>
              <span className="mono" style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)" }}>
                {name.title ? name.identifier : job.id.slice(0, 8)}
                {expandable && ` · ${historyCount} steps`}
              </span>
            </div>
          )}
          {indent && (
            <Link
              to={`/jobs/${job.id}`}
              className="mono"
              style={{ color: "var(--text-secondary)", textDecoration: "none", marginLeft: 8 }}
              onClick={(e) => e.stopPropagation()}
            >
              {job.id.slice(0, 8)}
            </Link>
          )}
        </div>
      </td>

      {showEngine && <td style={{ padding: "6px 10px", color: "var(--text-secondary)" }}>{job.engine_id}</td>}

      <td style={{ padding: "6px 10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <JobStatusBadge status={job.status} />
          {job.status === "failed" && job.error_summary?.category === "launch_timeout" && (
            <span style={{ color: "var(--state-degraded)", fontSize: 12 }}>(timed out)</span>
          )}
        </div>
      </td>
      <td style={{ padding: "6px 10px", color: "var(--text-secondary)", fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>
        {jobDuration(job)}
      </td>
      {showOutput && (
        <td style={{ padding: "6px 10px", color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>{output ?? "—"}</td>
      )}
      <td style={{ padding: "6px 10px", color: "var(--text-secondary)", fontFamily: "var(--font-mono)", fontSize: "var(--text-sm)" }}>
        {formatRelative(job.updated_at)}
      </td>

      {selectable && (
        <td style={{ padding: "6px 10px" }} onClick={(e) => e.stopPropagation()}>
          {!indent && onDelete && (
            <button
              onClick={onDelete}
              aria-label={`Delete ${name.title ?? name.identifier}`}
              style={{
                background: "none",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                color: "var(--text-secondary)",
                cursor: "pointer",
                fontSize: 12,
                padding: "5px 9px",
              }}
            >
              Delete
            </button>
          )}
        </td>
      )}
    </tr>
  );
}
