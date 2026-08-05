import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface MetricTileProps {
  label: string;
  /** Pre-formatted for display. Numeric tiles that animate pass the value
   * already run through their count-up + formatter. */
  value: ReactNode;
  hint?: ReactNode;
  /** Optional identity colour. Confined to the icon chip and the numeral --
   * never the card, border or background, so a row of tiles still reads as
   * one row of metrics rather than four coloured panels. */
  accent?: string;
  icon?: LucideIcon;
  /** Position in the page's entrance cascade. */
  index?: number;
}

/**
 * The one metric tile for every page.
 *
 * There were three, all sharing the same `.grid-stats` slot and none
 * agreeing: Dashboard's had an icon chip, 10px radius and a 34px numeral;
 * EngineDetail's had a 2px coloured top border, 6px radius and a 26px
 * numeral; JobDetail's had an uppercase label, 6px radius and a 15px
 * numeral. Two of them were both called `InfoTile`, defined separately in
 * two files. The same slot looking different on each page is what stops a
 * card hierarchy from meaning anything.
 *
 * Resolved toward the Dashboard treatment because it was the most recently
 * designed and the most legible: container radius, a numeral large enough
 * to be the thing the eye lands on, and colour carried by the chip rather
 * than by a border stripe that read as a status the tile did not have.
 */
export function MetricTile({ label, value, hint, accent, icon: Icon, index = 0 }: MetricTileProps) {
  return (
    <div
      className="rise-in card-interactive"
      style={{
        ["--i" as string]: index,
        background: "var(--panel)",
        // Container radius -- panels and cards are --radius-lg, interactive
        // controls are --radius. Two of the old tiles used the control
        // radius, which made them read as oversized buttons.
        borderRadius: "var(--radius-lg)",
        padding: "15px 17px 16px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        {Icon && (
          <span
            aria-hidden
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 26,
              height: 26,
              flexShrink: 0,
              borderRadius: "var(--radius)",
              background: `color-mix(in srgb, ${accent ?? "var(--text-dim)"} 14%, transparent)`,
              color: accent ?? "var(--text-dim)",
            }}
          >
            <Icon size={15} strokeWidth={1.9} />
          </span>
        )}
        {/* A metric's name is a title, not a field label: sentence case at
            13px reads as the heading of the number below it, where the
            shared uppercase .label style read as chrome. */}
        <span
          style={{
            minWidth: 0,
            overflowWrap: "anywhere",
            fontSize: "var(--text-base)",
            fontWeight: 600,
            letterSpacing: "-0.005em",
            color: "var(--text-secondary)",
          }}
        >
          {label}
        </span>
      </div>

      <div
        className="mono"
        style={{
          fontSize: "var(--text-metric)",
          fontWeight: 550,
          lineHeight: 1.1,
          letterSpacing: "-0.025em",
          color: accent ?? "var(--text-primary)",
          marginTop: 13,
          // Tabular figures so a counting number climbs in place instead of
          // reflowing the tile on every frame.
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
      {hint && (
        <div style={{ fontSize: "var(--text-sm)", color: "var(--text-dim)", lineHeight: 1.35, marginTop: 5 }}>
          {hint}
        </div>
      )}
    </div>
  );
}
