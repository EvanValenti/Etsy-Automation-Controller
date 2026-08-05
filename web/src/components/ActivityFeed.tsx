import { Link } from "react-router-dom";
import { Ban, Check, CircleCheck, CircleDot, Pause, Play, Plus, Timer, X, type LucideIcon } from "lucide-react";
import type { JobEvent } from "../api/types";
import { formatRelative } from "../status";
import { EmptyBlock } from "./AsyncState";

/**
 * Real icons, not Unicode glyphs.
 *
 * This map used to be "＋ ⏸ ◉ ▶ ✓ ✕ ⏱ ⊘ ●". Those characters are not a
 * typeface the app controls: each one resolves through the OS font
 * fallback chain, so they arrived at different optical weights, different
 * cap heights and different baselines -- ＋ is even a FULLWIDTH plus
 * (U+FF0B), which reserves roughly double the advance width of the others.
 * Stacked in a timeline the column visibly wobbled. lucide icons render
 * from one family at one stroke weight and one box, so the column is a
 * column.
 */
const EVENT_ICON: Record<string, LucideIcon> = {
  created: Plus,
  queued: Pause,
  engine_reserved: CircleDot,
  launch_started: Play,
  launch_succeeded: Check,
  launch_failed: X,
  timed_out: Timer,
  cancelled: Ban,
  completed: CircleCheck,
};

const EVENT_COLOR: Record<string, string> = {
  created: "var(--text-secondary)",
  queued: "var(--state-queued)",
  engine_reserved: "var(--state-launching)",
  launch_started: "var(--state-running)",
  launch_succeeded: "var(--state-success)",
  launch_failed: "var(--state-failure)",
  timed_out: "var(--state-failure)",
  cancelled: "var(--state-cancelled)",
  completed: "var(--state-success)",
};

export function ActivityFeed({ events }: { events: JobEvent[] }) {
  if (events.length === 0) return <EmptyBlock>No activity recorded yet.</EmptyBlock>;

  return (
    <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 2 }}>
      {events.map((event, i) => (
        <li
          key={event.id}
          // Keyed by event id, so when polling prepends a new event only
          // that <li> mounts and slides in -- the rows already on screen
          // keep their place instead of the whole feed re-animating.
          className="slide-in"
          style={{
            // Capped: a 30-event feed shouldn't take a second to deal out.
            ["--i" as string]: Math.min(i, 10),
            display: "flex",
            // Centre, not baseline. Baseline aligns an icon by the text's
            // invisible baseline, which sits an icon low in its own row.
            alignItems: "center",
            gap: 10,
            padding: "7px 6px",
            borderBottom: "1px solid var(--border)",
            fontSize: "var(--text-sm)",
          }}
        >
          <span
            aria-hidden
            style={{
              color: EVENT_COLOR[event.event_type] ?? "var(--text-secondary)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 16,
              flexShrink: 0,
            }}
          >
            {(() => {
              const Icon = EVENT_ICON[event.event_type] ?? CircleDot;
              return <Icon size={13} strokeWidth={2.2} />;
            })()}
          </span>
          <span style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)", flexShrink: 0 }}>
            {event.event_type.replace(/_/g, " ")}
          </span>
          <span style={{ color: "var(--text-dim)" }}>on</span>
          <span style={{ color: "var(--text-secondary)" }}>{event.engine_id}</span>
          <Link
            to={`/jobs/${event.job_id}`}
            className="mono"
            style={{ color: "var(--text-dim)", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            title={event.job_id}
          >
            job:{event.job_id.slice(0, 8)}
          </Link>
          <span style={{ marginLeft: "auto", color: "var(--text-dim)", fontFamily: "var(--font-mono)", fontSize: 11, whiteSpace: "nowrap" }}>
            {formatRelative(event.occurred_at)}
          </span>
        </li>
      ))}
    </ul>
  );
}
