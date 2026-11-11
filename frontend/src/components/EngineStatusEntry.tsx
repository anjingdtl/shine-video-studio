import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, Cpu, Loader2, PlugZap, ShieldAlert, ShieldCheck, Square } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { fetchRuntimeStatus, submitRuntimeControl, type RuntimeSnapshot } from '../api';

/** 顶部常驻的「本地制作引擎」状态条（统一运行调度控制面）。
 *
 * 唯一数据来源是后端 /runtime-orchestrator/status 的派生快照；这里只做
 * 展示与发起安全动作（启动/停止/检查连接/核对恢复），绝不自行推断
 * ComfyUI 是否在线，也绝不在浏览器里决定进程的生死。 */

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

const ACTION_LABELS: Record<string, string> = {
  ensure: '启动引擎',
  stop: '停止引擎',
  probe: '检查连接',
  reconcile: '核对恢复',
};

const POLL_MS = 8000;

export function EngineStatusEntry({ apiOnline }: { apiOnline: boolean }) {
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  const act = useCallback(async (action: NonNullable<RuntimeSnapshot['recovery']['safe_action']>) => {
    if (!snapshot || busy) return;
    setBusy(true);
    setError(null);
    try {
      await submitRuntimeControl(action, snapshot.installation_id || 'workbench');
      // 立即刷新一次，其余交给轮询。
      setSnapshot(await fetchRuntimeStatus());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
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
          className={`btn-secondary${canStop ? ' engine-stop' : ''}`}
          disabled={busy}
          title={snapshot?.recovery?.advice || actionLabel}
          onClick={() => act(safeAction)}
        >
          {busy ? '处理中…' : actionLabel}
        </button>
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
          {error && <span className="engine-detail-error" role="alert">{error}</span>}
        </span>
      )}
    </span>
  );
}
