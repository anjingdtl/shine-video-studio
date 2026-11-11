/** 错误码 → 一句话原因 + 可选动作（设计方案 §7.2/§7.3）。
 *  取代「；」join 整句一行：每条 blocker 一行，可行动的带「去处理」按钮。
 *  target 是语义导航键，由装配层（App）解析为真实跳转，前端不硬编码路由。 */
export type ErrorActionTarget =
  | 'setup-models' | 'setup-env' | 'assets-stage' | 'package-stage' | 'storyboard-stage'
  | 'run-stage' | 'library' | 'retry-scene' | 'split-plan' | 'consent';

export type ErrorAction = { reason: string; action?: { label: string; target: ErrorActionTarget } };

export const ERROR_ACTIONS: Record<string, ErrorAction> = {
  PENDING_WORKFLOW_MAPPING: { reason: '本作品还没登记生成方案', action: { label: '去基础配置登记', target: 'setup-models' } },
  WORKFLOW_SIZE_NOT_MAPPED: { reason: '生成方案没有接入画幅设置', action: { label: '去基础配置登记', target: 'setup-models' } },
  PROFILE_NOT_VALIDATED: { reason: '生成方案还没在本机验证节点', action: { label: '去验证本机节点', target: 'setup-models' } },
  H3_CHARACTER_REFERENCE_MISSING: { reason: '有出场人物没有主素材', action: { label: '去准备素材', target: 'assets-stage' } },
  SHOT_CHARACTER_MASTER_MISSING: { reason: '镜头点名的人物缺主素材', action: { label: '去准备素材', target: 'assets-stage' } },
  SCRIPT_TEXT_CONSENT_REQUIRED: { reason: '首次发送剧本到云端需同意', action: { label: '查看说明并同意', target: 'consent' } },
  REMOTE_AGENT_CONSENT_REQUIRED: { reason: '首次发送剧本到云端需同意', action: { label: '查看说明并同意', target: 'consent' } },
  SHOT_REVISION_CHANGED: { reason: '镜头已升版，需要新导演包', action: { label: '重新生成导演包', target: 'package-stage' } },
  PIPELINE_EPISODE_SCRIPT_SUPERSEDED: { reason: '分集剧本已被新大纲替代', action: { label: '重做这一卷分集', target: 'package-stage' } },
  LLM_TIMEOUT: { reason: '云端模型响应超时', action: { label: '重试这一场', target: 'retry-scene' } },
  LLM_RATE_LIMIT: { reason: '云端模型限流，稍后重试', action: { label: '重试这一场', target: 'retry-scene' } },
  MATERIAL_IDENTITY_AMBIGUOUS: { reason: '同一人物在多场外观描述不一致', action: { label: '按单场拆分计划', target: 'split-plan' } },
  EPISODE_PLAN_NOT_CONFIRMED: { reason: '分集方案尚未确认', action: { label: '去剧本统筹', target: 'package-stage' } },
  PACKAGE_NOT_CONFIRMED: { reason: '导演包尚未确认', action: { label: '去导演包', target: 'package-stage' } },
  NO_PACKAGE: { reason: '尚未生成导演包', action: { label: '去导演包', target: 'package-stage' } },
  SCRIPT_NOT_PREPARED: { reason: '还没有剧本', action: { label: '去创意与剧本', target: 'storyboard-stage' } },
  IMAGE_RUNTIME_NOT_READY: { reason: '本机出图环境还没就绪', action: { label: '去基础配置', target: 'setup-env' } },
  LLM_NOT_CONFIGURED: { reason: '还没配置文本模型', action: { label: '去配置文本模型', target: 'setup-models' } },
};

/** 把服务端 blocker 映射为可行动条目；未知码保留原文，不编造动作。 */
export function describeBlocker(
  code: string | undefined,
  message: string,
): { reason: string; action?: { label: string; target: ErrorActionTarget } } {
  const known = code ? ERROR_ACTIONS[code] : undefined;
  if (known) return { reason: known.reason, action: known.action };
  return { reason: message || code || '需要处理' };
}