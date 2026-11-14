import { api as request, ApiError, json, timeoutSignal } from './api';

function api<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, { ...init, signal: init?.signal ?? timeoutSignal(60_000) });
}

export type SetupPlan = { plan_id: string; sha256: string; kind?: string; status: string };
export const SETUP_EVIDENCE = '本机用户确认固定部署计划及后续实例配置步骤';

/**
 * The honest stage vocabulary of a persistent installation attempt.
 *
 * FILES_VERIFIED_PENDING_RUNTIME states that the pinned bytes are on disk.  It
 * is not a runtime claim: loading the checkpoint is still separate work.
 */
export type InstallationStage =
  | 'QUEUED'
  | 'DOWNLOADING'
  | 'RETRY_WAIT'
  | 'VERIFYING'
  | 'INSTALLING'
  | 'FILES_VERIFIED_PENDING_RUNTIME'
  | 'VALIDATING_RUNTIME_CONTRACT'
  | 'RUNTIME_CONTRACT_READY'
  | 'PAUSED'
  | 'CANCELLED'
  | 'FAILED_RETRYABLE'
  | 'NEEDS_REVIEW'
  | 'MOCK_VERIFIED';

/** The re-attachable truth about one installation attempt. */
export type InstallationAttemptView = {
  attempt_id: string;
  plan_id: string;
  plan_hash?: string | null;
  executor?: string | null;
  stage: InstallationStage | string;
  status: string;
  current_component?: string | null;
  current_file?: string | null;
  confirmed_bytes: number;
  total_bytes: number;
  speed_bps: number;
  retry_count: number;
  error_code?: string | null;
  error_message?: string | null;
  heartbeat_at?: string | null;
  cancel_requested: boolean;
  pause_requested: boolean;
  terminal: boolean;
  resumable: boolean;
  created_at?: string | null;
};

/** The answer of a queued installation: the attempt id is the only truth. */
export type QueuedInstallation = {
  attempt_id: string;
  status: string;
  stage: string;
  /** The plan that owns the attempt (may differ from the requested plan). */
  plan_id?: string;
  plan_hash: string;
  requested_plan_id?: string;
  requested_plan_hash?: string;
  reused?: boolean;
};

/** The answer of a synchronous execution that is not an installation queue. */
export type CompletedExecution = { status: string; [key: string]: unknown };

export type SetupExecutionResult = QueuedInstallation | CompletedExecution;

/** True when the execution produced a persistent attempt to re-attach to. */
export function isQueuedInstallation(result: SetupExecutionResult): result is QueuedInstallation {
  const candidate = result as QueuedInstallation;
  return typeof candidate?.attempt_id === 'string' && candidate.attempt_id.length > 0;
}

const ATTEMPT_KEY = (planId: string) => `shine.setupAttempt.${planId}`;
const UNCERTAIN_KEY = (planId: string) => `shine.setupUnconfirmed.${planId}`;

/** Remember the attempt of a plan so a reopened page can re-attach to it. */
export function rememberInstallationAttempt(planId: string, attemptId: string): void {
  try {
    localStorage.setItem(ATTEMPT_KEY(planId), attemptId);
  } catch {
    /* a private browser may refuse storage; the server stays the truth */
  }
}

/** The attempt id this browser remembers for a plan, if any. */
export function storedInstallationAttempt(planId: string): string | null {
  try {
    return localStorage.getItem(ATTEMPT_KEY(planId));
  } catch {
    return null;
  }
}

/** Forget the attempt of a plan; the server record is never deleted. */
export function forgetInstallationAttempt(planId: string): void {
  try {
    localStorage.removeItem(ATTEMPT_KEY(planId));
  } catch {
    /* ignore */
  }
}

function markUncertain(planId: string): void {
  try {
    localStorage.setItem(UNCERTAIN_KEY(planId), '1');
  } catch {
    /* ignore */
  }
}

function clearUncertain(planId: string): void {
  try {
    localStorage.removeItem(UNCERTAIN_KEY(planId));
  } catch {
    /* ignore */
  }
}

function isUncertain(planId: string): boolean {
  try {
    return localStorage.getItem(UNCERTAIN_KEY(planId)) === '1';
  } catch {
    return false;
  }
}

/** Read the current state of one installation attempt. */
export async function fetchInstallationAttempt(attemptId: string): Promise<InstallationAttemptView> {
  return api<InstallationAttemptView>(`/comfyops/installation-attempts/${attemptId}`);
}

/** Ask the Worker to stop at the next safe point and keep the bytes. */
export async function pauseInstallationAttempt(attemptId: string): Promise<InstallationAttemptView> {
  return api<InstallationAttemptView>(`/comfyops/installation-attempts/${attemptId}/pause`, json('POST', {}));
}

/** Continue a paused or retryable attempt from its confirmed bytes. */
export async function resumeInstallationAttempt(attemptId: string): Promise<InstallationAttemptView> {
  return api<InstallationAttemptView>(`/comfyops/installation-attempts/${attemptId}/resume`, json('POST', {}));
}

/** Cancel the attempt.  Only this explicit request stops the download. */
export async function cancelInstallationAttempt(attemptId: string): Promise<InstallationAttemptView> {
  return api<InstallationAttemptView>(`/comfyops/installation-attempts/${attemptId}/cancel`, json('POST', {}));
}

/** The stages that mean the installation did not finish. */
const INSTALL_FAILED_STAGES = ['NEEDS_REVIEW', 'CANCELLED', 'FAILED_RETRYABLE'];

/**
 * Follow one installation attempt until it reaches a definite stage.
 *
 * The wait is a short poll of the server state, not one long HTTP request:
 * a refresh or a restart loses nothing, and the operator can pause, resume
 * or cancel while the wait runs.
 */
export async function waitForInstallationAttempt(
  attemptId: string,
  onUpdate?: (view: InstallationAttemptView) => void,
  options?: { intervalMs?: number; maxTicks?: number },
): Promise<InstallationAttemptView> {
  const interval = options?.intervalMs ?? 2000;
  const maxTicks = options?.maxTicks ?? 1800;
  for (let tick = 0; tick < maxTicks; tick += 1) {
    const view = await fetchInstallationAttempt(attemptId);
    onUpdate?.(view);
    if (view.terminal) {
      if (INSTALL_FAILED_STAGES.includes(view.stage)) {
        throw new Error('安装停在 ' + view.stage + (view.error_code ? '（' + view.error_code + '）' : '')
          + '：' + (view.error_message ?? '') + '。请查看安装详情后处理；不会自动重投。');
      }
      return view;
    }
    await new Promise(resolve => setTimeout(resolve, interval));
  }
  throw new Error('安装仍在进行中（INSTALL_STILL_RUNNING）：可以稍后重新打开页面继续查看进度；不会重复提交。');
}

/**
 * The attempts that write this plan's physical target.
 *
 * The receipt belongs to the directory, not to the plan row that requested it
 * first: two workspaces that install the same bytes into the same root share
 * one attempt.  Asking only for this plan's own rows loses that receipt after
 * a refresh or a cleared browser cache (J02).
 */
async function targetAttempts(planId: string): Promise<InstallationAttemptView[]> {
  const rows = await api<Array<InstallationAttemptView>>(
    `/comfyops/install-plans/${planId}/attempts?scope=target`,
  );
  return (rows ?? []).filter((row) => !!row?.attempt_id);
}

/** The newest attempt that still owns this plan's target, if there is one. */
export async function findActiveAttempt(planId: string): Promise<InstallationAttemptView | null> {
  const rows = await targetAttempts(planId);
  return [...rows].reverse().find((row) => !row.terminal) ?? null;
}

/**
 * The newest attempt of this target, including a finished one.
 *
 * A reopened page must be able to show "the installation already finished"
 * instead of an empty installer, so completion is a valid re-attachment.
 */
export async function latestInstallationAttempt(planId: string): Promise<InstallationAttemptView | null> {
  const rows = await targetAttempts(planId);
  return rows.length ? rows[rows.length - 1] : null;
}

/** The server record of the attempts of a plan, if it can be read at all. */
async function lookupAttempt(planId: string): Promise<InstallationAttemptView | null> {
  try {
    return await latestInstallationAttempt(planId);
  } catch {
    return null;
  }
}

/**
 * Approve the plan and queue its installation.
 *
 * The request only queues a persistent attempt: the resident Worker executes
 * it and the browser re-attaches by attempt id.  Nothing waits inside one long
 * HTTP request, so a slow download, a refresh or a restart never loses the
 * installation.
 */
export async function approveAndExecuteSetupPlan(
  plan: SetupPlan,
  check: () => void = () => undefined,
  options?: { wait?: boolean },
): Promise<SetupExecutionResult> {
  check();
  if (isUncertain(plan.plan_id)) {
    // The previous request's outcome is unknown.  The server record is the
    // truth: re-attach to it instead of submitting the plan a second time.
    const view = await lookupAttempt(plan.plan_id);
    if (view) {
      clearUncertain(plan.plan_id);
      rememberInstallationAttempt(plan.plan_id, view.attempt_id);
      if (options?.wait !== false) {
        await waitForInstallationAttempt(view.attempt_id);
      }
      return {
        attempt_id: view.attempt_id,
        status: view.status,
        stage: view.stage,
        plan_id: view.plan_id,
        plan_hash: view.plan_hash ?? plan.sha256,
        requested_plan_id: plan.plan_id,
        requested_plan_hash: plan.sha256,
        reused: true,
      };
    }
    throw new Error('EXECUTION_UNCONFIRMED：请到专业设置对账，不能重投此计划。');
  }
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
  const path = plan.kind === 'INSTANCE_LIFECYCLE'
    ? `/comfyops/instance-plans/${plan.plan_id}/execute`
    : `/comfyops/install-plans/${plan.plan_id}/execute`;
  const body = plan.kind === 'INSTANCE_LIFECYCLE'
    ? { plan_hash: digest }
    : { plan_hash: digest, executor: 'local' };
  if (plan.kind === 'INSTANCE_LIFECYCLE') {
    // The instance chain executor is synchronous and unchanged: it drives the
    // local ComfyUI instance and answers with its own result.
    const result = await api<CompletedExecution>(path, {
      ...json('POST', body),
      signal: timeoutSignal(600_000),
    });
    check();
    return result;
  }
  let queued: QueuedInstallation | null = null;
  try {
    const answer = await api<QueuedInstallation>(path, {
      ...json('POST', body),
      signal: timeoutSignal(120_000),
    });
    check();
    if (!answer || !answer.attempt_id) {
      throw new Error('安装没有返回尝试编号（EXECUTION_UNCONFIRMED）');
    }
    rememberInstallationAttempt(plan.plan_id, answer.attempt_id);
    queued = answer;
  } catch (error) {
    // A disconnected browser cannot tell whether the server committed the
    // request.  The server record is the truth: re-attach to it instead of
    // submitting the plan a second time.
    const view = await lookupAttempt(plan.plan_id);
    if (view) {
      // The server committed the request after all: re-attach to that attempt
      // instead of submitting the plan a second time.
      clearUncertain(plan.plan_id);
      rememberInstallationAttempt(plan.plan_id, view.attempt_id);
      queued = {
        attempt_id: view.attempt_id,
        status: view.status,
        stage: view.stage,
        plan_id: view.plan_id,
        plan_hash: view.plan_hash ?? digest,
        requested_plan_id: plan.plan_id,
        requested_plan_hash: digest,
        reused: true,
      };
    } else {
      // The outcome stays unknown: mark it and refuse a blind resubmission.
      markUncertain(plan.plan_id);
      throw new Error(`执行未确认（EXECUTION_UNCONFIRMED）：${String(error instanceof ApiError ? error.message : error)}。请刷新页面重新附着已有尝试；不要重复提交。`);
    }
  }
  if (options?.wait !== false) {
    // The installation is a persistent task: follow its real stage instead of
    // holding one HTTP request open for the whole download.
    await waitForInstallationAttempt(queued.attempt_id);
  }
  return queued;
}