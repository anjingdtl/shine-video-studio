import { useCallback, useEffect, useRef, useState } from 'react';
import { api, json, reviseDirectorOutline } from '../api';
import { ScriptTextConsent } from './ScriptTextConsent';
import { OutlineTaskRetry, type OutlineRetry } from './OutlineTaskRetry';
import { useStageRail, useRailPresent } from './StageRail';
import type { RailAction } from './ActionRail';
import { Meter } from './Meter';

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
  // ① 收束后的可编辑正文：直接改主线、人物、场次顺序或保留悬念，
  // 一次确认即定稿；疑问是提示，不再是必答项。
  const [markdown, setMarkdown] = useState('');
  const [outlineSha, setOutlineSha] = useState('');
  const [outlineArtifactId, setOutlineArtifactId] = useState('');

  const load = useCallback(async () => {
    const rows = await api<StoryTask[]>(
      `/projects/${projectId}/story-preparation?document_id=${documentId}`);
    setTasks(rows);
    try {
      const body = await api<{ artifact_id: string | null; sha256?: string; markdown: string }>(
        `/projects/${projectId}/director/outline`);
      if (body.artifact_id) {
        setOutlineArtifactId(body.artifact_id);
        setOutlineSha(body.sha256 ?? '');
        setMarkdown((current) => current || body.markdown);
      }
    } catch { /* the editable body is an enhancement, not a gate */ }
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

  const railPresent = useRailPresent();
  const extractOutline = () => void act(() => api(`/projects/${projectId}/story-preparation/outlines`, json('POST', {
    document_id: documentId,
    expected_document_hash: documentHash,
    locked_facts: lockedFacts,
  })), '大纲任务已排队；关掉页面也会由常驻 Worker 继续。');
  const saveRevision = () => void act(async () => {
    const body = await reviseDirectorOutline(projectId, markdown, outlineSha);
    setOutlineArtifactId(body.artifact_id);
    setOutlineSha(body.sha256);
  }, '修改已保存为新一版大纲草稿；确认后生效。');
  const confirmOutline = () => void act(() => (
    outlineArtifactId && outlineSha
      ? api(`/projects/${projectId}/director/outline/confirm`,
          json('POST', { artifact_id: outlineArtifactId, expected_sha256: outlineSha }))
      : api(`/projects/${projectId}/story-preparation/outlines/${outline?.artifact_id}/confirm`,
          json('POST', { expected_sha256: outline?.sha256 }))),
    '大纲已确认，可以进入第 2 阶段分集。');
  const resumeOutline = () => void act(() => api(
    `/projects/${projectId}/story-preparation/tasks/${outlineTask?.task_id}/resume`,
    json('POST', { expected_state_version: outlineTask?.state_version })), '大纲任务已恢复。');

  // ① 创意与剧本的顺序主操作：提取大纲 → （直接编辑正文）→ 确认大纲。
  //    疑问是提示不是必答项：确认动作会把当前疑问列表作为「保留的悬念」记入审计。
  const needsConsentResume = outlineTask?.status === 'NEEDS_REVIEW' && !outlineTask.retry
    && outlineTask.error_code === 'REMOTE_AGENT_CONSENT_REQUIRED';
  let outlinePrimary: RailAction | null = null;
  if (needsConsentResume) {
    outlinePrimary = { label: '恢复大纲任务', onClick: resumeOutline, loading: busy, disabled: busy || !scriptwriterAllowed,
      disabledReason: scriptwriterAllowed ? undefined : '先在上方授权剧本文字发送' };
  } else if (!outline) {
    outlinePrimary = { label: '后台提取并归并总体大纲', onClick: extractOutline, loading: busy,
      disabled: busy || running || blockedSameInput || !scriptwriterAllowed,
      disabledReason: !scriptwriterAllowed ? '先在上方授权剧本文字发送'
        : blockedSameInput ? '该输入已有待核对结果，先核对再继续' : undefined };
  } else if (!outline.approved) {
    outlinePrimary = { label: '确认这版总体大纲', onClick: confirmOutline, loading: busy,
      disabled: busy || outline.stale };
  } else {
    // 已确认后仍要留修订路径：补充锁定事实 → 重新提取。否则「补充锁定事实后
    // 重新生成」的提示没有可点的入口。
    outlinePrimary = { label: '补充锁定事实后重新提取大纲', onClick: extractOutline, loading: busy,
      disabled: busy || running || blockedSameInput || !scriptwriterAllowed,
      disabledReason: !scriptwriterAllowed ? '先在上方授权剧本文字发送'
        : blockedSameInput ? '该输入已有待核对结果，先核对再继续' : undefined };
  }
  useStageRail('outline-confirm', {
    priority: 1,
    primary: outlinePrimary,
    running: running ? { label: '大纲提取中', done: outlineTask?.summary?.done, total: outlineTask?.summary?.total } : null,
  }, [outline, outlineTask, busy, running, blockedSameInput, scriptwriterAllowed, lockedFacts, documentId, documentHash]);

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
    <details className="advanced-fold">
      <summary>锁定事实（高级：每行一条；变更会创建新的大纲输入并重新提取）</summary>
      <label className="material-field"><span>补充锁定事实</span>
        <textarea value={facts} rows={3} onChange={(event) => setFacts(event.target.value)} />
      </label>
    </details>
    {!railPresent && <button type="button" className="btn-primary" disabled={busy || running || blockedSameInput || !scriptwriterAllowed}
      onClick={extractOutline}>
      {busy ? '提交中…' : '后台提取并归并总体大纲'}
    </button>}
    {outlineTask && <>
      <p className="run-hint">
        大纲任务 {outlineTask.task_id.slice(0, 8)} · {TASK_STATUS_LABELS[outlineTask.status] ?? outlineTask.status}
        · {outlineTask.summary?.label ?? outlineTask.progress.stage}
        （{outlineTask.summary?.done ?? outlineTask.progress.cursor}/{outlineTask.summary?.total ?? '?'}，
        {outlineTask.summary?.percent ?? 0}%）
      </p>
      <Meter label="大纲任务进度" value={outlineTask.summary?.percent ?? 0} count={`${outlineTask.summary?.percent ?? 0}%`} tone="busy" />
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
    {!railPresent && outlineTask?.status === 'NEEDS_REVIEW' && !outlineTask.retry && outlineTask.error_code === 'REMOTE_AGENT_CONSENT_REQUIRED' && (
      <button type="button" className="btn-primary" disabled={busy || !scriptwriterAllowed}
        onClick={resumeOutline}>
        恢复大纲任务
      </button>
    )}

    {outline && <div className="outline-body">
      <b>大纲版本 {outline.artifact_id.slice(0, 8)} · {outline.approved ? '你已确认' : '待你审阅'}</b>
      {markdown && <>
        <p className="run-hint">
          正文可以直接改：主线、人物、场次顺序或保留悬念都行。改完先保存为新一版，
          再点一次「确认这版总体大纲」；确认后的正文就是分集、分镜和导演包的共同依据。
        </p>
        <textarea className="outline-editor" value={markdown} rows={14} disabled={busy}
          onChange={(event) => setMarkdown(event.target.value)} />
        <div className="outline-actions">
          <button type="button" className="btn-secondary" disabled={busy || !outlineSha}
            onClick={saveRevision}>保存修改为新版本</button>
        </div>
      </>}
      {outline.payload.questions.length > 0 && <div className="notice" role="note">
        <p>模型疑问（可保留的悬念，不影响确认）：{outline.payload.questions.join('；')}。</p>
      </div>}
      {!outline.approved && <div className="outline-actions">
        {!railPresent && <button type="button" className="btn-primary"
          disabled={busy || outline.stale}
          onClick={confirmOutline}>
          确认这版总体大纲
        </button>}
      </div>}
    </div>}
    {problem && <p role="alert" className="form-error">{problem}</p>}
  </section>;
}
