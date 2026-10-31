import { Sparkles } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

type ToolInfo = { present: boolean; version: string; at_floor: boolean };
type EnvReport = {
  platform: Record<string, string>;
  tools: Record<string, ToolInfo>;
  gpu: Record<string, unknown>;
  ram: { status: string; ram_mb?: number; at_floor?: boolean };
  disk: { status: string; free_gb: number; at_floor: boolean };
  port: { status: string; port: number };
  data_dir: { path: string; writable: boolean };
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
type Readiness = { catalog: { id: string; version: string }; layers: Array<Record<string, unknown>>; production_gates: Record<string, boolean>; human_steps: string[] };
type Grant = { grant_id: string; snapshot: string };

const TOOL_LABELS: Record<string, string> = {
  uv: 'uv', node: 'Node.js', npm: 'npm', pwsh: 'PowerShell 7', ffmpeg: 'FFmpeg', ffprobe: 'FFprobe',
};

function fmtBytes(value: number): string {
  const units = ['B', 'KB', 'MB', 'GB'];
  let result = value;
  let index = 0;
  while (result >= 1024 && index < 3) { result /= 1024; index += 1; }
  return `${result.toFixed(result >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function SetupPanel({ projectId, onRefresh }: { projectId: string; onRefresh: () => Promise<void> }) {
  const [token, setToken] = useState('local-operator');
  const [env, setEnv] = useState<EnvReport | null>(null);
  const [catalog, setCatalog] = useState<CatalogSummary | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [plans, setPlans] = useState<PlanRef[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'bad'; text: string } | null>(null);
  const projectRef = useRef(projectId);
  projectRef.current = projectId;

  const headers = useCallback((): RequestInit => ({ headers: { Authorization: `Bearer ${token}` } }), [token]);
  const withJson = useCallback((method: string, body: unknown): RequestInit => {
    const base = headers();
    return { ...base, method, headers: { ...base.headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  }, [headers]);

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
  const loadCatalog = useCallback(async () => {
    try { setCatalog(await api<CatalogSummary>('/setup/catalog')); } catch { /* keep prior state */ }
  }, []);
  const loadReadiness = useCallback(async () => {
    try { setReadiness(await api<Readiness>('/setup/readiness')); } catch { /* keep prior state */ }
  }, []);

  useEffect(() => { void loadEnvironment(); void loadCatalog(); void loadReadiness(); }, [loadEnvironment, loadCatalog, loadReadiness]);

  async function approveAndExecute(plan: PlanRef, label: string): Promise<void> {
    const grant = await api<Grant>('/preparation/approval-grants',
      withJson('POST', { action: 'install', target_id: plan.plan_id, context: null }));
    await api(`/comfyops/install-plans/${plan.plan_id}/approve`,
      withJson('POST', { grant_id: grant.grant_id, snapshot: grant.snapshot, evidence: 'Setup 向导内人工批准' }));
    const exec = await api<Record<string, unknown>>(`/comfyops/install-plans/${plan.plan_id}/execute`,
      { ...withJson('POST', { plan_hash: plan.sha256, executor: 'local' }), signal: AbortSignal.timeout(600000_000) });
    say(`${label} 执行完成：${JSON.stringify(exec).slice(0, 120)}`);
  }

  function createInstallPlan() {
    void run(async () => {
      const core = ['comfyui', 'comfy-runtime-wheels', 'minimax-h3-diffusion', 'minimax-h3-text-encoder',
        'minimax-h3-vae', 'minimax-h3-lora', 'minimax-h3-pdd',
        'qwen3-vl-8b-fp8', 'qwen3-asr-0.6b', 'ComfyUI-KJNodes', 'comfyui-videohelpersuite',
        'ComfyUI-MiniMax-H3-PDD-Acc', 'ComfyUI-SolAttn_triton', 'Comfyui_Minimax_h3_latent_Upscaler'];
      const result = await api<{ plan_id: string; sha256: string; download_bytes: number }>(
        '/setup/install-plan', withJson('POST', { components: core, project_id: projectRef.current, disk_free: 0 }));
      return `安装计划 ${result.plan_id} 已创建（下载 ${fmtBytes(result.download_bytes)}）`;
    });
  }

  function createInstanceChain() {
    void run(async () => {
      const result = await api<{ plans: PlanRef[] }>('/setup/instance-chain', withJson('POST', {
        project_id: projectRef.current, instance_name: 'shine-h3', port: 8188, mode: 'local',
      }));
      setPlans(result.plans);
      return `实例链 4 张计划已创建：${result.plans.map((p) => p.action).join(' → ')}`;
    });
  }

  async function runPlan(plan: PlanRef) {
    setBusy(true);
    try { await approveAndExecute(plan, plan.action || plan.kind); }
    catch (error) { say((error as Error).message, 'bad'); }
    finally { setBusy(false); void loadReadiness(); }
  }

  function generateConfiguration() {
    void run(async () => {
      const result = await api<Record<string, unknown>>('/setup/configuration',
        withJson('POST', { project_id: projectRef.current }));
      await onRefresh();
      return `受管配置已生成：${String((result as { recipe?: string }).recipe ?? '')}`;
    });
  }

  function registerProfiles() {
    void run(async () => {
      const result = await api<{ profiles: Array<{ name: string; status: string }> }>('/setup/profiles',
        withJson('POST', { project_id: projectRef.current }));
      return result.profiles.map((p) => `${p.name}: ${p.status}`).join('、');
    });
  }

  return (
    <section className="card section-card" id="setup">
      <div className="section-heading">
        <div>
          <div className="section-kicker">00 / NEW MACHINE SETUP</div>
          <h2>新机设置与生产就绪</h2>
          <p>空白 Windows 上的完整链路：环境检查 → 部署计划 → 人工批准 → 隔离安装 → 配置生成 → 就绪检查。</p>
        </div>
        <div className="section-icon"><Sparkles size={17} /></div>
      </div>

      {notice && <div className={notice.kind === 'ok' ? 'ok-line' : 'error-line'}>{notice.text}</div>}

      <div className="settings-grid">
        <div className="settings-block">
          <h3>1 · 环境检查</h3>
          {!env ? <div className="empty-inline">读取中…</div> : <>
            <div className="worker-row"><div><b>GPU</b><span>{String(env.gpu.name ?? '—')} · {String(env.gpu.vram_mb ?? '—')} MiB · 驱动 {String(env.gpu.driver_version ?? '—')}</span></div></div>
            <div className="worker-row"><div><b>内存/磁盘</b><span>{env.ram.ram_mb ?? '—'} MB · 磁盘可用 {env.disk.free_gb} GB（数据目录 {env.data_dir.path}）</span></div></div>
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
          <div className="inline-form">
            <input value={token} type="password" placeholder="操作员凭据（仅内存）"
              onChange={(event) => setToken(event.target.value)} />
          </div>
          <div className="inline-form">
            <button className="primary-button" disabled={busy || !projectId} onClick={createInstallPlan}>生成安装计划</button>
            <button className="secondary-button" disabled={busy || !projectId} onClick={createInstanceChain}>生成实例链计划</button>
            <button className="secondary-button" disabled={busy || !projectId} onClick={registerProfiles}>登记工作流 Profile</button>
            <button className="secondary-button" disabled={busy || !projectId} onClick={generateConfiguration}>生成受管配置</button>
          </div>
          {plans.length > 0 && <div className="setup-plans">
            {plans.map((plan) => (
              <div className="worker-row" key={plan.plan_id}>
                <div><b>{plan.action ?? plan.kind}</b><span>{plan.plan_id} · {plan.status} · tier {plan.tier ?? '-'}</span></div>
                <button className="text-button" disabled={busy} onClick={() => void runPlan(plan)}>批准并执行</button>
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
              return (
                <div className="worker-row" key={String(layer.layer)}>
                  <span className={`status-led ${status === 'READY' ? 'mock' : 'offline'}`} />
                  <div>
                    <b>{String(layer.layer)}</b>
                    <span>{status}{layer.blockers ? ` · ${JSON.stringify(layer.blockers).slice(0, 120)}` : ''}</span>
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
    </section>
  );
}
