import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, ArrowDownToLine, ArrowUpRight, Check, ChevronDown, CircleHelp, Clapperboard,
  Clock3, Film, FolderKanban, LockKeyhole, LockOpen, Pause, Play, Plus, RefreshCw,
  ShieldCheck, Sparkles, Upload, X,
} from 'lucide-react';

type Project = {
  id: string; title: string; description: string; frozen_style_version_id: string | null;
  style_draft: Style | null;
};
type Asset = {
  id: string; filename: string; kind: string; content_type: string; sha256: string;
  byte_size: number; approved: boolean; locked: boolean;
};
type Style = {
  photography_language: string; shot_blocking: string; color_palette: string;
  lighting_tone: string; lens_texture: string; editing_rhythm: string; sound_performance: string;
};
type StyleVersion = { id: string; version: number; sha256: string; snapshot: Style };
type Shot = { id: string; name: string; revision: number; spec: Record<string, unknown> };
type Job = {
  id: string; shot_id: string; shot_name: string; status: string; provider: string;
  simulated: boolean; video_created: boolean; paused: boolean; stale: boolean;
  failure_code: string | null; semantic_retries: number; max_semantic_retries: number;
  request: { reference_manifest: Array<{ label: string; asset_id: string; sha256: string }> };
  attempts: Array<{ attempt_no: number; status: string; failure_code: string | null }>;
};

const API = '/api/v1';
const emptyStyle: Style = {
  photography_language: '', shot_blocking: '', color_palette: '', lighting_tone: '',
  lens_texture: '', editing_rhythm: '', sound_performance: '',
};
const styleFields: Array<[keyof Style, string, string]> = [
  ['photography_language', '摄影语言', '景别、机位高度、镜头运动与禁用项'],
  ['shot_blocking', '镜头调度', '人物走位、正反打与空间关系'],
  ['color_palette', '色彩风格', '主辅色、冷暖关系与肤色保护'],
  ['lighting_tone', '影调与灯光', '反差、黑位、主光方向与阴影细节'],
  ['lens_texture', '镜头质感', '焦段、景深、颗粒与锐化边界'],
  ['editing_rhythm', '剪辑与节奏', '镜头时长、对白停顿与切换规则'],
  ['sound_performance', '声音表演', '语言、语速、音色及环境声规则'],
];
const statusLabels: Record<string, string> = {
  PLANNED: '等待前置镜头', WAITING_ASSET: '等待素材', READY: '准备中', QUEUED: '队列中',
  SUBMITTING: '提交中', RUNNING: '运行中', QC_PENDING: '检查中', PASSED: '模拟通过',
  REPAIR_PENDING: '等待修复', NEEDS_REVIEW: '需要人工复核', FAILED: '失败',
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep HTTP status */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [styles, setStyles] = useState<StyleVersion[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [style, setStyle] = useState<Style>(emptyStyle);
  const [projectTitle, setProjectTitle] = useState('');
  const [shotName, setShotName] = useState('第一镜');
  const [storyBeat, setStoryBeat] = useState('');
  const [duration, setDuration] = useState(6);
  const [assetKind, setAssetKind] = useState('image');
  const [shotAssetId, setShotAssetId] = useState('');
  const [mockScenario, setMockScenario] = useState('success');
  const [workerState, setWorkerState] = useState<string>('checking');
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const project = projects.find((item) => item.id === projectId);
  const currentStyle = styles.find((item) => item.id === project?.frozen_style_version_id);
  const approvedAssets = useMemo(() => assets.filter((asset) => asset.approved && asset.locked), [assets]);

  const refresh = useCallback(async (id = projectId) => {
    const health = await fetch('/health').then((response) => response.json());
    setWorkerState(health.status === 'ok' ? 'mock' : 'offline');
    const projectRows = await api<Project[]>('/projects');
    setProjects(projectRows);
    if (!id) return;
    const selected = await api<Project>(`/projects/${id}`);
    setProjects((rows) => rows.map((row) => row.id === id ? selected : row));
    const [assetRows, styleRows, shotRows, jobRows] = await Promise.all([
      api<Asset[]>(`/projects/${id}/assets`), api<StyleVersion[]>(`/projects/${id}/styles`),
      api<Shot[]>(`/projects/${id}/shots`), api<Job[]>(`/projects/${id}/jobs`),
    ]);
    setAssets(assetRows); setStyles(styleRows); setShots(shotRows); setJobs(jobRows);
    setStyle(selected.style_draft ?? styleRows.at(-1)?.snapshot ?? emptyStyle);
  }, [projectId]);

  useEffect(() => {
    api<Project[]>('/projects').then((rows) => {
      setProjects(rows);
      if (rows.length) setProjectId(rows[0].id);
    }).catch((error: Error) => report(error.message, 'error'));
  }, []);

  useEffect(() => {
    if (projectId) refresh(projectId).catch((error: Error) => report(error.message, 'error'));
  }, [projectId, refresh]);

  useEffect(() => {
    if (!projectId) return;
    const timer = window.setInterval(() => {
      Promise.all([api<Job[]>(`/projects/${projectId}/jobs`), api<Shot[]>(`/projects/${projectId}/shots`)]).
        then(([jobRows, shotRows]) => { setJobs(jobRows); setShots(shotRows); }).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [projectId]);

  function report(text: string, kind: 'success' | 'error' = 'success') {
    setNotice({ text, kind });
    window.setTimeout(() => setNotice(null), 5000);
  }

  async function withBusy(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); } catch (error) { report((error as Error).message, 'error'); }
    finally { setBusy(false); }
  }

  async function createProject() {
    if (!projectTitle.trim()) return;
    await withBusy(async () => {
      const created = await api<Project>('/projects', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: projectTitle.trim(), description: '' }),
      });
      setProjectTitle(''); setProjects((rows) => [created, ...rows]); setProjectId(created.id);
      report('项目已创建。先完善七项风格规则并由你冻结。');
    });
  }

  async function saveStyle() {
    if (!projectId) return;
    await withBusy(async () => {
      await api(`/projects/${projectId}/style/draft`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(style),
      });
      await refresh(projectId); report('风格草案已保存。');
    });
  }

  async function freezeStyle() {
    if (!projectId) return;
    await withBusy(async () => {
      const frozen = await api<StyleVersion>(`/projects/${projectId}/style/freeze`, { method: 'POST' });
      await refresh(projectId); report(`Style Bible V${frozen.version} 已冻结，版本哈希已记录。`);
    });
  }

  async function uploadAsset(file?: File) {
    if (!file || !projectId) return;
    await withBusy(async () => {
      const form = new FormData(); form.append('asset_kind', assetKind); form.append('file', file);
      const asset = await api<Asset>(`/projects/${projectId}/assets`, { method: 'POST', body: form });
      await refresh(projectId); report(`素材已入库，SHA-256 ${asset.sha256.slice(0, 12)}…；需要人工批准并锁定。`);
    });
  }

  async function updateAsset(assetId: string, action: 'approve' | 'lock') {
    await withBusy(async () => {
      await api(`/assets/${assetId}/${action}`, { method: 'POST' });
      await refresh(projectId); report(action === 'approve' ? '素材已由当前用户批准。' : '素材已锁定。');
    });
  }

  async function createShot() {
    if (!project || !currentStyle || !storyBeat.trim()) return;
    await withBusy(async () => {
      const assetIds = shotAssetId ? [shotAssetId] : [];
      const body = {
        name: shotName.trim() || `镜头 ${shots.length + 1}`,
        spec: {
          schema_version: '1.0', story_beat: storyBeat.trim(), duration_target_s: Number(duration),
          shot_priority: 'B', style_version_id: currentStyle.id, camera: {}, characters: [],
          required_asset_ids: assetIds, dialogue: [], sound: {}, continuity: {}, locked_story_facts: [],
        },
      };
      await api(`/projects/${projectId}/shots`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      setStoryBeat(''); await refresh(projectId); report('分镜已保存，可创建模拟任务。');
    });
  }

  async function createJob(shot: Shot) {
    await withBusy(async () => {
      const selectedAsset = assets.find((asset) => asset.id === shotAssetId);
      const useAs = selectedAsset?.kind === 'video' ? 'video' : selectedAsset?.kind === 'audio' ? 'audio' : 'picture';
      const refs = selectedAsset ? [{ asset_id: selectedAsset.id, use_as: useAs }] : [];
      await api(`/projects/${projectId}/jobs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ shot_id: shot.id, references: refs, depends_on: [], mock_scenario: mockScenario }),
      });
      await refresh(projectId); report('模拟任务已进入本地队列。请保持独立 Worker 运行。');
    });
  }

  async function togglePause(job: Job) {
    await withBusy(async () => {
      await api(`/jobs/${job.id}/${job.paused ? 'resume' : 'pause'}`, { method: 'POST' });
      await refresh(projectId);
    });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Clapperboard size={19} /></div><div><b>SHINE</b><span>VIDEO STUDIO</span></div></div>
        <div className="workspace-label">工作区</div>
        <button className="nav-item active"><FolderKanban size={17} />项目工作台 <span className="nav-dot" /></button>
        <button className="nav-item" onClick={() => document.getElementById('assets')?.scrollIntoView({ behavior: 'smooth' })}><Film size={17} />素材与风格</button>
        <button className="nav-item" onClick={() => document.getElementById('production')?.scrollIntoView({ behavior: 'smooth' })}><Activity size={17} />生产监控</button>
        <div className="sidebar-spacer" />
        <div className="privacy-card"><ShieldCheck size={17} /><div><b>本地优先</b><span>素材仅保存在当前设备</span></div><Check size={15} /></div>
        <div className="profile"><div className="avatar">S</div><div><b>本地工作区</b><span>开发预览</span></div><ChevronDown size={16} /></div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb"><span>工作区</span><span className="crumb-slash">/</span><b>{project?.title ?? '项目工作台'}</b></div>
          <div className="top-actions"><div className="service-status"><span className={`status-led ${workerState}`} />{workerState === 'mock' ? 'API 已连接 · Mock 模式' : '检查连接中'}</div><button className="icon-button" title="刷新" onClick={() => refresh()}><RefreshCw size={16} /></button><button className="help-button"><CircleHelp size={16} /> 帮助</button></div>
        </header>

        <div className="page-wrap">
          <section className="welcome-row">
            <div><div className="eyebrow"><span className="eyebrow-line" /> 制片工作台 <span className="eyebrow-en">PRODUCTION DESK</span></div><h1>让好故事，<em>有章可循。</em></h1><p>在生成之前，先锁定风格、素材与镜头意图。</p></div>
            <div className="date-chip"><Clock3 size={15} /> 本地项目 <span>·</span> {projects.length} 个</div>
          </section>

          {!project && <section className="empty-project card"><div className="empty-art"><FolderKanban size={27} /></div><h2>从一个项目开始</h2><p>项目、人工素材、冻结风格和任务状态都保存在本地 SQLite 中。</p><div className="create-project"><input value={projectTitle} onChange={(e) => setProjectTitle(e.target.value)} placeholder="给项目起个名字" onKeyDown={(e) => e.key === 'Enter' && createProject()} /><button className="primary-button" disabled={busy || !projectTitle.trim()} onClick={createProject}><Plus size={16} /> 新建项目</button></div></section>}

          {project && <>
            <section className="project-selector card">
              <div className="project-icon"><Clapperboard size={20} /></div>
              <div className="project-meta"><span>当前制片项目</span><b>{project.title}</b></div>
              <select aria-label="切换项目" value={projectId} onChange={(e) => setProjectId(e.target.value)}>{projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}</select>
              <div className="selector-divider" /><input className="new-project-inline" value={projectTitle} onChange={(e) => setProjectTitle(e.target.value)} placeholder="新项目名称" /><button className="subtle-button" disabled={busy || !projectTitle.trim()} onClick={createProject}><Plus size={15} /> 新建项目</button>
            </section>

            <section className="metrics-grid">
              <div className="metric-card"><div className="metric-top"><span>项目素材</span><div className="metric-icon mint"><Film size={16} /></div></div><div className="metric-value">{assets.length}<small>项</small></div><div className="metric-foot">{approvedAssets.length} 项已批准并锁定</div></div>
              <div className="metric-card"><div className="metric-top"><span>冻结风格</span><div className="metric-icon sand"><LockKeyhole size={16} /></div></div><div className="metric-value">{currentStyle ? `V${currentStyle.version}` : '—'}</div><div className="metric-foot">{currentStyle ? `SHA · ${currentStyle.sha256.slice(0, 10)}` : '完成七项规则后由你冻结'}</div></div>
              <div className="metric-card"><div className="metric-top"><span>分镜任务</span><div className="metric-icon blue"><Activity size={16} /></div></div><div className="metric-value">{jobs.length}<small>个</small></div><div className="metric-foot">{jobs.filter((job) => job.status === 'PASSED').length} 个模拟流程通过</div></div>
              <div className="metric-card accent-metric"><div className="metric-top"><span>运行模式</span><div className="metric-icon cream"><Sparkles size={16} /></div></div><div className="metric-value small-value">Mock H3</div><div className="metric-foot">不生成视频 · 待本机接入 MiniMax-H3</div></div>
            </section>

            <section className="card section-card" id="assets">
              <div className="section-heading"><div><div className="section-kicker">01 / ASSET REGISTRY</div><h2>素材与主资产审批</h2><p>上传只写入本地内容寻址目录；只有人工批准并锁定的素材才能加入镜头。</p></div><div className="section-icon"><Film size={18} /></div></div>
              <div className="upload-row"><div><label>素材类型</label><select value={assetKind} onChange={(e) => setAssetKind(e.target.value)}><option value="image">图片</option><option value="video">视频</option><option value="audio">音频</option></select></div><label className="upload-button"><Upload size={16} /> 上传到本地素材库<input type="file" accept={assetKind === 'image' ? 'image/png,image/jpeg,image/webp' : assetKind === 'audio' ? 'audio/*' : 'video/*'} onChange={(e) => uploadAsset(e.target.files?.[0])} /></label><span className="upload-hint">最大 25 MiB · 校验文件签名 · SHA-256 去重</span></div>
              <div className="asset-list">{assets.length === 0 ? <div className="empty-inline">尚无素材。请只上传你提供且有权使用的素材。</div> : assets.map((asset) => <div className="asset-row" key={asset.id}><div className={`asset-type ${asset.kind}`}>{asset.kind === 'image' ? 'IMG' : asset.kind === 'audio' ? 'AUD' : 'VID'}</div><div className="asset-name"><b>{asset.filename}</b><span>{asset.sha256.slice(0, 16)}… · {(asset.byte_size / 1024).toFixed(1)} KB</span></div><div className="asset-state">{asset.locked ? <span className="pill approved"><LockKeyhole size={12} />已锁定</span> : asset.approved ? <span className="pill approved"><Check size={12} />已批准</span> : <span className="pill pending"><Clock3 size={12} />待人工审批</span>}</div>{!asset.approved && <button className="text-button" onClick={() => updateAsset(asset.id, 'approve')}>批准</button>}{asset.approved && !asset.locked && <button className="text-button" onClick={() => updateAsset(asset.id, 'lock')}>锁定</button>}</div>)}</div>
              <div className="guardrail-note"><LockKeyhole size={14} /><span>Agent 没有主资产创建或审批权限；素材只在本机使用，不会发送到云端。</span></div>
            </section>

            <section className="card section-card" id="style">
              <div className="section-heading"><div><div className="section-kicker">02 / STYLE BIBLE</div><h2>先定风格，再开机位</h2><p>每项规则都进入不可变快照。新风格会创建新版本，旧镜头继续引用原版本。</p></div><div className={`frozen-badge ${currentStyle ? 'is-frozen' : ''}`}>{currentStyle ? <><LockKeyhole size={13} /> 已冻结 V{currentStyle.version}</> : <><LockOpen size={13} /> 尚未冻结</>}</div></div>
              <div className="style-grid">{styleFields.map(([key, label, hint], index) => <label className="style-field" key={key}><span><i>0{index + 1}</i>{label}</span><textarea value={style[key]} placeholder={hint} onChange={(e) => setStyle((value) => ({ ...value, [key]: e.target.value }))} rows={2} /></label>)}</div>
              <div className="section-footer"><span>{currentStyle ? <><Check size={14} /> 冻结哈希 {currentStyle.sha256.slice(0, 18)}…</> : '所有七项必须填写，并由你明确执行冻结'}</span><div><button className="secondary-button" disabled={busy || !style.photography_language} onClick={saveStyle}>保存草案</button><button className="primary-button" disabled={busy || styleFields.some(([key]) => !style[key].trim())} onClick={freezeStyle}><LockKeyhole size={15} /> {currentStyle ? '冻结为新版本' : '批准并冻结'}</button></div></div>
            </section>

            <section className="production-layout" id="production">
              <div className="card section-card storyboard-card"><div className="section-heading compact"><div><div className="section-kicker">03 / SHOT WORKSPACE</div><h2>分镜与模拟任务</h2><p>正式任务绑定 Style hash 和逐任务局部引用清单。</p></div><div className="section-icon plum"><Clapperboard size={18} /></div></div>
                {!currentStyle && <div className="blocked-callout"><LockKeyhole size={16} /><span>Style Bible 尚未冻结，分镜和生产入口已锁定。</span></div>}
                <div className={`shot-form ${!currentStyle ? 'is-disabled' : ''}`}><label>镜头名称<input value={shotName} onChange={(e) => setShotName(e.target.value)} disabled={!currentStyle} /></label><label>时长（4–15 秒）<input type="number" min={4} max={15} value={duration} onChange={(e) => setDuration(Number(e.target.value))} disabled={!currentStyle} /></label><label className="wide-field">镜头意图<textarea value={storyBeat} onChange={(e) => setStoryBeat(e.target.value)} placeholder="描述画面、动作和关键剧情事实" disabled={!currentStyle} /></label><label className="wide-field">锁定素材<select value={shotAssetId} onChange={(e) => setShotAssetId(e.target.value)} disabled={!currentStyle}><option value="">不引用素材</option>{approvedAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.filename} · {asset.kind}</option>)}</select></label><button className="primary-button wide-button" disabled={busy || !currentStyle || !storyBeat.trim()} onClick={createShot}><Plus size={15} /> 保存分镜</button></div>
                <div className="shot-list">{shots.map((shot) => <div className="shot-row" key={shot.id}><div className="shot-number">{String(shot.revision).padStart(2, '0')}</div><div className="shot-content"><b>{shot.name}</b><span>{String(shot.spec.story_beat ?? '')}</span></div><button className="small-action" disabled={!currentStyle || busy} onClick={() => createJob(shot)}>加入 Mock 队列 <ArrowUpRight size={13} /></button></div>)}{shots.length === 0 && <div className="empty-inline">冻结风格后创建第一条分镜。</div>}</div>
              </div>

              <div className="card section-card monitor-card"><div className="section-heading compact"><div><div className="section-kicker">04 / PRODUCTION MONITOR</div><h2>任务运行情况</h2><p>任务状态与每次尝试都保存在 SQLite。</p></div><div className="worker-indicator"><span className="pulse" />独立 Worker</div></div>
                <div className="mock-warning"><Sparkles size={14} /><span>演示模式：Mock 只模拟调度与 QC，不会生成或冒充视频。</span></div>
                <div className="scenario-row"><label>下一项模拟结果<select value={mockScenario} onChange={(e) => setMockScenario(e.target.value)}><option value="success">正常完成</option><option value="delay">延迟完成</option><option value="fail">模拟失败（可修复）</option><option value="uncertain">模拟提交结果不明</option><option value="duplicate">幂等重复确认</option></select></label><code>uv run --project backend python -m shine.worker</code></div>
                <div className="job-list">{jobs.length === 0 ? <div className="empty-inline">暂无任务。通过上方镜头卡片加入队列。</div> : jobs.map((job) => <div className="job-row" key={job.id}><div className={`job-state ${job.status.toLowerCase()}`}><span /></div><div className="job-details"><div><b>{job.shot_name}</b><span className="job-mode">MOCK</span>{job.stale && <span className="job-stale">需复核</span>}</div><span>{statusLabels[job.status] ?? job.status}{job.failure_code ? ` · ${job.failure_code}` : ''}</span><small>{job.attempts.length} 次尝试 · 返工 {job.semantic_retries}/{job.max_semantic_retries}</small></div>{['PLANNED', 'READY', 'QUEUED'].includes(job.status) && <button className="icon-button tiny" title={job.paused ? '继续任务' : '暂停任务'} onClick={() => togglePause(job)}>{job.paused ? <Play size={14} /> : <Pause size={14} />}</button>}</div>)}</div>
                <div className="monitor-footer"><span><span className="status-led mock" />{workerState === 'mock' ? '后端可用' : '连接中'}</span><span>仅 loopback · 不上传素材</span></div>
              </div>
            </section>

            <footer className="page-footer"><span>SHINE VIDEO STUDIO <i>·</i> V1.0 DEVELOPMENT</span><span><ShieldCheck size={13} /> 本地状态库 · 用户可控素材</span></footer>
          </>}
        </div>
      </main>

      {notice && <div className={`toast ${notice.kind}`}><span>{notice.kind === 'success' ? <Check size={15} /> : <X size={15} />}</span>{notice.text}<button onClick={() => setNotice(null)}><X size={14} /></button></div>}
      {busy && <div className="busy-indicator"><ArrowDownToLine size={14} />处理中</div>}
    </div>
  );
}

export default App;
