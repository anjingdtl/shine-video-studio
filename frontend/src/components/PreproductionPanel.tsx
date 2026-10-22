import { useEffect, useRef, useState } from 'react';
import { api, json, API } from '../api';

type Row = { id: string; status: string; sha256: string; payload: Record<string, unknown>; formal_asset_id?: string };
type Registry = { categories: string[]; revisions: Row[]; legacy_profiles: unknown[] };
const views = ['环境中心', '模型与节点', '工作流能力', '全资产工厂', 'Voice Studio', '开机前预检'];

export function PreproductionPanel({ projectId, onRefresh }: { projectId: string; onRefresh: () => Promise<void> }) {
  const [view, setView] = useState(0);
  const [mode, setMode] = useState('fake');
  const [confirmed, setConfirmed] = useState(false);
  const [diskBudget, setDiskBudget] = useState('');
  const [localEvidence, setLocalEvidence] = useState('{}');
  const offline = mode === 'fake';
  const [token, setToken] = useState('');
  const [rows, setRows] = useState<Row[]>([]);
  const [rowView, setRowView] = useState(-1);
  const activeView = useRef(view);
  activeView.current = view;
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [evidence, setEvidence] = useState('');
  const [entity, setEntity] = useState('');
  const [family, setFamily] = useState('character');
  const [rightsId, setRightsId] = useState('');
  const [voiceRights, setVoiceRights] = useState('');
  const [workflowId, setWorkflowId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [text, setText] = useState('你好，这是对白试听。');
  const [voiceSource, setVoiceSource] = useState('ORIGINAL_DESIGN');
  const [referenceId, setReferenceId] = useState('');
  const [emotion, setEmotion] = useState('neutral');
  const [speed, setSpeed] = useState(1);
  const [pause, setPause] = useState(0);
  const [documentId, setDocumentId] = useState('');
  const [packageJson, setPackageJson] = useState('');
  const [draftJson, setDraftJson] = useState('');
  const [packageId, setPackageId] = useState('');
  const [rightsScope, setRightsScope] = useState('asset');
  const [license, setLicense] = useState('');
  const headers = () => ({ Authorization: `Bearer ${token}` });
  const post = <T,>(path: string, body: unknown, privileged = false) => api<T>(path, {
    ...json('POST', body), headers: { 'Content-Type': 'application/json', ...(privileged ? headers() : {}) },
  });
  async function load() {
    const requestedView = view;
    let nextRows: Row[] = [];
    let nextResult: unknown = undefined;
    let nextRegistry: Registry | null = null;
    if (view === 0) nextRows = await api<Row[]>('/comfyops/inventory');
    if (view === 1) {
      const [plans, models, nodes] = await Promise.all([
        api<Row[]>('/comfyops/install-plans'), api('/comfyops/models'), api('/comfyops/nodes'),
      ]);
      nextRows = plans; nextResult = { models, nodes };
    }
    if (view === 2) { nextRegistry = await api<Registry>('/workflow-capabilities'); nextRows = nextRegistry.revisions; }
    if (view === 3) nextRows = await api<Row[]>(`/projects/${projectId}/candidate-assets`);
    if (view === 4) nextRows = await api<Row[]>(`/projects/${projectId}/voice-profiles`);
    if (view === 5) nextResult = await api(`/projects/${projectId}/preproduction/requirements`);
    if (activeView.current !== requestedView) return;
    setRows(nextRows); setRowView(requestedView);
    if (nextRegistry) setRegistry(nextRegistry);
    if (nextResult !== undefined) setResult(nextResult);
  }
  useEffect(() => { setResult(null); setError(''); load().catch(e => setError(e.message)); }, [view, projectId]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { const r = await fn(); await load(); setResult(r); await onRefresh(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function approve(row: Row, action: string, path: string) {
    if (!offline && !confirmed) throw new Error('请确认本地权限和操作风险。');
    if (!evidence.trim()) throw new Error('请填写核验、权利和试听证据。');
    const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
      { action, target_id: row.id }, true);
    return post(path, { grant_id: grant.grant_id, snapshot: grant.snapshot, offline, listened: action === 'voice_freeze', evidence }, true);
  }
  return <section className="card" id="preproduction">
    <div className="section-heading"><h2>制片准备 · V1.3</h2><strong>请求模式：{offline ? '模拟' : '本地'} · 实际执行器与验证级别见后端结果</strong></div>
    <nav className="button-row">{views.map((name, i) => <button key={name} onClick={() => setView(i)}
      disabled={busy} aria-pressed={view === i}>{name}</button>)}</nav>
    <p>候选晋升、音色冻结与安装审批使用服务器验证的操作员身份；凭据仅保留在当前页面内存。</p>
    <div className="form-grid">
      <label>操作员凭据<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} /></label>
      <label>人工核验 / 试听 / 权利证据<input value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
    </div>
    <label>操作模式<select value={mode} onChange={e => { setMode(e.target.value); setConfirmed(false); }}>
      <option value="fake">模拟 / 离线</option><option value="local">本地 / 生产证据</option></select></label>
    {!offline && <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
      我已核对服务器配置的范围、来源、许可和具体操作风险</label>}
    <p>切换请求模式不会更改已有结果来源。模拟输出不能晋升为真实验证。</p>
    <button disabled={busy} onClick={() => act(load)}>刷新后端状态</button>
    {error && <p role="alert" className="error-text">{error}</p>}
    {view === 0 && <>
      <p>模拟使用 FakeWindowsHostProbe；本地仅扫描管理员在服务器配置的路径与端口白名单。</p>
      <button disabled={busy} onClick={() => act(() => post('/comfyops/inventory/refresh', { mode }))}>运行选定模式的只读检测</button>
      <button disabled={busy} onClick={() => act(() => api('/comfyops/resource-status'))}>资源与租约</button>
    </>}
    {view === 1 && <>
      <label>模型 / 节点 PackageSpec JSON<textarea value={packageJson} onChange={e => setPackageJson(e.target.value)}
        placeholder="包含固定 revision、文件 URL / size / SHA256、license_id、usage、dependencies" /></label>
      <button disabled={busy} onClick={() => act(() => post('/comfyops/packages', JSON.parse(packageJson)))}>登记来源与版本</button>
      <label>Package ID<input value={packageId} onChange={e => setPackageId(e.target.value)} /></label>
      <label>安装磁盘预算（字节，须核对空间）<input type="number" min="0" value={diskBudget}
        onChange={e => setDiskBudget(e.target.value)} /></label>
      <button disabled={busy || !diskBudget || (!offline && !confirmed)} onClick={() => act(() => post('/comfyops/install-plans', {
        project_id: projectId, package_ids: [packageId], disk_free: Number(diskBudget), isolated: true, executor: mode,
      }))}>生成选定执行器的隔离计划</button>
    </>}
    {view === 2 && <>
      <p>分类：{registry?.categories.join(' · ')}</p>
      <label>WorkflowDraft JSON<textarea value={draftJson} onChange={e => setDraftJson(e.target.value)} /></label>
      <button disabled={busy} onClick={() => act(() => post('/workflow-capabilities/drafts', JSON.parse(draftJson)))}>登记草案</button>
      <button disabled={busy} onClick={() => act(() => post('/workflow-capabilities/assemble', {
        name: 'Fake 图像模板', template: 'fake-image-v1',
      }))}>组装白名单 Fake 图像模板</button>
      <label>本地执行证据 JSON（instance_id、prompt_id、output_sha256）<textarea value={localEvidence}
        onChange={e => setLocalEvidence(e.target.value)} /></label>
      <details><summary>兼容的旧 H3 Profiles（不更改原图）</summary><pre>{JSON.stringify(registry?.legacy_profiles, null, 2)}</pre></details>
    </>}
    {(view === 3 || view === 4) && <>
      <div className="form-grid">
        <label>角色 / 实体 ID<input value={entity} onChange={e => setEntity(e.target.value)} /></label>
        <label>资产权利 ID<input value={rightsId} onChange={e => setRightsId(e.target.value)} /></label>
        <label>说明 / 生成提示<textarea value={prompt} onChange={e => setPrompt(e.target.value)} /></label>
      </div>
      <details><summary>登记权利与授权</summary>
        <label>授权范围<select value={rightsScope} onChange={e => setRightsScope(e.target.value)}>
          <option value="asset">素材使用</option><option value="voice_design">原创音色</option><option value="voice_clone">参考声音克隆（绑定下方音频候选）</option>
        </select></label>
        <label>许可证 / 权利依据<input value={license} onChange={e => setLicense(e.target.value)} /></label>
        <button disabled={busy} onClick={() => act(async () => {
          const r = await post<Row>('/preparation/rights', { project_id: projectId, scope: rightsScope,
            license_id: license, purpose: prompt, evidence,
            ...(rightsScope === 'voice_clone' ? { reference_candidate_id: referenceId } : {}) });
          return approve(r, 'rights_approve', `/preparation/rights/${r.id}/approve`);
        })}>登记并由操作员确认权利</button>
      </details>
    </>}
    {view === 3 && <>
      <label>资产类别<select value={family} onChange={e => setFamily(e.target.value)}>
        {['character', 'costume', 'scene', 'prop', 'keyframe', 'storyboard', 'action', 'voice', 'dialogue', 'sfx', 'music', 'subtitle']
          .map(f => <option key={f}>{f}</option>)}</select></label>
      <label>已模拟执行的 Workflow ID<input value={workflowId} onChange={e => setWorkflowId(e.target.value)} /></label>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/candidate-assets/generate`, {
        family, entity_id: entity, rights_id: rightsId, prompt, workflow_id: workflowId,
      }))}>生成 Fake 候选（合成测试图）</button>
      <label>导入已有素材<input type="file" disabled={busy} onChange={e => {
        const file = e.target.files?.[0]; if (!file) return;
        const form = new FormData(); form.append('file', file); form.append('family', family);
        form.append('entity_id', entity); form.append('rights_id', rightsId);
        act(() => api(`/projects/${projectId}/candidate-assets/import`, { method: 'POST', body: form }));
      }} /></label>
    </>}
    {view === 4 && <>
      <label>音色来源<select value={voiceSource} onChange={e => setVoiceSource(e.target.value)}>
        <option value="ORIGINAL_DESIGN">原创设计</option><option value="AUTHORIZED_CLONE">已授权录音克隆</option>
        <option value="USER_IMPORT">已有音频导入</option></select></label>
      <label>参考音频候选 ID<input value={referenceId} onChange={e => setReferenceId(e.target.value)} /></label>
      <label>音色设计权利 ID<input value={voiceRights} onChange={e => setVoiceRights(e.target.value)} /></label>
      <label>情绪<input value={emotion} onChange={e => setEmotion(e.target.value)} /></label>
      <label>语速（0.5–2）<input type="number" min="0.5" max="2" step="0.1" value={speed} onChange={e => setSpeed(Number(e.target.value))} /></label>
      <label>停顿秒数（0–3）<input type="number" min="0" max="3" step="0.1" value={pause} onChange={e => setPause(Number(e.target.value))} /></label>
      <label>普通话对白 / 试听文本<textarea value={text} onChange={e => setText(e.target.value)} /></label>
      <button disabled={busy} onClick={() => act(async () => {
        const body = { character: entity, description: prompt, text, rights_id: voiceRights, asset_rights_id: rightsId,
          source: voiceSource, reference_id: referenceId || null, supersedes_voice_id: null };
        let execution_decision;
        if (voiceSource === 'AUTHORIZED_CLONE') {
          const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', {
            action: 'voice_execute', target_id: voiceRights, context: body,
          }, true);
          execution_decision = { grant_id: grant.grant_id, snapshot: grant.snapshot, evidence };
        }
        return post(`/projects/${projectId}/voice-profiles/${voiceSource === 'AUTHORIZED_CLONE' ? 'clone' : voiceSource === 'USER_IMPORT' ? 'import' : 'design'}`,
          { ...body, ...(execution_decision ? { execution_decision } : {}) }, voiceSource === 'AUTHORIZED_CLONE');
      })}>制作候选试听（默认 Fake 测试音调）</button>
    </>}
    {view === 5 && <>
      <label>已导入剧本 Document ID<input value={documentId} onChange={e => setDocumentId(e.target.value)} /></label>
      <label>导入剧本<input type="file" accept=".txt,.md,.json,.docx,.pdf" onChange={e => {
        const file = e.target.files?.[0]; if (!file) return; const form = new FormData(); form.append('file', file);
        act(async () => { const d = await api<{ id: string }>(`/projects/${projectId}/documents`, { method: 'POST', body: form });
          setDocumentId(d.id); return d; });
      }} /></label>
      <p>剧本按“角色：对白”“场景：名称”“道具：名称”提取需求；可通过 API 补充关键需求。</p>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/preproduction/analyze`, { document_id: documentId }))}>分析资产与任务依赖</button>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/preproduction/analyze`, {
        document_id: documentId, create_revision: true,
      }))}>建立新的预检版本（保留历史冻结清单）</button>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/preproduction/readiness`, { offline }))}>运行开机预检</button>
      <label>已批准资产使用权 ID<input value={rightsId} onChange={e => setRightsId(e.target.value)} /></label>
      <label>原创音色权利 ID（可选）<input value={voiceRights} onChange={e => setVoiceRights(e.target.value)} /></label>
      <label>已验证图像工作流 ID<input value={workflowId} onChange={e => setWorkflowId(e.target.value)} /></label>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/preproduction/run`, {
        asset_rights_id: rightsId, workflow_id: workflowId,
        ...(voiceRights ? { voice_rights_id: voiceRights } : {}),
      }))}>按依赖自动制作缺失候选（等待人工晋升）</button>
      <button disabled={busy} onClick={() => act(async () => {
        const r = await post<Row>(`/projects/${projectId}/preproduction/analyze`, { document_id: documentId });
        return approve(r, 'manifest_freeze', `/projects/${projectId}/preproduction/freeze`);
      })}>操作员冻结选定模式清单</button>
    </>}
    {rowView === view && rows.map(row => <article className="card" key={row.id}>
      <strong>{String(row.payload.family ?? row.payload.character ?? row.payload.name ?? row.payload.source ?? row.id)} · {row.status}</strong>
      <p>ID {row.id} · SHA256 {row.sha256}</p>
      {view === 1 && row.status === 'AWAITING_APPROVAL' && <button disabled={busy}
        onClick={() => act(() => approve(row, 'install', `/comfyops/install-plans/${row.id}/approve`))}>批准此哈希计划</button>}
      {view === 1 && row.status === 'APPROVED' && row.payload.executor === 'FAKE_ONLY' && <button disabled={busy} onClick={() => act(() =>
        post(`/comfyops/install-plans/${row.id}/execute`, { executor: 'fake', plan_hash: row.sha256 }, true))}>模拟执行（无系统变更）</button>}
      {view === 2 && row.status === 'DRAFT' && <button disabled={busy} onClick={() => act(() =>
        post(`/workflow-capabilities/${row.id}/validate`, { object_info: { ShineFake: { input: { required: { prompt: ['STRING'] } } } } }))}>使用 Fake 节点目录验证</button>}
      {view === 2 && row.status === 'STATIC_VALIDATED' && <button disabled={busy} onClick={() => act(() =>
        post(`/workflow-capabilities/${row.id}/fake-execute`, {}))}>Fake HTTP 执行契约</button>}
      {view === 2 && !offline && <>
        <button disabled={busy || !confirmed} onClick={() => act(async () => {
          const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
            { action: 'workflow_validate', target_id: row.id }, true);
          return post(`/workflow-capabilities/${row.id}/local-execution-evidence`, {
            ...JSON.parse(localEvidence), grant_id: grant.grant_id, snapshot: grant.snapshot, offline: false, evidence,
          }, true);
        })}>提交并核验本地 History 证据</button>
        {row.status === 'REAL_TESTED' && <button disabled={busy || !confirmed} onClick={() => act(() =>
          approve(row, 'workflow_publish', `/workflow-capabilities/${row.id}/publish`))}>操作员发布此版本</button>}
      </>}
      {view === 3 && row.status !== 'PROMOTED'  && <>
        <button disabled={busy} onClick={() => act(() => post(`/candidate-assets/${row.id}/qc`, {}))}>候选 QC</button>
        <button disabled={busy || row.status !== 'READY_FOR_REVIEW'} onClick={() => act(() =>
          approve(row, 'promote', `/candidate-assets/${row.id}/promote`))}>按选定模式人工晋升</button>
      </>}
      {view === 4 && <>
        <audio controls src={`${API}/candidate-assets/${String(row.payload.candidate_id)}/media`} />
        <p>先在全资产工厂 QC 并晋升样音，再试听冻结。</p>
        {row.status === 'PREVIEW' && <button disabled={busy} onClick={() => act(() =>
          approve(row, 'voice_freeze', `/voice-profiles/${row.id}/approve-and-freeze`))}>操作员确认试听并冻结</button>}
        {row.status === 'FROZEN' && <button disabled={busy} onClick={() => act(() => post(`/voice-profiles/${row.id}/dialogue`, {
          character: row.payload.character, text, voice_hash: row.sha256, emotion, speed, pause,
        }))}>生成对白 / ASR / 逐字对齐 / SRT / ASS</button>}
      </>}
      <details><summary>来源、风险、QC 与完整快照</summary><pre>{JSON.stringify(row.payload, null, 2)}</pre></details>
    </article>)}
    {view === 4 && result !== null && typeof result === 'object' && 'voice_id' in result && 'id' in result &&
      <p><a href={`${API}/voice-renders/${String(result.id)}/subtitles?format=srt`} download>下载 SRT</a>
        {' · '}<a href={`${API}/voice-renders/${String(result.id)}/subtitles?format=ass`} download>下载 ASS</a></p>}
    {result !== null && <details open><summary>后端结果 / 阻滞原因</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
  </section>;
}
