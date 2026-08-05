import { useEffect, useRef, useState } from "react";
import { getAiImagePromptDetail } from "../../api/client";
import type { AiImageMediaCategory, AiImagePromptDetail } from "../../api/types";
import { Spinner } from "../AsyncState";
import { Button } from "../Button";

function friendlyError(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}

/**
 * Read-only viewer for one prompt.
 *
 * The operator view is only what actually gets sent and reviewed: the
 * system prompt (when there is one), the user prompt, and the reference
 * images. Everything else the package carries -- prompt/schema versions,
 * system prompt paths, output folders, compiler and debug metadata -- is
 * real and still available, but behind Developer Details, because showing
 * the raw prompt package as if it WERE the prompt made an operator scroll
 * past twenty fields of bookkeeping to find the two paragraphs they were
 * meant to check.
 *
 * Each prompt body is its own <textarea readOnly>, which is what makes
 * Ctrl+A select that prompt alone rather than the whole page.
 */
export function PromptViewer({
  jobName,
  category,
  conceptId,
}: {
  jobName: string;
  category: AiImageMediaCategory;
  conceptId: string;
}) {
  const [detail, setDetail] = useState<AiImagePromptDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    getAiImagePromptDetail(jobName, category, conceptId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err) => {
        if (!cancelled) setError(friendlyError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [jobName, category, conceptId]);

  if (error) return <div style={{ fontSize: 13, color: "var(--state-failure)" }}>{error}</div>;
  if (!detail) {
    return (
      <div style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 8 }}>
        <Spinner size={12} /> Loading prompt…
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 10 }}>
      {detail.system_prompt && (
        <PromptSection label="System Prompt" body={detail.system_prompt} />
      )}
      <PromptSection label="User Prompt" body={detail.user_prompt} />

      <div>
        <div style={{ fontSize: "var(--text-base)", fontWeight: 700, marginBottom: 6 }}>Reference Images</div>
        {detail.reference_images.length === 0 ? (
          <div style={{ fontSize: 13, color: "var(--text-dim)" }}>None attached to this prompt.</div>
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: "var(--text-secondary)" }}>
            {detail.reference_images.map((name) => (
              <li key={name} className="mono">
                {name}
              </li>
            ))}
          </ul>
        )}
      </div>

      <details>
        <summary className="label">Developer Details</summary>
        <pre
          className="mono"
          style={{
            fontSize: "var(--text-xs)",
            color: "var(--text-secondary)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            background: "var(--panel-raised)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: 10,
            maxHeight: 320,
            overflowY: "auto",
            margin: "8px 0 0",
          }}
        >
          {JSON.stringify(detail.technical_metadata, null, 2)}
        </pre>
      </details>
    </div>
  );
}

/** One prompt body: read-only, scrollable, independently selectable, with
 * its own copy button so the operator never has to select text by hand. */
function PromptSection({ label, body }: { label: string; body: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(body);
    } catch {
      // Clipboard permission can be denied; selecting the text is the
      // fallback that always works.
      ref.current?.select();
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
        <span style={{ fontSize: "var(--text-base)", fontWeight: 700 }}>{label}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{body.length.toLocaleString()} chars</span>
          <Button variant="outline" onClick={copy}>
            {copied ? "Copied ✓" : "Copy"}
          </Button>
        </div>
      </div>
      <textarea
        ref={ref}
        readOnly
        value={body}
        // Ctrl+A inside a textarea selects only this field's contents,
        // which is exactly the scoping we want per prompt.
        onFocus={(e) => e.currentTarget.setSelectionRange(0, 0)}
        rows={12}
        className="mono"
        style={{
          width: "100%",
          background: "var(--panel-raised)",
          border: "1px solid var(--border)",
          color: "var(--text-secondary)",
          borderRadius: "var(--radius)",
          padding: 12,
          fontSize: 12,
          lineHeight: 1.5,
          resize: "vertical",
        }}
      />
    </div>
  );
}
