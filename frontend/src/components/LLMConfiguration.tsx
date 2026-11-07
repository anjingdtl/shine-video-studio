import { useState } from 'react';
import { KeyRound, Plus, Trash2 } from 'lucide-react';
import type { AgentRoute, ProviderProfile } from '../api';
import { label, providerKindLabels, reasoningEffortLabels, routeRoleLabels } from '../constants';

type Props = {
  profiles: ProviderProfile[]; routes: AgentRoute[]; busy: boolean;
  onCreateProfile: (payload: Record<string, unknown>) => Promise<boolean> | void;
  onUpdateProfile: (id: string, payload: Record<string, unknown>) => void;
  onTestProfile: (id: string) => void;
  onTestGeneration?: (id: string) => void;
  onSetKey: (id: string, key: string) => Promise<boolean> | void;
  onDeleteProfile: (id: string) => void;
  onRoute: (role: string, id: string) => void;
};

/** Clear a draft only after the server confirmed the write. */
function clearOnSuccess(outcome: Promise<boolean> | void, clear: () => void): void {
  if (outcome && typeof (outcome as Promise<boolean>).then === 'function') {
    void (outcome as Promise<boolean>).then(ok => { if (ok !== false) clear(); });
    return;
  }
  clear();
}

function secretLabel(profile: ProviderProfile): string {
  switch (profile.secret_status) {
    case 'stored': return '已存密钥';
    case 'missing': return '密钥文件缺失，请重新保存密钥';
    case 'unreadable': return '密钥无法读取，请重新保存密钥';
    default: return profile.has_api_key ? '已存密钥' : '无密钥';
  }
}

export function LLMConfiguration({ profiles, routes, busy, onCreateProfile, onUpdateProfile, onTestProfile, onTestGeneration, onSetKey, onDeleteProfile, onRoute }: Props) {
  const [name, setName] = useState('Local LLM');
  const [kind, setKind] = useState('openai_compatible');
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:11434/v1');
  const [model, setModel] = useState('qwen2.5:7b');
  const [apiKey, setApiKey] = useState('');
  const [maxTokens, setMaxTokens] = useState('');
  const [reasoningEffort, setReasoningEffort] = useState('');
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [timeoutDrafts, setTimeoutDrafts] = useState<Record<string, string>>({});
  const [outputTokenDrafts, setOutputTokenDrafts] = useState<Record<string, string>>({});

  function createProfile() {
    const outcome = onCreateProfile({
      name, kind, base_url: baseUrl, model, api_key: apiKey || null, timeout_s: 180, max_retries: 2,
      generation_options: kind === 'openai_compatible' ? {
        ...(maxTokens ? { max_completion_tokens: Number(maxTokens) } : {}),
        ...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
      } : {},
    });
    // The key draft is only cleared once the server confirmed the profile.
    clearOnSuccess(outcome, () => setApiKey(''));
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
              <input aria-label="最大输出 token" type="number" min="1" max="524288" step="1" value={maxTokens} placeholder="回复上限（留空沿用服务商默认；与输入上下文分开）" onChange={event => setMaxTokens(event.target.value)} />
              <select aria-label="思考深度" value={reasoningEffort} onChange={event => setReasoningEffort(event.target.value)}>
                <option value="">服务商默认思考深度</option>
                {['low', 'medium', 'high', 'xhigh', 'max'].map(value => <option key={value} value={value}>{label(reasoningEffortLabels, value)}</option>)}
              </select>
            </>}
            <button className="primary-button" disabled={busy} onClick={createProfile}><Plus size={13} /> 添加</button>
          </div>
          {profiles.map((profile) => {
            const capabilities = profile.capabilities as {
              models?: string[]; model_listed?: boolean;
              generation?: { generation_ok?: boolean; error_code?: string | null; checked_at?: string };
            };
            return (
            <div className="profile-row" key={profile.id}>
              <div><b>{profile.name}</b><span>{label(providerKindLabels, profile.kind)} · {profile.model || '未设置模型'} · {secretLabel(profile)}</span>
                {(() => {
                  const context = profile.capabilities.model_context as {
                    context_window_tokens?: number | null; source?: string;
                  } | undefined;
                  const tokens = context?.context_window_tokens;
                  return tokens
                    ? <span>输入上下文：{tokens.toLocaleString()} tokens · {context?.source?.includes('official') ? '官方模型说明' : '服务商模型目录'}</span>
                    : <span>输入上下文：未报告固定上限；完整内容交由模型服务判定，不做小字符截断</span>;
                })()}
                {capabilities.model_listed === false && Array.isArray(capabilities.models) && capabilities.models.length > 0 && (
                  <span>服务列出的 Model ID：{capabilities.models.join('、')}；请从中明确选择</span>
                )}
                {capabilities.generation && (
                  <span>生成验收：{capabilities.generation.generation_ok
                    ? '已通过（接口与格式，不代表答案质量）'
                    : '未通过' + (capabilities.generation.error_code ? '（' + capabilities.generation.error_code + '）' : '')}</span>
                )}
                {profile.generation_options?.max_completion_tokens && <span>输出上限 {profile.generation_options.max_completion_tokens} token（含思考）</span>}
                {profile.generation_options?.reasoning_effort && <span>思考深度：{label(reasoningEffortLabels, profile.generation_options.reasoning_effort)}</span>}
                <details className="profile-timeout-settings">
                  <summary>调整结构化回复上限（可选）</summary>
                  <label>最大输出 token（包含思考）
                    <input aria-label={'最大输出 token ' + profile.name} type="number" min="1" max="524288" step="1"
                      value={outputTokenDrafts[profile.id] ?? String(profile.generation_options?.max_completion_tokens ?? '')}
                      placeholder="留空沿用服务商默认"
                      disabled={busy}
                      onChange={event => setOutputTokenDrafts(rows => ({ ...rows, [profile.id]: event.target.value }))} />
                  </label>
                  <small>这是回复长度限制，不是输入上下文窗口。留空时不附加较小的固定回复上限。</small>
                  <button className="text-button" type="button" disabled={busy}
                    onClick={() => {
                      const raw = outputTokenDrafts[profile.id] ?? String(profile.generation_options?.max_completion_tokens ?? '');
                      onUpdateProfile(profile.id, { generation_options: {
                        ...(raw.trim() ? { max_completion_tokens: Number(raw) } : {}),
                        ...(profile.generation_options?.reasoning_effort
                          ? { reasoning_effort: profile.generation_options.reasoning_effort } : {}),
                      } });
                    }}>保存回复上限</button>
                </details>
                <details className="profile-timeout-settings">
                  <summary>模型回复较慢？调整等待时间</summary>
                  <label>最长等待时间
                    <select aria-label={'最长等待时间 ' + profile.name} value={timeoutDrafts[profile.id] ?? String(profile.timeout_s)}
                      disabled={busy} onChange={event => setTimeoutDrafts(rows => ({ ...rows, [profile.id]: event.target.value }))}>
                      {[60, 120, 180, 300, 600].map(seconds => <option key={seconds} value={seconds}>{seconds < 60 ? seconds + ' 秒' : seconds / 60 + ' 分钟'}</option>)}
                    </select>
                  </label>
                  <small>模型回复较慢时可以延长等待。超时后请求会保留待核实状态，不会自动重发。</small>
                  <button className="text-button" type="button" disabled={busy || Number(timeoutDrafts[profile.id] ?? profile.timeout_s) === profile.timeout_s}
                    onClick={() => onUpdateProfile(profile.id, { timeout_s: Number(timeoutDrafts[profile.id] ?? profile.timeout_s) })}>
                    保存等待时间
                  </button>
                </details>
              </div>
              <div className="profile-actions">
                <input
                  value={keyDrafts[profile.id] ?? ''}
                  type="password"
                  placeholder="更新密钥"
                  disabled={busy}
                  onChange={(event) => setKeyDrafts((rows) => ({ ...rows, [profile.id]: event.target.value }))}
                />
                <button className="text-button" disabled={busy || !keyDrafts[profile.id]} onClick={() => {
                  const outcome = onSetKey(profile.id, keyDrafts[profile.id]);
                  // The draft stays until the server confirmed the rotation.
                  clearOnSuccess(outcome, () => setKeyDrafts((rows) => ({ ...rows, [profile.id]: '' })));
                }}>保存密钥</button>
                <button className="text-button" disabled={busy} onClick={() => onTestProfile(profile.id)}>测试</button>
                <button className="text-button" disabled={busy || !onTestGeneration} title="真正调用一次生成接口，检查模型能否按 JSON 约定回答"
                  onClick={() => onTestGeneration?.(profile.id)}>生成验收</button>
                <button className="text-button" disabled={busy} title="把此模型用于镜头规划环节" onClick={() => onRoute('shot_planner', profile.id)}>用作镜头规划</button>
                <button className="text-button" disabled={busy} title="把此模型用于剧本规划环节" onClick={() => onRoute('script_planner', profile.id)}>用作剧本规划</button>
                <button className="text-button" disabled={busy} title="把此模型用于画面审片环节" onClick={() => onRoute('vlm_reviewer', profile.id)}>用作画面审片</button>
                <button className="text-button" disabled={busy} title="把此模型用于语音核对环节" onClick={() => onRoute('asr_reviewer', profile.id)}>用作语音核对</button>
                <button className="icon-button tiny" disabled={busy} onClick={() => onDeleteProfile(profile.id)}><Trash2 size={12} /></button>
              </div>
            </div>
          );})}
          <div className="route-row">
            {routes.length === 0 ? <span className="empty-inline">还没给各环节指定模型；真实准备尚未完成，请明确指定文本工种使用的模型。</span> : routes.map((route) => (
              <span className="route-chip" key={route.role}>{label(routeRoleLabels, route.role)} → {route.profile_name}</span>
            ))}
          </div>
        </div>

  );
}
