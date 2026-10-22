import { PreproductionPanel } from './components/PreproductionPanel';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, ArrowDownToLine, Check, ChevronDown, CircleHelp, Clapperboard, Clock3,
  Film, FolderKanban, LockKeyhole, RefreshCw, ShieldCheck, Sparkles, X,
} from 'lucide-react';
import {
  api, json, type AgentRoute, type Asset, type AuditPage, type Job, type Project,
  type PromptPreview, type ProviderProfile, type QCReport, type QualityGate, type RepairSuggestion, type Shot,
  type Style, type StyleVersion, type SystemHealth, type SystemStatus, type WorkflowProfile,
} from './api';
import { emptyStyle } from './constants';
import AssetPanel from './components/AssetPanel';
import StylePanel from './components/StylePanel';
import ShotPanel from './components/ShotPanel';
import ProductionPanel from './components/ProductionPanel';
import SettingsPanel from './components/SettingsPanel';
import AuditPanel from './components/AuditPanel';
import FilmCrewPanel from './components/FilmCrewPanel';

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [styles, setStyles] = useState<StyleVersion[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [style, setStyle] = useState<Style>(emptyStyle);
  const [projectTitle, setProjectTitle] = useState('');
  const [mockScenario, setMockScenario] = useState('success');
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [routes, setRoutes] = useState<AgentRoute[]>([]);
  const [workflowProfiles, setWorkflowProfiles] = useState<WorkflowProfile[]>([]);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [queueProvider, setQueueProvider] = useState('mock');
  const [queueProfile, setQueueProfile] = useState('');

  const project = projects.find((item) => item.id === projectId);
  const currentStyle = styles.find((item) => item.id === project?.frozen_style_version_id);
  const approvedAssets = useMemo(() => assets.filter((asset) => asset.approved && asset.locked), [assets]);

  function report(text: string, kind: 'success' | 'error' = 'success') {
    setNotice({ text, kind });
    window.setTimeout(() => setNotice(null), 5200);
  }

  async function withBusy(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); } catch (error) { report((error as Error).message, 'error'); }
    finally { setBusy(false); }
  }

  const loadSystem = useCallback(async () => {
    try { setSystem(await api<SystemStatus>('/system/status')); }
    catch { setSystem(null); }
  }, []);

  const loadAudit = useCallback(async (id: string, offset = 0) => {
    const page = await api<AuditPage>(`/projects/${id}/events?limit=30&offset=${offset}`);
    setAudit((current) => offset === 0 || !current
      ? page
      : { ...page, items: [...current.items, ...page.items] });
  }, []);

  const loadSettings = useCallback(async (id: string) => {
    const [profileRows, routeRows, workflowRows] = await Promise.all([
      api<ProviderProfile[]>('/provider-profiles'),
      api<AgentRoute[]>('/agent-routes'),
      api<WorkflowProfile[]>(`/workflow-profiles?project_id=${id}`),
    ]);
    setProfiles(profileRows); setRoutes(routeRows); setWorkflowProfiles(workflowRows);
  }, []);

  const loadProject = useCallback(async (id: string) => {
    const selected = await api<Project>(`/projects/${id}`);
    setProjects((rows) => rows.map((row) => row.id === id ? selected : row));
    const [assetRows, styleRows, shotRows, jobRows] = await Promise.all([
      api<Asset[]>(`/projects/${id}/assets`),
      api<StyleVersion[]>(`/projects/${id}/styles`),
      api<Shot[]>(`/projects/${id}/shots`),
      api<Job[]>(`/projects/${id}/jobs`),
    ]);
    setAssets(assetRows); setStyles(styleRows); setShots(shotRows); setJobs(jobRows);
    setStyle(selected.style_draft ?? styleRows.at(-1)?.snapshot ?? emptyStyle);
    await loadSettings(id);
    await loadAudit(id, 0);
  }, [loadAudit, loadSettings]);

  const refreshAll = useCallback(async () => {
    await loadSystem();
    if (projectId) await loadProject(projectId);
  }, [loadSystem, loadProject, projectId]);

  useEffect(() => {
    api<Project[]>('/projects').then((rows) => {
      setProjects(rows);
      if (rows.length) setProjectId(rows[0].id);
    }).catch((error: Error) => report(error.message, 'error'));
  }, []);

  useEffect(() => {
    if (projectId) loadProject(projectId).catch((error: Error) => report(error.message, 'error'));
  }, [projectId, loadProject]);

  useEffect(() => { loadSystem(); }, [loadSystem]);

  useEffect(() => {
    if (!projectId) return;
    const timer = window.setInterval(() => {
      Promise.all([
        api<Job[]>(`/projects/${projectId}/jobs`),
        api<Shot[]>(`/projects/${projectId}/shots`),
      ]).then(([jobRows, shotRows]) => { setJobs(jobRows); setShots(shotRows); }).catch(() => undefined);
      loadSystem();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [projectId, loadSystem]);

  async function createProject() {
    if (!projectTitle.trim()) return;
    await withBusy(async () => {
      const created = await api<Project>('/projects', json('POST', { title: projectTitle.trim(), description: '' }));
      setProjectTitle(''); setProjects((rows) => [created, ...rows]); setProjectId(created.id);
      report('项目已创建。先完成素材审批与七维风格冻结。');
    });
  }

  async function saveStyle() {
    if (!projectId) return;
    await withBusy(async () => {
      await api(`/projects/${projectId}/style/draft`, json('PUT', style));
      await loadProject(projectId); report('风格草案已保存。');
    });
  }

  async function freezeStyle(operatorToken = '') {
    if (!projectId) return;
    await withBusy(async () => {
      const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${operatorToken}` };
      const grant = operatorToken ? await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', {
        ...json('POST', { action: 'style_freeze', target_id: projectId }), headers,
      }) : { grant_id: undefined, snapshot: undefined };
      const frozen = await api<StyleVersion>(`/projects/${projectId}/style/freeze`, {
        ...json('POST', { approval_note: '', grant_id: grant.grant_id, snapshot: grant.snapshot }), ...(operatorToken ? { headers } : {}),
      });
      await loadProject(projectId);
      report(`Style Bible V${frozen.version} 已冻结；${frozen.impact?.affected_shot_ids.length ?? 0} 个历史镜头引用旧版本。`);
    });
  }

  async function uploadAsset(file: File, meta: { kind: string; role: string; entityId: string; tags: string }) {
    if (!projectId) return;
    await withBusy(async () => {
      const form = new FormData();
      form.append('asset_kind', meta.kind);
      form.append('file', file);
      if (meta.role) form.append('role', meta.role);
      if (meta.entityId) form.append('entity_id', meta.entityId);
      if (meta.tags) form.append('tags', meta.tags);
      const asset = await api<Asset>(`/projects/${projectId}/assets`, { method: 'POST', body: form });
      await loadProject(projectId);
      report(`素材已入库，SHA-256 ${asset.sha256.slice(0, 12)}…；需要人工批准并锁定。`);
    });
  }

  async function updateAsset(assetId: string, action: 'approve' | 'lock') {
    await withBusy(async () => {
      await api(`/assets/${assetId}/${action}`, { method: 'POST' });
      await loadProject(projectId);
      report(action === 'approve' ? '素材已由当前用户批准。' : '素材已锁定，可供镜头引用。');
    });
  }

  async function patchAssetMetadata(assetId: string, patch: Record<string, unknown>) {
    await withBusy(async () => {
      await api(`/assets/${assetId}/metadata`, json('PATCH', patch));
      await loadProject(projectId); report('素材元数据已更新。');
    });
  }

  async function createShot(payload: Record<string, unknown>) {
    await withBusy(async () => {
      await api(`/projects/${projectId}/shots`, json('POST', payload));
      await loadProject(projectId); report('分镜已保存；每个镜头保留独立素材绑定。');
    });
  }

  async function saveBindings(shotId: string, bindings: Record<string, unknown>[]) {
    await withBusy(async () => {
      await api(`/shots/${shotId}/bindings`, json('PUT', { bindings }));
      await loadProject(projectId); setPreview(null); report('镜头绑定已更新。');
    });
  }

  async function previewShot(shotId: string) {
    await withBusy(async () => {
      const result = await api<PromptPreview>(`/shots/${shotId}/compile`, { method: 'POST' });
      setPreview(result); report(`H3 Prompt 已编译：${result.mode}。`);
    });
  }

  async function queueShot(shot: Shot) {
    if (queueProvider === 'comfyui') {
      const profile = workflowProfiles.find(p => p.id === queueProfile && p.mode === 'Ref2VA');
      if (!profile) { report('请先选择已注册的 Ref2VA Profile。', 'error'); return; }
      if (!window.confirm(`提交真实 Ref2VA 生成？\n镜头：${shot.name}\nProfile：${profile.name}\n此操作会进入 GPU 队列。`)) return;
    }
    await withBusy(async () => {
      await api(`/projects/${projectId}/jobs`, json('POST', {
        shot_id: shot.id, mock_scenario: mockScenario, provider: queueProvider,
        workflow_profile_id: queueProvider === 'comfyui' ? queueProfile : null,
        generation_mode: queueProvider === 'comfyui' ? 'Ref2VA' : null,
      }));
      await loadProject(projectId); report('任务已进入本地队列；请保持独立 Worker 运行。');
    });
  }

  async function togglePause(job: Job) {
    await withBusy(async () => {
      await api(`/jobs/${job.id}/${job.paused ? 'resume' : 'pause'}`, { method: 'POST' });
      await loadProject(projectId);
    });
  }

  async function loadJobQC(jobId: string): Promise<QCReport[]> {
    return api<QCReport[]>(`/jobs/${jobId}/qc`);
  }

  async function loadJobGates(jobId: string): Promise<QualityGate[]> {
    return api<QualityGate[]>(`/jobs/${jobId}/quality-gates`);
  }

  async function suggestRepair(jobId: string): Promise<RepairSuggestion> {
    return api<RepairSuggestion>(`/jobs/${jobId}/repair/suggest`, { method: 'POST' });
  }

  async function applyRepair(jobId: string, plan: Record<string, unknown>) {
    await withBusy(async () => {
      await api(`/jobs/${jobId}/repair?mock_scenario=success`, json('POST', plan));
      await loadProject(projectId); report('按修复计划重新入队。');
    });
  }

  async function updateProjectSettings(patch: Record<string, unknown>) {
    await withBusy(async () => {
      await api(`/projects/${projectId}/settings`, json('PATCH', patch));
      await loadProject(projectId);
    });
  }

  async function createProfile(payload: Record<string, unknown>) {
    await withBusy(async () => {
      await api<ProviderProfile>('/provider-profiles', json('POST', payload));
      await loadSettings(projectId); report('Provider 配置已保存；密钥不会回显。');
    });
  }

  async function testProfile(profileId: string) {
    await withBusy(async () => {
      const result = await api<{ ok: boolean; message: string; error_code?: string | null; capabilities: Record<string, unknown> }>(`/provider-profiles/${profileId}/test`, { method: 'POST' });
      report(result.ok ? `连接成功 · ${String(result.capabilities?.models ?? '')}` : `连接失败：${result.error_code ?? result.message}`, result.ok ? 'success' : 'error');
      await loadSettings(projectId);
    });
  }

  async function setProfileKey(profileId: string, key: string) {
    await withBusy(async () => {
      await api(`/provider-profiles/${profileId}/key`, json('PUT', { api_key: key }));
      await loadSettings(projectId); report('密钥已写入本机安全存储。');
    });
  }

  async function deleteProfile(profileId: string) {
    await withBusy(async () => {
      await api(`/provider-profiles/${profileId}`, { method: 'DELETE' });
      await loadSettings(projectId); report('Provider 配置已删除。');
    });
  }

  async function routeProfile(role: string, profileId: string) {
    await withBusy(async () => {
      await api<AgentRoute>('/agent-routes', json('PUT', { role, provider_profile_id: profileId }));
      await loadSettings(projectId); report(`已把 ${role} 路由到此 Provider。`);
    });
  }

  async function testComfy(baseUrl: string): Promise<SystemHealth> {
    return withBusyReturn(async () => {
      const result = await api<SystemHealth>('/comfy/test', json('POST', { base_url: baseUrl, timeout_s: 10 }));
      if (result.ok) await loadSystem();
      return result;
    });
  }

  async function withBusyReturn<T>(action: () => Promise<T>): Promise<T> {
    setBusy(true);
    try { return await action(); }
    catch (error) { report((error as Error).message, 'error'); throw error; }
    finally { setBusy(false); }
  }

  async function registerWorkflow(payload: Record<string, unknown>) {
    await withBusy(async () => {
      await api<WorkflowProfile>('/workflow-profiles', json('POST', payload));
      await loadSettings(projectId); report('工作流 Profile 已注册并通过结构校验。');
    });
  }

  async function deleteWorkflow(profileId: string) {
    await withBusy(async () => {
      await api(`/workflow-profiles/${profileId}`, { method: 'DELETE' });
      await loadSettings(projectId);
    });
  }

  const apiOnline = system?.api_online ?? false;
  const modeLabel = system?.render_mode === 'comfyui' ? 'ComfyUI 模式' : 'Mock 模式';

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Clapperboard size={19} /></div><div><b>SHINE</b><span>VIDEO STUDIO</span></div></div>
        <div className="workspace-label">工作区</div>
        <button className="nav-item active"><FolderKanban size={17} />项目工作台 <span className="nav-dot" /></button>
        <button className="nav-item" onClick={() => document.getElementById('assets')?.scrollIntoView({ behavior: 'smooth' })}><Film size={17} />素材与风格</button>
        <button className="nav-item" onClick={() => document.getElementById('production')?.scrollIntoView({ behavior: 'smooth' })}><Activity size={17} />生产与质检</button>
        <button className="nav-item" onClick={() => document.getElementById('settings')?.scrollIntoView({ behavior: 'smooth' })}><Sparkles size={17} />模型与系统</button>
        <button className="nav-item" onClick={() => document.getElementById('audit')?.scrollIntoView({ behavior: 'smooth' })}><ShieldCheck size={17} />审计追踪</button>
        <button className="nav-item" onClick={() => document.getElementById('film-crew')?.scrollIntoView({ behavior: 'smooth' })}><Clapperboard size={17} />影视制片团队</button>
        <button className="nav-item" onClick={() => document.getElementById('h3-rulebook')?.scrollIntoView({ behavior: 'smooth' })}><ShieldCheck size={17} />H3 官方规范中心</button>
        <div className="sidebar-spacer" />
        <div className="privacy-card"><ShieldCheck size={17} /><div><b>本地优先</b><span>素材与密钥不离开本机</span></div><Check size={15} /></div>
        <div className="profile"><div className="avatar">S</div><div><b>本地工作区</b><span>{apiOnline ? 'API 已连接' : 'API 离线'}</span></div><ChevronDown size={16} /></div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb"><span>工作区</span><span className="crumb-slash">/</span><b>{project?.title ?? '项目工作台'}</b></div>
          <div className="top-actions">
            <div className="status-cluster">
              <span className="service-status"><span className={`status-led ${apiOnline ? 'mock' : 'offline'}`} />API</span>
              <span className="service-status"><span className={`status-led ${system?.worker_online ? 'mock' : 'offline'}`} />Worker</span>
              <span className="service-status"><span className={`status-led ${system?.mock_provider_available ? 'mock' : 'offline'}`} />Mock</span>
              <span className="service-status"><span className={`status-led ${system?.h3_connected ? 'mock' : 'offline'}`} />H3 真机</span>
            </div>
            <span className={`mode-badge ${system?.render_mode === 'comfyui' ? 'real' : ''}`}>{modeLabel}</span>
            <button className="icon-button" title="刷新" onClick={() => refreshAll().catch((error: Error) => report(error.message, 'error'))}><RefreshCw size={15} /></button>
            <button className="help-button"><CircleHelp size={15} /> 帮助</button>
          </div>
        </header>

        <div className="page-wrap">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> 制片工作台 <span className="eyebrow-en">PRODUCTION DESK</span></div>
              <h1>让好故事，<em>有章可循。</em></h1>
              <p>先锁定风格、人工资产与镜头意图，再让本地 H3 工作流执行。</p>
            </div>
            <div className="date-chip"><Clock3 size={14} /> 本地项目 <span>·</span> {projects.length} 个</div>
          </section>

          {!project && <section className="empty-project card">
            <div className="empty-art"><FolderKanban size={26} /></div>
            <h2>从一个项目开始</h2>
            <p>项目、人工素材、冻结风格、任务与审计都保存在本地 SQLite 中。</p>
            <div className="create-project">
              <input value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="给项目起个名字" onKeyDown={(event) => event.key === 'Enter' && createProject()} />
              <button className="primary-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>新建项目</button>
            </div>
          </section>}

          {project && <>
            <section className="project-selector card">
              <div className="project-icon"><Clapperboard size={20} /></div>
              <div className="project-meta"><span>当前制片项目</span><b>{project.title}</b></div>
              <select aria-label="切换项目" value={projectId} onChange={(event) => setProjectId(event.target.value)}>{projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}</select>
              <div className="selector-divider" />
              <input className="new-project-inline" value={projectTitle} onChange={(event) => setProjectTitle(event.target.value)} placeholder="新项目名称" />
              <button className="subtle-button" disabled={busy || !projectTitle.trim()} onClick={createProject}>新建项目</button>
            </section>

            <section className="metrics-grid">
              <div className="metric-card"><div className="metric-top"><span>项目素材</span><div className="metric-icon mint"><Film size={15} /></div></div><div className="metric-value">{assets.length}<small>项</small></div><div className="metric-foot">{approvedAssets.length} 项已批准并锁定</div></div>
              <div className="metric-card"><div className="metric-top"><span>冻结风格</span><div className="metric-icon sand"><LockKeyhole size={15} /></div></div><div className="metric-value">{currentStyle ? `V${currentStyle.version}` : '—'}</div><div className="metric-foot">{currentStyle ? `SHA · ${currentStyle.sha256.slice(0, 10)}` : '完成七项规则后由你冻结'}</div></div>
              <div className="metric-card"><div className="metric-top"><span>分镜任务</span><div className="metric-icon blue"><Activity size={15} /></div></div><div className="metric-value">{jobs.length}<small>个</small></div><div className="metric-foot">{jobs.filter((job) => job.status === 'PASSED').length} 个通过 · {jobs.filter((job) => job.status === 'NEEDS_REVIEW').length} 个待复核</div></div>
              <div className="metric-card accent-metric"><div className="metric-top"><span>运行模式</span><div className="metric-icon cream"><Sparkles size={15} /></div></div><div className="metric-value small-value">{modeLabel}</div><div className="metric-foot">{system?.h3_connected ? '真实 H3 已连接' : '真实 H3 未连接 · 待真机验收'}</div></div>
            </section>

            <AssetPanel
              assets={assets}
              busy={busy}
              onUpload={uploadAsset}
              onApprove={(assetId) => updateAsset(assetId, 'approve')}
              onLock={(assetId) => updateAsset(assetId, 'lock')}
              onPatchMetadata={patchAssetMetadata}
            />

            <StylePanel style={style} setStyle={setStyle} currentStyle={currentStyle} styles={styles} busy={busy} onSave={saveStyle} onFreeze={freezeStyle} />

              <div className="card">
                <label>提交方式<select value={queueProvider} onChange={e => setQueueProvider(e.target.value)}>
                  <option value="mock">Mock 软件演示（默认）</option><option value="comfyui">真实 Ref2VA（逐次确认）</option>
                </select></label>
                {queueProvider === 'comfyui' && <label>真实工作流 Profile<select value={queueProfile} onChange={e => setQueueProfile(e.target.value)}>
                  <option value="">请选择</option>{workflowProfiles.filter(p => p.mode === 'Ref2VA').map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select></label>}
              </div>
            <section className="production-layout" id="production">
              <ShotPanel
                assets={assets}
                shots={shots}
                currentStyle={currentStyle}
                busy={busy}
                preview={preview}
                onPreview={previewShot}
                onQueue={queueShot}
                onCreate={createShot}
                onSaveBindings={saveBindings}
                setPreview={setPreview}
              />
              <ProductionPanel
                jobs={jobs}
                system={system}
                busy={busy}
                scenario={mockScenario}
                setScenario={setMockScenario}
                onTogglePause={togglePause}
                onLoadQC={loadJobQC}
                onLoadGates={loadJobGates}
                onSuggest={suggestRepair}
                onRepair={applyRepair}
              />
            </section>

            <SettingsPanel
              project={project}
              profiles={profiles}
              routes={routes}
              workflowProfiles={workflowProfiles}
              system={system}
              busy={busy}
              onProjectSettings={updateProjectSettings}
              onCreateProfile={createProfile}
              onTestProfile={testProfile}
              onSetKey={setProfileKey}
              onDeleteProfile={deleteProfile}
              onRoute={routeProfile}
              onTestComfy={testComfy}
              onRegisterWorkflow={registerWorkflow}
              onDeleteWorkflow={deleteWorkflow}
            />

            <AuditPanel
              page={audit}
              busy={busy}
              onLoad={() => loadAudit(projectId, 0).catch((error: Error) => report(error.message, 'error'))}
              onMore={() => loadAudit(projectId, audit?.items.length ?? 0).catch((error: Error) => report(error.message, 'error'))}
            />

            <PreproductionPanel key={`prep-${projectId}`} projectId={projectId} onRefresh={refreshAll} />

            <FilmCrewPanel key={projectId} projectId={projectId} profiles={profiles} shots={shots} onRefresh={refreshAll} />

            <footer className="page-footer"><span>SHINE VIDEO STUDIO <i>·</i> V1.1 DEVELOPMENT</span><span><ShieldCheck size={12} /> 本地状态库 · 用户可控素材</span></footer>
          </>}
        </div>
      </main>

      {notice && <div className={`toast ${notice.kind}`}><span>{notice.kind === 'success' ? <Check size={15} /> : <X size={15} />}</span>{notice.text}<button onClick={() => setNotice(null)}><X size={14} /></button></div>}
      {busy && <div className="busy-indicator"><ArrowDownToLine size={14} />处理中</div>}
    </div>
  );
}

export default App;
