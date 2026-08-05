import { useEffect, useRef, useState } from "react";

/**
 * Critically-damped spring step response (zeta = 1), the JS twin of the
 * --spring-smooth linear() curve in index.css. Numbers use the non-
 * overshooting spring on purpose: a count that sails past 89 to 94 and
 * comes back reads as a glitch in a metric, however good it looks on a
 * decorative element.
 */
function springProgress(t: number): number {
  if (t >= 1) return 1;
  const w0 = 6.9; // settles to ~0.1% at t = 1
  return 1 - Math.exp(-w0 * t) * (1 + w0 * t);
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

interface CountUpOptions {
  /** First paint counts from zero -- the one moment the number performs. */
  mountDuration?: number;
  /** Later changes are a smaller move and shouldn't re-run the whole show. */
  updateDuration?: number;
}

/**
 * Animates a number toward `target`, returning the in-flight value.
 *
 * Retargets mid-flight rather than restarting: when a poll lands while the
 * previous count is still running, the animation continues from wherever it
 * currently is instead of snapping back to the old value first.
 *
 * Returns `null` while `target` is null, so a loading tile can render its
 * own placeholder rather than counting up from zero to a number nobody has
 * fetched yet.
 */
export function useCountUp(target: number | null, options: CountUpOptions = {}): number | null {
  const { mountDuration = 950, updateDuration = 520 } = options;

  const [display, setDisplay] = useState<number | null>(target === null ? null : 0);
  const frameRef = useRef<number | undefined>(undefined);
  // The live value, read synchronously when a new target arrives so the
  // next animation can start from the current on-screen number.
  const currentRef = useRef(0);
  const hasAnimatedRef = useRef(false);

  useEffect(() => {
    if (target === null) {
      setDisplay(null);
      return;
    }

    // Skip the animation entirely when nobody can see it. A hidden tab
    // suspends requestAnimationFrame, so without this the count would sit
    // frozen at its start value until the tab was focused again -- the
    // metric would read as stale rather than as animating.
    if (prefersReducedMotion() || document.hidden) {
      currentRef.current = target;
      setDisplay(target);
      hasAnimatedRef.current = true;
      return;
    }

    const from = currentRef.current;
    const to = target;
    if (from === to) {
      setDisplay(to);
      return;
    }

    // Seed the starting value synchronously. Without this the first painted
    // frame still carries the previous state (null on mount), so a tile
    // would flash its "no data" em dash before the first rAF callback.
    setDisplay(from);

    const duration = hasAnimatedRef.current ? updateDuration : mountDuration;
    hasAnimatedRef.current = true;
    const start = performance.now();

    function tick(now: number) {
      const elapsed = (now - start) / duration;
      const eased = springProgress(elapsed);
      const value = from + (to - from) * eased;
      currentRef.current = value;
      setDisplay(value);
      if (elapsed < 1) {
        frameRef.current = requestAnimationFrame(tick);
      } else {
        currentRef.current = to;
        setDisplay(to);
      }
    }

    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== undefined) cancelAnimationFrame(frameRef.current);
    };
  }, [target, mountDuration, updateDuration]);

  // Never report "no value" while a real one exists. If the animation hasn't
  // produced a frame yet -- throttled rAF, an interrupted mount -- fall
  // through to the true number rather than rendering the loading placeholder
  // on top of data we already have.
  if (target === null) return null;
  return display ?? target;
}
