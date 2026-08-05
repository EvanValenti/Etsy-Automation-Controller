import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useAnimatedPresence } from "../hooks/useAnimatedPresence";
import { Button } from "./Button";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  variant?: "primary" | "danger";
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Blocking confirmation for actions with real side effects — launching an
 * engine subprocess or cancelling one. Escape/backdrop-click cancel;
 * Enter confirms (only while not already loading, so a slow request can't
 * be double-submitted by a stray keypress).
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  variant = "primary",
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const { mounted, closing } = useAnimatedPresence(open);

  useEffect(() => {
    if (!open) return;
    confirmRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter" && !loading) onConfirm();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, loading, onConfirm, onCancel]);

  if (!mounted) return null;

  return createPortal(
    <div
      onClick={onCancel}
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
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(420px, 90vw)",
          background: "var(--panel)",
          border: "1px solid var(--border-bright)",
          borderTop: `2px solid ${variant === "danger" ? "var(--state-failure)" : "var(--accent)"}`,
          borderRadius: "var(--radius)",
          padding: 20,
          animation: closing ? "dialog-out 100ms var(--ease) forwards" : "dialog-in 0.15s ease-out",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
        }}
      >
        <h2 id="confirm-dialog-title" style={{ margin: 0, fontFamily: "var(--font-mono)", fontSize: 14 }}>
          {title}
        </h2>
        <p style={{ margin: "10px 0 20px", color: "var(--text-secondary)", fontSize: "var(--text-sm)", lineHeight: 1.5 }}>
          {description}
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            Cancel
          </Button>
          <Button ref={confirmRef} variant={variant} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
