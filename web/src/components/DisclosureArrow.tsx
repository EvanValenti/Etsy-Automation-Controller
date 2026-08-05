import { ChevronRight } from "lucide-react";

/**
 * The one disclosure control used by every expandable surface in the
 * console -- jobs, workflow sections, tree/folder-style lists.
 *
 * Two things it standardizes that were previously per-component guesses:
 *  - a ~32x32px hit target, so the arrow is comfortably clickable rather
 *    than a 10px glyph an operator has to aim at;
 *  - one rotating chevron (right = collapsed, down = expanded) instead of
 *    each surface picking its own glyph pair. Rotation is a CSS transform
 *    on one icon, so the two states are the same shape turning, not two
 *    different symbols swapping.
 *
 * The icon is a lucide ChevronRight rather than the "▶" character it used
 * to be: that glyph came from whatever font the OS resolved it in, so it
 * rendered as a heavy filled triangle next to lucide's 2px strokes
 * everywhere else, at a size the app could not control.
 *
 * Purely presentational: it renders a <span>, never a <button>, so it can
 * sit inside a larger clickable row (a table cell, a <summary>) without
 * nesting interactive elements. The surrounding row owns the click
 * handling and the accessible name.
 */
export function DisclosureArrow({ open, size = 32 }: { open: boolean; size?: number }) {
  return (
    <span
      aria-hidden
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        flexShrink: 0,
        color: "var(--text-secondary)",
        lineHeight: 1,
        transform: open ? "rotate(90deg)" : "rotate(0deg)",
        transition: "transform var(--spring-dur-snap) var(--spring-snap)",
        userSelect: "none",
      }}
    >
      <ChevronRight size={14} strokeWidth={2.4} />
    </span>
  );
}
