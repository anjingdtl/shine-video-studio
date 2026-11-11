import { useState, type FormEvent } from 'react';
import {
  CheckCircle2, Copy, FlaskConical, KeyRound, Pencil, Plus, Power, Save, ShieldCheck, Trash2,
} from 'lucide-react';
import type { AgentRoute, ProviderProfile } from '../api';
import { label, providerKindLabels, reasoningEffortLabels } from '../constants';

type Draft = {
  name: string; kind: string; base_url: string; model: string; api_key: string;
  timeout_s: number; max_retries: number; temperature: number;
  max_completion_tokens: string; reasoning_effort: string;
};

type Props = {
  profiles: ProviderProfile[]; routes: AgentRoute[]; busy: boolean;
  onCreateProfile: (payload: Record<string, unknown>) => Promise<boolean> | void;
  onUpdateProfile: (id: string, payload: Record<string, unknown>) => Promise<boolean> | void;
  onDuplicateProfile: (id: string, payload: Record<string, unknown>) => Promise<boolean> | void;
  onTestProfile: (id: string) => void;
  onTestGeneration: (id: string) => void;
  onSetKey: (id: string, key: string) => Promise<boolean> | void;
  onDeleteProfile: (id: string) => void;
};

const emptyDraft: Draft = {
  name: '', kind: 'openai_compatible', base_url: '', model: '', api_key: '',
  timeout_s: 180, max_retries: 2, temperature: 0.2,
  max_completion_tokens: '', reasoning_effort: '',
};

const defaultEndpoint: Record<string, string> = {
  openai_compatible: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com',
  ollama: 'http://127.0.0.1:11434/v1',
  mock: '',
};

function clearWhenSaved(outcome: Promise<boolean> | void, onSuccess: () => void): void {
  if (outcome && typeof outcome.then === 'function') {
    void outcome.then(ok => { if (ok !== false) onSuccess(); });
  } else {
    onSuccess();
  }
}

function checkLabel(value?: string, kind: 'connection' | 'catalog' | 'generation' = 'connection'): string {
  if (kind === 'connection') {
    return ({ CONNECTED: '连接成功', REACHABLE_AUTH_UNVERIFIED: '服务可达，鉴权待生成检查', FAILED: '连接失败', EXPIRED: '配置变更，证据已过期', NOT_CHECKED: '尚未检查' } as Record<string, string>)[value ?? ''] ?? '尚未检查';
  }
  if (kind === 'catalog') {
    return ({ LISTED: '目录已列出', NOT_LISTED: '目录未列出此模型', INVALID: '目录响应无法识别', UNAVAILABLE: '服务未提供模型目录', NOT_REPORTED: '目录状态未报告', EXPIRED: '证据已过期', NOT_CHECKED: '尚未检查' } as Record<string, string>)[value ?? ''] ?? '尚未检查';
  }
  return ({ PASSED: '结构化生成通过', PASSED_CATALOG_UNAVAILABLE: '生成通过 · 目录不可用', FAILED: '生成未通过', EXPIRED: '配置变更，证据已过期', NOT_CHECKED: '尚未检查' } as Record<string, string>)[value ?? ''] ?? '尚未检查';
}

function secretLabel(profile: ProviderProfile): string {
  const status = profile.evidence?.secret_status ?? profile.secret_status;
  if (status === 'stored') return '密钥已安全保存';
  if (status === 'missing') return '密钥文件缺失，请重新保存';
  if (status === 'unreadable') return '密钥无法读取，请重新保存';
  return profile.has_api_key ? '密钥已安全保存' : '无需密钥或尚未填写';
}

function ProfileEditor({
  profile, busy, onSave,
}: { profile: ProviderProfile; busy: boolean; onSave: (payload: Record<string, unknown>) => Promise<boolean> | void }) {
  const [name, setName] = useState(profile.name);
  const [kind, setKind] = useState(profile.kind);
  const [baseUrl, setBaseUrl] = useState(profile.base_url);
  const [model, setModel] = useState(profile.model);
  const [timeout, setTimeout] = useState(String(profile.timeout_s));
  const [retries, setRetries] = useState(String(profile.max_retries));
  const [temperature, setTemperature] = useState(String(profile.temperature));
  const [outputTokens, setOutputTokens] = useState(String(profile.generation_options?.max_completion_tokens ?? ''));
  const [reasoning, setReasoning] = useState(profile.generation_options?.reasoning_effort ?? '');

  function save(event: FormEvent) {
    event.preventDefault();
    const generationOptions = kind === 'openai_compatible' ? {
      ...(outputTokens.trim() ? { max_completion_tokens: Number(outputTokens) } : {}),
      ...(reasoning ? { reasoning_effort: reasoning } : {}),
    } : {};
    void onSave({
      name, kind, base_url: baseUrl, model, timeout_s: Number(timeout),
      max_retries: Number(retries), temperature: Number(temperature), generation_options: generationOptions,
    });
  }

  return <details className="llm-advanced-settings">
    <summary><Pencil size={13} /> 编辑连接与模型参数</summary>
    <form className="llm-model-form" onSubmit={save}>
      <label>配置名称<input value={name} required maxLength={80} onChange={event => setName(event.target.value)} /></label>
      <label>服务平台<select value={kind} onChange={event => setKind(event.target.value)}>
        {['openai_compatible', 'anthropic', 'ollama', 'mock'].map(value => <option key={value} value={value}>{label(providerKindLabels, value)}</option>)}
      </select></label>
      <label>接口地址<input value={baseUrl} required onChange={event => setBaseUrl(event.target.value)} /></label>
      <label>Model ID<input value={model} required onChange={event => setModel(event.target.value)} /></label>
      <details className="llm-advanced-settings llm-advanced-nested"><summary>高级参数</summary>
        <div className="llm-advanced-grid">
          <label>最长等待（秒）<input aria-label={'最长等待 ' + profile.name} type="number" min="1" max="600" value={timeout} onChange={event => setTimeout(event.target.value)} /></label>
          <label>失败重试次数<input type="number" min="0" max="5" value={retries} onChange={event => setRetries(event.target.value)} /></label>
          <label>温度<input type="number" min="0" max="2" step="0.1" value={temperature} onChange={event => setTemperature(event.target.value)} /></label>
          {kind === 'openai_compatible' && <>
            <label>回复上限（token）<input aria-label={'回复上限 ' + profile.name} type="number" min="1" max="524288" value={outputTokens} onChange={event => setOutputTokens(event.target.value)} /></label>
            <label>思考深度<select value={reasoning} onChange={event => setReasoning(event.target.value)}>
              <option value="">服务商默认</option>
              {['low', 'medium', 'high', 'xhigh', 'max'].map(value => <option key={value} value={value}>{label(reasoningEffortLabels, value)}</option>)}
            </select></label>
          </>}
        </div>
        <small>等待时间不改变任务的输入上下文检查、创作输出预算或远端发送授权。</small>
      </details>
      <button className="btn-secondary" type="submit" disabled={busy || !name.trim() || !baseUrl.trim() || !model.trim()}><Save size={13} /> 保存修改</button>
    </form>
  </details>;
}

function DuplicateForm({
  profile, busy, onDuplicate,
}: { profile: ProviderProfile; busy: boolean; onDuplicate: (id: string, payload: Record<string, unknown>) => Promise<boolean> | void }) {
  const [name, setName] = useState(`${profile.name} 副本`);
  const [model, setModel] = useState(profile.model);
  const [copyCredential, setCopyCredential] = useState(false);
  const [open, setOpen] = useState(false);
  return <div className="llm-duplicate">
    <button className="btn-text" type="button" disabled={busy} onClick={() => setOpen(value => !value)}><Copy size={13} /> 复制配置</button>
    {open && <form className="llm-duplicate-form" onSubmit={event => {
      event.preventDefault();
      const outcome = onDuplicate(profile.id, { name, model, copy_credential: copyCredential });
      clearWhenSaved(outcome, () => setOpen(false));
    }}>
      <label>副本名称<input value={name} required maxLength={80} onChange={event => setName(event.target.value)} /></label>
      <label>Model ID<input value={model} required onChange={event => setModel(event.target.value)} /></label>
      <label className="llm-copy-key"><input type="checkbox" checked={copyCredential} onChange={event => setCopyCredential(event.target.checked)} />
        由本机安全存储创建独立密钥副本</label>
      <p>副本默认不共享可删除的密钥引用。也可以不复制密钥，之后单独填写。</p>
      <button className="btn-secondary" type="submit" disabled={busy || !name.trim() || !model.trim()}>创建副本</button>
    </form>}
  </div>;
}

export function LLMConfiguration({
  profiles, routes, busy, onCreateProfile, onUpdateProfile, onDuplicateProfile,
  onTestProfile, onTestGeneration, onSetKey, onDeleteProfile,
}: Props) {
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});

  function changeKind(kind: string) {
    setDraft(current => ({ ...current, kind, base_url: current.base_url || defaultEndpoint[kind] || '' }));
  }

  function create(event: FormEvent) {
    event.preventDefault();
    const payload = {
      name: draft.name.trim(), kind: draft.kind, base_url: draft.base_url.trim(), model: draft.model.trim(),
      api_key: draft.api_key || null, timeout_s: draft.timeout_s, max_retries: draft.max_retries,
      temperature: draft.temperature,
      generation_options: draft.kind === 'openai_compatible' ? {
        ...(draft.max_completion_tokens.trim() ? { max_completion_tokens: Number(draft.max_completion_tokens) } : {}),
        ...(draft.reasoning_effort ? { reasoning_effort: draft.reasoning_effort } : {}),
      } : {},
    };
    const outcome = onCreateProfile(payload);
    clearWhenSaved(outcome, () => setDraft(emptyDraft));
  }

  return <section className="llm-manager" aria-label="AI 模型管理中心">
    <div className="llm-manager-heading">
      <div>
        <span className="section-kicker">MODEL LIBRARY</span>
        <h3><KeyRound size={16} /> AI 模型管理中心</h3>
        <p>在此管理不同平台的文本模型。密钥保存在本机安全存储，不会回显给浏览器。</p>
      </div>
      <span className="llm-profile-count">{profiles.length} 个模型</span>
    </div>

    <details className="llm-add-model" open={profiles.length === 0}>
      <summary><Plus size={14} /> 添加模型</summary>
      <form className="llm-model-form" onSubmit={create}>
        <label>配置名称<input aria-label="模型配置名称" value={draft.name} required maxLength={80} placeholder="例如：创作主模型" onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
        <label>服务平台<select aria-label="服务平台" value={draft.kind} onChange={event => changeKind(event.target.value)}>
          {['openai_compatible', 'anthropic', 'ollama'].map(value => <option key={value} value={value}>{label(providerKindLabels, value)}</option>)}
        </select></label>
        <label>接口地址<input aria-label="接口地址" value={draft.base_url} required placeholder="https://…/v1" onChange={event => setDraft({ ...draft, base_url: event.target.value })} /></label>
        <label>Model ID<input aria-label="Model ID" value={draft.model} required placeholder="服务商提供的准确模型 ID" onChange={event => setDraft({ ...draft, model: event.target.value })} /></label>
        <label>API Key（本机保存）<input aria-label="API Key" type="password" autoComplete="new-password" value={draft.api_key} placeholder="本地服务可留空" onChange={event => setDraft({ ...draft, api_key: event.target.value })} /></label>
        <details className="llm-advanced-settings"><summary>高级参数（可选）</summary>
          <div className="llm-advanced-grid">
            <label>最长等待（秒）<input aria-label="最长等待" type="number" min="1" max="600" value={draft.timeout_s} onChange={event => setDraft({ ...draft, timeout_s: Number(event.target.value) })} /></label>
            <label>失败重试次数<input aria-label="失败重试次数" type="number" min="0" max="5" value={draft.max_retries} onChange={event => setDraft({ ...draft, max_retries: Number(event.target.value) })} /></label>
            <label>温度<input aria-label="温度" type="number" min="0" max="2" step="0.1" value={draft.temperature} onChange={event => setDraft({ ...draft, temperature: Number(event.target.value) })} /></label>
            {draft.kind === 'openai_compatible' && <>
              <label>回复上限（token）<input aria-label="回复上限 token" type="number" min="1" max="524288" value={draft.max_completion_tokens} placeholder="留空使用服务商默认" onChange={event => setDraft({ ...draft, max_completion_tokens: event.target.value })} /></label>
              <label>思考深度<select aria-label="思考深度" value={draft.reasoning_effort} onChange={event => setDraft({ ...draft, reasoning_effort: event.target.value })}>
                <option value="">服务商默认</option>
                {['low', 'medium', 'high', 'xhigh', 'max'].map(value => <option key={value} value={value}>{label(reasoningEffortLabels, value)}</option>)}
              </select></label>
            </>}
          </div>
          <small>回复上限是生成输出预算；输入上下文仍按模型能力检查。</small>
        </details>
        <button className="btn-secondary" type="submit" disabled={busy || !draft.name.trim() || !draft.base_url.trim() || !draft.model.trim()}><Plus size={13} /> 添加</button>
      </form>
    </details>

    <div className="llm-model-list">
      {profiles.length === 0 && <p className="empty-inline">还没有模型。先添加一套文本模型，再完成连接与结构化生成检查。</p>}
      {profiles.map(profile => {
        const routeUses = routes.filter(route => route.provider_profile_id === profile.id);
        const caps = profile.capabilities as { model_context?: { context_window_tokens?: number; source?: string } };
        const contextTokens = caps.model_context?.context_window_tokens;
        const evidence = profile.evidence;
        return <article className={`llm-model-card ${profile.enabled ? '' : 'disabled'}`} key={profile.id}>
          <div className="llm-model-card-head">
            <div>
              <h4>{profile.name}</h4>
              <p>{label(providerKindLabels, profile.kind)} · {profile.model || '未设置 Model ID'} · {profile.enabled ? '已启用' : '已停用'}</p>
            </div>
            <span className={`llm-secret-state ${profile.secret_status === 'stored' ? 'secure' : ''}`}><ShieldCheck size={13} /> {secretLabel(profile)}</span>
          </div>
          <div className="llm-evidence-grid" aria-label={profile.name + '检查状态'}>
            <span>连接：{checkLabel(evidence?.connection_status)}</span>
            <span>模型目录：{checkLabel(evidence?.catalog_status, 'catalog')}</span>
            <span>结构化生成：{checkLabel(evidence?.generation_status, 'generation')}</span>
          </div>
          {contextTokens ? <p className="llm-context-note">输入上下文：{contextTokens.toLocaleString()} tokens · {caps.model_context?.source?.includes('official') ? '官方模型说明' : '服务商模型目录'}</p>
            : <p className="llm-context-note">输入上下文由模型能力检查；每次创作仍会执行上下文预算校验。</p>}
          {routeUses.length > 0 && <p className="llm-route-usage">当前服务 {routeUses.length} 个 Agent</p>}
          <div className="llm-model-actions">
            <button className="btn-text" type="button" disabled={busy} onClick={() => onTestProfile(profile.id)}><FlaskConical size={13} /> 测试连接</button>
            <button className="btn-text" type="button" disabled={busy} onClick={() => onTestGeneration(profile.id)}><CheckCircle2 size={13} /> 测试结构化生成</button>
            <label className="llm-enabled-toggle"><Power size={13} />
              <input type="checkbox" checked={profile.enabled} disabled={busy} aria-label={'启用模型 ' + profile.name}
                onChange={event => void onUpdateProfile(profile.id, { enabled: event.target.checked })} /> {profile.enabled ? '启用中' : '已停用'}
            </label>
          </div>
          <ProfileEditor profile={profile} busy={busy} onSave={payload => onUpdateProfile(profile.id, payload)} />
          <details className="llm-advanced-settings">
            <summary><KeyRound size={13} /> 更新密钥</summary>
            <div className="llm-key-form">
              <input aria-label={'更新密钥 ' + profile.name} type="password" autoComplete="new-password" placeholder="输入新密钥；不会显示旧密钥"
                disabled={busy} value={keyDrafts[profile.id] ?? ''}
                onChange={event => setKeyDrafts(values => ({ ...values, [profile.id]: event.target.value }))} />
              <button className="btn-secondary" type="button" disabled={busy || !keyDrafts[profile.id]}
                onClick={() => clearWhenSaved(onSetKey(profile.id, keyDrafts[profile.id]), () => setKeyDrafts(values => ({ ...values, [profile.id]: '' })))}>
                保存新密钥
              </button>
            </div>
          </details>
          <DuplicateForm profile={profile} busy={busy} onDuplicate={onDuplicateProfile} />
          <button className="btn-text llm-delete" type="button" disabled={busy} onClick={() => {
            const roles = routeUses.map(route => route.role).join('、');
            const note = routeUses.length ? `当前还有 Agent 指向此模型：${roles}。` : '';
            if (window.confirm(`${note}删除模型配置？仍被 Agent 或未结束任务使用时，服务端会阻止删除。`)) onDeleteProfile(profile.id);
          }}><Trash2 size={13} /> 删除模型</button>
        </article>;
      })}
    </div>
    <p className="llm-security-note">连接检查、模型目录和结构化生成是三项不同证据。真实生成检查会产生一次服务商 API 请求；不会发送项目剧本或素材。配置变更后，旧检查会标记过期。</p>
  </section>;
}
