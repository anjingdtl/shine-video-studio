import { useCallback, useEffect, useRef, useState } from 'react';
import { api, json } from '../api';
import { OutlineTaskRetry, type OutlineRetry } from './OutlineTaskRetry';

type Section = { production_section_id: string; order: number; title: string; start_offset: number; end_offset: number; estimated_duration_s: number };
type Outline = { artifact_id: string; sha256: string; approved: boolean; stale: boolean; questions_sha256?: string | null; payload: {
  summary: string; story_line: string[]; timeline: string[]; relationships: string[]; locked_facts: string[];
  characters: { name: string; entity_id: string; stable_identity: string }[];
  questions: string[]; production_sections: Section[];
} };
type TaskSummary = { stage: string; label: string; done: number; total: number; percent: number;
  lease_expires_at: string | null; awaiting_reclaim: boolean; lease_remaining_s: number | null };
type StoryTask = { task_id: string; role: string; status: string; state_version: number; error_code: string | null;
  outline: Outline | null; summary?: TaskSummary; retry?: OutlineRetry | null;
  progress: { stage: string; cursor: number;
    blocked_subtasks?: { status?: string; reconciliation?: string; scene_key?: string; error_code?: string;
      dead_worker?: boolean; outcome_unknown?: boolean; external_outcome?: string; task_id?: string }[];
    blocked_result?: { rejections?: { role?: string; task_id?: string; code?: string; external_outcome?: string }[] };
    reconciliation?: { checked_at: string; outcomes: { subtask_id: string | null; outcome: string; last_phase?: string | null }[] };
    results?: { unit: { production_section_id: string }; storyboard: { steps: Record<string, { artifact_id: string }> } }[] } };

// A block is only decided when the reconciliation gave it an outcome: a
// subcall that may already have reached the provider keeps waiting for the
// person, and the system never asks again on its own.
const UNDECIDED = new Set(['RUNNING', 'NEEDS_REVIEW', 'FAILED']);
// A block whose reconciliation ended in one of these is still binding: the
// server refuses to resume it, so the button must not pretend otherwise.
const UNRESOLVED = new Set(['UNCONFIRMABLE', 'RETRIES_EXHAUSTED']);
const resumable = (blocked?: { status?: string; reconciliation?: string }[]) =>
  (blocked ?? []).every((item) => !UNDECIDED.has(item?.status ?? '')
    && !UNRESOLVED.has(item?.reconciliation ?? ''));
const RECONCILE_LABELS: Record<string, string> = {
  RESULT_ADOPTED: '已有产物，直接采用',
  NOT_SENT_PROVEN: '可证明从未发出，已注销该次请求',
  RESPONSE_RECEIVED_NO_ANSWER: '服务商已经答复但没有可用结果，可以重新尝试一次',
  UNCONFIRMABLE: '结果未知，系统不会用同一请求重发',
  RETRIES_EXHAUSTED: '已经重试到上限，需要修改输入或取消',
  NO_SUBCALL: '没有可核对的子请求',
  SUBTASK_RECORD_MISSING: '子任务记录已不存在',
};
function reconciliationNote(view?: StoryTask['progress']['reconciliation']) {
  if (!view?.outcomes?.length) return '';
  const lines = view.outcomes.map((item) => RECONCILE_LABELS[item.outcome] ?? item.outcome);
  return `核对结果：${lines.join('；')}。`;
}
const TASK_STATUS_LABELS: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '进行中', SUCCEEDED: '已完成', NEEDS_REVIEW: '需要你处理',
  FAILED: '失败', PAUSED: '已暂停', CANCELLED: '已取消',
};
type ContinuityInfo = { production_section_id: string; order: number; prior_production_section_id: string | null;
  verified_prior_available: boolean; prior_production_status: string;
  own_production: { status: string; missing: { reason?: string }[] } };

export function StoryPreparationPanel({ projectId, documentId, documentHash, styleFrozen, onMaterials }: {
  projectId: string; documentId: string; documentHash: string; styleFrozen: boolean; onMaterials: () => void;
}) {
  const [tasks, setTasks] = useState<StoryTask[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [facts, setFacts] = useState('');
  const [continuity, setContinuity] = useState<Record<string, string>>({});
  const [carryover, setCarryover] = useState<Record<string, ContinuityInfo>>({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  // The four-stage episode contracts already chose the scenes once. When they
  // exist, this panel must not ask for a second section selection: it becomes
  // a read-only summary of the confirmed scope.
  const [lockedScope, setLockedScope] = useState<{ title: string; scenes: string[] }[] | null>(null);
  useEffect(() => {
    let alive = true;
    api<{ volumes?: { scripts?: { section_id: string; title: string }[];
      episode_contracts?: { title: string; sections: string[]; confirmed: boolean }[] }[] }>(
      `/projects/${projectId}/pipeline`)
      .then((overview) => {
        if (!alive) return;
        const episodes: { title: string; scenes: string[] }[] = [];
        for (const volume of overview.volumes ?? []) {
          const titles = new Map((volume.scripts ?? []).map((s) => [s.section_id, s.title]));
          for (const contract of volume.episode_contracts ?? []) {
            if (!contract.confirmed) continue;
            episodes.push({ title: contract.title,
              scenes: contract.sections.map((id) => titles.get(id) ?? id.slice(0, 8)) });
          }
        }
        setLockedScope(episodes.length ? episodes : null);
      })
      .catch(() => { if (alive) setLockedScope(null); });
    return () => { alive = false; };
  }, [projectId]);
  const submitting = useRef(false);
  const load = useCallback(async () => {
    const tasks = await api<StoryTask[]>(`/projects/${projectId}/story-preparation?document_id=${documentId}`);
    setTasks(tasks);
    const approved = tasks.find((t) => t.outline?.approved)?.outline;
    if (!approved) { setCarryover({}); return; }
    try {
      const view = await api<{ sections: ContinuityInfo[] }>(
        `/projects/${projectId}/story-preparation/outlines/${approved.artifact_id}/continuity`);
      setCarryover(Object.fromEntries(view.sections.map((s) => [s.production_section_id, s])));
    } catch { /* carry-over availability is derived live; keep the last known view */ }
  }, [projectId, documentId]);
  useEffect(() => { void load().catch(() => undefined); }, [load]);
  const running = tasks.some((t) => ['QUEUED', 'RUNNING'].includes(t.status));
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => { void load().catch(() => undefined); }, 3000);
    return () => window.clearInterval(timer);
  }, [load, running]);
  const outline = tasks.find((t) => t.outline)?.outline;
  const outlineTask = tasks.find((t) => t.role === 'story_outline_run');
  async function action(call: () => Promise<unknown>) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true); setMessage('');
    try { await call(); await load(); }
    catch (e) { setMessage(String(e)); await load().catch(() => undefined); }
    finally { submitting.current = false; setBusy(false); }
  }
  const selectionBody = { outline_artifact_id: outline?.artifact_id, expected_outline_hash: outline?.sha256,
    production_section_ids: selected, continuity_facts: Object.fromEntries(Object.entries(continuity)
      .map(([key, value]) => [key, value.split('\n').map((v) => v.trim()).filter(Boolean)])) };
  return <details className="material-stage" open={!lockedScope}>
    <summary>{lockedScope ? '总体大纲与分集范围 · 已确认（无需再勾选）' : '总体大纲与生产段落（长剧本分步制作）'}</summary>
    <div className="material-stage-body">
      <p>{lockedScope
        ? '分集时已经确认过每集的场次，这里不再重复勾选：素材「分析剧本」按整份剧本逐场分析，分镜与排产走四阶段流程。'
        : '生产段落是章节或场群；H3 生成分段是一次 4–15 秒输出。先提取全文并逐层归并大纲，由你确认后，只为勾选的段落准备素材和正式分镜。'}</p>
      <button type="button" disabled={busy} onClick={() => void load().catch(() => setMessage('读取大纲状态失败，请重试。'))}>刷新大纲与段落状态</button>
      {!lockedScope && <>
        <label className="material-field"><span>补充锁定事实（每行一条；变更会创建新大纲输入）</span>
          <textarea value={facts} onChange={(e) => setFacts(e.target.value)} rows={3} />
        </label>
        <button type="button" disabled={busy || !documentId} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/outlines`, json('POST', {
          document_id: documentId, expected_document_hash: documentHash, locked_facts: facts.split('\n').map((s) => s.trim()).filter(Boolean),
        })))}>后台提取并归并总体大纲</button>
      </>}
      {outlineTask && <p>大纲任务 {outlineTask.task_id.slice(0, 8)} · {TASK_STATUS_LABELS[outlineTask.status] ?? outlineTask.status} · {outlineTask.summary?.label ?? outlineTask.progress.stage} · 已完成 {outlineTask.summary?.done ?? outlineTask.progress.cursor}/{outlineTask.summary?.total ?? '?'}（{outlineTask.summary?.percent ?? 0}%）</p>}
      <p>关闭页面后由常驻 Worker 继续；刷新只查询进度。未知模型结果只核对，不自动重发。</p>
      {outline && <>
        <b>大纲版本 {outline.artifact_id.slice(0, 8)} · {outline.approved ? '你已确认' : '待你审阅'}</b>
        <p>{outline.payload.summary}</p>
        {lockedScope && <ul className="material-scene-list" aria-label="已确认的分集范围">
          {lockedScope.map((episode) => (
            <li key={episode.title}><span>{episode.title}</span><span>{episode.scenes.join('、')}</span></li>
          ))}
        </ul>}
        <details><summary>主线、人物、关系、时间线、锁定事实与来源范围</summary>
          <p>{outline.payload.story_line.join(' / ')}</p>
          <p>{outline.payload.characters.map((c) => `${c.name}（${c.entity_id}）`).join('、')}</p>
          <p>{outline.payload.relationships.join(' / ')}</p><p>{outline.payload.timeline.join(' / ')}</p>
          <ul>{outline.payload.locked_facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
          <p>摘要不替代原文；下面每段固定原文 Unicode 偏移，正式分镜读取对应原文。</p>
        </details>
        {outline.payload.questions.length > 0 && <div role="alert">
          <p>仍有待确认事实：{outline.payload.questions.join('；')}。</p>
          <p>补充锁定事实后重新生成大纲；如果这些疑问是你有意保留的悬念（悬疑剧本常见），可以按当前设定确认，确认记录会写下你接受的全部疑问。</p>
        </div>}
        {!outline.approved && <button type="button" disabled={busy || outline.stale || !!outline.payload.questions.length}
          onClick={() => action(() => api(`/projects/${projectId}/story-preparation/outlines/${outline.artifact_id}/confirm`, json('POST', { expected_sha256: outline.sha256 })))}>确认这版总体大纲</button>}
        {!outline.approved && !!outline.payload.questions.length && <button type="button" className="btn-primary"
          disabled={busy || outline.stale || !outline.questions_sha256}
          onClick={() => action(() => api(`/projects/${projectId}/story-preparation/outlines/${outline.artifact_id}/confirm`, json('POST', {
            expected_sha256: outline.sha256, acknowledge_questions_sha256: outline.questions_sha256,
          })))}>按当前设定确认（这些疑问保留为悬念）</button>}
        {!lockedScope && <>
        <div className="media-grid">
          {outline.payload.production_sections.map((section) => {
            const carry = carryover[section.production_section_id];
            const verifiedPrior = carry?.verified_prior_available === true;
            return <article className="media-card" key={section.production_section_id}>
              <label><input type="checkbox" checked={selected.includes(section.production_section_id)}
                onChange={(e) => setSelected((old) => e.target.checked ? [...old, section.production_section_id] : old.filter((id) => id !== section.production_section_id))} />
                {section.order}. {section.title}</label>
              <small>剧情范围：原文 {section.start_offset}–{section.end_offset}；估计 {section.estimated_duration_s} 秒（字数推算）</small>
              <span>{outline.approved ? '可选择本次工作范围' : '等待大纲确认'}</span>
              {section.order === 1 && <small>首段，无需承接。</small>}
              {section.order > 1 && (verifiedPrior
                ? <small className="carry-verified">上一段末状态已验证（来自已通过的成片），本段无需填写承接事实。</small>
                : <small>上一段末状态未验证（{carry?.prior_production_status === 'PARTIAL' ? '尚未全部通过' : '尚未制作'}），需要你提供承接事实。</small>)}
              {carry?.own_production.status === 'VERIFIED' && <small>本段成片已全部通过，可作为后段的验证承接。</small>}
              {section.order > 1 && <label className="material-field"><span>{verifiedPrior
                ? '补充承接事实（可选；上一段末状态已验证）'
                : '本段承接事实（由你确认，每行一条）'}</span>
                <textarea value={continuity[section.production_section_id] ?? ''} rows={2}
                  onChange={(e) => setContinuity({ ...continuity, [section.production_section_id]: e.target.value })} />
              </label>}
            </article>;
          })}
        </div>
        <button type="button" disabled={busy || !outline.approved || !selected.length} onClick={() => action(async () => {
          await api(`/projects/${projectId}/story-preparation/materials`, json('POST', selectionBody)); onMaterials();
          setMessage('已读取所选段落的来源事实，生成素材需求草案；请在下方选择已有素材、上传或生成。');
        })}>准备所选段落的素材清单</button>
        <button type="button" disabled={busy || !outline.approved || !selected.length || !styleFrozen}
          onClick={() => action(() => api(`/projects/${projectId}/story-preparation/sections`, json('POST', selectionBody)))}>为所选生产段落生成正式分镜</button>
        {!styleFrozen && <small>正式分镜还需你先确认素材并冻结风格。</small>}
        </>}
      </>}
      {tasks.map((task) => {
        const rejection = task.progress.blocked_result?.rejections?.find((item) => item.task_id
          && ['director', 'editor'].includes(item.role ?? '')
            && ['AGENT_REQUEST_REPLAY_BLOCKED', 'AGENT_REQUEST_RECONCILIATION_REQUIRED'].includes(item.code ?? ''));
        const canExplicitlyResend = (item: NonNullable<StoryTask['progress']['blocked_subtasks']>[number]) =>
          task.status === 'NEEDS_REVIEW' && item.task_id && UNDECIDED.has(item.status ?? '')
          && (item.external_outcome === 'UNKNOWN' || item.outcome_unknown)
          && (!item.reconciliation || item.reconciliation === 'UNCONFIRMABLE');
        return <div key={task.task_id} className="material-run">
        <b>{task.role === 'story_section_run' ? '分步分镜' : '总体大纲'} · {TASK_STATUS_LABELS[task.status] ?? task.status}</b>
        {task.summary && (task.summary.total > 0 || ['QUEUED', 'RUNNING'].includes(task.status)) && <div className="run-progress">
          <div className="run-progress-track"><span style={{ width: `${task.summary.percent}%` }} /></div>
          <small>{task.summary.label} · 已完成 {task.summary.done}/{task.summary.total || '?'}（{task.summary.percent}%）</small>
          {task.summary.awaiting_reclaim && <small>上一次推进被中断；Worker 已接管或即将接管并继续，不会重复提交模型请求。</small>}
          {!task.summary.awaiting_reclaim && task.summary.lease_remaining_s !== null && task.summary.lease_remaining_s > 0
            && <small>Worker 正在推进（本次接管有效期还剩约 {Math.ceil(task.summary.lease_remaining_s / 60)} 分钟）</small>}
        </div>}
        {task.retry && <OutlineTaskRetry retry={task.retry} busy={busy}
          onRetry={(fields) => void action(() => api(
            `/projects/${projectId}/story-preparation/tasks/${task.task_id}/retry`,
            json('POST', { expected_state_version: task.state_version, ...fields }),
          ))} />}
        {task.error_code && <p>需要处理：{task.error_code}。先点「核对已有结果」：服务商已答复但没有可用结果的子任务会被释放，可以重新尝试；结果未知的不会自动重发。</p>}
        {!!task.progress.blocked_subtasks?.length && <small>阻塞的子任务：{task.progress.blocked_subtasks.map((item) => [item.scene_key, item.error_code].filter(Boolean).join(' ')).join('；')}</small>}
        {task.progress.blocked_subtasks?.some((item) => item.dead_worker) && (
          <small>上一次推进被中断（Worker 已退出）：可证明从未发出的请求会释放并重问；结果未知的不会自动重发，请核对原任务。</small>
        )}
        {task.progress.blocked_subtasks?.map((item) => (
          !task.retry?.can_retry && !task.retry?.exhausted && canExplicitlyResend(item) ? (
            <button key={item.task_id} type="button" className="btn-text" disabled={busy}
              onClick={() => {
                const which = item.scene_key ? '「' + item.scene_key + '」' : '这一场';
                if (!window.confirm(which + '的旧请求可能仍有答案，可能多花一次模型调用。确认以一次新的请求重问？')) return;
                void action(() => api('/projects/' + projectId + '/story-preparation/tasks/' + task.task_id + '/resend-unknown',
                  json('POST', { expected_state_version: task.state_version, subtask_id: item.task_id,
                    evidence: '用户确认对结果未知的子任务发起一次新请求' })));
              }}>明确重发这一场</button>
          ) : null
        ))}
        {reconciliationNote(task.progress.reconciliation) && <p className="run-reconcile">{reconciliationNote(task.progress.reconciliation)}</p>}
        {['QUEUED', 'RUNNING'].includes(task.status) && <button type="button" disabled={busy} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/tasks/${task.task_id}/pause`, json('POST', { expected_state_version: task.state_version })))}>暂停任务</button>}
        {['PAUSED', 'FAILED', 'NEEDS_REVIEW', 'CANCELLED'].includes(task.status) && <>
          <button type="button" disabled={busy} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/tasks/${task.task_id}/reconcile`, json('POST', { expected_state_version: task.state_version })))}>核对已有结果</button>
          {['PAUSED', 'FAILED', 'NEEDS_REVIEW'].includes(task.status) && <button type="button" disabled={busy || !!rejection || !resumable(task.progress.blocked_subtasks)} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/tasks/${task.task_id}/resume`, json('POST', { expected_state_version: task.state_version })))}>恢复未完成工作</button>}
          {rejection && <>
            <small>原 {rejection.role} 调用仍为 UNKNOWN。只能核对原任务；在没有未发送证明前不会创建新请求。</small>
          </>}
          {!resumable(task.progress.blocked_subtasks) && !rejection && <small>有子任务的模型结果未知，系统不会自动重发。{
            task.retry?.can_retry || (!task.retry?.exhausted && task.progress.blocked_subtasks?.some(canExplicitlyResend))
              ? '核对后仍无结果时，可以明确确认发起一次新请求。'
              : '若核对后仍无法继续，请修改剧本后重新导入并重新提取，或取消本任务。'
          }</small>}
          {task.status === 'CANCELLED' && <small>本任务已取消：仍可核对原任务，释放可证明从未发出的请求，然后重新提取。</small>}
        </>}
        {!['SUCCEEDED', 'CANCELLED'].includes(task.status) && <button type="button" disabled={busy} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/tasks/${task.task_id}/cancel`, json('POST', { expected_state_version: task.state_version })))}>取消后续工作</button>}
        {task.progress.results?.map((result, i) => <div key={i}>
          <p>生产段落 {result.unit.production_section_id.slice(0, 8)} · 工作单元 {i + 1} 已提出分镜，尚未生产。</p>
          <button type="button" disabled={busy} onClick={() => action(() => api(`/projects/${projectId}/film-artifacts/${result.storyboard.steps.cinematographer.artifact_id}/apply-camera`, json('POST', {
            continuity_artifact_id: result.storyboard.steps.continuity_supervisor.artifact_id,
            arbitration_artifact_id: result.storyboard.steps.director_arbitration?.artifact_id,
            name_prefix: `段落-${result.unit.production_section_id.slice(0, 8)}-${i + 1}`,
          })))}>审阅后应用本单元分镜</button>
        </div>)}
      </div>;
      })}
      {message && <p role="status">{message}</p>}
    </div>
  </details>;
}
