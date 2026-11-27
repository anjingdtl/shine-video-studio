import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Brain, Check, Database, Globe2, Loader2, Pencil, Plus, RefreshCw, Search,
  ShieldCheck, Sparkles, Trash2, UserRound,
} from 'lucide-react';

import { api, json, type AgentRoutePreview, type LlmOverview, type ProviderProfile } from '../api';
import {
  DEFAULT_COPILOT_PROFILE,
  applyCopilotProfile,
  getCopilotProfile,
  loadCopilotProfile,
  type CopilotProfile,
} from '../copilotProfile';

type ProjectOption = { id: string; title: string };

type MemorySource = {
  message_id: string;
  role: string | null;
  excerpt: string | null;
  created_at: string | null;
};

type MemoryRow = {
  id: string;
  scope: 'GLOBAL' | 'PROJECT';
  project_id: string | null;
  project_title: string | null;
  content: string;
  memory_type: string;
  source: 'MANUAL' | 'AUTO';
  source_session_id: string | null;
  source_message_ids: string[];
  content_revision: number;
  status: string;
  confirmed: boolean;
  created_at: string;
  updated_at: string;
  sources?: MemorySource[];
};

type MemoryStatus = {
  state: string;
  fts5: string;
  fts_rows?: number | null;
  vector?: {
    provider?: string;
    dim?: number;
    semantic_model?: string;
    mode?: string;
    note?: string;
  };
  vector_rows?: number;
  stale_vectors?: number;
  active_memories?: number;
  counts?: {
    active?: number;
    global?: number;
    project?: number;
    auto?: number;
    manual?: number;
    deleted_tombstones?: number;
  };
  memory_enabled?: boolean;
  auto_memory_enabled?: boolean;
  notice?: string;
  degraded_fallback?: string;
};

const MEMORY_TYPE_LABELS: Record<string, string> = {
  PREFERENCE: '偏好',
  PROJECT_DECISION: '项目决策',
  CREATIVE_HABIT: '创作习惯',
  CONSTRAINT: '约束',
  EXPERIENCE: '经验',
  SUMMARY: '摘要',
};

const STATUS_LABELS: Record<string, string> = {
  OK: '正常',
  DEGRADED: '已降级（文字检索可用）',
  REBUILD_REQUIRED: '需要重建索引',
  CORRUPT: '索引损坏（可重建）',
  DISABLED: '长期记忆已关闭',
};

function memoryTypeLabel(value: string): string {
  return MEMORY_TYPE_LABELS[value] || value;
}

function memoryScopeLabel(row: MemoryRow): string {
  if (row.scope === 'GLOBAL') return '全局';
  return row.project_title ? `项目 · ${row.project_title}` : '项目专属';
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export type CopilotSettingsPageProps = {
  projects: ProjectOption[];
  projectId: string;
};

export function CopilotSettingsPage({ projects, projectId }: CopilotSettingsPageProps) {
  const [profile, setProfile] = useState<CopilotProfile>(() => getCopilotProfile());
  const [nickname, setNickname] = useState(profile.assistant_nickname);
  const [salutation, setSalutation] = useState(profile.user_salutation);
  const [startup, setStartup] = useState(profile.startup_memory);
  /** A slow profile read must never overwrite text the person already typed. */
  const typed = useRef({ nickname: false, salutation: false, startup: false });
  const [identityError, setIdentityError] = useState('');
  const [startupError, setStartupError] = useState('');
  const [saving, setSaving] = useState<'identity' | 'startup' | 'llm' | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [llmProfiles, setLlmProfiles] = useState<ProviderProfile[]>([]);
  const [copilotProfileId, setCopilotProfileId] = useState('');
  const [savedCopilotProfileId, setSavedCopilotProfileId] = useState('');
  const [routeRevision, setRouteRevision] = useState('');
  const [llmError, setLlmError] = useState('');

  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [memories, setMemories] = useState<MemoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingMemories, setLoadingMemories] = useState(false);
  const [memoryError, setMemoryError] = useState('');
  const [scopeFilter, setScopeFilter] = useState<'ALL' | 'GLOBAL' | 'PROJECT'>('ALL');
  const [projectFilter, setProjectFilter] = useState('');
  const [query, setQuery] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [refreshEpoch, setRefreshEpoch] = useState(0);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [clearProjectId, setClearProjectId] = useState(projectId || '');

  const [newContent, setNewContent] = useState('');
  const [newScope, setNewScope] = useState<'GLOBAL' | 'PROJECT'>('GLOBAL');
  const [newProject, setNewProject] = useState(projectId || projects[0]?.id || '');
  const [newType, setNewType] = useState('PREFERENCE');
  const [editingId, setEditingId] = useState('');
  const [editContent, setEditContent] = useState('');
  const [editType, setEditType] = useState('SUMMARY');
  const [expandedSources, setExpandedSources] = useState<Record<string, boolean>>({});

  const selectedProject = useMemo(
    () => projects.find((row) => row.id === projectId) || null,
    [projects, projectId],
  );

  const applyProfile = useCallback((next: Partial<CopilotProfile>) => {
    const merged = applyCopilotProfile({ ...getCopilotProfile(), ...next });
    setProfile(merged);
    return merged;
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const payload = await api<MemoryStatus>('/support/memories/status');
      setStatus(payload);
    } catch (err) {
      setMemoryError(err instanceof Error ? err.message : '无法读取本地记忆状态。');
    }
  }, []);

  const loadProfile = useCallback(async () => {
    const payload = await loadCopilotProfile(true);
    setProfile(payload);
    if (!typed.current.nickname) setNickname(payload.assistant_nickname);
    if (!typed.current.salutation) setSalutation(payload.user_salutation);
    if (!typed.current.startup) setStartup(payload.startup_memory);
  }, []);

  const loadMemories = useCallback(async () => {
    setLoadingMemories(true);
    setMemoryError('');
    try {
      const params = new URLSearchParams();
      if (scopeFilter !== 'ALL') params.set('scope', scopeFilter);
      if (projectFilter) params.set('project_id', projectFilter);
      if (appliedQuery.trim()) params.set('q', appliedQuery.trim());
      params.set('limit', '200');
      const payload = await api<{ memories: MemoryRow[]; total: number }>(
        `/support/memories?${params.toString()}`,
      );
      setMemories(payload.memories || []);
      setTotal(Number(payload.total) || 0);
    } catch (err) {
      setMemoryError(err instanceof Error ? err.message : '无法读取长期记忆。');
    } finally {
      setLoadingMemories(false);
    }
  }, [scopeFilter, projectFilter, appliedQuery]);

  const loadLlmRoute = useCallback(async () => {
    try {
      const overview = await api<LlmOverview>('/llm/overview');
      setLlmProfiles(overview.profiles || []);
      setRouteRevision(overview.route_revision || '');
      const current = (overview.routes || []).find((row) => row.role === 'support_agent')?.provider_profile_id || '';
      setCopilotProfileId(current);
      setSavedCopilotProfileId(current);
    } catch {
      // Older test stubs may not register /llm/overview; keep rest of page working.
    }
  }, []);

  useEffect(() => {
    void loadProfile();
    void loadStatus();
    void loadLlmRoute();
  }, [loadProfile, loadStatus, loadLlmRoute]);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories, refreshEpoch]);

  useEffect(() => {
    setClearProjectId((current) => current || projectId || projects[0]?.id || '');
    setNewProject((current) => current || projectId || projects[0]?.id || '');
  }, [projectId, projects]);

  const refreshAfterMutation = async (message: string) => {
    setNotice(message);
    setError('');
    setRefreshEpoch((value) => value + 1);
    await loadStatus();
  };

  const validateIdentity = (): string => {
    const trimmed = nickname.trim();
    if (!trimmed) return '管家昵称不能为空。';
    if (trimmed.length > 20) return '管家昵称最多 20 个字符。';
    if (/[<>]/.test(trimmed)) return '管家昵称不能包含 HTML 标签或尖括号。';
    if (salutation.trim().length > 20) return '对用户的称呼最多 20 个字符。';
    if (/[<>]/.test(salutation)) return '对用户的称呼不能包含 HTML 标签或尖括号。';
    return '';
  };

  const saveIdentity = async () => {
    const problem = validateIdentity();
    if (problem) {
      setIdentityError(problem);
      return;
    }
    setSaving('identity');
    setIdentityError('');
    try {
      const payload = await api<CopilotProfile>('/support/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          assistant_nickname: nickname.trim(),
          user_salutation: salutation.trim(),
        }),
      });
      applyProfile(payload);
      setNickname(payload.assistant_nickname);
      setSalutation(payload.user_salutation);
      setNotice('身份设置已保存；全局 AI 浮窗标题立即同步。');
      setError('');
    } catch (err) {
      setIdentityError(err instanceof Error ? err.message : '身份设置没有保存。');
    } finally {
      setSaving(null);
    }
  };

  const saveCopilotLlm = async () => {
    setSaving('llm');
    setLlmError('');
    try {
      const preview = await api<AgentRoutePreview>('/agent-routes/preview', json('POST', {
        updates: [{ role: 'support_agent', provider_profile_id: copilotProfileId || null }],
      }));
      if (!preview.can_apply) {
        const msg = preview.blockers?.[0]?.message || '当前不能保存模型分配，请先在「基础配置 → 文本模型」完成模型连接检查。';
        setLlmError(msg);
        return;
      }
      await api('/agent-routes/batch', json('PUT', {
        updates: preview.updates,
        expected_revision: preview.expected_revision || routeRevision,
      }));
      await loadLlmRoute();
      window.dispatchEvent(new CustomEvent('shine:copilot-llm-updated'));
      setNotice(copilotProfileId ? 'AI 管家解释模型已保存并立即生效。' : '已清空文本模型；AI 管家将仅展示「项目体检面板」，配置模型后可恢复对话与自主操作。');
      setError('');
    } catch (err) {
      setLlmError(err instanceof Error ? err.message : '模型分配没有保存。');
    } finally {
      setSaving(null);
    }
  };

  const saveStartup = async (value: string, message: string) => {
    if (value.length > 4000) {
      setStartupError('启动预读记忆最多 4000 个字符。');
      return;
    }
    setSaving('startup');
    setStartupError('');
    try {
      const payload = await api<CopilotProfile>('/support/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ startup_memory: value }),
      });
      applyProfile(payload);
      setStartup(payload.startup_memory);
      setNotice(message);
      setError('');
    } catch (err) {
      setStartupError(err instanceof Error ? err.message : '启动预读记忆没有保存。');
    } finally {
      setSaving(null);
    }
  };

  const togglePreference = async (
    key: 'memory_enabled' | 'auto_memory_enabled' | 'remote_memory_processing_consent' | 'remote_context_consent',
    value: boolean,
  ) => {
    const before = profile;
    setProfile({ ...profile, [key]: value });
    setError('');
    try {
      const payload = await api<CopilotProfile>('/support/preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
      });
      applyProfile(payload);
      setNotice('隐私与记忆设置已保存，下一次对话立即生效。');
      await loadStatus();
    } catch (err) {
      setProfile(before);
      setError(err instanceof Error ? err.message : '设置没有保存。');
    }
  };

  const createMemory = async () => {
    const content = newContent.trim();
    if (content.length < 2) {
      setMemoryError('记忆内容至少 2 个字符。');
      return;
    }
    if (newScope === 'PROJECT' && !newProject) {
      setMemoryError('项目记忆必须选择真实项目。');
      return;
    }
    setMemoryBusy(true);
    setMemoryError('');
    try {
      const result = await api<{ memory: MemoryRow | null; deduplicated: boolean; suppressed: boolean }>(
        '/support/memories',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scope: newScope,
            project_id: newScope === 'PROJECT' ? newProject : null,
            content,
            memory_type: newType,
          }),
        },
      );
      setNewContent('');
      await refreshAfterMutation(
        result.deduplicated ? '已有相同或高度相近的记忆，已合并，没有新增副本。' : '记忆已保存到本机。',
      );
    } catch (err) {
      setMemoryError(err instanceof Error ? err.message : '记忆没有保存。');
    } finally {
      setMemoryBusy(false);
    }
  };

  const beginEdit = (row: MemoryRow) => {
    setEditingId(row.id);
    setEditContent(row.content);
    setEditType(row.memory_type);
    setMemoryError('');
  };

  const saveEdit = async () => {
    if (!editingId) return;
    if (editContent.trim().length < 2) {
      setMemoryError('记忆内容至少 2 个字符。');
      return;
    }
    setMemoryBusy(true);
    setMemoryError('');
    try {
      await api(`/support/memories/${editingId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editContent.trim(), memory_type: editType }),
      });
      setEditingId('');
      await refreshAfterMutation('记忆已更新，旧向量与关键词索引已失效并重建。');
    } catch (err) {
      setMemoryError(err instanceof Error ? err.message : '记忆没有更新。');
    } finally {
      setMemoryBusy(false);
    }
  };

  const deleteMemory = async (row: MemoryRow) => {
    if (!window.confirm('删除后这条记忆不会再被召回。确定删除吗？')) return;
    setMemoryBusy(true);
    setMemoryError('');
    try {
      await api(`/support/memories/${row.id}`, { method: 'DELETE' });
      await refreshAfterMutation('记忆已删除；检索索引中的旧条目已同时移除。');
    } catch (err) {
      setMemoryError(err instanceof Error ? err.message : '记忆没有删除。');
    } finally {
      setMemoryBusy(false);
    }
  };

  const clearMemories = async (scope: 'PROJECT' | 'AUTO') => {
    if (scope === 'PROJECT' && !clearProjectId) {
      setMemoryError('请先选择要清除记忆的项目。');
      return;
    }
    const label = scope === 'PROJECT' ? '这个项目的全部记忆' : '全部自动生成的记忆';
    if (!window.confirm(`确定清除${label}吗？此操作不可撤销，但不会删除原始对话。`)) return;
    setMemoryBusy(true);
    setMemoryError('');
    try {
      await api('/support/memories/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope,
          project_id: scope === 'PROJECT' ? clearProjectId : null,
          confirm: true,
        }),
      });
      await refreshAfterMutation(scope === 'PROJECT' ? '该项目记忆已清除；其它项目记忆保留。' : '全部自动记忆已清除；手动记忆保留。');
    } catch (err) {
      setMemoryError(err instanceof Error ? err.message : '清除没有完成。');
    } finally {
      setMemoryBusy(false);
    }
  };

  const rebuildIndex = async () => {
    setMemoryBusy(true);
    setMemoryError('');
    try {
      await api('/support/memories/rebuild', { method: 'POST' });
      await refreshAfterMutation('本地检索索引已从权威记忆表重建。');
    } catch (err) {
      setMemoryError(err instanceof Error ? err.message : '索引没有重建。');
    } finally {
      setMemoryBusy(false);
    }
  };

  const counts = status?.counts || {};
  const statusLabel = STATUS_LABELS[status?.state || ''] || status?.state || '正在读取';

  return (
    <div className="director-page copilot-settings" id="copilot">
      {notice && <p className="copilot-settings-notice" role="status"><Check size={14} />{notice}</p>}
      {error && <p className="copilot-settings-error" role="alert">{error}</p>}

      <section className="card section-card copilot-settings-card" id="copilot-identity">
        <div className="section-heading">
          <div>
            <div className="section-kicker">基础身份</div>
            <h2>给管家一个名字，也告诉它怎么称呼你</h2>
            <p>修改后全局 AI 浮窗标题立即同步；不会影响历史会话、任务授权或任何业务接口。</p>
          </div>
          <span className="section-icon"><Sparkles size={16} /></span>
        </div>
        <div className="copilot-settings-grid">
          <label className="copilot-field">
            <span>管家昵称</span>
            <input
              aria-label="管家昵称"
              value={nickname}
              maxLength={20}
              placeholder={DEFAULT_COPILOT_PROFILE.assistant_nickname}
              onChange={(event) => {
                typed.current.nickname = true;
                setNickname(event.target.value);
                setIdentityError('');
              }}
            />
            <small>{nickname.length}/20 · 默认「{DEFAULT_COPILOT_PROFILE.assistant_nickname}」；不允许 HTML 或系统指令。</small>
          </label>
          <label className="copilot-field">
            <span>AI 对你的称呼</span>
            <input
              aria-label="对用户的称呼"
              value={salutation}
              maxLength={20}
              placeholder="例如：导演（可留空）"
              onChange={(event) => {
                typed.current.salutation = true;
                setSalutation(event.target.value);
                setIdentityError('');
              }}
            />
            <small>{salutation.length}/20 · 留空时管家不会强行称呼你。</small>
          </label>
        </div>
        {identityError && <p className="copilot-settings-error" role="alert">{identityError}</p>}
        <div className="section-footer">
          <span><UserRound size={13} /> 身份只影响交流方式，不改变权限。</span>
          <div>
            <button type="button" className="btn-primary" onClick={() => void saveIdentity()} disabled={saving === 'identity'}>
              {saving === 'identity' ? <Loader2 size={14} className="spin" /> : <Check size={14} />}
              保存身份
            </button>
          </div>
        </div>
      </section>

      <section className="card section-card copilot-settings-card" id="copilot-llm">
        <div className="section-heading">
          <div>
            <div className="section-kicker">解释模型（与 Agent 路由同一台账）</div>
            <h2>选择用于回答问题与解释诊断的文本模型</h2>
            <p>与「基础配置 → 文本模型 → AI 管家（support_agent）」读写同一条分配记录；保存后立即对下一条提问生效。</p>
          </div>
          <span className="section-icon"><Sparkles size={16} /></span>
        </div>
        <label className="copilot-field">
          <span>AI 管家使用的模型</span>
          <select
            aria-label="AI 管家使用的模型"
            value={copilotProfileId}
            onChange={(event) => {
              setCopilotProfileId(event.target.value);
              setLlmError('');
            }}
          >
            <option value="">未分配（仅提供项目体检面板，不开启 AI 对话）</option>
            {llmProfiles.map((row) => (
              <option
                key={row.id}
                value={row.id}
                disabled={(!row.enabled || row.kind === 'mock' || !row.model.trim()) && row.id !== savedCopilotProfileId}
              >
                {row.name} · {row.model || '缺少 Model ID'}{!row.enabled ? '（已停用）' : row.kind === 'mock' ? '（演示模型）' : ''}
              </option>
            ))}
          </select>
          <small>
            {savedCopilotProfileId
              ? `当前生效：${llmProfiles.find((r) => r.id === savedCopilotProfileId)?.name || '已分配模型'}`
              : '当前生效：仅项目体检面板（未配置文本模型，不消耗 Token）'}
          </small>
        </label>
        {llmError && <p className="copilot-settings-error" role="alert">{llmError}</p>}
        <div className="section-footer">
          <span><ShieldCheck size={13} /> 模型仅做回答与证据解释，不能绕过预览确认或修改源码。</span>
          <div>
            <button
              type="button"
              className="btn-primary"
              onClick={() => void saveCopilotLlm()}
              disabled={saving === 'llm' || copilotProfileId === savedCopilotProfileId}
            >
              {saving === 'llm' ? <Loader2 size={14} className="spin" /> : <Check size={14} />}
              保存模型选择
            </button>
          </div>
        </div>
      </section>

      <section className="card section-card copilot-settings-card" id="copilot-startup">
        <div className="section-heading">
          <div>
            <div className="section-kicker">启动预读记忆</div>
            <h2>每次新会话，先读一遍你的长期说明</h2>
            <p>内容直接注入现有管家上下文，不会为了预读额外调用一次付费模型。</p>
          </div>
          <span className="section-icon"><Brain size={16} /></span>
        </div>
        <label className="copilot-field">
          <span>给管家的长期说明</span>
          <textarea
            aria-label="启动预读记忆"
            rows={7}
            maxLength={4000}
            value={startup}
            placeholder="例如：我主要制作 AI 影视短剧，喜欢电影化叙事。回答时尽量使用通俗中文……"
            onChange={(event) => {
              typed.current.startup = true;
              setStartup(event.target.value);
              setStartupError('');
            }}
          />
          <small>{startup.length}/4000 个字符 · 可以清空或恢复默认；密钥与敏感路径会被拒绝。</small>
        </label>
        {startupError && <p className="copilot-settings-error" role="alert">{startupError}</p>}
        <div className="section-footer">
          <span><ShieldCheck size={13} /> 预读内容不能提高权限，也不能覆盖系统安全规则与实时业务事实。</span>
          <div>
            <button type="button" className="btn-secondary" onClick={() => void saveStartup('', '启动预读记忆已恢复默认（空白）。')} disabled={saving === 'startup'}>
              恢复默认
            </button>
            <button type="button" className="btn-secondary" onClick={() => void saveStartup('', '启动预读记忆已清空。')} disabled={saving === 'startup' || !startup}>
              清空
            </button>
            <button type="button" className="btn-primary" onClick={() => void saveStartup(startup, '启动预读记忆已保存，新会话会自动预读。')} disabled={saving === 'startup'}>
              {saving === 'startup' ? <Loader2 size={14} className="spin" /> : <Check size={14} />}
              保存预读
            </button>
          </div>
        </div>
      </section>

      <section className="card section-card copilot-settings-card" id="copilot-memories">
        <div className="section-heading">
          <div>
            <div className="section-kicker">长期记忆</div>
            <h2>本机记得你的偏好与项目历史</h2>
            <p>只保存经过筛选的长期有效信息；原始对话仍以会话记录为准。项目记忆只在对应项目召回。</p>
          </div>
          <span className="section-icon"><Database size={16} /></span>
        </div>

        <div className="copilot-toggle-rows">
          <label className="copilot-toggle">
            <input
              type="checkbox"
              aria-label="启用长期记忆"
              checked={profile.memory_enabled}
              onChange={(event) => void togglePreference('memory_enabled', event.target.checked)}
            />
            <span><b>长期记忆</b><small>关闭后不再召回记忆；不会删除已有记忆。</small></span>
          </label>
          <label className="copilot-toggle">
            <input
              type="checkbox"
              aria-label="启用自动提炼"
              checked={profile.auto_memory_enabled}
              onChange={(event) => void togglePreference('auto_memory_enabled', event.target.checked)}
            />
            <span><b>自动提炼</b><small>在会话的合适节点批量提炼；不会每说一句话就调用模型。</small></span>
          </label>
        </div>

        <div className="copilot-status-grid" aria-label="本地记忆状态">
          <div><span>检索状态</span><b className={`copilot-state state-${(status?.state || 'unknown').toLowerCase()}`}>{statusLabel}</b></div>
          <div><span>记忆数量</span><b>{counts.active ?? status?.active_memories ?? 0}</b></div>
          <div><span>全局 / 项目</span><b>{counts.global ?? 0} / {counts.project ?? 0}</b></div>
          <div><span>自动 / 手动</span><b>{counts.auto ?? 0} / {counts.manual ?? 0}</b></div>
          <div><span>FTS5</span><b>{status?.fts5 === 'AVAILABLE' ? '可用' : '不可用（已降级）'}</b></div>
          <div><span>本地向量</span><b>{status?.vector?.provider || 'local-hash-ngram-v1'} · {status?.vector?.dim || 256} 维</b></div>
        </div>
        {status?.state && status.state !== 'OK' && (
          <p className="copilot-settings-hint" role="status">
            {status.notice || status.degraded_fallback || '检索索引需要处理；AI 管家仍可对话，可点「重建索引」修复。'}
          </p>
        )}
        <p className="copilot-settings-hint">
          Embedding：{status?.vector?.semantic_model || 'NOT_CONFIGURED'}。本版本使用 CPU 本地哈希向量作为轻量检索适配，
          不占显卡、不下载模型；FTS5 是关键词退路。
        </p>

        <div className="copilot-memory-toolbar">
          <label className="copilot-inline-field">
            <span>范围</span>
            <select aria-label="记忆范围筛选" value={scopeFilter} onChange={(event) => setScopeFilter(event.target.value as typeof scopeFilter)}>
              <option value="ALL">全部</option>
              <option value="GLOBAL">全局</option>
              <option value="PROJECT">项目</option>
            </select>
          </label>
          <label className="copilot-inline-field">
            <span>项目</span>
            <select aria-label="记忆项目筛选" value={projectFilter} onChange={(event) => setProjectFilter(event.target.value)}>
              <option value="">全部项目</option>
              {projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}
            </select>
          </label>
          <label className="copilot-search">
            <Search size={14} />
            <input
              aria-label="搜索长期记忆"
              value={query}
              placeholder="搜索记忆内容"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter') setAppliedQuery(query); }}
            />
          </label>
          <button type="button" className="btn-secondary" onClick={() => setAppliedQuery(query.trim())}>搜索</button>
          <button type="button" className="btn-secondary" onClick={() => { setQuery(''); setAppliedQuery(''); }}>重置</button>
          <button type="button" className="btn-secondary" onClick={() => setRefreshEpoch((value) => value + 1)} disabled={loadingMemories}>
            {loadingMemories ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} 刷新
          </button>
        </div>

        <div className="copilot-memory-create">
          <label className="copilot-field">
            <span>手动新增记忆</span>
            <textarea
              aria-label="新记忆内容"
              rows={3}
              maxLength={2000}
              value={newContent}
              placeholder="写一条希望管家长期记住的偏好、项目决策或约束。"
              onChange={(event) => setNewContent(event.target.value)}
            />
          </label>
          <div className="copilot-memory-create-row">
            <label className="copilot-inline-field">
              <span>范围</span>
              <select aria-label="新记忆范围" value={newScope} onChange={(event) => setNewScope(event.target.value as 'GLOBAL' | 'PROJECT')}>
                <option value="GLOBAL">全局</option>
                <option value="PROJECT">项目专属</option>
              </select>
            </label>
            {newScope === 'PROJECT' && (
              <label className="copilot-inline-field">
                <span>项目</span>
                <select aria-label="新记忆项目" value={newProject} onChange={(event) => setNewProject(event.target.value)}>
                  {projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}
                </select>
              </label>
            )}
            <label className="copilot-inline-field">
              <span>分类</span>
              <select aria-label="新记忆分类" value={newType} onChange={(event) => setNewType(event.target.value)}>
                {Object.entries(MEMORY_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <button type="button" className="btn-primary" onClick={() => void createMemory()} disabled={memoryBusy || !newContent.trim()}>
              <Plus size={14} /> 保存记忆
            </button>
          </div>
        </div>

        {memoryError && <p className="copilot-settings-error" role="alert">{memoryError}</p>}
        <div className="copilot-memory-list" aria-label="长期记忆列表">
          {loadingMemories && memories.length === 0 && <p className="copilot-settings-hint"><Loader2 size={14} className="spin" /> 正在读取本机记忆…</p>}
          {!loadingMemories && memories.length === 0 && <p className="copilot-settings-hint">还没有符合当前筛选的长期记忆。</p>}
          {memories.map((row) => (
            <article key={row.id} className="copilot-memory-row">
              <div className="copilot-memory-meta">
                <span className={`copilot-chip ${row.source === 'AUTO' ? 'auto' : 'manual'}`}>{row.source === 'AUTO' ? '自动提炼' : '手动'}</span>
                <span className="copilot-chip">{memoryTypeLabel(row.memory_type)}</span>
                <span className="copilot-chip scope">{memoryScopeLabel(row)}</span>
                <span className="copilot-memory-time">更新 {formatTime(row.updated_at)} · 修订 {row.content_revision}</span>
              </div>
              {editingId === row.id ? (
                <div className="copilot-memory-edit">
                  <textarea
                    aria-label="编辑记忆内容"
                    rows={3}
                    maxLength={2000}
                    value={editContent}
                    onChange={(event) => setEditContent(event.target.value)}
                  />
                  <div>
                    <select aria-label="编辑记忆分类" value={editType} onChange={(event) => setEditType(event.target.value)}>
                      {Object.entries(MEMORY_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select>
                    <button type="button" className="btn-primary" onClick={() => void saveEdit()} disabled={memoryBusy}>保存</button>
                    <button type="button" className="btn-secondary" onClick={() => setEditingId('')}>取消</button>
                  </div>
                </div>
              ) : (
                <p className="copilot-memory-content">{row.content}</p>
              )}
              <div className="copilot-memory-actions">
                <button type="button" className="btn-text" onClick={() => setExpandedSources((value) => ({ ...value, [row.id]: !value[row.id] }))}>
                  来源
                </button>
                <button type="button" className="btn-text" onClick={() => beginEdit(row)}><Pencil size={12} /> 编辑</button>
                <button type="button" className="btn-text danger" onClick={() => void deleteMemory(row)}><Trash2 size={12} /> 删除</button>
              </div>
              {expandedSources[row.id] && (
                <div className="copilot-memory-sources">
                  <div>来源会话：{row.source_session_id || '手动创建'} · 消息数 {row.source_message_ids.length}</div>
                  {(row.sources || []).map((source) => (
                    <p key={source.message_id}><b>{source.role === 'assistant' ? '管家' : '你'}：</b>{source.excerpt || '（消息已清理）'}</p>
                  ))}
                  {(!row.sources || row.sources.length === 0) && <p>没有可展示的来源摘录。</p>}
                </div>
              )}
            </article>
          ))}
        </div>
        <div className="section-footer">
          <span>共 {total} 条符合当前筛选。</span>
          <div>
            <label className="copilot-inline-field compact">
              <span>清除项目</span>
              <select aria-label="要清除记忆的项目" value={clearProjectId} onChange={(event) => setClearProjectId(event.target.value)}>
                <option value="">选择项目</option>
                {projects.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}
              </select>
            </label>
            <button type="button" className="btn-secondary" onClick={() => void clearMemories('PROJECT')} disabled={memoryBusy || !clearProjectId}>
              清除该项目记忆
            </button>
            <button type="button" className="btn-secondary" onClick={() => void clearMemories('AUTO')} disabled={memoryBusy || !(counts.auto ?? 0)}>
              清除全部自动记忆
            </button>
            <button type="button" className="btn-secondary" onClick={() => void rebuildIndex()} disabled={memoryBusy}>
              <RefreshCw size={13} /> 重建索引
            </button>
          </div>
        </div>
        {selectedProject && scopeFilter === 'PROJECT' && !projectFilter && (
          <p className="copilot-settings-hint">当前作品是「{selectedProject.title}」；项目记忆默认只在该项目内召回。</p>
        )}
      </section>

      <section className="card section-card copilot-settings-card" id="copilot-privacy">
        <div className="section-heading">
          <div>
            <div className="section-kicker">隐私与记忆管理</div>
            <h2>记忆留在本机，去向由你决定</h2>
            <p>关闭是停止使用，删除是移除内容，两者不同。</p>
          </div>
          <span className="section-icon"><ShieldCheck size={16} /></span>
        </div>
        <div className="copilot-toggle-rows">
          <label className="copilot-toggle">
            <input
              type="checkbox"
              aria-label="允许远端模型提炼记忆"
              checked={profile.remote_memory_processing_consent}
              onChange={(event) => void togglePreference('remote_memory_processing_consent', event.target.checked)}
            />
            <span><b>允许远端 LLM 提炼记忆</b><small>未允许时，自动提炼在远端模型上不可用；仍可手动新增记忆。</small></span>
          </label>
          <label className="copilot-toggle">
            <input
              type="checkbox"
              aria-label="允许把召回记忆发送给远端模型"
              checked={profile.remote_context_consent}
              onChange={(event) => void togglePreference('remote_context_consent', event.target.checked)}
            />
            <span><b>允许召回记忆作为远端上下文</b><small>关闭时，远端模型只收到本机业务证据与预读说明，不收到召回的历史记忆。</small></span>
          </label>
        </div>
        <ul className="copilot-privacy-list">
          <li><Database size={13} /><span>记忆保存在本机 SQLite，与 AI 管家会话使用同一个数据库；不依赖云端向量库。</span></li>
          <li><Globe2 size={13} /><span>本地检索不需要联网；只有在你允许且配置了远端模型时，才会发送限定范围内的对话用于提炼。</span></li>
          <li><Trash2 size={13} /><span>删除单条记忆会立即移除关键词与向量条目；自动提炼不会把同一内容未经许可重新创建。</span></li>
          <li><ShieldCheck size={13} /><span>关闭长期记忆只停止召回和提炼，不删除已有记忆；清除操作需要明确确认。</span></li>
        </ul>
      </section>
    </div>
  );
}
