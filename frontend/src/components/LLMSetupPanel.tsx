import { useEffect, useRef, useState } from 'react';
import {
  api as request, json, timeoutSignal, type AgentRoute, type AgentRoutePreview, type LlmOverview,
  type LlmReadiness, type LlmRole, type ProviderProfile, type ProviderTestResult,
} from '../api';
import { routeRoleLabels } from '../constants';
import { AgentModelRouting } from './AgentModelRouting';
import { LLMConfiguration } from './LLMConfiguration';

type MutationStatus = 'idle' | 'succeeded' | 'failed' | 'unconfirmed';
type RefreshStatus = 'idle' | 'succeeded' | 'failed';

class ProviderCheckFailed extends Error {
  constructor(public readonly result: ProviderTestResult) {
    super(result.message || '检查未通过');
  }
}

function describeFailure(error: unknown): string {
  if (error instanceof ProviderCheckFailed) {
    const code = error.result.error_code;
    return code ? `${error.message}（${code}）` : error.message;
  }
  if (error instanceof DOMException && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
    return '请求超时或被中断，服务端可能已经写入；请先重新检查状态，不要重复提交。';
  }
  return error instanceof Error ? error.message : String(error);
}

function legacyRole(role: Record<string, unknown>, route?: AgentRoute): LlmRole {
  const roleId = String(role.role ?? '');
  return {
    role: roleId,
    label_zh: String(role.label_zh ?? routeRoleLabels[roleId] ?? roleId),
    group: String(role.group ?? '文本 Agent'),
    business_entry: String(role.business_entry ?? ''),
    required: role.required !== false,
    capability_type: String(role.capability_type ?? 'structured_text'),
    dependency_stages: Array.isArray(role.dependency_stages) ? role.dependency_stages.map(String) : [],
    description: String(role.description ?? ''),
    profile_id: route?.provider_profile_id ?? null,
    profile_name: route?.profile_name ?? null,
    profile_kind: null,
    profile_enabled: null,
    profile_valid: false,
    code: typeof role.code === 'string' ? role.code : null,
    generation_code: typeof role.generation_code === 'string' ? role.generation_code : null,
    readiness_status: String(role.readiness_status ?? 'UNCONFIGURED'),
    secret_status: String(role.secret_status ?? 'unknown'),
    connection_status: String(role.connection_status ?? 'NOT_CHECKED'),
    catalog_status: String(role.catalog_status ?? 'NOT_CHECKED'),
    generation_status: String(role.generation_status ?? 'NOT_CHECKED'),
    last_connection_checked_at: typeof role.last_connection_checked_at === 'string' ? role.last_connection_checked_at : null,
    last_generation_checked_at: typeof role.last_generation_checked_at === 'string' ? role.last_generation_checked_at : null,
  };
}

export function LLMSetupPanel({ onChanged }: { onChanged?: () => void | Promise<void> }) {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [routes, setRoutes] = useState<AgentRoute[]>([]);
  const [roles, setRoles] = useState<LlmRole[]>([]);
  const [routeRevision, setRouteRevision] = useState('');
  const [readiness, setReadiness] = useState<LlmReadiness | null>(null);
  const [multimodal, setMultimodal] = useState<Array<Record<string, unknown>>>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [mutationStatus, setMutationStatus] = useState<MutationStatus>('idle');
  const [refreshStatus, setRefreshStatus] = useState<RefreshStatus>('idle');
  const [autoNote, setAutoNote] = useState('');
  const [routingOpen, setRoutingOpen] = useState(false);
  const mounted = useRef(true);
  const locked = useRef(false);
  const autoRan = useRef(false);

  async function api<T>(path: string, init?: RequestInit, timeoutMs = 60_000): Promise<T> {
    return request<T>(path, { ...init, signal: init?.signal ?? timeoutSignal(timeoutMs) });
  }

  async function load() {
    try {
      const overview = await api<LlmOverview>('/llm/overview');
      if (!mounted.current) return;
      setProfiles(overview.profiles);
      setRoutes(overview.routes);
      setRoles(overview.roles);
      setRouteRevision(overview.route_revision);
      setReadiness(overview.readiness);
      setMultimodal(overview.multimodal_capabilities ?? []);
      return;
    } catch {
      // Older local workspaces keep their original CRUD and readiness routes.
      // The newer overview is the source used when available.
    }
    const [p, r, state] = await Promise.all([
      api<ProviderProfile[]>('/provider-profiles'),
      api<AgentRoute[]>('/agent-routes'),
      api<LlmReadiness>('/setup/llm-readiness'),
    ]);
    let revision = '';
    let multi: Array<Record<string, unknown>> = [];
    try { revision = (await api<{ route_revision: string }>('/llm/roles')).route_revision; } catch { /* old server */ }
    try { multi = (await api<{ multimodal: Array<Record<string, unknown>> }>('/llm/capabilities')).multimodal; } catch { /* old server */ }
    if (!mounted.current) return;
    const routeMap = new Map(r.map(route => [route.role, route]));
    setProfiles(p);
    setRoutes(r);
    setRoles(state.roles.map(role => legacyRole(role as unknown as Record<string, unknown>, routeMap.get(role.role))));
    setRouteRevision(revision);
    setReadiness(state);
    setMultimodal(multi ?? []);
  }

  async function run(
    action: () => Promise<unknown>,
    options: { successMessage?: string } = {},
  ): Promise<boolean> {
    if (locked.current) return false;
    locked.current = true;
    setBusy(true);
    setMutationStatus('unconfirmed');
    setRefreshStatus('idle');
    let succeeded = false;
    let failure = '';
    try {
      await action();
      succeeded = true;
    } catch (error) {
      failure = describeFailure(error);
      setMutationStatus(failure.includes('超时') || failure.includes('中断') ? 'unconfirmed' : 'failed');
    }
    let refreshFailed = false;
    try {
      await load();
      await onChanged?.();
    } catch {
      refreshFailed = true;
    }
    setRefreshStatus(refreshFailed ? 'failed' : 'succeeded');
    if (succeeded) {
      setMutationStatus('succeeded');
      setNotice(refreshFailed
        ? '配置已保存，但状态刷新失败；请重新检查，不要重复添加。'
        : (options.successMessage ?? '配置已保存。'));
    } else {
      setNotice('操作未完成：' + failure + (refreshFailed ? '；状态刷新失败，请重新检查。' : ''));
    }
    locked.current = false;
    if (mounted.current) setBusy(false);
    return succeeded;
  }

  useEffect(() => {
    mounted.current = true;
    void load().catch(error => { if (mounted.current) setNotice(describeFailure(error)); });
    return () => { mounted.current = false; };
  }, []);

  /** 只有一个可用模型时：自动分配给全部工种，并各做一次真实检查。
   *
   * 这是用户明确要求的默认动作：不需要逐个点。已经有分配、或有多个模型时
   * 不自动改派，避免覆盖用户自己的安排。 */
  useEffect(() => {
    if (autoRan.current || busy || locked.current) return;
    if (!profiles.length || !roles.length || !routeRevision) return;
    const usable = profiles.filter((row) => row.enabled && row.kind !== 'mock' && row.model.trim());
    if (usable.length !== 1) return;
    const profileId = usable[0].id;
    const assigned = new Set(routes.map((route) => route.role));
    const missing = roles.filter((role) => !assigned.has(role.role));
    const needsTest = readiness?.status !== 'READY' || roles.some((role) => role.code);
    if (missing.length === 0 && !needsTest) return;
    autoRan.current = true;
    void (async () => {
      try {
        if (missing.length > 0) {
          const preview = await api<AgentRoutePreview>('/agent-routes/preview', json('POST', {
            updates: roles.map((role) => ({ role: role.role, provider_profile_id: profileId })),
          }));
          if (!preview.can_apply) {
            setAutoNote('还有未结束的任务占用模型分配；结束后再点一次「重新读取」即可自动补齐。');
            return;
          }
          await api('/agent-routes/batch', json('PUT', {
            updates: preview.updates,
            expected_revision: preview.expected_revision || routeRevision,
          }));
        }
        // 只有一个模型时，连接与生成检查也自动做一次：用户不必逐个工种点。
        await api(`/provider-profiles/${profileId}/test`, { method: 'POST' }, timeoutFor(profileId));
        await api(`/provider-profiles/${profileId}/test-generation`, { method: 'POST' }, timeoutFor(profileId));
        setAutoNote(missing.length > 0
          ? `只有一个模型：已自动分配给全部 ${roles.length} 个工种，并完成连接与生成检查。`
          : '只有一个模型：已自动完成连接与生成检查。');
        await load();
        await onChanged?.();
      } catch (error) {
        setAutoNote('自动配置没有完成：' + describeFailure(error));
      }
    })();
  }, [profiles, roles, routes, routeRevision, readiness, busy, onChanged]);

  function timeoutFor(profileId: string): number {
    const profile = profiles.find(row => row.id === profileId);
    return Math.max(60, Math.ceil(profile?.timeout_s ?? 180) + 30) * 1000;
  }

  async function previewRoutes(updates: AgentRoutePreview['updates']): Promise<AgentRoutePreview | null> {
    try {
      return await api<AgentRoutePreview>('/agent-routes/preview', json('POST', { updates }));
    } catch (error) {
      setNotice('改派预览失败：' + describeFailure(error));
      return null;
    }
  }

  function applyRoutes(preview: AgentRoutePreview): Promise<boolean> {
    return run(() => api('/agent-routes/batch', json('PUT', {
      updates: preview.updates,
      expected_revision: preview.expected_revision || routeRevision,
    })), { successMessage: `已保存 ${preview.changed_role_count} 项 Agent 模型分配。` });
  }

  return <div data-testid="llm-setup" className="llm-setup-panel">
    {notice && <p role="alert" className="llm-notice">{notice}</p>}
    {autoNote && <p className="llm-auto-note" role="status">{autoNote}</p>}
    <div className="llm-write-state" role="status">
      <span>{mutationStatus === 'idle' ? '尚未操作'
        : mutationStatus === 'succeeded' ? '已保存'
          : mutationStatus === 'failed' ? '未完成' : '结果未知，请重新检查'}
        {refreshStatus === 'failed' ? ' · 刷新失败' : ''}</span>
      <button className="btn-text" type="button" disabled={busy} onClick={() => void run(async () => {
        await load();
      }, { successMessage: '已重新读取模型与路由状态。' })}>重新读取</button>
    </div>

    <LLMConfiguration profiles={profiles} routes={routes} busy={busy}
      onCreateProfile={body => run(() => api('/provider-profiles', json('POST', body)), { successMessage: '模型配置已保存。现在可以测试连接与结构化生成。' })}
      onUpdateProfile={(id, body) => run(() => api('/provider-profiles/' + id, json('PUT', body)), { successMessage: '模型配置已保存；相关检查证据已过期。' })}
      onDuplicateProfile={(id, body) => run(() => api('/provider-profiles/' + id + '/duplicate', json('POST', body)), { successMessage: '模型副本已创建，并使用独立的本机密钥引用。' })}
      onTestProfile={id => run(async () => {
        const result = await api<ProviderTestResult>('/provider-profiles/' + id + '/test', { method: 'POST' }, timeoutFor(id));
        if (!result.ok) throw new ProviderCheckFailed(result);
      }, { successMessage: '连接检查已完成；模型目录状态会单独显示。' })}
      onTestGeneration={id => run(async () => {
        const result = await api<ProviderTestResult>('/provider-profiles/' + id + '/test-generation', { method: 'POST' }, timeoutFor(id));
        if (!result.ok) throw new ProviderCheckFailed(result);
      }, { successMessage: '结构化生成检查通过；这不代表答案质量验收。' })}
      onSetKey={(id, key) => run(() => api('/provider-profiles/' + id + '/key', json('PUT', { api_key: key })), { successMessage: '新密钥已安全保存；原检查证据已过期。' })}
      onDeleteProfile={id => void run(() => api('/provider-profiles/' + id, { method: 'DELETE' }), { successMessage: '模型配置已删除。' })} />

    <div className="llm-readiness-summary" aria-label="LLM 就绪状态">
      <span>{readiness?.status === 'READY' ? '全部工种已配好并通过检查' : '还有工种等待配置'}
        {' · '}{profiles.length} 个模型{readiness?.text_agent_ready ? ' · 生成检查通过' : ''}</span>
    </div>
    {(readiness?.roles.filter(role => role.code).length ?? 0) > 0 && <p className="llm-compact-hint">
      还有 {readiness?.roles.filter(role => role.code).length} 个工种没有分配模型：在「Agent 分配」里一次全选即可，只影响用到它的步骤。
    </p>}

    <details className="llm-routing-fold" open={routingOpen} onToggle={(event) => setRoutingOpen((event.target as HTMLDetailsElement).open)}>
      <summary>Agent 模型分配（{roles.length} 个工种）</summary>
      <AgentModelRouting roles={roles} profiles={profiles} routes={routes} revision={routeRevision} busy={busy}
        multimodal={multimodal} onPreview={previewRoutes} onApply={applyRoutes} />
    </details>
  </div>;
}
