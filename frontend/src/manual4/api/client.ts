/** Isolated API client for Manual4 (`/api/v1/manual4/*` only).
 *  Never calls seven-stage endpoints. */

const M4_API_BASE = '/api/v1';

export type M4StageKey = 'storyboard' | 'materials' | 'production' | 'delivery';

export type M4Blocker = {
  code: string;
  stage: M4StageKey;
  message: string;
  action?: string;
  shot_id?: string;
  shot_code?: string;
  requirement_id?: string;
  run_id?: string;
};

export type M4StageSummary = {
  key: M4StageKey;
  title: string;
  done: boolean;
  status: 'done' | 'active' | 'blocked' | 'pending';
  blocker_count: number;
  blockers: M4Blocker[];
  facts: Record<string, unknown>;
};

export type M4ReadinessVerdict = {
  project_id: string;
  workflow_kind: 'manual4';
  next_stage: M4StageKey;
  all_done: boolean;
  stages: M4StageSummary[];
  can_confirm_storyboard: boolean;
  can_start_production: boolean;
  can_assemble_delivery: boolean;
  summary: Record<string, unknown>;
};

export type M4Overview = {
  project: {
    id: string;
    title: string;
    description: string;
    language: string;
    max_shot_seconds: number;
    workflow_kind: 'manual4';
    created_at: string;
  };
  readiness: M4ReadinessVerdict;
  worker_online: boolean;
  gpu: {
    busy: boolean;
    active_leases: Array<Record<string, unknown>>;
    reconcile_required: Array<Record<string, unknown>>;
  };
  recent_events: Array<{
    id: string;
    stage: string;
    event_type: string;
    entity_type: string;
    entity_id: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
};

export async function m4Request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers ?? undefined);
  const method = (init?.method ?? 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    headers.set('x-shine-local-action', '1');
  }
  const response = await fetch(`${M4_API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (typeof payload?.detail === 'string') detail = payload.detail;
      else if (payload?.detail?.message) detail = payload.detail.message;
      else if (payload?.detail?.code) detail = payload.detail.code;
    } catch { /* ignore body parse error */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function fetchM4Overview(projectId: string): Promise<M4Overview> {
  return m4Request<M4Overview>(`/manual4/projects/${projectId}/overview`, { cache: 'no-store' });
}

export type M4RequestedAsset = {
  family: 'character' | 'scene' | 'prop';
  name: string;
  variant?: string;
};

export type M4DraftShot = {
  episode_code: string;
  shot_no: number;
  shot_code: string;
  target_seconds: number | null;
  summary: string;
  dialogue: Array<{ speaker: string; text: string }>;
  requested_assets: M4RequestedAsset[];
  source_span?: Record<string, unknown>;
  aspect_ratio?: string;
  continuity_mode?: string;
};

export type M4DraftEpisode = {
  episode_no: number;
  episode_code: string;
  title: string;
  target_total_seconds: number | null;
  shots: M4DraftShot[];
};

export type M4StoryboardPayload = {
  episodes: M4DraftEpisode[];
  ambiguities: Array<{ code: string; message: string; episode_code?: string; shot_code?: string }>;
  extractor: string;
};

export type M4StoryboardState = {
  project_id: string;
  max_shot_seconds: number;
  draft: {
    revision_id: string | null;
    revision: number;
    status: string;
    source_sha256: string;
    summary_sha256: string;
    max_shot_seconds: number;
    payload: M4StoryboardPayload;
    confirmed_at: string | null;
  };
  confirmed: unknown | null;
  source_documents: Array<{ id: string; filename: string; source_format: string; sha256: string; byte_size: number }>;
  confirmed_episodes: Array<Record<string, unknown>>;
  confirmed_shots: Array<Record<string, unknown>>;
};

export function fetchM4StoryboardDraft(projectId: string): Promise<M4StoryboardState> {
  return m4Request<M4StoryboardState>(`/manual4/projects/${projectId}/storyboard/draft`, { cache: 'no-store' });
}

export function importM4Storyboard(
  projectId: string,
  input: { file?: File; text?: string; filename?: string; useLlm?: boolean; llmProfileId?: string; remoteConsent?: boolean },
): Promise<{ document: Record<string, unknown>; draft: M4StoryboardState['draft'] }> {
  const form = new FormData();
  if (input.file) form.append('file', input.file);
  if (input.text !== undefined) form.append('text', input.text);
  if (input.filename) form.append('filename', input.filename);
  if (input.useLlm) form.append('use_llm', 'true');
  if (input.llmProfileId) form.append('llm_profile_id', input.llmProfileId);
  if (input.remoteConsent) form.append('remote_consent', 'true');
  return m4Request(`/manual4/projects/${projectId}/storyboard/import`, { method: 'POST', body: form });
}

export function saveM4StoryboardDraft(
  projectId: string,
  payload: M4StoryboardPayload,
  expectedRevision?: number,
): Promise<{ draft: M4StoryboardState['draft'] }> {
  return m4Request(`/manual4/projects/${projectId}/storyboard/draft`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload, expected_revision: expectedRevision ?? null }),
  });
}

export function confirmM4Storyboard(
  projectId: string,
  opts: { expectedRevision?: number; acknowledgeDurationMismatch?: boolean },
): Promise<{ confirmed: M4StoryboardState['draft']; invalidated_prompts: string[]; requirements_count: number }> {
  return m4Request(`/manual4/projects/${projectId}/storyboard/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      expected_revision: opts.expectedRevision ?? null,
      acknowledge_duration_mismatch: Boolean(opts.acknowledgeDurationMismatch),
    }),
  });
}

export type M4AssetVersion = {
  id: string;
  project_id: string;
  requirement_id: string | null;
  family: string;
  entity_name: string;
  variant_name: string;
  source_kind: string;
  filename: string;
  content_type: string;
  sha256: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  duration_s: number | null;
  status: string;
  is_mock: boolean;
  provenance: Record<string, unknown>;
  promoted_by: string | null;
  promoted_at: string | null;
  created_at: string;
  media_url: string;
};

export type M4MaterialRequirement = {
  id: string;
  project_id: string;
  storyboard_revision_id: string;
  family: string;
  entity_name: string;
  variant_name: string;
  canonical_key: string;
  description: string;
  shot_ids: string[];
  status: string;
  bound_asset_version_id: string | null;
  user_declined: boolean;
  bound_asset: M4AssetVersion | null;
  candidates: M4AssetVersion[];
  reusable_assets: M4AssetVersion[];
};

export type M4ShotBinding = {
  id: string;
  asset_version_id: string;
  use_as: string;
  role_label: string;
  ordinal: number;
  asset: M4AssetVersion;
};

export type M4MaterialShot = {
  id: string;
  episode_id: string;
  shot_no: number;
  shot_code: string;
  revision: number;
  target_seconds: number;
  summary: string;
  bindings: M4ShotBinding[];
  ref2va_ready: boolean;
};

export type M4MaterialsState = {
  confirmed_revision: { id: string; revision: number } | null;
  requirements: M4MaterialRequirement[];
  assets: M4AssetVersion[];
  shots: M4MaterialShot[];
  readiness: M4Overview['readiness'];
};

export function fetchM4Materials(projectId: string): Promise<M4MaterialsState> {
  return m4Request<M4MaterialsState>(`/manual4/projects/${projectId}/materials/requirements`, { cache: 'no-store' });
}

export function selectOrGenerateM4Material(
  projectId: string,
  reqId: string,
  body: {
    mode: 'generate' | 'reuse';
    asset_version_id?: string;
    prompt?: string;
    width?: number;
    height?: number;
    seed?: number;
    steps?: number;
  },
): Promise<{ mode: string; asset: M4AssetVersion; state: M4MaterialsState }> {
  return m4Request(`/manual4/projects/${projectId}/materials/${reqId}/select-or-generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function declineM4Material(
  projectId: string,
  reqId: string,
  reason = '',
): Promise<{ requirement_id: string; status: string; state: M4MaterialsState }> {
  return m4Request(`/manual4/projects/${projectId}/materials/${reqId}/decline`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
}

export function uploadM4Asset(
  projectId: string,
  input: { family: string; entity_name: string; variant_name?: string; requirement_id?: string; file: File },
): Promise<{ asset: M4AssetVersion; state: M4MaterialsState }> {
  const form = new FormData();
  form.append('family', input.family);
  form.append('entity_name', input.entity_name);
  if (input.variant_name) form.append('variant_name', input.variant_name);
  if (input.requirement_id) form.append('requirement_id', input.requirement_id);
  form.append('file', input.file);
  return m4Request(`/manual4/projects/${projectId}/assets/upload`, { method: 'POST', body: form });
}

export function promoteM4Asset(
  projectId: string,
  assetId: string,
  requirementId?: string,
): Promise<{ asset: M4AssetVersion; bound_shots: string[]; state: M4MaterialsState }> {
  return m4Request(`/manual4/projects/${projectId}/assets/${assetId}/promote`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requirement_id: requirementId ?? null }),
  });
}

export function putM4ShotBindings(
  projectId: string,
  shotId: string,
  bindings: Array<{ asset_version_id: string; use_as?: string; role_label?: string }>,
): Promise<{ shot_id: string; state: M4MaterialsState }> {
  return m4Request(`/manual4/projects/${projectId}/shots/${shotId}/bindings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bindings }),
  });
}

export type M4PromptRevision = {
  id: string;
  shot_id: string;
  revision: number;
  status: string;
  prompt_text: string;
  prompt_sha256: string;
  shot_revision: number;
  target_seconds: number;
  generation_mode: string;
  reference_manifest: Array<{
    asset_version_id: string;
    h3_label: string;
    role_label: string;
    use_as: string;
    sha256: string;
  }>;
  reference_manifest_sha256: string;
  warnings: Array<{ code: string; severity: string; message: string }>;
  frozen_by: string | null;
  frozen_at: string | null;
  invalidated_reason: string | null;
  updated_at: string;
};

export type M4Job = {
  id: string;
  run_id: string;
  episode_id: string;
  shot_id: string;
  shot_revision: number;
  ordinal: number;
  status: string;
  attempt_count: number;
  target_seconds: number;
  continuity_mode: string;
  provider: string;
  output_sha256: string;
  output_bytes: number;
  measured_duration_s: number | null;
  measured_width: number | null;
  measured_height: number | null;
  measured_fps: number | null;
  qc_result: Record<string, unknown>;
  failure_code: string | null;
  failure_detail: string;
  media_url: string | null;
};

export type M4ProductionRun = {
  id: string;
  project_id: string;
  execution_snapshot_id: string;
  scope_kind: string;
  provider: string;
  status: string;
  worker_id: string | null;
  error_code: string | null;
  error_detail: string;
  jobs: M4Job[];
  created_at: string;
};

export type M4ProductionShot = {
  id: string;
  episode_id: string;
  shot_no: number;
  shot_code: string;
  revision: number;
  target_seconds: number;
  summary: string;
  dialogue: Array<{ speaker: string; text: string }>;
  aspect_ratio: string;
  continuity_mode: string;
  reference_manifest: M4PromptRevision['reference_manifest'];
  frozen_prompt: M4PromptRevision | null;
  draft_prompt: M4PromptRevision | null;
  active_prompt: M4PromptRevision | null;
  prompt_valid_for_run: boolean;
  prompt_invalid_reason: string | null;
  latest_job: M4Job | null;
};

export type M4ProductionState = {
  confirmed_revision: { id: string; revision: number } | null;
  episodes: Array<{
    id: string;
    episode_no: number;
    episode_code: string;
    title: string;
    planned_total_seconds: number;
    shots: M4ProductionShot[];
  }>;
  runs: M4ProductionRun[];
  options: {
    variants: Array<{ variant_id: string; label: string; verified: boolean; accelerators: string[] }>;
    accelerators: Array<{ id: string; steps: number; requires_pdd: boolean | null; label: string }>;
    frames: Array<{ orientation: string; aspect_ratio: string; scale: number; width: number; height: number }>;
  };
  readiness: M4Overview['readiness'];
};

export type M4RunConfigInput = {
  scope_kind: string;
  scope_target_ids: string[];
  variant_id: string;
  accelerator_id: string;
  resolution_scale: number;
  second_pass?: { enabled: boolean; denoise: number; latent_scale: number; nfe: string } | null;
  seed: number;
  provider?: string;
};

export function fetchM4ProductionState(projectId: string): Promise<M4ProductionState> {
  return m4Request<M4ProductionState>(`/manual4/projects/${projectId}/production/state`, { cache: 'no-store' });
}

export function updateM4ShotOptions(
  projectId: string,
  shotId: string,
  opts: { aspect_ratio?: string; continuity_mode?: string },
): Promise<{ shot_id: string; state: M4ProductionState }> {
  return m4Request(`/manual4/projects/${projectId}/shots/${shotId}/render-options`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  });
}

export function saveM4PromptDraft(
  projectId: string,
  shotId: string,
  promptText: string,
): Promise<{ prompt: M4PromptRevision; state: M4ProductionState }> {
  return m4Request(`/manual4/projects/${projectId}/shots/${shotId}/prompt/draft`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt_text: promptText }),
  });
}

export function freezeM4Prompt(
  projectId: string,
  shotId: string,
  promptText?: string,
): Promise<{ prompt: M4PromptRevision; state: M4ProductionState }> {
  return m4Request(`/manual4/projects/${projectId}/shots/${shotId}/prompt/freeze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt_text: promptText ?? null }),
  });
}

export function previewM4Snapshot(
  projectId: string,
  config: M4RunConfigInput,
): Promise<{ snapshot_sha256: string; items: Array<Record<string, unknown>> }> {
  return m4Request(`/manual4/projects/${projectId}/production/snapshot/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
}

export function createM4Run(
  projectId: string,
  config: M4RunConfigInput,
): Promise<{ run: M4ProductionRun; state: M4ProductionState }> {
  return m4Request(`/manual4/projects/${projectId}/production/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
}

export function controlM4Run(
  projectId: string,
  runId: string,
  action: 'pause' | 'resume' | 'cancel',
): Promise<{ run: M4ProductionRun; state: M4ProductionState }> {
  return m4Request(`/manual4/projects/${projectId}/production/runs/${runId}/${action}`, {
    method: 'POST',
  });
}

export function retakeM4Shot(
  projectId: string,
  shotId: string,
  config: M4RunConfigInput,
): Promise<{ run: M4ProductionRun; state: M4ProductionState }> {
  return m4Request(`/manual4/projects/${projectId}/shots/${shotId}/retake`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  });
}

export function reconcileM4Job(
  projectId: string,
  jobId: string,
  decision: 'requeue' | 'mark_failed',
): Promise<{ job: M4Job; state: M4ProductionState }> {
  return m4Request(`/manual4/projects/${projectId}/jobs/${jobId}/reconcile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  });
}

export type M4DeliveryAcceptance = {
  id: string;
  delivery_id: string;
  decision: 'accepted' | 'rejected';
  watched: boolean;
  evidence: string;
  delivery_sha256: string;
  actor: string;
  created_at: string;
};

export type M4Delivery = {
  id: string;
  project_id: string;
  run_id: string | null;
  episode_id: string;
  version: number;
  status: string;
  shots_digest: string;
  edl_json: Array<{ ordinal: number; shot_id: string; shot_code: string; job_id: string; sha256: string; in_s: number; out_s: number }>;
  sha256: string;
  byte_size: number | null;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  is_mock: boolean;
  has_subtitles: boolean;
  media_url: string | null;
  subtitle_url: string | null;
  acceptance: M4DeliveryAcceptance | null;
  created_at: string;
};

export type M4DeliveryEpisode = {
  id: string;
  episode_no: number;
  episode_code: string;
  title: string;
  ready_to_assemble: boolean;
  shots: Array<{
    shot_id: string;
    shot_code: string;
    target_seconds: number;
    passed_jobs: Array<{ id: string; output_sha256: string; measured_duration_s: number | null; provider: string; created_at: string }>;
  }>;
  deliveries: M4Delivery[];
};

export type M4DeliveryState = {
  confirmed_revision: { id: string; revision: number } | null;
  episodes: M4DeliveryEpisode[];
  deliveries: M4Delivery[];
  readiness: M4Overview['readiness'];
};

export function fetchM4DeliveryState(projectId: string): Promise<M4DeliveryState> {
  return m4Request<M4DeliveryState>(`/manual4/projects/${projectId}/delivery/state`, { cache: 'no-store' });
}

export function assembleM4EpisodeDelivery(
  projectId: string,
  episodeId: string,
  opts?: { job_overrides?: Record<string, string>; include_subtitles?: boolean; burn_subtitles?: boolean },
): Promise<{ delivery: M4Delivery; state: M4DeliveryState }> {
  return m4Request(`/manual4/projects/${projectId}/episodes/${episodeId}/deliveries/assemble`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts ?? {}),
  });
}

export function acceptM4Delivery(
  projectId: string,
  deliveryId: string,
  body: { decision: 'accepted' | 'rejected'; watched: boolean; evidence: string; expected_sha256: string },
): Promise<{ acceptance: M4DeliveryAcceptance; state: M4DeliveryState }> {
  return m4Request(`/manual4/projects/${projectId}/deliveries/${deliveryId}/accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
