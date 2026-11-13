import { useEffect, useRef, useState } from 'react';
import { api, fetchRuntimeStatus, type ProductionStrategy } from '../api';
import { approveAndExecuteSetupPlan, fetchInstallationAttempt, isQueuedInstallation,
  pauseInstallationAttempt, resumeInstallationAttempt, type InstallationAttemptView, type SetupPlan } from '../setupExecution';

type ResourceReport = {
  components: Array<{ component_id: string; size_bytes: number; state: string; license_id: string;
    license_url: string; files: Array<{ url: string; sha256: string; size: number }> }>;
  missing_download_bytes?: number;
  snapshot_sha256?: string;
  download_eligible: boolean;
  managed_installations?: Array<{ id: string }>;
  blockers: Array<{ code: string }>;
};
type Plan = SetupPlan & { download_bytes: number; disk_required: number };

const post = <T,>(path: string, body: unknown) => api<T>(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
const gib = (size: number) => `${(size / 1024 ** 3).toFixed(2)} GiB`;

/** All execution still goes through the existing one-use grant and installer. */
export function StrategyResourceCard({ projectId, strategy }: { projectId: string; strategy: ProductionStrategy }) {
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const check = () => { if (!alive.current) throw new Error('选择已改变，操作已停止。'); };
  const [report, setReport] = useState<ResourceReport | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [source, setSource] = useState('AUTO');
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState('');
  const attemptKey = `shine-resource-attempt:${projectId}:${JSON.stringify(strategy)}`;
  const [attemptId, setAttemptId] = useState(() => localStorage.getItem(attemptKey));
  const [attempt, setAttempt] = useState<InstallationAttemptView | null>(null);
  useEffect(() => {
    if (!attemptId) return;
    let active = true;
    const poll = () => { void fetchInstallationAttempt(attemptId).then(value => {
      if (active) setAttempt(value);
    }).catch(() => { if (active) setMessage('暂时无法读取准备进度，请稍后核对。'); }); };
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [attemptId]);

  const inspect = async () => {
    setWorking(true); setMessage(''); setPlan(null);
    try {
      const result = await post<ResourceReport>('/setup/resources/resolve', { project_id: projectId, strategy });
      check(); setReport(result);
      if (!result.download_eligible || !result.snapshot_sha256) return;
      const [runtime, storage] = await Promise.all([
        fetchRuntimeStatus(), api<{ free_bytes: number }>('/setup/storage'),
      ]);
      check();
      const candidates = result.managed_installations ?? [];
      const installationId = runtime.installation_id || (candidates.length === 1 ? candidates[0].id : '');
      if (!installationId) throw new Error('请先在基础配置中登记或选定本机受管安装实例。');
      const created = await post<Plan>('/setup/resources/install-plan', {
        project_id: projectId, strategy, snapshot_sha256: result.snapshot_sha256,
        instance_id: installationId, disk_free: storage.free_bytes, source_preference: source,
      });
      check(); setPlan(created);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setWorking(false); }
  };
  const confirm = async () => {
    if (!plan || working) return;
    setWorking(true); setMessage('');
    try {
      const result = await approveAndExecuteSetupPlan(plan, check, { wait: false });
      if (isQueuedInstallation(result)) {
        localStorage.setItem(attemptKey, result.attempt_id); setAttemptId(result.attempt_id);
      }
      setMessage('已交给本机后台准备；文件完成后仍需试镜验证。');
      setPlan(null);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { setWorking(false); }
  };
  return <div className="strategy-resource-card">
    <h3>模型资源检测</h3>
    <p>先核对当前选择需要的文件；候选模型下载完成后仍不能直接开拍。</p>
    <label>下载来源 <select value={source} disabled={working} onChange={event => {
      setSource(event.target.value); setPlan(null);
    }}>
      <option value="AUTO">自动选择</option><option value="MIRROR_FIRST">国内可信镜像优先</option>
      <option value="OFFICIAL_FIRST">国际官方源优先</option>
    </select></label>{' '}
    <button type="button" className="btn-secondary" disabled={working} onClick={() => void inspect()}>
      {working ? '正在核对…' : '检测所需资源'}
    </button>
    {report && <div>
      {report.blockers.map((item, index) => <p key={`${item.code}-${index}`}>暂不能准备：{item.code}</p>)}
      {report.components.map(item => <p key={item.component_id}>
        {item.component_id} · {gib(item.size_bytes)} · {item.state === 'MISSING' ? '待下载' : '已校验'} ·{' '}
        <a href={item.license_url} target="_blank" rel="noreferrer">{item.license_id}</a>
        {item.files.map(file => <span key={file.sha256}> · <a href={file.url} target="_blank" rel="noreferrer">固定官方来源</a></span>)}
      </p>)}
      <p>预计下载 {gib(report.missing_download_bytes ?? 0)}；节点兼容性和本机试镜尚未核验。</p>
    </div>}
    {plan && <div className="required-action">
      <p>本次计划下载 {gib(plan.download_bytes)}，需预留 {gib(plan.disk_required)} 磁盘空间。
        来源为上方选择的固定源或已审镜像，文件将按 SHA-256 校验。</p>
      <button type="button" className="must-button" disabled={working} onClick={() => void confirm()}>
        确认并下载上述资源
      </button>
    </div>}
    {attempt && <div role="status">
      <p>准备状态：{attempt.stage} · {gib(attempt.confirmed_bytes)} / {gib(attempt.total_bytes)}</p>
      {attempt.current_component && <p>正在处理：{attempt.current_component}</p>}
      {attempt.error_code && <p>需要处理：{attempt.error_code}</p>}
      {!attempt.terminal && attempt.stage !== 'PAUSED' && <button type="button" disabled={working}
        onClick={() => { void pauseInstallationAttempt(attempt.attempt_id).then(setAttempt); }}>暂停</button>}
      {attempt.resumable && <button type="button" disabled={working}
        onClick={() => { void resumeInstallationAttempt(attempt.attempt_id).then(setAttempt); }}>继续</button>}
    </div>}
    {message && <p role="status">{message}</p>}
  </div>;
}
