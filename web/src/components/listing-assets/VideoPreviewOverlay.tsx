import { useEffect } from "react";

/**
 * In-app video preview.
 *
 * Deliberately a <video> element inside the Controller, not a download,
 * an Explorer window, or a new browser tab: previewing an asset while
 * deciding whether to include it must not take the operator out of the
 * page they are assembling. Mirrors the image preview overlay already
 * used by the AI Image Generator's review gallery, so both media types
 * behave the same way.
 */
export function VideoPreviewOverlay({ url, title, onClose }: { url: string; title: string; onClose: () => void }) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-label={`Preview: ${title}`}
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.86)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 12,
        zIndex: 60,
        padding: 24,
        animation: "overlay-in 0.15s ease-out",
      }}
    >
      {/* Stop propagation so using the player's own controls doesn't
          dismiss the overlay. */}
      <video
        controls
        autoPlay
        src={url}
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: "100%", maxHeight: "80vh", background: "#000", borderRadius: "var(--radius)" }}
      />
      <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>
        {title} — click outside the player or press Esc to close
      </div>
    </div>
  );
}
