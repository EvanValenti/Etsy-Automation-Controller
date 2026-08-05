import { Link } from "react-router-dom";
import type { Engine, EngineHealth, Job } from "../api/types";
import { deriveOperatorState, operatorStateLabel, OPERATOR_STATE_STYLE, PRIMARY_ACTION_LABEL } from "../engineOperatorState";
import { Boxes, Shirt, Sparkles, Video, type LucideIcon } from "lucide-react";
import { formatRelative, JOB_STATUS } from "../status";

const ENGINE_ICON: Record<string, LucideIcon> = {
  "etsy-ai-image-generator": Sparkles,
  "etsy-mockup-generator": Shirt,
  "etsy-video-generator": Video,
};
import { Badge } from "./Badge";
import { Button } from "./Button";

export function EngineCard({
  engine,
  health,
  mostRecentJob,
  index = 0,
}: {
  engine: Engine;
  health?: EngineHealth;
  /** The most recently updated Controller Job for this engine, if any.
   * Used for the "Last Job" shortcut below -- never for the status badge,
   * which describes the engine, not any job. */
  mostRecentJob?: Job | null;
  /** Position in the dashboard's entrance sequence. */
  index?: number;
}) {
  const operatorState = deriveOperatorState(health);
  const stateInfo = OPERATOR_STATE_STYLE[operatorState];
  const badgeLabel = operatorStateLabel(operatorState);
  const primaryLabel = PRIMARY_ACTION_LABEL[engine.id] ?? "Open Engine";
  const EngineIcon = ENGINE_ICON[engine.id] ?? Boxes;
  const queued = health && health.queue_length > 0 ? health.queue_length : 0;

  return (
    <div
      className="rise-in card-interactive"
      style={{
        ["--i" as string]: index,
        background: "var(--panel-raised)",
        // Border deliberately omitted -- .card-interactive owns it so the
        // hover glow isn't blocked by an inline style.
        borderRadius: "var(--radius-lg)",
        padding: "16px 17px 16px",
        display: "flex",
        flexDirection: "column",
        // Full height + the auto margin on the action block below is what
        // makes every card's buttons sit on the same line, however much
        // status text the card above them happens to carry.
        height: "100%",
      }}
    >
      {/* 1. IDENTITY -- mark, name, and the engine's state, on one row.
          The mark lets the three cards be told apart by shape and colour
          before any of them is read; previously every card opened with the
          same weight of grey text and only the wording differed. */}
      <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
        <span
          aria-hidden
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 34,
            height: 34,
            flexShrink: 0,
            borderRadius: "var(--radius)",
            background: "color-mix(in srgb, var(--brand) 13%, transparent)",
            color: "var(--brand)",
          }}
        >
          <EngineIcon size={17} strokeWidth={1.9} />
        </span>

        <div
          style={{
            // One step below a Panel heading (--text-title). This was the
            // app's only 16px, sitting between two scale steps for no
            // reason -- a card title nested inside a panel should read as
            // subordinate to that panel's own heading.
            fontSize: "var(--text-lg)",
            fontWeight: 600,
            letterSpacing: "var(--track-title)",
            lineHeight: 1.25,
            minWidth: 0,
            flex: "1 1 auto",
            overflowWrap: "anywhere",
          }}
        >
          {engine.name}
        </div>

        {/* 2. STATUS -- vertically centred against the mark, never shrunk. */}
        <Badge color={stateInfo.color} label={badgeLabel} pulse={stateInfo.pulse} />
      </div>

      {/* Supporting detail for the status above. Deliberately NOT a control:
          the two things an operator acts on are buttons in the action block,
          so this line stays quiet and never competes with them. */}
      <div
        style={{
          marginTop: 11,
          fontSize: "var(--text-sm)",
          lineHeight: 1.4,
          color: "var(--text-dim)",
          minHeight: 18,
        }}
      >
        {operatorState === "error" ? (
          <span style={{ color: "var(--state-failure)" }}>Engine reported a failure. Open it for details.</span>
        ) : operatorState === "offline" ? (
          <span style={{ color: "var(--state-offline)" }}>Unreachable. Work can't be launched right now.</span>
        ) : mostRecentJob ? (
          <>
            Last job {JOB_STATUS[mostRecentJob.status].label.toLowerCase()}{" "}
            {formatRelative(mostRecentJob.updated_at ?? mostRecentJob.created_at)}
            {queued > 0 && ` · ${queued} queued`}
          </>
        ) : (
          "No jobs yet"
        )}
      </div>

      {/* 3 + 4. ACTIONS -- the one control that starts work, full width and
          alone on its row, then the two that navigate and inspect. Three
          equal-width buttons in one row was the problem: the eye had to read
          all three to find the one that begins something. Now the tiers do
          that work (see components/Button.tsx). */}
      <div style={{ marginTop: "auto", paddingTop: 14 }}>
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 13, display: "flex", flexDirection: "column", gap: 8 }}>
          <Link to={`/engines/${engine.id}`} style={{ textDecoration: "none", display: "block" }}>
            <Button variant="primary" style={{ width: "100%", justifyContent: "center" }}>
              {primaryLabel}
            </Button>
          </Link>

          <div style={{ display: "flex", gap: 8 }}>
            <Link to={`/jobs?engine_id=${encodeURIComponent(engine.id)}`} style={{ textDecoration: "none", flex: "1 1 0", minWidth: 0 }}>
              <Button variant="nav" style={{ width: "100%", justifyContent: "center" }}>
                View Jobs{queued > 0 ? ` (${queued})` : ""}
              </Button>
            </Link>
            {mostRecentJob && (
              <Link to={`/jobs/${mostRecentJob.id}`} style={{ textDecoration: "none", flex: "1 1 0", minWidth: 0 }}>
                <Button
                  variant="utility"
                  style={{ width: "100%", justifyContent: "center" }}
                  title={`Open the last job (${JOB_STATUS[mostRecentJob.status].label.toLowerCase()} ${formatRelative(
                    mostRecentJob.updated_at ?? mostRecentJob.created_at,
                  )})`}
                >
                  Last Job {JOB_STATUS[mostRecentJob.status].label}
                </Button>
              </Link>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
