import type { ReactNode } from "react";
import { RefreshCw } from "lucide-react";
import { Spinner } from "./AsyncState";
import { DisclosureArrow } from "./DisclosureArrow";
import { formatRelative } from "../status";

interface PanelProps {
  title: string;
  eyebrow?: string;
  action?: ReactNode;
  children: ReactNode;
  style?: React.CSSProperties;
  /** Step 11: when supplied, the header shows "updated Xs ago" plus a
   * manual refresh affordance — the visible signal that this panel is a
   * live view, not a static snapshot, without needing a full reload. */
  lastUpdated?: number | null;
  refreshing?: boolean;
  onRefresh?: () => void;
  /** When supplied, the WHOLE header becomes the expand/collapse control
   * (with a rotating disclosure arrow), instead of a separate small
   * "Show"/"Hide" button an operator has to aim at. Omit for a plain,
   * non-collapsible panel — the default everywhere else. */
  collapsible?: boolean;
  open?: boolean;
  onToggle?: () => void;
  /** Shown inline in the header while collapsed, so a closed panel still
   * says what's inside it ("10 recent events hidden") rather than just
   * "Collapsed." Ignored when open. */
  collapsedSummary?: ReactNode;
  /** Position in the page's entrance sequence. A page that renders several
   * panels passes 0,1,2... so they arrive one after another instead of all
   * landing on the same frame. Defaults to 0 (arrives immediately). */
  index?: number;
}

/** The recurring "module" chrome — every section of the console lives in
 * one of these: sharp corners, thin top accent rule, mono label header.
 * Mirrors instrument-panel framing rather than a generic rounded card. */
export function Panel({
  title,
  eyebrow,
  action,
  children,
  style,
  lastUpdated,
  refreshing,
  onRefresh,
  collapsible,
  open,
  onToggle,
  collapsedSummary,
  index = 0,
}: PanelProps) {
  const isOpen = open ?? true;
  return (
    <section
      style={{
        ["--i" as string]: index,
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderTop: "1px solid var(--border)",
        borderRadius: "var(--radius-lg)",
        display: "flex",
        flexDirection: "column",
        minWidth: 0,
        // Bounce spring with real travel, offset by this panel's place in
        // the page sequence. `both` holds it hidden through the delay.
        animation: "panel-in var(--spring-dur-bounce) var(--spring-bounce) both",
        animationDelay: "calc(var(--i, 0) * var(--stagger-step))",
        ...style,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          // 16px horizontally, matching the body below. The header used 18
          // while the body used 16, so on every panel in the app the title
          // sat 2px to the right of the content it was titling. Two pixels
          // is invisible as a number and very visible as a broken vertical
          // edge once you have five panels stacked down a page.
          // The collapsible variant keeps its 10px left inset -- the
          // disclosure chevron's own 32px box supplies the rest, which is
          // what puts the collapsible title on the same line as the others.
          padding: collapsible ? "11px 16px 11px 10px" : "15px 16px",
          borderBottom: isOpen ? "1px solid var(--border)" : "none",
          gap: 12,
        }}
      >
        {/* The entire title block is the toggle when collapsible — arrow and
            title together, one large target, rather than a separate button. */}
        <div
          role={collapsible ? "button" : undefined}
          tabIndex={collapsible ? 0 : undefined}
          aria-expanded={collapsible ? isOpen : undefined}
          onClick={collapsible ? onToggle : undefined}
          onKeyDown={
            collapsible
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onToggle?.();
                  }
                }
              : undefined
          }
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            flex: 1,
            minWidth: 0,
            cursor: collapsible ? "pointer" : undefined,
            borderRadius: "var(--radius)",
            padding: collapsible ? "4px 6px 4px 0" : undefined,
          }}
        >
          {/* 22px, not the default 32. The 32px box exists so a chevron in a
              dense table row is comfortably clickable on its own; in a panel
              header the ENTIRE header is already the toggle (role=button
              above), so the oversized box bought no target and pushed this
              title 46px in while a non-collapsible panel's title sits at 16.
              A leading chevron can never align exactly -- it would have to
              overhang outside the panel -- but 36px reads as a related row
              rather than a differently-indented one. */}
          {collapsible && <DisclosureArrow open={isOpen} size={22} />}
          <div style={{ minWidth: 0 }}>
            {eyebrow && <div className="label" style={{ marginBottom: 2 }}>{eyebrow}</div>}
            <h2
              style={{
                margin: 0,
                // Sans, larger, tighter. A section heading is a name, and at
                // 15px tracked mono it was competing with body copy instead
                // of leading it.
                fontSize: "var(--text-title)",
                fontWeight: 600,
                letterSpacing: "var(--track-title)",
                lineHeight: 1.25,
                color: "var(--text-primary)",
              }}
            >
              {title}
            </h2>
          </div>
          {collapsible && !isOpen && collapsedSummary && (
            <span style={{ fontSize: "var(--text-base)", color: "var(--text-secondary)", marginLeft: 10 }}>{collapsedSummary}</span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {onRefresh !== undefined && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRefresh();
              }}
              title="Refresh now"
              aria-label="Refresh now"
              className="btn-quiet"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                background: "none",
                border: "none",
                color: "var(--text-secondary)",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-xs)",
                cursor: "pointer",
                // .btn-quiet pulls left by 8px so a left-aligned label stays
                // optically flush with the content above it. This one sits
                // at the right edge of the header, where that shift would
                // just push it off the panel's right margin.
                marginLeft: 0,
              }}
            >
              {refreshing ? (
                <Spinner size={10} />
              ) : (
                // Keyed on lastUpdated so the glyph remounts -- and therefore
                // spins once -- each time a poll actually lands. A background
                // refresh becomes something you see happen instead of a
                // number that silently changed under you.
                <span
                  key={lastUpdated ?? 0}
                  className="refresh-tick"
                  aria-hidden
                  style={{ display: "inline-flex", alignItems: "center" }}
                >
                  <RefreshCw size={11} strokeWidth={2.2} />
                </span>
              )}
              {lastUpdated ? formatRelative(new Date(lastUpdated).toISOString()) : ""}
            </button>
          )}
          {action}
        </div>
      </header>
      {isOpen && <div style={{ padding: "15px 16px 16px", flex: 1, minHeight: 0 }}>{children}</div>}
    </section>
  );
}
