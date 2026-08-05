import { useEffect, useState } from "react";
import { CircleCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { fetchVideoPresets, getJobOutputVideoUrl, openListingOutputs, openOutputFolder } from "../../api/client";
import type { Job, VideoPreset } from "../../api/types";
import { formatDuration } from "../../status";
import { Button } from "../Button";

const ENGINE_ID = "etsy-video-generator";

interface Props {
  job: Job;
  totalExecutionSeconds: number | null;
}

/** Completed-result view for a succeeded etsy-video-generator Job — real
 * MP4 preview, filename, friendly preset name, duration, and folder
 * actions, so a normal operator never has to read result_summary JSON to
 * find their video. Raw JSON stays available via JobDetail's own
 * Developer Details disclosure, not duplicated here.
 */
export function VideoJobResult({ job, totalExecutionSeconds }: Props) {
  const navigate = useNavigate();
  const [presets, setPresets] = useState<VideoPreset[] | null>(null);
  const [folderMessage, setFolderMessage] = useState<string | null>(null);
  const [folderError, setFolderError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchVideoPresets()
      .then((data) => !cancelled && setPresets(data))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const presetKey = typeof job.config.preset_key === "string" ? job.config.preset_key : null;
  const presetLabel = presets?.find((p) => p.key === presetKey)?.label ?? presetKey ?? "—";
  // Controller-side listing metadata, recorded on the Job's own config at
  // launch — mirrors MockupJobResult's Design ID row.
  const designId = typeof job.config.design_id === "string" && job.config.design_id.trim() ? job.config.design_id : null;
  const outputPath = typeof job.result_summary?.output_video_path === "string" ? job.result_summary.output_video_path : null;
  const filename = outputPath ? outputPath.split(/[\\/]/).pop() : null;

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
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "var(--text-sm)", fontWeight: 600, color: "var(--state-success)" }}>
        <CircleCheck size={13} strokeWidth={2.2} aria-hidden />
        Video generated successfully.
      </div>
      <video
        controls
        src={getJobOutputVideoUrl(job.id)}
        style={{ width: "100%", maxHeight: 360, background: "#000", borderRadius: "var(--radius)", display: "block" }}
      />

      <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: "var(--text-sm)" }}>
        <Row label="Output file" value={filename ?? "—"} mono />
        <Row label="Preset" value={presetLabel} />
        <Row label="Design ID" value={designId ?? "—"} mono />
        <Row label="Duration" value={formatDuration(totalExecutionSeconds)} mono />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        <Button variant="outline" onClick={() => handleOpen(() => openOutputFolder(job.id))}>
          Open Output Folder
        </Button>
        <Button variant="outline" onClick={() => handleOpen(() => openListingOutputs(job.id))}>
          Open Current Listing Outputs
        </Button>
        <Button variant="primary" onClick={() => navigate(`/engines/${ENGINE_ID}`)}>
          Launch Another Video
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
