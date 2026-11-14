import { useCallback, useEffect, useRef, useState } from 'react';
import { api, json, fetchDirectorOutline, reviseDirectorOutline } from '../api';
import type { DirectorOutline, OutlineVersion } from '../api';
import { ScriptTextConsent } from './ScriptTextConsent';
import { OutlineTaskRetry, type OutlineRetry } from './OutlineTaskRetry';
import { useStageRail, useRailPresent } from './StageRail';
import type { RailAction } from './ActionRail';
import { Meter } from './Meter';
import { pacingOptions, type PacingValue } from '../constants';

type Section = { production_section_id: string; order: number; title: string };
type Outline = {
  artifact_id: string; sha256: string; approved: boolean; stale: boolean;
  user_edited?: boolean;
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
type Editor = { base: string; text: string; original: string };

function versionTime(iso: string): string {
  const zoned = /Z$|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  return new Date(zoned).toLocaleString();
}

/**
 * 第 1 阶段的普通大纲入口。
 *
 * 剧本导入后，下一步就在同一页：提取并归并总体大纲 → 你确认这一版。它调用
 * 的是素材准备页同一个接口与同一份大纲产物，不是第二条链路；把入口放在这里
 * 只是不让用户为了第 1 阶段的事跑去第 4 阶段的素材区。
 *
 * 版本管理：当前最新版本（含未确认草稿）以 director/outline 视图为准，
 * 任务列表只负责进度；保存修改产生新草稿，旧版本保留，确认哪版哪版才是依据。
 */
export function OutlineConfirmationPanel({ projectId, documentId, documentHash, onMessage }: {
  projectId: string;
  documentId: string;
  documentHash: string;
  onMessage?: (message: string) => void;
}) {
  const [tasks, setTasks] = useState<StoryTask[]>([]);
  const [view, setView] = useState<DirectorOutline | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [facts, setFacts] = useState('');
  const [pacing, setPacing] = useState<PacingValue>('balanced');
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [problem, setProblem] = useState('');
  const [scriptwriterAllowed, setScriptwriterAllowed] = useState(false);

  const load = useCallback(async () => {
    const rows = await api<StoryTask[]>(
      `/projects/${projectId}/story-preparation?document_id=${documentId}`);
    setTasks(rows);
    try {
      setView(await fetchDirectorOutline(projectId));
    } catch { /* the editable body is an enhancement, not a gate */ }
  }, [projectId, documentId]);

  useEffect(() => { void load().catch(() => undefined); }, [load]);
  const running = tasks.some((row) => ['QUEUED', 'RUNNING'].includes(row.status));
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => { void load().catch(() => undefined); }, 3000);
    return () => window.clearInterval(timer);
  }, [load, running]);

  const taskOutline = tasks.find((row) => row.outline)?.outline ?? null;
  const outlineTask = tasks.find((row) => row.role === 'story_outline_run');
  const current = view?.artifact_id ? (view as DirectorOutline & { artifact_id: string }) : null;
  const display = current ?? taskOutline;
  const versions = view?.versions ?? [];
  const questions = current ? current.questions : (taskOutline?.payload.questions ?? []);
  const pacingLabel = typeof view?.payload?.pacing_label === 'string' ? view.payload.pacing_label : null;
  const editorText = editor ? editor.text : (view?.markdown ?? '');
  const dirty = !!editor && editor.text !== editor.original;
  const baseId = editor?.base ?? current?.artifact_id ?? '';
  const baseSha = editor
    ? (versions.find((v) => v.artifact_id === editor.base)?.sha256 ?? '')
    : (current?.sha256 ?? '');
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
  const extractOutline = () => void act(async () => {
    const res = await api<{ task_id: string; status: string }>(
      `/projects/${projectId}/story-preparation/outlines`, json('POST', {
        document_id: documentId,
        expected_document_hash: documentHash,
        locked_facts: lockedFacts,
        pacing,
      }));
    if (res.status === 'QUEUED' || res.status === 'RUNNING') {
      onMessage?.('大纲任务已排队；关掉页面也会由常驻 Worker 继续。');
    } else if (res.status === 'SUCCEEDED') {
      onMessage?.('剧本、锁定事实与节奏和上次一致：复用已有大纲结果，没有重跑模型。要重新生成请修改锁定事实、切换节奏或导入新剧本。');
    } else {
      onMessage?.(`沿用已有大纲任务（${TASK_STATUS_LABELS[res.status] ?? res.status}），未创建新任务。`);
    }
  });
  const saveRevision = () => void act(async () => {
    const body = await reviseDirectorOutline(projectId, editorText, baseSha);
    setEditor(null);
    setView((prev) => (prev ? {
      ...prev, artifact_id: body.artifact_id, sha256: body.sha256, markdown: body.markdown,
      questions: body.questions, user_edited: true, approved: false, confirmed: false,
    } : prev));
  }, '修改已保存为新一版大纲草稿（无需模型调用）；点「确认这版总体大纲」后生效。');
  const confirmVersion = (target: { artifact_id: string; sha256: string }) => void act(() =>
    api(`/projects/${projectId}/director/outline/confirm`,
      json('POST', { artifact_id: target.artifact_id, expected_sha256: target.sha256 })),
    '大纲已确认，可以进入第 2 阶段分集。');
  const confirmOutline = () => {
    if (current) {
      confirmVersion({ artifact_id: current.artifact_id, sha256: current.sha256 ?? '' });
      return;
    }
    if (taskOutline) {
      void act(() => api(
        `/projects/${projectId}/story-preparation/outlines/${taskOutline.artifact_id}/confirm`,
        json('POST', { expected_sha256: taskOutline.sha256,
          acknowledge_questions_sha256: taskOutline.questions_sha256 })),
      '大纲已确认，可以进入第 2 阶段分集。');
    }
  };
  const loadVersion = (v: OutlineVersion) => void act(async () => {
    const body = await fetchDirectorOutline(projectId, v.artifact_id);
    setEditor({ base: v.artifact_id, text: body.markdown, original: body.markdown });
  }, `已把版本 ${v.artifact_id.slice(0, 8)} 载入编辑器；修改后保存为新版本。`);
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
  } else if (!display) {
    outlinePrimary = { label: '后台提取并归并总体大纲', onClick: extractOutline, loading: busy,
      disabled: busy || running || blockedSameInput || !scriptwriterAllowed,
      disabledReason: !scriptwriterAllowed ? '先在上方授权剧本文字发送'
        : blockedSameInput ? '该输入已有待核对结果，先核对再继续' : undefined };
  } else if (!display.approved) {
    outlinePrimary = { label: '确认这版总体大纲', onClick: confirmOutline, loading: busy,
      disabled: busy || display.stale };
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
  }, [display, outlineTask, busy, running, blockedSameInput, scriptwriterAllowed, lockedFacts, documentId, documentHash, editor, versions]);

  return <section className="card section-card outline-confirm" id="outline-confirm">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">第 1 阶段 · 确认大纲</div>
        <h2>先把这版故事定下来</h2>
        <p>系统按原文提取并逐层归并大纲；你确认的这一版就是分集、分镜和导演包的共同依据。
          另一份剧本导入后只是候选，不会自动替换当前依据。</p>
      </div>
      {display?.approved ? <span className="head-chip ok">大纲已确认</span>
        : display ? <span className="head-chip warn">待确认大纲</span> : null}
    </div>

    <ScriptTextConsent projectId={projectId} documentId={documentId}
      expanded={!scriptwriterAllowed} onScriptwriterAccess={setScriptwriterAllowed} />
    <details className="advanced-fold">
      <summary>锁定事实（高级：每行一条；变更会创建新的大纲输入并重新提取）</summary>
      <label className="material-field"><span>补充锁定事实</span>
        <textarea value={facts} rows={3} onChange={(event) => setFacts(event.target.value)} />
      </label>
    </details>
    <fieldset className="pacing-picker">
      <legend>剧情节奏（决定大纲的叙事密度，切换后重新提取才生效）</legend>
      {pacingOptions.map(([value, label, hint]) => (
        <label key={value} className={`pacing-option${pacing === value ? ' active' : ''}`}>
          <input type="radio" name="outline-pacing" value={value} checked={pacing === value}
            onChange={() => setPacing(value)} />
          <span className="pacing-label">{label}</span>
          <span className="pacing-hint">{hint}</span>
        </label>
      ))}
    </fieldset>
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

    {display && <div className="outline-body">
      <b>大纲版本 {display.artifact_id.slice(0, 8)} · {display.approved ? '你已确认' : '待你审阅'}
        {display.user_edited ? ' · 用户编辑稿' : ''}{pacingLabel ? ` · ${pacingLabel}` : ''}</b>
      {current && current.confirmed_artifact_id && current.confirmed_artifact_id !== current.artifact_id && (
        <p className="run-hint">当前确认依据是版本 {current.confirmed_artifact_id.slice(0, 8)}；
          这一版确认后才会成为分集、分镜和导演包的共同依据。</p>
      )}
      {editor && editor.base !== current?.artifact_id && (
        <p className="run-hint">编辑器内容基于版本 {editor.base.slice(0, 8)}（非最新版本）；保存会以它为基础产生新版本。</p>
      )}
      {editorText && <>
        <p className="run-hint">
          正文可以直接改：主线、人物、场次顺序或保留悬念都行。改完先保存为新一版，
          再点一次「确认这版总体大纲」；确认后的正文就是分集、分镜和导演包的共同依据。
        </p>
        <textarea className="outline-editor" aria-label="大纲正文" value={editorText} rows={14} disabled={busy}
          onChange={(event) => setEditor({
            base: baseId || display.artifact_id,
            text: event.target.value,
            original: editor ? editor.original : (view?.markdown ?? ''),
          })} />
        <div className="outline-actions">
          <button type="button" className="btn-secondary" disabled={busy || !baseSha || !dirty}
            onClick={saveRevision}>保存修改为新版本</button>
        </div>
      </>}
      {questions.length > 0 && <div className="notice" role="note">
        <p>模型疑问（可保留的悬念，不影响确认）：{questions.join('；')}。</p>
      </div>}
      {!display.approved && !railPresent && <div className="outline-actions">
        <button type="button" className="btn-primary"
          disabled={busy || display.stale}
          onClick={confirmOutline}>
          确认这版总体大纲
        </button>
      </div>}
    </div>}
    {versions.length > 0 && <details className="advanced-fold">
      <summary>大纲版本历史（{versions.length}{versions.length >= 30 ? '，最近 30 版' : ''}）</summary>
      <ul className="outline-versions">
        {versions.map((v, index) => ({ v, n: versions.length - index })).reverse().map(({ v, n }) => (
          <li key={v.artifact_id}>
            <span className="ov-id">v{n} · {v.artifact_id.slice(0, 8)}</span>
            <span className="muted">{versionTime(v.created_at)}</span>
            {v.confirmed && <span className="head-chip ok">确认依据</span>}
            {v.artifact_id === current?.artifact_id && <span className="head-chip">当前最新</span>}
            {v.user_edited && <span className="head-chip">用户编辑</span>}
            {v.stale && <span className="head-chip warn">已失效</span>}
            <span className="ov-actions">
              <button type="button" className="btn-secondary" disabled={busy}
                onClick={() => loadVersion(v)}>查看正文</button>
              {!v.approved && !v.stale && <button type="button" className="btn-secondary" disabled={busy}
                onClick={() => confirmVersion(v)}>确认此版</button>}
            </span>
          </li>
        ))}
      </ul>
      <p className="run-hint">保存修改会产生新版本；旧版本保留、不重写。只有你确认的那一版才是共同依据。</p>
    </details>}
    {problem && <p role="alert" className="form-error">{problem}</p>}
  </section>;
}
