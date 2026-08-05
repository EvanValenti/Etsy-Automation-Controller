import type {
  AiImageConcept,
  AiImageConceptDecisionResult,
  AiImageConceptProviderInfo,
  AiImageFinishedImageImportResult,
  AiImageJobManifest,
  AiImageManualConceptExport,
  AiImageManualConceptImportResult,
  AiImageManualImageImportResult,
  AiImageManualPromptCopy,
  AiImageManualPromptSummary,
  AiImageMediaCategory,
  AiImageGeneratedImage,
  AiImagePromptDetail,
  AiImagePromptSummary,
  AiImageReferenceAssetRole,
  AiImageStore,
  Engine,
  EngineHealth,
  EngineMetrics,
  Job,
  JobEvent,
  JobMetrics,
  JobProgress,
  JobStatus,
  ListingAssetCandidates,
  ListingAssetPreview,
  ListingSources,
  LifetimeMetrics,
  ListingWorkspaceManifest,
  MockupBackground,
  QueuedJobInfo,
  VideoPreset,
} from "./types";

import { diagnoseApiFailure, readApiEnvironment, type ApiDiagnostic } from "./diagnostics";

/** Resolved once at module load, the same moment Vite injects the env --
 * a value that is set-but-unusable falls back rather than building
 * malformed request URLs, and the reason is preserved for diagnostics. */
export const API_ENVIRONMENT = readApiEnvironment(import.meta.env.VITE_API_BASE_URL, import.meta.env.DEV);

const BASE_URL = API_ENVIRONMENT.effective;
const REQUEST_TIMEOUT_MS = 8000;

/**
 * Thrown for every failure mode the UI needs to distinguish: a reachable
 * server returning a structured error (`status` set, `detail` from the
 * body), a request that timed out (`kind: "timeout"`), or the server
 * being unreachable at all (`kind: "network"`). Components branch on
 * `kind`/`status` rather than parsing error strings.
 *
 * `diagnostic` carries the actionable classification -- which of "backend
 * not running", "wrong port", "missing VITE_API_BASE_URL", "invalid
 * config", "timeout" or "HTTP error" this actually is, plus the checks
 * that resolve it. See api/diagnostics.ts.
 */
export class ApiError extends Error {
  readonly kind: "http" | "timeout" | "network";
  readonly status: number | null;
  readonly detail: unknown;
  readonly diagnostic: ApiDiagnostic | null;

  constructor(
    message: string,
    kind: "http" | "timeout" | "network",
    status: number | null = null,
    detail: unknown = null,
    diagnostic: ApiDiagnostic | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.detail = detail;
    this.diagnostic = diagnostic;
  }
}

/** Builds the ApiError for one failed attempt, with its diagnostic. */
function apiFailure(
  kind: "http" | "timeout" | "network",
  path: string,
  options: { status?: number | null; serverMessage?: string | null; detail?: unknown } = {},
): ApiError {
  const status = options.status ?? null;
  const diagnostic = diagnoseApiFailure({
    kind,
    path,
    url: `${BASE_URL}${path}`,
    status,
    serverMessage: options.serverMessage ?? null,
    timeoutMs: REQUEST_TIMEOUT_MS,
    environment: API_ENVIRONMENT,
  });
  return new ApiError(options.serverMessage ?? diagnostic.title, kind, status, options.detail ?? null, diagnostic);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw apiFailure("timeout", path);
    }
    throw apiFailure("network", path);
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON (or was empty) — detail stays null */
    }
    const message =
      (detail && typeof detail === "object" && "detail" in detail && typeof (detail as { detail: unknown }).detail === "string")
        ? (detail as { detail: string }).detail
        : `${path} failed with ${response.status}`;
    throw apiFailure("http", path, { status: response.status, serverMessage: message, detail });
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// -- Jobs --------------------------------------------------------------

export function listJobs(filters?: { status?: JobStatus; engine_id?: string }): Promise<Job[]> {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.engine_id) params.set("engine_id", filters.engine_id);
  const query = params.toString();
  return request<Job[]>(`/jobs${query ? `?${query}` : ""}`);
}

export function getJob(jobId: string): Promise<Job> {
  return request<Job>(`/jobs/${jobId}`);
}

export function createJob(engineId: string, config: Record<string, unknown>): Promise<Job> {
  return request<Job>("/jobs", { method: "POST", body: JSON.stringify({ engine_id: engineId, config }) });
}

export function cancelJob(jobId: string): Promise<Job> {
  return request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" });
}

/**
 * Prompt 4 (Cleanup & Job Lifecycle): safely deletes a Job -- server-side
 * this refuses an active Job (409) and recycles the Job's own generated
 * output to the Windows Recycle Bin (never a permanent delete). See
 * infra/cleanup.py.
 */
export function deleteJob(jobId: string): Promise<void> {
  return request(`/jobs/${jobId}`, { method: "DELETE" });
}

export function getJobEvents(jobId: string): Promise<JobEvent[]> {
  return request<JobEvent[]>(`/jobs/${jobId}/events`);
}

export function getJobProgress(jobId: string): Promise<JobProgress> {
  return request<JobProgress>(`/jobs/${jobId}/progress`);
}

export function getJobMetrics(jobId: string): Promise<JobMetrics> {
  return request<JobMetrics>(`/jobs/${jobId}/metrics`);
}

// -- Engines -------------------------------------------------------------

export function listEngines(): Promise<Engine[]> {
  return request<Engine[]>("/engines");
}

export function getEngineHealth(engineId: string): Promise<EngineHealth> {
  return request<EngineHealth>(`/engines/${engineId}/health`);
}

export function getEngineQueue(engineId: string): Promise<QueuedJobInfo[]> {
  return request<QueuedJobInfo[]>(`/engines/${engineId}/queue`);
}

export function getEngineMetrics(engineId: string): Promise<EngineMetrics> {
  return request<EngineMetrics>(`/engines/${engineId}/metrics`);
}

// -- Activity --------------------------------------------------------------

export function getActivity(limit = 50): Promise<JobEvent[]> {
  return request<JobEvent[]>(`/activity?limit=${limit}`);
}

// -- Lifetime metrics ------------------------------------------------------

/**
 * The Dashboard's headline accomplishment numbers, read from the server's
 * permanent completed-workflow ledger rather than recomputed from the Job
 * rows that happen to still exist.
 *
 * That difference is the point: these used to be derived here in the
 * browser by grouping succeeded jobs, so deleting old job history reduced
 * Time Saved and Lifetime Production. Deleting history is housekeeping --
 * it doesn't undo work that was completed. See infra/lifetime_metrics.py.
 */
export function getLifetimeMetrics(): Promise<LifetimeMetrics> {
  return request<LifetimeMetrics>("/metrics/lifetime");
}

export function getHealth(): Promise<{ status: string; service: string; version: string }> {
  return request(`/health`);
}

// -- Video Generator operator workflow (Step 12) ----------------------------
// Scoped entirely to etsy-video-generator. launchVideoJob() deliberately
// does NOT go through request() above: it sends multipart form data (so it
// must not force a JSON Content-Type header) and the server-side launch can
// legitimately block for up to the engine's configured timeout (~120s),
// far past request()'s 8s default — see EngineDetail's video launch
// workflow for how callers avoid blocking navigation on this promise.

const VIDEO_LAUNCH_TIMEOUT_MS = 130_000;

export function fetchVideoPresets(): Promise<VideoPreset[]> {
  return request<VideoPreset[]>("/video-generator/presets");
}

export async function launchVideoJob(files: File[], presetKey: string, designId: string | null = null): Promise<Job> {
  const form = new FormData();
  form.set("preset_key", presetKey);
  for (const file of files) form.append("images", file, file.name);
  if (designId) form.append("design_id", designId);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), VIDEO_LAUNCH_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/video-generator/jobs`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("Launch request timed out client-side (the job may still be running server-side).", "timeout");
    }
    throw apiFailure("network", "/video-generator/jobs");
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON */
    }
    const message =
      detail && typeof detail === "object" && "detail" in detail
        ? typeof (detail as { detail: unknown }).detail === "string"
          ? (detail as { detail: string }).detail
          : JSON.stringify((detail as { detail: unknown }).detail)
        : `launch failed with ${response.status}`;
    throw new ApiError(message, "http", response.status, detail);
  }

  return (await response.json()) as Job;
}

export function getJobOutputVideoUrl(jobId: string): string {
  return `${BASE_URL}/jobs/${jobId}/output-video`;
}

export function openOutputFolder(jobId: string): Promise<{ opened: string }> {
  return request(`/jobs/${jobId}/open-output-folder`, { method: "POST" });
}

export function openListingOutputs(jobId: string): Promise<{ opened: string }> {
  return request(`/jobs/${jobId}/open-listing-outputs`, { method: "POST" });
}

/** Opens the folder where this ENGINE collects its generated work, for the
 * engine page's top-level "Open Output Folder" action. Distinct from the
 * per-job routes above, which stay scoped to one job's output. */
export function openEngineOutputFolder(engineId: string): Promise<{ opened: string }> {
  return request(`/engines/${encodeURIComponent(engineId)}/open-output-folder`, { method: "POST" });
}

// -- Mockup Generator operator workflow (Step 13) ----------------------------
// Scoped entirely to etsy-mockup-generator. The preview launch, like
// launchVideoJob() above, sends multipart form data and can legitimately
// block for up to the engine's configured worker timeout (300s) — a
// generous client-side timeout, well past that, avoids the browser giving
// up on a request the server is still legitimately working on.

const MOCKUP_LAUNCH_TIMEOUT_MS = 310_000;

export function fetchMockupBackgrounds(): Promise<MockupBackground[]> {
  return request<MockupBackground[]>("/mockup-generator/backgrounds");
}

async function postMockupPreviewForm(form: FormData): Promise<Job> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), MOCKUP_LAUNCH_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/mockup-generator/jobs/preview`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("Preview request timed out client-side (the job may still be running server-side).", "timeout");
    }
    throw apiFailure("network", "/mockup-generator/jobs/preview");
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON */
    }
    const message =
      detail && typeof detail === "object" && "detail" in detail
        ? typeof (detail as { detail: unknown }).detail === "string"
          ? (detail as { detail: string }).detail
          : JSON.stringify((detail as { detail: unknown }).detail)
        : `preview launch failed with ${response.status}`;
    throw new ApiError(message, "http", response.status, detail);
  }

  return (await response.json()) as Job;
}

export function launchMockupPreviewJob(zipFile: File, backgroundPath: string, designId: string | null): Promise<Job> {
  const form = new FormData();
  form.append("zip_file", zipFile, zipFile.name);
  form.append("background_path", backgroundPath);
  if (designId) form.append("design_id", designId);
  return postMockupPreviewForm(form);
}

export function launchMockupPreviewJobFromStaged(
  stagedZipPath: string,
  backgroundPath: string,
  designId: string | null,
): Promise<Job> {
  const form = new FormData();
  form.append("staged_zip_path", stagedZipPath);
  form.append("background_path", backgroundPath);
  if (designId) form.append("design_id", designId);
  return postMockupPreviewForm(form);
}

/**
 * Root cause of the false-timeout bug (live operator testing, Mockup
 * Generator full-batch runs): this call used to go through request()
 * above, which applies the generic REQUEST_TIMEOUT_MS (8000ms) meant for
 * ordinary metadata reads/writes. POST /mockup-generator/jobs/{id}/batch
 * runs coordinator.evaluate() synchronously in the request handler --
 * exactly like the preview launch above -- and can legitimately take
 * well past 8s for a real batch. The browser aborted the fetch at 8s,
 * the UI reported "timed out" and marked the workflow failed, while the
 * server (unaware the client gave up -- FastAPI/Starlette doesn't check
 * for client disconnection on a synchronous, run_in_threadpool handler
 * like this one) kept running to completion and persisted the Job as
 * SUCCEEDED seconds or minutes later. Confirmed live: the "failed"
 * batch's Job was found SUCCEEDED in the database immediately after.
 *
 * Fixed at two layers:
 *   1. Here: a realistic client-side timeout (matching the preview
 *      launch's, since both block on the same class of engine work),
 *      so a normal-length batch doesn't abort in the first place.
 *   2. In MockupLaunchWorkflow.tsx: this promise's resolution is no
 *      longer what drives the UI's success/failure state at all -- that
 *      component polls the persisted Job (the existing Job-polling
 *      architecture already used by JobDetail/VideoLaunchWorkflow) until
 *      it reaches a terminal status, so the UI is correct even if THIS
 *      fetch times out, the tab was backgrounded, or the network blips
 *      mid-request. A timeout/network error from this function is
 *      therefore deliberately NOT treated as an authoritative failure by
 *      its caller -- only a real HTTP error response is.
 */
export async function launchMockupBatchJob(previewJobId: string): Promise<Job> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), MOCKUP_LAUNCH_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/mockup-generator/jobs/${previewJobId}/batch`, {
      method: "POST",
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("Batch request timed out client-side (the job may still be running server-side).", "timeout");
    }
    throw apiFailure("network", `/mockup-generator/jobs/${previewJobId}/batch`);
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON */
    }
    const message =
      detail && typeof detail === "object" && "detail" in detail
        ? typeof (detail as { detail: unknown }).detail === "string"
          ? (detail as { detail: string }).detail
          : JSON.stringify((detail as { detail: unknown }).detail)
        : `batch launch failed with ${response.status}`;
    throw new ApiError(message, "http", response.status, detail);
  }

  return (await response.json()) as Job;
}

export function getMockupPreviewImageUrl(jobId: string): string {
  return `${BASE_URL}/mockup-generator/jobs/${jobId}/preview-image`;
}

export function getMockupResultImageUrl(jobId: string): string {
  return `${BASE_URL}/mockup-generator/jobs/${jobId}/result-image`;
}

/** One generated mockup by filename, within that job's own assets folder
 * — what lets Listing Assets show individual mockups rather than one
 * thumbnail per batch. */
export function getMockupAssetFileUrl(jobId: string, filename: string): string {
  return `${BASE_URL}/mockup-generator/jobs/${jobId}/assets/${encodeURIComponent(filename)}/file`;
}

export function openMockupOutputFolder(jobId: string): Promise<{ opened: string }> {
  return request(`/mockup-generator/jobs/${jobId}/open-output-folder`, { method: "POST" });
}

export function openMockupListingOutputs(jobId: string): Promise<{ opened: string }> {
  return request(`/mockup-generator/jobs/${jobId}/open-listing-outputs`, { method: "POST" });
}

// -- AI Image Generator operator workflow (Step 14) --------------------------
// Scoped entirely to etsy-ai-image-generator. Job creation/listing/status
// reads are cheap, local, synchronous calls (like fetchMockupBackgrounds())
// and go through the generic request() helper. advanceAiImageJob() is the
// one call that can legitimately run a real Claude/OpenAI API call for a
// while -- it gets its own realistic timeout, exactly like
// launchMockupBatchJob() (see that function's docstring for the full
// false-timeout diagnosis this pattern exists to avoid). Its resolution
// is also not what should drive UI state -- callers should poll the
// returned/created Job (GET /jobs/{id}) the same way
// MockupLaunchWorkflow.tsx already does.

const AI_IMAGE_ADVANCE_TIMEOUT_MS = 610_000;

export function listAiImageStores(): Promise<AiImageStore[]> {
  return request<AiImageStore[]>("/image-generator/stores");
}

export function listAiImageJobs(): Promise<AiImageJobManifest[]> {
  return request<AiImageJobManifest[]>("/image-generator/jobs");
}

export function getAiImageJobStatus(jobName: string): Promise<AiImageJobManifest> {
  return request<AiImageJobManifest>(`/image-generator/jobs/${encodeURIComponent(jobName)}/status`);
}

export interface CreateAiImageJobPayload {
  product_name: string;
  store_id: string;
  campaign_id: string;
  product_type: string;
  concept_counts?: Record<string, number>;
  creative_notes?: string;
  product_color?: string;
}

export function createAiImageJob(payload: CreateAiImageJobPayload): Promise<{ job_name: string; job_folder: string }> {
  return request("/image-generator/jobs", { method: "POST", body: JSON.stringify(payload) });
}

export async function advanceAiImageJob(jobName: string): Promise<Job> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), AI_IMAGE_ADVANCE_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/image-generator/jobs/${encodeURIComponent(jobName)}/advance`, {
      method: "POST",
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("Advance request timed out client-side (the job may still be running server-side).", "timeout");
    }
    throw apiFailure("network", `/image-generator/jobs/${encodeURIComponent(jobName)}/advance`);
  } finally {
    clearTimeout(timeout);
  }

  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON */
    }
    const message =
      detail && typeof detail === "object" && "detail" in detail
        ? typeof (detail as { detail: unknown }).detail === "string"
          ? (detail as { detail: string }).detail
          : JSON.stringify((detail as { detail: unknown }).detail)
        : `advance failed with ${response.status}`;
    throw new ApiError(message, "http", response.status, detail);
  }

  return (await response.json()) as Job;
}

/** Which folder an "open" action should land the operator in.
 *  images    -- the deepest folder of real generated images, e.g.
 *               outputs/generated/ai_product_mockups/ai_01/
 *  job_files -- the whole job package (jobs/<job_name>/).
 * Adding a third destination later is a new value here plus a new entry
 * in the route's _OPEN_TARGETS -- no new endpoint, no new function. */
export type OpenTarget = "images" | "job_files";

export function openAiImageOutputFolder(jobName: string, target: OpenTarget = "images"): Promise<{ opened: string }> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/open-output-folder?target=${target}`,
    { method: "POST" },
  );
}

// -- AI Image Generator Manual Mode (Prompt 2) --------------------------
// Every call here is fast, local, synchronous work (no external API call)
// so the generic request() helper's 8s timeout is fine -- none of these
// need the launchMockupBatchJob()-style long-timeout treatment.

export function exportAiImageManualConcepts(jobName: string): Promise<AiImageManualConceptExport> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/manual/concepts/export`, { method: "POST" });
}

export function importAiImageManualConcepts(jobName: string, responseJsonText: string): Promise<AiImageManualConceptImportResult> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/manual/concepts/import`, {
    method: "POST",
    body: JSON.stringify({ response_json_text: responseJsonText }),
  });
}

export function listAiImageManualPrompts(jobName: string): Promise<AiImageManualPromptSummary[]> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/manual/prompts`);
}

export function copyAiImageManualPrompt(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
): Promise<AiImageManualPromptCopy> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/manual/prompts/${category}/${encodeURIComponent(conceptId)}/copy`,
    { method: "POST" },
  );
}

export async function importAiImageManualImage(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
  file: File,
): Promise<AiImageManualImageImportResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(
    `${BASE_URL}/image-generator/jobs/${encodeURIComponent(jobName)}/manual/images/${category}/${encodeURIComponent(conceptId)}`,
    { method: "POST", body: form },
  );
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON */
    }
    const message =
      detail && typeof detail === "object" && "detail" in detail && typeof (detail as { detail: unknown }).detail === "string"
        ? (detail as { detail: string }).detail
        : `image import failed with ${response.status}`;
    throw new ApiError(message, "http", response.status, detail);
  }
  return (await response.json()) as AiImageManualImageImportResult;
}

/**
 * Import a batch of already-finished images as this job's deliverables,
 * completing the job.
 *
 * The manual equivalent of the whole OpenAI generation step, for work done
 * outside the Controller. Distinct from importAiImageManualImage() above,
 * which attaches ONE image to ONE built prompt package as part of the
 * per-concept manual flow. This needs no concepts and no prompts, and the
 * job is finished when it returns -- there is no review step after it,
 * because the operator already chose these images by hand.
 *
 * Uses fetch directly rather than request(): multipart upload, and the
 * generic helper's 8s timeout is too short for a batch of full-size images.
 */
export async function importAiImageFinishedImages(
  jobName: string,
  category: AiImageMediaCategory,
  files: File[],
): Promise<AiImageFinishedImageImportResult> {
  const form = new FormData();
  form.append("category", category);
  for (const file of files) form.append("files", file, file.name);

  const response = await fetch(
    `${BASE_URL}/image-generator/jobs/${encodeURIComponent(jobName)}/manual/finished-images`,
    { method: "POST", body: form },
  );
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON */
    }
    // The route reports per-file rejections as {detail: {errors: [...]}} so
    // the operator is told which file was the problem, not just that one was.
    const inner = detail && typeof detail === "object" ? (detail as { detail?: unknown }).detail : null;
    let message = `image import failed with ${response.status}`;
    if (typeof inner === "string") {
      message = inner;
    } else if (inner && typeof inner === "object" && Array.isArray((inner as { errors?: unknown }).errors)) {
      message = ((inner as { errors: string[] }).errors).join(" · ");
    }
    throw new ApiError(message, "http", response.status, detail);
  }
  return (await response.json()) as AiImageFinishedImageImportResult;
}

// -- Concept Review -----------------------------------------------------
// Every call here is fast, local, synchronous file reads/writes (no
// external API call) -- the generic request() helper's 8s timeout is
// fine, same reasoning as Manual Mode above. The Controller never
// interprets a concept or an approval decision; every function here is a
// thin pass-through to concept_review.py's own real functions (see
// Etsy-AI-Image-Generator/src/headless.py's Concept Review section).

export function listAiImageConcepts(jobName: string, category: AiImageMediaCategory): Promise<AiImageConcept[]> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/concepts/${category}`);
}

export function approveAiImageConcept(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
): Promise<AiImageConceptDecisionResult> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/concepts/${category}/${encodeURIComponent(conceptId)}/approve`,
    { method: "POST" },
  );
}

export function rejectAiImageConcept(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
): Promise<AiImageConceptDecisionResult> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/concepts/${category}/${encodeURIComponent(conceptId)}/reject`,
    { method: "POST" },
  );
}

export function getAiImageConceptProvider(jobName: string): Promise<AiImageConceptProviderInfo> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/concept-provider`);
}

// -- Prompt Review ----------------------------------------------------------
// markAiImagePromptReviewComplete() delegates to the engine's own reusable
// prompt_builder.mark_prompt_review_complete() (via the headless adapter) --
// this never writes a marker file itself, and returns the engine's
// refreshed job status so the caller doesn't need a second request to see
// next_step move to Generate Images.

export function listAiImagePrompts(jobName: string): Promise<AiImagePromptSummary[]> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/prompts`);
}

export async function getAiImagePromptText(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
): Promise<string> {
  const result = await request<{ text: string }>(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/prompts/${category}/${encodeURIComponent(conceptId)}/text`,
  );
  return result.text;
}

/** The structured prompt for the viewer: operator-facing text separated
 * from technical metadata. getAiImagePromptText() above still returns the
 * engine's single formatted blob and is unchanged. */
export function getAiImagePromptDetail(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
): Promise<AiImagePromptDetail> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/prompts/${category}/${encodeURIComponent(conceptId)}/detail`,
  );
}

/** Removes one prompt from the generation set, so only the remaining
 * prompts are sent to OpenAI. Returns the refreshed job status. */
export function deleteAiImagePrompt(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
): Promise<{ deleted: string; category: string; job_status: AiImageJobManifest }> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/prompts/${category}/${encodeURIComponent(conceptId)}`,
    { method: "DELETE" },
  );
}

export function markAiImagePromptReviewComplete(jobName: string): Promise<AiImageJobManifest> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/prompts/review-complete`, {
    method: "POST",
  });
}

// -- Image Review ----------------------------------------------------------
// The in-app replacement for the engine's interactive Review Images
// screen. Approving/rejecting routes through the engine's own image_review
// logic, which owns outputs/approved|rejected/ and the approved-media
// handoff -- nothing here decides what "approved" means.

export function listAiImageGeneratedImages(jobName: string): Promise<AiImageGeneratedImage[]> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/generated-images`);
}

export function getAiImageGeneratedImageUrl(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
  filename: string,
): string {
  return `${BASE_URL}/image-generator/jobs/${encodeURIComponent(jobName)}/generated-images/${category}/${encodeURIComponent(conceptId)}/${encodeURIComponent(filename)}/file`;
}

export function setAiImageReviewStatus(
  jobName: string,
  category: AiImageMediaCategory,
  conceptId: string,
  status: "approved" | "rejected",
): Promise<{ concept_id: string; review_status: string; job_status: AiImageJobManifest }> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/generated-images/${category}/${encodeURIComponent(conceptId)}/review`,
    { method: "POST", body: JSON.stringify({ status }), headers: { "Content-Type": "application/json" } },
  );
}

// -- Reference Image Management ------------------------------------------
// Reference images are a persistent job resource, not a workflow stage --
// these are callable regardless of pipeline state. The Controller performs
// no content/format/size/filename validation of its own; every check (and
// every error message surfaced here) comes from the engine's
// reference_images.py.

export function listAiImageReferenceImages(jobName: string): Promise<string[]> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/reference-images`);
}

export function getAiImageReferenceImageRoles(jobName: string): Promise<AiImageReferenceAssetRole[]> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/reference-images/roles`);
}

export function getAiImageReferenceImageFileUrl(jobName: string, filename: string): string {
  return `${BASE_URL}/image-generator/jobs/${encodeURIComponent(jobName)}/reference-images/${encodeURIComponent(filename)}/file`;
}

export async function addAiImageReferenceImage(jobName: string, file: File): Promise<{ filename: string }> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(
    `${BASE_URL}/image-generator/jobs/${encodeURIComponent(jobName)}/reference-images`,
    { method: "POST", body: form },
  );
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = await response.json();
    } catch {
      /* body wasn't JSON */
    }
    const message =
      detail && typeof detail === "object" && "detail" in detail && typeof (detail as { detail: unknown }).detail === "string"
        ? (detail as { detail: string }).detail
        : `reference image upload failed with ${response.status}`;
    throw new ApiError(message, "http", response.status, detail);
  }
  return (await response.json()) as { filename: string };
}

export function removeAiImageReferenceImage(jobName: string, filename: string): Promise<{ filename: string }> {
  return request(`/image-generator/jobs/${encodeURIComponent(jobName)}/reference-images/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
}

export function correctAiImageReferenceImageRole(
  jobName: string,
  filename: string,
  role: string,
): Promise<AiImageReferenceAssetRole[]> {
  return request(
    `/image-generator/jobs/${encodeURIComponent(jobName)}/reference-images/${encodeURIComponent(filename)}/role`,
    { method: "POST", body: JSON.stringify({ role }) },
  );
}

// -- Listing Workspace (Controller feature, Prompt 3) --------------------
// Every call here is fast, local, synchronous file copying -- no external
// API/provider call -- so the generic request() helper's 8s timeout is
// fine, same reasoning as the Manual Mode calls above.

export interface BuildListingWorkspacePayload extends ListingSources {
  listing_id: string;
  /** The candidate filenames the operator kept selected. Omit for "all of
   * it" — see infra/listing_workspace.py's build(). */
  selected_filenames?: string[] | null;
}

export function buildListingWorkspace(payload: BuildListingWorkspacePayload): Promise<ListingWorkspaceManifest> {
  return request("/listing-workspace/build", { method: "POST", body: JSON.stringify(payload) });
}

/**
 * Every individually selectable asset behind these source jobs. Read-only
 * despite being a POST (the sources are lists) — nothing is built.
 */
export function listListingAssetCandidates(sources: ListingSources): Promise<ListingAssetCandidates> {
  return request("/listing-workspace/candidates", { method: "POST", body: JSON.stringify(sources) });
}

/**
 * The URL that previews one candidate asset, resolved from the descriptor
 * the Controller returned. Every branch is an endpoint that engine's own
 * pages already use — this adds no new file-serving surface.
 */
export function listingAssetPreviewUrl(preview: ListingAssetPreview): string {
  switch (preview.kind) {
    case "mockup":
      return getMockupAssetFileUrl(preview.job, preview.file);
    case "ai_image":
      return getAiImageGeneratedImageUrl(preview.job, preview.category as AiImageMediaCategory, preview.concept_id, preview.file);
    case "video":
      return getJobOutputVideoUrl(preview.job);
  }
}

export function listListingWorkspaces(): Promise<ListingWorkspaceManifest[]> {
  return request("/listing-workspace");
}

export function getListingWorkspace(listingId: string): Promise<ListingWorkspaceManifest> {
  return request(`/listing-workspace/${encodeURIComponent(listingId)}`);
}

export function getListingWorkspaceAssetFileUrl(listingId: string, filename: string): string {
  return `${BASE_URL}/listing-workspace/${encodeURIComponent(listingId)}/assets/${encodeURIComponent(filename)}/file`;
}

export function openListingWorkspace(listingId: string): Promise<{ opened: string }> {
  return request(`/listing-workspace/${encodeURIComponent(listingId)}/open`, { method: "POST" });
}

/**
 * Prompt 4 (Cleanup & Job Lifecycle): recycles the built workspace folder
 * to the Windows Recycle Bin. Never touches the generators' own outputs --
 * see infra/cleanup.py.
 */
export function deleteListingWorkspace(listingId: string): Promise<void> {
  return request(`/listing-workspace/${encodeURIComponent(listingId)}`, { method: "DELETE" });
}
