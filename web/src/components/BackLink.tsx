import { useLocation, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

/**
 * "Back" should return to wherever the operator actually came from
 * (Dashboard, Jobs, an engine page, an activity-feed link -- JobDetail in
 * particular is linked from all of those), not always the same hardcoded
 * destination. `location.key === "default"` is React Router's own signal
 * that this page was loaded directly (a pasted URL, a refresh, or the
 * first page in the tab) rather than navigated to from within the app --
 * only then is there no real "back" to go to, so `to` is used as a
 * sensible landing page instead of leaving the SPA via raw browser
 * history. Label is deliberately generic ("Back", not "Back to Jobs")
 * since the real destination now depends on where the operator came
 * from, not on `to`.
 */
export function BackLink({ to, label = "Back" }: { to: string; label?: string }) {
  const navigate = useNavigate();
  const location = useLocation();

  function handleClick() {
    if (location.key === "default") {
      navigate(to);
    } else {
      navigate(-1);
    }
  }

  return (
    <button
      onClick={handleClick}
      className="btn-quiet"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        background: "none",
        border: "none",
        color: "var(--text-dim)",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-xs)",
        cursor: "pointer",
        textAlign: "left",
        alignSelf: "flex-start",
      }}
    >
      {/* lucide, not "←". The arrow character rendered from whatever font
          the OS resolved it in, at a weight that matched nothing else on
          the page. */}
      <ArrowLeft size={13} strokeWidth={2.2} aria-hidden />
      {label}
    </button>
  );
}
