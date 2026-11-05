import { useCallback, useEffect, useState } from 'react';
import { api, json } from '../api';

type Section = { production_section_id: string; order: number; title: string; start_offset: number; end_offset: number; estimated_duration_s: number };
type Outline = { artifact_id: string; sha256: string; approved: boolean; stale: boolean; payload: {
  summary: string; story_line: string[]; timeline: string[]; relationships: string[]; locked_facts: string[];
  characters: { name: string; entity_id: string; stable_identity: string }[];
  questions: string[]; production_sections: Section[];
} };
type StoryTask = { task_id: string; role: string; status: string; state_version: number; error_code: string | null;
  outline: Outline | null; progress: { stage: string; cursor: number; results?: { unit: { production_section_id: string }; storyboard: { steps: Record<string, { artifact_id: string }> } }[] } };

export function StoryPreparationPanel({ projectId, documentId, documentHash, styleFrozen, onMaterials }: {
  projectId: string; documentId: string; documentHash: string; styleFrozen: boolean; onMaterials: () => void;
}) {
  const [tasks, setTasks] = useState<StoryTask[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [facts, setFacts] = useState('');
  const [continuity, setContinuity] = useState<Record<string, string>>({});
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    setTasks(await api<StoryTask[]>(`/projects/${projectId}/story-preparation?document_id=${documentId}`));
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
    setBusy(true); setMessage('');
    try { await call(); await load(); }
    catch (e) { setMessage(String(e)); }
    finally { setBusy(false); }
  }
  const selectionBody = { outline_artifact_id: outline?.artifact_id, expected_outline_hash: outline?.sha256,
    production_section_ids: selected, continuity_facts: Object.fromEntries(Object.entries(continuity)
      .map(([key, value]) => [key, value.split('\n').map((v) => v.trim()).filter(Boolean)])) };
  return <details className="material-stage" open>
    <summary>总体大纲与生产段落（长剧本分步制作）</summary>
    <div className="material-stage-body">
      <p>生产段落是章节或场群；H3 生成分段是一次 4–15 秒输出。先提取全文并逐层归并大纲，由你确认后，只为勾选的段落准备素材和正式分镜。</p>
      <label className="material-field"><span>补充锁定事实（每行一条；变更会创建新大纲输入）</span>
        <textarea value={facts} onChange={(e) => setFacts(e.target.value)} rows={3} />
      </label>
      <button type="button" disabled={busy || !documentId} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/outlines`, json('POST', {
        document_id: documentId, expected_document_hash: documentHash, locked_facts: facts.split('\n').map((s) => s.trim()).filter(Boolean),
      })))}>后台提取并归并总体大纲</button>
      {outlineTask && <p>大纲任务 {outlineTask.task_id.slice(0, 8)} · {outlineTask.status} · {outlineTask.progress.stage} · 已处理 {outlineTask.progress.cursor} 个当前层单元</p>}
      <p>关闭页面后由常驻 Worker 继续；刷新只查询进度。未知模型结果只核对，不自动重发。</p>
      {outline && <>
        <b>大纲版本 {outline.artifact_id.slice(0, 8)} · {outline.approved ? '你已确认' : '待你审阅'}</b>
        <p>{outline.payload.summary}</p>
        <details><summary>主线、人物、关系、时间线、锁定事实与来源范围</summary>
          <p>{outline.payload.story_line.join(' / ')}</p>
          <p>{outline.payload.characters.map((c) => `${c.name}（${c.entity_id}）`).join('、')}</p>
          <p>{outline.payload.relationships.join(' / ')}</p><p>{outline.payload.timeline.join(' / ')}</p>
          <ul>{outline.payload.locked_facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
          <p>摘要不替代原文；下面每段固定原文 Unicode 偏移，正式分镜读取对应原文。</p>
        </details>
        {outline.payload.questions.length > 0 && <p role="alert">仍有待确认事实：{outline.payload.questions.join('；')}。补充锁定事实后重新生成大纲。</p>}
        {!outline.approved && <button type="button" disabled={busy || outline.stale || !!outline.payload.questions.length}
          onClick={() => action(() => api(`/projects/${projectId}/story-preparation/outlines/${outline.artifact_id}/confirm`, json('POST', { expected_sha256: outline.sha256 })))}>确认这版总体大纲</button>}
        <div className="media-grid">
          {outline.payload.production_sections.map((section) => <article className="media-card" key={section.production_section_id}>
            <label><input type="checkbox" checked={selected.includes(section.production_section_id)}
              onChange={(e) => setSelected((old) => e.target.checked ? [...old, section.production_section_id] : old.filter((id) => id !== section.production_section_id))} />
              {section.order}. {section.title}</label>
            <small>剧情范围：原文 {section.start_offset}–{section.end_offset}；估计 {section.estimated_duration_s} 秒（字数推算）</small>
            <span>{outline.approved ? '可选择本次工作范围' : '等待大纲确认'}</span>
            {section.order > 1 && <label className="material-field"><span>本段承接事实（由你确认，每行一条）</span>
              <textarea value={continuity[section.production_section_id] ?? ''} rows={2}
                onChange={(e) => setContinuity({ ...continuity, [section.production_section_id]: e.target.value })} />
            </label>}
          </article>)}
        </div>
        <button type="button" disabled={busy || !outline.approved || !selected.length} onClick={() => action(async () => {
          await api(`/projects/${projectId}/story-preparation/materials`, json('POST', selectionBody)); onMaterials();
          setMessage('已读取所选段落的来源事实，生成素材需求草案；请在下方选择已有素材、上传或生成。');
        })}>准备所选段落的素材清单</button>
        <button type="button" disabled={busy || !outline.approved || !selected.length || !styleFrozen}
          onClick={() => action(() => api(`/projects/${projectId}/story-preparation/sections`, json('POST', selectionBody)))}>为所选生产段落生成正式分镜</button>
        {!styleFrozen && <small>正式分镜还需你先确认素材并冻结风格。</small>}
      </>}
      {tasks.map((task) => <div key={task.task_id} className="material-run">
        <b>{task.role === 'story_section_run' ? '分步分镜' : '总体大纲'} · {task.status}</b>
        {task.error_code && <p>需要处理：{task.error_code}。核对原任务后再恢复，未知结果不会自动重发。</p>}
        {['QUEUED', 'RUNNING'].includes(task.status) && <button type="button" disabled={busy} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/tasks/${task.task_id}/pause`, json('POST', { expected_state_version: task.state_version })))}>暂停任务</button>}
        {['PAUSED', 'FAILED', 'NEEDS_REVIEW'].includes(task.status) && <>
          <button type="button" disabled={busy} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/tasks/${task.task_id}/reconcile`, json('POST', { expected_state_version: task.state_version })))}>核对已有结果</button>
          <button type="button" disabled={busy} onClick={() => action(() => api(`/projects/${projectId}/story-preparation/tasks/${task.task_id}/resume`, json('POST', { expected_state_version: task.state_version })))}>恢复未完成工作</button>
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
      </div>)}
      {message && <p role="status">{message}</p>}
    </div>
  </details>;
}
