import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, FileText, HelpCircle, Loader2, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react';
import { API, ApiError, api, json } from '../api';
import { DIALOGUE_LANGUAGES, failureLabels, label } from '../constants';
import { AgentProgress, type AgentRunProgress } from './AgentProgress';

type DocumentRow = {
  id: string; filename: string; sha256: string; source_format: string;
  text_preview: string; text_length: number; created_at: string;
};
type Step = { artifact_id: string; kind: string; sha256: string };
type StoryboardResult = {
  status: 'PROPOSED' | 'NEEDS_REVIEW' | 'BLOCKED_MISSING_MASTERS' | 'AWAITING_CHARACTER_CONFIRMATION' | 'REJECTED';
  script_source: string;
  script_sha256?: string;
  steps: Record<string, Step>;
  missing_characters?: string[];
  rejections?: Array<{ role: string; code: string; task_id?: string; external_outcome?: string }>;
  continuity_issues?: string[];
  prompt_story_facts?: string[];
  prompt_style?: Record<string, string> | null;
  continuity_arbitration?: {
    artifact_id: string;
    rulings: Array<{ verdict: string; rule_id: string; issue: string; rationale: string }>;
    recommendation: string;
    released: boolean;
  } | null;
  next?: { apply_camera: { camera_artifact_id: string; continuity_artifact_id: string; edit_intent_artifact_id: string; arbitration_artifact_id?: string | null } };
  action?: string;
  failed_role?: string;
  resume_steps?: Record<string, string>;
};
type BindingCandidate = {
  asset_id: string; filename: string; entity_id: string | null; tags: string[];
  score: number; reason: string; approved: boolean; locked: boolean;
};
type BindingRow = {
  script_name: string; needs_real_name: boolean;
  candidates: BindingCandidate[]; suggested_asset_id: string | null;
  suggested_name: string; confidence: string;
};
type BindingProposal = {
  proposal_id: string; breakdown_artifact_id: string;
  characters: BindingRow[];
  masters: Array<{ id: string; filename: string; entity_id: string | null; tags: string[]; approved: boolean; locked: boolean }>;
};

const stepLabels: Record<string, string> = {
  scriptwriter: '编剧 Agent · 场次与节拍',
  director: '导演 Agent · 镜头拆分',
  cinematographer: '摄影 Agent · 摄影计划',
  continuity_supervisor: '场记 Agent · 连续性审查',
  editor_edit_intent: '剪辑 Agent · 冻结剪辑意图',
  editor: '剪辑 Agent · 冻结剪辑意图',
};

/** The crew order the workbench shows and the server runs. */
const CHAIN_STEPS = ['scriptwriter', 'director', 'cinematographer', 'continuity_supervisor', 'editor_edit_intent'];

/** The languages the H3 model is asked to speak. The value is the language name
 *  that goes into the prompt, so it stays English. */
export { DIALOGUE_LANGUAGES };

export default function StoryboardPanel({ projectId, onRefresh, onNavigate }: {
  projectId: string;
  onRefresh: () => void;
  onNavigate?: (page: string) => void;
}) {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [selectedDoc, setSelectedDoc] = useState('');
  const [inlineText, setInlineText] = useState('');
  const [consent, setConsent] = useState(false);
  const [facts, setFacts] = useState('');
  const [busy, setBusy] = useState(false);
  const [requiresReconciliation, setRequiresReconciliation] = useState(false);
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<StoryboardResult | null>(null);
  const [message, setMessage] = useState('');
  const [progressRuns, setProgressRuns] = useState<AgentRunProgress[]>([]);
  const [language, setLanguage] = useState('Chinese');
  const [languageState, setLanguageState] = useState<'saved' | 'saving' | 'failed'>('saved');
  const [binding, setBinding] = useState<BindingProposal | null>(null);
  const [bindingChoice, setBindingChoice] = useState<Record<string, { asset_id: string; name: string }>>({});
  const [bindingBusy, setBindingBusy] = useState(false);
  const [rulepackId, setRulepackId] = useState<string | null | undefined>(undefined);
  const [pollError, setPollError] = useState('');

  const saveLanguage = useCallback(async (value: string) => {
    setLanguage(value);
    setLanguageState('saving');
    try {
      await api(`/projects/${projectId}/settings`, json('PATCH', { language: value }));
      setLanguageState('saved');
      setMessage(`对白语言已设为 ${value}：之后的镜头会按这个语言说出台词。`);
    } catch (error) {
      setLanguageState('failed');
      setMessage(String(error));
    }
  }, [projectId]);

  const reloadDocuments = useCallback(async () => {
    try {
      const rows = await api<DocumentRow[]>(`/projects/${projectId}/documents`);
      setDocuments(rows);
      setSelectedDoc(current => current && rows.some(doc => doc.id === current)
        ? current
        : rows.length === 1 ? rows[0].id : '');
    } catch { setDocuments([]); }
  }, [projectId]);

  const reloadLanguage = useCallback(async () => {
    try {
      const project = await api<{ language: string }>(`/projects/${projectId}`);
      const stored = project.language || 'Chinese';
      // The project may still carry a language this build does not know; the
      // control then shows the default instead of an empty selection.
      const known = DIALOGUE_LANGUAGES.some(item => item.id === stored);
      setLanguage(known ? stored : 'Chinese');
      setLanguageState('saved');
    } catch { /* the control keeps its last known value */ }
  }, [projectId]);

  useEffect(() => { reloadDocuments(); reloadLanguage(); }, [reloadDocuments, reloadLanguage]);

  // The crew cannot run before the project is bound to the official rule pack.
  // Say so here, with the way out, instead of letting the chain refuse later.
  useEffect(() => {
    let alive = true;
    api<{ rulepack_id: string | null }>(`/projects/${projectId}/rulepack`)
      .then(row => { if (alive) setRulepackId(row.rulepack_id); })
      .catch(() => { if (alive) setRulepackId(undefined); });
    return () => { alive = false; };
  }, [projectId]);

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const row = await fetch(`${API}/projects/${projectId}/documents`, { method: 'POST', body: form })
        .then(r => { if (!r.ok) throw new Error(`上传失败 ${r.status}`); return r.json(); });
      await reloadDocuments();
      setSelectedDoc(row.id);
      setRequiresReconciliation(false); setResult(null);
      setMessage(`剧本已导入：${row.filename}（${row.text_length} 字符）`);
    } catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }

  /** Run the crew chain, optionally continuing from one role. */
  async function runChain(options?: { start_role?: string; resume_steps?: Record<string, string> }) {
    if (!consent) { setMessage('需要先勾选同意把剧本文本发给已配置的 LLM 服务商。'); return; }
    if (!selectedDoc && !inlineText.trim()) {
      setMessage('请先从“已导入剧本”中选择一份，或导入/粘贴剧本。');
      return;
    }
    setBusy(true); setResult(null); setProgressRuns([]); setPollError('');
    setMessage('正在按顺序调用真实 LLM 的五个影视 Agent。模型可能需要几分钟，请稍候。');
    const priorRuns = await api<AgentRunProgress[]>(`/projects/${projectId}/film-runs`, { signal: AbortSignal.timeout(5000) }).catch(() => []);
    const priorRunIds = new Set(priorRuns.map(run => run.id));
    let polling = true;
    let pollInFlight = false;
    const pollProgress = async () => {
      if (!polling || pollInFlight) return;
      pollInFlight = true;
      try {
        const runs = await api<AgentRunProgress[]>(`/projects/${projectId}/film-runs`, { signal: AbortSignal.timeout(5000) });
        if (!polling) return;
        setPollError('');
        const currentRuns = runs.filter(run => !priorRunIds.has(run.id));
        setProgressRuns(currentRuns);
        const completed = new Set(currentRuns.filter(run => run.status === 'SUCCEEDED'
          && run.trace?.tool !== 'continuity_arbitration').map(run => run.role_id)).size;
        const active = currentRuns.find(run => run.status === 'RUNNING');
        if (active) {
          if (active.trace?.tool === 'continuity_arbitration') {
            setMessage('五个工种已完成，导演正在裁定连续性问题。');
            return;
          }
          const currentIndex = ['scriptwriter', 'director', 'cinematographer', 'continuity_supervisor', 'editor']
            .indexOf(active.role_id);
          const currentLabel = stepLabels[active.role_id] ?? active.role_id;
          setMessage(`正在调用第 ${Math.max(1, currentIndex + 1)}/5 步：${currentLabel}。已完成 ${completed}/5 步，模型可能需要几分钟。`);
        } else if (completed > 0) {
          setMessage(`已完成 ${Math.min(5, completed)}/5 步真实模型调用，正在准备下一位 Agent。`);
        }
      } catch { setPollError('暂时无法读取运行进度，生成请求仍在等待返回。请检查本机服务；不要重复提交。'); }
      finally { pollInFlight = false; }
    };
    const progressTimer = window.setInterval(() => { void pollProgress(); }, 2000);
    try {
      const body: Record<string, unknown> = {
        allow_remote_text: consent,
        locked_story_facts: facts.split('\n').map(s => s.trim()).filter(Boolean),
        confirm_characters: !options?.start_role,
        ...(options?.start_role ? { start_role: options.start_role } : {}),
        ...(options?.resume_steps ? { resume_steps: options.resume_steps } : {}),
      };
      if (selectedDoc) body.document_id = selectedDoc; else body.text = inlineText;
      const row = await api<StoryboardResult>(`/projects/${projectId}/storyboard/generate`, json('POST', body));
      setResult(row);
      const uncertain = (row.rejections ?? []).some(r => r.external_outcome !== 'NOT_SENT' && [
        'LLM_TIMEOUT', 'LLM_CONNECTION', 'LLM_SERVER',
        'AGENT_REQUEST_RECONCILIATION_REQUIRED', 'AGENT_REQUEST_REPLAY_BLOCKED',
      ].includes(r.code));
      setRequiresReconciliation(uncertain);
      if (row.status === 'PROPOSED') {
        setMessage('五个 Agent 已完成真实调用，分镜方案已生成。请人工复核后应用。');
      } else if (row.status === 'NEEDS_REVIEW') {
        setMessage('分镜已生成，但场记 Agent 报告了连续性问题，需人工复核。');
      } else if (row.status === 'AWAITING_CHARACTER_CONFIRMATION') {
        await openBinding('编剧已完成。请二次确认人物、建议名字与素材，确认后才会调用导演。', row.steps.scriptwriter?.artifact_id);
      } else if (row.status === 'BLOCKED_MISSING_MASTERS') {
        const reason = `这些人物没有匹配到唯一的已确认人物素材：${(row.missing_characters ?? []).join('、')}。`;
        setMessage(`${reason}请检查素材用途和名称；系统不会创建或替换人物素材。`);
        await openBinding(reason, row.steps.scriptwriter?.artifact_id);
      } else {
        // A refused chain states its reason in the panel status, never in a
        // toast that disappears before it is read.
        const rejections = row.rejections ?? [];
        const reason = rejections.map(r => `${stepLabels[r.role] ?? r.role}：${label(failureLabels, r.code)}`)
          .join('；') || '未知原因';
        const definitelyNotSent = rejections.some(r => r.external_outcome === 'NOT_SENT');
        const taskId = rejections.find(r => r.task_id)?.task_id;
        setMessage(uncertain
          ? `分镜链暂停：${reason}。远端结果未确认，系统已保留请求且不会自动重发；本次未创建镜头。${taskId ? `任务编号：${taskId}。` : ''}请先在「专业模式 → 影视制片团队 → Agent 运行记录」核对该请求。`
          : definitelyNotSent
            ? `分镜链未能连接到模型服务：${reason}。系统确认请求未送达，没有产生远端结果；检查网络恢复后可以重新生成。${taskId ? `任务编号：${taskId}。` : ''}`
            : `分镜链被拒绝：${reason}。未创建任何镜头，请先处理提示中的问题。`);
      }
    } catch (e) {
      const uncertain = !(e instanceof ApiError) || e.status >= 500;
      setRequiresReconciliation(uncertain);
      setMessage(uncertain
        ? `生成请求未能返回：${String(e)}。后台可能仍在运行，请先核对 Agent 运行记录，不要重复提交。`
        : `分镜请求被拒绝：${String(e)}。请处理提示后继续。`);
    }
    finally {
      polling = false; window.clearInterval(progressTimer);
      try {
        const runs = await api<AgentRunProgress[]>(`/projects/${projectId}/film-runs`, { signal: AbortSignal.timeout(5000) });
        setProgressRuns(runs.filter(run => !priorRunIds.has(run.id)));
      } catch { /* Keep the last observed progress. */ }
      setBusy(false);
    }
  }

  const generate = () => runChain();

  /** Ask the server which uploaded master answers each script character. */
  async function openBinding(reason?: string, breakdownId?: string) {
    setBindingBusy(true);
    try {
      const proposal = await api<BindingProposal>(`/projects/${projectId}/storyboard/character-binding/proposal`,
        json('POST', breakdownId ? { breakdown_artifact_id: breakdownId } : {}));
      setBinding(proposal);
      setResult(current => current ?? {
        status: 'AWAITING_CHARACTER_CONFIRMATION', script_source: 'saved-breakdown',
        steps: { scriptwriter: { artifact_id: proposal.breakdown_artifact_id, kind: 'ScriptBreakdown', sha256: '' } },
      });
      const choices: Record<string, { asset_id: string; name: string }> = {};
      for (const row of proposal.characters) {
        if (row.suggested_asset_id) {
          choices[row.script_name] = { asset_id: row.suggested_asset_id, name: row.suggested_name };
        }
      }
      setBindingChoice(choices);
      setMessage(`${reason ?? ''}已打开人物对照：逐条核对「谁是哪张图」，必要时改名，再点「确认并写入名字」。`);
    } catch (e) {
      setMessage(`${reason ?? ''}人物对照暂时打不开：${String(e)}`);
    } finally { setBindingBusy(false); }
  }

  /** Write the confirmed names on the masters and freeze the ones that are not. */
  async function applyBinding() {
    if (!binding) return;
    const bindings = Object.entries(bindingChoice)
      .filter(([, value]) => value.asset_id && value.name.trim())
      .map(([script_name, value]) => ({ script_name, asset_id: value.asset_id, name: value.name.trim() }));
    if (bindings.length !== binding.characters.length) { setMessage('请为每一位人物选择素材并填写名字，再一起确认。'); return; }
    if (new Set(bindings.map(row => row.asset_id)).size !== bindings.length) {
      setMessage('同一张人物素材不能同时对应不同人物，请逐条核对。'); return;
    }
    setBindingBusy(true);
    try {
      const applied = await api<{ artifact_id: string; assets: Array<{ id: string; entity_id: string; locked: boolean }> }>(
        `/projects/${projectId}/storyboard/character-binding/apply`,
        json('POST', { breakdown_artifact_id: binding.breakdown_artifact_id, bindings }));
      // The name is part of a master's identity, so the master is frozen after
      // it is named.  An already frozen master keeps its version.
      for (const asset of applied.assets) {
        if (asset.locked) continue;
        for (const action of ['approve', 'lock']) {
          const grant = await api<{ grant_id: string; snapshot: string }>('/preparation/approval-grants',
            json('POST', { action: `asset_${action}`, target_id: asset.id }));
          await api(`/assets/${asset.id}/${action}`, json('POST', {
            grant_id: grant.grant_id, snapshot: grant.snapshot, offline: false,
            evidence: '用户在分镜人物对照中确认名图对应，并确认素材内容及使用权。',
          }));
        }
      }
      const resumeSteps = { scriptwriter: applied.artifact_id };
      setBinding(null);
      onRefresh();
      setMessage('名字已写入素材，素材已冻结。现在从导演这一步继续，不必重跑编剧。');
      await runChain({ start_role: 'director', resume_steps: resumeSteps });
    } catch (e) { setMessage(String(e)); }
    finally { setBindingBusy(false); }
  }

  /** Retry only the role that failed; the earlier roles keep their artifacts. */
  async function resumeFrom(role: string, steps: Record<string, string>) {
    setBinding(null);
    await runChain({ start_role: role, resume_steps: steps });
  }

  async function apply() {
    if (!result?.next) return;
    setApplying(true);
    try {
      const camera = result.next.apply_camera;
      const row = await api<{ shot_ids: string[] }>(`/projects/${projectId}/storyboard/apply`, json('POST', {
        camera_artifact_id: camera.camera_artifact_id,
        continuity_artifact_id: camera.continuity_artifact_id,
        edit_intent_artifact_id: camera.edit_intent_artifact_id,
        ...(camera.arbitration_artifact_id ? { arbitration_artifact_id: camera.arbitration_artifact_id } : {}),
      }),
      );
      setMessage(`分镜已应用：创建 ${row.shot_ids.length} 个镜头。可在生产序列面板排入生产。`);
      onRefresh();
    } catch (e) { setMessage(String(e)); }
    finally { setApplying(false); }
  }

  const failedRole = result?.failed_role;
  const resumeSteps = result?.resume_steps ?? {};

  return <section className="card section-card" id="storyboard">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">03 / STORYBOARD</div>
        <h2>AI 自动分镜（完整剧本驱动）</h2>
        <p>导入完整剧本后，编剧、导演、摄影、场记、剪辑五个 Agent 按已冻结的风格与规则包顺序真实调用，产出结构化分镜方案；资产缺失会明确阻断，不会自动创建资产。</p>
      </div>
      <div className="section-icon plum"><Sparkles size={17} /></div>
    </div>

    <div className="module-guide">
      <HelpCircle size={13} />
      <div>
        <p><b>这一步要做什么：</b>导入完整剧本 → 勾选同意发送文本 → 点下面的橙色按钮。五个 Agent 会依次真实调用模型。</p>
        <p><b>会卡在哪里：</b>任何一步失败都只重跑那一步，不用从头再来；剧本人物对不上素材时，先在人物对照里确认「谁是哪张图」。</p>
      </div>
    </div>

    {rulepackId === null && <div className="required-action">
      <ShieldCheck size={17} className="required-icon" />
      <div className="required-text">
        <b>还差一步：本项目还没绑定官方规则包</b>
        <small>五个 Agent 都要按同一套官方规范工作，所以没绑定就会在第一步被拒绝。先去「素材与风格」点一次「启用官方规则包」。</small>
      </div>
      {onNavigate && <button className="must-button" onClick={() => onNavigate('assets')}>去启用官方规则包</button>}
    </div>}

    <div className="storyboard-import">
      <label className="import-label">导入剧本
        <input type="file" accept=".txt,.md,.json,.docx,.pdf" onChange={upload} disabled={busy} />
      </label>
      <label htmlFor="storyboard-doc">或选择已导入剧本
        <select id="storyboard-doc" value={selectedDoc} disabled={busy} onChange={e => {
          setSelectedDoc(e.target.value); setRequiresReconciliation(false); setResult(null);
          if (e.target.value) setInlineText('');
        }}>
          <option value="">{documents.length ? '请选择剧本' : '—'}</option>
          {documents.map(doc => <option key={doc.id} value={doc.id}>{doc.filename}（{doc.text_length} 字符）</option>)}
        </select>
      </label>
      <label htmlFor="storyboard-language">对白语言
        <select id="storyboard-language" value={language} disabled={busy} onChange={e => saveLanguage(e.target.value)}>
          {DIALOGUE_LANGUAGES.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
    </div>
    <p className="storyboard-language-note">
      对白语言会写进 H3 提示词：{languageState === 'saved' ? '已保存' : languageState === 'saving' ? '保存中' : '保存失败'}。
      之后的镜头会要求模型用这个语言说出台词；没有台词的镜头会明确要求不要出现任何人声。
      已经生成的镜头不会被改写。
    </p>
    {!selectedDoc && (
      <label>粘贴完整剧本文本<textarea rows={6} value={inlineText} onChange={e => {
        setInlineText(e.target.value); setRequiresReconciliation(false); setResult(null);
      }} /></label>
    )}
      <label className="storyboard-facts">锁定剧情事实（每行一条，可选）
        <textarea id="storyboard-facts" rows={3} value={facts} disabled={busy} onChange={e => {
        setFacts(e.target.value); setRequiresReconciliation(false); setResult(null);
      }}
        placeholder={'例如：信封始终未拆封\n账册第十七页缺失'} />
    </label>
    <label className="consent-row">
      <input type="checkbox" checked={consent} disabled={busy} onChange={e => setConsent(e.target.checked)} />
      <span>我同意把该剧本的文本内容发送给已配置的 LLM 服务商用于分镜（媒体文件不会上传）。</span>
    </label>
    <div className="required-action">
      <Sparkles size={17} className="required-icon" />
      <div className="required-text">
        <b>必做：生成 AI 分镜方案</b>
        <small>需要先导入剧本并勾选上面的同意。五个 Agent 会依次真实调用模型，每步之间留有间隔。</small>
      </div>
      <button className="must-button" disabled={busy || bindingBusy || !!binding || requiresReconciliation} onClick={generate}>
        {busy ? <Loader2 size={14} /> : <Sparkles size={14} />}
        {requiresReconciliation ? '先核实前次请求' : '生成 AI 分镜方案'}
      </button>
    </div>
    {message && <p role="status">{message}</p>}
    {!busy && !binding && <button type="button" disabled={bindingBusy}
      onClick={() => openBinding('读取本项目最近一次编剧结果，请核对它属于当前选择的剧本。')}>
      读取已完成的编剧人物对照（不重跑模型）
    </button>}
    {pollError && <p role="alert">{pollError}</p>}
    {busy && <p>切换到「素材与风格」不会中断本次调用；返回后可继续查看进度。模型未提供内部百分比。</p>}
    {(busy || progressRuns.length > 0) && <AgentProgress runs={progressRuns} />}

    {result && <div className="storyboard-result">
      {result.status === 'REJECTED' && <div className="blocked-callout">
        <AlertTriangle size={15} />
        <span>分镜链被拒绝：{(result.rejections ?? []).map(r => `${stepLabels[r.role] ?? r.role}：${label(failureLabels, r.code)}`).join('；') || '未知原因'}</span>
      </div>}
      {result.status === 'BLOCKED_MISSING_MASTERS' && <div className="blocked-callout">
        <AlertTriangle size={15} />
        <span>这些人物没有匹配到唯一的已确认人物素材：{(result.missing_characters ?? []).join('、')}。请在下面的「人物对照」里确认谁是哪张图，确认后会自动改名并冻结。</span>
      </div>}
      {result.status === 'REJECTED' && failedRole && Object.keys(resumeSteps).length > 0 && <div className="step-retry">
        <RefreshCw size={14} />
        <span>这一步已经完成的部分不会重跑。</span>
        <button className="must-button" disabled={busy || requiresReconciliation}
          onClick={() => resumeFrom(failedRole, resumeSteps)}>
          从「{stepLabels[failedRole] ?? failedRole}」这一步重试
        </button>
      </div>}
      {binding && <div className="binding-panel">
        <div className="section-kicker">人物对照（剧本人物 ↔ 你的素材）</div>
        <p className="binding-intro">系统按名字、标签和上传顺序给出了建议，只作参考。以缩略图为准逐条核对；名字会写进素材本身，五个 Agent 就靠它把剧本人物和你的图对上。</p>
        <div className="binding-list">
          {binding.characters.map((row) => {
            const choice = bindingChoice[row.script_name];
            return <div className={`binding-row ${choice?.asset_id ? 'accepted' : ''}`} key={row.script_name}>
              <div>
                <span className="binding-label">剧本人物</span>
                <span className="binding-script">{row.script_name}</span>
                {row.needs_real_name && <small className="binding-label">这是编号，需要一个真名</small>}
              </div>
              <div>
                <span className="binding-label">用这张图</span>
                <select value={choice?.asset_id ?? ''} onChange={e => {
                  const asset = binding.masters.find(m => m.id === e.target.value);
                  setBindingChoice(current => ({
                    ...current,
                    [row.script_name]: {
                      asset_id: e.target.value,
                      name: current[row.script_name]?.name || asset?.tags?.find(t => t && t !== '主角') || row.suggested_name || row.script_name,
                    },
                  }));
                }}>
                  <option value="">— 请选择 —</option>
                  {binding.masters.map(master => (
                    <option key={master.id} value={master.id}>
                      {master.filename}{master.entity_id ? `（${master.entity_id}）` : ''}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <span className="binding-label">素材上写这个名字</span>
                <input value={choice?.name ?? ''} placeholder={row.suggested_name || row.script_name}
                  onChange={e => setBindingChoice(current => ({
                    ...current, [row.script_name]: { ...current[row.script_name], name: e.target.value },
                  }))} />
              </div>
              <div className="binding-candidates">
                {row.candidates.slice(0, 4).map(candidate => (
                  <button type="button" key={candidate.asset_id}
                    className={`binding-candidate ${choice?.asset_id === candidate.asset_id ? 'selected' : ''}`}
                    onClick={() => setBindingChoice(current => ({
                      ...current,
                      [row.script_name]: {
                        asset_id: candidate.asset_id,
                        name: current[row.script_name]?.name || row.suggested_name || row.script_name,
                      },
                    }))}>
                    <img className="asset-thumb small" src={`${API}/assets/${candidate.asset_id}/media`} alt={candidate.filename} />
                    <small>{candidate.reason}</small>
                  </button>
                ))}
              </div>
            </div>;
          })}
        </div>
        <div className="required-action">
          <CheckCircle2 size={17} className="required-icon" />
          <div className="required-text">
            <b>必做：确认名图对应</b>
            <small>确认后会把名字写进素材、冻结素材，并从导演这一步继续生成分镜。</small>
          </div>
          <button className="must-button" disabled={busy || bindingBusy} onClick={applyBinding}>
            {bindingBusy ? <Loader2 size={14} /> : <CheckCircle2 size={14} />} 确认并写入名字
          </button>
        </div>
      </div>}
      {(result.status === 'PROPOSED' || result.status === 'NEEDS_REVIEW') && <>
        <div className="agent-step-list">
          {CHAIN_STEPS.map(name => {
            const step = result.steps[name];
            const row = progressRuns.find(run => run.role_id === (name === 'editor_edit_intent' ? 'editor' : name));
            const state = step ? 'done' : row?.status === 'RUNNING' ? 'running' : 'failed';
            return <div className={`agent-step-row ${state}`} key={name}>
              {step ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
              <b>{stepLabels[name] ?? name}</b>
              <span>{step ? '已完成' : row?.status === 'RUNNING' ? '进行中' : '未完成'}</span>
              {step && <span className="step-when">{step.sha256.slice(0, 12)}…</span>}
            </div>;
          })}
        </div>
        {!!result.prompt_story_facts?.length && <div className="blocked-callout">
          <span>请核对锁定事实的英文提示词表述：{result.prompt_story_facts.join('；')}</span>
        </div>}
        {result.prompt_style && <details><summary>核对冻结风格的英文提示词表述</summary>
          {Object.entries(result.prompt_style).map(([key, value]) => <p key={key}>{key}：{value}</p>)}
        </details>}
        {(result.continuity_issues ?? []).length > 0 && <div className="blocked-callout">
          <AlertTriangle size={15} />
          <span>场记报告连续性问题：{(result.continuity_issues ?? []).join('；')}</span>
        </div>}
        {result.continuity_arbitration && <div className="storyboard-arbitration">
          <div className="section-kicker">导演仲裁（按已锁定规则包）</div>
          {(result.continuity_arbitration.rulings ?? []).map((ruling, index) => (
            <p key={index}>
              <strong>{ruling.verdict === 'not_a_conflict' ? '不构成冲突' : '构成冲突'}</strong>
              {' · '}{ruling.rule_id} · {ruling.issue}
              <small>{ruling.rationale}</small>
            </p>
          ))}
        </div>}
        <ol className="storyboard-steps">
          {Object.entries(result.steps).map(([name, step]) => (
            <li key={name}>
              <FileText size={12} />
              <span>{stepLabels[name] ?? name}</span>
              <small>{step.kind} · {step.sha256.slice(0, 12)}…</small>
            </li>
          ))}
        </ol>
        {result.next && <button className="primary-button" disabled={applying} onClick={apply}>
          {applying ? <Loader2 size={14} /> : <Sparkles size={14} />} 应用为正式分镜（人工确认）
        </button>}
      </>}
    </div>}
  </section>;
}
