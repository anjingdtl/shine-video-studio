export const API = '/api/v1';

/** 中文标签：校验错误里常见的分镜/风格字段，定位到用户能看懂的名字。 */
const FIELD_LABELS: Record<string, string> = {
  duration_target_s: '时长（4–15 秒）',
  story_beat: '镜头意图',
  name: '名称',
  characters: '人物',
  dialogue: '对白',
  end_s: '对白结束时间',
  start_s: '对白开始时间',
  title: '项目名称',
};

/** Turn a FastAPI/pydantic error payload into one readable line; the raw
 *  technical JSON stays available for support but is no longer the only
 *  thing the user sees. */
function describeDetail(detail: unknown): string {
  if (typeof detail === 'string') {
    if (detail === 'STYLE_APPROVAL_CONTENT_CHANGED') return '风格草稿已在其他页面修改，请检查后重新确认（STYLE_APPROVAL_CONTENT_CHANGED）。';
    if (detail === 'STYLE_APPROVAL_CONTENT_REQUIRED') return '本次批准缺少确认的风格内容，请刷新工作台后重试（STYLE_APPROVAL_CONTENT_REQUIRED）。';
    return detail;
  }
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      const loc: unknown[] = Array.isArray(item?.loc) ? item.loc : [];
      const field = String(loc.at(-1) ?? '');
      let msg = String(item.msg ?? '');
      if (item.type === 'value_error' && msg.startsWith('Value error, ')) msg = msg.slice('Value error, '.length);
      const prefix = FIELD_LABELS[field] ?? (field === 'spec' ? '' : loc.filter((part) => part !== 'body').join('.'));
      return prefix ? `${prefix}：${msg}` : msg;
    });
    return `服务器拒绝了这次提交（${parts.join('；')}）`;
  }
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const code = 'code' in detail && detail.code ? `（${String(detail.code)}）` : '';
    return `${String(detail.message)}${code}`;
  }
  return JSON.stringify(detail);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('X-Shine-Local-Action', '1');
  const response = await fetch(`${API}${path}`, { ...init, headers });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.detail === undefined ? JSON.stringify(body) : describeDetail(body.detail);
    } catch { /* keep HTTP status */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export type Style = {
  photography_language: string; shot_blocking: string; color_palette: string;
  lighting_tone: string; lens_texture: string; editing_rhythm: string; sound_performance: string;
};

export type Project = {
  id: string; title: string; description: string; language: string;
  frozen_style_version_id: string | null; style_draft: Style | null;
  allow_remote_review: boolean; auto_repair_enabled: boolean; created_at: string;
};

export type Asset = {
  id: string; project_id: string; filename: string; kind: string; content_type: string;
  sha256: string; byte_size: number; role: string | null; entity_id: string | null;
  tags: string[]; duration_s: number | null; width: number | null; height: number | null;
  approved: boolean; locked: boolean; approved_at: string | null; locked_at: string | null;
};

export type StyleVersion = {
  id: string; project_id: string; version: number; sha256: string; snapshot: Style;
  approved_by: string; approval_note: string; frozen_at: string;
  impact?: { affected_shot_ids: string[]; invalidated_job_ids: string[] };
};

export type Binding = {
  id: string; asset_id: string; filename: string; kind: string; use_as: string;
  role: string | null; frame_role: string | null; ordinal: number; sha256: string;
};

export type Shot = {
  id: string; project_id: string; name: string; revision: number;
  spec: {
    story_beat?: string; duration_target_s?: number; style_version_id?: string;
    characters?: string[]; required_asset_ids?: string[]; dialogue?: Array<{
      speaker_id: string; text: string; start_s: number; end_s: number;
    }>; continuity?: Record<string, unknown>;
  };
  bindings: Binding[];
};

export type PromptPreview = {
  shot_id: string; shot_revision: number; mode: string; compiled_prompt: string;
  sections: Record<string, string>; prompt_sha256: string; compiler_version: string;
  rule_version: string; labels: Record<string, string>; warnings: string[];
  reference_manifest: Array<{ label: string; asset_id: string; sha256: string; use_as: string }>;
  style_version_id: string; style_hash: string;
};

export type JobAttempt = {
  id: string; attempt_no: number; status: string; provider_job_id: string | null;
  failure_code: string | null; submit_intent_at: string; submit_ack_at: string | null;
  progress: number; output_sha256: string | null; output_bytes: number | null;
  reconciliation_state: string | null; request_fingerprint: string;
  attempt_kind?: string; parent_attempt_id?: string | null; prompt_sha256?: string | null;
  parameter_sha256?: string | null; manifest_sha256?: string | null;
  prompt_graph_sha256?: string | null; repair_reason?: string | null;
};

export type QualityGate = {
  id: string; job_id: string; attempt_id: string | null; artifact_id: string | null;
  gate_name: string; status: string; required: boolean; is_mock: boolean;
  model: string | null; confidence: number | null; error_code: string | null;
  coverage: string; evidence: Record<string, unknown>; created_at: string;
};

export type RepairRecord = {
  id: string; action: string; root_cause: string; changes: string[]; outcome: string; created_at: string;
};

export type Job = {
  id: string; project_id: string; shot_id: string; shot_name: string; shot_revision: number;
  status: string; state_version: number; provider: string; simulated: boolean;
  video_created: boolean; paused: boolean; stale: boolean; failure_code: string | null;
  provider_job_id: string | null; semantic_retries: number; max_semantic_retries: number;
  style_version_id: string; generation_mode: string | null; workflow_profile_id: string | null;
  compiled_prompt_sha256: string | null; artifact_id: string | null; last_qc_result: string | null;
  last_repair_plan: Record<string, unknown> | null;
  request_revision?: number; quality_verification?: string | null;
  acceptance_status?: 'accepted' | 'pending_review' | 'rejected' | 'mock' | 'not_run';
  acceptance_note?: string | null; acceptance_final_gate?: string | null;
  request: {
    reference_manifest: Array<{ label: string; asset_id: string; sha256: string; use_as: string }>;
    compiled_prompt_sha256?: string; compiler_version?: string; rule_version?: string;
    self_review_scope?: 'basic' | 'full'; basic_review_resolution?: { width: number; height: number } | null;
  };
  attempts: JobAttempt[]; repairs: RepairRecord[];
  compiled_prompt?: string | null; compiled_prompt_sections?: Record<string, string>;
  created_at: string; updated_at: string;
};

export type WorkerStatus = {
  worker_id: string; online: boolean; version: string; pid: number | null; status: string;
  current_job_id: string | null; current_phase: string | null;
  last_heartbeat_at: string; last_error: string | null; processed_count: number;
};

/** 后端权威的「演示 / 真实制作」状态。renderer_online 只是当前探活结果：
 * 受管渲染器空闲停机是正常状态，不代表制作模式失效。 */
export type ProductionModeStatus = {
  mode: 'DEMO' | 'SETUP_REQUIRED' | 'VALIDATION_REQUIRED' | 'READY' | 'RUNNING'
  | 'BLOCKED' | 'RECONCILE_REQUIRED' | string;
  label: string;
  detail: string;
  action: 'enable_real' | 'start_making' | 'view_progress' | 'check_fix' | null;
  code: string | null;
  render_provider: string;
  runtime_status: string;
  renderer_online: boolean;
  worker_online: boolean;
  running_jobs: number;
  series_active: number;
};

export type SystemStatus = {
  api_online: boolean; worker_online: boolean; mock_provider_available: boolean;
  h3_connected: boolean; render_mode: string; pending_jobs: number; running_jobs: number;
  failed_jobs: number; blocked_jobs: number; workers: WorkerStatus[]; last_error: string | null;
  production_mode: ProductionModeStatus;
};

export type QCReport = {
  id: string; result: string; is_mock: boolean; reviewer: string;
  report: {
    hard_failures?: Array<{ code: string; evidence: string; evidence_time_s?: number }>;
    soft_scores?: Record<string, number>; review_confidence?: number;
    technical?: { result: string; checks: Array<{ name: string; status: string; detail: string }> };
    semantic?: Array<{ reviewer: string; is_mock: boolean; findings: Array<{ code: string; severity: string; confidence: number; message: string }> }>;
    coverage?: Record<string, string>;
  };
  created_at: string;
};

export type ProviderProfile = {
  id: string; name: string; kind: string; base_url: string; model: string;
  has_api_key: boolean; capabilities: Record<string, unknown>; timeout_s: number;
  max_retries: number; temperature: number; enabled: boolean; created_at: string; updated_at: string;
  generation_options?: { max_completion_tokens?: number | null; reasoning_effort?: string | null };
};

export type AgentRoute = { role: string; provider_profile_id: string; profile_name: string };

export type WorkflowProfile = {
  id: string; project_id: string | null; name: string; mode: string; graph_sha256: string;
  parameter_map: Record<string, string>; required_nodes: string[]; checkpoint_id: string;
  validated_against: Record<string, unknown> | null; created_at: string;
  model_identity?: Record<string, unknown>;
};

export type AuditItem = {
  id: string; project_id: string | null; actor: string; event_type: string;
  entity_type: string; entity_id: string; payload: Record<string, unknown>; created_at: string;
};

export type AuditPage = { items: AuditItem[]; total: number; limit: number; offset: number };

export type RepairSuggestion = {
  job_id: string; plan: Record<string, unknown>; auto_apply: boolean; error_category: string;
};

export type SystemHealth = { ok: boolean; message: string; h3_nodes?: string[]; node_count?: number };

export type ProductionSeriesItem = {
  id: string; shot_id: string; position: number; status: string; job_id: string | null;
  continuity_mode: string; failure_code: string | null; job_status: string | null;
  job_artifact_id: string | null; job_failure_code: string | null;
};

export type ProductionSeries = {
  id: string; project_id: string; status: string; provider: string;
  workflow_profile_id: string | null; generation_mode: string | null;
  self_review_scope: string; shot_count: number; completed_count: number;
  worker_id: string | null; lease_id: string | null; current_job_id: string | null;
  state_version: number; failure_code: string | null;
  finalization_state: string; finalization_note: string | null;
  edit_intent_artifact_id: string | null;
  delivery_result: {
    order_id: string; version: number; artifact_id: string; sha256: string;
    byte_size: number; duration_s: number; width: number; height: number;
    fps: number; review_state: string; edl_source: string;
  } | null;
  created_at: string | null; started_at: string | null; finished_at: string | null;
  items: ProductionSeriesItem[];
};

export type TerminologyTerm = { canonical: string; aliases: string[] };

export type ProjectTerminology = {
  id: string; project_id: string; revision: number; terms: TerminologyTerm[];
  notes: string; sha256: string; source: string; created_by: string;
  created_at: string; updated_at: string;
};

export type SubtitleCue = { index: number; start_s: number; end_s: number; text: string };

export type SubtitleTrack = {
  id: string; project_id: string; revision: number; status: string; cues: SubtitleCue[];
  sha256: string; source: string; source_artifact_sha256: string | null;
  created_by: string; created_at: string; updated_at: string;
};
