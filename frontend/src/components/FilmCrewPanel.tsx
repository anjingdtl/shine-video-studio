import { useEffect, useState } from 'react';
import { api, json, type ProviderProfile, type Shot } from '../api';
import { failureLabels, label, modeLabels, serviceStatusLabels, statusLabels } from '../constants';

type Role = { role: string; label: string; model: string | null; profile_id: string | null;
  output_kind: string; required_inputs: string[]; permissions: string; tool_allowlist: string[] };
type Artifact = { id: string; kind: string; payload_json: Record<string, unknown>; sha256: string;
  producer_role: string; stale: boolean; approved_by: string | null };
type Run = { id: string; role_id: string; status: string; model_id: string; error_code: string | null;
  rulepack_id: string; trace: Record<string, unknown> };
type RuleHub = { packs: Array<{ id: string; sha256: string; approved_at: string | null; manifest: Record<string, unknown> }>;
  sources: Array<{ path: string; sha256: string; url: string; local_cache_status: string }>;
  rules: Array<{ id: string; origin: string; summary: string; source_path: string; locator: string }>;
  local_workflow_capabilities: Array<{ name: string; mode: string; status: string }> };

export default function FilmCrewPanel({ projectId, profiles, shots, onRefresh }: {
  projectId: string; profiles: ProviderProfile[]; shots: Shot[]; onRefresh: () => Promise<void>;
}) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [hub, setHub] = useState<RuleHub | null>(null);
  const [pin, setPin] = useState('');
  const [role, setRole] = useState('scriptwriter');
  const [tool, setTool] = useState('propose');
  const [brief, setBrief] = useState('');
  const [facts, setFacts] = useState('');
  const [inputs, setInputs] = useState<string[]>([]);
  const [offline, setOffline] = useState(false);
  const [remoteText, setRemoteText] = useState(false);
  const [reviewShotId, setReviewShotId] = useState('');
  const [reconcileCredential, setReconcileCredential] = useState('');
  const [reconcileEvidence, setReconcileEvidence] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [query, setQuery] = useState('');
  const [candidate, setCandidate] = useState('');
  const [result, setResult] = useState<unknown>(null);

  async function reload() {
    const [rs, ars, , h, lock] = await Promise.all([
      api<Role[]>('/film-agents'), api<Artifact[]>(`/projects/${projectId}/film-artifacts`),
      api<Run[]>(`/projects/${projectId}/film-runs`).then(setRuns), api<RuleHub>('/h3-rulebook'),
      api<{ rulepack_id: string | null }>(`/projects/${projectId}/rulepack`),
    ]);
    setRoles(rs); setArtifacts(ars); setHub(h); setPin(lock.rulepack_id ?? '');
  }

  useEffect(() => {
    setInputs([]); setReviewShotId(''); setRemoteText(false);
    setReconcileCredential(''); setReconcileEvidence('');
    reload().catch(e => setMessage(String(e)));
  }, [projectId]);
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setMessage('');
    try { setResult(await action()); await reload(); await onRefresh(); setMessage('操作完成，请查看结果与门禁状态。'); }
    catch (e) { setMessage(String(e)); await reload().catch(() => undefined); }
    finally { setBusy(false); setRemoteText(false); }
  }
  async function abandonRemote(run: Run) {
    if (!window.confirm('仅放弃这次远端未知响应；不重试、不批准质量，也不证明本机模型已卸载。继续？')) {
      setReconcileCredential(''); setReconcileEvidence(''); return;
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (reconcileCredential) headers.Authorization = `Bearer ${reconcileCredential}`;
    try {
      await act(async () => {
        const grant = await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants', {
          ...json('POST', { action: 'model_reconcile', target_id: run.id }), headers,
        });
        return api(`/projects/${projectId}/film-runs/${run.id}/abandon-remote-response`, {
          ...json('POST', { grant_id: grant.grant_id, snapshot: grant.snapshot, evidence: reconcileEvidence }), headers,
        });
      });
    } finally { setReconcileCredential(''); setReconcileEvidence(''); }
  }
  const current = roles.find(r => r.role === role);
  const reviewShot = shots.find(s => s.id === reviewShotId && s.project_id === projectId);
  const filteredRules = hub?.rules.filter(r => JSON.stringify(r).toLowerCase().includes(query.toLowerCase())) ?? [];
  const needsRemoteReconciliation = (run: Run) => run.status === 'NEEDS_REVIEW' || (
    run.status === 'REJECTED' && run.trace.external_outcome !== 'NOT_SENT'
    && ['LLM_TIMEOUT', 'LLM_CONNECTION', 'LLM_SERVER'].includes(run.error_code ?? '')
  );
  return <>
    <section className="panel" id="film-crew">
      <div className="panel-header"><h2>影视制片团队 <small>V1.1</small></h2>
        <button disabled={busy} onClick={() => act(reload)}>刷新运行记录</button></div>
      <p>编剧 → 导演 → 摄影 → 场记 → 规则预检 → 唯一 Worker → QC → 剪辑。所有工种仅产出建议，审批由你操作。</p>
      <div className="crew-grid">{roles.map(r => <article className="settings-block" key={r.role}>
        <h3>{r.label}</h3><p>{r.output_kind}</p><p>{r.permissions}</p>
        <label>角色模型（可共用同一配置）<select disabled={busy} value={r.profile_id ?? ''}
          onChange={e => act(() => api('/agent-routes', json('PUT', { role: r.role, provider_profile_id: e.target.value })))}>
          <option value="" disabled>请配置模型路由</option>
          {profiles.map(p => <option value={p.id} key={p.id}>{p.name} · {p.model}</option>)}
        </select></label><small>{r.model ?? '未配置，不会偷偷换别的模型'} · 最近一次：{label(statusLabels, runs.find(t => t.role_id === r.role)?.status ?? '未运行')}</small>
      </article>)}</div>
      <div className="settings-block">
        <h3>工种交接 / 风格预制建议</h3>
        <p>当前规范锁：{pin || '未冻结（先在下方规范中心选择版本）'}</p>
        <label>执行工种<select value={role} onChange={e => { setRole(e.target.value); setTool('propose'); setRemoteText(false); }}>
          {roles.map(r => <option key={r.role} value={r.role}>{r.label}</option>)}</select></label>
        <label>任务类型<select value={tool} onChange={e => setTool(e.target.value)}>
          {(current?.tool_allowlist ?? ['propose']).map(t => <option key={t} value={t}>{t === 'style_proposal' ? '风格建议（仅草案）' : '专业计划'}</option>)}</select></label>
        <p>所需上游产物：{current?.required_inputs.join('、') || '无'}。在下方勾选准确的版本。</p>
        {role === 'qc_reviewer' && <label>检查镜头输入<select value={reviewShotId} onChange={e => setReviewShotId(e.target.value)}>
          <option value="">选择本项目镜头</option>
          {shots.filter(s => s.project_id === projectId).map(s => <option key={s.id} value={s.id}>{s.name} · 修订 {s.revision}</option>)}
        </select><small>只比较镜头文本与冻结风格；不审查画面、声音或批准影片。</small></label>}
        <label>剧本 / 工作简报<textarea rows={5} value={brief} onChange={e => setBrief(e.target.value)} /></label>
        <label>用户锁定故事事实（每行一条，编剧必须原样保留）<textarea rows={2} value={facts} onChange={e => setFacts(e.target.value)} /></label>
        <label><input type="checkbox" checked={offline} onChange={e => setOffline(e.target.checked)} />
          允许剪辑预览使用 mock / 部分技术验证镜头（不进入正式交付）</label>
        <label><input type="checkbox" checked={remoteText} onChange={e => setRemoteText(e.target.checked)} />
          允许本次工种向所选远端模型发送剧本、风格与资产元数据（不发送媒体字节）</label>
        <button disabled={busy || !pin || !current?.profile_id || (role === 'qc_reviewer' && !reviewShot)} onClick={() => act(() => api(
          `/projects/${projectId}/film-agents/${role}/run`, json('POST', {
            brief, locked_story_facts: facts.split('\n').filter(Boolean), input_revision_ids: role === 'qc_reviewer' ? [] : inputs,
            tool, offline_preview: offline, allow_remote_text: remoteText,
            ...(role === 'qc_reviewer' && reviewShot ? { shot_spec: reviewShot.spec } : {}),
          })))}>运行工种</button>
      </div>
      <h3>版本化产物 / 剪辑决策 EDL</h3>
      {!artifacts.length && <p>尚无产物。先配置模型并执行编剧任务；Mock 配置需注入结构化测试响应，不会伪造专业产物。</p>}
      {artifacts.map(a => <details key={a.id} className="settings-block">
        <summary><input type="checkbox" aria-label={`选择 ${a.kind} ${a.id}`} disabled={a.stale} checked={inputs.includes(a.id)}
          onChange={e => setInputs(v => e.target.checked ? [...v, a.id] : v.filter(id => id !== a.id))} />
          {a.kind} · {a.id.slice(0, 8)} · {a.stale ? '依赖已失效' : a.approved_by ? '用户已应用' : '待用户审查'}</summary>
        <pre>{JSON.stringify(a.payload_json, null, 2)}</pre><small>SHA-256 {a.sha256}</small>
        {a.kind === 'StyleProposal' && <button disabled={busy || a.stale} onClick={() => act(() => api(
          `/projects/${projectId}/film-artifacts/${a.id}/style-draft`, { method: 'POST' }))}>应用为风格草案（仍需人工冻结）</button>}
        {a.kind === 'CameraPlan' && <button disabled={busy || a.stale} onClick={() => act(() => api(
          `/projects/${projectId}/film-artifacts/${a.id}/apply-camera`, json('POST', {
            continuity_artifact_id: artifacts.find(r => inputs.includes(r.id) && r.kind === 'ContinuityReport')?.id ?? '',
          })))}>人工应用摄影方案（需勾选对应场记报告）</button>}
      </details>)}
      <h3>Agent 运行记录</h3>
      {runs.some(needsRemoteReconciliation) &&
        <div className="settings-block">
          <p>远端文本响应丢失时可显式放弃接收，结果仍为未知。仅适用于已结束调用的远端文本工种；本机模型需单独对账释放。个人桌面部署无需口令，本机操作会记录为 local-user；如本机配置了操作员凭据可填写，留空即按本机身份操作。</p>
          <label>远端响应对账凭据<input type="password" autoComplete="off" value={reconcileCredential}
            onChange={e => setReconcileCredential(e.target.value)} /></label>
          <label>远端响应对账说明<textarea value={reconcileEvidence} onChange={e => setReconcileEvidence(e.target.value)} /></label>
        </div>}
      {runs.map(r => <details key={r.id}>
        <summary>{roles.find(x => x.role === r.role_id)?.label ?? r.role_id} · {r.model_id} · {label(statusLabels, r.status)}{r.error_code ? ` · ${label(failureLabels, r.error_code)}` : ''}</summary>
        {needsRemoteReconciliation(r) &&
          <button
            disabled={busy || (reconcileCredential.length > 0 && reconcileCredential.length < 32) || !reconcileEvidence.trim()}
            onClick={() => abandonRemote(r)}>放弃远端未知响应（不重试）</button>}
        <pre>{JSON.stringify(r, null, 2)}</pre></details>)}
    </section>
    <section className="panel" id="h3-rulebook">
      <div className="panel-header"><h2>H3 官方规范中心</h2><button disabled={busy} onClick={() => act(
        () => api('/h3-rulebook/recheck', { method: 'POST' }))}>重新校验来源文件</button></div>
      <p>五种模式：T2VA / I2VA / FL2VA / L2VA / Ref2VA。本机支持情况以各工作流档案与实测证据为准。</p>
      <p>可执行规则由 Shine 自编并固定版本；官方原文未缓存时显示 SOURCE_UNAVAILABLE，许可未确认前不提供原文分发。</p>
      {hub?.packs.map(p => <article className="settings-block" key={p.id}>
        <b>{p.id}</b><p>SHA-256 {p.sha256}</p><p>{p.approved_at ? '规则包已批准' : '候选版本待人工批准'}</p>
        <button disabled={busy || !!pin || !p.approved_at} onClick={() => act(() => api(
          `/projects/${projectId}/rulepack/freeze`, json('POST', { rulepack_id: p.id })))}>冻结到本项目</button>
        {!p.approved_at && <button disabled={busy} onClick={() => act(() => api(
          `/h3-rulebook/packs/${p.id}/approve`, { method: 'POST' }))}>人工批准此规则包</button>}
        <details><summary>版本与来源元数据</summary><pre>{JSON.stringify(p.manifest, null, 2)}</pre></details>
      </article>)}
      <h3>官方来源 / 本机缓存</h3>
      {hub?.sources.map(s => <article key={s.path} className="settings-block">
        <a href={s.url} target="_blank" rel="noreferrer">{s.path}</a><p>{s.local_cache_status} · {s.sha256}</p>
        <label>导入你已合法获取的原始文件（仅本机缓存）<input type="file" disabled={busy} onChange={e => {
          const file = e.target.files?.[0]; if (!file) return;
          const data = new FormData(); data.append('file', file);
          act(() => api(`/h3-rulebook/cache?source_path=${encodeURIComponent(s.path)}`, { method: 'POST', body: data }));
          e.target.value = '';
        }} /></label></article>)}
      <label>检索机器规则<input value={query} onChange={e => setQuery(e.target.value)} placeholder="标签、字段名或规则 ID" /></label>
      {filteredRules.map(r => <p key={r.id}><b>{r.origin} · {r.id}</b> — {r.summary}<br /><small>{r.source_path} · {r.locator}</small></p>)}
      <h3>确定性镜头预检</h3>
      {shots.map(s => <button key={s.id} disabled={busy || !pin} onClick={() => act(() => api(
        `/shots/${s.id}/compile`, { method: 'POST' }))}>{s.name}：预检并生成提示词</button>)}
      <h3>本机 Workflow 能力</h3>
      {hub?.local_workflow_capabilities.map(w => <p key={w.name}>{w.name} · {label(modeLabels, w.mode)} · {label(serviceStatusLabels, w.status)}</p>)}
      <details><summary>提交候选规则包 / 差异审查</summary>
        <p>仅支持经过当前代码校验的固定官方版本；上游版本或机器规则变化需要开发、测试后才能批准。</p>
        <textarea rows={5} value={candidate} onChange={e => setCandidate(e.target.value)} placeholder="候选 manifest JSON" />
        <button disabled={busy || !candidate} onClick={() => act(() => api('/h3-rulebook/candidates', json('POST', {
          manifest: JSON.parse(candidate),
        })))}>生成差异并提交候选</button></details>
    </section>
    {message && <p role="status">{message}</p>}
    {result !== null && <details open className="film-operation-result"><summary>最近操作结果</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>}
  </>;
}
