import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

const CHORD_WINDOW_MS = 700;

/**
 * GitHub-style "g then x" navigation chords (g d / g j) plus "?" to
 * open the shortcuts help overlay. Ignored while focus is inside a text
 * input/textarea/select so it never fights with typing a job config or a
 * filter value.
 */
export function useKeyboardShortcuts(onOpenHelp: () => void) {
  const navigate = useNavigate();
  const chordArmed = useRef(false);
  const chordTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    function isTypingTarget(el: EventTarget | null): boolean {
      if (!(el instanceof HTMLElement)) return false;
      const tag = el.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
    }

    function onKeyDown(e: KeyboardEvent) {
      if (isTypingTarget(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "?") {
        e.preventDefault();
        onOpenHelp();
        return;
      }

      if (chordArmed.current) {
        chordArmed.current = false;
        window.clearTimeout(chordTimer.current);
        if (e.key === "d") {
          e.preventDefault();
          navigate("/");
        } else if (e.key === "j") {
          e.preventDefault();
          navigate("/jobs");
        }
        return;
      }

      if (e.key === "g") {
        chordArmed.current = true;
        chordTimer.current = window.setTimeout(() => {
          chordArmed.current = false;
        }, CHORD_WINDOW_MS);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.clearTimeout(chordTimer.current);
    };
  }, [navigate, onOpenHelp]);
}
