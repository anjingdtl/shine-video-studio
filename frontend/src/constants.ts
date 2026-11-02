import type { Style } from './api';

export const emptyStyle: Style = {
  photography_language: '', shot_blocking: '', color_palette: '', lighting_tone: '',
  lens_texture: '', editing_rhythm: '', sound_performance: '',
};

export const styleFields: Array<[keyof Style, string, string]> = [
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
  accepted: '已通过终审', pending_review: '待人工验收',
  rejected: '未通过', mock: '仅演示验证', not_run: '未执行',
};

export const failureLabels: Record<string, string> = {
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
  SOURCE_UNAVAILABLE: '官方原文暂不可用',
  SERIES_SOURCE_NOT_PASSED: '前序镜头未通过',
  SERIES_OWNERSHIP_LOST: '连续生产的接管权丢失',
  SERIES_CANCELLED: '连续生产已取消',
  DELIVERY_ASSEMBLY_LEASE_LOST: '成片合成被打断，待恢复',
};

export const gateLabels: Record<string, string> = {
  technical: '技术校验（画面/声音规格）', semantic: '内容审查（AI 看片）',
  audio: '声音校验', style: '风格一致性', final: '终审',
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

export const reviewOptionLabels: Record<string, string> = {
  NOT_REVIEWED: '未审', PASS: '通过', FAIL: '不通过', NOT_APPLICABLE: '不适用',
};

export const continuityLabels: Record<string, string> = {
  SOFT_REFERENCE_CONTINUITY: '软连续（参考上一镜）',
  HARD_FRAME_ANCHOR_CONTINUITY: '硬连续（锚定上一镜尾帧）',
  soft: '软连续（参考上一镜）', hard: '硬连续（锚定上一镜尾帧）',
  last_frame: '软连续：承接上一镜末帧', cut: '独立镜头（不承接）',
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
  SUBMIT_INTENT: '提交中断，待核实', OK: '正常',
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

export const auditEntityLabels: Record<string, string> = {
  project: '项目', asset: '素材', candidate_asset: '候选素材', style_version: '风格版本',
  shot: '分镜', job: '生成任务', job_attempt: '任务尝试', repair: '返工记录',
  workflow_profile: '工作流模板', provider_profile: '模型配置', series: '连续生产',
  delivery_order: '成片订单', terminology: '人名对照表', subtitle_track: '字幕',
  rule_pack: '规则包', rights: '版权记录', installation_plan: '安装计划',
};

export const auditEventLabels: Record<string, string> = {
  PROJECT_CREATED: '创建项目', PROJECT_UPDATED: '修改项目设置',
  ASSET_UPLOADED: '上传素材', ASSET_APPROVED: '批准素材', ASSET_LOCKED: '锁定素材',
  ASSET_METADATA_PATCHED: '修改素材信息',
  CANDIDATE_PROMOTED: '候选素材转正', CANDIDATE_REJECTED: '候选素材被拒',
  STYLE_DRAFT_SAVED: '保存风格草稿', STYLE_FROZEN: '冻结风格版本',
  SHOT_CREATED: '新建分镜', SHOT_UPDATED: '修改分镜', SHOT_BINDINGS_SET: '更新镜头素材绑定',
  JOB_QUEUED: '任务进入队列', JOB_STARTED: '任务开始执行', JOB_COMPLETED: '任务完成',
  JOB_PASSED: '任务通过', JOB_FAILED: '任务失败', JOB_PAUSED: '任务暂停', JOB_RESUMED: '任务继续',
  JOB_NEEDS_REVIEW: '任务转入人工复核', JOB_RECONCILED: '任务已核实',
  REPAIR_APPLIED: '执行返工', REVIEW_RECHECK: '重跑审片',
  OPERATOR_REVIEW_SUBMITTED: '人工审片提交',
  SERIES_STARTED: '连续生产开始', SERIES_FINISHED: '连续生产完成',
  SERIES_CANCELLED: '连续生产取消',
  DELIVERY_ORDER_CREATED: '生成成片订单', DELIVERY_RELEASED: '成片已交付',
  TERMINOLOGY_SAVED: '保存人名对照表', SUBTITLE_SAVED: '保存字幕',
  WORKFLOW_PROFILE_REGISTERED: '注册工作流模板', PROVIDER_PROFILE_CREATED: '新增模型配置',
  RIGHTS_APPROVED: '批准版权记录',
};

export const subtitleStatusLabels: Record<string, string> = {
  PENDING: '草稿', APPROVED: '已定稿',
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
