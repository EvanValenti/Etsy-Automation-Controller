import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  copyAiImageManualPrompt,
  exportAiImageManualConcepts,
  importAiImageFinishedImages,
  importAiImageManualConcepts,
  importAiImageManualImage,
  listAiImageManualPrompts,
} from "../../api/client";
import type { AiImageManualPromptSummary, AiImageMediaCategory } from "../../api/types";
import { Button } from "../Button";
import { EmptyBlock, Spinner } from "../AsyncState";
import { Select } from "../Select";

// Remembering the operator's last choice, per this job's own answer to
// "which category is this batch?" -- there is no per-category section in
// this panel for it to default from.
const FINISHED_CATEGORY_KEY = "controller.manualImport.finishedCategory";

const FINISHED_CATEGORY_OPTIONS: { value: AiImageMediaCategory; label: string }[] = [
  { value: "ai_product_mockup", label: "AI Product Mockup" },
  { value: "lifestyle_mockup", label: "Lifestyle" },
];

function readLastFinishedCategory(): AiImageMediaCategory {
  try {
    const stored = window.localStorage.getItem(FINISHED_CATEGORY_KEY);
    if (stored === "ai_product_mockup" || stored === "lifestyle_mockup") return stored;
  } catch {
    /* storage unavailable -- fall through to the default */
  }
  return "ai_product_mockup";
}

function friendlyError(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}

async function copyToBrowserClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Manual Mode infrastructure: the Controller never interprets a concept,
 * a prompt, or an image here -- every action is a thin pass-through to
 * the engine's own real manual-workflow functions (see
 * infra/adapters/image_generator/adapter.py's "Manual Mode" section).
 * Once an artifact lands back on disk (an imported concept response, a
 * manually-generated image), the pipeline state converges exactly as if
 * the automatic path had produced it -- this panel doesn't need to (and
 * doesn't) special-case that.
 */
export function ManualModePanel({
  jobName,
  refreshSignal,
  onChanged,
}: {
  jobName: string;
  /** Bumped by the parent whenever the job's pipeline state changes for
   * any reason (advance, or this panel's own actions) -- e.g. Build
   * Prompts running (via "Advance / Resume", not this panel) makes new
   * prompts eligible, and this panel has no other way to notice since
   * jobName itself doesn't change. */
  refreshSignal: string;
  onChanged: () => void;
}) {
  const [conceptExportText, setConceptExportText] = useState<string | null>(null);
  const [conceptExportError, setConceptExportError] = useState<string | null>(null);
  const [conceptExporting, setConceptExporting] = useState(false);
  const [conceptCopied, setConceptCopied] = useState(false);
  const conceptCopiedTimer = useRef<number | null>(null);

  const [importText, setImportText] = useState("");
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const [prompts, setPrompts] = useState<AiImageManualPromptSummary[] | null>(null);
  const [promptsError, setPromptsError] = useState<string | null>(null);
  const [copiedFor, setCopiedFor] = useState<string | null>(null);
  const [justCopiedFor, setJustCopiedFor] = useState<string | null>(null);
  const justCopiedTimer = useRef<number | null>(null);
  const [promptMessage, setPromptMessage] = useState<string | null>(null);

  const COPIED_RESET_MS = 2000;

  useEffect(() => {
    return () => {
      if (conceptCopiedTimer.current) window.clearTimeout(conceptCopiedTimer.current);
      if (justCopiedTimer.current) window.clearTimeout(justCopiedTimer.current);
    };
  }, []);

  const [uploadingFor, setUploadingFor] = useState<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Finished-image import. The category persists across jobs and sessions:
  // an operator working through a batch of lifestyle shots would otherwise
  // reset to the default on every job and silently file the next batch in
  // the wrong category.
  const [finishedCategory, setFinishedCategory] = useState<AiImageMediaCategory>(readLastFinishedCategory);
  const [finishedFiles, setFinishedFiles] = useState<File[]>([]);
  const [importingFinished, setImportingFinished] = useState(false);
  const [finishedMessage, setFinishedMessage] = useState<string | null>(null);
  const [finishedError, setFinishedError] = useState<string | null>(null);
  const finishedInputRef = useRef<HTMLInputElement>(null);

  function chooseFinishedCategory(category: AiImageMediaCategory) {
    setFinishedCategory(category);
    try {
      window.localStorage.setItem(FINISHED_CATEGORY_KEY, category);
    } catch {
      /* private mode / storage disabled -- the selection still works for this session */
    }
  }

  async function handleImportFinishedImages() {
    if (finishedFiles.length === 0) return;
    setImportingFinished(true);
    setFinishedError(null);
    setFinishedMessage(null);
    try {
      const result = await importAiImageFinishedImages(jobName, finishedCategory, finishedFiles);
      setFinishedMessage(
        `Imported ${result.count} image${result.count === 1 ? "" : "s"} — this job is complete.`,
      );
      setFinishedFiles([]);
      if (finishedInputRef.current) finishedInputRef.current.value = "";
      onChanged();
    } catch (err) {
      setFinishedError(friendlyError(err));
    } finally {
      setImportingFinished(false);
    }
  }

  function refreshPrompts() {
    listAiImageManualPrompts(jobName)
      .then(setPrompts)
      .catch((err) => {
        // The engine's own message here ("No prompt packages exist for
        // this job... Next step: ...") is already clear and operator-
        // facing -- strip the Controller route's own technical-sounding
        // "Could not list manual prompt packages: " prefix rather than
        // showing both layered together.
        const raw = friendlyError(err);
        setPromptsError(raw.replace(/^Could not list manual prompt packages: /, ""));
      });
  }

  useEffect(() => {
    setPromptsError(null);
    refreshPrompts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobName, refreshSignal]);

  async function handleExportConcepts() {
    setConceptExporting(true);
    setConceptExportError(null);
    try {
      const result = await exportAiImageManualConcepts(jobName);
      setConceptExportText(result.prompt_text);
      const copied = await copyToBrowserClipboard(result.prompt_text);
      if (copied) {
        setConceptCopied(true);
        if (conceptCopiedTimer.current) window.clearTimeout(conceptCopiedTimer.current);
        conceptCopiedTimer.current = window.setTimeout(() => setConceptCopied(false), COPIED_RESET_MS);
      }
    } catch (err) {
      setConceptExportError(friendlyError(err));
    } finally {
      setConceptExporting(false);
    }
  }

  async function handleImportConcepts() {
    if (!importText.trim()) return;
    setImporting(true);
    setImportError(null);
    setImportMessage(null);
    try {
      const result = await importAiImageManualConcepts(jobName, importText);
      if (result.imported) {
        setImportMessage(`Imported ${result.ai_count ?? 0} AI + ${result.lifestyle_count ?? 0} Lifestyle concept(s).`);
        setImportText("");
        onChanged();
        refreshPrompts();
      } else if (result.cancelled_overwrite) {
        setImportError("Import declined: existing approved/rejected concepts were not overwritten.");
      } else {
        setImportError("Import did not complete — check the response JSON matches the expected template shape.");
      }
    } catch (err) {
      if (err instanceof ApiError) setImportError(err.message);
      else setImportError(friendlyError(err));
    } finally {
      setImporting(false);
    }
  }

  async function handleCopyPrompt(category: AiImageMediaCategory, conceptId: string) {
    const key = `${category}/${conceptId}`;
    setCopiedFor(key);
    setPromptMessage(null);
    try {
      const result = await copyAiImageManualPrompt(jobName, category, conceptId);
      const copied = await copyToBrowserClipboard(result.prompt_text);
      if (copied) {
        setJustCopiedFor(key);
        if (justCopiedTimer.current) window.clearTimeout(justCopiedTimer.current);
        justCopiedTimer.current = window.setTimeout(() => setJustCopiedFor(null), COPIED_RESET_MS);
      } else {
        setPromptMessage(`Prepared prompt for ${result.concept_name} (clipboard copy failed — see incoming folder).`);
      }
    } catch (err) {
      setPromptMessage(friendlyError(err));
    } finally {
      setCopiedFor(null);
    }
  }

  async function handleUploadImage(category: AiImageMediaCategory, conceptId: string, file: File) {
    const key = `${category}/${conceptId}`;
    setUploadingFor(key);
    setUploadError(null);
    setUploadMessage(null);
    try {
      const result = await importAiImageManualImage(jobName, category, conceptId, file);
      if (result.imported.length > 0) {
        setUploadMessage(`Imported image for ${conceptId}.`);
        onChanged();
        refreshPrompts();
      } else {
        const reason = result.skipped[0]?.reason ?? "Nothing was imported.";
        setUploadError(reason);
      }
    } catch (err) {
      setUploadError(friendlyError(err));
    } finally {
      setUploadingFor(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* Three numbered steps with the expectations stated inline: what to
          copy, where to run it, what to paste back, and what happens on
          import. Previously all of that was one sentence under a button. */}
      <div>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Generate concepts yourself</div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
          Use this when you'd rather run the concept step in your own LLM session than spend Claude API credits here.
          The result is identical either way — the same concepts land in this job.
        </div>

        <div style={{ fontSize: "var(--text-base)", fontWeight: 600, marginBottom: 6 }}>1. Copy the request</div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
          Copies this job's full concept-generation request — product, store, campaign, and the response format to
          follow — to your clipboard.
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Button variant="outline" onClick={handleExportConcepts} loading={conceptExporting}>
            {conceptCopied ? "Copied ✓" : "Copy Request JSON"}
          </Button>
        </div>
        {conceptExportError && <div style={{ fontSize: 13, color: "var(--state-failure)", marginTop: 6 }}>{conceptExportError}</div>}
        {conceptExportText && (
          <div style={{ fontSize: 13, color: "var(--state-success)", marginTop: 6 }}>
            Copied ({conceptExportText.length.toLocaleString()} characters).
          </div>
        )}

        <div style={{ fontSize: "var(--text-base)", fontWeight: 600, margin: "16px 0 6px" }}>2. Run it in any capable LLM</div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
          Paste it into ChatGPT, Claude, or another supported LLM. Ask for the response exactly in the JSON format the
          request specifies — no commentary before or after it. Then copy the JSON it returns.
        </div>

        <div style={{ fontSize: "var(--text-base)", fontWeight: 600, margin: "16px 0 6px" }}>3. Paste the response here</div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
          On import, the concepts are validated against this job's schema and saved as proposed concepts. The workflow
          then moves to Concept Review, where you approve the ones worth building prompts for. Nothing is generated or
          charged by importing.
        </div>

        <div>
          <textarea
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder='Paste the concept response JSON here, e.g. {"ai_product_mockup_concepts": [...], "lifestyle_mockup_concepts": [...]}'
            rows={12}
            className="mono"
            style={{
              width: "100%",
              background: "var(--panel-raised)",
              border: "1px solid var(--border-bright)",
              color: "var(--text-primary)",
              borderRadius: "var(--radius)",
              padding: 12,
              fontSize: "var(--text-sm)",
              resize: "vertical",
              minHeight: 200,
            }}
          />
          <div style={{ marginTop: 10 }}>
            <Button variant="primary" onClick={handleImportConcepts} disabled={!importText.trim()} loading={importing}>
              Import Concepts
            </Button>
          </div>
          {importMessage && <div style={{ fontSize: 13, color: "var(--state-success)", marginTop: 6 }}>{importMessage}</div>}
          {importError && <div style={{ fontSize: 13, color: "var(--state-failure)", marginTop: 6 }}>{importError}</div>}
        </div>
      </div>

      {/* Import finished images. Deliberately just a category, a file
          picker and a button: importing finished images means the operator
          already did the concepts, prompts, generation and selection
          elsewhere, so there is nothing left to explain or to review --
          the job is complete when this returns. */}
      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16 }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 4 }}>Import finished images</div>
        <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 12 }}>
          Already generated the images yourself? Import them here and this job is done.
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Select
            value={finishedCategory}
            onChange={(e) => chooseFinishedCategory(e.target.value as AiImageMediaCategory)}
            aria-label="Category for imported images"
          >
            {FINISHED_CATEGORY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>

          <input
            ref={finishedInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            onChange={(e) => {
              setFinishedFiles(Array.from(e.target.files ?? []));
              setFinishedError(null);
              setFinishedMessage(null);
            }}
            style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}
          />

          <Button
            variant="primary"
            onClick={handleImportFinishedImages}
            disabled={finishedFiles.length === 0}
            loading={importingFinished}
          >
            {finishedFiles.length > 1 ? `Import ${finishedFiles.length} Images` : "Import Images"}
          </Button>
        </div>

        {finishedMessage && <div style={{ fontSize: 13, color: "var(--state-success)", marginTop: 8 }}>{finishedMessage}</div>}
        {finishedError && <div style={{ fontSize: 13, color: "var(--state-failure)", marginTop: 8 }}>{finishedError}</div>}
      </div>

      <div>
        {/* Lives here, inside Manual Workflow, rather than standing on its
            own: generating one image at a time is a step OF the manual
            path, not a separate feature an operator chooses between. */}
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Prompts — one image at a time</div>
        {promptsError && <EmptyBlock>{promptsError}</EmptyBlock>}
        {!prompts && !promptsError && <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Loading…</div>}
        {prompts && prompts.length === 0 && (
          <EmptyBlock>No prompt packages awaiting manual generation — either none are built yet, or every built one already has an image.</EmptyBlock>
        )}
        {prompts && prompts.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {prompts.map((p) => {
              const key = `${p.category}/${p.concept_id}`;
              return (
                <div
                  key={key}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 10,
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius)",
                    padding: "8px 10px",
                    flexWrap: "wrap",
                  }}
                >
                  <div>
                    <div style={{ fontSize: "var(--text-sm)", fontWeight: 600 }}>{p.concept_name || p.concept_id}</div>
                    <div className="mono" style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)" }}>
                      {p.category} / {p.concept_id}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <Button variant="outline" onClick={() => handleCopyPrompt(p.category, p.concept_id)} loading={copiedFor === key}>
                      {justCopiedFor === key ? "Copied ✓" : "Copy Prompt"}
                    </Button>
                    <label
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        border: "1px solid var(--border-bright)",
                        borderRadius: "var(--radius)",
                        padding: "8px 16px",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--text-xs)",
                        letterSpacing: "0.05em",
                        textTransform: "uppercase",
                        color: "var(--text-secondary)",
                        cursor: "pointer",
                      }}
                    >
                      {uploadingFor === key && <Spinner size={12} />}
                      Import Image
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        hidden
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          e.target.value = "";
                          if (file) handleUploadImage(p.category, p.concept_id, file);
                        }}
                      />
                    </label>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {promptMessage && <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 8 }}>{promptMessage}</div>}
        {uploadMessage && <div style={{ fontSize: 11, color: "var(--state-success)", marginTop: 6 }}>{uploadMessage}</div>}
        {uploadError && <div style={{ fontSize: 11, color: "var(--state-failure)", marginTop: 6 }}>{uploadError}</div>}
      </div>
    </div>
  );
}
