import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, fetchVideoPresets, launchVideoJob, listJobs, openAiImageOutputFolder, openMockupOutputFolder } from "../../api/client";
import type { Job, VideoPreset } from "../../api/types";
import { Button } from "../Button";
import { EmptyBlock } from "../AsyncState";

const ENGINE_ID = "etsy-video-generator";
const MOCKUP_ENGINE_ID = "etsy-mockup-generator";
const IMAGE_ENGINE_ID = "etsy-ai-image-generator";
const MIN_IMAGES = 3;
const MAX_IMAGES = 5;
const ALLOWED_EXTENSIONS = [".png", ".jpg", ".jpeg"];
const POLL_INTERVAL_MS = 500;
const MAX_POLL_ATTEMPTS = 40; // ~20s safety cap — well past the normal <1s job-creation latency

/** Most recently updated Job in `jobs`, or null if empty. Used to find
 * "the newest completed job" for a given engine -- Controller Job
 * timestamps are the only ordering signal available here (engine-side
 * job manifests carry no created/updated timestamp of their own). */
function latestByUpdatedAt(jobs: Job[]): Job | null {
  if (jobs.length === 0) return null;
  return jobs.reduce((latest, j) => ((j.updated_at ?? "") > (latest.updated_at ?? "") ? j : latest));
}

let localIdCounter = 0;
function nextLocalId(): string {
  localIdCounter += 1;
  return `local-${localIdCounter}`;
}

interface StagedImage {
  localId: string;
  file: File;
  previewUrl: string;
}

function isSupportedImageFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

const dropzoneBase: React.CSSProperties = {
  border: "1px dashed var(--border-bright)",
  borderRadius: "var(--radius)",
  padding: "22px 16px",
  textAlign: "center",
  color: "var(--text-dim)",
  fontSize: "var(--text-sm)",
  transition: "border-color 0.15s ease, background 0.15s ease",
};

export function VideoLaunchWorkflow() {
  const navigate = useNavigate();

  const [images, setImages] = useState<StagedImage[]>([]);
  const [presetKey, setPresetKey] = useState<string | null>(null);
  // Same Design ID an operator gives a mockup batch, so this video is tied
  // to the design it was made for and Listing Assets finds all three
  // engines' output for that design under one search.
  const [designId, setDesignId] = useState("");
  const [presets, setPresets] = useState<VideoPreset[] | null>(null);
  const [presetsError, setPresetsError] = useState<string | null>(null);
  const [dragOverDropzone, setDragOverDropzone] = useState(false);
  // Two separate pieces of drag state, both needed: reorderIndex is the
  // thumbnail being dragged (dimmed), dropTargetIndex is the one it is
  // currently hovering over (highlighted, so the operator can see where it
  // will land before releasing).
  const [reorderIndex, setReorderIndex] = useState<number | null>(null);
  const [dropTargetIndex, setDropTargetIndex] = useState<number | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string[] | string | null>(null);
  const [devDetailsOpen, setDevDetailsOpen] = useState(false);

  // Input Sources shortcuts -- see this component's "Input Sources"
  // section below. undefined = still loading, null = none found.
  const [latestMockupJob, setLatestMockupJob] = useState<Job | null | undefined>(undefined);
  const [latestAiImageJob, setLatestAiImageJob] = useState<Job | null | undefined>(undefined);
  const [openingSource, setOpeningSource] = useState<"mockup" | "ai_image" | null>(null);
  const [sourceOpenError, setSourceOpenError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchVideoPresets()
      .then((data) => {
        if (!cancelled) setPresets(data);
      })
      .catch((err) => {
        if (!cancelled) setPresetsError(err instanceof Error ? err.message : "Could not load presets");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Batch-phase only -- a mockup preview job has no assets/ worth
    // opening, only a single unapproved representative image.
    listJobs({ engine_id: MOCKUP_ENGINE_ID, status: "succeeded" })
      .then((jobs) => {
        if (!cancelled) setLatestMockupJob(latestByUpdatedAt(jobs.filter((j) => j.result_summary?.phase === "batch")));
      })
      .catch(() => !cancelled && setLatestMockupJob(null));
    listJobs({ engine_id: IMAGE_ENGINE_ID, status: "succeeded" })
      .then((jobs) => {
        if (!cancelled) setLatestAiImageJob(latestByUpdatedAt(jobs));
      })
      .catch(() => !cancelled && setLatestAiImageJob(null));
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleOpenMockupOutputs() {
    if (!latestMockupJob) return;
    setOpeningSource("mockup");
    setSourceOpenError(null);
    try {
      await openMockupOutputFolder(latestMockupJob.id);
    } catch (err) {
      setSourceOpenError(err instanceof Error ? err.message : "Could not open folder");
    } finally {
      setOpeningSource(null);
    }
  }

  async function handleOpenAiImageOutputs() {
    if (!latestAiImageJob) return;
    const jobName = typeof latestAiImageJob.config.job_name === "string" ? latestAiImageJob.config.job_name : null;
    if (!jobName) return;
    setOpeningSource("ai_image");
    setSourceOpenError(null);
    try {
      await openAiImageOutputFolder(jobName);
    } catch (err) {
      setSourceOpenError(err instanceof Error ? err.message : "Could not open folder");
    } finally {
      setOpeningSource(null);
    }
  }

  useEffect(() => {
    // Revoke object URLs on unmount / whenever the image set changes away
    // from a given file, so we don't leak blob URLs as the user edits.
    return () => {
      images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function addFiles(fileList: FileList | File[]) {
    const incoming = Array.from(fileList);
    const accepted: StagedImage[] = [];
    const rejected: string[] = [];

    for (const file of incoming) {
      if (!isSupportedImageFile(file)) {
        rejected.push(`${file.name} (unsupported type — use .png, .jpg, or .jpeg)`);
        continue;
      }
      accepted.push({ localId: nextLocalId(), file, previewUrl: URL.createObjectURL(file) });
    }

    setImages((prev) => {
      const room = MAX_IMAGES - prev.length;
      const toAdd = accepted.slice(0, Math.max(0, room));
      if (accepted.length > toAdd.length) {
        rejected.push(`only ${MAX_IMAGES} images allowed — ${accepted.length - toAdd.length} extra file(s) skipped`);
      }
      return [...prev, ...toAdd];
    });

    setFileError(rejected.length > 0 ? rejected.join(" · ") : null);
  }

  function removeImage(localId: string) {
    setImages((prev) => {
      const target = prev.find((i) => i.localId === localId);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((i) => i.localId !== localId);
    });
  }

  /**
   * Moves one thumbnail to another position, keeping every other
   * thumbnail's relative order. The array IS the order: the "#1, #2, #3"
   * badges are rendered from each item's index, the Review summary lists
   * the same array, and launch() sends the files in that same order (the
   * server stages them 01_, 02_, ... and the engine renders "exactly the
   * order it's given") -- so there is one ordering in the whole workflow,
   * never a displayed order and a separate real one.
   */
  function reorder(fromIndex: number, toIndex: number) {
    setImages((prev) => {
      if (fromIndex === toIndex) return prev;
      const next = [...prev];
      const [moved] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, moved);
      return next;
    });
  }

  function endReorderDrag() {
    setReorderIndex(null);
    setDropTargetIndex(null);
  }

  const countValid = images.length >= MIN_IMAGES && images.length <= MAX_IMAGES;
  const presetSelected = presetKey !== null;
  const canLaunch = countValid && presetSelected && !launching;
  const selectedPreset = presets?.find((p) => p.key === presetKey) ?? null;

  async function handleLaunch() {
    if (!canLaunch || !presetKey) return;
    setLaunching(true);
    setLaunchError(null);

    let existingIds: Set<string>;
    try {
      existingIds = new Set((await listJobs({ engine_id: ENGINE_ID })).map((j) => j.id));
    } catch {
      existingIds = new Set();
    }

    let navigated = false;
    let attempts = 0;
    const pollTimer = window.setInterval(async () => {
      if (navigated) return;
      attempts += 1;
      if (attempts > MAX_POLL_ATTEMPTS) {
        window.clearInterval(pollTimer);
        return;
      }
      try {
        const current = await listJobs({ engine_id: ENGINE_ID });
        const created = current.find((j) => !existingIds.has(j.id));
        if (created) {
          navigated = true;
          window.clearInterval(pollTimer);
          navigate(`/jobs/${created.id}`);
        }
      } catch {
        /* transient — try again next tick */
      }
    }, POLL_INTERVAL_MS);

    try {
      await launchVideoJob(
        images.map((i) => i.file),
        presetKey,
        designId.trim() || null,
      );
    } catch (err) {
      if (!navigated) {
        window.clearInterval(pollTimer);
        if (err instanceof ApiError && err.status === 422 && err.detail && typeof err.detail === "object") {
          const detail = (err.detail as { detail?: unknown }).detail;
          if (detail && typeof detail === "object" && "errors" in detail) {
            setLaunchError((detail as { errors: string[] }).errors);
          } else {
            setLaunchError(err.message);
          }
        } else if (!(err instanceof ApiError && err.kind === "timeout")) {
          setLaunchError(err instanceof Error ? err.message : "Launch failed");
        }
      }
    } finally {
      window.clearInterval(pollTimer);
      if (!navigated) setLaunching(false);
    }
  }

  const devPayloadPreview = {
    images: images.map((i) => i.file.name),
    preset_key: presetKey,
    design_id: designId.trim() || null,
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {/* -- Input Sources: convenience shortcuts to where completed job
         output already lives -- opens the folder only, never copies or
         imports anything. Dragging images in below works exactly as
         before. -- */}
      <div>
        <div className="label" style={{ marginBottom: 8 }}>
          Input Sources
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          <Button
            variant="outline"
            onClick={handleOpenMockupOutputs}
            disabled={!latestMockupJob}
            loading={openingSource === "mockup"}
            title={latestMockupJob === undefined ? "Checking…" : latestMockupJob ? "Opens the newest completed Mockup Generator job's assets folder" : "No completed Mockup Generator job available."}
          >
            Open Latest Mockup Outputs
          </Button>
          <Button
            variant="outline"
            onClick={handleOpenAiImageOutputs}
            disabled={!latestAiImageJob}
            loading={openingSource === "ai_image"}
            title={latestAiImageJob === undefined ? "Checking…" : latestAiImageJob ? "Opens a folder of the newest completed AI Image Generator job's approved images, ready to select" : "No completed AI Image Generator job available."}
          >
            Open Latest AI Image Outputs
          </Button>
          <Button variant="outline" onClick={() => fileInputRef.current?.click()}>
            Browse Files...
          </Button>
        </div>
        {latestMockupJob === null && (
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 6 }}>No completed Mockup Generator job available.</div>
        )}
        {latestAiImageJob === null && (
          <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 6 }}>No completed AI Image Generator job available.</div>
        )}
        {sourceOpenError && <div style={{ fontSize: 11, color: "var(--state-failure)", marginTop: 6 }}>{sourceOpenError}</div>}
      </div>

      {/* -- 1. Image input -- */}
      <div>
        <div className="label" style={{ marginBottom: 8 }}>
          Images ({images.length} of {MIN_IMAGES}–{MAX_IMAGES} selected)
        </div>

        <div
          style={{
            ...dropzoneBase,
            borderColor: dragOverDropzone ? "var(--accent)" : "var(--border-bright)",
            background: dragOverDropzone ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "transparent",
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOverDropzone(true);
          }}
          onDragLeave={() => setDragOverDropzone(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOverDropzone(false);
            if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files);
          }}
        >
          <div style={{ marginBottom: 10 }}>Drag and drop images here, or</div>
          <Button
            type="button"
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
          >
            Upload Images
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg"
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>

        {fileError && <div style={{ color: "var(--state-degraded)", fontSize: "var(--text-xs)", marginTop: 6 }}>{fileError}</div>}
        {!countValid && images.length > 0 && (
          <div style={{ color: "var(--state-failure)", fontSize: "var(--text-xs)", marginTop: 6 }}>
            {images.length < MIN_IMAGES
              ? `Select at least ${MIN_IMAGES - images.length} more image(s).`
              : `Remove ${images.length - MAX_IMAGES} image(s) — ${MAX_IMAGES} max.`}
          </div>
        )}

        {images.length > 0 && (
          <>
            {/* The reorder affordance was previously only discoverable by
                hovering a thumbnail for its tooltip. Sequence IS the
                deliverable here, so it gets said out loud. */}
            <div style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)", marginTop: 12 }}>
              Drag images to reorder. The video renders in the order shown below.
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 10 }}>
              {images.map((img, index) => {
                const dragging = reorderIndex === index;
                const isDropTarget = dropTargetIndex === index && reorderIndex !== null && !dragging;
                return (
                  <div
                    key={img.localId}
                    draggable
                    onDragStart={() => setReorderIndex(index)}
                    onDragOver={(e) => {
                      e.preventDefault();
                      if (reorderIndex !== null) setDropTargetIndex(index);
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      if (reorderIndex !== null) reorder(reorderIndex, index);
                      endReorderDrag();
                    }}
                    onDragEnd={endReorderDrag}
                    style={{
                      position: "relative",
                      // Big enough to actually recognize which mockup is
                      // which while arranging the sequence -- the previous
                      // 96x72 tile was too small to tell two color
                      // variations of the same shirt apart, which is
                      // exactly the judgement this step asks for.
                      width: 168,
                      cursor: "grab",
                      border: `1px solid ${isDropTarget ? "var(--accent)" : "var(--border-bright)"}`,
                      borderRadius: "var(--radius)",
                      overflow: "hidden",
                      background: "var(--panel-raised)",
                      opacity: dragging ? 0.4 : 1,
                      transform: isDropTarget ? "translateY(-2px)" : "none",
                      transition: "opacity 0.12s ease, border-color 0.12s ease, transform 0.12s ease",
                    }}
                    title="Drag to reorder"
                  >
                    <img
                      src={img.previewUrl}
                      alt={img.file.name}
                      draggable={false}
                      style={{ width: "100%", height: 128, objectFit: "cover", display: "block" }}
                    />
                    <div
                      className="mono"
                      style={{
                        position: "absolute",
                        top: 6,
                        left: 6,
                        background: "rgba(0,0,0,0.72)",
                        color: "#fff",
                        fontSize: 12,
                        fontWeight: 600,
                        borderRadius: 3,
                        padding: "2px 7px",
                      }}
                    >
                      #{index + 1}
                    </div>
                    <button
                      type="button"
                      onClick={() => removeImage(img.localId)}
                      title="Remove"
                      aria-label={`Remove ${img.file.name}`}
                      style={{
                        position: "absolute",
                        top: 6,
                        right: 6,
                        background: "rgba(0,0,0,0.72)",
                        color: "#fff",
                        border: "none",
                        borderRadius: 3,
                        width: 22,
                        height: 22,
                        lineHeight: "22px",
                        fontSize: 14,
                        cursor: "pointer",
                      }}
                    >
                      ×
                    </button>
                    <div
                      className="mono"
                      style={{
                        fontSize: "var(--text-xs)",
                        color: "var(--text-dim)",
                        padding: "5px 7px",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                      title={img.file.name}
                    >
                      {img.file.name}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>

      {/* -- 2. Preset selection -- */}
      <div>
        <div className="label" style={{ marginBottom: 8 }}>
          Preset
        </div>
        {presetsError && <EmptyBlock>{presetsError}</EmptyBlock>}
        {!presets && !presetsError && <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Loading presets…</div>}
        {presets && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {presets.map((preset) => {
              const selected = preset.key === presetKey;
              return (
                <label
                  key={preset.key}
                  style={{
                    display: "flex",
                    gap: 10,
                    alignItems: "flex-start",
                    padding: "10px 12px",
                    borderRadius: "var(--radius)",
                    border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                    background: selected ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "var(--panel)",
                    cursor: "pointer",
                  }}
                >
                  <input
                    type="radio"
                    name="video-preset"
                    checked={selected}
                    onChange={() => setPresetKey(preset.key)}
                    style={{ marginTop: 3 }}
                  />
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{preset.label}</div>
                    <div style={{ fontSize: "var(--text-xs)", color: "var(--text-secondary)", marginTop: 2 }}>{preset.description}</div>
                  </div>
                </label>
              );
            })}
          </div>
        )}
        {/* No "Select a preset." error here any more. It rendered in the
            failure colour from the moment the page opened -- before the
            operator had done anything to fail -- and the same sentence
            already appears next to the disabled Launch button, where it
            actually answers the question being asked ("why can't I press
            this?"). An error that precedes the interaction is noise, and
            showing it twice trains people to ignore the colour.
            The genuine post-interaction validation above (image count,
            gated on images.length > 0) is untouched. */}
      </div>

      {/* Deliberately worded and placed the same as the Mockup Generator's
          own Design ID field -- one design's video, mockups, and AI images
          should be labelled the same way by the same operator habit. */}
      <div>
        <div className="label" style={{ marginBottom: 8 }}>
          Design ID (optional)
        </div>
        <input
          value={designId}
          onChange={(e) => setDesignId(e.target.value)}
          placeholder="Optional reference"
          style={{
            background: "var(--panel-raised)",
            border: "1px solid var(--border-bright)",
            color: "var(--text-primary)",
            borderRadius: "var(--radius)",
            padding: "8px 10px",
            fontSize: 12,
            width: "100%",
            maxWidth: 320,
          }}
        />
        <div style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)", marginTop: 6 }}>
          Ties this video to a design so Listing Assets can find it alongside that design's mockups and AI images.
        </div>
      </div>

      {/* -- 3/4. Review + Launch -- */}
      <div>
        <div className="label" style={{ marginBottom: 8 }}>
          Review
        </div>
        {images.length === 0 && !presetSelected ? (
          <div style={{ fontSize: 12, color: "var(--text-dim)" }}>Select images and a preset to review your launch.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: "var(--text-sm)" }}>
            <div>
              <span style={{ color: "var(--text-dim)" }}>Images: </span>
              <span className="mono">{images.length}</span>
              {images.length > 0 && (
                // Numbered, matching the strip's own #1/#2/#3 badges -- the
                // review line is the last thing read before launching, so
                // it should confirm the SEQUENCE, not just the set.
                <span className="mono" style={{ color: "var(--text-secondary)" }}>
                  {" "}
                  ({images.map((i, index) => `${index + 1}. ${i.file.name}`).join(", ")})
                </span>
              )}
            </div>
            <div>
              <span style={{ color: "var(--text-dim)" }}>Preset: </span>
              <span>{selectedPreset ? selectedPreset.label : "— none selected —"}</span>
            </div>
            <div>
              <span style={{ color: "var(--text-dim)" }}>Design ID: </span>
              <span className="mono">{designId.trim() || "—"}</span>
            </div>
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
          <Button variant="primary" onClick={handleLaunch} disabled={!canLaunch} loading={launching}>
            {launching ? "Launching…" : "Launch Video"}
          </Button>
          {!canLaunch && !launching && (
            <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
              {!countValid ? "Need 3–5 valid images" : !presetSelected ? "Select a preset" : ""}
            </span>
          )}
        </div>

        {launchError && (
          <div style={{ color: "var(--state-failure)", fontSize: "var(--text-xs)", marginTop: 8 }}>
            {Array.isArray(launchError) ? launchError.join(" · ") : launchError}
          </div>
        )}

        <details style={{ marginTop: 14 }} open={devDetailsOpen} onToggle={(e) => setDevDetailsOpen((e.target as HTMLDetailsElement).open)}>
          <summary
            style={{
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-secondary)",
            }}
          >
            Developer Details
          </summary>
          <pre
            className="mono"
            style={{
              marginTop: 8,
              fontSize: 11,
              color: "var(--text-secondary)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {JSON.stringify(devPayloadPreview, null, 2)}
          </pre>
        </details>
      </div>
    </div>
  );
}
