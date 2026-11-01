import { useEffect, useRef, useState } from 'react';
import { api, json, API } from '../api';
import { label, prepRowLabels } from '../constants';

type Row = { id: string; status: string; sha256: string; payload: Record<string, unknown>; project_id?: string; formal_asset_id?: string; trace?: Record<string, unknown> };
type Registry = { categories: string[]; revisions: Row[]; legacy_profiles: unknown[] };
const views = ['环境中心', '模型与节点', '工作流能力', '全资产工厂', 'Voice Studio', '开机前预检'];

export function PreproductionPanel({ projectId, onRefresh, reviewScope }: {
  projectId: string; onRefresh: () => Promise<void>; reviewScope: 'basic' | 'full';
}) {
  const [view, setView] = useState(0);
  const [mode, setMode] = useState('fake');
  const [confirmed, setConfirmed] = useState(false);
  const [diskBudget, setDiskBudget] = useState('');
  const [localEvidence, setLocalEvidence] = useState('{}');
  const offline = mode === 'fake';
  const [rows, setRows] = useState<Row[]>([]);
  const [rowView, setRowView] = useState(-1);
  const activeView = useRef(view);
  activeView.current = view;
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  const [installAttempts, setInstallAttempts] = useState<Record<string, Row[]>>({});
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
  const [scopeJson, setScopeJson] = useState('{}');
  const [scopeHash, setScopeHash] = useState('');
  const [instanceJson, setInstanceJson] = useState('{"name":"studio","action":"CREATE","port":8190,"source_package_id":"","package_ids":[]}');
  const [instanceId, setInstanceId] = useState('');
  const [backupJson, setBackupJson] = useState('{"instance_id":"","root":""}');
  const [callRecords, setCallRecords] = useState<Row[]>([]);
  const [releaseProfile, setReleaseProfile] = useState('');
  const [attemptRecords, setAttemptRecords] = useState<Row[]>([]);
  const [resourceJson, setResourceJson] = useState('{"id":"","owner":""}');
  const [relativePath, setRelativePath] = useState('');
  const [templateId, setTemplateId] = useState('fake-image-v1');
  const [preview, setPreview] = useState(false);
  const [dialogueListened, setDialogueListened] = useState(false);
  const [requirementsJson, setRequirementsJson] = useState('[]');
  const headers = () => ({ 'X-Shine-Local-Action': '1' });
  const post = <T,>(path: string, body: unknown, privileged = false) => api<T>(path, {
    ...json('POST', body), headers: { 'Content-Type': 'application/json', ...(privileged ? headers() : {}) },
  });
  async function load() {
    const requestedView = view;
    const requestedProject = projectId;
    let nextRows: Row[] = [];
    let nextResult: unknown = undefined;
    let nextRegistry: Registry | null = null;
    if (view === 0) nextRows = await api<Row[]>('/comfyops/inventory');
    if (view === 1) {
      const [plans, models, nodes] = await Promise.all([
        api<Row[]>(`/comfyops/install-plans?project_id=${projectId}`), api('/comfyops/models'), api('/comfyops/nodes'),
      ]);
      nextRows = plans; nextResult = { models, nodes };
    }
    if (view === 2) { nextRegistry = await api<Registry>('/workflow-capabilities'); nextRows = nextRegistry.revisions; }
    if (view === 3) nextRows = await api<Row[]>(`/projects/${projectId}/candidate-assets`);
    if (view === 4) nextRows = await api<Row[]>(`/projects/${projectId}/voice-profiles`);
    if (view === 5) nextResult = await api(`/projects/${projectId}/preproduction/requirements`);
    if (activeView.current !== requestedView || activeProject.current !== requestedProject) return;
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
    return post(path, { grant_id: grant.grant_id, snapshot: grant.snapshot, offline, listened: action === 'voice_freeze' || (action === 'promote' && dialogueListened), evidence }, true);
  }
  return <section className="card" id="preproduction">
    <div className="section-heading"><h2>制片准备 · V1.3</h2><strong>当前模式：{offline ? '模拟演练（不落真数据）' : '本地真跑'} · 结果真伪以后端返回为准</strong></div>
    <p className="prep-intro">普通素材在上方上传并确认即可。首次装机请到「新机设置」点击检测；候选素材、音色制作与故障维护在下方按需展开。</p>
    <details><summary>展开高级准备（候选素材、音色与维护）</summary>
    <nav className="button-row">{views.map((name, i) => <button key={name} onClick={() => setView(i)}
      disabled={busy} aria-pressed={view === i}>{name}</button>)}</nav>
    <p>本机操作无需密码。候选晋升、音色冻结与安装仍由你点击确认。</p>
    <div className="form-grid">
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
      <button disabled={busy} onClick={() => act(async () => {
        const context = await api<{ roots: string[]; ports: number[]; configuration_hash: string }>('/comfyops/execution-context', { headers: headers() });
        setScopeHash(context.configuration_hash); setScopeJson(JSON.stringify({ roots: context.roots, ports: context.ports }, null, 2)); return context;
      })}>读取执行器与授权扫描范围</button>
      <label>选择扫描范围 JSON（仅可缩小管理员白名单）<textarea value={scopeJson} onChange={e => setScopeJson(e.target.value)} /></label>
      <button disabled={busy || !scopeHash} onClick={() => act(async () => {
        const context = { project_id: projectId, ...JSON.parse(scopeJson), previous_hash: scopeHash };
        const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', { action: 'scope_configure', target_id: projectId, context }, true);
        const updated = await post<{ configuration_hash: string }>('/comfyops/scope', { ...context, grant_id: grant.grant_id, snapshot: grant.snapshot }, true);
        setScopeHash(updated.configuration_hash); return updated;
      })}>操作员保存受限扫描范围</button>
      <label>隔离实例操作（CREATE / INSTALL_NODE / BIND_MODELS / START / STOP / SNAPSHOT / RESTORE / ROLLBACK）
        <textarea value={instanceJson} onChange={e => setInstanceJson(e.target.value)} /></label>
      <button disabled={busy || (!offline && !confirmed)} onClick={() => act(() => post('/comfyops/instance-plans', {
        ...JSON.parse(instanceJson), project_id: projectId, mode, risk_confirmed: confirmed || offline,
      }, true))}>生成实例操作计划（到模型与节点中心审批执行）</button>
      <label>现有实例只读备份 JSON（instance_id / root）<textarea value={backupJson} onChange={e => setBackupJson(e.target.value)} /></label>
      <button disabled={busy || (!offline && !confirmed)} onClick={() => act(() => post('/comfyops/backup-plans', {
        ...JSON.parse(backupJson), project_id: projectId, mode, risk_confirmed: confirmed || offline,
      }, true))}>生成现有实例只读备份计划</button>
      <label>受管实例 ID<input value={instanceId} onChange={e => setInstanceId(e.target.value)} /></label>
      <button disabled={busy || !instanceId} onClick={() => act(() => api(`/comfyops/instances/${instanceId}/health`, { headers: headers() }))}>核验实例健康与队列</button>
      <button disabled={busy || !instanceId} onClick={() => act(async () => { const records = await api<Row[]>(`/comfyops/instances/${instanceId}/attempts`, { headers: headers() }); setAttemptRecords(records); return records; })}>实例操作 / 快照 / 失败对账记录</button>
    </>}
    {view === 0 && <>
      <label>资源释放对账 JSON（id / owner）<textarea value={resourceJson} onChange={e => setResourceJson(e.target.value)} /></label>
      <button disabled={busy || !evidence} onClick={() => act(async () => {
        const resource = JSON.parse(resourceJson);
        const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', { action: 'resource_reconcile', target_id: resource.id }, true);
        return post(`/comfyops/resource-reservations/${resource.id}/release`, { owner: resource.owner, reconciled: true,
          proof_decision: { grant_id: grant.grant_id, snapshot: grant.snapshot, evidence } }, true);
      })}>核验实际卸载并对账释放（失联模型须先单独对账）</button>
      <button disabled={busy} onClick={() => act(async () => { const records = await api<Row[]>(`/projects/${projectId}/preparation/model-calls`, { headers: headers() }); setCallRecords(records); return records; })}>读取模型调用与失联记录</button>
      <label>旧审片调用的释放 Provider ID（只生成提案，仍需人工对账）<input aria-label="旧审片释放 Provider ID" value={releaseProfile} onChange={e => setReleaseProfile(e.target.value)} /></label>
      {callRecords.filter(r => ['RUNNING', 'NEEDS_REVIEW', 'RECONCILING'].includes(r.status)).map(r => <article key={r.id}>
        <p>模型调用 {r.id.slice(0, 8)}… · {label(prepRowLabels, r.status)}</p>
        {Boolean(r.trace?.review_provider) && <pre>{JSON.stringify(r.trace?.review_provider, null, 2)}</pre>}
        {!r.trace?.review_provider && ['VLM', 'ASR'].includes(String(r.trace?.kind)) && <button disabled={busy || !releaseProfile} onClick={() => act(async () => {
          const proposed = await post<Row>(`/preparation/model-calls/${r.id}/release-provider-proposal`, { provider_profile_id: releaseProfile });
          setCallRecords(previous => previous.map(row => row.id === r.id ? proposed : row)); return proposed;
        })}>固定旧审片释放目标（不执行）</button>}
        <button disabled={busy || !evidence} onClick={() => act(() => approve(r, 'model_reconcile', `/preparation/model-calls/${r.id}/abandon`))}>核验队列与释放后放弃丢失结果（不重投）</button>
      </article>)}
      {attemptRecords.filter(r => r.status === 'SUBMIT_INTENT').map(r => <article key={r.id}>
        <p>中断实例操作 {r.id}</p>
        <button disabled={busy || !evidence} onClick={() => act(async () => {
          const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', { action: 'instance_reconcile', target_id: r.id }, true);
          return post(`/comfyops/instance-attempts/${r.id}/reconcile`, { grant_id: grant.grant_id, snapshot: grant.snapshot, evidence }, true);
        })}>核验 owner 并对账（回滚需另批 ROLLBACK 计划）</button>
      </article>)}
    </>}
    {view === 1 && <>
      <label>模型 / 节点 PackageSpec JSON<textarea value={packageJson} onChange={e => setPackageJson(e.target.value)}
        placeholder="包含固定 revision、文件 URL / size / SHA256、license_id、usage、dependencies" /></label>
      <button disabled={busy} onClick={() => act(() => post('/comfyops/packages', JSON.parse(packageJson)))}>登记来源与版本</button>
      <label>Package ID<input value={packageId} onChange={e => setPackageId(e.target.value)} /></label>
      <label>离线文件在固定清单中的相对路径<input value={relativePath} onChange={e => setRelativePath(e.target.value)} /></label>
      <label>导入固定摘要的离线模型 / 节点 / wheel<input type="file" disabled={busy} onChange={e => {
        const file = e.target.files?.[0]; if (!file) return;
        const form = new FormData(); form.append('relative_path', relativePath); form.append('file', file);
        act(() => api(`/comfyops/packages/${packageId}/offline-files`, { method: 'POST', headers: headers(), body: form }));
      }} /></label>
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
      <label>受审计模板<select value={templateId} onChange={e => setTemplateId(e.target.value)}>
        {['fake-image-v1', 'fake-voice-v1', 'h3-ref2va-2img', 'h3-ref2va-2img-1aud'].map(t => <option key={t}>{t}</option>)}
      </select></label>
      <button disabled={busy} onClick={() => act(() => post('/workflow-capabilities/assemble', {
        name: templateId + ' draft', template: templateId,
      }))}>组装受审计草案（不自动发布）</button>
      <p>独立审片 / ASR 使用已完成调用的 samples；H3 使用 job_id 与 graph_sha256；其它 ComfyUI 能力使用 instance_id、prompt_id、output_sha256。</p>
      <label>本地执行证据 JSON<textarea aria-label="本地执行证据 JSON" value={localEvidence}
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
      <label><input type="checkbox" checked={dialogueListened} onChange={e => setDialogueListened(e.target.checked)} />我已逐句试听并核验待人工复核的对白、角色和语音</label>
      <label>资产类别<select value={family} onChange={e => setFamily(e.target.value)}>
        {['character', 'costume', 'scene', 'prop', 'keyframe', 'storyboard', 'action', 'voice', 'dialogue', 'sfx', 'music', 'subtitle']
          .map(f => <option key={f}>{f}</option>)}</select></label>
      <label>已模拟执行的 Workflow ID<input value={workflowId} onChange={e => setWorkflowId(e.target.value)} /></label>
      {!offline && <label><input type="checkbox" checked={preview} onChange={e => setPreview(e.target.checked)} />授权此工作流的本地候选预览（生产发布仍需独立证据）</label>}
      <button disabled={busy || (!offline && !confirmed)} onClick={() => act(async () => {
        let preview_decision;
        if (!offline && preview) {
          const g = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', { action: 'workflow_preview', target_id: workflowId }, true);
          preview_decision = { grant_id: g.grant_id, snapshot: g.snapshot, offline: false, evidence };
        }
        return post(`/projects/${projectId}/candidate-assets/generate`, {
          family, entity_id: entity, rights_id: rightsId, prompt, workflow_id: workflowId, execution_mode: mode,
          ...(preview_decision ? { preview_decision } : {}),
        }, !offline);
      })}>按所选执行器制作候选</button>
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
          source: voiceSource, reference_id: referenceId || null, supersedes_voice_id: null, execution_mode: mode };
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
      <p>支持结构化 scenes / shots / beats、服装、道具、逐句对白与依赖；文本注释也可提取。关键需求保持阻断。</p>
      <label>补充关键需求 JSON<textarea value={requirementsJson} onChange={e => setRequirementsJson(e.target.value)} /></label>
      <p>预检使用当前选择的{reviewScope === 'basic' ? '基础自审' : '完整质量门禁'}范围。</p>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/preproduction/analyze`, { document_id: documentId, requirements: JSON.parse(requirementsJson), review_scope: reviewScope }))}>分析资产与任务依赖</button>
      <button disabled={busy} onClick={() => act(() => post(`/projects/${projectId}/preproduction/analyze`, {
        document_id: documentId, create_revision: true, review_scope: reviewScope,
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
        const r = await post<Row>(`/projects/${projectId}/preproduction/analyze`, { document_id: documentId, review_scope: reviewScope });
        return approve(r, 'manifest_freeze', `/projects/${projectId}/preproduction/freeze`);
      })}>操作员冻结选定模式清单</button>
    </>}
    {rowView === view && rows.filter(row => view !== 1 || row.project_id === projectId).map(row => <article className="card" key={row.id}>
      <strong>{String(row.payload.family ?? row.payload.character ?? row.payload.name ?? row.payload.source ?? row.id)} · {label(prepRowLabels, row.status)}</strong>
      <p>编号 {row.id.slice(0, 8)}… · 校验指纹 {row.sha256.slice(0, 12)}…（指纹一致 = 内容没被改动）</p>
      {view === 1 && row.status === 'AWAITING_APPROVAL' && <button disabled={busy}
        onClick={() => act(() => approve(row, 'install', `/comfyops/install-plans/${row.id}/approve`))}>批准此哈希计划</button>}
      {view === 1 && row.payload.executor === 'LOCAL_ISOLATED' && !row.payload.kind && <>
        <button disabled={busy} onClick={() => act(async () => {
          const attempts = await api<Row[]>(`/comfyops/install-plans/${row.id}/attempts`);
          setInstallAttempts(previous => ({ ...previous, [row.id]: attempts }));
          return attempts;
        })}>查看安装尝试</button>
        {installAttempts[row.id]?.length === 0 && <p>此计划尚未开始安装。</p>}
        {installAttempts[row.id]?.map(attempt => <div key={attempt.id}>
          <p>安装尝试 {attempt.id.slice(0, 8)}… · {label(prepRowLabels, attempt.status)}{attempt.payload.error_type ? ` · ${String(attempt.payload.error_type)}` : ''}</p>
          {attempt.status === 'FAILED_RECONCILE_REQUIRED' && <button disabled={busy || offline || !confirmed}
            onClick={() => act(async () => {
              const reconciled = await approve(attempt, 'install_reconcile', `/comfyops/install-attempts/${attempt.id}/reconcile`);
              const attempts = await api<Row[]>(`/comfyops/install-plans/${row.id}/attempts`);
              setInstallAttempts(previous => ({ ...previous, [row.id]: attempts }));
              return reconciled;
            })}>核对安装失败（不重试）</button>}
        </div>)}
        {row.status === 'RECONCILED_NO_RETRY' && <>
          <p>旧尝试已结束。继续安装须新建并批准计划。</p>
          <button disabled={busy || offline || !confirmed || !diskBudget} onClick={() => act(() => post('/comfyops/install-plans', {
            project_id: projectId,
            package_ids: (row.payload.packages as { id: string }[]).map(p => p.id),
            disk_free: Number(diskBudget), isolated: true, executor: 'local',
          }))}>从原清单新建待批准计划</button>
        </>}
      </>}
      {view === 1 && row.status === 'APPROVED' && row.payload.executor === 'FAKE_ONLY' && <button disabled={busy} onClick={() => act(() =>
        post(row.payload.kind === 'INSTANCE_BACKUP' ? `/comfyops/backup-plans/${row.id}/execute` : row.payload.kind === 'INSTANCE_LIFECYCLE' ? `/comfyops/instance-plans/${row.id}/execute` : `/comfyops/install-plans/${row.id}/execute`, ['INSTANCE_LIFECYCLE', 'INSTANCE_BACKUP'].includes(String(row.payload.kind)) ? { plan_hash: row.sha256 } : { executor: 'fake', plan_hash: row.sha256 }, true))}>演练执行（不改动系统）</button>}
      {view === 1 && row.status === 'APPROVED' && row.payload.executor === 'LOCAL_ISOLATED' && ['INSTANCE_LIFECYCLE', 'INSTANCE_BACKUP'].includes(String(row.payload.kind)) &&
        <button disabled={busy || !confirmed || offline} onClick={() => act(() => post(`/comfyops/${row.payload.kind === 'INSTANCE_BACKUP' ? 'backup-plans' : 'instance-plans'}/${row.id}/execute`, { plan_hash: row.sha256 }, true))}>执行已批准的本地实例计划（须服务器显式启用）</button>}
      {view === 1 && row.status === 'APPROVED' && row.payload.executor === 'LOCAL_ISOLATED' && !['INSTANCE_LIFECYCLE', 'INSTANCE_BACKUP'].includes(String(row.payload.kind)) &&
        <button disabled={busy || !confirmed || offline} onClick={() => act(() => post(`/comfyops/install-plans/${row.id}/execute`, { plan_hash: row.sha256, executor: 'local' }, true))}>执行已批准的本地包安装（服务器须显式启用）</button>}
      {view === 2 && ['PRODUCTION_APPROVED', 'FAKE_EXECUTED', 'REAL_TESTED'].includes(row.status) && <button disabled={busy} onClick={() => act(() => approve(row, 'workflow_deprecate', `/workflow-capabilities/${row.id}/deprecate`))}>操作员弃用此版本（保留历史）</button>}
      {view === 2 && !offline && ['DRAFT', 'STATIC_VALIDATED'].includes(row.status) && (row.payload.native_provider ?
        <button disabled={busy} onClick={() => act(() => post(`/workflow-capabilities/${row.id}/validate-native`, {}, true))}>读取独立审片 / ASR 执行器验证</button> : <>
          <label>工作流验证实例 ID<input value={instanceId} onChange={e => setInstanceId(e.target.value)} /></label>
          <button disabled={busy || !instanceId} onClick={() => act(() => post(`/workflow-capabilities/${row.id}/validate-local`, { instance_id: instanceId }, true))}>读取本地节点契约验证</button>
        </>)}
      {view === 2 && !row.payload.native_provider && row.status === 'DRAFT'  && <button disabled={busy} onClick={() => act(() =>
        post(`/workflow-capabilities/${row.id}/validate`, { object_info: { ShineFake: { input: { required: { prompt: ['STRING'] } } } } }))}>离线演练：用内置假节点校验结构</button>}
      {view === 2 && !row.payload.native_provider && row.status === 'STATIC_VALIDATED' && <button disabled={busy} onClick={() => act(() =>
        post(`/workflow-capabilities/${row.id}/fake-execute`, {}))}>离线演练：模拟一次调用（不落真）</button>}
      {view === 2 && !offline && <>
        <button disabled={busy || !confirmed || (Boolean(row.payload.native_provider) && row.status !== 'STATIC_VALIDATED')} onClick={() => act(async () => {
          const payload = JSON.parse(localEvidence);
          if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('执行证据必须是 JSON 对象');
          const native = Boolean(row.payload.native_provider);
          if (native && payload.project_id && payload.project_id !== projectId) throw new Error('校准记录必须属于当前项目');
          const endpoint = native ? 'native-execution-evidence' :
            ('candidate_id' in payload || 'job_id' in payload) ? 'local-evidence' : 'local-execution-evidence';
          const grant = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
            { action: 'workflow_validate', target_id: row.id }, true);
          return post(`/workflow-capabilities/${row.id}/${endpoint}`, {
            ...payload, ...(native ? { project_id: projectId } : {}),
            grant_id: grant.grant_id, snapshot: grant.snapshot, offline: false, evidence,
          }, true);
        })}>{row.payload.native_provider ? '核验已完成的审片 / ASR 校准记录' : '提交并核验本地执行证据'}</button>
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
        {row.status === 'FROZEN' && <button disabled={busy} onClick={() => act(() => (async () => {
          const body = { character: row.payload.character, text, voice_hash: row.sha256, emotion, speed, pause,
            audio_mode: 'VOICE_REFERENCE', execution_mode: mode };
          let execution_decision;
          if (row.payload.source === 'AUTHORIZED_CLONE') {
            const g = await post<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', {
              action: 'voice_execute', target_id: row.payload.rights_id, context: { voice_id: row.id, ...body },
            }, true);
            execution_decision = { grant_id: g.grant_id, snapshot: g.snapshot, evidence };
          }
          return post(`/voice-profiles/${row.id}/dialogue`, { ...body, ...(execution_decision ? { execution_decision } : {}) }, row.payload.source === 'AUTHORIZED_CLONE');
        })())}>生成对白 / ASR / 逐字对齐 / SRT / ASS</button>}
      </>}
      <details><summary>来源、风险、QC 与完整快照</summary><pre>{JSON.stringify(row.payload, null, 2)}</pre></details>
    </article>)}
    {view === 4 && result !== null && typeof result === 'object' && 'voice_id' in result && 'id' in result &&
      <p><a href={`${API}/voice-renders/${String(result.id)}/subtitles?format=srt`} download>下载 SRT</a>
        {' · '}<a href={`${API}/voice-renders/${String(result.id)}/subtitles?format=ass`} download>下载 ASS</a></p>}
    {result !== null && <details open><summary>后端结果 / 阻滞原因</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
    </details>
  </section>;
}
