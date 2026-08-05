import { useEffect, useRef, useState } from "react";

/**
 * Keeps a conditionally-rendered overlay (dialog, drawer) mounted for
 * `exitMs` after its `open` flag flips false, so the close plays the
 * dialog-out/overlay-out keyframes in index.css instead of the element
 * vanishing on the frame the state changes. Mirrors the enter animation
 * already on these overlays, just for the direction that was missing one.
 */
export function useAnimatedPresence(open: boolean, exitMs = 100) {
  const [mounted, setMounted] = useState(open);
  const [closing, setClosing] = useState(false);
  // Read synchronously inside the effect below instead of depending on
  // `mounted` state, so the effect only needs to re-run when `open` itself
  // changes.
  const mountedRef = useRef(open);
  mountedRef.current = mounted;

  useEffect(() => {
    if (open) {
      setMounted(true);
      setClosing(false);
      return;
    }
    if (mountedRef.current) setClosing(true);
  }, [open]);

  useEffect(() => {
    if (!closing) return;
    const timer = window.setTimeout(() => {
      setMounted(false);
      setClosing(false);
    }, exitMs);
    return () => window.clearTimeout(timer);
  }, [closing, exitMs]);

  return { mounted, closing };
}
