import { useState } from 'react';
import { KeyRound, Plus, Trash2 } from 'lucide-react';
import type { AgentRoute, ProviderProfile } from '../api';
import { label, providerKindLabels, reasoningEffortLabels, routeRoleLabels } from '../constants';

type Props = {
  profiles: ProviderProfile[]; routes: AgentRoute[]; busy: boolean;
  onCreateProfile: (payload: Record<string, unknown>) => void;
  onTestProfile: (id: string) => void; onSetKey: (id: string, key: string) => void;
  onDeleteProfile: (id: string) => void; onRoute: (role: string, id: string) => void;
};
export function LLMConfiguration({ profiles, routes, busy, onCreateProfile, onTestProfile, onSetKey, onDeleteProfile, onRoute }: Props) {
  const [name, setName] = useState('Local LLM');
  const [kind, setKind] = useState('openai_compatible');
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:11434/v1');
  const [model, setModel] = useState('qwen2.5:7b');
  const [apiKey, setApiKey] = useState('');
  const [maxTokens, setMaxTokens] = useState('');
  const [reasoningEffort, setReasoningEffort] = useState('');
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  function createProfile() {
    onCreateProfile({ name, kind, base_url: baseUrl, model, api_key: apiKey || null, timeout_s: 60, max_retries: 2,
      generation_options: kind === 'openai_compatible' ? {
        ...(maxTokens ? { max_completion_tokens: Number(maxTokens) } : {}),
        ...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
      } : {},
    });
    setApiKey('');
  }

  return (
        <div className="settings-block" id="llm-config">
          <h3><KeyRound size={14} /> AI 模型（LLM）配置</h3>
          <p className="block-hint">写作、规划、审片各环节用的模型在这里添加和指定；密钥只存本机。</p>
          <div className="provider-form">
            <input value={name} placeholder="配置名称" onChange={(event) => setName(event.target.value)} />
            <select value={kind} onChange={(event) => setKind(event.target.value)}>
              <option value="openai_compatible">OpenAI-compatible</option>
              <option value="anthropic">Anthropic 原生</option>
              <option value="ollama">Ollama</option>
              <option value="mock">Mock</option>
            </select>
            <input value={baseUrl} placeholder="Base URL" onChange={(event) => setBaseUrl(event.target.value)} />
            <input value={model} placeholder="Model ID" onChange={(event) => setModel(event.target.value)} />
            <input value={apiKey} type="password" placeholder="API Key（不会回显）" onChange={(event) => setApiKey(event.target.value)} />
            {kind === 'openai_compatible' && <>
              <input aria-label="最大输出 token" type="number" min="1" max="524288" step="1" value={maxTokens} placeholder="输出上限（含思考，留空沿用服务商默认）" onChange={event => setMaxTokens(event.target.value)} />
              <select aria-label="思考深度" value={reasoningEffort} onChange={event => setReasoningEffort(event.target.value)}>
                <option value="">服务商默认思考深度</option>
                {['low', 'medium', 'high', 'xhigh', 'max'].map(value => <option key={value} value={value}>{label(reasoningEffortLabels, value)}</option>)}
              </select>
            </>}
            <button className="primary-button" disabled={busy} onClick={createProfile}><Plus size={13} /> 添加</button>
          </div>
          {profiles.map((profile) => (
            <div className="profile-row" key={profile.id}>
              <div><b>{profile.name}</b><span>{label(providerKindLabels, profile.kind)} · {profile.model || '未设置模型'} · {profile.has_api_key ? '已存密钥' : '无密钥'}</span>
                {profile.generation_options?.max_completion_tokens && <span>输出上限 {profile.generation_options.max_completion_tokens} token（含思考）</span>}
                {profile.generation_options?.reasoning_effort && <span>思考深度：{label(reasoningEffortLabels, profile.generation_options.reasoning_effort)}</span>}
              </div>
              <div className="profile-actions">
                <input
                  value={keyDrafts[profile.id] ?? ''}
                  type="password"
                  placeholder="更新密钥"
                  onChange={(event) => setKeyDrafts((rows) => ({ ...rows, [profile.id]: event.target.value }))}
                />
                <button className="text-button" disabled={!keyDrafts[profile.id]} onClick={() => { onSetKey(profile.id, keyDrafts[profile.id]); setKeyDrafts((rows) => ({ ...rows, [profile.id]: '' })); }}>保存密钥</button>
                <button className="text-button" onClick={() => onTestProfile(profile.id)}>测试</button>
                <button className="text-button" title="把此模型用于镜头规划环节" onClick={() => onRoute('shot_planner', profile.id)}>用作镜头规划</button>
                <button className="text-button" title="把此模型用于剧本规划环节" onClick={() => onRoute('script_planner', profile.id)}>用作剧本规划</button>
                <button className="text-button" title="把此模型用于画面审片环节" onClick={() => onRoute('vlm_reviewer', profile.id)}>用作画面审片</button>
                <button className="text-button" title="把此模型用于语音核对环节" onClick={() => onRoute('asr_reviewer', profile.id)}>用作语音核对</button>
                <button className="icon-button tiny" onClick={() => onDeleteProfile(profile.id)}><Trash2 size={12} /></button>
              </div>
            </div>
          ))}
          <div className="route-row">
            {routes.length === 0 ? <span className="empty-inline">还没给各环节指定模型；真实准备尚未完成，请明确指定文本工种使用的模型。</span> : routes.map((route) => (
              <span className="route-chip" key={route.role}>{label(routeRoleLabels, route.role)} → {route.profile_name}</span>
            ))}
          </div>
        </div>

  );
}
