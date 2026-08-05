/**
 * Mirrors core/domain/*.py and core/domain/monitoring.py 1:1 — field names
 * and enum values match the real JSON the Controller API returns exactly.
 * No parallel "frontend model" is invented; if the backend adds a field,
 * add it here, don't reshape it.
 */

export type JobStatus =
  | "pending"
  | "validated"
  | "queued"
  | "running"
  | "waiting_on_approval"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface EngineRunReference {
  engine_id: string;
  reference_type: string;
  reference_value: string;
  created_at: string | null;
  extra: Record<string, unknown>;
}

export interface Job {
  id: string;
  engine_id: string;
  config: Record<string, unknown>;
  status: JobStatus;
  pipeline_run_id: string | null;
  run_reference: EngineRunReference | null;
  result_summary: Record<string, unknown> | null;
  error_summary: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

export type ImplementationStatus = "real" | "partial" | "stub";
export type CancelSupport = "immediate" | "graceful" | "unsupported";

export interface EngineCapabilities {
  engine_id: string;
  display_name: string;
  supports_launch: boolean;
  version: string | null;
  cancel_support: CancelSupport;
  supports_monitoring: boolean;
  supports_approvals: boolean;
  supports_pipelines: boolean;
  max_concurrent_runs: number;
  launch_config_schema: Record<string, string>;
  possible_approval_types: string[];
  health_status: string;
  implementation_status: ImplementationStatus;
  notes: string[];
}

export interface Engine {
  id: string;
  name: string;
  adapter_binding: string;
  capabilities: EngineCapabilities;
  health_status: string;
  registered_at: string | null;
}

export type JobEventType =
  | "created"
  | "queued"
  | "engine_reserved"
  | "launch_started"
  | "launch_succeeded"
  | "launch_failed"
  | "timed_out"
  | "cancelled"
  | "completed";

export interface JobEvent {
  id: string;
  job_id: string;
  engine_id: string;
  event_type: JobEventType;
  occurred_at: string;
  metadata: Record<string, unknown>;
}

export type JobProgressPhase =
  | "queued"
  | "starting"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "unknown";

export interface JobProgress {
  phase: JobProgressPhase;
  percentage: number | null;
  stage: string | null;
  message: string | null;
  updated_at: string | null;
}

export interface JobMetrics {
  queue_wait_seconds: number | null;
  launch_duration_seconds: number | null;
  total_execution_seconds: number | null;
}

export type EngineState = "idle" | "busy" | "launching" | "degraded" | "offline";

export interface EngineHealth {
  engine_id: string;
  state: EngineState;
  current_job_id: string | null;
  queue_length: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_launch_duration_seconds: number | null;
}

export type QueueWaitReason =
  | "engine_unavailable"
  | "execution_disabled"
  | "eligible_pending_evaluation"
  | "engine_busy";

export interface QueuedJobInfo {
  job_id: string;
  queued_at: string;
  wait_reason: QueueWaitReason;
}

export interface EngineMetrics {
  engine_id: string;
  success_count: number;
  failure_count: number;
  timeout_count: number;
  cancel_count: number;
}

/**
 * GET /metrics/lifetime -- the Dashboard's headline numbers, counted in
 * completed WORKFLOWS (one finished unit of operator work), never in raw
 * Controller Job rows.
 *
 * Cumulative by construction: the server records each completion once into
 * a permanent ledger, so these are unaffected by deleting job history.
 * `minutes_saved` is the server-side estimate total -- the estimate itself
 * lives in infra/lifetime_metrics.py, not here.
 */
export interface LifetimeMetrics {
  minutes_saved: number;
  lifetime_production: number;
  completed_today: number;
}

// -- Video Generator operator workflow (Step 12) ----------------------------

export interface VideoPreset {
  key: string;
  label: string;
  description: string;
}

// -- Mockup Generator operator workflow (Step 13) ----------------------------

export interface MockupBackground {
  name: string;
  path: string;
  usable: boolean;
  reason: string | null;
}

// -- AI Image Generator operator workflow (Step 14) --------------------------
// Mirrors Etsy-AI-Image-Generator's own job_manifest.py shape 1:1 -- these
// are NOT Controller domain types, they're the engine's own manifest
// passed through verbatim (see api/image_generator_routes.py). The
// Controller never reinterprets pipeline_status/counts/artifacts; it just
// presents them.

export interface AiImageCampaign {
  campaign_id: string;
  campaign_name: string;
}

export interface AiImageStore {
  store_id: string;
  store_name: string;
  campaigns: AiImageCampaign[];
}

export interface AiImagePipelineStatus {
  job_created: boolean;
  creative_brief_complete: boolean;
  concept_planning_complete: boolean;
  manual_concept_package_exported: boolean;
  manual_concept_response_present: boolean;
  manual_concept_response_awaiting_import: boolean;
  concept_generation_started: boolean;
  concept_generation_complete: boolean;
  concept_review_started: boolean;
  concept_review_complete: boolean;
  prompt_build_complete: boolean;
  prompt_review_complete: boolean;
  manual_image_packages_exported: boolean;
  image_generation_started: boolean;
  image_generation_complete: boolean;
  image_review_started: boolean;
  image_review_complete: boolean;
  approved_media_handoff_ready: boolean;
  ready_for_controller: boolean;
}

export interface AiImageCounts {
  ai_product_mockup_concepts_requested: number;
  ai_product_mockup_concepts_generated: number;
  ai_product_mockup_concepts_approved: number;
  ai_product_mockup_concepts_rejected: number;
  lifestyle_mockup_concepts_requested: number;
  lifestyle_mockup_concepts_generated: number;
  lifestyle_mockup_concepts_approved: number;
  lifestyle_mockup_concepts_rejected: number;
  images_generated: number;
  images_failed: number;
  images_approved: number;
  images_rejected: number;
  images_not_reviewed: number;
}

export interface AiImageJobManifest {
  job_name: string;
  updated_at: string;
  store: { store_id: string; store_name: string };
  campaign: { campaign_id: string; campaign_name: string };
  product: { product_name: string; product_type: string; product_color: string };
  pipeline_status: AiImagePipelineStatus;
  counts: AiImageCounts;
  artifacts: Record<string, string>;
  warnings: string[];
  next_step: string;
  /** Only present on list_jobs() entries the engine couldn't read. */
  status?: "unreadable";
  detail?: string;
}

export interface AiImageAdvanceResult {
  status: "advanced" | "waiting_on_human" | "complete" | "error";
  stage: string | null;
  detail: string | null;
}

// -- AI Image Generator Manual Mode (Prompt 2) --------------------------
// Same "presented verbatim, never reinterpreted" posture as the rest of
// this engine's types.

export interface AiImageManualConceptExport {
  manual_dir: string;
  prompt_path: string;
  prompt_text: string;
  response_template_text: string;
  response_path: string;
  clipboard_copied: boolean;
  ai_count: number;
  lifestyle_count: number;
}

export interface AiImageManualConceptImportResult {
  imported: boolean;
  cancelled_overwrite: boolean;
  summary: Record<string, unknown> | null;
  ai_count: number | null;
  lifestyle_count: number | null;
  backup_paths: string[];
  report_path: string | null;
}

export type AiImageMediaCategory = "ai_product_mockup" | "lifestyle_mockup";

export interface AiImageManualPromptSummary {
  category: AiImageMediaCategory;
  concept_id: string;
  concept_name: string;
  generation_status: string | null;
}

export interface AiImageManualPromptCopy {
  category: AiImageMediaCategory;
  concept_id: string;
  concept_name: string;
  prompt_text: string;
  reference_images: string[];
  manual_dir: string;
  incoming_dir: string;
}

export interface AiImageManualImageImportResult {
  imported: { category: string; concept_id: string; files: string[] }[];
  skipped: { category?: string; concept_id?: string; reason: string }[];
}

/**
 * Result of importing finished images as a job's final deliverables.
 *
 * No `skipped`, unlike the per-concept import above: that one walks every
 * pending concept and reports which it couldn't do, whereas this batch is
 * all-or-nothing (a partial import would mark the job complete while
 * silently missing deliverables), so a 2xx means every file landed.
 *
 * `job_status` is the refreshed manifest, returned so the caller can show
 * the now-completed job without a second round trip.
 */
export interface AiImageFinishedImageImportResult {
  imported: {
    category: string;
    concept_id: string;
    concept_name: string;
    filename: string;
    source_filename: string;
  }[];
  category: string;
  count: number;
  job_status: AiImageJobManifest;
}

// -- Concept Review + Reference Images (Controller-Engine wiring) --------
// Concepts are presented verbatim from concept_review.py's own on-disk
// shape -- the Controller never reinterprets a concept's fields, only the
// ones it displays. "status" is the only field this UI ever changes, via
// the approve/reject endpoints (which mutate the engine's own file, never
// this object directly).

export type AiImageConceptStatus = "proposed" | "approved" | "rejected";

export interface AiImageConcept {
  concept_id: string;
  concept_name: string;
  concept_summary?: string;
  status: AiImageConceptStatus;
  purpose?: string;
  role?: string;
  environment?: string;
  mood?: string;
  reasoning?: string;
  niche_relevance?: string;
  variety_notes?: string;
  // Every other field concept generation wrote is preserved but not
  // typed here -- this UI never depends on fields beyond the ones above.
  [key: string]: unknown;
}

export interface AiImageConceptDecisionResult {
  concept: AiImageConcept;
  counts: { proposed: number; approved: number; rejected: number };
}

export interface AiImageReferenceAssetRole {
  filename: string;
  role: string;
}

export interface AiImageConceptProviderInfo {
  job_name: string;
  provider_id: string;
  label: string;
  configured: boolean;
  automatic_provider_configured: boolean;
}

export interface AiImagePromptSummary {
  category: AiImageMediaCategory;
  concept_id: string;
  concept_name: string;
  generation_status: string | null;
}

/** One prompt as structure rather than a formatted blob: the operator-
 * facing prompt text and reference images, with everything technical
 * quarantined in technical_metadata for a collapsed Developer Details
 * section. Mirrors headless.get_prompt_detail(). */
export interface AiImagePromptDetail {
  category: AiImageMediaCategory;
  concept_id: string;
  concept_name: string;
  generation_status: string | null;
  review_status: string | null;
  system_prompt: string;
  user_prompt: string;
  reference_images: string[];
  technical_metadata: Record<string, unknown>;
}

export type AiImageReviewStatus = "not_reviewed" | "approved" | "rejected";

/** One generated concept's images plus the review decision the engine
 * already records in generation_metadata.json. */
export interface AiImageGeneratedImage {
  category: AiImageMediaCategory;
  concept_id: string;
  concept_name: string;
  review_status: AiImageReviewStatus;
  generated_files: string[];
  generated_at: string | null;
}

// -- Listing Workspace (Controller feature, Prompt 3) --------------------
// Mirrors infra/listing_workspace.py's manifest shape 1:1 -- see that
// module's docstring for the workspace layout and rebuild semantics.

export interface ListingWorkspaceAsset {
  filename: string;
  source: "mockup" | "ai_image" | "video";
  source_job: string;
  original_path: string;
}

export interface ListingWorkspaceManifest {
  listing_id: string;
  built_at: string;
  sources: {
    video_job_ids: string[];
    mockup_job_ids: string[];
    ai_image_job_names: string[];
  };
  images: ListingWorkspaceAsset[];
  videos: ListingWorkspaceAsset[];
}

/**
 * How to preview one candidate asset before anything is built. Each kind
 * names an already-public per-engine endpoint the client turns into a URL
 * (see listingAssetPreviewUrl) — never a filesystem path.
 */
export type ListingAssetPreview =
  | { kind: "mockup"; job: string; file: string }
  | { kind: "ai_image"; job: string; category: string; concept_id: string; file: string }
  | { kind: "video"; job: string };

/**
 * One individually selectable asset. `filename` is the workspace filename
 * a build would produce, and is therefore also the key a selection is
 * expressed in — see infra/listing_workspace.py's PlannedAsset.
 */
export interface ListingAssetCandidate {
  filename: string;
  source: "mockup" | "ai_image" | "video";
  source_job: string;
  label: string;
  preview: ListingAssetPreview;
}

export interface ListingAssetCandidates {
  images: ListingAssetCandidate[];
  videos: ListingAssetCandidate[];
}

export interface ListingSources {
  video_job_ids: string[];
  mockup_job_ids: string[];
  ai_image_job_names: string[];
}
