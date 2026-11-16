/** 错误码 → 一句话原因 + 可选动作（设计方案 §7.2/§7.3）。
 *  取代「；」join 整句一行：每条 blocker 一行，可行动的带「去处理」按钮。
 *  target 是语义导航键，由装配层（App）解析为真实跳转，前端不硬编码路由。 */
export type ErrorActionTarget =
  | 'setup-models' | 'setup-env' | 'assets-stage' | 'plan-stage' | 'package-stage'
  | 'creative-stage' | 'produce-stage' | 'run-stage' | 'library' | 'retry-scene'
  | 'split-plan' | 'consent';

export type ErrorAction = {
  reason: string;
  /** 为什么这一步必须做：必做动作块要向人说清代价，不只说缺什么。 */
  why?: string;
  action?: { label: string; target: ErrorActionTarget };
  /** 当 action 的落点页就是用户当前所在页时改指的动作：真正的上游前置。
   *  例如分集未确认出现在剧本统筹页本身，此时真正缺的是第 1 阶段的大纲确认。 */
  selfLoopAction?: { label: string; target: ErrorActionTarget };
};

export const ERROR_ACTIONS: Record<string, ErrorAction> = {
  PENDING_WORKFLOW_MAPPING: { reason: '本作品还没登记生成方案', action: { label: '去基础配置登记', target: 'setup-models' } },
  WORKFLOW_SIZE_NOT_MAPPED: { reason: '生成方案没有接入画幅设置', action: { label: '去基础配置登记', target: 'setup-models' } },
  PROFILE_NOT_VALIDATED: { reason: '生成方案还没在本机验证节点', action: { label: '去验证本机节点', target: 'setup-models' } },
  H3_CHARACTER_REFERENCE_MISSING: { reason: '有出场人物没有主素材', action: { label: '去准备素材', target: 'assets-stage' } },
  SHOT_CHARACTER_MASTER_MISSING: { reason: '镜头点名的人物缺主素材', action: { label: '去准备素材', target: 'assets-stage' } },
  SHOT_REFERENCE_MISSING: { reason: '镜头至少需要一张参考图（人物或场景）才能 Ref2VA 开拍', action: { label: '去准备场景图', target: 'assets-stage' } },
  SCRIPT_TEXT_CONSENT_REQUIRED: { reason: '首次发送剧本到云端需同意', action: { label: '查看说明并同意', target: 'consent' } },
  REMOTE_AGENT_CONSENT_REQUIRED: { reason: '首次发送剧本到云端需同意', action: { label: '查看说明并同意', target: 'consent' } },
  SHOT_REVISION_CHANGED: { reason: '镜头已升版，需要新导演包', action: { label: '重新生成导演包', target: 'package-stage' } },
  PIPELINE_EPISODE_SCRIPT_SUPERSEDED: { reason: '分集剧本已被新大纲替代', action: { label: '重做这一卷分集', target: 'package-stage' } },
  LLM_TIMEOUT: { reason: '云端模型响应超时', action: { label: '重试这一场', target: 'retry-scene' } },
  LLM_RATE_LIMIT: { reason: '云端模型限流，稍后重试', action: { label: '重试这一场', target: 'retry-scene' } },
  MATERIAL_IDENTITY_AMBIGUOUS: { reason: '同一人物在多场外观描述不一致', action: { label: '按单场拆分计划', target: 'split-plan' } },
  // 「去剧本统筹」必须真的落到剧本统筹（#d/assets）。它原来指向导演包（#d/package），
  //  恢复动作把用户送到比来源更靠后的阶段，绕圈而不推进。
  EPISODE_PLAN_NOT_CONFIRMED: {
    reason: '分集方案尚未确认',
    action: { label: '去剧本统筹', target: 'plan-stage' },
    // 这个阻塞就出现在剧本统筹页本身：此时「去剧本统筹」是零进展的自环，
    // 真正的上游前置是第 1 阶段的大纲确认（该页点「生成分镜书」返回
    // 422 SCRIPT_OUTLINE_NOT_CONFIRMED，/journey 的 script.outline_confirmed=false）。
    selfLoopAction: { label: '去第 1 阶段确认大纲', target: 'creative-stage' },
  },
  PACKAGE_NOT_CONFIRMED: { reason: '导演包尚未确认', action: { label: '去导演包', target: 'package-stage' } },
  NO_PACKAGE: { reason: '尚未生成导演包', action: { label: '去导演包', target: 'package-stage' } },
  SCRIPT_NOT_PREPARED: { reason: '还没有剧本', action: { label: '去创意与剧本', target: 'creative-stage' } },
  // 分镜必须基于一个已冻结的风格版本；风格在第②阶段与分集一起定稿。
  STORYBOARD_STYLE_NOT_FROZEN: {
    reason: '风格还没有冻结',
    why: '分镜必须引用一个已冻结的风格版本，否则镜头与成片的风格无法对齐。',
    action: { label: '去冻结风格', target: 'plan-stage' },
  },
  // 大纲是分集与分镜的共同依据；没确认大纲时，分集与分镜都不能开始。
  SCRIPT_OUTLINE_NOT_CONFIRMED: {
    reason: '剧本大纲还没有确认',
    why: '分集、分镜和导演包都以你确认的那一版大纲为依据，大纲未确认时它们无法成立。',
    action: { label: '去确认大纲', target: 'creative-stage' },
  },
  // 分集方案要先落到已应用的镜头上，才谈得上下游一致。
  EPISODE_PLAN_NOT_MATERIALIZED: {
    reason: '分集方案还没有落到镜头上',
    why: '分集要按场次对应到已应用的镜头，否则这一集的镜头范围不成立。',
    action: { label: '去把分集落到镜头', target: 'produce-stage' },
  },
  IMAGE_RUNTIME_NOT_READY: { reason: '本机出图环境还没就绪', action: { label: '去基础配置', target: 'setup-env' } },
  LLM_NOT_CONFIGURED: { reason: '还没配置文本模型', action: { label: '去配置文本模型', target: 'setup-models' } },
  // 装机与门禁常见拒绝：服务端消息已说清原因，这里补上「去哪里处理」。
  HOST_NOT_QUALIFIED: {
    reason: '这台电脑没有通过这套方案的硬件准入',
    why: '下载权重是存储行为，运行模型才需要算力。门槛对比在基础配置的能力卡上如实展示；可以先准备其它能力。',
    action: { label: '查看硬件门槛', target: 'setup-env' },
  },
  PROFILE_WEIGHTS_NOT_AVAILABLE: { reason: '这套方案的权重还没在本机就位', action: { label: '去基础配置准备', target: 'setup-models' } },
  PROFILE_WEIGHTS_NOT_VERIFIED: { reason: '权重在本机但还没逐字节核验', action: { label: '去核验已有文件', target: 'setup-models' } },
  PROFILE_WEIGHTS_NOT_CONSUMABLE: { reason: '权重已核验但当前渲染器读不到那个目录', action: { label: '去绑定到实例', target: 'setup-models' } },
  PROFILE_NOT_REGISTERED_FOR_PROJECT: { reason: '候选方案还没登记到当前作品', action: { label: '去登记方案', target: 'setup-models' } },
  PROFILE_GRAPH_IDENTITY_CHANGED: { reason: '方案图哈希与变体清单不一致', action: { label: '去基础配置核对', target: 'setup-models' } },
  MANAGED_INSTANCE_NOT_CONFIGURED: { reason: '本机还没有受管渲染器配置', action: { label: '去基础配置准备', target: 'setup-env' } },
  MODEL_VARIANT_NOT_ACCEPTED: { reason: '候选模型还没通过验收，不能用于正式生产', action: { label: '查看模型状态', target: 'setup-models' } },
  VARIANT_NOT_DOWNLOADABLE: { reason: '这个变体未进入自动下载路径', action: { label: '查看候选准备方式', target: 'setup-models' } },
  REAL_RUNTIME_NOT_ACTIVATED: { reason: '真实制作配置还没验证激活', action: { label: '去基础配置完成', target: 'setup-env' } },
  SERVICE_VALIDATION_REQUIRED: { reason: '本机服务或工作流变化，需要重新验证', action: { label: '去重新验证', target: 'setup-env' } },
  ASSET_RIGHTS_NOT_APPROVED: { reason: '素材权利还没确认', action: { label: '去确认素材', target: 'assets-stage' } },
  CHARACTER_NAME_ALREADY_IN_USE: { reason: '这个名字已绑定另一张人物素材', action: { label: '去素材页核对', target: 'assets-stage' } },
  OUTPUT_DIRECTORY_BUSY: { reason: '有活动任务，输出目录暂不能迁移', action: { label: '查看活动任务', target: 'setup-env' } },
  SHOT_SUBMISSION_OUTCOME_UNKNOWN: { reason: '上一次外部提交结果还没核对', why: '可能已发出的请求不会自动重发；先核对原任务，再决定是否重拍。', action: { label: '去进度页核对', target: 'run-stage' } },
};

/** 把服务端 blocker 映射为可行动条目；未知码保留原文，不编造动作。 */
export function describeBlocker(
  code: string | undefined,
  message: string,
): {
  reason: string;
  why?: string;
  action?: { label: string; target: ErrorActionTarget };
  selfLoopAction?: { label: string; target: ErrorActionTarget };
} {
  const known = code ? ERROR_ACTIONS[code] : undefined;
  if (known) {
    return { reason: known.reason, why: known.why, action: known.action, selfLoopAction: known.selfLoopAction };
  }
  return { reason: message || code || '需要处理' };
}