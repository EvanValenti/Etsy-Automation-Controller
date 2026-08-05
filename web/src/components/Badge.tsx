import { useEffect, useRef, useState } from "react";
import { StatusDot } from "./StatusDot";

interface BadgeProps {
  color: string;
  label: string;
  pulse?: boolean;
}

/**
 * A status pill that reacts when its status actually changes.
 *
 * The change is the whole point: an operator watching a job go Running ->
 * Completed is looking at this pill at the moment the poll lands, and a
 * silent colour swap is easy to miss entirely. So a real change plays a
 * spring pop plus one expanding ring in the new state's colour, and then
 * stops. Nothing loops here except `pulse`, which marks genuinely live work.
 *
 * Deliberately silent on FIRST render -- otherwise every badge on the
 * dashboard would ring on page load, which is noise, not signal. The card
 * entrance already covers arrival.
 */
export function Badge({ color, label, pulse }: BadgeProps) {
  const previousLabel = useRef(label);
  // Increments only on a real change; stays 0 for the whole first render,
  // which is what gates the mount-time animation off.
  const [changeCount, setChangeCount] = useState(0);

  useEffect(() => {
    if (previousLabel.current !== label) {
      previousLabel.current = label;
      setChangeCount((c) => c + 1);
    }
  }, [label]);

  const hasChanged = changeCount > 0;

  return (
    <span
      className={pulse ? "badge-live" : undefined}
      style={{
        position: "relative",
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        // A fixed 22px height on a pill, so every badge in the app is the
        // same object regardless of its word.
        height: 22,
        padding: "0 10px 0 8px",
        borderRadius: "var(--radius-pill)",
        // No outline. The dot already carries the state colour, so a full
        // ring around it made a one-word status read as loudly as a button.
        border: "1px solid transparent",
        background: `color-mix(in srgb, ${color} 13%, transparent)`,
        color,
        // Consumed by the badge-live breathe loop and the status-ring burst.
        ["--ring-color" as string]: `color-mix(in srgb, ${color} 55%, transparent)`,
        // The pill's own colour still crossfades, so the surface under the
        // popping content moves with it rather than cutting.
        transition: `background var(--dur-base) ease, color var(--dur-base) ease`,
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-xs)",
        fontWeight: 600,
        letterSpacing: "0.015em",
        lineHeight: 1,
        // Never wraps and never compresses. Badge labels are single state
        // words now (Ready / Running / Offline / Error); the previous
        // wrap-anywhere rule was written for long counted labels that no
        // longer exist, and it let "Ready" break mid-word inside a tight
        // card header.
        whiteSpace: "nowrap",
        flexShrink: 0,
      }}
    >
      {/* Remounted on each change so the spring pop replays. */}
      <span
        key={changeCount}
        className={hasChanged ? "status-pop" : undefined}
        style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
      >
        <StatusDot color={color} pulse={pulse} size={6} />
        {label}
      </span>

      {hasChanged && <span key={`ring-${changeCount}`} className="status-ring" aria-hidden />}
    </span>
  );
}
