import type { AiImagePipelineStatus } from "../../api/types";
import { Circle, CircleCheck } from "lucide-react";

const STAGES: { key: keyof AiImagePipelineStatus; label: string }[] = [
  { key: "job_created", label: "Job Created" },
  { key: "concept_planning_complete", label: "Concepts Planned" },
  { key: "concept_generation_complete", label: "Concepts Generated" },
  { key: "concept_review_complete", label: "Concepts Reviewed" },
  { key: "prompt_build_complete", label: "Prompts Built" },
  { key: "prompt_review_complete", label: "Prompts Reviewed" },
  { key: "image_generation_complete", label: "Images Generated" },
  { key: "image_review_complete", label: "Images Reviewed" },
];

/** Presents the engine's own pipeline_status verbatim as a checklist --
 * the Controller does not reinterpret what "complete" means for any
 * stage, it just reflects the booleans job_manifest.json already
 * computed. */
export function PipelineChecklist({ status }: { status: AiImagePipelineStatus }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {STAGES.map(({ key, label }) => {
        const done = Boolean(status[key]);
        return (
          <div key={key} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
            <span
              aria-hidden
              style={{
                // Flex-centred box rather than a mono text cell: an icon has
                // no baseline to align to, so text-align/font-family here
                // did nothing except leave it sitting low in the row.
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 14,
                flexShrink: 0,
                color: done ? "var(--state-success)" : "var(--text-dim)",
              }}
            >
              {done ? <CircleCheck size={13} strokeWidth={2.2} /> : <Circle size={13} strokeWidth={2} />}
            </span>
            <span style={{ color: done ? "var(--text-primary)" : "var(--text-dim)" }}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}
