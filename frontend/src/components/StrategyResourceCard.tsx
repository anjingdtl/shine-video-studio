import { useEffect, useRef, useState } from 'react';
import { api, ERROR_CODE_LABELS, fetchRuntimeStatus, type ProductionStrategy } from '../api';
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
type NodeReport = { state: string; missing_nodes?: string[];
  missing_inputs?: Array<{ class_type: string; input: string }> };
type LocalInventory = { snapshot_sha256: string; candidate_bytes: number;
  candidates: Array<{ component_id: string; size_bytes: number; state: string }> };

const post = <T,>(path: string, body: unknown) => api<T>(path, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
const gib = (size: number) => `${(size / 1024 ** 3).toFixed(2)} GiB`;
const resourceName = (id: string) => ({
  'minimax-h3-diffusion-w6a8': '视频模型',
  'minimax-h3-text-encoder-nvfp4': '文字理解模型',
  'minimax-h3-vae-int8': '画面解码模型',
  'minimax-h3-audio-vae-fp32': '声音解码模型',
} as Record<string, string>)[id] ?? id;
const blockerText = (code: string) => ({
  VARIANT_RECIPE_UNVERIFIED: '此候选模型目前只登记了原生 20 步单次采样，请调整画面加速和二次精修。',
  RESOURCE_PIN_MISSING: '所需资源的固定来源或校验信息不完整，暂不能准备。',
  VARIANT_CATALOG_MISMATCH: '模型清单发生变化，请刷新后重新检测。',
  RELAYVSR_WEIGHTS_NOT_PINNED: '高清视频模型尚未完成来源和校验登记。',
} as Record<string, string>)[code] ?? '当前方案还缺少已核实的资源，请到基础配置检查。';
const stageName = (stage: string) => ({
  QUEUED: '等待后台处理', DOWNLOADING: '下载中', VERIFYING: '校验中', INSTALLING: '准备中',
  FILES_VERIFIED_PENDING_RUNTIME: '文件已校验，等待试镜', PAUSED: '已暂停',
  FAILED_RETRYABLE: '暂时中断，可继续', NEEDS_REVIEW: '需要核对',
} as Record<string, string>)[stage] ?? '正在处理';

/** All execution still goes through the existing one-use grant and installer. */
export function StrategyResourceCard({ projectId, strategy }: { projectId: string; strategy: ProductionStrategy }) {
  const alive = useRef(true);
  const actionInFlight = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const check = () => { if (!alive.current) throw new Error('选择已改变，操作已停止。'); };
  const [report, setReport] = useState<ResourceReport | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [inventory, setInventory] = useState<LocalInventory | null>(null);
  const [instanceId, setInstanceId] = useState('');
  const [nodeReport, setNodeReport] = useState<NodeReport | null>(null);
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
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setWorking(true); setMessage(''); setPlan(null); setInventory(null);
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
      // 阶段 A（离线整备）不要求已有受管 ComfyUI 实例：新装机可以先下载并校验
      // 权重，实例绑定（阶段 B）等 ComfyUI 装好后再走既有激活流程。
      setInstanceId(installationId);
      if (installationId) {
        try {
          const local = await post<LocalInventory>('/setup/resources/inspect-local', {
            project_id: projectId, strategy, instance_id: installationId,
          });
          check(); setInventory(local);
        } catch {
          check(); setMessage('暂时无法扫描受管模型目录，仍可查看下载计划。');
        }
      }
      const created = await post<Plan>('/setup/resources/install-plan', {
        project_id: projectId, strategy, snapshot_sha256: result.snapshot_sha256,
        instance_id: installationId || undefined, disk_free: storage.free_bytes, source_preference: source,
      });
      check(); setPlan(created);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { actionInFlight.current = false; if (alive.current) setWorking(false); }
  };
  const checkNodes = async () => {
    if (!instanceId || actionInFlight.current) return;
    actionInFlight.current = true; setWorking(true); setMessage('');
    try {
      const result = await post<NodeReport>('/setup/resources/live-check', {
        project_id: projectId, strategy, instance_id: instanceId,
      });
      check(); setNodeReport(result);
    } catch (error) { if (alive.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { actionInFlight.current = false; if (alive.current) setWorking(false); }
  };
  const reuseLocal = async () => {
    if (!inventory || !instanceId || working || actionInFlight.current) return;
    actionInFlight.current = true;
    setWorking(true); setMessage(''); setPlan(null);
    try {
      const result = await post<{ reused_bytes: number; rejected: string[] }>('/setup/resources/import-local', {
        project_id: projectId, strategy, instance_id: instanceId, snapshot_sha256: inventory.snapshot_sha256,
      });
      check();
      actionInFlight.current = false;
      await inspect();
      check();
      setMessage(`已校验并复用 ${gib(result.reused_bytes)}；${result.rejected.length} 项未通过校验。`);
    } catch (error) { if (alive.current) setMessage(error instanceof Error ? error.message : String(error)); }
    finally { actionInFlight.current = false; if (alive.current) setWorking(false); }
  };
  const confirm = async () => {
    if (!plan || working || actionInFlight.current) return;
    actionInFlight.current = true;
    setWorking(true); setMessage('');
    try {
      const result = await approveAndExecuteSetupPlan(plan, check, { wait: false });
      if (isQueuedInstallation(result)) {
        localStorage.setItem(attemptKey, result.attempt_id); setAttemptId(result.attempt_id);
      }
      setMessage('已交给本机后台准备；文件完成后仍需试镜验证。');
      setPlan(null);
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
    finally { actionInFlight.current = false; if (alive.current) setWorking(false); }
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
      {report.blockers.map((item, index) => <p key={`${item.code}-${index}`}>{blockerText(item.code)}</p>)}
      {report.components.map(item => <p key={item.component_id}>
        {resourceName(item.component_id)} · {gib(item.size_bytes)} · {item.state === 'MISSING' ? '待下载' : '已校验'} ·{' '}
        <a href={item.license_url} target="_blank" rel="noreferrer">{item.license_id}</a>
        {item.files.map(file => <span key={file.sha256}> · <a href={file.url} target="_blank" rel="noreferrer">固定官方来源</a></span>)}
      </p>)}
      <p>预计下载 {gib(report.missing_download_bytes ?? 0)}；节点兼容性和本机试镜尚未核验。</p>
    </div>}
    {instanceId && <button type="button" className="btn-secondary" disabled={working}
      onClick={() => void checkNodes()}>检查已启动引擎的节点</button>}
    {nodeReport && <p role="status">
      {nodeReport.state === 'NODE_INPUTS_MISSING'
        ? `缺少节点或输入：${[...(nodeReport.missing_nodes ?? []),
            ...(nodeReport.missing_inputs ?? []).map(item => `${item.class_type}.${item.input}`)].join('、')}`
        : nodeReport.state === 'NODE_INPUTS_PRESENT'
          ? '单图样例的节点及输入已找到；显卡兼容性和真实试镜仍需核验。'
          : '节点暂未核验：请先启动并核对受管引擎；系统不会接管外部服务。'}
    </p>}
    {inventory && inventory.candidates.length > 0 && <div className="notice">
      <p>本机受管目录发现 {inventory.candidates.length} 个大小相符的候选文件，
        最多可复用 {gib(inventory.candidate_bytes)}；确认后会复制并核对 SHA-256，源文件不会修改。</p>
      <button type="button" className="btn-secondary" disabled={working} onClick={() => void reuseLocal()}>
        校验并复用本机文件
      </button>
    </div>}
    {plan && <div className="required-action">
      <p>本次计划下载 {gib(plan.download_bytes)}，需预留 {gib(plan.disk_required)} 磁盘空间。
        来源为上方选择的固定源或已审镜像，文件将按 SHA-256 校验。</p>
      {!instanceId && <p>当前还没有受管 ComfyUI 实例：这一步只做离线下载与校验（阶段 A）；
        装好 ComfyUI 后再把已校验的资源绑定到受管实例并核对节点（阶段 B），不会凭空启动渲染器。</p>}
      <button type="button" className="must-button" disabled={working} onClick={() => void confirm()}>
        确认并下载上述资源
      </button>
    </div>}
    {attempt && <div role="status">
      <p>准备状态：{stageName(attempt.stage)} · {gib(attempt.confirmed_bytes)} / {gib(attempt.total_bytes)}</p>
      {attempt.current_component && <p>正在处理：{resourceName(attempt.current_component)}</p>}
      {attempt.error_code && <p>需要处理：{ERROR_CODE_LABELS[attempt.error_code] ?? '请到基础配置核对准备任务。'}</p>}
      {!attempt.terminal && attempt.stage !== 'PAUSED' && <button type="button" disabled={working}
        onClick={() => { void pauseInstallationAttempt(attempt.attempt_id).then(setAttempt); }}>暂停</button>}
      {attempt.resumable && <button type="button" disabled={working}
        onClick={() => { void resumeInstallationAttempt(attempt.attempt_id).then(setAttempt); }}>继续</button>}
    </div>}
    {message && <p role="status">{message}</p>}
  </div>;
}
