import type { Style, StyleDimension } from './api';

/** The languages the H3 model is asked to speak. The value is the language name
 *  that goes into the prompt, so it stays in English. */
export const DIALOGUE_LANGUAGES: Array<{ id: string; label: string }> = [
  { id: 'Chinese', label: '中文（普通话）' },
  { id: 'English', label: 'English' },
  { id: 'Japanese', label: '日本語' },
  { id: 'Korean', label: '한국어' },
  { id: 'Cantonese', label: '粤语' },
  { id: 'French', label: 'Français' },
  { id: 'German', label: 'Deutsch' },
  { id: 'Spanish', label: 'Español' },
];

export const emptyStyle: Style = {
  photography_language: '', shot_blocking: '', color_palette: '', lighting_tone: '',
  lens_texture: '', editing_rhythm: '', sound_performance: '', film_type: null,
};

export const styleFields: Array<[StyleDimension, string, string]> = [
  ['photography_language', '摄影语言', '景别、机位高度、镜头运动与禁用项'],
  ['shot_blocking', '镜头调度', '人物走位、正反打与空间关系'],
  ['color_palette', '色彩风格', '主辅色、冷暖关系与肤色保护'],
  ['lighting_tone', '影调与灯光', '反差、黑位、主光方向与阴影细节'],
  ['lens_texture', '镜头质感', '焦段、景深、颗粒与锐化边界'],
  ['editing_rhythm', '剪辑与节奏', '镜头时长、对白停顿与切换规则'],
  ['sound_performance', '声音表演', '语言、语速、音色及环境声规则'],
];

/** 取映射表里的中文；没有收录的值原样返回，保证信息不丢失。 */
export function label(map: Record<string, string>, value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  return map[value] ?? value;
}

export const statusLabels: Record<string, string> = {
  PLANNED: '等待前置镜头', WAITING_ASSET: '等待素材', READY: '准备中', QUEUED: '排队中',
  SUBMITTING: '提交中', RUNNING: '生成中', QC_PENDING: '质检中', PASSED: '已通过',
  REPAIR_PENDING: '等待修复', NEEDS_REVIEW: '需人工复核', FAILED: '失败',
  COMPLETED: '已完成', SUBMITTED: '已提交', REJECTED: '已驳回', PENDING: '待处理',
  APPROVED: '已批准', CANCELLED: '已取消', EXPIRED: '已过期',
};

export const acceptanceLabels: Record<string, string> = {
  accepted: '已确认', pending_review: '待人工确认（不阻断制作）',
  rejected: '未通过', mock: '仅演示验证', not_run: '未执行',
};

export const advisoryReviewLabels: Record<string, string> = {
  QUEUED: '排队等待审片', RUNNING: '审片中', COMPLETED: '审片建议已记录',
  UNAVAILABLE: '审片不可用（未阻断制作）', SUPERSEDED: '视频或镜头已更新，旧建议作废',
  NEEDS_REVIEW: '审片结果不明，需人工对账',
};

export const advisoryReviewKindLabels: Record<string, string> = {
  VLM: '画面与风格建议', ASR: '对白转写建议',
};

export const failureLabels: Record<string, string> = {
  STORYBOARD_DIALOGUE_RATIO_CHANGED: '冻结的对白比例已修改，请从编剧重新生成分镜。',
  ADAPTED_DIALOGUE_SPEAKER_NOT_IN_SOURCE_BEAT: '改编台词需由原文已有且在本节拍出场的人物说出。',
  ADAPTED_DIALOGUE_OUTSIDE_BEAT_WINDOW: '改编台词超出节拍时间窗或与其他台词重叠。',
  PENDING_WORKFLOW_MAPPING: '参考素材还没接入工作流（缺参数映射）',
  PENDING_HARD_CONTINUITY_TEST: '硬性连续性测试还没做',
  QC_FAILED: '自动质检未通过',
  QC_NEEDS_REVIEW: '质检拿不准，需要人看一眼',
  QC_RECHECK_REQUESTED: '已申请重跑审片',
  QC_RECHECK_INPUT_CHANGED: '重审前画面已变化，需重新提交',
  HUMAN_REVIEW_REQUIRED: '需要人工审片',
  MANUAL_REVIEW_INCOMPLETE: '人工审片没做完',
  MANUAL_REVIEW_FAILED: '人工审片未通过',
  WORKER_EXCEPTION: '后台执行器出错',
  SUBMISSION_OUTCOME_UNKNOWN: '提交后结果不明，先核实再动',
  COMFY_SUBMISSION_OUTCOME_UNKNOWN: '提交后结果不明，先核实再动',
  COMFY_WORKER_EXCEPTION_WITH_EXTERNAL: '执行器出错（远端任务可能还在跑）',
  ARTIFACT_HASH_MISMATCH: '成片校验不一致，疑似被改动',
  COMFY_WORKFLOW_PROFILE_MISSING: '没有可用的生成工作流模板',
  COMFY_OUTPUT_MISSING: '生成结束但没有产出文件',
  EXTERNAL_JOB_NOT_FOUND: '生成端查不到这条任务',
  ATTEMPT_RECORD_MISSING: '尝试记录丢失',
  RECONCILIATION_UNAVAILABLE: '暂时无法核实结果',
  DEPENDENCY_BLOCKED: '前置镜头还没完成',
  DEPENDENCY_INVALIDATED: '前置镜头被重做，本任务作废',
  AGENT_DEPENDENCY_INVALIDATED: '依赖的前置结果已失效',
  SHOT_REVISION_REPLACED: '镜头内容已更新，请重新提交',
  STYLE_VERSION_SUPERSEDED: '风格版本已更新，请重新对齐',
  GPU_RESOURCE_LEASE_LOST: '显卡使用权丢失，待恢复',
  MODEL_RELEASE_RECONCILIATION_REQUIRED: '模型是否已卸载待核实',
  COMFY_HANDOFF_RECONCILIATION_REQUIRED: '执行环境交接待核实',
  COMFY_HANDOFF_OWNERSHIP_UNVERIFIED: '执行环境归属未确认',
  COMFY_ACTIVATION_RECONCILIATION_REQUIRED: '执行环境激活待核实',
  COMFY_HEARTBEAT_RECONCILIATION_REQUIRED: '心跳中断，状态待核实',
  RULE_PREFLIGHT_FAILED: '规则包预检未通过',
  LLM_TIMEOUT: '远端模型响应超时', LLM_CONNECTION: '连不上远端模型', LLM_SERVER: '远端模型服务出错',
  LLM_RATE_LIMIT: '服务商限制了调用频率，请稍候再试',
  LLM_AUTH: '服务商拒绝了模型凭据',
  LLM_MODEL_NOT_FOUND: '服务商找不到配置的接口或模型',
  LLM_ERROR: '服务商拒绝了这次生成请求',
  LLM_FORMAT: '模型返回的结构不符合该角色的契约',
  STORY_EXTRACTION_NEEDS_REVIEW: '这一卷的大纲没读完：有一场的模型请求结果无法确认，需要你决定是否重发这一幕',
  STORY_MERGE_NEEDS_REVIEW: '这一卷的大纲归并没有完成，需要核对后继续',
  STORY_DURABLE_OUTLINE_REQUIRED: '长剧本要先确认总体大纲，才能逐场分析',
  CONTRACT_OR_POLICY_REJECTED: '不符合剧组的硬性约束',
  EDITORIAL_TIMING_CONFLICT: '台词放不进生成段，或生成段时长与剧本切点表对不上',
  SHOT_DIALOGUE_LONGER_THAN_SHOT: '这一镜的台词比镜头还长，按原速说不完',
  SHOT_LOCATION_UNKNOWN: '镜头的地点不在已登记的场景里',
  SHOT_CONTINUITY_PREDECESSOR_UNKNOWN: '接拍依赖的上一镜不存在或不属于本作品',
  EDITORIAL_TIMING_UNGROUPABLE: '剧本切点表无法分组成 4–15 秒的 H3 生成段，请调整切点时长',
  EDITORIAL_BEAT_COUNT_MISMATCH: '编剧分段数与剧本切点表不一致，请重跑编剧或调整切点表',
  AGENT_REQUEST_RECONCILIATION_REQUIRED: '远端模型结果待核实，请勿重试',
  AGENT_REQUEST_REPLAY_BLOCKED: '这次未知请求已放弃，需要新的剧本修订',
  SOURCE_UNAVAILABLE: '官方原文暂不可用',
  SERIES_SOURCE_NOT_PASSED: '前序镜头未通过',
  SERIES_OWNERSHIP_LOST: '连续生产的接管权丢失',
  SERIES_CANCELLED: '连续生产已取消',
  DELIVERY_ASSEMBLY_LEASE_LOST: '成片合成被打断，待恢复',
};

/** 外部结果未知的渲染失败码：系统不自动重投，由人决定放弃或重拍。 */
export const UNCERTAIN_RENDER_FAILURE_CODES = [
  'COMFY_SUBMISSION_OUTCOME_UNKNOWN',
  'COMFY_WORKER_EXCEPTION_WITH_EXTERNAL',
  'COMFY_ACTIVATION_RECONCILIATION_REQUIRED',
  'COMFY_HANDOFF_RECONCILIATION_REQUIRED',
  'COMFY_JOB_NOT_FOUND',
];
/** 用户已放弃未知结果的失败码：下一步是重拍，不是再核对。 */
export const ABANDONED_RENDER_FAILURE_CODE = 'USER_ABANDONED_UNRECONCILED';

export const gateLabels: Record<string, string> = {
  technical: '技术校验（完整性与规格）', semantic: '画面建议',
  audio: '对白识别建议', style: '风格建议', final: '综合状态',
};

export const gateStatusLabels: Record<string, string> = {
  passed: '通过', failed: '未通过', pending: '待执行', running: '进行中',
  unavailable: '工具缺失，无法判定', skipped: '已跳过', needs_review: '需人工复核',
  PASSED: '通过', FAILED: '未通过', PENDING: '待执行', RUNNING: '进行中',
  UNAVAILABLE: '工具缺失，无法判定', NEEDS_REVIEW: '需人工复核',
};

export const reconciliationLabels: Record<string, string> = {
  collected: '已取回结果', discarded: '已废弃', submission_unknown: '结果不明待核实',
  rejected_before_queue: '排队前被拒', history_found: '已找到历史记录',
  completed_without_output: '完成但没有产物', not_found: '远端查无此任务',
  worker_exception_known_id: '执行器异常（任务号已知）',
};

export const repairOutcomeLabels: Record<string, string> = {
  REDONE: '已重做', REQUEUED: '已重新排队', DEFERRED: '暂缓，等人工', BLOCKED: '被阻断',
  SUCCEEDED: '修复成功', FAILED: '修复未成功',
};

export const repairActionLabels: Record<string, string> = {
  wait_for_human: '等待人工处理', recompile_prompt: '重新编译提示词', requeue: '重新排队',
  rebuild_manifest: '重建素材清单', adjust_parameters: '调整生成参数', rebind_asset: '更换绑定素材',
  none: '无需动作',
};

/** H3 单镜时长的硬边界，也是「单镜秒数上限」这一项设置的可调范围。 */
export const MIN_SHOT_SECONDS = 4;
export const MAX_SHOT_SECONDS = 15;
/**
 * 新分镜书的「单镜秒数上限」初始值：与后端 schemas.DEFAULT_MAX_SHOT_SECONDS 同源同值
 * （两处各有一条测试钉住）。它只是这项设置的起始值，不是另一道上限；已冻结的风格不受影响。
 */
export const DEFAULT_MAX_SHOT_SECONDS = 10;

export const reviewOptionLabels: Record<string, string> = {
  NOT_REVIEWED: '未审', PASS: '通过', FAIL: '不通过', NOT_APPLICABLE: '不适用',
};

export const continuityLabels: Record<string, string> = {
  SOFT_REFERENCE_CONTINUITY: '软连续（参考上一镜）',
  HARD_FRAME_ANCHOR_CONTINUITY: '硬连续（锚定上一镜尾帧）',
  soft: '软连续（参考上一镜）', hard: '硬连续（锚定上一镜尾帧）',
  last_frame: '软连续：参考上一镜末帧', cut: '独立镜头（不承接）',
  hard_frame_anchor: '硬连续：锚定上一镜末帧（能力待验证）',
  video_reference: '视频参考连续（传输未验收）',
};

export const finalizationLabels: Record<string, string> = {
  NONE: '—', IDLE: '未开始收尾', FINALIZING: '正在收尾', RELEASED: '已收尾并释放资源',
  RECONCILE_REQUIRED: '收尾中断，待核实',
};

export const prepTaskLabels: Record<string, string> = {
  RUNNING: '执行中', NEEDS_REVIEW: '待人工复核', RECONCILING: '结果核实中',
  COMPLETED: '已完成', FAILED: '失败', QUEUED: '排队中', PENDING: '待处理',
};

/** 制片准备面板里各类记录（候选资产/工作流版本/安装计划/音色）的状态合集。 */
export const prepRowLabels: Record<string, string> = {
  ...prepTaskLabels,
  AWAITING_APPROVAL: '等待批准', APPROVED: '已批准', READY_FOR_REVIEW: '待人工复核',
  PROMOTED: '已转正（正式素材）', REJECTED: '已拒绝',
  DRAFT: '草稿', STATIC_VALIDATED: '结构校验通过', FAKE_EXECUTED: '演练执行过（非真实验证）',
  REAL_TESTED: '真实测试通过', PRODUCTION_APPROVED: '生产已采纳', DEPRECATED: '已弃用',
  PREVIEW: '待试听确认', FROZEN: '已冻结',
  RECONCILED_NO_RETRY: '已对账收尾（不重试）', FAILED_RECONCILE_REQUIRED: '失败，需人工对账',
  SUBMIT_INTENT: '提交中断，待核实', INSTALLING: '正在安装',
  FILES_VERIFIED_PENDING_RUNTIME: '文件已核验，待运行验证', OK: '正常',
};

export const providerLabels: Record<string, string> = {
  mock: '演示', comfyui: '真实生成',
};

export const modeLabels: Record<string, string> = {
  t2v: '文字生成视频', i2v: '图片生成视频', t2v_first: '文字起版', i2v_first: '图片起版',
};

export const reasoningEffortLabels: Record<string, string> = {
  low: '低（快）', medium: '中', high: '高', xhigh: '超高', max: '最高（最慢最细致）',
};

export const providerKindLabels: Record<string, string> = {
  openai_compatible: 'OpenAI 兼容接口', anthropic: 'Anthropic 原生', ollama: 'Ollama（本机）',
  mock: '演示用假模型',
};

export const routeRoleLabels: Record<string, string> = {
  shot_planner: '镜头规划', script_planner: '剧本规划',
  four_stage_bible_run: '全剧蓝图', four_stage_scene_script_run: '分场剧本',
  scriptwriter: '编剧', director: '导演', cinematographer: '摄影指导', editor: '剪辑师',
  continuity_supervisor: '场记', sound_director: '声音导演', producer: '制片统筹',
  comfyops: '制作环境助理', repair_planner: '修复规划', qc_reviewer: '质量审核',
  vlm_reviewer: '画面审片', asr_reviewer: '语音核对',
};

export const workerPhaseLabels: Record<string, string> = {
  idle: '空闲', starting: '启动中', claiming: '正在领取任务', waiting: '等待任务',
  rendering: '生成中', submitting: '提交中', qc: '质检中', reviewing: '审片中',
  finalizing: '收尾中', reconciling: '核实中', sleeping: '休眠', stopping: '停止中',
};

export const serviceStatusLabels: Record<string, string> = {
  ok: '正常', passed: '通过', yes: '正常', no: '不满足', failed: '未通过',
  pending: '待检测', unavailable: '无法检测', unknown: '未知', skipped: '已跳过',
  warn: '有告警', running: '进行中', needs_review: '待人工复核', error: '出错',
};

export const auditActorLabels: Record<string, string> = {
  operator: '操作员', agent: 'AI 助手', worker: '后台执行器', system: '系统',
  user: '用户', admin: '管理员', api: '接口调用',
};

/** 审计实体：后端 add_audit 的第 4 个参数，这里是它面向人的名字。 */
export const auditEntityLabels: Record<string, string> = {
  project: '项目', asset: '素材', candidate_asset: '候选素材', candidate: '候选素材',
  style_version: '风格版本', shot: '分镜', job: '生成任务', job_attempt: '任务尝试',
  repair: '返工记录', workflow_profile: '工作流模板', provider_profile: '模型配置',
  series: '连续生产', delivery_order: '成片订单', terminology: '人名对照表',
  project_terminology: '人名对照表', subtitle_track: '字幕', rule_pack: '规则包',
  rulepack: '规则包', rights: '版权记录', installation_plan: '安装计划',
  installation_attempt: '安装尝试', library_asset: '素材库素材', library_version: '素材版本',
  library_render_task: '素材库出图任务', library_voice_profile: '素材库声音档案',
  library_media: '素材库媒体', library_rights: '素材版权', library_project_link: '素材库项目关联',
  director_package: '导演包', director_episode_plan: '导演分集计划', production_series: '生产系列',
  production_run: '生产批次', production_run_episode: '生产批次剧集', preproduction_plan: '前期计划',
  deployment: '部署', package: '资源包', agent_task: '智能体任务', agent_artifact: '智能体产物',
  agent_route: '智能体路由', agent_routes: '智能体路由', material_run: '素材生产',
  material_item: '素材条目', material_text_consent: '素材文字授权', asset_requirement: '素材需求',
  project_document: '项目文档', resource_lease: '资源占用', comfy_instance: '渲染器实例',
  relay_upscale_item: '接力放大条目', app_setting: '应用设置', setup_task: '环境准备任务',
  voice_runtime: '声音运行时', variant: '变体', tool_repair: '基础程序修复', artifact: '产物',
  style_lora: '风格 LoRA', system: '系统',
};

/** 审计事件：精编过的事件名。键必须与后端实际写下的事件码一致。 */
export const auditEventLabels: Record<string, string> = {
  PROJECT_CREATED: '创建项目', PROJECT_SETTINGS_UPDATED: '修改项目设置',
  PROJECT_TERMINOLOGY_SAVED: '保存人名对照表',
  ASSET_UPLOADED: '上传素材', ASSET_APPROVED: '批准素材', ASSET_LOCKED: '锁定素材',
  ASSET_METADATA_UPDATED: '修改素材信息', ASSET_UNFROZEN: '解冻素材',
  CANDIDATE_CREATED: '创建候选素材', CANDIDATE_PROMOTED: '候选素材转正',
  STYLE_DRAFT_UPDATED: '更新风格草稿', STYLE_FROZEN: '冻结风格版本',
  STYLE_IMPACT_ANALYZED: '分析风格影响', STYLE_APPLIED_TO_SHOTS: '风格应用到分镜',
  STYLE_LORA_IMPORTED: '导入风格 LoRA', STYLE_LORA_UPDATED: '修改风格 LoRA 配置',
  STYLE_LORA_DELETED: '删除风格 LoRA',
  SHOT_CREATED: '新建分镜', SHOT_BINDINGS_UPDATED: '更新分镜素材绑定',
  SHOT_CONTINUITY_SET: '设置分镜连续性', SHOT_REVISION_REWRITTEN: '重写分镜修订',
  SHOT_PROMPT_SAVED: '保存分镜提示词', SHOT_PROMPT_FROZEN: '冻结分镜提示词',
  SHOT_PROMPT_UNFROZEN: '解冻分镜提示词',
  SHOT_SUBTITLE_SAVED: '保存分镜字幕', SHOT_SUBTITLE_FROZEN: '冻结分镜字幕',
  SUBTITLE_REVISION_SAVED: '保存字幕修订', SUBTITLE_REVISION_APPROVED: '批准字幕修订',
  SUBTITLE_STYLE_UPDATED: '更新字幕字号',
  JOB_PAUSED: '任务暂停', JOB_RESUMED: '任务继续',
  OPERATOR_REVIEW_RECORDED: '记录人工复核',
  PRODUCTION_SERIES_CREATED: '创建生产系列', PRODUCTION_SERIES_PAUSED: '暂停生产系列',
  PRODUCTION_SERIES_RESUMED: '继续生产系列', PRODUCTION_SERIES_CANCELLED: '取消生产系列',
  PRODUCTION_RUN_CREATED: '创建生产批次', PRODUCTION_RUN_CANCELLED: '取消生产批次',
  PRODUCTION_BATCH_REOPENED_PRE_DISPATCH: '恢复待派发批次',
  DELIVERY_ASSEMBLED: '成片已装配', EPISODE_DELIVERED: '剧集已交付',
  DELIVERY_ACCEPTANCE_RECORDED: '记录人工验收',
  EPISODE_PLAN_CREATED: '创建分集计划', EPISODE_PLAN_CONFIRMED: '确认分集计划',
  EPISODE_SHOTS_CONFIRMED: '确认剧集分镜', EPISODE_SUBTITLE_COMPILED: '编译剧集字幕',
  DIRECTOR_PACKAGE_CREATED: '创建导演包', DIRECTOR_PACKAGE_CONFIRMED: '确认导演包',
  DIRECTOR_PRODUCTION_STARTED: '导演生产开始', DIRECTOR_STORYBOARD_QUEUED: '导演分镜已排队',
  CREATIVE_BRIEF_SAVED: '保存创意简报', CREATIVE_BRIEF_CONFIRMED: '确认创意简报',
  CREATIVE_SCRIPT_QUEUED: '创意剧本已排队', CREATIVE_SCRIPT_WRITTEN: '创意剧本已写出',
  STORY_OUTLINE_QUEUED: '故事大纲已排队', STORY_OUTLINE_CONFIRMED: '确认故事大纲',
  STORY_OUTLINE_EDITED: '编辑故事大纲', STORY_SECTIONS_SELECTED: '选择剧本章节',
  STORYBOARD_GENERATED: '生成分镜板', STORYBOARD_BOOK_GENERATED: '生成分镜手册',
  MATERIAL_PLAN_DRAFTED: '起草素材计划', MATERIAL_PLAN_CONFIRMED: '确认素材计划',
  MATERIAL_BATCH_CREATED: '创建素材批次', MATERIAL_BATCH_RECONCILED: '素材批次已对账',
  MATERIAL_BRIEF_PRODUCED: '产出素材需求单', MATERIAL_BRIEF_REFUSED: '素材需求单被拒绝',
  MATERIAL_PROMPT_FROZEN: '冻结素材提示词', MATERIAL_PROMPT_UNFROZEN: '解冻素材提示词',
  MATERIAL_SCENE_PREPARED: '素材场景已备好', MATERIAL_SCENE_AUTO_RETRY: '素材拆解已安排重试',
  MATERIAL_IMAGE_WORKER_LOST: '素材出图后台服务丢失',
  MATERIAL_MASTER_SELECTED: '选择素材主条目', MATERIAL_MASTER_ARCHIVED: '归档素材主条目',
  LIBRARY_ASSET_CREATED: '创建素材库素材', LIBRARY_VERSION_PROMOTED: '素材版本转正',
  LIBRARY_RIGHTS_APPROVED: '批准素材版权',
  LIBRARY_CHARACTER_SHEET_STEP_RECONCILED: '角色定妆照单步已对账（未完不拼版）',
  PROVIDER_PROFILE_CREATED: '新增模型配置', PROVIDER_PROFILE_UPDATED: '更新模型配置',
  PROVIDER_PROFILE_DELETED: '删除模型配置', PROVIDER_PROFILE_DUPLICATED: '模型配置重复',
  PROVIDER_KEY_UPDATED: '更新模型密钥', PROVIDER_KEY_CLEARED: '清除模型密钥',
  PROVIDER_CONNECTION_TESTED: '检测模型连接', PROVIDER_GENERATION_TESTED: '检测模型生成',
  WORKFLOW_PROFILE_REGISTERED: '注册工作流模板',
  WORKFLOW_PROFILE_VALIDATED: '校验工作流模板', WORKFLOW_PROFILE_DELETED: '删除工作流模板',
  TECHNICAL_QC_RUN: '运行技术质检', REPAIR_ESCALATED: '返工升级',
  REPAIR_PLAN_SUGGESTED: '建议返工方案', RULEPACK_APPROVED: '批准规则包',
  OUTPUT_DIRECTORY_CHANGED: '切换输出目录', QUALITY_POLICY_UPDATED: '更新质量策略',
  WORKER_EXCEPTION: '后台制作服务异常', MODEL_CALL_COMPLETED: '模型调用完成',
  IMAGE_RUNTIME_BOUND: '绑定出图运行时', IMAGE_RENDERER_STARTED: '出图渲染器启动',
  IMAGE_RENDERER_STOPPED: '出图渲染器停止', IMAGE_VALIDATION_BATCH_CREATED: '创建出图验证批次',
  VIDEO_ARTIFACT_DELETED: '删除成片产物', VIDEO_LIBRARY_ORGANIZED: '整理成片库',
  VOICE_RUNTIME_CONFIGURED: '配置声音运行时', AGENT_PLAN_CREATED: '创建智能体计划',
  AGENT_ROUTE_UPDATED: '更新智能体路由', AGENT_ROUTE_DELETED: '删除智能体路由',
  INSTALL_ATTEMPT_NEEDS_REVIEW: '安装尝试转入人工复核', INSTALL_ATTEMPT_RESUMED: '安装尝试已继续',
  LOCAL_INSTALL_QUEUED: '本机安装已排队', LOCAL_INSTALL_FAILED: '本机安装失败',
  EXISTING_COMFY_INSTALLATION_ADOPTED: '收编已有 ComfyUI',
  EXISTING_COMFY_INSTALLATION_VERIFIED: '核验已有 ComfyUI',
  PACKAGE_REGISTERED_FROM_CATALOG: '按目录登记资源包', PREPRODUCTION_BLOCKED: '前期制作被拦下',
};

/** 后端已经不再写下的事件码：留着是为了让历史记录仍然读得懂，不再当活码用。 */
export const auditEventLabelsDeprecated: Record<string, string> = {
  WORKFLOW_PROFILE_AUTO_REGISTERED: '自动登记工作流模板',
  PROJECT_UPDATED: '修改项目设置', ASSET_METADATA_PATCHED: '修改素材信息',
  CANDIDATE_REJECTED: '候选素材被拒', STYLE_DRAFT_SAVED: '保存风格草稿',
  SHOT_UPDATED: '修改分镜', SHOT_BINDINGS_SET: '更新镜头素材绑定',
  JOB_QUEUED: '任务进入队列', JOB_STARTED: '任务开始执行', JOB_COMPLETED: '任务完成',
  JOB_PASSED: '任务通过', JOB_FAILED: '任务失败', JOB_NEEDS_REVIEW: '任务转入人工复核',
  JOB_RECONCILED: '任务已核实', REPAIR_APPLIED: '执行返工', REVIEW_RECHECK: '重跑审片',
  OPERATOR_REVIEW_SUBMITTED: '人工审片提交', SERIES_STARTED: '连续生产开始',
  SERIES_FINISHED: '连续生产完成', SERIES_CANCELLED: '连续生产取消',
  DELIVERY_ORDER_CREATED: '生成成片订单', DELIVERY_RELEASED: '成片已交付',
  TERMINOLOGY_SAVED: '保存人名对照表', SUBTITLE_SAVED: '保存字幕',
  RIGHTS_APPROVED: '批准版权记录',
};

/** 事件码末段的动词：后端写下的事件名几乎都是「主体_动作」，动作在最后一段。 */
export const auditActionLabels: Record<string, string> = {
  SAVED: '保存', CREATED: '创建', UPDATED: '更新', DELETED: '删除', QUEUED: '加入队列',
  ENQUEUED: '已排队', STARTED: '开始', COMPLETED: '完成', FINISHED: '完成', FAILED: '失败',
  BLOCKED: '被拦下', RECONCILED: '已对账', RECONCILE: '对账', RECONCILIATION: '对账',
  CONFIRMED: '已确认', FROZEN: '已冻结', UNFROZEN: '已解冻', PINNED: '已钉住',
  APPROVED: '已批准', REJECTED: '已拒绝', REFUSED: '已拒绝', LOCKED: '已锁定',
  RESUMED: '已继续', PAUSED: '已暂停', ABANDONED: '已放弃', ADOPTED: '已收编',
  VERIFIED: '已核验', TESTED: '已检测', CACHED: '已缓存', BOUND: '已绑定',
  REBOUND: '已重新绑定', SELECTED: '已选择', CHOSEN: '已选择', INTENT: '意图已记录',
  REQUESTED: '已请求', AUTHORIZED: '已授权', GRANTED: '已授权', RELEASED: '已释放',
  APPLIED: '已应用', UNCERTAIN: '结果不确定', REQUIRED: '待处理', PENDING: '等待中',
  PROVEN: '已核实', REGISTERED: '已登记', CANCELLED: '已取消', UNCONFIRMABLE: '无法确认',
  PASSED: '已通过', RUNNING: '运行中', PROPOSED: '已提议', GENERATED: '已生成',
  READY: '已就绪', DRAFTED: '已起草', ATTEMPT: '一次尝试', EXHAUSTED: '已用尽',
  ARCHIVED: '已归档', PREPARED: '已备好', CONFIGURED: '已配置', UNAVAILABLE: '不可用',
  ISSUED: '已签发', VALIDATED: '已通过校验', EDITED: '已编辑', COMPILED: '已编译',
  MISSING: '缺失', EXECUTED: '已执行', ASSEMBLED: '已装配', PERSISTED: '已落盘',
  MATERIALIZED: '已实体化', PROMOTED: '已转正', CORRECTED: '已更正', DEFERRED: '已推迟',
  SUPERSEDED: '已被取代', ADVANCED: '已推进', STOPPED: '已停止', STORED: '已保存',
  SUGGESTED: '已建议', RETIRED: '已退役', ORGANIZED: '已整理', RECOVERED: '已恢复',
  DISCARDED: '已丢弃', ESCALATED: '已升级', UPLOADED: '已上传', REVALIDATED: '已重新核验',
  UNCONFIRMED: '未确认', TRANSFERRED: '已转移', ADJUSTED: '已调整', REVISED: '已修订',
  RECORDED: '已记录', REUSE: '已复用', REUSED: '已复用', ADDED: '已添加',
  REATTACHED: '已重新接管', RETRIED: '已重试', ACTIVATED: '已启用', DELIVERED: '已交付',
  ANALYZED: '已分析', COMMITTED: '已提交', EXCEPTION: '发生异常', LOST: '已丢失',
  JUDGED: '已判定', DUPLICATED: '重复', REWRITTEN: '已重写', ACKNOWLEDGED: '已确认',
  CLEARED: '已清除', SET: '已设置', PRODUCED: '已产出', WRITTEN: '已写入',
  RESERVED: '已预留', IMPORTED: '已导入', CHANGED: '已变更', TARGET: '目标',
  SUBMIT: '提交', QC: '质检', LEASE: '占用', ARTIFACT: '产物', CATALOG: '目录',
  BATCH: '批次', HANDOFF: '交接', STAGE: '阶段', REVIEW: '复核', SHOTS: '分镜',
  ONLY: '仅', HISTORY: '历史', PARAMETERS: '参数', PROJECT: '项目', RUN: '运行',
  SUBTITLE: '字幕', DEPENDENCY: '依赖',
};

/** 事件码首段的主体：动作之外的另一半。 */
export const auditSubjectLabels: Record<string, string> = {
  ADVISORY: '审片', AGENT: '智能体', ASSET: '素材', AUTO: '自动', CANDIDATE: '候选素材',
  CHARACTER: '人物', COMFY: '渲染器', CREATIVE: '创意', DELIVERY: '交付', DEPLOYMENT: '部署',
  DIRECTOR: '导演', EPISODE: '剧集', EXISTING: '已有安装', EXTERNAL: '外部实例', FILM: '影片',
  GENERATION: '生成', H3: 'H3 渲染', HANDOFF: '交接', IMAGE: '出图', INSTALL: '安装',
  INSTANCE: '实例', JOB: '生成任务', LIBRARY: '素材库', LITERARY: '文学', LOCAL: '本机安装',
  MACHINE: '机器', MATERIAL: '素材', MATERIALS: '素材', MOCK: '模拟', MODEL: '模型',
  OFFLINE: '离线', OPERATOR: '人工', OUTPUT: '输出', PACKAGE: '资源包', PERSISTED: '已落盘',
  PIPELINE: '流水线', PREPARATION: '准备', PREPRODUCTION: '前期制作', PRODUCTION: '生产',
  PROJECT: '项目', PROVIDER: '模型服务', QC: '质检', QUALITY: '质量', RELAY: '接力',
  REMOTE: '远端', REPAIR: '返工', RESOURCE: '资源', REVIEW: '复核', RULE: '规则',
  RULEPACK: '规则包', RUN: '运行', SCRIPT: '剧本', SERIES: '连续生产', SETUP: '环境准备',
  STALE: '过期结果', STORY: '剧本', STORYBOARD: '分镜板', STRATEGY: '策略',
  SUBMISSION: '提交', SUBMIT: '提交', SUBTITLE: '字幕', TECHNICAL: '技术质检',
  TOOL: '基础程序', UPLOADED: '上传', VERIFIED: '已核验', VIDEO: '成片', VOICE: '声音',
  WORKER: '后台制作服务', WORKFLOW: '工作流',
};

/**
 * 一个事件码面向人的说法。
 *
 * 后端写下的是大写事件码（COMFY_OUTPUT_PERSISTED 这样）。裸码上屏对谁都没有意义，
 * 所以这里逐层落到能读懂的话：精编名 → 动作 + 主体 → 动作 + 实体 → 兜底。
 * 任何一步都不把裸码原样交出去。
 */
export function auditEventLabel(code: string, entityType?: string | null): string {
  const precise = auditEventLabels[code];
  if (precise) return precise;
  const parts = code.split('_');
  const action = auditActionLabels[parts[parts.length - 1]];
  const subject = auditSubjectLabels[parts[0]];
  const entity = entityType ? auditEntityLabels[entityType] : undefined;
  if (action && subject) return `${action} · ${subject}`;
  if (action && entity) return `${action} · ${entity}`;
  if (action) return action;
  if (entity) return entity;
  return `未知操作（${code}）`;
}

export const subtitleStatusLabels: Record<string, string> = {
  PENDING: '草稿', DRAFT: '草稿', APPROVED: '已定稿',
};

export const qcResultLabels: Record<string, string> = {
  pass: '通过', passed: '通过', fail: '未通过', failed: '未通过',
  needs_review: '需人工复核', unavailable: '无法判定', mock: '演示数据',
};

export const checkStatusLabels: Record<string, string> = {
  pass: '通过', fail: '未通过', unavailable: '无法检测', warn: '有告警',
  skipped: '已跳过', missing: '缺失',
};

export const coverageLabels: Record<string, string> = {
  covered: '已覆盖', not_covered: '未覆盖', partial: '部分覆盖',
  full: '已覆盖', none: '未覆盖',
};

export const roleOptions = [
  ['character', '人物'], ['costume', '服装'], ['scene', '场景'], ['prop', '道具'],
  ['voice', '声音'], ['style_reference', '风格参考'], ['generic', '通用'],
] as const;

export const roleLabels: Record<string, string> = Object.fromEntries(
  roleOptions.map(([value, text]) => [value, text]),
);

export const useAsOptions = [
  ['subject', 'Subject 人物主体'], ['picture', 'Picture 图像参考'],
  ['video', 'Video 视频参考'], ['audio', 'Audio 音频参考'],
] as const;

export const useAsLabels: Record<string, string> = Object.fromEntries(
  useAsOptions.map(([value, text]) => [value, text]),
);

/** 大纲生成节奏三档：与后端 materialprep/pacing.py 的声明一一对应。 */
export const pacingOptions = [
  ['compact', '紧凑剧情', '冲突密集、推进快，适合短时长快切。'],
  ['balanced', '均衡剧情', '情节与人物/氛围均衡，默认节奏。'],
  ['relaxed', '舒缓剧情', '氛围与内心停留多、节奏慢，适合文艺慢切。'],
] as const;
export type PacingValue = (typeof pacingOptions)[number][0];
