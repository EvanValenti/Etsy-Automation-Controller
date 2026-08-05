import type { AiImageJobManifest, Job } from "./api/types";
import { formatDuration } from "./status";

/** A short, human-meaningful identifier pulled from whatever this engine's
 * own launch config happens to carry -- never the raw config object, just
 * the one field an operator would actually recognize this job by. Falls
 * back to nothing (the short id alone still identifies the row) rather
 * than guessing at fields a config doesn't have. */
export function jobIdentifier(job: Job): string | null {
  const config = job.config as Record<string, unknown>;
  if (typeof config.job_name === "string") return config.job_name; // AI Image Generator
  if (typeof config.design_id === "string" && config.design_id) return config.design_id; // Mockup Generator
  if (typeof config.preset_key === "string") return config.preset_key; // Video Generator
  return null;
}

// Only these two engines currently create a fresh Controller Job row for
// every "advance"/re-batch call against the SAME persistent engine-side
// job (see infra/cleanup.py's _other_jobs_reference_path -- a real,
// observed situation, not a guess): AI Image Generator's job_name and
// Mockup Generator's design_id each identify one underlying unit of work
// across many Controller Job rows. Video Generator's preset_key is a
// shared config CHOICE, not an identity -- grouping by it would wrongly
// merge unrelated videos, so it is deliberately excluded here.
const GROUPABLE_ENGINES = new Set(["etsy-ai-image-generator", "etsy-mockup-generator"]);

/** jobIdentifier(), but null for any engine where that identifier isn't
 * actually a stable identity for one unit of work (e.g. Video Generator's
 * preset_key, shared across many unrelated videos). Use THIS -- never the
 * raw jobIdentifier() -- for anything that decides whether two Job rows
 * represent "the same job" (grouping, sibling/delete-safety checks).
 * jobIdentifier() itself stays fine for plain display (a title). */
export function groupIdentifier(job: Job): string | null {
  return GROUPABLE_ENGINES.has(job.engine_id) ? jobIdentifier(job) : null;
}

function jobTime(job: Job): number {
  const iso = job.updated_at ?? job.created_at;
  return iso ? new Date(iso).getTime() : 0;
}

export interface JobGroup {
  /** Stable across re-fetches: engine_id+identifier for a real group, or
   * the Job's own id for anything ungrouped. */
  key: string;
  engineId: string;
  identifier: string | null;
  /** Every Controller Job row for this group, newest first. jobs[0] is
   * this group's "current" row -- what the table's primary row reflects. */
  jobs: Job[];
}

/**
 * Groups Controller Job rows that represent the same underlying engine-
 * side job into one JobGroup, so the Jobs table can present "Engine Job ->
 * Workflow History" instead of one flat row per stage-advance. Purely a
 * presentation grouping over data the engine already writes into
 * job.config -- no repository state is read, duplicated, or inferred, and
 * the underlying Job rows are untouched (each is still deletable/
 * inspectable on its own, see JobDetail).
 */
export function groupJobs(jobs: Job[]): JobGroup[] {
  const groups = new Map<string, Job[]>();
  const order: string[] = [];

  for (const job of jobs) {
    const identifier = groupIdentifier(job);
    const key = identifier ? `${job.engine_id}:${identifier}` : `solo:${job.id}`;
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)!.push(job);
  }

  return order.map((key) => {
    const groupedJobs = groups.get(key)!.slice().sort((a, b) => jobTime(b) - jobTime(a));
    return { key, engineId: groupedJobs[0].engine_id, identifier: groupIdentifier(groupedJobs[0]), jobs: groupedJobs };
  });
}

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

/** created_at -> updated_at for a terminal job; "In progress" while
 * running; "—" for anything not yet started/finished. Computed entirely
 * from fields already on the Job object -- no extra request per row, so
 * this scales to a long list the same way the rest of the table does. */
export function jobDuration(job: Job): string {
  if (!job.created_at) return "—";
  if (!TERMINAL_STATUSES.has(job.status)) {
    return job.status === "running" ? "In progress" : "—";
  }
  if (!job.updated_at) return "—";
  const start = new Date(job.created_at).getTime();
  const end = new Date(job.updated_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return "—";
  return formatDuration((end - start) / 1000);
}

/**
 * A finished operator DELIVERABLE this job produced, or null if it didn't
 * produce one.
 *
 * Deliberately null for the AI Image Generator: its result_summary carries
 * the workflow stage that advance() reached ("Concept Review", "Prompt
 * Review", "Image Review"). Those are stages of the work, not output of
 * it, and showing them in an "Output" column made every in-flight job look
 * like it had delivered something. A stage belongs in Status; only a real
 * artifact count belongs here.
 */
export function jobOutputSummary(job: Job): string | null {
  if (job.status !== "succeeded" || !job.result_summary) return null;
  const r = job.result_summary;

  if (job.engine_id === "etsy-video-generator") {
    return typeof r.output_video_path === "string" ? "1 video" : null;
  }

  if (job.engine_id === "etsy-mockup-generator") {
    const manifest = (r.manifest ?? {}) as Record<string, unknown>;
    const counts = (manifest.output_counts ?? {}) as Record<string, number>;
    const total = Object.values(counts).reduce((sum, n) => sum + (Number(n) || 0), 0);
    return total > 0 ? `${total} mockup${total === 1 ? "" : "s"}` : null;
  }

  return null;
}

/**
 * How a job should be NAMED in the interface: a human-readable title an
 * operator recognizes, plus the technical identifier as secondary detail.
 *
 * The readable half is only ever built from names an engine already
 * records for itself -- the AI Image Generator's manifest carries
 * product_name/store_name/campaign_name, which is exactly the
 * "Stop. There's Mushrooms — Outdoor / Mushroom Hunting" an operator
 * thinks in. When no manifest is available (a legacy job, another engine,
 * a manifest the engine couldn't read) `title` is null and callers fall
 * back to showing the identifier alone -- never a fabricated title.
 */
export interface JobDisplayName {
  /** Large text: what the operator calls this work. Null when unknown. */
  title: string | null;
  /** Small secondary text: slug / listing id / job id. Always present. */
  identifier: string;
}

/** Manifests keyed by their job_name, as returned by listAiImageJobs(). */
export type ManifestsByJobName = Map<string, AiImageJobManifest>;

export function buildManifestLookup(manifests: AiImageJobManifest[] | null | undefined): ManifestsByJobName {
  const map: ManifestsByJobName = new Map();
  for (const m of manifests ?? []) {
    if (m.status !== "unreadable" && m.job_name) map.set(m.job_name, m);
  }
  return map;
}

/** "Stop. There's Mushrooms — Outdoor / Mushroom Hunting" from a manifest,
 * degrading gracefully as fields go missing (a job created before a store
 * or campaign was chosen still gets its product name). */
export function manifestTitle(manifest: AiImageJobManifest): string | null {
  const product = manifest.product?.product_name?.trim();
  const store = manifest.store?.store_name?.trim();
  const campaign = manifest.campaign?.campaign_name?.trim();
  const context = [store, campaign].filter(Boolean).join(" / ");
  if (product && context) return `${product} — ${context}`;
  return product || context || null;
}

// What each Controller Job row in a workflow history actually DID, in the
// Controller's own established stage vocabulary -- the same labels
// PipelineChecklist shows for completed stages. Keyed on the stage
// identifier the engine already reports in result_summary.advance.stage,
// so this is a relabeling of existing data, never a new state machine.
const AI_IMAGE_STAGE_LABELS: Record<string, string> = {
  job_created: "Job Created",
  concept_planning: "Concepts Planned",
  concept_generation: "Concepts Generated",
  concept_review: "Concepts Reviewed",
  prompt_build: "Prompts Built",
  prompt_review: "Prompts Reviewed",
  image_generation: "Images Generated",
  image_review: "Images Reviewed",
  ready_for_controller: "Workflow Complete",
};

// The Mockup Generator's two-phase run, from the phase its own launch
// config already records.
const MOCKUP_PHASE_LABELS: Record<string, string> = {
  preview: "Preview Created",
  batch: "Batch Generated",
};

/**
 * The workflow stage one Controller Job row represents, e.g. "Prompts
 * Built" -- replacing the positional "step 3", which told an operator
 * where a row sat in a list but nothing about what happened.
 *
 * Returns null when the engine reports no stage for this row (a failed or
 * still-running job that never got far enough to name one). Callers show
 * nothing rather than falling back to internal numbering.
 */
export function jobStageLabel(job: Job): string | null {
  if (job.engine_id === "etsy-ai-image-generator") {
    const advance = (job.result_summary as { advance?: { stage?: string | null } } | null)?.advance;
    const stage = advance?.stage;
    return stage ? AI_IMAGE_STAGE_LABELS[stage] ?? null : null;
  }

  if (job.engine_id === "etsy-mockup-generator") {
    const phase = (job.config as Record<string, unknown>).phase;
    return typeof phase === "string" ? MOCKUP_PHASE_LABELS[phase] ?? null : null;
  }

  return null;
}

export function jobDisplayName(job: Job, manifests?: ManifestsByJobName): JobDisplayName {
  const identifier = jobIdentifier(job) ?? job.id.slice(0, 8);
  const jobName = typeof (job.config as Record<string, unknown>).job_name === "string"
    ? ((job.config as Record<string, unknown>).job_name as string)
    : null;
  const manifest = jobName ? manifests?.get(jobName) : undefined;
  return { title: manifest ? manifestTitle(manifest) : null, identifier };
}
