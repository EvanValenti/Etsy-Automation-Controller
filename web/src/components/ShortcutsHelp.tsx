import { useEffect } from "react";
import { createPortal } from "react-dom";
import { useAnimatedPresence } from "../hooks/useAnimatedPresence";

const SHORTCUTS: [string, string][] = [
  ["g d", "Go to Dashboard"],
  ["g j", "Go to Jobs"],
  ["?", "Show this help"],
  ["Esc", "Close dialog"],
];

function Key({ children }: { children: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 7px",
        marginRight: 2,
        background: "var(--panel-raised)",
        border: "1px solid var(--border-bright)",
        borderRadius: 3,
        fontFamily: "var(--font-mono)",
        fontSize: 11,
      }}
    >
      {children}
    </span>
  );
}

export function ShortcutsHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { mounted, closing } = useAnimatedPresence(open);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!mounted) return null;

  return createPortal(
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(4, 5, 6, 0.72)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        animation: closing ? "overlay-out 100ms var(--ease) forwards" : "overlay-in 0.12s ease-out",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(320px, 90vw)",
          background: "var(--panel)",
          border: "1px solid var(--border-bright)",
          borderTop: "2px solid var(--accent)",
          borderRadius: "var(--radius)",
          padding: 20,
          animation: closing ? "dialog-out 100ms var(--ease) forwards" : "dialog-in 0.15s ease-out",
        }}
      >
        <h2 style={{ margin: "0 0 14px", fontFamily: "var(--font-mono)", fontSize: 13 }}>Keyboard Shortcuts</h2>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {SHORTCUTS.map(([keys, label]) => (
            <div key={keys} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "var(--text-sm)" }}>
              <span style={{ color: "var(--text-secondary)" }}>{label}</span>
              <span>
                {keys.split(" ").map((k) => (
                  <Key key={k}>{k}</Key>
                ))}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
