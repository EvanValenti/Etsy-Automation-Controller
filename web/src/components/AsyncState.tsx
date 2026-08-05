import type { ReactNode } from "react";
import { TriangleAlert } from "lucide-react";
import { ApiError } from "../api/client";
import { Button } from "./Button";

/** A faster spinner makes the same wait feel shorter -- perceived
 * performance, not actual. 0.55s is about the floor before the ring stops
 * reading as a rotation and starts reading as a flicker. */
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: size,
        height: size,
        border: "2px solid var(--border-bright)",
        borderTopColor: "var(--accent)",
        // A second lit segment turns the ring from one travelling dot into a
        // sweep, which is what makes the rotation legible at this speed.
        borderRightColor: "color-mix(in srgb, var(--accent) 45%, transparent)",
        borderRadius: "var(--radius-pill)",
        animation: "spin 0.55s linear infinite",
      }}
    />
  );
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text-dim)", padding: "20px 0", fontFamily: "var(--font-mono)", fontSize: 12 }}>
      <Spinner />
      {label}…
    </div>
  );
}

function describeError(error: Error): { title: string; hint: string } {
  if (error instanceof ApiError) {
    // The diagnostic already classified this precisely (backend down vs.
    // wrong port vs. unset env var vs. timeout vs. HTTP error) -- prefer
    // it over re-deriving a vaguer message here.
    if (error.diagnostic) {
      return { title: error.diagnostic.title, hint: error.diagnostic.summary };
    }
    if (error.status === 404) {
      return { title: "Not found", hint: error.message };
    }
    return { title: `Request failed (${error.status ?? "?"})`, hint: error.message };
  }
  return { title: "Unexpected error", hint: error.message };
}

export function ErrorBlock({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const { title, hint } = describeError(error);
  // Checks and attempt facts are a development aid: in production the
  // headline plus summary is the whole message.
  const diagnostic = error instanceof ApiError ? error.diagnostic : null;
  const showDetail = diagnostic !== null && import.meta.env.DEV;
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "16px",
        border: "1px solid color-mix(in srgb, var(--state-failure) 40%, var(--border))",
        background: "color-mix(in srgb, var(--state-failure) 8%, transparent)",
        borderRadius: "var(--radius)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--state-failure)", fontSize: "var(--text-base)", fontWeight: 600 }}>
        <TriangleAlert size={14} strokeWidth={2.2} aria-hidden style={{ flexShrink: 0 }} />
        {title}
      </div>
      <div style={{ color: "var(--text-secondary)", fontSize: "var(--text-base)" }}>{hint}</div>

      {showDetail && diagnostic.checks.length > 0 && (
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 4 }}>Try this</div>
          <ol style={{ margin: 0, paddingLeft: 20, color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.6 }}>
            {diagnostic.checks.map((check) => (
              <li key={check}>{check}</li>
            ))}
          </ol>
        </div>
      )}

      {showDetail && (
        <details>
          <summary className="label">Diagnostics</summary>
          <table style={{ borderCollapse: "collapse", marginTop: 8, fontSize: "var(--text-sm)" }}>
            <tbody>
              {diagnostic.facts.map((fact) => (
                <tr key={fact.label}>
                  <td style={{ padding: "3px 12px 3px 0", color: "var(--text-dim)", whiteSpace: "nowrap", verticalAlign: "top" }}>
                    {fact.label}
                  </td>
                  <td className="mono" style={{ padding: "3px 0", color: "var(--text-secondary)", wordBreak: "break-all" }}>
                    {fact.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {/* The shared Button, not a bespoke one. This was the app's only
          uppercase tracked-out mono control, so the single button an
          operator meets at the worst moment -- something just failed --
          was also the one that looked like it came from a different app. */}
      {onRetry && (
        <Button variant="danger" onClick={onRetry} style={{ alignSelf: "flex-start" }}>
          Retry
        </Button>
      )}
    </div>
  );
}

/** An empty panel should read as idle, not broken. `empty-idle` drifts the
 * dashed border and breathes the text on long, offset cycles (7s / 5s) --
 * slow enough that it never pulls the eye off real content, present enough
 * that the surface isn't a dead rectangle. */
export function EmptyBlock({ children }: { children: ReactNode }) {
  return (
    <div
      className="empty-idle"
      style={{
        color: "var(--text-dim)",
        fontSize: "var(--text-sm)",
        fontFamily: "var(--font-mono)",
        padding: "18px 4px",
        textAlign: "center",
        border: "1px dashed var(--border)",
        borderRadius: "var(--radius)",
      }}
    >
      {/* Wrapped so the breathe applies to the text -- .empty-idle > *
          targets children, keeping the border drift and the text fade on
          independent cycles rather than pulsing as one block. */}
      <span style={{ display: "inline-block" }}>{children}</span>
    </div>
  );
}
