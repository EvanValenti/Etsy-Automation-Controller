import { useState, type ReactNode } from "react";
import { Button } from "../Button";

/**
 * One selectable asset, used identically by AI Images, Mockups and Videos.
 *
 * Previously each asset type had its own interaction: AI images were
 * checkbox rows with no thumbnail, mockups and videos were small tiles
 * with a separate "skip" tile to clear the choice. Three different rules
 * for the same act of picking an asset. This is the single model --
 * click to select, click again to deselect, everywhere.
 *
 * Information order is human-first: thumbnail, then the title an operator
 * recognizes, then campaign and date, and only then the technical
 * identifier in small dim mono. The id is present for disambiguation, but
 * it should never be the thing you have to read to know what this is.
 */
export function AssetCard({
  selected,
  onToggle,
  title,
  campaign,
  date,
  identifier,
  badge,
  thumbnail,
  onPreview,
}: {
  selected: boolean;
  onToggle: () => void;
  /** Human-readable name. Always the largest text on the card. */
  title: string;
  /** Store / campaign context, when the asset carries it. */
  campaign?: string | null;
  /** When this asset was produced. */
  date?: string | null;
  /** Slug, job id, or run token — secondary by design. */
  identifier?: string | null;
  /** Short status chip, e.g. "5 approved". */
  badge?: string | null;
  thumbnail?: ReactNode;
  /** When supplied, renders a Preview action that opens in-app. */
  onPreview?: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggle();
        }
      }}
      style={{
        width: 224,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: 10,
        cursor: "pointer",
        borderRadius: "var(--radius)",
        border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
        background: selected ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "var(--panel-raised)",
        transition: "border-color 0.12s ease, background 0.12s ease",
      }}
    >
      <div style={{ position: "relative" }}>
        <div
          style={{
            width: "100%",
            height: 150,
            borderRadius: "var(--radius)",
            overflow: "hidden",
            background: "#000",
            border: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          {thumbnail}
        </div>
        {selected && (
          <span
            aria-hidden
            style={{
              position: "absolute",
              top: 6,
              right: 6,
              width: 22,
              height: 22,
              borderRadius: "50%",
              background: "var(--accent)",
              color: "#000",
              fontSize: 13,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            ✓
          </span>
        )}
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 600, overflowWrap: "anywhere" }}>{title}</div>
        {campaign && <div style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)", overflowWrap: "anywhere" }}>{campaign}</div>}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {date && <span style={{ fontSize: 12, color: "var(--text-dim)" }}>{date}</span>}
          {badge && <span style={{ fontSize: 12, color: "var(--state-success)" }}>{badge}</span>}
        </div>
        {identifier && (
          <div className="mono" style={{ fontSize: 11, color: "var(--text-dim)", overflowWrap: "anywhere" }}>
            {identifier}
          </div>
        )}
      </div>

      {onPreview && (
        <div onClick={(e) => e.stopPropagation()}>
          <Button
            variant="outline"
            onClick={(e) => {
              e?.stopPropagation();
              onPreview();
            }}
          >
            Preview
          </Button>
        </div>
      )}
    </div>
  );
}

/** Placeholder for an asset with no renderable thumbnail. */
export function ThumbPlaceholder({ label }: { label: string }) {
  return (
    <span style={{ fontSize: 12, color: "var(--text-dim)", textAlign: "center", padding: "0 8px" }}>{label}</span>
  );
}

/** An <img> that degrades to a placeholder instead of a broken-image box
 * if the source can't be served. A thumbnail is an aid, never a reason
 * for a card to look defective.
 *
 * Lazy + async-decoded because these are the engines' real full-size
 * assets served straight from disk (a single mockup PNG is routinely
 * several MB), and a listing can legitimately show twenty of them at once
 * -- only the ones actually scrolled into view should be fetched. */
export function SafeThumb({ src, alt = "" }: { src: string; alt?: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <ThumbPlaceholder label="No preview" />;
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
    />
  );
}
