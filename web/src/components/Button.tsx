import { forwardRef } from "react";
import type { ButtonHTMLAttributes } from "react";
import { Spinner } from "./AsyncState";

type Variant = "primary" | "nav" | "utility" | "danger" | "ghost" | "outline";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
}

/**
 * Six variants across three tiers, so an operator can tell what a control
 * DOES before reading its label.
 *
 *   primary   solid orange   starts work (Create, Generate, Launch, Build)
 *   nav       indigo outline goes somewhere (View Jobs, Open Engine)
 *   utility   gold outline   inspects a specific thing (Last Job, Details)
 *   outline   neutral        everything else (Refresh, Cancel, Browse)
 *   danger    crimson        destructive
 *   ghost     bare           lowest-priority, inline
 *
 * The tier colours are not decoration; they follow the system's existing
 * meanings. Indigo is the brand and carries structure, so navigation wears
 * it. Gold marks a specific record worth inspecting. Orange stays reserved
 * for the one control on a card that starts real work, which is what keeps
 * it meaningful when three buttons sit side by side.
 *
 * `primary` is the only solid fill in the system. Its label is near-black:
 * white on this orange measures 2.8:1 and fails WCAG AA, while near-black
 * measures 6.9:1.
 */
const VARIANT_STYLE: Record<Variant, React.CSSProperties> = {
  primary: {
    background: "var(--action-solid)",
    border: "1px solid var(--action-solid)",
    color: "var(--action-fg)",
    fontWeight: 600,
  },
  nav: {
    background: "color-mix(in srgb, var(--brand) 9%, transparent)",
    border: "1px solid color-mix(in srgb, var(--brand) 42%, transparent)",
    color: "var(--brand)",
  },
  utility: {
    background: "color-mix(in srgb, var(--metric-gold) 8%, transparent)",
    border: "1px solid color-mix(in srgb, var(--metric-gold) 38%, transparent)",
    color: "var(--metric-gold)",
  },
  danger: {
    background: "transparent",
    border: "1px solid color-mix(in srgb, var(--state-failure) 45%, transparent)",
    color: "var(--state-failure)",
  },
  outline: {
    background: "var(--panel-raised)",
    border: "1px solid var(--border-bright)",
    color: "var(--text-secondary)",
  },
  ghost: {
    background: "transparent",
    border: "1px solid transparent",
    color: "var(--text-dim)",
  },
};

/** The one button implementation for the whole app — every action
 * (launch, cancel, retry, refresh, confirm) routes through this so
 * hover/disabled/loading states stay visually consistent (Step 11). */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "outline", loading, disabled, children, style, className, ...rest },
  ref,
) {
  const isDisabled = disabled || loading;
  return (
    <button
      ref={ref}
      disabled={isDisabled}
      className={["btn", `btn-${variant}`, className].filter(Boolean).join(" ")}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        // Height, radius, font family and size come from `.btn` in
        // index.css, which shares them with `.control` -- that shared
        // baseline is what puts buttons, inputs and selects on one line.
        // Only the horizontal padding is stated here.
        padding: "0 13px",
        // Sentence case in the UI sans, not tracked-out mono caps. Uppercase
        // mono read as terminal output rather than as something to press,
        // and it made every long label ("Open Latest AI Image Outputs")
        // wider than it needed to be.
        fontWeight: 500,
        letterSpacing: "var(--track-base)",
        // nowrap keeps a label on one line, but on its own it lets a long
        // label spill straight out of the button box. The engine cards run
        // two equal-width buttons in a row and "Last Job Completed" already
        // fills its box to within ~2px, so any narrower viewport overflowed
        // it. Ellipsis degrades instead: the label truncates inside the
        // button rather than escaping it. `title` on the caller still
        // carries the full text.
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
        minWidth: 0,
        cursor: isDisabled ? "not-allowed" : "pointer",
        opacity: isDisabled && !loading ? 0.45 : 1,
        // No inline `transition` here on purpose: the full press cycle
        // (hover lift -> compress -> spring back) lives in .btn in
        // index.css, and an inline transition would win over it and flatten
        // the whole thing back to a linear fade.
        ...VARIANT_STYLE[variant],
        ...style,
      }}
      {...rest}
    >
      {loading && <Spinner size={12} />}
      {children}
    </button>
  );
});
