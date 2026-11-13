import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, Cpu, Loader2, PlugZap, ShieldAlert, ShieldCheck, Square } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import {
  ApiError,
  ERROR_CODE_LABELS,
  fetchRuntimeOperation,
  fetchRuntimeStatus,
  isStaleRuntimeState,
  submitRuntimeControl,
  type ControlOperation,
  type RuntimeSnapshot,
} from '../api';

/** 顶部常驻的「本地制作引擎」状态条（统一运行调度控制面）。
 *
 * 唯一数据来源是后端 /runtime-orchestrator/status 的派生快照；这里只做
 * 展示与发起安全动作（启动/停止/检查连接/核对恢复），绝不自行推断
 * ComfyUI 是否在线，也绝不在浏览器里决定进程的生死。
 *
 * 每次动作之后不止刷新快照，还要把这次动作的结论翻译成一句人话：拒绝、
 * 待人工核对、失败都各有措辞，且不出现 PID / Mutex / 租约这类工程词。 */

type Visual = { icon: LucideIcon; tone: 'idle' | 'busy' | 'starting' | 'warn' | 'down' | 'off'; label: string };

const VISUALS: Record<string, Visual> = {
  NOT_BOUND: { icon: PlugZap, tone: 'off', label: '引擎未配置' },
  STOPPED: { icon: Square, tone: 'off', label: '引擎已停止' },
  STARTING: { icon: Loader2, tone: 'starting', label: '引擎启动中' },
  READY_IDLE: { icon: ShieldCheck, tone: 'idle', label: '引擎就绪' },
  BUSY: { icon: Cpu, tone: 'busy', label: '引擎制作中' },
  DEGRADED: { icon: ShieldAlert, tone: 'warn', label: '连接不稳定' },
  RECONCILING: { icon: Activity, tone: 'starting', label: '正在核对恢复' },
  BLOCKED_UNVERIFIED: { icon: ShieldAlert, tone: 'warn', label: '待人工核查' },
  EXTERNAL_CONFLICT: { icon: ShieldAlert, tone: 'warn', label: '端口被外部占用' },
};

type SafeAction = NonNullable<RuntimeSnapshot['recovery']['safe_action']>;

const ACTION_LABELS: Record<string, string> = {
  ensure: '启动引擎',
  stop: '停止引擎',
  probe: '检查连接',
  reconcile: '核对恢复',
};

/** 一次动作结束后给人看的一句话；`tone` 复用状态条既有的语气。 */
type Feedback = { tone: 'idle' | 'warn' | 'down'; text: string };

/** 动作成功时后端 `result.outcome` 的结论（reconcile 的四态是核对专用）。 */
const OUTCOME_TEXTS: Record<string, Record<string, string>> = {
  ensure: {
    STARTED: '引擎已启动。',
    REUSED: '引擎本来就在运行，已直接沿用。',
  },
  stop: {
    STOPPED: '引擎已停止。',
    ALREADY_STOPPED: '引擎本来就已经停止，不用再停。',
  },
  reconcile: {
    NOTHING_TO_RECONCILE: '核对完成：没有需要处理的记录。',
    RECONCILED: '核对完成：已清理失效的引擎记录。',
    NEEDS_REVIEW: '仍需人工核对：有结果未知的任务，已保留原证据。',
    FAILED: '核对失败：读取引擎证据出错。',
  },
  release: {
    RELEASED: '已释放本机占用的制作进程。',
    PARTIAL: '有一部分没能释放，请再点一次核对恢复。',
  },
};

/** 后端没给结论（或结论不在上表里）时的兜底措辞。 */
const ACTION_DEFAULT_TEXTS: Record<string, string> = {
  ensure: '启动请求已经处理。',
  stop: '停止请求已经处理。',
  probe: '已重新检查连接，结果见上方状态。',
  reconcile: '核对完成，详情见上方状态。',
  release: '释放请求已经处理。',
};

/** 拒绝原因里最常见的一条：引擎正在做别的事，现在轮不到这个动作。 */
const REFUSAL_TEXTS: Record<string, string> = {
  IMAGE_RENDERER_BUSY_TASK_IN_FLIGHT: '引擎正在制作，等手头的活做完再操作。',
  RUNTIME_WORK_IN_FLIGHT: '引擎正在制作，等手头的活做完再操作。',
  RUNTIME_OPERATION_IN_PROGRESS: '引擎已有控制操作正在处理。',
  RUNTIME_OPERATION_INTERRUPTED: '上次操作中断，已检查引擎记录，仍需核对。',
  EXTERNAL_INSTANCE_ON_PORT: '引擎端口已被外部程序占用。',
};

/** 把机器码翻成一句人话；翻不出来就不显示码，只给一句通用提示。 */
function reasonText(code: string | null | undefined): string | null {
  if (!code) return null;
  return REFUSAL_TEXTS[code] ?? ERROR_CODE_LABELS[code] ?? null;
}

function operationOutcome(operation: ControlOperation): string | null {
  const outcome = operation.result?.outcome;
  return typeof outcome === 'string' ? outcome : null;
}

/** 本次动作的一句话结论：成功 / 被拒绝 / 待人工核对 / 失败。 */
function describeOperation(action: string, operation: ControlOperation): Feedback {
  const outcome = operationOutcome(operation);
  if (operation.status === 'SUCCEEDED') {
    const text = (outcome && OUTCOME_TEXTS[action]?.[outcome])
      || ACTION_DEFAULT_TEXTS[action]
      || '操作已完成。';
    // 「仍需人工核对」不是成功也不是失败：它要人去看一眼。
    return { tone: action === 'reconcile' && outcome === 'NEEDS_REVIEW' ? 'warn' : 'idle', text };
  }
  if (operation.status === 'REFUSED') {
    const reason = reasonText(operation.failure_code);
    return { tone: 'warn', text: `现在还不能执行这个操作：${reason ?? '请稍后再试。'}` };
  }
  if (operation.status === 'FAILED') {
    const reason = reasonText(operation.failure_code);
    return { tone: 'down', text: `操作没有完成：${reason ?? '请稍后再试一次。'}` };
  }
  // PENDING / RUNNING：后台制作服务还没执行完，不要重复点。
  return { tone: 'warn', text: '操作已提交，正在等待本机后台服务执行。' };
}

/** 运行配置一行：工作流是否还是本机登记的那一套。 */
function workflowText(workflow: string | undefined): string {
  if (workflow === 'VERIFIED') return '工作流已固定';
  if (workflow === 'MISMATCH') return '工作流与配置不一致';
  return '未核对';
}

const POLL_MS = 8000;

export function EngineStatusEntry({ apiOnline }: { apiOnline: boolean }) {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const pendingOperation = useRef<ControlOperation | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  useEffect(() => {
    if (!apiOnline) return;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await fetchRuntimeStatus();
        if (!alive.current) return;
        setSnapshot(next);
        setError(null);
        const pending = pendingOperation.current;
        if (pending) {
          const operation = await fetchRuntimeOperation(pending.operation_id).catch(() => null);
          if (operation && alive.current && pendingOperation.current === pending
              && operation.status !== 'PENDING' && operation.status !== 'RUNNING') {
            pendingOperation.current = null;
            setFeedback(describeOperation(operation.action, operation));
          }
        }
      } catch (exc) {
        if (!alive.current) return;
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (alive.current) timer = window.setTimeout(poll, POLL_MS);
      }
    };
    poll();
    return () => { if (timer !== undefined) window.clearTimeout(timer); };
  }, [apiOnline]);

  const act = useCallback(async (action: SafeAction) => {
    if (!snapshot || busy) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      // 带上页面此刻看到的状态版本：过期就让后端拒绝，而不是按旧世界动手。
      const operation = await submitRuntimeControl(
        action, snapshot.installation_id || 'workbench', snapshot.state_version);
      if (!alive.current) return;
      setFeedback(describeOperation(action, operation));
      pendingOperation.current = operation.status === 'PENDING' || operation.status === 'RUNNING'
        ? operation : null;
      // 结论先说，再刷新快照；其余交给轮询。
      const next = await fetchRuntimeStatus();
      if (!alive.current) return;
      setSnapshot(next);
    } catch (exc) {
      if (!alive.current) return;
      if (isStaleRuntimeState(exc)) {
        // 别把 RUNTIME_STATE_VERSION_STALE 丢给人：先拉最新状态，再请人确认。
        setFeedback({ tone: 'warn', text: '引擎状态已更新，请确认后再操作。' });
        try {
          const next = await fetchRuntimeStatus();
          if (alive.current) setSnapshot(next);
        } catch {
          /* 拉不到就交给轮询，提示已经给过了。 */
        }
      } else {
        const reason = exc instanceof ApiError ? reasonText(exc.code) : null;
        setFeedback(reason
          ? { tone: 'warn', text: reason }
          : { tone: 'down', text: '操作没有提交成功，请稍后再试一次。' });
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  }, [snapshot, busy]);

  if (!apiOnline) return null;
  const state = snapshot?.state ?? 'STOPPED';
  const visual = VISUALS[state] ?? VISUALS.STOPPED;
  const Icon = visual.icon;
  const safeAction = snapshot?.recovery?.safe_action ?? null;
  const actionLabel = safeAction ? ACTION_LABELS[safeAction] : null;
  // 停止是显式操作：只在后端判定安全（safe_action=stop）时才出现。
  const canStop = safeAction === 'stop';
  // 已有控制操作在跑：动手的按钮变成「处理中…」，但只读的检查连接照旧可用。
  const operationInFlight = Boolean(snapshot?.current?.operation_id);
  const actionPending = busy || (operationInFlight && (safeAction !== 'probe' || snapshot?.current?.operation_action === 'probe'));
  const operationLabel = snapshot?.current?.operation_action
    ? (ACTION_LABELS[snapshot.current.operation_action] ?? '引擎控制') : null;

  return (
    <span className="engine-status" aria-live="polite">
      <button
        type="button"
        className={`engine-entry tone-${visual.tone}`}
        title={(snapshot?.recovery?.advice) || '本地制作引擎（ComfyUI）运行状态；点击查看详情。'}
        onClick={() => setDetailOpen((open) => !open)}
      >
        <Icon size={13} aria-hidden="true" />
        <span className="engine-entry-label">{visual.label}</span>
      </button>
      {safeAction && actionLabel && (
        <button
          type="button"
          className={`btn-secondary${canStop && !actionPending ? ' engine-stop' : ''}`}
          disabled={actionPending}
          title={actionPending && operationInFlight
            ? '本机引擎正在执行上一个操作，结束前不能再提交。'
            : (snapshot?.recovery?.advice || actionLabel)}
          onClick={() => act(safeAction)}
        >
          {actionPending ? '处理中…' : actionLabel}
        </button>
      )}
      {feedback && (
        <span className={`engine-feedback tone-${feedback.tone}`} role="status">{feedback.text}</span>
      )}
      {detailOpen && (
        <span className="engine-detail" role="note">
          <span>
            {snapshot?.recovery?.advice || '暂无说明。'}
            {snapshot?.recovery?.reason_code ? `（${snapshot.recovery.reason_code}）` : ''}
          </span>
          {snapshot?.health && (
            <span className="engine-detail-health">
              Worker {snapshot.health.worker === 'UP' ? '在线' : '离线'}
              {' · '}引擎 HTTP {snapshot.health.comfy_http === 'UP' ? '可达' : snapshot.health.comfy_http === 'DOWN' ? '不可达' : '未知'}
              {snapshot.resource && snapshot.resource.active_leases > 0 ? ` · GPU 租约 ×${snapshot.resource.active_leases}` : ''}
            </span>
          )}
          {snapshot?.health && (
            <span className="engine-detail-health">运行配置：{workflowText(snapshot.health.workflow)}</span>
          )}
          {snapshot?.diagnostics && (
            <span className="engine-detail-health">
              归属：{snapshot.diagnostics.ownership === 'VERIFIED' ? '已核实' :
                snapshot.diagnostics.ownership === 'EXTERNAL' ? '外部实例，不会接管' : '未核实'}
              {' · '}H3 生产：尚需单独核验
              {snapshot.diagnostics.missing_nodes.length > 0
                ? ` · 缺少节点：${snapshot.diagnostics.missing_nodes.join('、')}` : ''}
            </span>
          )}
          <span className="engine-detail-health">启动引擎用于图片制作；H3 开拍由生产任务另行启动和核验。</span>
          {operationLabel && (
            <span className="engine-detail-health">
              当前操作：{operationLabel}
              {snapshot?.current?.operation_status === 'RUNNING' ? '（执行中）' : '（等待执行）'}
            </span>
          )}
          {error && <span className="engine-detail-error" role="alert">{error}</span>}
        </span>
      )}
    </span>
  );
}
