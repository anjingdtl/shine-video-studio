import { Sparkles } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { label, prepRowLabels, providerLabels, serviceStatusLabels } from '../constants';
import type { DeploymentStorage } from './StorageSettings';

type ToolInfo = { present: boolean; version: string; at_floor: boolean };
type ExistingScan = { root: string; snapshot: string; candidate_bytes: number; scanned_files: number;
  matches: Array<{ component_id: string; relative_path: string; size: number }>;
  missing: Array<{ component_id: string; relative_path: string; size: number }>; skipped: string[] };
type EnvReport = {
  platform: Record<string, string>;
  tools: Record<string, ToolInfo>;
  gpu: Record<string, unknown>;
  ram: { status: string; ram_mb?: number; at_floor?: boolean };
  disk: { status: string; free_gb: number; at_floor: boolean };
  port: { status: string; port: number };
  data_dir: { path: string; writable: boolean };
  deployment_dir?: { path: string; writable: boolean };
  blockers: Array<{ code: string; tool?: string; detail?: string }>;
  bootstrap: string;
};
type CatalogComponent = {
  component_id: string; kind: string; revision: string; license_id: string; usage: string;
  license_url: string; destination_family: string | null; role: string | null;
  files: number; bytes: number; status: string; blocked_reason: string;
};
type CatalogSummary = {
  catalog_id: string; catalog_version: string; generated: string;
  platform: Record<string, unknown>; total_bytes: number; components: CatalogComponent[];
};
type PlanRef = { plan_id: string; sha256: string; kind: string; action?: string; tier?: string; status: string };
type UpstreamRow = { id: string; label: string; host: string; status: string;
  http_status: number | null; error: string | null; needed_by: string };
type Connectivity = { status: string; checked_at: string; blocked: string[]; degraded: string[];
  upstreams: UpstreamRow[]; guidance: string[] };
type Readiness = { catalog: { id: string; version: string }; layers: Array<Record<string, unknown>>; production_gates: Record<string, boolean>; human_steps: string[] };
type Grant = { grant_id: string; snapshot: string };
type RuntimeState = { status: string; render_provider: string; error: string | null;
  candidate: { project_id: string; recipe_sha256: string } | null;
  services_verified?: boolean;
  service_validation?: { status: string; blockers?: string[]; validated_at?: string | null;
    node_count?: number | null; workflow_profiles?: string[]; review_models?: Record<string, string> } | null;
};

const TOOL_LABELS: Record<string, string> = {
  uv: 'uv', node: 'Node.js', npm: 'npm', pwsh: 'PowerShell 7', ffmpeg: 'FFmpeg', ffprobe: 'FFprobe',
};
const RUNTIME_LABELS: Record<string, string> = {
  ACTIVE: '已激活', NOT_ACTIVATED: '待确认', INVALID_REQUIRES_REVIEW: '配置已变更，需重新核验',
};

function blockerText(blocker: EnvReport['blockers'][number]): string {
  const tool = TOOL_LABELS[blocker.tool ?? ''] ?? blocker.tool ?? '';
  const messages: Record<string, string> = {
    TOOL_MISSING_INSTALLABLE: `${tool} 尚未安装`, TOOL_STALE: `${tool} 版本需要更新`,
    GPU_VRAM_BELOW_FLOOR: '显卡可用显存不足', RAM_BELOW_FLOOR: '本机内存不足或无法读取',
    DISK_BELOW_FLOOR: '安装目录空间不足', PORT_OCCUPIED: '服务端口被占用',
    DATA_DIR_NOT_WRITABLE: '项目数据目录无法写入', DEPLOYMENT_DIR_NOT_WRITABLE: '安装目录无法写入',
    NVIDIA_SMI_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
    NVIDIA_DRIVER_UNAVAILABLE: '无法检测 NVIDIA 显卡，请检查显卡驱动',
    NVIDIA_SMI_OUTPUT_UNPARSEABLE: '显卡检测结果无法读取，请检查显卡驱动',
  };
  return messages[blocker.code] ?? `环境检测未通过（${blocker.code}），请查看检测详情`;
}

function fmtBytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB'];
  let result = value;
  let index = 0;
  while (result >= 1024 && index < 3) { result /= 1024; index += 1; }
  return `${result.toFixed(result >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function SetupPanel({ projectId, onRefresh }: { projectId: string; onRefresh: () => Promise<void> }) {
  const [checked, setChecked] = useState(false);
  const [progress, setProgress] = useState('');
  const [existingRoots, setExistingRoots] = useState<string[]>([]);
  const [existingPath, setExistingPath] = useState('');
  const [existingScan, setExistingScan] = useState<ExistingScan | null>(null);
  const [existingResolved, setExistingResolved] = useState(false);
  const [existingChoice, setExistingChoice] = useState<'reuse' | 'new'>('reuse');
  const [importResult, setImportResult] = useState('');
  const [env, setEnv] = useState<EnvReport | null>(null);
  const [connectivity, setConnectivity] = useState<Connectivity | null>(null);
  const [storage, setStorage] = useState<DeploymentStorage | null>(null);
  const [catalog, setCatalog] = useState<CatalogSummary | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [plans, setPlans] = useState<PlanRef[]>([]);
  const [runtime, setRuntime] = useState<RuntimeState | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'bad'; text: string } | null>(null);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const withJson = useCallback((method: string, body: unknown): RequestInit => {
    return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  }, []);

  const say = useCallback((text: string, kind: 'ok' | 'bad' = 'ok') => {
    setNotice({ kind, text });
    window.setTimeout(() => setNotice(null), 6000);
  }, []);

  async function run(action: () => Promise<string>) {
    setBusy(true);
    try { say(await action()); } catch (error) { say((error as Error).message, 'bad'); }
    finally { setBusy(false); }
  }

  const loadEnvironment = useCallback(async () => {
    try { setEnv(await api<EnvReport>('/setup/environment')); } catch { /* loopback probe stays silent */ }
  }, []);

  function checkConnectivity() {
    void run(async () => {
      const result = await api<Connectivity>('/setup/connectivity');
      setConnectivity(result);
      if (result.blocked.length > 0) return `上游不可达：${result.blocked.join('、')}。先解决代理/VPN 再下载。`;
      return `上游检查通过：${result.status} · ${result.upstreams.map((row) => row.host).join(' / ')}`;
    });
  }
  const loadCatalog = useCallback(async () => {
    try { setCatalog(await api<CatalogSummary>('/setup/catalog')); } catch { /* keep prior state */ }
  }, []);
  const loadReadiness = useCallback(async () => {
    try { setReadiness(await api<Readiness>('/setup/readiness')); } catch { /* keep prior state */ }
  }, []);
  const loadRuntime = useCallback(async () => {
    try { setRuntime(await api<RuntimeState>('/setup/runtime')); }
    catch (error) { say(String(error), 'bad'); }
  }, [say]);
  const loadStorage = useCallback(async () => {
    try { setStorage(await api<DeploymentStorage>('/setup/storage')); }
    catch (error) { say(String(error), 'bad'); }
  }, [say]);
  const loadPlans = useCallback(async () => {
    const selected = projectId;
    if (!selected) { setPlans([]); return; }
    const rows = await api<Array<{ id: string; sha256: string; status: string;
      payload: { kind?: string; deployment?: unknown; operation?: { action?: string } } }>>(
      `/comfyops/install-plans?project_id=${selected}`);
    if (projectRef.current !== selected) return;
    setPlans(rows.filter(row => row.payload.deployment || row.payload.kind === 'INSTANCE_LIFECYCLE')
      .map(row => ({ plan_id: row.id, sha256: row.sha256, status: row.status,
        kind: row.payload.kind ?? 'INSTALL', action: row.payload.operation?.action })));
  }, [projectId]);
  useEffect(() => { setPlans([]); void loadPlans().catch(error => say(String(error), 'bad')); }, [loadPlans, say]);

  useEffect(() => { void loadEnvironment(); void loadCatalog(); void loadReadiness(); void loadRuntime(); },
    [loadEnvironment, loadCatalog, loadReadiness, loadRuntime]);
  useEffect(() => {
    void loadStorage();
    const refresh = () => {
      void loadStorage(); void loadEnvironment(); void loadReadiness();
      void loadPlans().catch(error => say(String(error), 'bad'));
    };
    window.addEventListener('shine-storage-changed', refresh);
    return () => window.removeEventListener('shine-storage-changed', refresh);
  }, [loadStorage, loadPlans, loadEnvironment, loadReadiness, say]);

  async function approveAndExecute(plan: PlanRef, label: string): Promise<void> {
    let approved = { sha256: plan.sha256 };
    if (plan.status === 'AWAITING_APPROVAL') {
      const grant = await api<Grant>('/preparation/approval-grants',
        withJson('POST', { action: 'install', target_id: plan.plan_id, context: null }));
      approved = await api<{ sha256: string }>(`/comfyops/install-plans/${plan.plan_id}/approve`,
        withJson('POST', { grant_id: grant.grant_id, snapshot: grant.snapshot, evidence: 'Setup 向导内人工批准' }));
    } else if (plan.status !== 'APPROVED') {
      throw new Error('该计划已执行或需要对账，请查看原始尝试记录。');
    }
    const exec = await api<Record<string, unknown>>(`/comfyops/${plan.kind === 'INSTANCE_LIFECYCLE' ? 'instance-plans' : 'install-plans'}/${plan.plan_id}/execute`,
      { ...withJson('POST', plan.kind === 'INSTANCE_LIFECYCLE' ? { plan_hash: approved.sha256 } : { plan_hash: approved.sha256, executor: 'local' }), signal: AbortSignal.timeout(600000_000) });
    if (!['FILES_VERIFIED', 'COMPLETED'].includes(String(exec.status))) {
      throw new Error(`执行停在 ${String(exec.status)}，请展开安装详情检查；没有自动重试。`);
    }
    say(`${label} 执行完成：${JSON.stringify(exec).slice(0, 120)}`);
  }

  function createInstallPlan() {
    void run(async () => {
      const core = ['comfyui', 'comfy-runtime-wheels', 'minimax-h3-diffusion', 'minimax-h3-text-encoder',
        'minimax-h3-vae', 'minimax-h3-lora', 'minimax-h3-pdd',
        'qwen3-vl-8b-fp8', 'qwen3-asr-0.6b', 'ComfyUI-KJNodes', 'comfyui-videohelpersuite',
        'ComfyUI-MiniMax-H3-PDD-Acc', 'ComfyUI-SolAttn_triton', 'Comfyui_Minimax_h3_latent_Upscaler'];
      const result = await api<{ plan_id: string; sha256: string; download_bytes: number; disk_required: number }>(
        '/setup/install-plan', withJson('POST', { components: core, project_id: projectRef.current, disk_free: storage?.free_bytes ?? 0 }));
      setPlans(previous => [...previous, { ...result, kind: 'INSTALL', status: 'AWAITING_APPROVAL' }]);
      return `安装计划 ${result.plan_id} 已创建（下载 ${fmtBytes(result.download_bytes)}，预留 ${fmtBytes(result.disk_required)}）`;
    });
  }

  function createInstanceChain() {
    void run(async () => {
      const result = await api<PlanRef & { complete: boolean }>('/setup/next-instance-plan', withJson('POST', {
        project_id: projectRef.current, instance_name: 'shine-h3', port: 8188, mode: 'local',
      }));
      if (result.complete) return '实例准备已完成并保持停止，请生成受管配置。生产时由 Worker 启动渲染器。';
      setPlans(previous => [...previous.filter(p => p.plan_id !== result.plan_id), { ...result, kind: 'INSTANCE_LIFECYCLE' }]);
      return `下一步：${result.action}，请审核后批准执行`;
    });
  }

  async function runPlan(plan: PlanRef) {
    setBusy(true);
    try { await approveAndExecute(plan, plan.action || plan.kind); }
    catch (error) { say((error as Error).message, 'bad'); }
    finally { setBusy(false); void loadReadiness(); void loadPlans().catch(error => say(String(error), 'bad')); }
  }

  function generateConfiguration() {
    void run(async () => {
      const result = await api<Record<string, unknown>>('/setup/configuration',
        withJson('POST', { project_id: projectRef.current }));
      await loadRuntime();
      await onRefresh();
      return `受管配置已生成：${String((result as { recipe?: string }).recipe ?? '')}`;
    });
  }

  function activateRuntime() {
    const candidate = runtime?.candidate;
    if (!candidate || candidate.project_id !== projectRef.current) return;
    void run(async () => {
      const result = await api<RuntimeState>('/setup/runtime/activate', withJson('POST', {
        project_id: candidate.project_id, recipe_sha256: candidate.recipe_sha256,
      }));
      setRuntime(result); await loadReadiness(); await onRefresh();
      return '运行配置已激活，API 和 Worker 将使用同一份配置。服务与真实推理仍需独立核验。';
    });
  }

  function validateServices() {
    const candidate = runtime?.candidate;
    if (!candidate || candidate.project_id !== projectRef.current) return;
    void run(async () => {
      const result = await api<{ status: string; node_count: number; workflow_profiles: string[];
        review_models: Record<string, string> }>('/setup/services/validate', withJson('POST', {
        project_id: candidate.project_id, recipe_sha256: candidate.recipe_sha256,
      }));
      await loadRuntime(); await loadReadiness();
      return `服务与工作流验证完成：${result.status} · ${result.workflow_profiles.length} 个工作流 · ${result.node_count} 个节点 · 审片 ${Object.values(result.review_models).join(' / ')}`;
    });
  }

  function registerProfiles() {
    void run(async () => {
      const result = await api<{ profiles: Array<{ name: string; status: string }> }>('/setup/profiles',
        withJson('POST', { project_id: projectRef.current }));
      // Refresh the shared workflow-profile state so the queue/series template
      // pickups on this page update immediately, without a manual reload.
      await onRefresh();
      return result.profiles.map((p) => `${p.name}: ${p.status}`).join('、');
    });
  }

  function detectEnvironment() {
    void run(async () => {
      setChecked(false);
      setExistingResolved(false); setExistingScan(null); setImportResult('');
      const [environment, ready, state, disk, existing] = await Promise.all([
        api<EnvReport>('/setup/environment'), api<Readiness>('/setup/readiness'),
        api<RuntimeState>('/setup/runtime'), api<DeploymentStorage>('/setup/storage'),
        api<{ installations: string[] }>('/setup/existing-installations'),
      ]);
      setEnv(environment); setReadiness(ready); setRuntime(state); setStorage(disk);
      setExistingRoots(existing.installations);
      setExistingChoice(existing.installations.length ? 'reuse' : 'new');
      setExistingPath(existing.installations[0] ?? '');
      setExistingResolved(existing.installations.length === 0);
      setChecked(true);
      return environment.blockers.length
        ? '检测完成，请先处理下方提示的硬件或基础工具问题。'
        : existing.installations.length ? '发现已有 ComfyUI，请先检查可复用的模型。'
          : '常见目录检测完成。如已有 ComfyUI，可手动指定目录；未发现不代表未安装。';
    });
  }

  function inspectExisting() {
    void run(async () => {
      setExistingScan(null); setExistingResolved(false); setImportResult('');
      const result = await api<ExistingScan>('/setup/existing-installations/inspect',
        withJson('POST', { path: existingPath }));
      setExistingScan(result);
      return `目录已识别，发现 ${result.matches.length} 项待哈希校验的模型文件。`;
    });
  }

  function reuseExisting() {
    if (!existingScan || !storage) return;
    if (!window.confirm(`校验并复用已有模型？\n来源：${existingScan.root}\n目标：${storage.root}\n最多复制 ${fmtBytes(existingScan.candidate_bytes)}；仅哈希与固定清单一致的文件进入缓存。原安装保持不变，不会下载或启动服务。`)) return;
    void run(async () => {
      const result = await api<{ reused_bytes: number; rejected: Array<{ component_id: string; relative_path: string }> }>(
        '/setup/existing-installations/import-models', { ...withJson('POST', {
          path: existingScan.root, snapshot: existingScan.snapshot,
        }), signal: AbortSignal.timeout(3_600_000) });
      const message = `已校验并缓存 ${fmtBytes(result.reused_bytes)}，${result.rejected.length} 项哈希不匹配，不能按固定版本复用。${result.rejected.map(row => `${row.component_id}/${row.relative_path}`).join('、')} 后续只下载缓存中缺少的组件。`;
      setImportResult(message); setExistingResolved(true);
      return message;
    });
  }

  function prepareAutomatically() {
    if (!projectId || !checked || !storage || storage.requires_selection || !existingResolved) return;
    const selected = projectId;
    if (!window.confirm(`下载并配置本机生产环境？\n目录：${storage.root}\n完整清单约 ${fmtBytes(catalog?.total_bytes ?? 0)}，已校验复用的模型与缓存不会重复下载。\n将独立安装固定版本的程序、节点及缺少的模型，保留原 ComfyUI，自动登记工作流、生成并激活配置，短暂启动服务检测后停止。不会生成视频。`)) return;
    void run(async () => {
      const ensureCurrent = () => {
        if (!mounted.current || projectRef.current !== selected) throw new Error('页面或项目已切换，自动准备已停止；已执行记录保留，请重新检测。');
      };
      try {
        setProgress('检查下载网络…');
        const network = await api<Connectivity>('/setup/connectivity');
        setConnectivity(network);
        if (network.blocked.length) throw new Error(`下载源无法连接：${network.blocked.join('、')}。请检查网络后重新检测。`);
        ensureCurrent();
        const pending = await api<Array<{ id: string; sha256: string; status: string; payload: { deployment?: unknown; kind?: string } }>>(
          `/comfyops/install-plans?project_id=${selected}`);
        if (pending.some(p => (p.payload.deployment || p.payload.kind === 'INSTANCE_LIFECYCLE')
          && !['FILES_VERIFIED_PENDING_RUNTIME', 'STOPPED', 'MOCK_VERIFIED', 'CANCELLED', 'RECONCILED_NO_RETRY'].includes(p.status))) {
          throw new Error('已有未完成的安装记录，请展开安装详情继续原计划或对账；不会新建重复安装。');
        }
        const ready = await api<Readiness>('/setup/readiness');
        if (!ready.layers.some(layer => layer.layer === 'L2_files_verified' && layer.status === 'READY')) {
          setProgress('下载并校验模型与组件（可耗时较长，请保持页面打开）…');
          const plan = await api<PlanRef>('/setup/install-plan', withJson('POST', {
            components: catalog?.components.map(c => c.component_id) ?? [], project_id: selected, disk_free: storage.free_bytes,
          }));
          ensureCurrent();
          await approveAndExecute({ ...plan, kind: 'INSTALL' }, '下载安装');
        }
        let complete = false;
        for (let step = 0; step < 8; step += 1) {
          ensureCurrent();
          setProgress('自动准备运行环境…');
          const plan = await api<PlanRef & { complete: boolean }>('/setup/next-instance-plan', withJson('POST', {
            project_id: selected, instance_name: 'shine-h3', port: 8188, mode: 'local',
          }));
          if (plan.complete) { complete = true; break; }
          ensureCurrent();
          await approveAndExecute({ ...plan, kind: 'INSTANCE_LIFECYCLE' }, '准备运行环境');
        }
        if (!complete) throw new Error('环境准备步骤超过预期，已停止，请检查安装详情。');
        ensureCurrent();
        setProgress('登记工作流并生成配置…');
        await api('/setup/profiles', withJson('POST', { project_id: selected }));
        ensureCurrent();
        await api('/setup/configuration', withJson('POST', { project_id: selected }));
        const state = await api<RuntimeState>('/setup/runtime');
        setRuntime(state);
        if (state.candidate?.project_id !== selected) throw new Error('没有匹配的运行配置，请检查安装详情。');
        const body = { project_id: selected, recipe_sha256: state.candidate.recipe_sha256 };
        ensureCurrent();
        setProgress('启动服务并检测工作流，完成后自动停止…');
        const validation = await api<{ status: string }>('/setup/services/validate', {
          ...withJson('POST', body), signal: AbortSignal.timeout(3_600_000),
        });
        if (validation.status !== 'READY') throw new Error(`服务检测未通过：${validation.status}。配置尚未激活。`);
        ensureCurrent();
        await api('/setup/runtime/activate', withJson('POST', body));
        await onRefresh();
        return '环境已配置并通过服务检测，可以回到制片。真实 H3 生成效果将在实际任务中验证。';
      } finally {
        setProgress(''); setChecked(false);
        await Promise.allSettled([loadRuntime(), loadReadiness(), loadPlans()]);
      }
    });
  }

  return (
    <section className="card section-card" id="setup">
      <div className="section-heading">
        <div>
          <div className="section-kicker">00 / NEW MACHINE SETUP</div>
          <h2>新机设置与生产就绪</h2>
          <p>先点击检测，再确认下载。安装、工作流登记和运行配置由系统依次完成。</p>
        </div>
        <div className="section-icon"><Sparkles size={17} /></div>
      </div>

      {notice && <div className={notice.kind === 'ok' ? 'ok-line' : 'error-line'}>{notice.text}</div>}

      <div className="settings-block">
        <button className="primary-button" disabled={busy} onClick={detectEnvironment}>检测本机环境</button>
        {checked && <>
          <p>安装目录：{storage?.root ?? '尚未选择'} · 可用 {storage?.free_gb ?? '—'} GB</p>
          {env?.blockers.length ? <p role="status">{env.blockers.map(blockerText).join('；')}。基础工具可重新运行「一键安装并启动.cmd」补齐；显存、内存或磁盘不足需要先处理。</p> :
          <p role="status">{runtime?.status === 'ACTIVE' && runtime.services_verified ? '当前配置已激活，服务已核验。' : '工作台尚未配置就绪；这不代表本机没有安装 ComfyUI 或模型。'}</p>}
          <fieldset className="setup-existing" disabled={busy}>
            <legend>已有 ComfyUI 与模型复用</legend>
            <p>{existingRoots.length ? `发现 ${existingRoots.length} 个已有安装。` : '常见目录未发现安装，可手动指定；不会遍历整个磁盘。'}</p>
            {existingRoots.map(root => <p key={root}><button className="secondary-button" onClick={() => {
              setExistingPath(root); setExistingChoice('reuse'); setExistingResolved(false);
              setExistingScan(null); setImportResult('');
            }}>{root}</button></p>)}
            <label><input type="radio" name="existing-comfy" checked={existingChoice === 'reuse'} onChange={() => {
              setExistingChoice('reuse'); setExistingResolved(false);
            }} />检查并复用已有模型</label>
            <label><input type="radio" name="existing-comfy" checked={existingChoice === 'new'} onChange={() => {
              setExistingChoice('new'); setExistingResolved(true);
            }} />独立安装，不导入已有模型</label>
            {existingChoice === 'reuse' && <>
              <label className="existing-path">已有 ComfyUI 目录<input value={existingPath} onChange={e => {
                setExistingPath(e.target.value); setExistingScan(null); setExistingResolved(false); setImportResult('');
              }} placeholder="包含 main.py 的目录或整合包目录" /></label>
              <button className="secondary-button" disabled={!existingPath.trim()} onClick={inspectExisting}>检查已有目录</button>
              {existingScan && <>
                <p>已识别：{existingScan.root} · 扫描 {existingScan.scanned_files} 个文件。</p>
                <p>候选可复用 {existingScan.matches.length} 项（{fmtBytes(existingScan.candidate_bytes)}，{existingResolved ? '哈希结果见下方' : '待哈希校验'}）；未找到固定版本文件 {existingScan.missing.length} 项。</p>
                <ul>{existingScan.matches.map(row => <li key={`${row.component_id}:${row.relative_path}`}>{row.component_id} / {row.relative_path}：{existingResolved ? '哈希结果见下方' : '待哈希校验'}</li>)}</ul>
                <details><summary>查看未找到的固定版本模型</summary><ul>{existingScan.missing.map(row =>
                  <li key={`${row.component_id}:${row.relative_path}`}>{row.component_id} / {row.relative_path} · {fmtBytes(row.size)}</li>)}</ul></details>
                {!!existingScan.skipped.length && <p>部分目录无法读取或为外部链接，未完整扫描，请核对模型位置。</p>}
                <button className="secondary-button" disabled={!storage || storage.requires_selection || existingResolved} onClick={reuseExisting}>校验并复用匹配模型</button>
              </>}
              {importResult && <p role="status">{importResult}</p>}
            </>}
            <p>复用只复制哈希匹配的模型到工作台缓存；原安装、节点、模型保持不变。工作台仍需独立配置固定版本运行环境，检测到目录不等于生产验证通过。外部链接与 extra_model_paths 指向的模型目录暂不自动读取。</p>
          </fieldset>
          <button className="primary-button" disabled={busy || !projectId || !catalog?.components.length || !!env?.blockers.length || !storage || storage.requires_selection
            || !existingResolved
            || (runtime?.status === 'ACTIVE' && runtime.services_verified && runtime.candidate?.project_id === projectId)}
            onClick={prepareAutomatically}>{runtime?.status === 'ACTIVE' && runtime.services_verified && runtime.candidate?.project_id === projectId ? '本项目环境已配置' : '下载缺项并自动配置'}</button>
          {!projectId && <p>先在下方给项目起个名字，即可开始自动配置。</p>}
          {storage?.requires_selection && <p>请选择下载安装目录：<a href="#deployment-storage">选择目录</a></p>}
        </>}
        {progress && <p role="status">{progress}</p>}
        <p>检测不会下载模型或提交视频。下载前会显示目录、体积与安装范围，由你确认。</p>
      </div>

      <details><summary>查看检测与安装详情（高级）</summary>
      <div className="settings-grid">
        <div className="settings-block">
          <h3>1 · 环境检查</h3>
          {!env ? <div className="empty-inline">读取中…</div> : <>
            <div className="worker-row"><div><b>GPU</b><span>{String(env.gpu.name ?? '—')} · {String(env.gpu.vram_mb ?? '—')} MiB · 驱动 {String(env.gpu.driver_version ?? '—')}</span></div></div>
            <div className="worker-row"><div><b>内存/磁盘</b><span>{env.ram.ram_mb ?? '—'} MB · 磁盘可用 {env.disk.free_gb} GB（安装目录 {env.deployment_dir?.path ?? env.data_dir.path}）</span></div></div>
            <div className="worker-row"><div><b>端口 {env.port.port}</b><span>{env.port.status === 'OK' ? '可用' : '被占用'}</span></div></div>
            <table className="setup-tools">
              <tbody>
                {Object.entries(env.tools).map(([name, info]) => (
                  <tr key={name}>
                    <td>{TOOL_LABELS[name] ?? name}</td>
                    <td>{info.present ? info.version : '缺失'}</td>
                    <td className={info.present && info.at_floor ? 'ok-cell' : 'bad-cell'}>
                      {info.present ? (info.at_floor ? '达标' : '版本过低') : '可由引导安装'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {env.blockers.length > 0 && <div className="guardrail-note">
              本机阻断项：{env.blockers.map((b) => b.code + (b.tool ? `(${b.tool})` : '')).join('、')}。
              可安装项运行：<code>pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1</code>
            </div>}
          </>}
        </div>

        <div className="settings-block">
          <h3>1b · 上游可达性（下载前必查）</h3>
          {!connectivity ? <div className="empty-inline">尚未检查。下载 60.8 GB 之前请先检查一次。</div> : <>
            <p className="guardrail-note">
              {connectivity.checked_at} · 结论 <b>{label(serviceStatusLabels, connectivity.status)}</b>
            </p>
            <table className="setup-tools">
              <tbody>
                {connectivity.upstreams.map((row) => (
                  <tr key={row.id}>
                    <td>{row.label}</td>
                    <td>{row.host}</td>
                    <td className={row.status === 'OK' ? 'ok-cell' : 'bad-cell'}>
                      {row.status === 'OK'
                        ? (row.http_status === 404 ? '可达（根路径 404 属正常）' : `可达 ${row.http_status}`)
                        : row.status === 'DEGRADED' ? `服务异常 ${row.http_status}` : `不可达（${row.error}）`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {connectivity.blocked.length > 0 && <div className="guardrail-note">
              阻断：{connectivity.blocked.join('、')}。先解决代理/VPN/防火墙，再回到这里复检；否则下载会在中途失败。
            </div>}
          </>}
          <div className="inline-form">
            <button className="secondary-button" disabled={busy} onClick={checkConnectivity}>检查上游可达性</button>
          </div>
        </div>

        <div className="settings-block">
          <h3>2 · 部署清单（固定版本）</h3>
          {!catalog ? <div className="empty-inline">读取中…</div> : <>
            <p className="route-row">
              {catalog.catalog_id} v{catalog.catalog_version}（{catalog.generated}）· 总量 {fmtBytes(catalog.total_bytes)}
            </p>
            <div className="setup-components">
              {catalog.components.map((component) => (
                <div className="worker-row" key={component.component_id}>
                  <div>
                    <b>{component.component_id}</b>
                    <span>{component.kind} · {component.revision.slice(0, 12)} · {fmtBytes(component.bytes)} · {component.license_id}</span>
                  </div>
                  <small>{component.destination_family ?? component.role ?? ''}</small>
                </div>
              ))}
            </div>
          </>}
          <p className="guardrail-note">安装仍走既有审批链：每张计划需要人工一次性授权，哈希不符即拒绝。</p>
        </div>

        <div className="settings-block">
          <h3>3 · 计划与批准</h3>
          <p className="guardrail-note">下载与安装到：{storage?.root ?? '读取中…'} · 可用 {storage?.free_gb ?? '—'} GB。
            <a href="#deployment-storage">修改下载安装目录</a>
            {storage?.requires_selection && ' 请先选择非系统盘目录，才能自动安装。'}
          </p>
          <div className="inline-form">
            <button className="primary-button" disabled={busy || !projectId || !storage || storage.requires_selection} onClick={createInstallPlan}>生成安装计划</button>
            <button className="secondary-button" disabled={busy || !projectId} onClick={createInstanceChain}>生成实例链计划</button>
            <button className="secondary-button" disabled={busy || !projectId} onClick={registerProfiles}>登记工作流 Profile</button>
            <button className="secondary-button" disabled={busy || !projectId} onClick={generateConfiguration}>生成受管配置</button>
            <button className="secondary-button" disabled={busy || runtime?.candidate?.project_id !== projectId}
              onClick={validateServices}>验证服务与工作流</button>
            <button className="secondary-button" disabled={busy || runtime?.candidate?.project_id !== projectId}
              onClick={activateRuntime}>确认并激活运行配置</button>
          </div>
          {!projectId && <p className="guardrail-note">计划生成、Profile 登记、配置生成与激活需要先创建一个项目（本页下方即可创建）；环境检查、上游可达性与部署清单无需项目。</p>}
          <p className="guardrail-note">验证会短暂启动已安装的 ComfyUI、VLM 和 ASR，读取真实节点/工作流契约并探测审片协议，完成后停止；不加载 H3 权重，也不提交镜头。</p>
          {runtime && <p className="guardrail-note setup-runtime-state">运行配置：{RUNTIME_LABELS[runtime.status] ?? runtime.status} · 当前模式：{label(providerLabels, runtime.render_provider)}
            {runtime.candidate?.project_id === projectId && <> · 待确认配方：<code>{runtime.candidate.recipe_sha256}</code></>}
            {runtime.service_validation && <> · 服务契约：{label(serviceStatusLabels, runtime.service_validation.status)}
              {runtime.service_validation.validated_at ? ` · ${runtime.service_validation.validated_at}` : ''}
              {runtime.service_validation.blockers?.length ? ` · ${runtime.service_validation.blockers.join('、')}` : ''}</>}
            {runtime.error && <> · {runtime.error}</>}
          </p>}
          {plans.length > 0 && <div className="setup-plans">
            {plans.map((plan) => (
              <div className="worker-row" key={plan.plan_id}>
                <div><b>{plan.action ?? plan.kind}</b><span>编号 {plan.plan_id.slice(0, 8)}… · {label(prepRowLabels, plan.status)} · 层级 {plan.tier ?? '—'}</span></div>
                <button className="text-button" disabled={busy || !['AWAITING_APPROVAL', 'APPROVED'].includes(plan.status)}
                  onClick={() => void runPlan(plan)}>{plan.status === 'APPROVED' ? '执行已批准计划' : '批准并执行'}</button>
              </div>
            ))}
          </div>}
          <p className="guardrail-note">安装计划与实例链计划共用既有端点；未批准或哈希变化会直接拒绝。</p>
        </div>

        <div className="settings-block">
          <h3>4 · 就绪检查</h3>
          {!readiness ? <div className="empty-inline">读取中…</div> : <>
            {readiness.layers.map((layer) => {
              const status = String(layer.status);
              const blockers = Array.isArray(layer.blockers) ? layer.blockers : [];
              return (
                <div className="worker-row" key={String(layer.layer)}>
                  <span className={`status-led ${status === 'READY' ? 'mock' : 'offline'}`} />
                  <div>
                    <b>{String(layer.layer)}</b>
                    <span>{label(serviceStatusLabels, status)}{blockers.length ? ` · 待解决：${blockers.map((item) => typeof item === 'string' ? item : JSON.stringify(item)).join('；').slice(0, 160)}` : ''}</span>
                  </div>
                </div>
              );
            })}
            <div className="guardrail-note">
              真实推理层只由真实生产证据更新；Mock/Fake 不会把它标成 READY。
              人工步骤：{readiness.human_steps.join('；')}。
            </div>
          </>}
        </div>
      </div>
      </details>
    </section>
  );
}
