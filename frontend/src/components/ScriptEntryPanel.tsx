import { useCallback, useEffect, useRef, useState } from 'react';
import { FileUp, Lightbulb, RefreshCw } from 'lucide-react';
import {
  ApiError, confirmCreativeBrief, generateScriptFromBrief,
  listCreativeBriefs, listScriptDocuments, saveCreativeBrief, uploadScriptDocument,
  type CreativeBrief,
} from '../api';
import { OutlineConfirmationPanel } from './OutlineConfirmationPanel';
import { useStageRail } from './StageRail';

const TASK_STATUS_LABELS: Record<string, string> = {
  QUEUED: '排队中（常驻 Worker 会接着处理）',
  RUNNING: '正在撰写',
  SUCCEEDED: '剧本已生成',
  COMPLETED: '剧本已生成',
  FAILED: '生成失败',
  NEEDS_REVIEW: '结果待核对（不会自动重发）',
};

/**
 * ① 创意与剧本 的双入口。
 *
 * 入口 A（想法）：写下一个想法 → 可编辑的创意简报（每次保存都是新版本）→ 确认
 * → 排队生成剧本。生成的剧本是普通的项目文档，和导入的剧本走完全相同的
 * 大纲/确认/分镜链路；不存在第二条剧本范式。
 * 入口 B（导入）：上传现成剧本原文（原文与哈希先入库，规范化/歧义确认
 * 由已有的大纲链路完成）。
 */
export function ScriptEntryPanel({ projectId, onMessage, onDocumentReady }: {
  projectId: string;
  onMessage?: (message: string) => void;
  onDocumentReady?: () => void;
}) {
  const [briefs, setBriefs] = useState<CreativeBrief[]>([]);
  const [documents, setDocuments] = useState<Array<{ id: string; filename: string; sha256: string; source_format: string }>>([]);
  // The outline step lives on this page: a script is not "imported and done",
  // it becomes usable when its outline is confirmed.  The selected document is
  // an explicit choice — importing another file only adds a candidate.
  const [selectedDocumentId, setSelectedDocumentId] = useState('');
  const [idea, setIdea] = useState('');
  const [extra, setExtra] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  // Scripts that already existed when the panel first loaded are not news.
  const announced = useRef<Set<string> | null>(null);
  const finishedDocuments = (rows: CreativeBrief[]) => rows
    .map((row) => (row.script_task?.status === 'SUCCEEDED' ? row.script_task.document_id : null))
    .filter((id): id is string => Boolean(id));

  const load = useCallback(async () => {
    const requested = projectId;
    const [briefRows, docRows] = await Promise.all([
      listCreativeBriefs(requested),
      listScriptDocuments(requested),
    ]);
    if (activeProject.current !== requested) return;
    if (announced.current === null) announced.current = new Set(finishedDocuments(briefRows));
    setBriefs(briefRows);
    // A confirmed volume plan materialises one document per volume, newest
    // first.  This panel's outline is written from the WHOLE script, so a
    // volume must never look like the basis: it used to become the default
    // selection and then the list sliced the real master away, leaving the
    // page claiming a random chapter was 当前依据.
    const ordered = [...docRows].sort(
      (a, b) => Number(a.source_format === 'volume') - Number(b.source_format === 'volume'));
    const master = ordered.find((row) => row.source_format !== 'volume');
    setDocuments(ordered);
    setSelectedDocumentId((current) => {
      const picked = docRows.find((row) => row.id === current);
      if (picked && picked.source_format !== 'volume') return current;
      return master?.id ?? current ?? docRows[0]?.id ?? '';
    });
  }, [projectId]);

  useEffect(() => { void load().catch(() => undefined); }, [load]);
  const generating = briefs.some((row) => row.script_task && ['QUEUED', 'RUNNING'].includes(row.script_task.status));
  useEffect(() => {
    if (!generating) return;
    const timer = window.setInterval(() => { void load().catch(() => undefined); }, 3000);
    return () => window.clearInterval(timer);
  }, [load, generating]);
  // A script the Worker just wrote is a new document: tell the material panel
  // once, so the next stage sees it without a manual reload.
  useEffect(() => {
    if (announced.current === null) return;
    const fresh = finishedDocuments(briefs).filter((id) => !announced.current!.has(id));
    fresh.forEach((id) => announced.current!.add(id));
    if (fresh.length) onDocumentReady?.();
  }, [briefs, onDocumentReady]);

  async function run(action: () => Promise<void>) {
    setBusy(true); setProblem(null);
    try { await action(); await load(); }
    catch (e) { setProblem(e instanceof ApiError ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  const latest = briefs[0] ?? null;
  const lockedFacts = extra.split('\n').map((row) => row.trim()).filter(Boolean);

  // 无剧本时引导用户先导入或撰写；有剧本后主操作交给大纲确认（priority 1）。
  useStageRail('script-entry', {
    priority: 3,
    primary: documents.length === 0 ? {
      label: '导入或撰写剧本',
      onClick: () => document.querySelector<HTMLTextAreaElement>('#script-entry textarea')?.focus(),
    } : null,
  }, [documents.length]);

  return <section className="card script-entry" id="script-entry">
    <div className="module-guide">
      <Lightbulb size={13} />
      <div>
        <p><b>两条入口，一条链路：</b>写下想法让 AI 写剧本，或直接导入现成剧本。两边最终都进入同一份
          「原文 + 大纲确认」流程，原文与锁定事实全程保留。</p>
      </div>
    </div>
    <div className="script-entry-grid">
      <div className="script-entry-a">
        <h3>入口 A · 我有一个想法</h3>
        <label className="script-entry-field">
          <span>想法（一句话或一段描述）</span>
          <textarea value={idea} rows={5} placeholder="例：雨夜里，一个钟表匠修好了一只停在十二点的怀表，客人却在第二天消失。"
            onChange={(event) => setIdea(event.target.value)} />
        </label>
        <label className="script-entry-field">
          <span>锁定事实（每行一条，AI 不得改动）</span>
          <textarea className="compact" value={extra} rows={3} placeholder="例：主角叫老周"
            onChange={(event) => setExtra(event.target.value)} />
        </label>
        <button type="button" className="btn-secondary" disabled={busy || !idea.trim()}
          onClick={() => run(async () => {
            await saveCreativeBrief(projectId, { idea: idea.trim(), locked_facts: lockedFacts });
            onMessage?.('已保存创意简报新版本；确认后即可生成剧本。');
          })}>保存为创意简报</button>

        {latest && <div className="brief-latest">
          <b>最新简报 · 第 {latest.version} 版 {latest.confirmed ? '（已确认）' : '（待确认）'}</b>
          <p className="brief-idea">{latest.payload.idea}</p>
          {latest.payload.locked_facts?.length ? <p>锁定：{latest.payload.locked_facts.join('；')}</p> : null}
          <div className="brief-actions">
            {!latest.confirmed && <button type="button" className="btn-secondary" disabled={busy}
              onClick={() => run(async () => {
                await confirmCreativeBrief(projectId, latest.id, latest.sha256);
                onMessage?.('创意简报已确认。');
              })}>确认这一版</button>}
            {latest.confirmed && <button type="button" className="btn-secondary" disabled={busy || generating}
              onClick={() => run(async () => {
                try {
                  await generateScriptFromBrief(projectId, latest.id, false);
                  onMessage?.('已排队生成剧本；关闭页面也不影响，常驻 Worker 会接着写。');
                } catch (e) {
                  if (e instanceof ApiError && e.status === 409 &&
                      String(e.message).includes('REMOTE_AGENT_CONSENT_REQUIRED')) {
                    const ok = window.confirm(
                      '当前「编剧」使用远程文本模型。发送的是简报文字（不含任何图像/音频字节）。同意发送吗？');
                    if (!ok) throw e;
                    await generateScriptFromBrief(projectId, latest.id, true);
                    onMessage?.('已同意并发送简报文字，排队生成剧本。');
                    return;
                  }
                  throw e;
                }
              })}>{generating ? '生成中…' : '生成剧本'}</button>}
          </div>
          {latest.script_task && <p className="brief-task">
            剧本任务：{TASK_STATUS_LABELS[latest.script_task.status] ?? latest.script_task.status}
            {latest.script_task.error_code ? ` · ${latest.script_task.error_code}` : ''}
          </p>}
          {latest.script_task?.next_step && <p className="brief-task">
            下一步：{latest.script_task.next_step}
          </p>}
          {latest.script_task?.status === 'NEEDS_REVIEW' && <p className="must-note">
            这次调用的结果不确定，只核对、不自动重发。请到专业设置查看任务日志后再决定。
          </p>}
        </div>}
      </div>

      <div className="script-entry-b">
        <h3>入口 B · 我已有剧本</h3>
        <p>上传剧本原文（.txt / .md / .json / .docx / .pdf）。原文与内容哈希先完整入库；
          场次拆分、人物/对白整理与歧义确认在「总体大纲」里完成，改写锁定内容前会先显示差异。</p>
        <label className="btn-secondary upload-button">
          <FileUp size={14} />
          <span>{busy ? '上传中…' : '选择并上传剧本'}</span>
          <input type="file" accept=".txt,.md,.json,.docx,.pdf" hidden disabled={busy}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (!file) return;
              void run(async () => {
                await uploadScriptDocument(projectId, file);
                onMessage?.(`剧本「${file.name}」已导入。`);
                onDocumentReady?.();
              });
            }} />
        </label>
        {documents.length > 0 && <ul className="script-doc-list">
          {documents.slice(0, 6).map((row) => <li key={row.id}>
            <button type="button"
              className={selectedDocumentId === row.id ? 'script-doc active' : 'script-doc'}
              aria-pressed={selectedDocumentId === row.id}
              onClick={() => setSelectedDocumentId(row.id)}>
              <b>{row.filename}</b>
              <span>{row.source_format === 'idea' ? '（由创意简报生成）' : row.source_format === 'volume' ? '（分卷）' : ''} {row.sha256.slice(0, 8)}</span>
              {selectedDocumentId === row.id && <em>当前依据</em>}
            </button>
          </li>)}
        </ul>}
        <button type="button" className="btn-secondary" disabled={busy}
          onClick={() => { void load().catch(() => undefined); }}>
          <RefreshCw size={12} /> 刷新
        </button>
      </div>
    </div>
    {selectedDocumentId && <OutlineConfirmationPanel
      key={`outline-${projectId}-${selectedDocumentId}`}
      projectId={projectId}
      documentId={selectedDocumentId}
      documentHash={documents.find((row) => row.id === selectedDocumentId)?.sha256 ?? ''}
      onMessage={onMessage}
    />}
    {problem && <p className="form-error">{problem}</p>}
  </section>;
}
