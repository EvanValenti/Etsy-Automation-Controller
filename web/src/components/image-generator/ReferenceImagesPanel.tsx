import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  addAiImageReferenceImage,
  correctAiImageReferenceImageRole,
  getAiImageReferenceImageFileUrl,
  getAiImageReferenceImageRoles,
  listAiImageReferenceImages,
  removeAiImageReferenceImage,
} from "../../api/client";
import { Button } from "../Button";
import { EmptyBlock, Spinner } from "../AsyncState";

function friendlyError(err: unknown): string {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Something went wrong";
}

// Mirrors Etsy-AI-Image-Generator/src/reference_assets.py's ROLE_DEFINITIONS
// -- a friendlier picker over the same taxonomy the engine already owns,
// not a new engine concept. Order matches ROLE_ORDER there.
const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: "product_front", label: "Product Front" },
  { value: "product_back", label: "Product Back" },
  { value: "human_model_front", label: "Human Model Front" },
  { value: "human_model_back", label: "Human Model Back" },
  { value: "folded_product", label: "Folded Product" },
  { value: "flat_lay", label: "Flat Lay" },
  { value: "hanging_product", label: "Hanging Product" },
  { value: "detail_closeup", label: "Detail Close-up" },
  { value: "sleeve_detail", label: "Sleeve Detail" },
  { value: "packaging", label: "Packaging" },
  { value: "lifestyle_reference", label: "Lifestyle Reference" },
  { value: "color_reference", label: "Color Reference" },
  { value: "scale_reference", label: "Scale Reference" },
  { value: "other", label: "Other" },
];

/**
 * Persistent reference-image management for one job -- visible regardless
 * of pipeline stage (reference images are a job resource, not a workflow
 * step). View/Add/Remove/Correct-role, every action a thin call to the
 * engine's reference_images.py via the Controller's routes. The Controller
 * performs no content/format/size/filename validation itself -- every
 * error shown here is the engine's own message, surfaced verbatim.
 */
export function ReferenceImagesPanel({ jobName }: { jobName: string }) {
  const [filenames, setFilenames] = useState<string[] | null>(null);
  const [roles, setRoles] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const [uploading, setUploading] = useState(false);
  /** Per-file outcomes from the last batch. A batch is never all-or-
   * nothing: every valid image is added and each rejection is reported
   * against its own filename, so one duplicate or unsupported file can't
   * discard the other nine an operator just dropped. */
  const [rejected, setRejected] = useState<{ filename: string; reason: string }[]>([]);
  const [addedCount, setAddedCount] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [removingFilename, setRemovingFilename] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [roleUpdating, setRoleUpdating] = useState<string | null>(null);
  const [roleError, setRoleError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  function refresh() {
    setError(null);
    Promise.all([listAiImageReferenceImages(jobName), getAiImageReferenceImageRoles(jobName)])
      .then(([names, roleEntries]) => {
        setFilenames(names);
        setRoles(Object.fromEntries(roleEntries.map((r) => [r.filename, r.role])));
      })
      .catch((err) => setError(friendlyError(err)));
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobName]);

  /**
   * Adds every file in a batch, one request each, and collects failures
   * per filename instead of aborting on the first one. The engine's own
   * reference_images.py is still the only validator -- an unsupported
   * format or duplicate name is reported here using the engine's message,
   * never pre-judged client-side.
   */
  async function handleAddFiles(fileList: FileList | File[]) {
    const files = Array.from(fileList);
    if (files.length === 0) return;

    setUploading(true);
    setRejected([]);
    setAddedCount(0);

    const failures: { filename: string; reason: string }[] = [];
    let added = 0;
    for (const file of files) {
      try {
        await addAiImageReferenceImage(jobName, file);
        added += 1;
      } catch (err) {
        failures.push({ filename: file.name, reason: friendlyError(err) });
      }
    }

    setAddedCount(added);
    setRejected(failures);
    setUploading(false);
    refresh();
  }

  async function handleRemove(filename: string) {
    setRemovingFilename(filename);
    setRemoveError(null);
    try {
      await removeAiImageReferenceImage(jobName, filename);
      refresh();
    } catch (err) {
      setRemoveError(friendlyError(err));
    } finally {
      setRemovingFilename(null);
    }
  }

  async function handleRoleChange(filename: string, role: string) {
    if (!role) return;
    setRoleUpdating(filename);
    setRoleError(null);
    try {
      const updated = await correctAiImageReferenceImageRole(jobName, filename, role);
      setRoles(Object.fromEntries(updated.map((r) => [r.filename, r.role])));
    } catch (err) {
      setRoleError(friendlyError(err));
    } finally {
      setRoleUpdating(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Large drop target with the button inside it, matching the Video
          Generator's upload area. Both paths (drop and picker) accept
          multiple files and go through the same batch handler. */}
      <div
        style={{
          border: "1px dashed",
          borderColor: dragOver ? "var(--accent)" : "var(--border-bright)",
          background: dragOver ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "transparent",
          borderRadius: "var(--radius)",
          padding: "26px 16px",
          textAlign: "center",
          color: "var(--text-secondary)",
          fontSize: "var(--text-base)",
          transition: "border-color 0.15s ease, background 0.15s ease",
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files.length > 0) handleAddFiles(e.dataTransfer.files);
        }}
      >
        <div style={{ marginBottom: 12 }}>Drag and drop reference images here, or</div>
        <Button variant="outline" onClick={() => fileInputRef.current?.click()} loading={uploading}>
          Upload Images
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          hidden
          onChange={(e) => {
            const files = e.target.files;
            if (files && files.length > 0) handleAddFiles(files);
            e.target.value = "";
          }}
        />
      </div>

      {addedCount > 0 && (
        <div style={{ fontSize: 13, color: "var(--state-success)" }}>
          Added {addedCount} image{addedCount === 1 ? "" : "s"}.
        </div>
      )}
      {rejected.length > 0 && (
        <div style={{ fontSize: 13, color: "var(--state-degraded)", display: "flex", flexDirection: "column", gap: 4 }}>
          <span>
            {rejected.length} file{rejected.length === 1 ? "" : "s"} couldn't be added:
          </span>
          {rejected.map((r) => (
            <span key={r.filename} style={{ color: "var(--text-secondary)" }}>
              <span className="mono">{r.filename}</span> — {r.reason}
            </span>
          ))}
        </div>
      )}

      {error && <EmptyBlock>{error}</EmptyBlock>}
      {!error && !filenames && (
        <div style={{ fontSize: 12, color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 8 }}>
          <Spinner size={14} /> Loading reference images…
        </div>
      )}
      {!error && filenames && filenames.length === 0 && <EmptyBlock>No reference images for this job yet.</EmptyBlock>}

      {filenames && filenames.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          {filenames.map((filename) => (
            <div
              key={filename}
              style={{
                width: 140,
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                padding: 8,
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              <img
                src={getAiImageReferenceImageFileUrl(jobName, filename)}
                alt={filename}
                style={{ width: "100%", height: 100, objectFit: "cover", borderRadius: "var(--radius)", background: "#000", display: "block" }}
              />
              <div
                className="mono"
                style={{ fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
                title={filename}
              >
                {filename}
              </div>
              <select
                value={roles[filename] ?? ""}
                onChange={(e) => handleRoleChange(filename, e.target.value)}
                disabled={roleUpdating === filename}
                style={{
                  background: "var(--panel-raised)",
                  border: "1px solid var(--border-bright)",
                  color: "var(--text-primary)",
                  borderRadius: "var(--radius)",
                  padding: "4px 6px",
                  fontSize: "var(--text-xs)",
                }}
              >
                <option value="" disabled>
                  {roles[filename] ? `Current: ${roles[filename]}` : "— set role —"}
                </option>
                {ROLE_OPTIONS.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
              <Button
                variant="danger"
                onClick={() => handleRemove(filename)}
                loading={removingFilename === filename}
                disabled={removingFilename !== null}
              >
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}
      {removeError && <div style={{ fontSize: 11, color: "var(--state-failure)" }}>{removeError}</div>}
      {roleError && <div style={{ fontSize: 11, color: "var(--state-failure)" }}>{roleError}</div>}
    </div>
  );
}
