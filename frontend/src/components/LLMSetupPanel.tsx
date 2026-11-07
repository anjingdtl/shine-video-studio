import { useEffect, useRef, useState } from 'react';
import { api as request, json, type AgentRoute, type ProviderProfile, type ProviderTestResult } from '../api';
import { routeRoleLabels } from '../constants';
import { LLMConfiguration } from './LLMConfiguration';

type Readiness = {
  status: string;
  generation_status?: string;
  text_agent_ready?: boolean;
  roles: Array<{
    role: string; profile_name: string | null; code: string | null; readiness_status?: string;
  }>;
};

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
    return code ? error.message + '（' + code + '）' : error.message;
  }
  if (error instanceof DOMException && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
    return '请求超时或被中断，服务端可能已经写入；请先重新检查状态，不要重复提交。';
  }
  return error instanceof Error ? error.message : String(error);
}

export function LLMSetupPanel({ onChanged }: { onChanged?: () => void | Promise<void> }) {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [routes, setRoutes] = useState<AgentRoute[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [mutationStatus, setMutationStatus] = useState<MutationStatus>('idle');
  const [refreshStatus, setRefreshStatus] = useState<RefreshStatus>('idle');
  const mounted = useRef(true);

  async function api<T>(path: string, init?: RequestInit, timeoutMs = 60_000): Promise<T> {
    return request<T>(path, { ...init, signal: init?.signal ?? AbortSignal.timeout(timeoutMs) });
  }
  const locked = useRef(false);

  async function load() {
    const [p, r, state] = await Promise.all([api<ProviderProfile[]>('/provider-profiles'),
      api<AgentRoute[]>('/agent-routes'), api<Readiness>('/setup/llm-readiness')]);
    if (!mounted.current) return;
    setProfiles(p); setRoutes(r); setReadiness(state);
  }

  /**
   * Run one write, then read the server state again.
   *
   * The write and the refresh have their own results: a successful write
   * that is followed by a failed refresh is still a saved configuration and
   * must never be shown as "not configured".  A failed write is re-read too,
   * because the answer may have been lost after the commit.
   */
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

  function timeoutFor(profileId: string): number {
    const profile = profiles.find(row => row.id === profileId);
    return Math.max(60, Math.ceil(profile?.timeout_s ?? 180) + 30) * 1000;
  }

  return <div data-testid="llm-setup">
    <p>新机准备 · 先配置文本 LLM，再安装 H3。可由一个模型承担全部文本工种；画面与语音审片仍使用独立配置。</p>
    {notice && <p role="alert">{notice}</p>}
    <p>写入状态：{mutationStatus === 'idle' ? '未操作'
      : mutationStatus === 'succeeded' ? '已保存'
      : mutationStatus === 'failed' ? '未完成'
      : '结果未知（请重新检查）'}
      ｜状态刷新：{refreshStatus === 'idle' ? '未刷新'
        : refreshStatus === 'succeeded' ? '已刷新' : '刷新失败'}</p>
    <LLMConfiguration profiles={profiles} routes={routes} busy={busy}
      onCreateProfile={body => run(() => api('/provider-profiles', json('POST', body)),
        { successMessage: '模型配置已保存。' })}
      onUpdateProfile={(id, body) => run(() => api('/provider-profiles/' + id, json('PUT', body)),
        { successMessage: '模型配置已保存。' })}
      onTestProfile={id => run(async () => {
        const result = await api<ProviderTestResult>('/provider-profiles/' + id + '/test',
          { method: 'POST' }, timeoutFor(id));
        if (!result.ok) throw new ProviderCheckFailed(result);
      }, { successMessage: '连接检查通过：服务可达，且模型目录列出了配置的 Model ID。' })}
      onTestGeneration={id => run(async () => {
        const result = await api<ProviderTestResult>('/provider-profiles/' + id + '/test-generation',
          { method: 'POST' }, timeoutFor(id));
        if (!result.ok) throw new ProviderCheckFailed(result);
      }, { successMessage: '生成与结构化输出测试通过：模型能按 JSON 约定回答（这不是答案质量验收）。' })}
      onSetKey={(id, key) => run(() => api('/provider-profiles/' + id + '/key', json('PUT', { api_key: key })),
        { successMessage: '密钥已保存。' })}
      onDeleteProfile={id => run(() => api('/provider-profiles/' + id, { method: 'DELETE' }),
        { successMessage: '模型配置已删除。' })}
      onRoute={(role, id) => run(() => api('/agent-routes', json('PUT', { role, provider_profile_id: id })),
        { successMessage: '已更新该工种使用的模型。' })} />
    <label>用于文本工种的模型<select value={selected} disabled={busy} onChange={event => setSelected(event.target.value)}>
      <option value="">请选择已添加的真实模型</option>
      {profiles.filter(p => p.kind !== 'mock' && p.enabled).map(p => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}
    </select></label>
    <button type="button" disabled={busy || !selected || !readiness} onClick={() => {
      if (!window.confirm('将所选模型明确用于全部文本工种（含编剧、导演、摄影、剪辑、规划与修复），并覆盖这些工种已有路由。继续？')) return;
      void run(async () => {
        const roles = (readiness?.roles ?? []).map(row => row.role);
        if (!roles.length) throw new Error('服务端没有返回可绑定的文本工种');
        // One request, one transaction: a half-bound routing table is never
        // written and never left behind.
        await api('/agent-routes/bulk', json('PUT', { provider_profile_id: selected, roles }));
      }, { successMessage: '已把所选模型用于全部文本工种。' });
    }}>用于全部文本工种</button>
    <button type="button" disabled={busy} onClick={() => void run(async () => undefined, { successMessage: '已重新读取 LLM 配置状态。' })}>重新检查 LLM 配置</button>
    <p>LLM 准备：{readiness?.status === 'READY'
      ? '连接检查通过（服务可达且模型目录列出了配置的 Model ID）'
      : '连接检查尚未完成'}</p>
    <p>生成验收：{readiness?.generation_status === 'GENERATION_READY'
      ? '生成与结构化输出测试通过（这是接口与格式验收，不代表答案质量验收）'
      : '尚未做生成验收；连接可用不等于模型能按约定完成结构化生成'}</p>
    {readiness?.roles.filter(row => row.code).map(row => <p key={row.role}>{routeRoleLabels[row.role] ?? row.role}：{row.code}</p>)}
    <p>「测试」只检测服务与模型列表；「生成验收」会真正调用一次生成接口，远端模型可能产生一次 API 费用。未通过测试、配置变更、禁用或 Mock 均不能完成真实准备。密钥只存本机；不会在检测中发送项目素材。</p>
  </div>;
}
