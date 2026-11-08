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

export const ERROR_CODE_LABELS: Record<string, string> = {
  INPAINT_TOTAL_INPUT_LIMIT: '局部修订还需要一个遮罩输入槽，请移除一张参考图后重试。',
  EXISTING_H3_MODEL_FILES_INCOMPLETE: 'ComfyUI 已确认安装，但部分 H3 文件没有通过固定清单核验。本次没有复制或下载；请到专业设置查看文件差异，确认兼容性后再单独处理缺项。',
  EXISTING_COMFY_HANDOFF_ADAPTER_MISMATCH: '本机 ComfyUI 的连接组件与当前工作台不一致。原安装没有修改；请到专业设置查看兼容处理办法。',
  EXISTING_MODEL_HASH_REJECTIONS_PRESENT: '本机有模型文件未通过完整性校验。为保护原文件，本次没有复用或下载替代文件；请到专业设置查看失败项。',
  RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK: '本机还有未核对或未结束的任务，暂时不能重新验证或启用运行配置。请先处理「有任务待核对」的镜头任务，或取消未完成的生产系列，再重试。',
  GENERATE_RUNTIME_CONFIGURATION_FIRST: '还没有为本机生成运行配置。请先在「制作环境」完成一次准备，再验证工作流。',
  STOP_OWNED_INSTANCE_BEFORE_VALIDATION: '本机渲染器仍在运行。请先停止受管实例，再验证工作流节点。',
  STORY_DURABLE_OUTLINE_REQUIRED: '这份剧本太长，一次性分析会丢掉细节。请改用「总体大纲与生产段落」：后台提取并归并全剧大纲，你确认后只为你勾选的段落准备正式分镜。',
  ACTIVE_RUNTIME_CONFIGURATION_CHANGED: '本机运行配置已经变化。请重新加载工作台后再验证。',
  FAILED_INSTALL_ATTEMPT_REQUIRED: '这条安装记录已经结束或不在可整理状态。请刷新安装列表后查看最新状态。',
  INSTALL_ATTEMPT_ACTIVE: '本机还有另一项安装正在执行。请等它结束或失败后再整理这一项。',
  INSTALL_OWNER_ACTIVE: '这次安装的进程仍在运行。请等它自行结束，或先停止服务再整理；系统不会强制覆盖正在写入的文件。',
  INSTALL_FILESYSTEM_RECOVERY_REQUIRED: '安装目录里留下了无法确认的临时文件，系统没有自动清理。请到专业设置查看安装详情后再处理。',
  INSTALL_JOURNAL_MISSING: '安装目录里有临时文件但缺少安装日志，系统无法确认它们属于哪一步，因此没有改动。',
  INSTALL_JOURNAL_UNVERIFIABLE: '安装日志无法解析，系统无法安全回滚，因此没有改动任何文件。',
  INSTALL_BACKUP_MISSING: '安装换入前缺少可恢复的原目录备份，系统没有改动文件。请到专业设置查看详情。',
  INSTALL_DESTINATION_CHANGED: '已安装目录的内容与固定清单不一致，系统没有改动它。请到专业设置查看差异。',
  APPROVAL_REQUIRED_OR_REPLAYED: '这次确认已过期或已经用过。请重新加载安装列表后再确认一次。',
  STOP_WORKER_BEFORE_SERVICE_VALIDATION: '本机后台制作服务正在运行，重新验证需要先停止它（一键启动器窗口里可以停止后台服务）。首次安装验证不受影响。',
  LLM_NOT_CONFIGURED: '还没有给这个角色配置模型服务。请先到基础配置里配置文本模型。',
  REMOTE_AGENT_CONSENT_REQUIRED: '当前模型在远端，需要你明确同意才会把剧本文字发出去。',
  LLM_CONTEXT_BUDGET_EXCEEDED: '这一段内容超过了模型的上下文预算。请拆短剧本或减少这一场的描述后重试。',
  MATERIAL_SCENE_PREPARATION_INVALID: '这一场的拆解结果不符合约定格式，请让模型按约定重答这一场。',
  MATERIAL_SOURCE_SPAN_UNKNOWN: '这一场引用了剧本里不存在的片段编号，请让模型按给出的片段重新引用。',
  MATERIAL_SCENE_KEY_UNKNOWN: '这一场的编号不是服务端切分出来的场号，请让模型按给出的场号重答。',
  MATERIAL_IDENTITY_AMBIGUOUS: '同一名字在不同场里描述不一致，需要你先消歧才能确认清单。',
  MATERIAL_PLAN_STALE: '清单在你阅读之后又变了，请刷新后重新确认。',
  MATERIAL_DOCUMENT_STALE: '剧本文件已经换过了，请刷新后重新分析。',
  MATERIAL_CONFIRMATION_REQUIRED: '这一项还没有确认素材来源，先在清单里确认后再排队。',
  MATERIAL_BRIEF_MISSING: '还没有为这一项写外观描述，无法出图。请重新生成这一项。',
  MATERIAL_BRIEF_NEEDS_CLARIFICATION: '外观描述里还有需要确认的问题，先回答问题再出图。',
  MATERIAL_BATCH_CONFLICT: '这个批次键已经被别的请求占用，请刷新后重试。',
  LICENSE_NOT_APPROVED: '还没有已批准的生成授权（研究用途），请先登记并批准一次生成授权。',
  IMAGE_RUNTIME_NOT_READY: '本机的出图环境还没就绪，无法真正出图。请先到基础配置完成出图环境准备。',
  IMAGE_REAL_PROVIDER_REQUIRED: '出图入口只接受真实渲染，不接受模拟。',
  IMAGE_SUBMISSION_OUTCOME_UNKNOWN: '这次提交的结果未知，需要先对账，不能直接重试。',
  IMAGE_OUTCOME_RECONCILIATION_REQUIRED: '这次提交的结果未知，需要先对账，不能直接重试。',
  IMAGE_TECHNICAL_QC_FAILED: '这张图没有通过技术质检（尺寸、格式或完整性），请重新生成一版。',
  ASSET_OR_VOICE_MISSING: '这一项还没有绑定有效的主素材。',
  ASSET_IDENTITY_MISMATCH: '绑定的主素材与这一项的人物/场景/道具对不上。',
  ASSET_HASH_CHANGED: '主素材文件在登记之后被改过，需要重新确认。',
  ASSET_FILE_MISSING: '主素材文件找不到了，需要重新上传。',
  MOCK_ASSET_NOT_PRODUCTION: '这一项绑定的是模拟素材，真实制作不能使用它。',
  MATERIAL_PLAN_NOT_CONFIRMED: '清单还没有确认，先确认清单再判定素材是否齐备。',
  MATERIAL_REQUIREMENT_UNBOUND: '还有必需项没有绑定有效主素材。',
  IMAGE_RENDERER_RECIPE_MISSING: '还没有为本机生成受管运行配置，出图渲染器无法启动。请先到基础配置完成本机环境登记。',
  IMAGE_RENDERER_RECIPE_MISMATCH: '出图运行时绑定的实例与本机受管运行配置不是同一个实例，请重新绑定。',
  IMAGE_RENDERER_INSTANCE_NOT_MANAGED: '出图运行时只能绑定本工作台受管的实例，请先完成受管实例绑定。',
  IMAGE_RENDERER_MEMORY_INSUFFICIENT: '本机显存或内存不够运行出图渲染器，请先释放其它占显存的进程。',
  IMAGE_RENDERER_OWNERSHIP_UNVERIFIED: '记录里的渲染器进程身份无法证明属于本工作台，因此没有停止它。',
  IMAGE_RENDERER_UNAVAILABLE: '本机出图渲染器没有起来，无法出图。请先在基础配置里启动它。',
  CREATIVE_BRIEF_INVALID: '创意简报还缺内容或有字段超出范围，请按提示修改后再保存。',
  CREATIVE_BRIEF_NOT_CONFIRMED: '先确认这一版创意简报，才能据此生成剧本。',
  CREATIVE_BRIEF_CHANGED: '创意简报在你确认前又改过，请看最新一版后再确认。',
  CREATIVE_BRIEF_STALE: '这一版创意简报已失效，请保存新的一版。',
  CREATIVE_SCRIPT_ROLE_NOT_CONFIGURED: '还没有给「编剧」角色配置文本模型。请先到基础配置里配置文本模型。',
  SCRIPT_OUTLINE_NOT_CONFIRMED: '先确认剧本大纲，再按剧本分集。',
  SCRIPT_OUTLINE_HAS_NO_SECTIONS: '已确认的大纲里没有可分的场次，请重新整理大纲。',
  EPISODE_SECTION_NOT_STORYBOARDED: '还有场次没有拆成镜头。请先为这些场次生成并应用分镜，再把分集落到镜头上。',
  EPISODE_PLAN_NOT_MATERIALIZED: '分集方案里的场次还没有全部落到镜头上，先点「把分集落到镜头」。',
  EPISODE_PLAN_NOT_SCRIPT_FIRST: '这份分集方案是按镜头分组的，不需要再落到镜头。',
  EPISODE_PLAN_COVERAGE_INVALID: '分集方案没有恰好覆盖全部内容（有遗漏、重复或已变化），请重新生成分集。',
  DIRECTOR_PACKAGE_POLICY_INVALID: '执行政策里有无法执行的值（类型错误或超出范围），请修改后再生成导演包。',
  PACKAGE_MANIFEST_TAMPERED: '导演包的固定内容与哈希不一致，已拒绝执行。请重新生成并确认导演包。',
  WORKFLOW_PROFILE_CHANGED: '生成方案在导演包确认后被修改，已停止派发新批次。请重新生成并确认导演包。',
  EPISODE_POST_CONTRACT_INVALID: '剪辑/字幕合同不合法，请检查入出点和字幕版本。',
  EDIT_CUT_SHOT_NOT_IN_EPISODE: '剪辑合同里有不属于这一集的镜头。',
  EPISODE_SUBTITLE_NOT_APPROVED: '选择的字幕版本不存在或尚未批准。',
  EPISODE_ASSEMBLY_IN_PROGRESS: '这一集正在装配，等它结束后再修改剪辑合同。',
};

/** Turn a FastAPI/pydantic error payload into one readable line; the raw
 *  technical JSON stays available for support but is no longer the only
 *  thing the user sees. */
function describeDetail(detail: unknown): string {
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'violations' in detail) {
    const violations = (detail as { violations?: Array<{ rule_id?: string; evidence?: string; remediation?: string }> }).violations;
    if (Array.isArray(violations) && violations.length) {
      return violations.map(v => `${v.rule_id ?? '校验'}：${v.evidence ?? '未通过'}${v.remediation ? `；${v.remediation}` : ''}`).join('；');
    }
  }
  if (typeof detail === 'string') {
    if (detail === 'STYLE_APPROVAL_CONTENT_CHANGED') return '风格草稿已在其他页面修改，请检查后重新确认（STYLE_APPROVAL_CONTENT_CHANGED）。';
    if (detail === 'STYLE_APPROVAL_CONTENT_REQUIRED') return '本次批准缺少确认的风格内容，请刷新工作台后重试（STYLE_APPROVAL_CONTENT_REQUIRED）。';
    if (ERROR_CODE_LABELS[detail]) return `${ERROR_CODE_LABELS[detail]}（${detail}）`;
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
  if (detail && typeof detail === 'object' && 'code' in detail && detail.code) {
    const code = String(detail.code);
    return ERROR_CODE_LABELS[code]
      ? `${ERROR_CODE_LABELS[code]}（${code}）`
      : `本机操作没有完成，请到专业设置查看专业日志。错误代码：${code}。`;
  }
  if (detail && typeof detail === 'object') return '本机操作没有完成，请到专业设置查看专业日志。错误代码：SETUP_OPERATION_FAILED。';
  return JSON.stringify(detail);
}

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
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
    throw new ApiError(message, response.status);
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
  entity_id?: string | null;
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

export type AdvisoryReview = {
  id: string; kind: string | null; status: string; error_code: string | null;
  model_id: string | null; artifact_id: string | null; artifact_sha256: string | null;
  job_id: string | null; started_at: string | null; finished_at: string | null;
  outcome: string | null; is_mock: boolean | null;
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
  advisory_reviews?: AdvisoryReview[];
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
  /** not_set | stored | missing | unreadable - never the key itself. */
  secret_status?: string;
};

/** The evidence of one provider check.
 *
 *  The connection fields and the generation fields are separate facts: a
 *  reachable provider with a listed model is not a generation acceptance. */
export type ProviderTestResult = {
  ok: boolean; level: string;
  reachable: boolean | null; auth_ok: boolean | null; catalog_ok: boolean | null;
  model_listed: boolean | null; generation_ok: boolean | null; structured_output_ok: boolean | null;
  model: string | null; latency_ms: number | null;
  error_code: string | null; message: string;
  capabilities: Record<string, unknown>;
  checked_config_sha256: string | null; checked_at: string | null;
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

/** The frame and clip length of a real H3 run; H3 facts: 24 fps, multiples of 32, 4–15 s. */
export type OutputSpec = {
  width: number;
  height: number;
  fps?: 24;
  clip_seconds: number | null;
};

export type ProductionSeriesItem = {
  id: string; shot_id: string; position: number; status: string; job_id: string | null;
  continuity_mode: string; failure_code: string | null; job_status: string | null;
  job_artifact_id: string | null; job_failure_code: string | null;
};

export type ProductionSeries = {
  id: string; project_id: string; status: string; provider: string;
  workflow_profile_id: string | null; generation_mode: string | null;
  self_review_scope: string; output_spec: OutputSpec | null; shot_count: number; completed_count: number;
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

/* ======================= 导演流程 / 整部作品 ======================= */

export type JourneyStage = {
  id: 'script' | 'plan' | 'storyboard' | 'assets' | 'package' | 'production' | 'post';
  label: string;
  done: boolean;
  summary: string;
  blockers: Array<{ code: string; message: string; shot_id?: string; asset_id?: string }>;
  facts: Record<string, unknown>;
};

export type Journey = {
  project_id: string;
  stages: JourneyStage[];
  next_stage: string | null;
  package: { id: string; revision: number; status: string; stale: boolean } | null;
  episode_plan: { id: string; version: number; episodes: number } | null;
  run: {
    id: string; status: string; required_episode_count: number;
    delivered_episode_count: number;
    blockers: Array<{ code: string; message: string }>;
    control_reason: string | null; last_progress_at: string | null;
  } | null;
};

export type PlannedEpisode = {
  episode_id: string; order: number; title: string; shot_ids: string[];
  section_ids?: string[];
  planned_seconds: number; boundary_note: string;
};

export type EpisodePlan = {
  id: string; project_id: string; version: number; status: string;
  target_seconds: number; tolerance_seconds: number; sha256: string; source: string;
  payload: {
    episodes: PlannedEpisode[]; target_seconds: number; tolerance_seconds: number;
    mode?: 'shots' | 'sections';
    source?: { outline_artifact_id?: string; outline_sha256?: string };
  };
  created_at: string; confirmed_at: string | null;
};

export type DirectorPackage = {
  id: string; project_id: string; revision: number; status: string;
  stale: boolean; stale_reason: string | null; scope_digest: string; manifest_sha256: string;
  policy_hash: string; episode_count: number; shot_count: number;
  episode_plan: { plan_id: string; version: number; sha256: string; target_seconds: number } | null;
  script: Record<string, unknown> | null;
  style: { style_version_id: string; version: number; sha256: string } | null;
  binding: {
    workflow_profile_id: string; mode: string; graph_sha256: string; checkpoint_id: string;
    generation_mode: string | null; output: Record<string, unknown>;
  } | null;
  policy: Record<string, unknown> | null;
  episodes: Array<{
    episode_id: string; position: number; title: string; shot_count: number;
    planned_seconds: number; manifest_sha256: string;
  }>;
  created_at: string; confirmed_at: string | null;
};

export type PackageReadiness = {
  package_id: string; revision: number; status: string; stale: boolean;
  scope_digest: string; manifest_sha256: string; config_fingerprint: string;
  ready: boolean; startable: boolean;
  blockers: Array<{ code: string; message: string; shot_id?: string; asset_id?: string }>;
  episode_count: number; shot_count: number; checked_at: string;
};

export type RunEpisode = {
  id: string; episode_id: string; position: number; title: string; status: string;
  shot_count: number; passed_shot_count: number; planned_seconds: number;
  shot_ids?: string[];
  post_contract?: { cuts?: Array<{ shot_id: string; in_s: number; out_s: number }>;
    subtitle_revision?: number | null; subtitle_sha256?: string } | null;
  actual_seconds: number | null; delivery_order_id: string | null;
  delivered_artifact_id: string | null; delivered_sha256: string | null;
  blocker_code: string | null; delivered_at: string | null;
  batches: Array<{
    batch_key: string; status: string; series_id: string | null; shot_count: number;
    failure_code: string | null; dispatched_at: string | null;
  }>;
};

export type ProductionRun = {
  id: string; project_id: string; package_id: string; package_revision: number;
  manifest_sha256: string; scope_digest: string; status: string; state_version: number;
  control_reason: string | null; blocker_count: number;
  scope: { episode_ids: string[]; all_episodes: boolean; shot_count: number };
  counters: Record<string, number>;
  progress: {
    required_episode_count: number; delivered_episode_count: number;
    producing_episode_count: number; blocked_episode_count: number;
    batch_count: number; passed_batch_count: number; delivered_seconds: number;
    passed_shot_count: number;
  };
  created_at: string; started_at: string | null; finished_at: string | null;
  last_progress_at: string | null; heartbeat_at: string | null;
  episodes?: RunEpisode[];
  created?: boolean;
};

export type RunEvent = {
  id: number; kind: string; message: string; payload: Record<string, unknown>; created_at: string;
};

export const getJourney = (projectId: string) =>
  api<Journey>(`/projects/${projectId}/journey`);

export const listEpisodePlans = (projectId: string) =>
  api<EpisodePlan[]>(`/projects/${projectId}/episode-plans`);

export const createEpisodePlan = (
  projectId: string, targetSeconds: number, toleranceSeconds: number, mode?: 'shots' | 'sections',
) =>
  api<EpisodePlan>(`/projects/${projectId}/episode-plans`,
    json('POST', { target_seconds: targetSeconds, tolerance_seconds: toleranceSeconds, ...(mode ? { mode } : {}) }));

export const materializeEpisodePlan = (projectId: string, version: number) =>
  api<EpisodePlan>(`/projects/${projectId}/episode-plans/${version}/materialize`, json('POST', {}));

export const confirmEpisodePlan = (projectId: string, version: number, sha256: string) =>
  api<EpisodePlan>(`/projects/${projectId}/episode-plans/${version}/confirm`,
    json('POST', { expected_sha256: sha256 }));

export const listDirectorPackages = (projectId: string) =>
  api<DirectorPackage[]>(`/projects/${projectId}/director-packages`);

export const createDirectorPackage = (
  projectId: string,
  body: { workflow_profile_id: string; generation_mode?: string | null; output?: Record<string, unknown> | null;
    policy?: Record<string, unknown> | null },
) => api<DirectorPackage>(`/projects/${projectId}/director-packages`, json('POST', body));

export const getPackageReadiness = (packageId: string) =>
  api<PackageReadiness>(`/director-packages/${packageId}/readiness`);

export const confirmDirectorPackage = (packageId: string, expectedManifest: string) =>
  api<DirectorPackage>(`/director-packages/${packageId}/confirm`,
    json('POST', { expected_manifest_sha256: expectedManifest }));

export const listProductionRuns = (projectId: string) =>
  api<ProductionRun[]>(`/projects/${projectId}/production-runs`);

export const createProductionRun = (projectId: string, body: { package_id: string; episode_ids?: string[] | null }) =>
  api<ProductionRun>(`/projects/${projectId}/production-runs`, json('POST', body));

export const getProductionRun = (runId: string) =>
  api<ProductionRun>(`/production-runs/${runId}`);

export const getRunEvents = (runId: string, afterId = 0) =>
  api<RunEvent[]>(`/production-runs/${runId}/events?after_id=${afterId}&limit=40`);

export const controlProductionRun = (runId: string, action: 'pause' | 'resume' | 'cancel' | 'reconcile') =>
  api<{ id: string; status: string; state_version?: number | null }>(
    `/production-runs/${runId}/${action}`, json('POST', {}));

export type EpisodePostContract = {
  cuts?: Array<{ shot_id: string; in_s: number; out_s: number }>;
  subtitle_revision?: number | null;
};

export const setEpisodePostContract = (runId: string, episodeId: string, body: EpisodePostContract) =>
  api<{ episode_id: string; post_contract: Record<string, unknown> | null; delivery_order_id: string | null;
    status: string }>(`/production-runs/${runId}/episodes/${episodeId}/post-contract`, json('PUT', body));

/* ======================= 入口：想法 / 导入 ======================= */

export type CreativeBriefPayload = {
  idea: string;
  genre?: string; audience?: string; theme?: string; core_conflict?: string; ending_direction?: string;
  language?: string; aspect_ratio?: string; episode_count_hint?: number | null;
  target_seconds_per_episode?: number; tolerance_seconds?: number;
  locked_facts?: string[]; forbidden_changes?: string[]; visual_direction?: string;
};

export type CreativeBrief = {
  id: string; version: number; payload: CreativeBriefPayload & { version: number };
  sha256: string; confirmed: boolean; stale: boolean; created_at: string; confirmed_at: string | null;
  script_task: {
    id: string; status: string; stage?: string | null; error_code: string | null;
    document_id: string | null;
    document_sha256: string | null; external_outcome: string | null;
    reconciliation?: string | null; next_step?: string | null; created_at: string;
  } | null;
  task_id?: string;
};

export type ScriptDocument = {
  id: string; project_id: string; filename: string; content_type: string; sha256: string;
  source_format: string; created_at: string; text_preview?: string; text_length?: number;
};

export const listCreativeBriefs = (projectId: string) =>
  api<CreativeBrief[]>(`/projects/${projectId}/creative-briefs`);

export const saveCreativeBrief = (projectId: string, body: CreativeBriefPayload) =>
  api<CreativeBrief>(`/projects/${projectId}/creative-briefs`, json('POST', body));

export const confirmCreativeBrief = (projectId: string, briefId: string, sha256: string) =>
  api<CreativeBrief>(`/projects/${projectId}/creative-briefs/${briefId}/confirm`,
    json('POST', { expected_sha256: sha256 }));

export const generateScriptFromBrief = (projectId: string, briefId: string, remoteTextConsent: boolean) =>
  api<CreativeBrief>(`/projects/${projectId}/creative-briefs/${briefId}/generate-script`,
    json('POST', { remote_text_consent: remoteTextConsent }));

export const listScriptDocuments = (projectId: string) =>
  api<ScriptDocument[]>(`/projects/${projectId}/documents`);

export function uploadScriptDocument(projectId: string, file: File) {
  const form = new FormData();
  form.append('file', file);
  return api<ScriptDocument>(`/projects/${projectId}/documents`, { method: 'POST', body: form });
}

/* ======================= 新机部署组合推荐 ======================= */

export type DeploymentOption = {
  purpose: 'h3_video' | 'image_prep' | string;
  label: string;
  skippable: boolean;
  catalog_id?: string;
  catalog_version?: string;
  status: 'SUPPORTED' | 'BLOCKED' | 'UNAVAILABLE' | string;
  requirements?: { os?: string; gpu_vendor?: string; min_vram_mb?: number; min_ram_mb?: number;
    min_disk_gb?: number; total_bytes?: number };
  total_bytes?: number; reuse_bytes?: number; download_bytes?: number;
  blockers: Array<{ code: string; required?: number; actual?: number; detail?: string }>;
  warnings?: Array<{ code: string; detail?: string }>;
  evidence?: { level: string; verified: boolean; detail?: string; measured_at?: string;
    peak_vram_mb?: number; peak_ram_mb?: number; duration_s?: number };
  verified?: boolean;
};

export type DeploymentRecommendation = {
  recommended: DeploymentOption[];
  blocked: DeploymentOption[];
  options: DeploymentOption[];
  host: { gpu?: string | null; vram_mb?: number | null; ram_mb?: number | null; free_gb?: number | null };
  notes: string[];
};

export const getDeploymentRecommendation = (reuseInstalled = true) =>
  api<DeploymentRecommendation>('/setup/deployment-recommendations',
    json('POST', { reuse_installed: reuseInstalled }));

// ============ 四阶段流水线（AI 编导） ============
export interface PipelineVolumeScriptEntry {
  section_id: string;
  scene_key: string;
  title: string | null;
  confirmed: boolean;
}

export interface PipelineVolumeStoryboard {
  status: string | null;
  updated_at: string | null;
  units: StoryboardUnit[];
}

export interface PipelineEpisodeContract {
  artifact_id: string;
  sha256: string;
  title: string | null;
  sections: string[];
  confirmed: boolean;
  /** False once the outline or a scene script the contract pinned moved on. */
  current: boolean;
  reason: string | null;
}

export interface PipelineHandoffReceipt {
  handoff_id: string;
  package_id: string;
  package_revision: number;
  plan_version: number;
  episodes: number;
  shots: number;
  episode_basis: string | null;
  lineage_sha256: string | null;
  created_at: string | null;
  readiness: {
    startable: boolean;
    ready?: boolean;
    blockers: Array<{ code: string; message?: string }>;
  };
}

export interface PipelineVolume {
  volume_id: string;
  order: number;
  title: string;
  char_count: number | null;
  outline_confirmed: boolean;
  latest_outline: {
    artifact_id: string; sha256: string; questions: string[]; questions_sha256: string | null;
  } | null;
  scripts: PipelineVolumeScriptEntry[];
  scripts_confirmed: number;
  scripts_total: number;
  /** The authoritative per-unit state: a refresh restores it. */
  storyboard: PipelineVolumeStoryboard;
  episode_contracts: PipelineEpisodeContract[];
  handoff: PipelineHandoffReceipt | null;
}

export interface PipelineOverview {
  master: { document_id: string | null; sha256: string | null };
  volume_plan_confirmed: boolean;
  /** The confirmed plan revision the boundary editor changes. */
  volume_plan: {
    artifact_id: string; sha256: string;
    volumes: Array<{ title: string; start_offset: number; end_offset: number }>;
  } | null;
  bible_confirmed: boolean;
  pending_bible: { artifact_id: string; sha256: string } | null;
  /** Why the confirmed blueprint no longer matches the current work. */
  bible_status: { artifact_id: string; sha256: string; blockers: string[] } | null;
  active_tasks: PipelineTaskSummary[];
  pending_scripts: Array<{
    volume_id: string; section_id: string; artifact_id: string; sha256: string;
    scene_key: string; title: string | null; text: string;
  }>;
  volumes: PipelineVolume[];
}

export const getPipelineOverview = (projectId: string) =>
  api<PipelineOverview>(`/projects/${projectId}/pipeline`);

export const importPipelineSource = (projectId: string, documentId: string, expectedHash: string) =>
  api<{ volume_plan_id: string; sha256: string; volumes: unknown[]; coverage: { complete: boolean } }>(
    `/projects/${projectId}/pipeline/sources`,
    json('POST', { document_id: documentId, expected_document_hash: expectedHash }));

export const confirmVolumePlan = (projectId: string, artifactId: string, sha256: string) =>
  api<{ confirmed: boolean; sha256: string; volume_documents: string[] }>(
    `/projects/${projectId}/pipeline/volume-plans/${artifactId}/confirm`,
    json('POST', { expected_sha256: sha256 }));

export const queueVolumeOutline = (projectId: string, volumeId: string) =>
  api<{ task_id: string }>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}/outline`, json('POST', {}));

export const queueBible = (projectId: string) =>
  api<{ task_id: string }>(`/projects/${projectId}/pipeline/bible`, json('POST', {}));

export const confirmBible = (projectId: string, artifactId: string, sha256: string) =>
  api<{ confirmed: boolean; sha256: string }>(
    `/projects/${projectId}/pipeline/bibles/${artifactId}/confirm`,
    json('POST', { expected_sha256: sha256 }));

export const queueVolumeScripts = (projectId: string, volumeId: string) =>
  api<{ queued: number; task_ids: string[] }>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}/scripts`, json('POST', {}));

export const confirmSceneScript = (projectId: string, artifactId: string, sha256: string) =>
  api<{ confirmed: boolean; sha256: string }>(
    `/projects/${projectId}/pipeline/scene-scripts/${artifactId}/confirm`,
    json('POST', { expected_sha256: sha256 }));

export const editSceneScript = (projectId: string, artifactId: string, text: string, note = '') =>
  api<{ artifact_id: string; sha256: string; confirmed: boolean }>(
    `/projects/${projectId}/pipeline/scene-scripts/${artifactId}/edit`,
    json('POST', { text, note }));

export interface StoryboardUnitRejection {
  code?: string;
  rejection_reason_code?: string;
  role?: string;
  external_outcome?: string;
  task_id?: string;
  state_version?: number;
}

export interface StoryboardUnit {
  unit_id: string;
  section_id: string;
  order: number | null;
  status: string | null;
  camera_artifact_id: string | null;
  continuity_artifact_id: string | null;
  arbitration_artifact_id: string | null;
  applied: boolean;
  /** The shots this scene really produced, once applied. */
  shot_ids?: string[];
  /** The chunk ordinal of a scene that had to be split. */
  chunk_index?: number;
  chunk_count?: number;
  title?: string | null;
  scene_key?: string | null;
  script_sha256?: string | null;
  updated_at?: string | null;
  /** What actually went wrong in this unit, with its reconciliation handle. */
  rejections?: StoryboardUnitRejection[];
  /** The characters this design names that have no approved master yet. */
  missing_characters?: string[];
}

export interface StoryboardDraft {
  status: string;
  /** One bounded crew input per scene, in scene order. */
  units: StoryboardUnit[];
  steps: Record<string, unknown>;
  next?: unknown;
  rejections?: Array<{ role?: string; code?: string; task_id?: string;
                       state_version?: number; external_outcome?: string;
                       rejection_reason_code?: string }>;
}

export const generateStoryboardDraft = (projectId: string, volumeId: string) =>
  api<StoryboardDraft>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}/storyboard-draft`, json('POST', {}));

/** Apply one scene's camera plan: this is what proves the scene->shots mapping. */
export const applyCameraPlan = (
    projectId: string, cameraArtifactId: string, continuityArtifactId: string | null) =>
  api<{ shot_ids: string[];
         material_notices?: Array<{ code: string; message: string;
                                  asset_id?: string; shot_id?: string }> }>(
    `/projects/${projectId}/film-artifacts/${cameraArtifactId}/apply-camera`,
    json('POST', { continuity_artifact_id: continuityArtifactId }));

/**
 * Record that a refused crew call was provably never sent.  Only this lets the
 * same request be attempted again; the ledger, not the button, decides.
 */
export const reconcileFilmTask = (projectId: string, taskId: string, expectedStateVersion: number) =>
  api<{ outcome: string; resubmittable: boolean; state_version: number }>(
    `/projects/${projectId}/film-runs/${taskId}/reconcile`,
    json('POST', { expected_state_version: expectedStateVersion }));

export const confirmVolumeOutline = (
    projectId: string, volumeId: string, outlineArtifactId: string, sha256: string,
    questionsSha256: string | null) =>
  api<{ approved: boolean; invalidated_section_ids: string[] }>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}/outline-confirm`,
    json('POST', { outline_artifact_id: outlineArtifactId, expected_sha256: sha256,
                   acknowledge_questions_sha256: questionsSha256 }));

export interface WorkflowProfileLite { id: string; name: string; mode: string }

export const requestVolumeHandoff = (
    projectId: string, volumeId: string, workflowProfileId: string,
    options: { acknowledgeSceneCoverage?: boolean } = {}) =>
  api<{ handoff_id: string; plan_version: number; package_revision: number;
        package_id: string; reused: boolean; episode_basis?: string;
        readiness: { ready: boolean; startable: boolean;
        blockers: Array<{ code: string; message?: string }> };
        episodes: number; shots: number }>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}/handoff`,
    json('POST', { workflow_profile_id: workflowProfileId,
                   acknowledge_scene_coverage: options.acknowledgeSceneCoverage ?? false }));

/** The authoritative state of one volume: draft, units, failures, receipt. */
export const getVolumePipelineState = (projectId: string, volumeId: string) =>
  api<PipelineVolume & { storyboard: PipelineVolumeStoryboard;
                         episode_contracts: PipelineEpisodeContract[];
                         handoff: PipelineHandoffReceipt | null }>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}`);

/** The delivery receipt of one volume, with the exact package id. */
export const getVolumeHandoffReceipt = (projectId: string, volumeId: string) =>
  api<PipelineHandoffReceipt>(`/projects/${projectId}/pipeline/volumes/${volumeId}/handoff`);

/** Redo only the scenes the person named; the other scenes keep their work. */
export const generateStoryboardDraftFor = (
    projectId: string, volumeId: string, sectionIds: string[]) =>
  api<StoryboardDraft>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}/storyboard-draft`,
    json('POST', { section_ids: sectionIds }));

/**
 * Reconcile one four-stage task.  The server decides whether it may continue:
 * only a proven "not sent" may be resumed, an unknown outcome stays unknown.
 */
export const reconcilePipelineTask = (
    projectId: string, taskId: string, expectedStateVersion: number) =>
  api<{ outcome: string; resubmittable: boolean; state_version: number; note?: string }>(
    `/projects/${projectId}/pipeline/tasks/${taskId}/reconcile`,
    json('POST', { expected_state_version: expectedStateVersion }));

/** Draft one episode contract from the confirmed scenes of a volume. */
export const buildEpisodeScript = (
    projectId: string, volumeId: string, title: string, sectionIds: string[]) =>
  api<{ artifact_id: string; sha256: string; continuity_issues: unknown[] }>(
    `/projects/${projectId}/pipeline/volumes/${volumeId}/episode-scripts`,
    json('POST', { title, section_ids: sectionIds }));

export const confirmEpisodeScript = (projectId: string, artifactId: string, sha256: string) =>
  api<{ confirmed: boolean; sha256: string; continuity_status?: string }>(
    `/projects/${projectId}/pipeline/episode-scripts/${artifactId}/confirm`,
    json('POST', { expected_sha256: sha256 }));

/** Adjust the volume boundaries: a new plan revision the person confirms. */
export const adjustVolumeBoundaries = (
    projectId: string, planArtifactId: string,
    volumes: Array<{ title: string; start_offset: number; end_offset: number }>) =>
  api<{ volume_plan_id: string; sha256: string; volumes: unknown[] }>(
    `/projects/${projectId}/pipeline/volume-plans/${planArtifactId}/boundaries`,
    json('POST', { volumes }));

/** Open the director package page on one exact package. */
export const openDirectorPackage = (packageId: string) => {
  if (!packageId) return;
  window.location.hash = `#d/package?package=${encodeURIComponent(packageId)}`;
};

export interface PipelineTaskSummary {
  task_id: string;
  role: string;
  state_version: number;
  status: string;
  stage: string;
  stage_label: string;
  progress_done: number;
  progress_total: number;
  error_code: string | null;
  attempt: number;
  external_outcome: string | null;
  blocked_reason: string | null;
  /** The operations the server allows: never inferred from an error string. */
  available_actions: Array<'resume' | 'reconcile' | 'new_attempt'>;
  /** The server's own reconciliation conclusion, when one was reached. */
  reconciliation?: { outcome?: string; resubmittable?: boolean; checked_at?: string } | null;
  superseded_by_task_id?: string | null;
  updated_at: string | null;
  consent_document_id: string | null;
  consent_document_sha256: string | null;
  consent_role: string;
}

export const resumePipelineTask = (projectId: string, taskId: string, expectedStateVersion: number) =>
  api<{ task_id: string; status: string }>(
    `/projects/${projectId}/pipeline/tasks/${taskId}/resume`,
    json('POST', { expected_state_version: expectedStateVersion }));

/**
 * Authorise a physically separate retry after an unknown send outcome.  The
 * first attempt keeps its UNKNOWN result as history; this accepts a second
 * provider call.
 */
export const startPipelineNewAttempt = (
    projectId: string, taskId: string, expectedStateVersion: number) =>
  api<{ task_id: string; attempt: number; status: string; supersedes: string }>(
    `/projects/${projectId}/pipeline/tasks/${taskId}/new-attempt`,
    json('POST', { expected_state_version: expectedStateVersion }));

export interface TextConsentScope {
  document_id: string;
  document_sha256: string;
  scope_hash: string;
  transmission: string;
  roles: Array<{ role: string; configured: boolean; provider?: string;
                 base_url?: string; model?: string; allowed: boolean }>;
}

export const getTextConsentScope = (projectId: string, documentId: string) =>
  api<TextConsentScope>(`/projects/${projectId}/material-preparation/text-consent/${documentId}`);

export const grantTextConsent = (
    projectId: string, documentId: string, documentSha256: string, scopeHash: string, granted: boolean) =>
  api<{ granted: boolean }>(
    `/projects/${projectId}/material-preparation/text-consent`,
    json('POST', { document_id: documentId, expected_document_hash: documentSha256,
                   expected_scope_hash: scopeHash, granted }));
