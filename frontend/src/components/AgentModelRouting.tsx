import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, Eye, Mic, RotateCcw, Save, WandSparkles } from 'lucide-react';
import type { AgentRoute, AgentRoutePreview, LlmRole, ProviderProfile } from '../api';
import { routeRoleLabels } from '../constants';

type Props = {
  roles: LlmRole[]; profiles: ProviderProfile[]; routes: AgentRoute[];
  revision: string; busy: boolean;
  multimodal: Array<Record<string, unknown>>;
  onPreview: (updates: AgentRoutePreview['updates']) => Promise<AgentRoutePreview | null>;
  onApply: (preview: AgentRoutePreview) => Promise<boolean>;
};

const workflows: Array<{ id: string; label: string; roles: string[] | null }> = [
  { id: 'script_and_storyboard', label: '剧本与分镜', roles: ['scriptwriter', 'script_planner', 'shot_planner'] },
  { id: 'four_stage', label: '四阶段创作', roles: ['scriptwriter', 'four_stage_bible_run', 'four_stage_scene_script_run'] },
  { id: 'film_crew', label: '影视制作团队', roles: null },
  { id: 'all_text', label: '全部文本 Agent', roles: null },
];

function roleStatus(role: LlmRole): string {
  if (!role.profile_id) return '未分配模型';
  if (role.code) return '需要检查';
  if (role.readiness_status === 'GENERATION_READY') return '生成检查通过';
  if (role.readiness_status === 'CONNECTION_READY') return '连接已验证';
  return role.readiness_status === 'STALE' ? '检查证据过期' : '已分配';
}

function roleDisplay(role: LlmRole): string {
  return role.label_zh || routeRoleLabels[role.role] || role.role;
}

export function AgentModelRouting({
  roles, profiles, routes, revision, busy, multimodal, onPreview, onApply,
}: Props) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [bulkProfile, setBulkProfile] = useState('');
  const [preview, setPreview] = useState<AgentRoutePreview | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState('');

  const currentRoutes = useMemo(() => new Map(routes.map(route => [route.role, route.provider_profile_id])), [routes]);
  const enabledProfiles = profiles.filter(profile => profile.enabled && profile.kind !== 'mock' && !!profile.model.trim());
  const groups = useMemo(() => {
    const result = new Map<string, LlmRole[]>();
    for (const role of roles) result.set(role.group || '其他', [...(result.get(role.group || '其他') ?? []), role]);
    return [...result.entries()];
  }, [roles]);

  useEffect(() => {
    setDraft(Object.fromEntries(roles.map(role => [role.role, currentRoutes.get(role.role) ?? ''])));
    setPreview(null);
  }, [roles, currentRoutes]);

  function selectRoles(next: string[]) {
    setSelectedRoles([...new Set(next)]);
  }

  function setRoleProfile(role: string, profileId: string) {
    setDraft(current => ({ ...current, [role]: profileId }));
    setPreview(null);
    setPreviewError('');
  }

  function bulkAssign(targetRoles: string[]) {
    if (!bulkProfile || targetRoles.length === 0) return;
    setDraft(current => ({ ...current, ...Object.fromEntries(targetRoles.map(role => [role, bulkProfile])) }));
    setSelectedRoles(targetRoles);
    setPreview(null);
    setPreviewError('');
  }

  function fillMissing() {
    const missing = roles.filter(role => !(currentRoutes.get(role.role) ?? '')).map(role => role.role);
    bulkAssign(missing);
  }

  function applyWorkflow(workflowId: string) {
    const workflow = workflows.find(item => item.id === workflowId);
    if (!workflow) return;
    const matching = workflow.roles
      ? roles.filter(role => workflow.roles?.includes(role.role))
      : workflow.id === 'film_crew'
        ? roles.filter(role => role.group === '影视制作团队')
        : roles;
    selectRoles(matching.map(role => role.role));
  }

  const updates = roles
    .filter(role => (draft[role.role] ?? currentRoutes.get(role.role) ?? '') !== (currentRoutes.get(role.role) ?? ''))
    .map(role => ({ role: role.role, provider_profile_id: draft[role.role] || null }));

  async function previewChanges() {
    if (updates.length === 0) {
      setPreview(null);
      setPreviewError('当前分配没有变化。');
      return;
    }
    setPreviewBusy(true);
    setPreviewError('');
    try {
      const result = await onPreview(updates);
      setPreview(result);
      if (!result) setPreviewError('预览失败，请刷新模型和角色状态后重试。');
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : String(error));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function saveChanges() {
    if (!preview?.can_apply) return;
    const changed = preview.changes.filter(change => change.action !== 'unchanged');
    const summary = changed.map(change => {
      const role = roles.find(item => item.role === change.role);
      const from = change.from_profile_name || '未配置';
      const to = change.to_profile_name || '移除分配';
      return `${role ? roleDisplay(role) : change.role}：${from} → ${to}`;
    }).join('\n');
    if (!window.confirm(`即将保存 ${preview.changed_role_count} 项 Agent 分配：\n${summary}\n\n当前预览会绑定新提交的任务；已排队或运行中的任务会保留原模型。继续？`)) return;
    await onApply(preview);
  }

  const groupedMultimodal = multimodal.filter(item => item.role === 'vlm_reviewer' || item.role === 'asr_reviewer');
  return <section className="agent-routing" aria-label="Agent 模型分配">
    <div className="agent-routing-heading">
      <div>
        <span className="section-kicker">AGENT ROUTING</span>
        <h3>Agent 模型分配</h3>
        <p>每个文本 Agent 都有明确路由；同一模型可以供多个角色使用。改派先预览，再一次保存。</p>
      </div>
      <span className="route-revision-state">{revision ? '路由状态已读取' : '等待读取路由'}</span>
    </div>

    <div className="agent-routing-bulk">
      <label>批量分配模型<select aria-label="批量分配模型" value={bulkProfile} disabled={busy} onChange={event => setBulkProfile(event.target.value)}>
        <option value="">选择已启用并可用的模型</option>
        {enabledProfiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}
      </select></label>
      <button className="secondary-button" type="button" disabled={busy || !bulkProfile || selectedRoles.length === 0}
        onClick={() => bulkAssign(selectedRoles)}>分配给选中角色（{selectedRoles.length}）</button>
      <button className="text-button" type="button" disabled={busy || !bulkProfile || !roles.some(role => !(currentRoutes.get(role.role) ?? ''))}
        onClick={fillMissing}>只补齐未配置角色</button>
      <details className="routing-workflow-picker"><summary><WandSparkles size={13} /> 按工作阶段选择</summary>
        <div className="routing-workflow-options">{workflows.map(workflow => <button type="button" key={workflow.id} disabled={busy}
          onClick={() => applyWorkflow(workflow.id)}>{workflow.label}</button>)}</div>
      </details>
    </div>

    {groups.map(([group, groupRoles]) => <section className="agent-role-group" key={group}>
      <div className="agent-role-group-title">
        <h4>{group}</h4>
        <button type="button" className="text-button" disabled={busy}
          onClick={() => selectRoles(selectedRoles.length === roles.length
            ? [] : [...selectedRoles.filter(role => !groupRoles.some(item => item.role === role)), ...groupRoles.map(item => item.role)])}>
          选择本组
        </button>
      </div>
      {groupRoles.map(role => {
        const currentId = currentRoutes.get(role.role) ?? role.profile_id ?? '';
        const currentProfile = profiles.find(profile => profile.id === currentId);
        return <div className="agent-role-row" key={role.role}>
          <input type="checkbox" aria-label={'选择角色 ' + roleDisplay(role)} checked={selectedRoles.includes(role.role)} disabled={busy}
            onChange={event => selectRoles(event.target.checked ? [...selectedRoles, role.role] : selectedRoles.filter(item => item !== role.role))} />
          <div className="agent-role-name">
            <b>{roleDisplay(role)}</b>
            <span>{role.business_entry} · {role.description}</span>
          </div>
          <div className="agent-role-current">
            <small>当前模型</small><b>{currentProfile?.name ?? role.profile_name ?? '未分配'}</b>
          </div>
          <label className="agent-role-select">改派为<select aria-label={'为 ' + roleDisplay(role) + ' 选择模型'} value={draft[role.role] ?? currentId} disabled={busy}
            onChange={event => setRoleProfile(role.role, event.target.value)}>
            <option value="">未分配</option>
            {profiles.map(profile => <option key={profile.id} value={profile.id}
              disabled={(!profile.enabled || profile.kind === 'mock' || !profile.model.trim()) && profile.id !== currentId}>
              {profile.name} · {profile.model || '缺少 Model ID'}{!profile.enabled ? '（已停用）' : profile.kind === 'mock' ? '（演示模型）' : ''}
            </option>)}
          </select></label>
          <span className={`agent-role-status ${role.code ? 'needs-check' : ''}`}>{roleStatus(role)}</span>
        </div>;
      })}
    </section>)}

    <div className="agent-route-actions">
      <button className="secondary-button" type="button" disabled={busy || previewBusy || updates.length === 0} onClick={() => void previewChanges()}>
        <Eye size={14} /> 预览改派影响（{updates.length}）
      </button>
      {updates.length > 0 && <button className="text-button" type="button" disabled={busy || previewBusy} onClick={() => {
        setDraft(Object.fromEntries(roles.map(role => [role.role, currentRoutes.get(role.role) ?? ''])));
        setPreview(null); setPreviewError('');
      }}><RotateCcw size={13} /> 撤销未保存改动</button>}
      {previewError && <span role="status" className="route-preview-error">{previewError}</span>}
    </div>

    {preview && <div className={`route-preview ${preview.can_apply ? 'ready' : 'blocked'}`}>
      <h4>改派影响预览</h4>
      {preview.changes.filter(change => change.action !== 'unchanged').map(change => {
        const role = roles.find(item => item.role === change.role);
        return <p key={change.role}><b>{role ? roleDisplay(role) : change.role}</b>　{change.from_profile_name || '未配置'} <ArrowRight size={12} /> {change.to_profile_name || '移除分配'}</p>;
      })}
      {preview.affected_tasks.length > 0 && <div className="route-preview-warning"><AlertTriangle size={14} />
        <span>有 {preview.affected_tasks.length} 个排队、运行或待核对任务受影响，当前批次不能保存。它们的已绑定模型不会被改动。</span>
      </div>}
      {preview.blockers.map(blocker => <p className="route-preview-blocker" key={`${blocker.role}-${blocker.code}`}>{roleDisplay(roles.find(role => role.role === blocker.role) ?? { role: blocker.role } as LlmRole)}：{blocker.message}</p>)}
      {preview.can_apply && <button className="primary-button" type="button" disabled={busy} onClick={() => void saveChanges()}>
        <Save size={14} /> 确认保存 {preview.changed_role_count} 项分配
      </button>}
    </div>}

    <details className="llm-multimodal-note">
      <summary>画面审片与语音核对（独立能力）</summary>
      <p>VLM 和 ASR 不属于文本 Agent 路由矩阵，不会被这里的批量分配覆盖。</p>
      {groupedMultimodal.length === 0 ? <p>当前未配置独立能力。</p> : groupedMultimodal.map(item => <p key={String(item.role)}>
        {item.role === 'vlm_reviewer' ? <Eye size={13} /> : <Mic size={13} />} {String(item.label_zh ?? item.role)}：{String(item.status ?? '状态未知')} {item.profile_name ? `· ${String(item.profile_name)}` : ''}
      </p>)}
    </details>
  </section>;
}
