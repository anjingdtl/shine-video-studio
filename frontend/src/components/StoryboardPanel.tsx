import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, FileText, Loader2, Sparkles } from 'lucide-react';
import { API, api, json } from '../api';
import { failureLabels, label } from '../constants';

type DocumentRow = {
  id: string; filename: string; sha256: string; source_format: string;
  text_preview: string; text_length: number; created_at: string;
};
type Step = { artifact_id: string; kind: string; sha256: string };
type StoryboardResult = {
  status: 'PROPOSED' | 'NEEDS_REVIEW' | 'BLOCKED_MISSING_MASTERS' | 'REJECTED';
  script_source: string;
  script_sha256?: string;
  steps: Record<string, Step>;
  missing_characters?: string[];
  rejections?: Array<{ role: string; code: string }>;
  continuity_issues?: string[];
  continuity_arbitration?: {
    artifact_id: string;
    rulings: Array<{ issue: string; verdict: string; rule_id: string; rationale: string }>;
    recommendation: string;
    released: boolean;
  } | null;
  next?: { apply_camera: { camera_artifact_id: string; continuity_artifact_id: string; edit_intent_artifact_id: string; arbitration_artifact_id?: string | null } };
  action?: string;
};

const stepLabels: Record<string, string> = {
  scriptwriter: '编剧 Agent · 场次与节拍',
  director: '导演 Agent · 镜头拆分',
  cinematographer: '摄影 Agent · 摄影计划',
  continuity_supervisor: '场记 Agent · 连续性审查',
  editor_edit_intent: '剪辑 Agent · 冻结剪辑意图',
};

export default function StoryboardPanel({ projectId, onRefresh }: { projectId: string; onRefresh: () => void }) {
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [selectedDoc, setSelectedDoc] = useState('');
  const [inlineText, setInlineText] = useState('');
  const [consent, setConsent] = useState(false);
  const [facts, setFacts] = useState('');
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<StoryboardResult | null>(null);
  const [message, setMessage] = useState('');

  const reloadDocuments = useCallback(async () => {
    try {
      const rows = await api<DocumentRow[]>(`/projects/${projectId}/documents`);
      setDocuments(rows);
      setSelectedDoc(current => current && rows.some(doc => doc.id === current)
        ? current
        : rows.length === 1 ? rows[0].id : '');
    } catch { setDocuments([]); }
  }, [projectId]);

  useEffect(() => { reloadDocuments(); }, [reloadDocuments]);

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true); setMessage('');
    try {
      const form = new FormData();
      form.append('file', file);
      const row = await fetch(`${API}/projects/${projectId}/documents`, { method: 'POST', body: form })
        .then(r => { if (!r.ok) throw new Error(`上传失败 ${r.status}`); return r.json(); });
      await reloadDocuments();
      setSelectedDoc(row.id);
      setMessage(`剧本已导入：${row.filename}（${row.text_length} 字符）`);
    } catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }

  async function generate() {
    if (!consent) { setMessage('需要先勾选同意把剧本文本发给已配置的 LLM 服务商。'); return; }
    if (!selectedDoc && !inlineText.trim()) {
      setMessage(documents.length
        ? '请先从“已导入剧本”中选择一份，或导入/粘贴剧本。'
        : '请导入剧本文件或粘贴完整剧本文本。');
      return;
    }
    setBusy(true); setResult(null); setMessage('');
    try {
      const body: Record<string, unknown> = {
        allow_remote_text: consent,
        locked_story_facts: facts.split('\n').map(s => s.trim()).filter(Boolean),
      };
      if (selectedDoc) body.document_id = selectedDoc; else body.text = inlineText;
      const row = await api<StoryboardResult>(`/projects/${projectId}/storyboard/generate`, json('POST', body));
      setResult(row);
      if (row.status === 'PROPOSED') {
        setMessage('五个 Agent 已完成真实调用，分镜方案已生成。请人工复核后应用。');
        onRefresh();
      } else if (row.status === 'NEEDS_REVIEW') {
        setMessage('分镜已生成，但场记 Agent 报告了连续性问题，需人工复核。');
      } else if (row.status === 'BLOCKED_MISSING_MASTERS') {
        setMessage(`缺少已批准并锁定的角色主资产：${(row.missing_characters ?? []).join('、')}。请上传并人工批准后再重新生成分镜。`);
      } else {
        // A refused chain states its reason in the panel status, never in a
        // toast that disappears before it is read.
        setMessage(`分镜链被拒绝：${(row.rejections ?? []).map(r => `${stepLabels[r.role] ?? r.role}：${label(failureLabels, r.code)}`).join('；') || '未知原因'}。未创建任何镜头，请复核后重试。`);
      }
    } catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }

  async function apply() {
    if (!result?.next) return;
    setApplying(true);
    try {
      const camera = result.next.apply_camera;
      const row = await api<{ shot_ids: string[] }>(
        `/projects/${projectId}/film-artifacts/${camera.camera_artifact_id}/apply-camera`,
        json('POST', {
          continuity_artifact_id: camera.continuity_artifact_id,
          ...(camera.arbitration_artifact_id ? { arbitration_artifact_id: camera.arbitration_artifact_id } : {}),
        }),
      );
      setMessage(`分镜已应用：创建 ${row.shot_ids.length} 个镜头。可在生产序列面板排入生产。`);
      onRefresh();
    } catch (e) { setMessage(String(e)); }
    finally { setApplying(false); }
  }

  return <section className="card section-card" id="storyboard">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">03 / STORYBOARD</div>
        <h2>AI 自动分镜（完整剧本驱动）</h2>
        <p>导入完整剧本后，编剧、导演、摄影、场记、剪辑五个 Agent 按已冻结的风格与规则包顺序真实调用，产出结构化分镜方案；资产缺失会明确阻断，不会自动创建资产。</p>
      </div>
      <div className="section-icon plum"><Sparkles size={17} /></div>
    </div>

    <div className="storyboard-import">
      <label className="import-label">导入剧本
        <input type="file" accept=".txt,.md,.json,.docx,.pdf" onChange={upload} disabled={busy} />
      </label>
      <label htmlFor="storyboard-doc">或选择已导入剧本
        <select id="storyboard-doc" value={selectedDoc} onChange={e => { setSelectedDoc(e.target.value); if (e.target.value) setInlineText(''); }}>
          <option value="">{documents.length ? '请选择剧本' : '—'}</option>
          {documents.map(doc => <option key={doc.id} value={doc.id}>{doc.filename}（{doc.text_length} 字符）</option>)}
        </select>
      </label>
    </div>
    {!selectedDoc && (
      <label>粘贴完整剧本文本<textarea rows={6} value={inlineText} onChange={e => setInlineText(e.target.value)} /></label>
    )}
    <label className="storyboard-facts">锁定剧情事实（每行一条，可选）
      <textarea id="storyboard-facts" rows={3} value={facts} onChange={e => setFacts(e.target.value)}
        placeholder={'例如：信封始终未拆封\n账册第十七页缺失'} />
    </label>
    <label className="consent-row">
      <input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />
      <span>我同意把该剧本的文本内容发送给已配置的 LLM 服务商用于分镜（媒体文件不会上传）。</span>
    </label>
    <button className="primary-button" disabled={busy} onClick={generate}>
      {busy ? <Loader2 size={14} /> : <Sparkles size={14} />} 生成 AI 分镜方案
    </button>
    {message && <p role="status">{message}</p>}

    {result && <div className="storyboard-result">
      {result.status === 'REJECTED' && <div className="blocked-callout">
        <AlertTriangle size={15} />
        <span>分镜链被拒绝：{(result.rejections ?? []).map(r => `${stepLabels[r.role] ?? r.role}：${label(failureLabels, r.code)}`).join('；') || '未知原因'}</span>
      </div>}
      {result.status === 'BLOCKED_MISSING_MASTERS' && <div className="blocked-callout">
        <AlertTriangle size={15} />
        <span>缺少已批准角色主资产：{(result.missing_characters ?? []).join('、')}。请上传并人工批准后再重新生成分镜。</span>
      </div>}
      {(result.status === 'PROPOSED' || result.status === 'NEEDS_REVIEW') && <>
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
