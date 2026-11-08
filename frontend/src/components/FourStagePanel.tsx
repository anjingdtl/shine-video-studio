import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleHelp, ListChecks } from 'lucide-react';
import {
  confirmBible, confirmSceneScript, confirmVolumeOutline, confirmVolumePlan, editSceneScript,
  generateStoryboardDraft, getPipelineOverview, getTextConsentScope, grantTextConsent,
  importPipelineSource, listScriptDocuments, queueBible, queueVolumeOutline, queueVolumeScripts,
  requestVolumeHandoff, resumePipelineTask,
  type PipelineOverview, type PipelineTaskSummary, type WorkflowProfileLite,
} from '../api';

type Props = {
  projectId: string;
  workflowProfiles?: WorkflowProfileLite[];
  onMessage: (message: string, kind?: 'error' | 'success') => void;
};

const ROLE_LABELS: Record<string, string> = {
  story_outline_run: '提取大纲',
  four_stage_bible_run: '生成全局故事蓝图',
  four_stage_scene_script_run: '编写场次剧本',
};

/** The four-stage creative pipeline in one place: import, outline, script, storyboard. */
export function FourStagePanel({ projectId, workflowProfiles, onMessage }: Props) {
  const [overview, setOverview] = useState<PipelineOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [volumePlanDraft, setVolumePlanDraft] = useState<{ id: string; sha256: string; volumes: unknown[] } | null>(null);
  const [documents, setDocuments] = useState<Array<{ id: string; filename: string; sha256: string }>>([]);
  const [editTarget, setEditTarget] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [handoffProfile, setHandoffProfile] = useState('');

  const refresh = useCallback(async () => {
    try {
      const [data, docs] = await Promise.all([
        getPipelineOverview(projectId),
        listScriptDocuments(projectId),
      ]);
      setOverview(data);
      setDocuments(docs ?? []);
    } catch {
      // A passive refresh that cannot reach the server stays silent: the
      // panel shows its loading state, and only user actions report errors.
      setOverview(null);
    }
  }, [projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  // 有活跃任务时每 4 秒轮询真实进度；空闲时停止，避免无谓请求。
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    const active = (overview?.active_tasks ?? []).some(
      (t) => ['QUEUED', 'RUNNING'].includes(t.status));
    if (active && pollRef.current === null) {
      pollRef.current = setInterval(() => { refresh(); }, 4000);
    } else if (!active && pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current !== null) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [overview, refresh]);

  async function authorizeAndContinue(task: PipelineTaskSummary) {
    if (!task.consent_document_id || !task.consent_document_sha256) return;
    setBusy(true);
    try {
      const scope = await getTextConsentScope(projectId, task.consent_document_id);
      await grantTextConsent(projectId, task.consent_document_id,
                             task.consent_document_sha256, scope.scope_hash, true);
      await resumePipelineTask(projectId, task.task_id, task.state_version);
      onMessage('已授权本机文本发送，任务已重新排队', 'success');
      await refresh();
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally { setBusy(false); }
  }

  async function run(action: () => Promise<unknown>, after?: (result: unknown) => void) {
    setBusy(true);
    try {
      const result = await action();
      await refresh();
      after?.(result);
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  }

  if (!overview) {
    return <section className="card" id="four-stage"><div className="module-guide"><CircleHelp size={13} />
      <p>正在读取四阶段进度…</p></div></section>;
  }

  const volumes = overview.volumes ?? [];
  const pendingScripts = overview.pending_scripts ?? [];
  const activeTasks = overview.active_tasks ?? [];
  return (
    <section className="card" id="four-stage">
      <header className="stage-head"><ListChecks size={17} /><h3>AI 编导四阶段</h3>
        <span className="muted">导入 → 大纲 → 剧本 → 分镜</span></header>
      <div className="module-guide"><CircleHelp size={13} /><div>
        <p><b>四步做完一部作品：</b>①选择母本并确认分卷 → ②逐卷提取大纲并确认全局故事蓝图 →
          ③逐场生成并确认剧本 → ④生成分镜草案。正式拍摄仍走原有素材与风格门禁。</p>
        <p>每一步的「确认」都需要你亲自点击；任务详情、Token 与 JSON 收纳在专业模式。</p>
      </div></div>

      {activeTasks.length > 0 && (
        <div className="pipeline-progress" role="group" aria-label="后台任务进度">
          {activeTasks.map((task) => {
            const pct = task.progress_total > 0
              ? Math.round((task.progress_done / task.progress_total) * 100)
              : null;
            const consentBlocked = task.error_code === 'REMOTE_AGENT_CONSENT_REQUIRED'
              && !!task.consent_document_id;
            return (
              <div className="progress-row" key={task.task_id}>
                <span className="progress-name">{ROLE_LABELS[task.role] ?? task.role}</span>
                <progress aria-label={`${ROLE_LABELS[task.role] ?? task.role}进度`}
                  value={pct ?? undefined} max={100} />
                <span className="progress-detail">
                  {task.status === 'NEEDS_REVIEW'
                    ? `待核对：${task.error_code ?? task.stage_label}`
                    : `${task.stage_label}${pct !== null ? ` · ${task.progress_done}/${task.progress_total}` : ''}`}
                </span>
                {consentBlocked && (
                  <button type="button" className="must-button" disabled={busy}
                    onClick={() => authorizeAndContinue(task)}>
                    授权本机文本发送并继续
                  </button>
                )}
                {task.status === 'NEEDS_REVIEW' && !consentBlocked && task.error_code
                  && task.error_code !== 'REMOTE_AGENT_CONSENT_REQUIRED' && (
                  <button type="button" className="secondary-button" disabled={busy}
                    onClick={() => run(() => resumePipelineTask(projectId, task.task_id, task.state_version))}>
                    修复后继续
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ① 文本导入与分卷 */}
      <div className="pipeline-step">
        <b>① 文本导入与分卷</b>
        {!overview.volume_plan_confirmed && (
          <div className="row">
            <select aria-label="选择母本文档" defaultValue="">
              <option value="" disabled>选择已导入的母本文档</option>
              {(documents ?? []).map((d) => <option key={d.id} value={d.id}>{d.filename}</option>)}
            </select>
            <button type="button" className="primary-button" disabled={busy}
              onClick={async (event) => {
                const select = event.currentTarget.previousElementSibling as HTMLSelectElement;
                const doc = (documents ?? []).find((d) => d.id === select.value);
                if (!doc) { onMessage('请先选择一个母本文档', 'error'); return; }
                setBusy(true);
                try {
                  const draft = await importPipelineSource(projectId, doc.id, doc.sha256);
                  setVolumePlanDraft({ id: draft.volume_plan_id, sha256: draft.sha256, volumes: draft.volumes });
                  await refresh();
                } catch (error) {
                  onMessage((error as Error).message, 'error');
                } finally { setBusy(false); }
              }}>生成分卷方案</button>
          </div>
        )}
        {volumePlanDraft && !overview.volume_plan_confirmed && (
          <div className="row">
            <span>分卷方案已生成（{volumePlanDraft.volumes.length} 卷），请确认。</span>
            <button type="button" className="must-button" disabled={busy}
              onClick={() => run(() => confirmVolumePlan(projectId, volumePlanDraft.id, volumePlanDraft.sha256),
                () => setVolumePlanDraft(null))}>确认分卷方案</button>
          </div>
        )}
        {overview.volume_plan_confirmed && <span className="muted">母本已导入，分卷方案已确认。</span>}
      </div>

      {/* ② 大纲与蓝图 */}
      <div className="pipeline-step">
        <b>② 故事大纲与全局蓝图</b>
        {!overview.volume_plan_confirmed && <span className="muted">先完成第①步。</span>}
        {overview.volume_plan_confirmed && volumes.length > 0 && (
          <ul className="volume-list">
            {volumes.map((v) => (
              <li key={v.volume_id}>
                <span>{v.title}</span>
                <span className="muted">大纲{v.outline_confirmed ? '已确认' : '未确认'}</span>
                <button type="button" className="secondary-button" disabled={busy}
                  onClick={() => run(() => queueVolumeOutline(projectId, v.volume_id))}>
                  {v.outline_confirmed ? '重新提取大纲' : '提取大纲'}
                </button>
                {v.latest_outline && !v.outline_confirmed && (
                  <button type="button" className="must-button" disabled={busy}
                    title={v.latest_outline.questions.length
                      ? `待确认问题：${v.latest_outline.questions.join('；')}`
                      : ''}
                    onClick={() => run(() => confirmVolumeOutline(
                      projectId, v.volume_id, v.latest_outline!.artifact_id,
                      v.latest_outline!.sha256, v.latest_outline!.questions_sha256))}>
                    确认本卷大纲
                  </button>
                )}
                {v.latest_outline && !v.outline_confirmed && v.latest_outline.questions.length > 0 && (
                  <div className="muted small">
                    大纲提出了 {v.latest_outline.questions.length} 个待确认问题，按钮上的提示列出全文；确认即视为已读并接受。
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {overview.volume_plan_confirmed && !overview.bible_confirmed && (
          <div className="row">
            <button type="button" className="must-button" disabled={busy}
              onClick={() => run(() => queueBible(projectId))}>生成全局故事蓝图</button>
          </div>
        )}
        {overview.pending_bible && !overview.bible_confirmed && (
          <div className="row">
            <span>全局故事蓝图已生成，请阅读后确认。</span>
            <button type="button" className="must-button" disabled={busy}
              onClick={() => run(() => confirmBible(projectId, overview.pending_bible!.artifact_id, overview.pending_bible!.sha256))}>
              确认蓝图</button>
          </div>
        )}
        {overview.bible_confirmed && <span className="muted">全局故事蓝图已确认。</span>}
      </div>

      {/* ③ 剧本编写 */}
      <div className="pipeline-step">
        <b>③ 剧本编写</b>
        {!overview.bible_confirmed && <span className="muted">先确认全局故事蓝图（第②步）。</span>}
        {overview.bible_confirmed && volumes.length > 0 && (
          <div className="scripts-grid">
            {volumes.map((v) => (
              <div key={v.volume_id} className="volume-scripts">
                <div><b>{v.title}</b><span className="muted"> 剧本 {v.scripts_confirmed}/{v.scripts_total} 场已确认</span></div>
                <button type="button" className="secondary-button" disabled={busy}
                  onClick={() => run(() => queueVolumeScripts(projectId, v.volume_id))}>
                  {v.scripts_total === 0 ? '先提取大纲再写剧本' : '生成未确认场次的剧本'}
                </button>
                <ul className="script-list">
                  {v.scripts.map((s) => (
                    <li key={s.section_id}>
                      <span>{s.scene_key} {s.title ?? ''}</span>
                      <span className={s.confirmed ? 'ok' : 'pending'}>{s.confirmed ? '已确认' : '未确认'}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        {pendingScripts.map((s) => (
          <div className="row" key={s.artifact_id}>
            <span>场次 {s.scene_key} 的剧本已生成，请确认或编辑。</span>
            <button type="button" className="must-button" disabled={busy}
              onClick={() => run(() => confirmSceneScript(projectId, s.artifact_id, s.sha256))}>确认这一场</button>
            <button type="button" className="secondary-button" disabled={busy}
              onClick={() => { setEditTarget(s.artifact_id); setEditText(s.text); }}>编辑</button>
          </div>
        ))}
        {editTarget && (
          <div className="edit-box">
            <textarea aria-label="编辑剧本" value={editText} rows={8}
              onChange={(event) => setEditText(event.target.value)} />
            <div className="row">
              <button type="button" className="must-button" disabled={busy || !editText.trim()}
                onClick={() => run(() => editSceneScript(projectId, editTarget, editText),
                  () => { setEditTarget(null); setEditText(''); })}>保存为新版本</button>
              <button type="button" className="secondary-button" disabled={busy}
                onClick={() => setEditTarget(null)}>取消</button>
            </div>
          </div>
        )}
      </div>

      {/* ④ 分镜编写（草案）与卷交付 */}
      <div className="pipeline-volume-step">
        <b>④ 分镜编写（草案）与卷交付</b>
        <span className="muted">草案不需要素材与风格冻结；正式拍摄（卷交付）仍走原有全部门禁。</span>
        {volumes.map((v) => (
          <div key={v.volume_id} className="row">
            <span>{v.title}</span>
            <button type="button" className="secondary-button" disabled={busy || !v.scripts_confirmed}
              title={v.scripts_confirmed ? '' : '先确认该卷全部场次剧本'}
              onClick={() => run(() => generateStoryboardDraft(projectId, v.volume_id))}>
              生成分镜草案
            </button>
          </div>
        ))}
        <div className="row">
          <span>卷交付 H3 制作（正式拍摄）：</span>
          <select aria-label="选择生成方案" value={handoffProfile}
            onChange={(event) => setHandoffProfile(event.target.value)}>
            <option value="" disabled>选择已登记的生成方案</option>
            {(workflowProfiles ?? []).map((p) => (
              <option key={p.id} value={p.id}>{p.name}（{p.mode}）</option>
            ))}
          </select>
          <button type="button" className="must-button" disabled={busy || !handoffProfile}
            onClick={() => run(() => requestVolumeHandoff(projectId, volumes[0]?.volume_id ?? '', handoffProfile),
              (result) => {
                const handoff = result as { readiness: { startable: boolean; blockers: Array<{ code: string }> } };
                if (handoff.readiness?.startable) {
                  onMessage('卷交付完成：可在导演包页创建生产运行', 'success');
                } else {
                  onMessage(`卷交付已建立，但有 ${handoff.readiness?.blockers?.length ?? 0} 项待解决`, 'error');
                }
              })}>
            交付本卷制作
          </button>
        </div>
        <div className="muted small">
          卷交付会把本卷已应用的镜头装进分集计划与导演包；缺少已批准母版、未冻结风格或方案未映射时会列出全部缺项。
        </div>
      </div>
    </section>
  );
}
