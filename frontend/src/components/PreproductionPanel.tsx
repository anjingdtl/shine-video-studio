import { useEffect, useState } from 'react';
import { api, json, API } from '../api';

type Row = { id: string; status: string; sha256: string; payload: Record<string, unknown>; formal_asset_id?: string };
type Registry = { categories: string[]; revisions: Row[]; legacy_profiles: unknown[] };
const views = ['环境中心', '模型与节点', '工作流能力', '全资产工厂', 'Voice Studio', '开机前预检'];

export function PreproductionPanel({ projectId, onRefresh }: { projectId: string; onRefresh: () => Promise<void> }) {
  const [view, setView] = useState(0);
  const [token, setToken] = useState('');
  const [rows, setRows] = useState<Row[]>([]);
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
    if (view === 0) setRows(await api<Row[]>('/comfyops/inventory'));
    if (view === 1) {
      setRows(await api<Row[]>('/comfyops/install-plans'));
      setResult({ models: await api('/comfyops/models'), nodes: await api('/comfyops/nodes') });
    }
    if (view === 2) { const r = await api<Registry>('/workflow-capabilities'); setRegistry(r); setRows(r.revisions); }
    if (view === 3) setRows(await api<Row[]>(`/projects/${projectId}/candidate-assets`));
    if (view === 4) setRows(await api<Row[]>(`/projects/${projectId}/voice-profiles`));
    if (view === 5) { setRows([]); setResult(await api(`/projects/${projectId}/preproduction/requirements`)); }
  }
  useEffect(() => { setResult(null); setError(''); load().catch(e => setError(e.message)); }, [view, projectId]);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { const r = await fn(); await load(); setResult(r); await onRefresh(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function approve(row: Row, action: string, path: string) {
    if (!evidence.trim()) throw new Error('请填写核验、权利和试听证据。');
    const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
      { action, target_id: row.id }, true);
    return post(path, { ...grant, offline: true, evidence }, true);
  }
  return <section className="card" id="preproduction">
    <div className="section-heading"><h2>制片准备 · V1.3</h2><strong>MOCK / 离线验证，真实 GPU 待本地验收</strong></div>
    <nav className="button-row">{views.map((name, i) => <button key={name} onClick={() => setView(i)}
      disabled={busy} aria-pressed={view === i}>{name}</button>)}</nav>
    <p>候选晋升、音色冻结与安装审批使用服务器验证的操作员身份；凭据仅保留在当前页面内存。</p>
    <div className="form-grid">
      <label>操作员凭据<input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} /></label>
      <label>人工核验 / 试听 / 权利证据<input value={evidence} onChange={e => setEvidence(e.target.value)} /></label>
    </div>
    <button disabled={busy} onClick={() => act(load)}>刷新后端状态</button>
    {error && <p role="alert" className="error-text">{error}</p>}
    {view === 0 && <>
      <p>当前扫描器为 FakeWindowsHostProbe。模拟 16GB GPU / 32GB RAM，不代表已扫描本机。</p>
      <button disabled={busy} onClick={() => act(() => post('/comfyops/inventory/refresh', {}))}>模拟只读环境检测</button>
      <button disabled={busy} onClick={() => act(() => api('/comfyops/resource-status'))}>资源与租约</button>
    </>}
    {view === 1 && <>
      <label>模型 / 节点 PackageSpec JSON<textarea value={packageJson} onChange={e => setPackageJson(e.target.value)}
        placeholder="包含固定 revision、文件 URL / size / SHA256、license_id、usage、dependencies" /></label>
      <button disabled={busy} onClick={() => act(() => post('/comfyops/packages', JSON.parse(packageJson)))}>登记来源与版本</button>
      <label>Package ID<input value={packageId} onChange={e => setPackageId(e.target.value)} /></label>
      <button disabled={busy} onClick={() => act(() => post('/comfyops/install-plans', {
        project_id: projectId, package_ids: [packageId], disk_free: 200 * 1024 ** 3, isolated: true,
      }))}>生成隔离 Dry Run 计划（磁盘预算为模拟值）</button>
    </>}
    {view === 2 && <>
      <p>分类：{registry?.categories.join(' · ')}</p>
      <label>WorkflowDraft JSON<textarea value={draftJson} onChange={e => setDraftJson(e.target.value)} /></label>
      <button disabled={busy} onClick={() => act(() => post('/workflow-capabilities/drafts', JSON.parse(draftJson)))}>登记草案</button>
      <button disabled={busy} onClick={() => act(() => post('/workflow-capabilities/assemble', {
        name: 'Fake 图像模板', template: 'fake-image-v1',
      }))}>组装白名单 Fake 图像模板</button>
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
          <option value="asset">素材使用</option><option value="voice_design">原创音色</option>
        </select></label>
        <label>许可证 / 权利依据<input value={license} onChange={e => setLicense(e.target.value)} /></label>
        <button disabled={busy} onClick={() => act(async () => {
          const r = await post<Row>('/preparation/rights', { project_id: projectId, scope: rightsScope,
            license_id: license, purpose: prompt, evidence });
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
      <label>音色设计权利 ID<input value={voiceRights} onChange={e => setVoiceRights(e.target.value)} /></label>
      <label>普通话对白 / 试听文本<textarea value={text} onChange={e => setText(e.target.value)} /></label>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/voice-profiles/design`, {
        character: entity, description: prompt, text, rights_id: voiceRights, asset_rights_id: rightsId,
      }))}>原创音色 Fake 试听（测试音调）</button>
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
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/preproduction/readiness`, { offline: true }))}>运行开机预检</button>
      <button disabled={busy} onClick={() => act(async () => {
        const r = await post<Row>(`/projects/${projectId}/preproduction/analyze`, { document_id: documentId });
        return approve(r, 'manifest_freeze', `/projects/${projectId}/preproduction/freeze`);
      })}>操作员冻结离线清单</button>
    </>}
    {rows.map(row => <article className="card" key={row.id}>
      <strong>{String(row.payload.family ?? row.payload.character ?? row.payload.name ?? row.payload.source ?? row.id)} · {row.status}</strong>
      <p>ID {row.id} · SHA256 {row.sha256}</p>
      {view === 1 && row.status === 'AWAITING_APPROVAL' && <button disabled={busy}
        onClick={() => act(() => approve(row, 'install', `/comfyops/install-plans/${row.id}/approve`))}>批准此哈希计划</button>}
      {view === 1 && row.status === 'APPROVED' && <button disabled={busy} onClick={() => act(() =>
        post(`/comfyops/install-plans/${row.id}/execute`, { executor: 'fake', plan_hash: row.sha256 }, true))}>模拟执行（无系统变更）</button>}
      {view === 2 && row.status === 'DRAFT' && <button disabled={busy} onClick={() => act(() =>
        post(`/workflow-capabilities/${row.id}/validate`, { object_info: { ShineFake: { input: { required: { prompt: ['STRING'] } } } } }))}>使用 Fake 节点目录验证</button>}
      {view === 2 && row.status === 'STATIC_VALIDATED' && <button disabled={busy} onClick={() => act(() =>
        post(`/workflow-capabilities/${row.id}/fake-execute`, {}))}>Fake HTTP 执行契约</button>}
      {view === 3 && row.status !== 'PROMOTED' && <>
        <button disabled={busy} onClick={() => act(() => post(`/candidate-assets/${row.id}/qc`, {}))}>候选 QC</button>
        <button disabled={busy || row.status !== 'READY_FOR_REVIEW'} onClick={() => act(() =>
          approve(row, 'promote', `/candidate-assets/${row.id}/promote`))}>人工晋升（仅离线清单）</button>
      </>}
      {view === 4 && <>
        <audio controls src={`${API}/candidate-assets/${String(row.payload.candidate_id)}/media`} />
        <p>先在全资产工厂 QC 并晋升样音，再试听冻结。</p>
        {row.status === 'PREVIEW' && <button disabled={busy} onClick={() => act(() =>
          approve(row, 'voice_freeze', `/voice-profiles/${row.id}/approve-and-freeze`))}>操作员确认试听并冻结</button>}
        {row.status === 'FROZEN' && <button disabled={busy} onClick={() => act(() => post(`/voice-profiles/${row.id}/dialogue`, {
          character: row.payload.character, text, voice_hash: row.sha256,
        }))}>生成对白 / ASR / 逐字对齐 / SRT / ASS</button>}
      </>}
      <details><summary>来源、风险、QC 与完整快照</summary><pre>{JSON.stringify(row.payload, null, 2)}</pre></details>
    </article>)}
    {result !== null && <details open><summary>后端结果 / 阻滞原因</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
  </section>;
}
