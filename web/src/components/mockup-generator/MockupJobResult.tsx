import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { getMockupResultImageUrl, openMockupListingOutputs, openMockupOutputFolder } from "../../api/client";
import type { Job } from "../../api/types";
import { formatDuration } from "../../status";
import { Button } from "../Button";

const ENGINE_ID = "etsy-mockup-generator";

interface Props {
  job: Job;
  totalExecutionSeconds: number | null;
  /** When rendered inline on the engine page itself (already at
   * /engines/etsy-mockup-generator), navigating to that same route is a
   * no-op -- the caller passes a state-reset callback instead. JobDetail
   * (a different route) omits this and gets the default navigate(). */
  onLaunchAnother?: () => void;
}

/** Completed-result view for a succeeded etsy-mockup-generator BATCH-phase
 * Job -- real representative mockup, asset counts, background/design id,
 * duration, and folder actions, so a normal operator never has to read
 * result_summary JSON to find their mockups. Raw manifest/config stays
 * available via JobDetail's own Developer Details disclosure, not
 * duplicated here.
 */
export function MockupJobResult({ job, totalExecutionSeconds, onLaunchAnother }: Props) {
  const navigate = useNavigate();
  const [folderMessage, setFolderMessage] = useState<string | null>(null);
  const [folderError, setFolderError] = useState<string | null>(null);

  const manifest = (job.result_summary?.manifest ?? {}) as Record<string, unknown>;
  const outputCounts = (manifest.output_counts ?? {}) as Record<string, number>;
  const totalAssets = Object.values(outputCounts).reduce((sum, n) => sum + (n ?? 0), 0);
  const backgroundFilename = typeof manifest.background_filename === "string" ? manifest.background_filename : "—";
  const designId = typeof manifest.design_id === "string" ? manifest.design_id : null;

  async function handleOpen(action: () => Promise<{ opened: string }>) {
    setFolderError(null);
    setFolderMessage(null);
    try {
      const result = await action();
      setFolderMessage(`Opened ${result.opened}`);
    } catch (err) {
      setFolderError(err instanceof Error ? err.message : "Could not open folder");
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--state-success)" }}>
        ✓ Mockups generated successfully — {totalAssets} asset{totalAssets === 1 ? "" : "s"} ready.
      </div>
      <img
        src={getMockupResultImageUrl(job.id)}
        alt="Representative completed mockup"
        style={{ width: "100%", maxHeight: 420, objectFit: "contain", background: "#000", borderRadius: "var(--radius)", display: "block" }}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: "var(--text-sm)" }}>
        <Row label="Total assets" value={String(totalAssets)} mono />
        {Object.entries(outputCounts).map(([category, count]) => (
          <Row key={category} label={category.replace(/_/g, " ")} value={String(count)} mono />
        ))}
        <Row label="Background" value={backgroundFilename} mono />
        <Row label="Design ID" value={designId ?? "—"} mono />
        <Row label="Duration" value={formatDuration(totalExecutionSeconds)} mono />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        <Button variant="outline" onClick={() => handleOpen(() => openMockupOutputFolder(job.id))}>
          Open Output Folder
        </Button>
        <Button variant="outline" onClick={() => handleOpen(() => openMockupListingOutputs(job.id))}>
          Open Current Listing Outputs
        </Button>
        <Button variant="primary" onClick={onLaunchAnother ?? (() => navigate(`/engines/${ENGINE_ID}`))}>
          Launch Another Mockup Job
        </Button>
      </div>

      {folderMessage && <div style={{ fontSize: 11, color: "var(--text-dim)" }}>{folderMessage}</div>}
      {folderError && <div style={{ fontSize: 11, color: "var(--state-failure)" }}>{folderError}</div>}
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span style={{ color: "var(--text-dim)" }}>{label}</span>
      <span className={mono ? "mono" : undefined} style={{ color: "var(--text-primary)", textAlign: "right", wordBreak: "break-all" }}>
        {value}
      </span>
    </div>
  );
}
