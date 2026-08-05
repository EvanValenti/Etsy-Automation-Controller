import { useCallback, useEffect, useState } from "react";
import {
  getAiImageGeneratedImageUrl,
  listAiImageGeneratedImages,
  openAiImageOutputFolder,
  setAiImageReviewStatus,
} from "../../api/client";
import type { AiImageGeneratedImage, AiImageMediaCategory } from "../../api/types";
import { Button } from "../Button";
import { EmptyBlock, Spinner } from "../AsyncState";

function friendlyError(err: unknown): string {
  return err instanceof Error ? err.message : "Something went wrong";
}

const CATEGORY_LABEL: Record<AiImageMediaCategory, string> = {
  ai_product_mockup: "AI Product Mockups",
  lifestyle_mockup: "Lifestyle Mockups",
};

const STATUS_STYLE: Record<string, { label: string; color: string }> = {
  approved: { label: "Approved", color: "var(--state-success)" },
  rejected: { label: "Rejected", color: "var(--state-failure)" },
  not_reviewed: { label: "Not reviewed", color: "var(--text-dim)" },
};

/**
 * In-app review for images this job has already generated, so the
 * workflow doesn't dead-end at "Images ready for your review" with no way
 * to actually review them. Groups by media category, shows every image
 * inline, and offers Preview / Open / Approve / Reject per image.
 *
 * Approving and rejecting route through the engine's own image_review
 * logic, which owns what those decisions mean for outputs/approved|
 * rejected/ and the approved-media handoff -- a rejected image is excluded
 * from the approved set by that existing logic, not by anything here.
 *
 * Per-image actions are rendered from one row component, so adding
 * "Regenerate this image" later is a button plus a handler rather than a
 * restructure of the gallery.
 */
export function ImageReviewPanel({
  jobName,
  refreshSignal,
  onReviewed,
}: {
  jobName: string;
  /** Bumped by the parent when pipeline state changes, so a freshly
   * generated batch appears without remounting. */
  refreshSignal?: string;
  onReviewed?: () => void;
}) {
  const [images, setImages] = useState<AiImageGeneratedImage[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ url: string; title: string } | null>(null);
  const [folderMessage, setFolderMessage] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listAiImageGeneratedImages(jobName)
      .then(setImages)
      .catch((err) => setError(friendlyError(err)));
  }, [jobName]);

  useEffect(() => {
    setError(null);
    refresh();
  }, [refresh, refreshSignal]);

  async function decide(image: AiImageGeneratedImage, status: "approved" | "rejected") {
    const key = `${image.category}/${image.concept_id}`;
    setBusyKey(key);
    setActionError(null);
    try {
      await setAiImageReviewStatus(jobName, image.category, image.concept_id, status);
      refresh();
      onReviewed?.();
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setBusyKey(null);
    }
  }

  async function openFolder() {
    setFolderMessage(null);
    try {
      const result = await openAiImageOutputFolder(jobName, "images");
      setFolderMessage(`Opened ${result.opened}`);
    } catch (err) {
      setActionError(friendlyError(err));
    }
  }

  if (error) return <EmptyBlock>{error}</EmptyBlock>;
  if (!images) {
    return (
      <div style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 8 }}>
        <Spinner size={14} /> Loading generated images…
      </div>
    );
  }
  if (images.length === 0) {
    return <EmptyBlock>No images generated for this job yet.</EmptyBlock>;
  }

  const approved = images.filter((i) => i.review_status === "approved").length;
  const rejected = images.filter((i) => i.review_status === "rejected").length;
  const pending = images.length - approved - rejected;

  const grouped = (Object.keys(CATEGORY_LABEL) as AiImageMediaCategory[])
    .map((category) => ({ category, items: images.filter((i) => i.category === category) }))
    .filter((g) => g.items.length > 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: "var(--state-success)" }}>
            Images Generated ✓
          </div>
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 3 }}>
            {images.length} image{images.length === 1 ? "" : "s"} generated
            {approved > 0 || rejected > 0 ? ` · ${approved} approved · ${rejected} rejected` : ""}
            {pending > 0 ? ` · ${pending} awaiting your decision` : ""}
          </div>
        </div>
        <Button variant="outline" onClick={openFolder}>
          Open Output Folder
        </Button>
      </div>
      {folderMessage && <div style={{ fontSize: "var(--text-sm)", color: "var(--text-dim)" }}>{folderMessage}</div>}
      {actionError && <div style={{ fontSize: 13, color: "var(--state-failure)" }}>{actionError}</div>}

      {grouped.map(({ category, items }) => (
        <div key={category}>
          <div style={{ fontSize: "var(--text-base)", fontWeight: 700, marginBottom: 8 }}>
            {CATEGORY_LABEL[category]} ({items.length})
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {items.map((image) => (
              <ImageCard
                key={`${image.category}/${image.concept_id}`}
                jobName={jobName}
                image={image}
                busy={busyKey === `${image.category}/${image.concept_id}`}
                onDecide={(status) => decide(image, status)}
                onPreview={(url, title) => setPreview({ url, title })}
              />
            ))}
          </div>
        </div>
      ))}

      {preview && <PreviewOverlay url={preview.url} title={preview.title} onClose={() => setPreview(null)} />}
    </div>
  );
}

function ImageCard({
  jobName,
  image,
  busy,
  onDecide,
  onPreview,
}: {
  jobName: string;
  image: AiImageGeneratedImage;
  busy: boolean;
  onDecide: (status: "approved" | "rejected") => void;
  onPreview: (url: string, title: string) => void;
}) {
  const filename = image.generated_files[0];
  const url = filename ? getAiImageGeneratedImageUrl(jobName, image.category, image.concept_id, filename) : null;
  const status = STATUS_STYLE[image.review_status] ?? STATUS_STYLE.not_reviewed;
  const title = image.concept_name || image.concept_id;

  return (
    <div
      style={{
        width: 230,
        border: "1px solid",
        borderColor: image.review_status === "approved" ? "var(--state-success)" : "var(--border)",
        borderRadius: "var(--radius)",
        padding: 10,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        opacity: image.review_status === "rejected" ? 0.55 : 1,
      }}
    >
      {url ? (
        <button
          type="button"
          onClick={() => onPreview(url, title)}
          title="Preview"
          style={{ padding: 0, border: "none", background: "none", cursor: "zoom-in" }}
        >
          <img
            src={url}
            alt={title}
            style={{ width: "100%", height: 170, objectFit: "cover", borderRadius: "var(--radius)", background: "#000", display: "block" }}
          />
        </button>
      ) : (
        <div style={{ height: 170, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-dim)", fontSize: "var(--text-sm)" }}>
          No image file
        </div>
      )}

      <div>
        <div style={{ fontSize: "var(--text-base)", fontWeight: 600, wordBreak: "break-word" }}>{title}</div>
        <div className="mono" style={{ fontSize: "var(--text-xs)", color: "var(--text-dim)" }}>
          {image.concept_id}
        </div>
      </div>
      <div style={{ fontSize: "var(--text-sm)", color: status.color, fontWeight: 600 }}>{status.label}</div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {url && (
          <>
            <Button variant="outline" onClick={() => onPreview(url, title)}>
              Preview
            </Button>
            <a href={url} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
              <Button variant="outline">Open</Button>
            </a>
          </>
        )}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Button
          variant={image.review_status === "approved" ? "primary" : "outline"}
          onClick={() => onDecide("approved")}
          loading={busy}
          disabled={busy}
        >
          Approve
        </Button>
        <Button variant="danger" onClick={() => onDecide("rejected")} loading={busy} disabled={busy}>
          Reject
        </Button>
      </div>
    </div>
  );
}

/** Full-size preview. Click anywhere or press Escape to dismiss. */
function PreviewOverlay({ url, title, onClose }: { url: string; title: string; onClose: () => void }) {
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
      <img src={url} alt={title} style={{ maxWidth: "100%", maxHeight: "82vh", objectFit: "contain" }} />
      <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>{title} — click anywhere or press Esc to close</div>
    </div>
  );
}
