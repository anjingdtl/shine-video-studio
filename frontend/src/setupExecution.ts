import { api as request, ApiError, json } from './api';

function api<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, { ...init, signal: init?.signal ?? AbortSignal.timeout(60_000) });
}

export type SetupPlan = { plan_id: string; sha256: string; kind?: string; status: string };
export const SETUP_EVIDENCE = '本机用户确认固定部署计划及后续实例配置步骤';

/** Shared by both setup surfaces. Approval changes the hash; never execute the old one. */
export async function approveAndExecuteSetupPlan(plan: SetupPlan, check: () => void = () => undefined) {
  check();
  const uncertaintyKey = `shine.setupUnconfirmed.${plan.plan_id}`;
  if (localStorage.getItem(uncertaintyKey)) throw new Error('EXECUTION_UNCONFIRMED：请到专业设置对账，不能重投此计划。');
  const context = await api<{ local_execution_enabled: boolean }>('/comfyops/execution-context');
  check();
  if (!context.local_execution_enabled) {
    throw new Error('本机执行尚未启用（LOCAL_EXECUTION_NOT_ENABLED）。请运行一键安装流程，或在本机 .env 设置 SHINE_ENABLE_LOCAL_EXECUTION=1 后重启服务。');
  }
  let digest = plan.sha256;
  if (plan.status === 'AWAITING_APPROVAL') {
    const grant = await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
      json('POST', { action: 'install', target_id: plan.plan_id, context: null }));
    check();
    const approved = await api<{ sha256: string }>(`/comfyops/install-plans/${plan.plan_id}/approve`,
      json('POST', { grant_id: grant.grant_id, snapshot: grant.snapshot, evidence: SETUP_EVIDENCE }));
    check();
    digest = approved.sha256;
  } else if (plan.status !== 'APPROVED') {
    throw new Error(`计划状态 ${plan.status} 不允许执行，请到专业设置检查或对账；没有自动重试。`);
  }
  check();
  let result: { status: string; payload?: { is_mock?: boolean } };
  try {
    localStorage.setItem(uncertaintyKey, '1');
    result = await api(`/comfyops/${plan.kind === 'INSTANCE_LIFECYCLE' ? 'instance-plans' : 'install-plans'}/${plan.plan_id}/execute`, {
      ...json('POST', plan.kind === 'INSTANCE_LIFECYCLE' ? { plan_hash: digest } : { plan_hash: digest, executor: 'local' }),
      signal: AbortSignal.timeout(3_600_000),
    });
    localStorage.removeItem(uncertaintyKey);
  } catch (error) {
    if (error instanceof ApiError) localStorage.removeItem(uncertaintyKey);
    // A disconnected browser cannot tell whether the server committed the operation.
    throw new Error(`执行未确认，请到专业设置读取尝试记录并对账，禁止直接重投（EXECUTION_UNCONFIRMED）：${String(error)}`);
  }
  check();
  if (!['FILES_VERIFIED', 'COMPLETED'].includes(result.status) || result.payload?.is_mock) {
    throw new Error(`执行停在 ${result.status}，请到专业设置检查或对账；没有自动重试。`);
  }
  return result;
}
