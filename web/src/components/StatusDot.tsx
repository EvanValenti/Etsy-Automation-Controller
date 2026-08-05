interface StatusDotProps {
  color: string;
  pulse?: boolean;
  size?: number;
}

/** A small glowing LED — the console's recurring visual vocabulary for
 * "state of a thing," reused for engine health, job status, and queue
 * wait reasons rather than three different iconography systems.
 *
 * When pulsing it does two things at once: the dot itself dims and shrinks
 * (see `pulse` in index.css), and a halo expands out of it and fades. One
 * without the other reads as a flat blink; together it reads as an
 * indicator light with something behind it. */
export function StatusDot({ color, pulse = false, size = 8 }: StatusDotProps) {
  return (
    <span
      className={pulse ? "dot-halo" : undefined}
      style={{
        position: "relative",
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "var(--radius-pill)",
        background: color,
        boxShadow: `0 0 6px 1px ${color}`,
        animation: pulse ? "pulse 1.8s ease-in-out infinite" : undefined,
        flexShrink: 0,
      }}
    />
  );
}
