import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, CircleStop, Loader2, RefreshCw } from 'lucide-react';
import { api, json, lifecycleStatus, startWorker, stopStack, stopWorker, type LifecycleStatus } from '../api';

type WorkStack = {
  id: string;
  kind: string;
  label: string;
  state: string;
  state_label: string;
  working: boolean;
  port: number | null;
  pid: number | null;
  lease_status: string | null;
  job_id: string | null;
  job_status: string | null;
  project_id: string | null;
  project_title: string | null;
  in_flight: boolean;
  historical: boolean;
  closeable: boolean;
  close_blocked_reason: string | null;
};

type IdleBlocker = {
  kind: string;
  id: string;
  status?: string | null;
  label: string;
};

type WorkStackSnapshot = {
  checked_at: string;
  stacks: WorkStack[];
  workers: Array<{ worker_id: string; online: boolean; phase: string | null; current_job_id: string | null; project_title: string | null }>;
  jobs: Array<{ job_id: string; project_title: string | null; status: string }>;
  idle_blockers?: IdleBlocker[] | null;
  idle_blockers_unreadable?: boolean;
  counts: { stacks: number; working_stacks: number; historical_stacks?: number; workers_online: number; running_jobs: number; idle_blockers?: number };
  process_error: string | null;
};

const STATE_TONE: Record<string, string> = {
  RUNNING: 'on', GONE: 'stale', STOPPED: 'off', UNVERIFIED: 'unknown',
};

/**
 * 左侧常驻的工作栈监控：这台机器上所有后台单元（渲染器、审片服务、Worker）。
 *
 * 不局限于当前作品：另一个作品拉起的渲染器同样占着显存，正是需要看见的东西。
 * 列表内部滚动，历史行不会把整页越撑越长；关闭走与「关闭本机后台」同一条
 * 证据链，正在出片的进程会被拒绝而不是被强行中断。
 */
export function WorkStackMonitor() {
  const [snapshot, setSnapshot] = useState<WorkStackSnapshot | null>(null);
  const [lifecycle, setLifecycle] = useState<LifecycleStatus | null>(null);
  const [confirmExit, setConfirmExit] = useState(false);
  const [workerBusy, setWorkerBusy] = useState(false);
  const [busyId, setBusyId] = useState('');
  const [confirmId, setConfirmId] = useState('');
  const [message, setMessage] = useState('');
  const [open, setOpen] = useState(true);
  const [showHistory, setShowHistory] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const value = await api<WorkStackSnapshot>('/setup/work-stacks');
      if (alive.current) setSnapshot(value);
    } catch {
      /* 后端未就绪时保持上一次快照，不刷错误 */
    }
    try {
      const value = await lifecycleStatus();
      if (alive.current) setLifecycle(value);
    } catch {
      /* 监督进程不在时保持上一次状态 */
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void load();
    const timer = window.setInterval(() => { void load(); }, 5000);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [load]);

  async function close(stack: WorkStack) {
    if (!stack.closeable) {
      setMessage(stack.close_blocked_reason ?? '这个工作栈现在不能关闭。');
      return;
    }
    // 行内二次确认：原生 confirm 会被浏览器自动化吞掉，也挡不住误点。
    if (confirmId !== stack.id) {
      setConfirmId(stack.id);
      setMessage(`关闭「${stack.label}${stack.project_title ? ' · ' + stack.project_title : ''}」？正在出片的后台不会被中断。`);
      return;
    }
    setBusyId(stack.id);
    setConfirmId('');
    setMessage('');
    try {
      const report = await api<{ stopped?: unknown[]; refused?: unknown[] }>(
        '/setup/managed-processes/release', json('POST', { target_id: stack.id }),
      );
      const stopped = report?.stopped?.length ?? 0;
      const refused = report?.refused?.length ?? 0;
      setMessage(stopped ? '已关闭并释放显存。' : refused ? '没能关闭：进程仍在出片或归属不明。' : '这个工作栈已经不在运行。');
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusyId('');
      setConfirmId('');
    }
  }

  const stacks = snapshot?.stacks ?? [];
  const live = stacks.filter((row) => !row.historical);
  const history = stacks.filter((row) => row.historical);
  const visible = showHistory ? [...live, ...history] : live;
  const working = stacks.filter((row) => row.working).length;
  const counts = snapshot?.counts;

  return <section className={`stack-monitor${open ? ' open' : ''}`} aria-label="工作栈监控">
    <button type="button" className="stack-monitor-head" onClick={() => setOpen((value) => !value)}
      aria-expanded={open}>
      <Activity size={13} />
      <span>工作栈</span>
      <em className={working ? 'busy' : ''}>{working ? `${working} 个在跑` : '空闲'}</em>
      <i className="stack-monitor-caret" aria-hidden="true">{open ? '−' : '+'}</i>
    </button>
    {open && <div className="stack-monitor-body">
      <div className="stack-monitor-meta">
        <span>Worker {counts?.workers_online ?? 0} 在线</span>
        <span>出片中 {counts?.running_jobs ?? 0}</span>
        <span>{lifecycle
          ? (lifecycle.idle_shutdown_minutes > 0
            ? `关页空闲 ${lifecycle.idle_shutdown_minutes} 分钟后自动退出`
            : '自动退出已关闭')
          : '生命周期未知'}</span>
        <button type="button" className="stack-monitor-refresh" title="刷新工作栈" onClick={() => void load()}>
          <RefreshCw size={11} />
        </button>
      </div>
      <div className="stack-monitor-meta">
        <button type="button" className="ghost-mini" disabled={workerBusy}
          title="服务复核验需要「API 活着 + Worker 停止」；验证完成后记得启动恢复"
          onClick={() => void (async () => {
            setWorkerBusy(true);
            setMessage('');
            try {
              const result = lifecycle?.worker === 'running' ? await stopWorker() : await startWorker();
              setMessage(result.code
                ? `Worker 操作被拒绝：${result.detail || result.code}`
                : (lifecycle?.worker === 'running' ? 'Worker 已停止；心跳约 30 秒后过期，可继续服务核验。' : 'Worker 已启动。'));
              await load();
            } catch (error) {
              setMessage(error instanceof Error ? error.message : String(error));
            } finally {
              setWorkerBusy(false);
            }
          })()}>
          {workerBusy ? '…' : lifecycle?.worker === 'running' ? '停止 Worker' : '启动 Worker'}
        </button>
        <button type="button" className="ghost-mini"
          onClick={() => void (async () => {
            if (!confirmExit) {
              setConfirmExit(true);
              setMessage('停止整个后台（API/Worker/Web）并释放显存？有活任务时会被拒绝，不会打断在途渲染。');
              return;
            }
            setConfirmExit(false);
            setMessage('');
            try {
              const result = await stopStack();
              setMessage(result.code === 'STACK_BUSY'
                ? `现在还不能停止：${(result.blockers ?? []).join('；')}`
                : '后台正在停止…停止后本页将不可用，再次使用请双击桌面快捷方式。');
            } catch (error) {
              setMessage(error instanceof Error ? error.message : String(error));
            }
          })()}>
          {confirmExit ? '确认退出' : '停止后台并退出'}
        </button>
        {confirmExit && <button type="button" className="ghost-mini" onClick={() => { setConfirmExit(false); setMessage(''); }}>取消</button>}
      </div>
      <div className="stack-monitor-list" role="log" aria-live="polite">
        {visible.length === 0 && !snapshot?.idle_blockers?.length && !snapshot?.idle_blockers_unreadable && <p className="stack-monitor-empty">当前没有后台进程在跑。</p>}
        {visible.length === 0 && snapshot?.idle_blockers_unreadable && (
          <p className="stack-monitor-empty">无活动渲染进程；配置等待项暂时读取失败，不能据此判断为空。</p>
        )}
        {visible.length === 0 && Boolean(snapshot?.idle_blockers?.length) && (
          <p className="stack-monitor-empty">
            无活动渲染进程；配置等待项：{snapshot!.idle_blockers!.slice(0, 2).map((row) => row.label).join('；')}
          </p>
        )}
        {visible.map((stack) => <div className={`stack-row ${STATE_TONE[stack.state] ?? 'unknown'}`} key={stack.id}>
          <span className="stack-dot" aria-hidden="true" />
          <div className="stack-row-text">
            <b>{stack.label}</b>
            <span>
              {stack.project_title ? `${stack.project_title} · ` : '未绑定作品 · '}
              {stack.state_label}
              {stack.job_status ? ` · 镜头 ${stack.job_status}` : ''}
              {stack.port ? ` · :${stack.port}` : ''}
            </span>
          </div>
          <button type="button" className="stack-close" disabled={busyId === stack.id || !stack.closeable}
            title={stack.close_blocked_reason ?? '关闭这个工作栈并释放显存'}
            onClick={() => void close(stack)}>
            {busyId === stack.id ? <Loader2 size={11} className="spin" /> : <CircleStop size={11} />}
          </button>
        </div>)}
      </div>
      {confirmId && <div className="stack-monitor-confirm" role="group" aria-label="确认关闭工作栈">
        <span>只关闭本工作台拥有的进程，正在出片的后台会被拒绝。</span>
        <button type="button" className="primary-mini"
          onClick={() => { const row = stacks.find((item) => item.id === confirmId); if (row) void close(row); }}>
          确认关闭
        </button>
        <button type="button" className="ghost-mini" onClick={() => { setConfirmId(''); setMessage(''); }}>取消</button>
      </div>}
      {history.length > 0 && <button type="button" className="stack-monitor-history" onClick={() => setShowHistory((value) => !value)}>
        {showHistory ? '收起历史记录' : `历史记录 ${history.length} 条`}
      </button>}
      {message && <p className="stack-monitor-message" role="status">{message}</p>}
    </div>}
  </section>;
}

export default WorkStackMonitor;
