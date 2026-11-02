import { useEffect, useRef, useState } from 'react';
import { api as request, json, type AgentRoute, type ProviderProfile } from '../api';
import { routeRoleLabels } from '../constants';
import { LLMConfiguration } from './LLMConfiguration';

type Readiness = { status: string; roles: Array<{ role: string; profile_name: string | null; code: string | null }> };
export function LLMSetupPanel({ onChanged }: { onChanged?: () => void | Promise<void> }) {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [routes, setRoutes] = useState<AgentRoute[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const mounted = useRef(true);
  async function api<T>(path: string, init?: RequestInit): Promise<T> {
    return request<T>(path, { ...init, signal: init?.signal ?? AbortSignal.timeout(60_000) });
  }
  const locked = useRef(false);
  async function load() {
    const [p, r, state] = await Promise.all([api<ProviderProfile[]>('/provider-profiles'),
      api<AgentRoute[]>('/agent-routes'), api<Readiness>('/setup/llm-readiness')]);
    if (!mounted.current) return;
    setProfiles(p); setRoutes(r); setReadiness(state);
  }
  async function run(action: () => Promise<unknown>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setNotice('');
    try {
      await action();
      if (mounted.current) { await load(); await onChanged?.(); }
    }
    catch (error) { if (mounted.current) setNotice(`LLM 配置未完成：${String(error)}`); }
    finally { locked.current = false; if (mounted.current) setBusy(false); }
  }
  useEffect(() => {
    mounted.current = true;
    void load().catch(error => { if (mounted.current) setNotice(String(error)); });
    return () => { mounted.current = false; };
  }, []);
  return <div data-testid="llm-setup">
    <p>新机准备 · 先配置文本 LLM，再安装 H3。可由一个模型承担全部文本工种；画面与语音审片仍使用独立配置。</p>
    {notice && <p role="alert">{notice}</p>}
    <LLMConfiguration profiles={profiles} routes={routes} busy={busy}
      onCreateProfile={body => void run(() => api('/provider-profiles', json('POST', body)))}
      onTestProfile={id => void run(async () => {
        const result = await api<{ ok: boolean; error_code?: string; message: string }>(`/provider-profiles/${id}/test`, { method: 'POST' });
        if (!result.ok) throw new Error(result.error_code ?? result.message);
      })}
      onSetKey={(id, key) => void run(() => api(`/provider-profiles/${id}/key`, json('PUT', { api_key: key })))}
      onDeleteProfile={id => void run(() => api(`/provider-profiles/${id}`, { method: 'DELETE' }))}
      onRoute={(role, id) => void run(() => api('/agent-routes', json('PUT', { role, provider_profile_id: id })))} />
    <label>用于文本工种的模型<select value={selected} disabled={busy} onChange={event => setSelected(event.target.value)}>
      <option value="">请选择已添加的真实模型</option>
      {profiles.filter(p => p.kind !== 'mock' && p.enabled).map(p => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}
    </select></label>
    <button type="button" disabled={busy || !selected || !readiness} onClick={() => {
      if (!window.confirm('将所选模型明确用于全部文本工种（含编剧、导演、摄影、剪辑、规划与修复），并覆盖这些工种已有路由。继续？')) return;
      void run(async () => {
        for (const row of readiness?.roles ?? []) {
          if (!mounted.current) return;
          await api('/agent-routes', json('PUT', { role: row.role, provider_profile_id: selected }));
        }
      });
    }}>用于全部文本工种</button>
    <button type="button" disabled={busy} onClick={() => void run(async () => undefined)}>重新检查 LLM 配置</button>
    <p>LLM 准备：{readiness?.status === 'READY' ? '配置及连接检查通过（尚非文本生成验收）' : '尚未完成'}</p>
    {readiness?.roles.filter(row => row.code).map(row => <p key={row.role}>{routeRoleLabels[row.role] ?? row.role}：{row.code}</p>)}
    <p>「测试」只检测服务与模型列表；未通过测试、配置变更、禁用或 Mock 均不能完成真实准备。密钥只存本机；不会在检测中发送项目素材。</p>
  </div>;
}
