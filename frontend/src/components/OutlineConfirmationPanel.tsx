import { useCallback, useEffect, useRef, useState } from 'react';
import { api, json } from '../api';
import { ScriptTextConsent } from './ScriptTextConsent';
import { OutlineTaskRetry, type OutlineRetry } from './OutlineTaskRetry';

type Section = { production_section_id: string; order: number; title: string };
type Outline = {
  artifact_id: string; sha256: string; approved: boolean; stale: boolean;
  questions_sha256?: string | null;
  payload: {
    summary: string; story_line: string[]; timeline: string[]; relationships: string[];
    locked_facts: string[]; characters: { name: string; entity_id: string }[];
    questions: string[]; production_sections: Section[];
  };
};
type StoryTask = {
  task_id: string; role: string; status: string; outline: Outline | null;
  state_version: number; error_code: string | null;
  retry?: OutlineRetry;
  summary?: { label: string; done: number; total: number; percent: number };
  progress: { stage: string; cursor: number; locked_facts?: string[] };
};

const TASK_STATUS_LABELS: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '进行中', SUCCEEDED: '已完成', NEEDS_REVIEW: '需要你处理',
  FAILED: '失败', PAUSED: '已暂停', CANCELLED: '已取消',
};

/**
 * 第 1 阶段的普通大纲入口。
 *
 * 剧本导入后，下一步就在同一页：提取并归并总体大纲 → 你确认这一版。它调用
 * 的是素材准备页同一个接口与同一份大纲产物，不是第二条链路；把入口放在这里
 * 只是不让用户为了第 1 阶段的事跑去第 4 阶段的素材区。
 */
export function OutlineConfirmationPanel({ projectId, documentId, documentHash, onMessage }: {
  projectId: string;
  documentId: string;
  documentHash: string;
  onMessage?: (message: string) => void;
}) {
  const [tasks, setTasks] = useState<StoryTask[]>([]);
  const [facts, setFacts] = useState('');
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [problem, setProblem] = useState('');
  const [scriptwriterAllowed, setScriptwriterAllowed] = useState(false);

  const load = useCallback(async () => {
    const rows = await api<StoryTask[]>(
      `/projects/${projectId}/story-preparation?document_id=${documentId}`);
    setTasks(rows);
  }, [projectId, documentId]);

  useEffect(() => { void load().catch(() => undefined); }, [load]);
  const running = tasks.some((row) => ['QUEUED', 'RUNNING'].includes(row.status));
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => { void load().catch(() => undefined); }, 3000);
    return () => window.clearInterval(timer);
  }, [load, running]);

  const outline = tasks.find((row) => row.outline)?.outline ?? null;
  const outlineTask = tasks.find((row) => row.role === 'story_outline_run');
  const lockedFacts = facts.split('\n').map((row) => row.trim()).filter(Boolean);
  const blockedSameInput = outlineTask?.status === 'NEEDS_REVIEW'
    && JSON.stringify(lockedFacts) === JSON.stringify(outlineTask.progress.locked_facts ?? []);

  async function act(call: () => Promise<unknown>, success?: string) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true); setProblem('');
    try {
      await call();
      await load();
      if (success) onMessage?.(success);
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
      await load().catch(() => undefined);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return <section className="card section-card outline-confirm" id="outline-confirm">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">第 1 阶段 · 确认大纲</div>
        <h2>先把这版故事定下来</h2>
        <p>系统按原文提取并逐层归并大纲；你确认的这一版就是分集、分镜和导演包的共同依据。
          另一份剧本导入后只是候选，不会自动替换当前依据。</p>
      </div>
      {outline?.approved && <span className="head-chip ok">大纲已确认</span>}
    </div>

    <ScriptTextConsent projectId={projectId} documentId={documentId}
      expanded={!scriptwriterAllowed} onScriptwriterAccess={setScriptwriterAllowed} />
    <label className="material-field"><span>补充锁定事实（每行一条；变更会创建新的大纲输入）</span>
      <textarea value={facts} rows={3} onChange={(event) => setFacts(event.target.value)} />
    </label>
    <button type="button" className="btn-primary" disabled={busy || running || blockedSameInput || !scriptwriterAllowed}
      onClick={() => void act(() => api(`/projects/${projectId}/story-preparation/outlines`, json('POST', {
        document_id: documentId,
        expected_document_hash: documentHash,
        locked_facts: lockedFacts,
      })), '大纲任务已排队；关掉页面也会由常驻 Worker 继续。')}>
      {busy ? '提交中…' : '后台提取并归并总体大纲'}
    </button>
    {outlineTask && <>
      <p className="run-hint">
        大纲任务 {outlineTask.task_id.slice(0, 8)} · {TASK_STATUS_LABELS[outlineTask.status] ?? outlineTask.status}
        · {outlineTask.summary?.label ?? outlineTask.progress.stage}
        （{outlineTask.summary?.done ?? outlineTask.progress.cursor}/{outlineTask.summary?.total ?? '?'}，
        {outlineTask.summary?.percent ?? 0}%）
      </p>
      <progress aria-label="大纲任务进度" max={100} value={outlineTask.summary?.percent ?? 0} />
      {outlineTask.retry && <OutlineTaskRetry retry={outlineTask.retry} busy={busy}
        disabled={running || !scriptwriterAllowed}
        onRetry={(fields) => void act(() => api(
          `/projects/${projectId}/story-preparation/tasks/${outlineTask.task_id}/retry`,
          json('POST', { expected_state_version: outlineTask.state_version, ...fields }),
        ), '大纲任务已重新排队。')} />}
    </>}
    {outlineTask?.error_code && (outlineTask.error_code === 'REMOTE_AGENT_CONSENT_REQUIRED' || !outlineTask.retry?.failure) && <p role="alert">
      {outlineTask.error_code === 'REMOTE_AGENT_CONSENT_REQUIRED'
        ? '大纲等待剧本文字授权。请查看上方模型和发送范围，授权后恢复该任务。'
        : `大纲任务需要处理（${outlineTask.error_code}）。可在素材准备的总体大纲区核对已有结果。`}
    </p>}
    {outlineTask?.status === 'NEEDS_REVIEW' && !outlineTask.retry && outlineTask.error_code === 'REMOTE_AGENT_CONSENT_REQUIRED' && (
      <button type="button" className="btn-primary" disabled={busy || !scriptwriterAllowed}
        onClick={() => void act(() => api(
          `/projects/${projectId}/story-preparation/tasks/${outlineTask.task_id}/resume`,
          json('POST', { expected_state_version: outlineTask.state_version }),
        ), '大纲任务已恢复。')}>
        恢复大纲任务
      </button>
    )}

    {outline && <div className="outline-body">
      <b>大纲版本 {outline.artifact_id.slice(0, 8)} · {outline.approved ? '你已确认' : '待你审阅'}</b>
      <p>{outline.payload.summary}</p>
      <details><summary>主线、人物、关系、时间线与锁定事实</summary>
        <p>{outline.payload.story_line.join(' / ')}</p>
        <p>{outline.payload.characters.map((c) => `${c.name}（${c.entity_id}）`).join('、')}</p>
        <p>{outline.payload.relationships.join(' / ')}</p>
        <p>{outline.payload.timeline.join(' / ')}</p>
        <ul>{outline.payload.locked_facts.map((f, index) => <li key={index}>{f}</li>)}</ul>
      </details>
      {outline.payload.questions.length > 0 && <div role="alert">
        <p>仍有待确认事实：{outline.payload.questions.join('；')}。</p>
        <p>补充锁定事实后重新生成；如果这些疑问是你有意保留的悬念，可以按当前设定确认，确认记录会写下你接受的全部疑问。</p>
      </div>}
      {!outline.approved && <div className="outline-actions">
        <button type="button" className="btn-primary"
          disabled={busy || outline.stale || outline.payload.questions.length > 0}
          onClick={() => void act(() => api(
            `/projects/${projectId}/story-preparation/outlines/${outline.artifact_id}/confirm`,
            json('POST', { expected_sha256: outline.sha256 })), '大纲已确认，可以进入第 2 阶段分集。')}>
          确认这版总体大纲
        </button>
        {outline.payload.questions.length > 0 && <button type="button" className="btn-secondary"
          disabled={busy || outline.stale || !outline.questions_sha256}
          onClick={() => void act(() => api(
            `/projects/${projectId}/story-preparation/outlines/${outline.artifact_id}/confirm`,
            json('POST', {
              expected_sha256: outline.sha256,
              acknowledge_questions_sha256: outline.questions_sha256,
            })), '已按当前设定确认，疑问保留为悬念。')}>
          按当前设定确认（这些疑问保留为悬念）
        </button>}
      </div>}
    </div>}
    {problem && <p role="alert" className="form-error">{problem}</p>}
  </section>;
}
