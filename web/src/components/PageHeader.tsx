import type { ReactNode } from "react";

interface PageHeaderProps {
  title: ReactNode;
  /** Supporting line under the title -- a description, or an identifier. */
  subtitle?: ReactNode;
  /** Short text on the right of the title row (a date, a one-line summary).
   * Sits on the title's baseline. Use `actions` for anything pressable. */
  meta?: ReactNode;
  /** Controls on the right of the title row. Top-aligned, since a button is
   * taller than a line of text and baseline-aligning it looks dropped. */
  actions?: ReactNode;
  /** Anything that belongs under the title block -- status badges, chips. */
  children?: ReactNode;
}

/**
 * The one page title treatment.
 *
 * Five pages previously carried four different h1 styles: Dashboard and
 * Jobs at 25px sans / weight 650 / -0.022em, EngineDetail at 24px MONO,
 * ListingAssets at 20px MONO, JobDetail at 24px sans with default weight
 * and no tracking. Moving between pages changed the title's typeface and
 * size, which is the fastest way to make one app read as three.
 *
 * Dashboard and Jobs had already been modernised; that pass just never
 * reached the other three. This component is that pass, made structural so
 * the next page cannot drift again.
 *
 * Tracking is negative here on purpose -- at 25px, letters set at 0 read
 * too loose. Small text elsewhere gets slightly positive tracking instead.
 */
export function PageHeader({ title, subtitle, meta, actions, children }: PageHeaderProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: children ? 10 : 0 }}>
      <div
        style={{
          display: "flex",
          // Baseline when the right side is text, flex-start when it holds
          // controls -- a 32px button baseline-aligned to a 25px title
          // hangs visibly below it.
          alignItems: actions ? "flex-start" : "baseline",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ minWidth: 0 }}>
          <h1
            style={{
              margin: 0,
              fontFamily: "var(--font-sans)",
              fontSize: "var(--text-display)",
              fontWeight: 650,
              letterSpacing: "var(--track-display)",
              lineHeight: 1.15,
              wordBreak: "break-word",
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <div
              style={{
                marginTop: 5,
                fontSize: "var(--text-sm)",
                color: "var(--text-secondary)",
                lineHeight: 1.45,
                maxWidth: "78ch",
              }}
            >
              {subtitle}
            </div>
          )}
        </div>

        {(meta || actions) && (
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", flexShrink: 0 }}>
            {meta && <span style={{ fontSize: "var(--text-base)", color: "var(--text-secondary)" }}>{meta}</span>}
            {actions}
          </div>
        )}
      </div>
      {children}
    </div>
  );
}
