import { useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { getHealth } from "../api/client";
import { useKeyboardShortcuts } from "../hooks/useKeyboardShortcuts";
import { usePolling } from "../hooks/usePolling";
import { ShortcutsHelp } from "./ShortcutsHelp";
import { StatusDot } from "./StatusDot";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/jobs", label: "Jobs" },
  { to: "/listing-assets", label: "Listing Assets" },
];

function ConnectionIndicator() {
  const { data, error, loading } = usePolling(getHealth, 10000);
  const online = !!data && !error;
  const color = loading && !data && !error ? "var(--state-idle)" : online ? "var(--state-success)" : "var(--state-offline)";
  const label = loading && !data && !error ? "Connecting" : online ? "API Online" : "API Unreachable";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-secondary)" }}>
      <StatusDot color={color} pulse={online} size={7} />
      {label}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const [helpOpen, setHelpOpen] = useState(false);
  const location = useLocation();
  useKeyboardShortcuts(() => setHelpOpen(true));

  return (
    <div className="app-shell" style={{ display: "flex", minHeight: "100vh" }}>
      <aside
        className="app-sidebar"
        style={{
          width: 216,
          flexShrink: 0,
          borderRight: "1px solid var(--border)",
          background: "var(--panel)",
          display: "flex",
          flexDirection: "column",
          position: "sticky",
          top: 0,
          height: "100vh",
        }}
      >
        <div className="app-sidebar-brand" style={{ padding: "20px 18px 16px", borderBottom: "1px solid var(--border)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span
              aria-hidden
              style={{
                width: 22,
                height: 22,
                borderRadius: "var(--radius)",
                background: "var(--brand-deep)",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#fff",
                fontSize: 12,
                fontWeight: 700,
                letterSpacing: "-0.02em",
                flexShrink: 0,
              }}
            >
              V
            </span>
            <span style={{ fontWeight: 600, fontSize: 15, letterSpacing: "-0.015em" }}>Etsy Controller</span>
          </div>
          <div style={{ marginTop: 3, fontSize: 12, color: "var(--text-dim)" }}>Vilicity Automation</div>
        </div>

        <nav style={{ padding: "12px 10px", display: "flex", flexDirection: "column", gap: 2 }}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              // is-active drives the left rail in index.css, which grows on
              // the bounce spring when the route changes -- a moving edge is
              // far easier to catch at a glance than a background tint.
              className={({ isActive }) => ["sidebar-navlink", isActive ? "is-active" : ""].filter(Boolean).join(" ")}
              style={({ isActive }) => ({
                display: "block",
                padding: "8px 12px",
                borderRadius: "var(--radius)",
                textDecoration: "none",
                fontSize: 13,
                fontWeight: isActive ? 550 : 450,
                letterSpacing: "0.005em",
                color: isActive ? "var(--text-primary)" : "var(--text-secondary)",
                // Only the active route sets background inline -- leaving it
                // unset (rather than "transparent") when inactive lets the
                // .sidebar-navlink:hover rule in index.css apply, since an
                // inline style always beats a stylesheet hover rule.
                ...(isActive ? { background: "var(--brand-wash)" } : {}),
                // No inline transition: .sidebar-navlink owns it, including
                // the transform used by :active.
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div
          className="app-sidebar-footer"
          style={{ marginTop: "auto", padding: "16px 18px", borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 10 }}
        >
          <ConnectionIndicator />
          <button
            onClick={() => setHelpOpen(true)}
            className="btn-quiet"
            style={{
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
            Press <span style={{ color: "var(--text-secondary)" }}>?</span> for shortcuts
          </button>
        </div>
      </aside>

      <main className="app-main" style={{ flex: 1, minWidth: 0, padding: "24px 30px 26px" }}>
        {/* Keyed by pathname so React remounts this wrapper on navigation,
            replaying the page-in animation once per route change rather
            than on every poll-driven re-render within a page. */}
        <div key={location.pathname} className="page-transition">
          {children}
        </div>
      </main>

      <ShortcutsHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
