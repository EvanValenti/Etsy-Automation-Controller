/**
 * Why an API call failed, in terms an operator (or a developer with a
 * half-configured environment) can act on.
 *
 * "Controller API unreachable" was true but useless: it covered a backend
 * that isn't running, a backend running on a different port than
 * VITE_API_BASE_URL points at, a missing .env.local entirely, and a
 * frontend that was never restarted after that file changed. Those have
 * four different fixes, and the message named none of them.
 *
 * This module owns the classification and the suggested checks. It reads
 * environment configuration but performs no I/O, so it stays testable and
 * has no opinion about how the result is rendered.
 */

/** The value the app falls back to when VITE_API_BASE_URL isn't set.
 * Mirrors web/.env.example and the port the README documents. */
export const FALLBACK_API_BASE_URL = "http://127.0.0.1:8123";

export type ApiFailureKind =
  | "backend_unreachable"
  | "config_missing"
  | "config_invalid"
  | "timeout"
  | "http_error";

export interface ApiEnvironment {
  /** Raw VITE_API_BASE_URL as configured, or undefined when unset. */
  configured: string | undefined;
  /** What the client actually uses (configured, else the fallback). */
  effective: string;
  /** True when nothing was configured and the fallback is in play. */
  usingFallback: boolean;
  /** Set when a value WAS configured but isn't a usable absolute URL. */
  invalidReason: string | null;
  isDevelopment: boolean;
}

/** Resolves the environment once, defensively: a configured-but-broken
 * value is reported rather than silently producing malformed requests. */
export function readApiEnvironment(
  configured: string | undefined,
  isDevelopment: boolean,
): ApiEnvironment {
  const trimmed = configured?.trim();

  if (!trimmed) {
    return {
      configured: undefined,
      effective: FALLBACK_API_BASE_URL,
      usingFallback: true,
      invalidReason: null,
      isDevelopment,
    };
  }

  let invalidReason: string | null = null;
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      invalidReason = `Protocol "${url.protocol}" is not http or https.`;
    }
  } catch {
    invalidReason = "Not an absolute URL (expected something like http://127.0.0.1:8123).";
  }

  return {
    configured: trimmed,
    // A broken value must not be used to build request URLs -- fall back
    // so the app still has a chance of working, and say so loudly.
    effective: invalidReason ? FALLBACK_API_BASE_URL : trimmed,
    usingFallback: invalidReason !== null,
    invalidReason,
    isDevelopment,
  };
}

export interface ApiDiagnostic {
  kind: ApiFailureKind;
  /** One-line headline. */
  title: string;
  /** One or two sentences an operator can read. */
  summary: string;
  /** Ordered, concrete things to check. Development mode only. */
  checks: string[];
  /** Key/value facts about the attempt. Development mode only. */
  facts: { label: string; value: string }[];
}

export interface ApiFailureContext {
  kind: "http" | "timeout" | "network";
  /** Path portion, e.g. "/engines". */
  path: string;
  /** Full URL actually requested. */
  url: string;
  status: number | null;
  /** Server-supplied message, when a response was received. */
  serverMessage: string | null;
  timeoutMs: number;
  environment: ApiEnvironment;
}

const ENV_VAR = "VITE_API_BASE_URL";

/**
 * Classifies one failure and produces the matching guidance.
 *
 * Ordering matters: a misconfigured or missing base URL is diagnosed
 * before "backend not running", because a developer whose .env.local is
 * empty will otherwise go hunting for a server that is, in fact, already
 * running on a port the frontend was never told about.
 */
export function diagnoseApiFailure(ctx: ApiFailureContext): ApiDiagnostic {
  const { environment: env } = ctx;
  const facts: { label: string; value: string }[] = [
    { label: "Attempted endpoint", value: ctx.url },
    { label: "Configured endpoint", value: env.configured ?? `(unset — using fallback)` },
    { label: "Fallback endpoint", value: FALLBACK_API_BASE_URL },
    { label: "Environment variable", value: ENV_VAR },
    { label: "Response received", value: ctx.status !== null ? "yes" : "no" },
  ];
  if (ctx.status !== null) facts.push({ label: "HTTP status", value: String(ctx.status) });

  if (ctx.kind === "http") {
    return {
      kind: "http_error",
      title: `Request failed (${ctx.status ?? "?"})`,
      summary:
        ctx.serverMessage ??
        `The Controller responded to ${ctx.path} with HTTP ${ctx.status ?? "?"}. The backend is reachable — this is an error from the request itself.`,
      checks: [
        "The backend is running and reachable — this is not a connection problem.",
        "Check the Controller API logs for the traceback behind this status.",
        ctx.status === 404
          ? "A 404 on a route that should exist usually means the API server predates the route — restart it."
          : "Retry once; if it persists the request itself is being rejected.",
      ],
      facts,
    };
  }

  if (ctx.kind === "timeout") {
    return {
      kind: "timeout",
      title: "Request timed out",
      summary: `No response from ${ctx.url} within ${ctx.timeoutMs}ms. The backend may be reachable but slow to answer this call.`,
      checks: [
        "Is the backend busy with a long-running engine call? Adapter calls spawn a Python subprocess and are slow on first use.",
        "Retry — a cold server often answers the second request quickly.",
        "If every request times out, the process may be wedged; restart the backend.",
      ],
      facts,
    };
  }

  // Nothing answered at all. Which of the several possible causes it is
  // depends entirely on how the base URL was configured.
  if (env.invalidReason) {
    return {
      kind: "config_invalid",
      title: `${ENV_VAR} is not a valid URL`,
      summary: `${ENV_VAR} is set to "${env.configured}", which can't be used: ${env.invalidReason} The app fell back to ${FALLBACK_API_BASE_URL}, which also did not answer.`,
      checks: [
        `Set ${ENV_VAR} in web/.env.local to an absolute URL, e.g. ${FALLBACK_API_BASE_URL}`,
        "Restart the Vite dev server — .env files are read at startup, not per request.",
        "Confirm the backend is running on that same port.",
      ],
      facts,
    };
  }

  if (env.usingFallback) {
    return {
      kind: "config_missing",
      title: `${ENV_VAR} is not set`,
      summary: `No ${ENV_VAR} is configured, so the app tried its fallback ${FALLBACK_API_BASE_URL} and got no response. Either the backend isn't running, or it's on a different port.`,
      checks: [
        `Create web/.env.local with ${ENV_VAR}=<your backend URL> (see web/.env.example).`,
        `Or start the backend on the fallback port: uvicorn api.main:app --app-dir . --host 127.0.0.1 --port 8123`,
        "Restart the Vite dev server after creating or editing .env.local.",
      ],
      facts,
    };
  }

  return {
    kind: "backend_unreachable",
    title: "Backend not responding",
    summary: `Nothing answered at ${env.effective}. The Controller API is either not running, or running on a different port than ${ENV_VAR} points at.`,
    checks: [
      "Is the backend running? Start it with: uvicorn api.main:app --app-dir . --host 127.0.0.1 --port 8123",
      `Does the backend's port match ${ENV_VAR} (${env.effective})? Note that plain \`uvicorn api.main:app\` defaults to port 8000, not 8123.`,
      "Did you restart the Vite dev server after changing web/.env.local?",
    ],
    facts,
  };
}
