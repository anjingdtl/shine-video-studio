import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, HelpCircle, History, LockKeyhole, LockOpen, Sparkles } from 'lucide-react';
import { api, json, type Style, type StyleVersion } from '../api';
import { styleFields } from '../constants';
import {
  matchStyleOption, presetForStyle, styleCatalogHash, STYLE_CATALOG_VERSION,
  STYLE_DIMENSION_OPTIONS, STYLE_PRESETS, type StyleDimension,
} from '../styleCatalog';

type Props = {
  projectId: string;
  style: Style;
  setStyle: (updater: (value: Style) => Style) => void;
  /** The last server-known draft; adopt sends it so the server can refuse when
   *  another writer changed the draft in between (§7.2). */
  serverDraft: Style | null;
  currentStyle?: StyleVersion;
  styles: StyleVersion[];
  busy: boolean;
  onSave: () => void;
  onFreeze: () => void;
  onDraftApplied: () => void;
  onNavigate: (id: string) => void;
  onMessage: (text: string, kind?: 'success' | 'error') => void;
};

type ArtifactRow = {
  id: string; kind: string; stale: boolean; created_at: string;
  payload_json: Record<string, unknown>;
};

type FilmAgentInfo = {
  role: string; label: string; profile_id: string | null; tool_allowlist: string[];
};

/** Friendlier lead-ins for known failure codes; the raw code stays visible. */
const AI_ERROR_HINTS: Array<[RegExp, string]> = [
  [/FREEZE_PROJECT_RULEPACK_FIRST/, '首次使用 AI 助手前，需要先把官方规则包绑定到本项目（一次性操作）。'],
  [/REMOTE_AGENT_CONSENT_REQUIRED/, '本次请求需要先勾选同意把文本发送给已配置的 LLM 服务商。'],
  [/(LLM_TIMEOUT|LLM_CONNECTION|LLM_SERVER)/, 'AI 助手暂时无法响应；你的草稿未受影响，稍后可重试。'],
  [/AGENT_ROUTE_MISSING|PROFILE_MISSING|NO_ROUTE/, 'AI 助手尚未完成配置：先在「基础配置 → ② 模型与生成方案」里配置并测试模型。'],
];

/** Roles allowed to emit a StyleProposal; the first routed role is used. */
const STYLE_PROPOSAL_ROLES = ['director', 'cinematographer', 'scriptwriter', 'editor'];

function aiErrorText(message: string): string {
  for (const [pattern, hint] of AI_ERROR_HINTS) {
    if (pattern.test(message)) return hint;
  }
  return message;
}

export default function StylePanel({
  projectId, style, setStyle, serverDraft, currentStyle, styles,
  busy, onSave, onFreeze, onDraftApplied, onNavigate, onMessage,
}: Props) {
  const matchedPreset = presetForStyle(style);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [customFields, setCustomFields] = useState<Set<StyleDimension>>(new Set());
  const [aiRoles, setAiRoles] = useState<FilmAgentInfo[] | null>(null);
  const [rulepackId, setRulepackId] = useState<string | null>(null);
  const [rulepacks, setRulepacks] = useState<Array<{ id: string }>>([]);
  const [aiHint, setAiHint] = useState('');
  const [aiConsent, setAiConsent] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const [adoptBusy, setAdoptBusy] = useState(false);
  const [aiError, setAiError] = useState('');
  const [aiProposal, setAiProposal] = useState<{
    artifactId: string; suggestion: Style; rationale: string; fromHistory: boolean;
  } | null>(null);
  const freshProposalRef = useRef(false);

  useEffect(() => {
    const hasCustom = styleFields.some(([field]) => String(style[field] ?? '').trim() && !matchStyleOption(field, style[field]));
    if (hasCustom) setDetailsOpen(true);
  }, [style]);

  useEffect(() => {
    let alive = true;
    api<FilmAgentInfo[]>('/film-agents')
      .then((rows) => { if (alive) setAiRoles(rows); })
      .catch(() => { if (alive) setAiRoles([]); });
    api<{ rulepack_id: string | null }>(`/projects/${projectId}/rulepack`)
      .then((row) => { if (alive) setRulepackId(row.rulepack_id); })
      .catch(() => { if (alive) setRulepackId(null); });
    api<{ packs: Array<{ id: string }> }>('/h3-rulebook')
      .then((row) => { if (alive) setRulepacks(row.packs); })
      .catch(() => { if (alive) setRulepacks([]); });
    api<ArtifactRow[]>(`/projects/${projectId}/film-artifacts`)
      .then((rows) => {
        if (!alive || freshProposalRef.current) return;
        const latest = rows.find((row) => row.kind === 'StyleProposal' && !row.stale);
        if (latest) setAiProposal({ artifactId: latest.id, suggestion: (latest.payload_json as { suggestion: Style }).suggestion, rationale: String((latest.payload_json as { rationale?: string }).rationale ?? ''), fromHistory: true });
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [projectId]);

  const aiRole = aiRoles === null
    ? null
    : STYLE_PROPOSAL_ROLES.map((role) => aiRoles?.find((row) => row.role === role)).find((row) => row && row.profile_id) ?? null;
  const aiConfigured = Boolean(aiRole);
  const allSevenFilled = styleFields.every(([field]) => String(style[field] ?? '').trim().length > 0);
  // Freezing is a separate confirmation of the current snapshot.  It must stay
  // available after the same content was saved as a draft; only saving needs a
  // draft change to be meaningful.
  const canFreeze = allSevenFilled;
  const canSave = allSevenFilled && styleFields.some(([field]) => style[field] !== (serverDraft?.[field] ?? ''));
  const diffRows = aiProposal
    ? styleFields.map(([field, label]) => ({ field, label, current: style[field], suggested: aiProposal.suggestion[field] ?? '' }))
    : [];

  function applyPreset(presetId: string) {
    const preset = STYLE_PRESETS.find((row) => row.id === presetId);
    if (!preset) return;
    const hasEdits = styleFields.some(([field]) => String(style[field] ?? '').trim() && style[field] !== preset.values[field]);
    if (hasEdits && !window.confirm('当前草稿已有自定义修改，选择整套预设会覆盖这些修改。继续吗？')) return;
    setStyle(() => ({ ...preset.values }));
    setCustomFields(new Set());
    setAiProposal(null);
    onMessage(`已套用整套风格「${preset.label}」，确认无误后保存或冻结。`);
  }

  function chooseOption(field: StyleDimension, optionId: string) {
    if (optionId === 'custom') {
      setCustomFields((current) => new Set(current).add(field));
      setDetailsOpen(true);
      return;
    }
    const option = STYLE_DIMENSION_OPTIONS[field].find((row) => row.id === optionId);
    if (!option) return;
    setStyle((value) => ({ ...value, [field]: option.text }));
    setCustomFields((current) => { const next = new Set(current); next.delete(field); return next; });
  }

  async function recommend() {
    if (aiBusy || !aiRole || !projectId) return;
    setAiBusy(true);
    setAiError('');
    try {
      const artifact = await api<ArtifactRow>(
        `/projects/${projectId}/film-agents/${aiRole.role}/run`,
        json('POST', {
          tool: 'style_proposal',
          brief: aiHint.trim(),
          allow_remote_text: aiConsent,
          input_revision_ids: [],
        }),
      );
      freshProposalRef.current = true;
      setAiProposal({
        artifactId: artifact.id,
        suggestion: (artifact.payload_json as { suggestion: Style }).suggestion,
        rationale: String((artifact.payload_json as { rationale?: string }).rationale ?? ''),
        fromHistory: false,
      });
    } catch (error) {
      setAiError(aiErrorText(String((error as Error).message)));
    } finally {
      setAiBusy(false);
    }
  }

  async function adopt() {
    if (!aiProposal || adoptBusy) return;
    setAdoptBusy(true);
    try {
      await api(`/projects/${projectId}/film-artifacts/${aiProposal.artifactId}/style-draft`, json('POST', { base_style: serverDraft }));
      onMessage('已采纳到草稿（仅草稿；冻结是单独的确认步骤）。');
      setAiProposal(null);
      onDraftApplied();
    } catch (error) {
      onMessage(String((error as Error).message), 'error');
    } finally {
      setAdoptBusy(false);
    }
  }

  async function enableRulepack(quiet = false) {
    const packId = rulepacks[0]?.id;
    if (!packId) { onMessage('官方规则包列表不可用，请稍后重试。', 'error'); return false; }
    try {
      await api(`/projects/${projectId}/rulepack/freeze`, json('POST', { rulepack_id: packId }));
      setRulepackId(packId);
      if (!quiet) onMessage('官方规则包已绑定到本项目（一次性；升级规则包需要代码评审与测试）。');
      return true;
    } catch (error) {
      onMessage(String((error as Error).message), 'error');
      return false;
    }
  }

  /** 冻结 = 一次点击完成两件事：先绑定官方规则包（每个作品一次性），再冻结风格。
   *  两步各自写审计；绑定失败时不冻结，风格内容不受影响。 */
  async function freezeNow() {
    if (busy || !canFreeze) return;
    if (rulepackId === null && rulepacks.length > 0) {
      const bound = await enableRulepack(true);
      if (!bound) return;
      onMessage('官方规则包已绑定，正在冻结风格…');
    }
    onFreeze();
  }

  return (
    <section className="card section-card" id="style">
      <div className="section-heading">
        <div>
          <div className="section-kicker">② 定风格</div>
          <h2>作品风格</h2>
          <p>选一套风格，可调整细节；确认冻结后用于新镜头。</p>
        </div>
        <div className={`frozen-badge ${currentStyle ? 'is-frozen' : ''}`}>
          {currentStyle ? <><LockKeyhole size={13} /> 已冻结 V{currentStyle.version}</> : <><LockOpen size={13} /> 尚未冻结</>}
        </div>
      </div>

      <div className="module-guide">
        <HelpCircle size={13} />
        <div>
          <p><b>这一步要做什么：</b>选一套风格或逐项调整，然后点右下角的冻结按钮。第一次冻结会同时批准并绑定官方规则包，所以那一次按钮写的是「批准并冻结」；之后写的是「冻结为新版本」。</p>
          <p><b>为什么要冻结：</b>风格是七个维度的不可变版本，之后每个镜头都引用它；不冻结，下面的 AI 分镜会直接拒绝运行。</p>
        </div>
      </div>

      <div className="style-preset-row">
        <label className="style-preset-label" htmlFor="style-preset">整套风格
          <select
            id="style-preset"
            value={matchedPreset?.id ?? ''}
            onChange={(event) => applyPreset(event.target.value)}
            disabled={busy}
          >
            <option value="">自定义（当前内容）</option>
            {STYLE_PRESETS.map((preset) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}
          </select>
        </label>
        <p className="style-preset-summary">
          {matchedPreset ? matchedPreset.summary : '当前为自定义内容；可选一套整套风格作为起点，或在下方逐项调整。'}
        </p>
      </div>

      <button type="button" className="text-button style-details-toggle" onClick={() => setDetailsOpen((open) => !open)} aria-expanded={detailsOpen}>
        <ChevronDown size={13} /> {detailsOpen ? '收起细节' : '调整细节'}
      </button>
      {detailsOpen && <div className="style-grid">
        {styleFields.map(([field, label, hint], index) => {
          const matched = matchStyleOption(field, style[field]);
          const customActive = customFields.has(field) || (!matched && !!String(style[field] ?? '').trim());
          const selectValue = matched?.id ?? (String(style[field] ?? '').trim() || customActive ? 'custom' : '');
          return (
            <div className="style-field" key={field}>
              <span><i>0{index + 1}</i>{label}</span>
              <select
                aria-label={label}
                value={selectValue}
                onChange={(event) => chooseOption(field, event.target.value)}
                disabled={busy}
              >
                <option value="">请选择…</option>
                {STYLE_DIMENSION_OPTIONS[field].map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
                <option value="custom">自定义…</option>
              </select>
              {customActive && (
                <textarea
                  aria-label={`${label}（自定义）`}
                  
                  value={style[field]}
                  placeholder={hint}
                  rows={2}
                  disabled={busy}
                  onChange={(event) => {
                    // Stay in custom mode while editing: clearing the text must not unmount the textarea mid-edit.
                    setCustomFields((current) => new Set(current).add(field));
                    setStyle((value) => ({ ...value, [field]: event.target.value }));
                  }}
                />
              )}
              {!customActive && !String(style[field] ?? '').trim() && <small className="style-field-hint">{hint}</small>}
              {!customActive && String(style[field] ?? '').trim() && <small className="style-field-hint">{style[field]}</small>}
            </div>
          );
        })}
      </div>}

      <div className="style-ai">
        <div className="style-ai-row">
          <Sparkles size={15} />
          <b>AI 帮我推荐</b>
          {aiRole && <span className="style-ai-note">使用已配置的「{aiRole.label}」角色；建议只进入草稿，冻结仍由你确认。</span>}
        </div>
        {aiRoles !== null && !aiConfigured && <p className="style-ai-empty">
          尚未配置 AI 助手，预设和手工选择照常可用。
          <button type="button" className="text-button" onClick={() => onNavigate('settings')}>去配置 AI 助手</button>
        </p>}
        {aiConfigured && rulepackId === null && rulepacks.length > 0 && <p className="style-ai-empty">
          本作品还没绑定官方规则包：点下方「批准并冻结」时会一并完成绑定（一次性），不用单独操作。
        </p>}
        {aiConfigured && rulepackId !== null && <>
          <label className="style-ai-hint">给 AI 的补充说明（可留空）
            <input value={aiHint} maxLength={2000} onChange={(event) => setAiHint(event.target.value)} placeholder="例如：更接近夜戏质感的风格" />
          </label>
          <label className="check-label style-ai-consent">
            <input type="checkbox" checked={aiConsent} onChange={(event) => setAiConsent(event.target.checked)} />
            <span>我同意把项目设置与补充说明文本发给已配置的 AI 文字服务（不发送素材文件）。</span>
          </label>
          <button type="button" className="secondary-button" disabled={aiBusy} onClick={recommend}>
            <Sparkles size={13} /> {aiBusy ? '正在请求 AI 建议…' : '请求 AI 风格建议'}
            <span className="style-ai-catalog">目录 {STYLE_CATALOG_VERSION} · {styleCatalogHash()}</span>
            <span className="style-ai-catalog">{aiRole?.label}</span>
          </button>
        </>}
        {aiError && <p className="error-line" role="alert">{aiError}</p>}
      </div>

      {aiProposal && <div className="style-ai-proposal">
        <div className="style-ai-proposal-head">
          <b>AI 建议{aiProposal.fromHistory ? '（上次生成，可能基于旧内容）' : ''}</b>
          <button type="button" className="text-button" onClick={() => setAiProposal(null)}>不采用</button>
        </div>
        <p className="style-ai-rationale">{aiProposal.rationale}</p>
        <table className="style-diff">
          <thead><tr><th>维度</th><th>当前</th><th>建议</th><th>变化</th></tr></thead>
          <tbody>
            {diffRows.map((row) => (
              <tr key={row.field} className={row.current === row.suggested ? 'unchanged' : 'changed'}>
                <th>{row.label}</th><td>{row.current || '（空）'}</td><td>{row.suggested || '（空）'}</td>
                <td>{row.current === row.suggested ? '—' : '已调整'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="section-footer">
          <span>采纳只写入草稿；冻结是单独的确认步骤。</span>
          <div>
            <button type="button" className="primary-button" disabled={adoptBusy} onClick={adopt}>
              {adoptBusy ? '正在采纳…' : '采纳到草稿'}
            </button>
          </div>
        </div>
      </div>}

      <div className="section-footer">
        <span>{currentStyle ? <><Check size={13} /> 已冻结 V{currentStyle.version}：所有新镜头都按这一版执行</> : '保存后冻结：冻结的就是屏幕上这七项'}</span>
        <div>
          <button className="secondary-button" disabled={busy || !canSave} onClick={onSave}>保存草稿</button>
        </div>
      </div>
      {currentStyle
        ? <div className="section-footer">
          <span>{rulepackId === null && rulepacks.length > 0
            ? '本作品还没绑定规则包：这次冻结会一并完成绑定。'
            : '冻结会生成新版本并写审计；只影响新镜头，旧镜头和历史任务继续用原版本。'}</span>
          <div>
            <button className="primary-button" disabled={busy || !canFreeze} onClick={freezeNow}>
              <LockKeyhole size={14} /> 冻结为新版本
            </button>
          </div>
        </div>
        : <div className="required-action">
          <LockKeyhole size={17} className="required-icon" />
          <div className="required-text">
            <b>必做：冻结这一版风格</b>
            <small>{rulepackId === null && rulepacks.length > 0
              ? '冻结前会先把屏幕上这七项原样保存，再申请一次性授权并冻结这份快照；本作品还没绑定官方规则包，这次冻结会一并完成（一次性）。'
              : '冻结前会先把屏幕上这七项原样保存，再申请一次性授权并冻结这份快照。'}</small>
          </div>
          <button className="must-button" disabled={busy || !canFreeze} onClick={freezeNow}>
            <LockKeyhole size={14} /> 批准并冻结
          </button>
        </div>}
      <p className="freeze-hint">点击冻结会先把屏幕上这七项<strong>原样保存</strong>，再申请一次性授权并冻结这份快照——你批准的内容和最终冻结的内容始终一致；失败时表单内容不会丢失。</p>
      {styles.length > 0 && <div className="version-row">
        <span className="version-label"><History size={12} /> 版本记录</span>
        {styles.map((version) => (
          <span className={`version-chip ${version.id === currentStyle?.id ? 'current' : ''}`} key={version.id}>
            V{version.version} · {version.sha256.slice(0, 8)}
          </span>
        ))}
      </div>}
    </section>
  );
}
