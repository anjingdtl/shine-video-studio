import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleHelp, ListChecks } from 'lucide-react';
import {
  ERROR_CODE_LABELS,
  adjustVolumeBoundaries, applyCameraPlan, buildEpisodeScript, confirmBible,
  confirmEpisodeScript, confirmSceneScript, confirmVolumeOutline, confirmVolumePlan,
  editSceneScript, generateStoryboardDraft, generateStoryboardDraftFor, getPipelineOverview,
  getTextConsentScope, grantTextConsent, importPipelineSource, listScriptDocuments,
  openDirectorPackage, queueBible, queueVolumeOutline, queueVolumeScripts,
  reconcileFilmTask,
  resendUnknownFilmTask, reconcilePipelineTask, requestVolumeHandoff, resumePipelineTask, startPipelineNewAttempt,
  type PipelineOverview, type PipelineTaskSummary, type StoryboardUnit,
  type WorkflowProfileLite,
} from '../api';
import { failureLabels } from '../constants';

type Props = {
  projectId: string;
  documentsVersion?: number;
  workflowProfiles?: WorkflowProfileLite[];
  onMessage: (message: string, kind?: 'error' | 'success') => void;
};

/**
 * Scene identifiers are not always readable.
 *
 * A scene whose key never got a short name falls back to the section UUID, and
 * the panel used to print that UUID beside the title - the person saw a
 * 36-character id where a scene name belongs. The title is what they read; the
 * key is shown only when it is a real short key.
 */
const UUID_LIKE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-/i;

/** A storyboard unit's state in words, never the raw enum. */
const STORYBOARD_STATUS_LABELS: Record<string, string> = {
  REJECTED: '这一场被拒绝',
  NEEDS_REVIEW: '这一场待人工核对',
  INCOMPLETE: '这一场还没完成',
  FAILED: '这一场失败了',
  RUNNING: '这一场生成中',
  WAITING_DEPENDENCY: '等前面的部分先完成',
};

/** What a person should read for one scene: its title, else a short key, else a word. */
function sceneLabel(item: { scene_key?: string | null; title?: string | null }): string {
  const title = (item.title ?? '').trim();
  const key = (item.scene_key ?? '').trim();
  if (title) return key && !UUID_LIKE.test(key) ? `${key} ${title}` : title;
  if (key && !UUID_LIKE.test(key)) return key;
  return '这一场';
}


const ROLE_LABELS: Record<string, string> = {
  story_outline_run: '提取大纲',
  four_stage_bible_run: '生成全局故事蓝图',
  four_stage_scene_script_run: '编写场次剧本',
};

/** The four-stage creative pipeline in one place: import, outline, script, storyboard. */
export function FourStagePanel({ projectId, documentsVersion, workflowProfiles, onMessage }: Props) {
  const [overview, setOverview] = useState<PipelineOverview | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const actionInFlight = useRef(false);
  const [volumePlanDraft, setVolumePlanDraft] = useState<{ artifact_id: string; sha256: string; volumes: unknown[] } | null>(null);
  const [documents, setDocuments] = useState<Array<{ id: string; filename: string; sha256: string }>>([]);
  const [editTarget, setEditTarget] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [handoffProfile, setHandoffProfile] = useState('');
  // The whole panel works on ONE selected volume: reviewing, drafting,
  // applying and delivering must never drift to a different volume than the
  // one the person is looking at.
  const [selectedVolumeId, setSelectedVolumeId] = useState<string>('');
  const [episodeTitle, setEpisodeTitle] = useState('');
  const [episodeSections, setEpisodeSections] = useState<string[]>([]);
  const [boundaryMode, setBoundaryMode] = useState(false);

  const refresh = useCallback(async () => {
    setLoadFailed(false);
    try {
      const [data, docs] = await Promise.all([
        getPipelineOverview(projectId),
        listScriptDocuments(projectId),
      ]);
      setOverview(data);
      setDocuments((docs ?? []).filter((doc) => doc.source_format !== 'volume'));
    } catch {
      setLoadFailed(true);
    }
  }, [projectId]);

  useEffect(() => { void refresh(); }, [refresh, documentsVersion]);
  // Switching project must not keep another project's selection or receipt.
  useEffect(() => {
    setSelectedVolumeId('');
    setHandoffProfile('');
    setVolumePlanDraft(null);
    setEpisodeTitle('');
    setEpisodeSections([]);
  }, [projectId]);

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
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(true);
    try {
      const result = await action();
      await refresh();
      after?.(result);
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  }

  if (!overview) {
    return <section className="card" id="four-stage"><div className="module-guide"><CircleHelp size={13} />
      {loadFailed
        ? <><p>四阶段进度读取失败。</p><button type="button" className="secondary-button"
            onClick={() => void refresh()}>重新读取</button></>
        : <p>正在读取四阶段进度…</p>}
    </div></section>;
  }

  const volumes = overview.volumes ?? [];
  const pendingScripts = overview.pending_scripts ?? [];
  const activeTasks = overview.active_tasks ?? [];
  const currentVolumeId = volumes.some((volume) => volume.volume_id === selectedVolumeId)
    ? selectedVolumeId : volumes[0]?.volume_id ?? '';
  const currentVolume = volumes.find((v) => v.volume_id === currentVolumeId);
  const bibleNeedsConfirmation = !overview.bible_confirmed || !!overview.bible_status?.blockers.length;
  // The draft is read from the server, never from local state: a refresh, a
  // crash or a restart must show the same pending work.
  // The plan draft is local state while generating, and a server field after a
  // refresh. Either way the person must still be able to confirm it.
  const pendingPlan = volumePlanDraft ?? overview.pending_volume_plan ?? null;
  const draftUnits = currentVolume?.storyboard?.units ?? [];
  const draftStatus = currentVolume?.storyboard?.status ?? null;
  const receipt = currentVolume?.handoff ?? null;
  const contracts = currentVolume?.episode_contracts ?? [];

  async function draftAndRecord(volumeId: string, sectionIds?: string[]) {
    setBusy(true);
    try {
      const result = sectionIds && sectionIds.length > 0
        ? await generateStoryboardDraftFor(projectId, volumeId, sectionIds)
        : await generateStoryboardDraft(projectId, volumeId);
      const proposed = result.units.filter((u) => u.status === 'PROPOSED').length;
      if (result.status === 'PROPOSED') {
        onMessage('分镜草案已生成：' + proposed + ' 个场次单元，请逐场应用', 'success');
      } else {
        const codes = (result.rejections ?? []).map((row) => row.rejection_reason_code ?? row.code);
        onMessage('分镜草案未全部完成：' + proposed + '/' + result.units.length
          + ' 个单元已生成；' + (codes[0] ?? '请查看原因'), 'error');
      }
      await refresh();
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally { setBusy(false); }
  }

  async function applyUnit(unit: StoryboardUnit) {
    if (!unit.camera_artifact_id) return;
    await run(
      () => applyCameraPlan(projectId, unit.camera_artifact_id!, unit.continuity_artifact_id,
                            unit.arbitration_artifact_id),
      (applied) => {
        const body = applied as { shot_ids?: string[];
          material_notices?: Array<{ code: string; message: string }> };
        const shotIds = body?.shot_ids ?? [];
        const notices = body?.material_notices ?? [];
        if (notices.length > 0) {
          onMessage('这一场的镜头已应用：' + shotIds.length + ' 个；' + notices[0].message, 'success');
        } else {
          onMessage('这一场的镜头已应用：' + shotIds.length + ' 个', 'success');
        }
      });
  }

  /**
   * Reconcile one failed unit and continue only that unit.
   *
   * The film ledger decides whether each call is safe to repeat.
   * A call whose outcome stays unknown needs the person's decision: this
   * button is that decision, so the unknown attempt is retired and the scene
   * is asked exactly once more.
   */
  async function reconcileUnit(unit: StoryboardUnit) {
    setBusy(true);
    try {
      const conclusions: string[] = [];
      const unknown: Array<{ task_id: string; state_version: number }> = [];
      const rejections = unit.rejections ?? [];
      let mayContinue = rejections.length > 0;
      for (const rejection of rejections) {
        if (!rejection.task_id) {
          mayContinue = false;
          continue;
        }
        const result = await reconcileFilmTask(
          projectId, rejection.task_id, rejection.state_version ?? 0);
        conclusions.push(result.note ?? (result.outcome === 'UNCONFIRMABLE'
          ? '无法确认服务商是否已处理这次调用，原任务已保留。'
          : result.outcome));
        if (result.outcome === 'UNKNOWN_REQUIRES_NEW_ATTEMPT') {
          unknown.push({ task_id: rejection.task_id, state_version: result.state_version });
        }
        mayContinue = mayContinue && result.resubmittable;
      }
      if (conclusions.length === 0) {
        onMessage('这一项没有可核对的调用记录，请查看任务详情', 'error');
        return;
      }
      if (unknown.length > 0) {
        // The person asked for this scene to be redone; an attempt whose
        // outcome cannot be confirmed is retired by that decision and the
        // scene is asked once more with a new request.
        for (const entry of unknown) {
          const resent = await resendUnknownFilmTask(
            projectId, entry.task_id, entry.state_version);
          conclusions.push(resent.note ?? '已登记你的确认，将以一次新的模型请求重问这一场。');
        }
        mayContinue = true;
      }
      await refresh();
      if (!mayContinue) {
        onMessage(conclusions.join('；'), 'error');
        return;
      }
      onMessage(conclusions.join('；') + ' 正在只重做这一场', 'success');
      setBusy(false);
      await draftAndRecord(currentVolumeId, [unit.section_id]);
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally { setBusy(false); }
  }

  return (
    <section className="card" id="four-stage">
      <header className="stage-head"><ListChecks size={17} /><h3>AI 编导四阶段</h3>
        <span className="muted">导入 → 大纲 → 剧本 → 分镜</span></header>
      <div className="module-guide"><CircleHelp size={13} /><div>
        <p><b>四步做完一部作品：</b>①选择母本并确认分卷 → ②逐卷提取大纲并确认全局故事蓝图 →
          ③逐场生成并确认剧本 → ④逐场生成分镜草案、应用镜头并交付本卷。</p>
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
            const actions = task.available_actions ?? [];
            // A progress bar without a value is an indeterminate animation: it
            // keeps moving even after the work is done.  Only a task that is
            // really in flight gets one.
            const inFlight = task.status === 'QUEUED' || task.status === 'RUNNING';
            const errorReason = task.error_code && (ERROR_CODE_LABELS[task.error_code]
              ?? failureLabels[task.error_code] ?? '这一步未完成，请根据错误代码核对原因。');
            return (
              <div className="progress-row" key={task.task_id}>
                <span className="progress-name">{ROLE_LABELS[task.role] ?? task.role}</span>
                {inFlight && <progress
                  aria-label={(ROLE_LABELS[task.role] ?? task.role) + '进度'}
                  value={pct ?? undefined} max={100} />}
                <span className="progress-detail">
                  {task.status === 'NEEDS_REVIEW'
                    ? '待核对：' + (task.error_code ?? task.stage_label)
                    : (task.status === 'FAILED' || task.status === 'PAUSED')
                      ? '已停止：' + (task.error_code ?? task.stage_label)
                      : task.stage_label + (inFlight && pct !== null ? ' · ' + task.progress_done + '/' + task.progress_total : '')}
                  {task.attempt > 1 ? ' · 第 ' + task.attempt + ' 次尝试' : ''}
                  {task.reconciliation?.outcome ? ' · 核对结论：' + task.reconciliation.outcome : ''}
                </span>
                {errorReason && <span className="progress-detail">{errorReason}</span>}
                {consentBlocked && (
                  <button type="button" className="must-button" disabled={busy}
                    onClick={() => authorizeAndContinue(task)}>
                    授权本机文本发送并继续
                  </button>
                )}
                {!inFlight && actions.includes('reconcile') && !consentBlocked && (
                  <button type="button" className="secondary-button" disabled={busy}
                    onClick={() => run(() => reconcilePipelineTask(
                      projectId, task.task_id, task.state_version))}>
                    核对这一项
                  </button>
                )}
                {!inFlight && actions.includes('resume') && !consentBlocked && (
                  <button type="button" className="secondary-button" disabled={busy}
                    onClick={() => run(() => resumePipelineTask(projectId, task.task_id, task.state_version))}>
                    修复后继续
                  </button>
                )}
                {!inFlight && actions.includes('new_attempt') && (
                  <button type="button" className="must-button" disabled={busy}
                    title="上一次可能已经发出去了。再次发送会多一次调用，上一次的结果会作为历史保留。"
                    onClick={() => {
                      if (!window.confirm('上一次模型请求的结果仍未知。确认发起一次新请求？')) return;
                      void run(() => startPipelineNewAttempt(projectId, task.task_id, task.state_version));
                    }}>
                    核对后重新发送一次
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
        {!overview.volume_plan_confirmed && documents.length === 0 &&
          <p className="muted">请先在上方上传母本，或从创意简报生成剧本。</p>}
        {!overview.volume_plan_confirmed && !pendingPlan && (
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
                  setVolumePlanDraft({ artifact_id: draft.volume_plan_id, sha256: draft.sha256, volumes: draft.volumes });
                  await refresh();
                } catch (error) {
                  onMessage((error as Error).message, 'error');
                } finally { setBusy(false); }
              }}>生成分卷方案</button>
          </div>
        )}
        {pendingPlan && !overview.volume_plan_confirmed && (
          <div className="row">
            <span>分卷方案已生成（{pendingPlan.volumes.length} 卷），请确认。</span>
            <button type="button" className="must-button" disabled={busy}
              onClick={() => run(() => confirmVolumePlan(projectId, pendingPlan.artifact_id, pendingPlan.sha256),
                () => setVolumePlanDraft(null))}>确认分卷方案</button>
          </div>
        )}
        {overview.volume_plan_confirmed && <span className="muted">母本已导入，分卷方案已确认。</span>}
        {overview.volume_plan_confirmed && (
          <div className="row">
            <button type="button" className="secondary-button" disabled={busy}
              onClick={() => setBoundaryMode(!boundaryMode)}>
              {boundaryMode ? '收起卷界调整' : '调整卷界'}
            </button>
            <span className="muted small">
              卷界改一次就产生新的分卷方案版本，旧版本保留为历史。
            </span>
          </div>
        )}
        {boundaryMode && overview.volume_plan && (
          <VolumeBoundaryEditor
            projectId={projectId}
            plan={overview.volume_plan}
            busy={busy}
            onMessage={onMessage}
            onDone={() => { setBoundaryMode(false); refresh(); }}
          />
        )}
      </div>

      {/* 全局面板共用同一个「当前卷」 */}
      {volumes.length > 1 && (
        <div className="row">
          <label htmlFor="four-stage-volume">当前处理的卷：</label>
          <select id="four-stage-volume" value={currentVolumeId}
            onChange={(event) => setSelectedVolumeId(event.target.value)}>
            {volumes.map((v) => (
              <option key={v.volume_id} value={v.volume_id}>
                {v.title}（大纲{v.outline_confirmed ? '已确认' : '未确认'} · 剧本 {v.scripts_confirmed}/{v.scripts_total}）
              </option>
            ))}
          </select>
        </div>
      )}

      {/* ② 大纲与蓝图 */}
      <div className="pipeline-step">
        <b>② 故事大纲与全局蓝图</b>
        {!overview.volume_plan_confirmed && <span className="muted">先完成第①步。</span>}
        {overview.volume_plan_confirmed && volumes.length > 0 && (
          <ul className="volume-list">
            {volumes.map((v) => (
              <li key={v.volume_id}>
                <span>{v.title}{v.volume_id === currentVolumeId ? '（当前）' : ''}</span>
                <span className="muted">大纲{v.outline_confirmed ? '已确认' : '未确认'}</span>
                <button type="button" className="secondary-button" disabled={busy}
                  onClick={() => run(() => queueVolumeOutline(projectId, v.volume_id))}>
                  {v.outline_confirmed ? '重新提取大纲' : '提取大纲'}
                </button>
                {v.latest_outline && !v.outline_confirmed && (
                  <button type="button" className="must-button" disabled={busy}
                    title={v.latest_outline.questions.length
                      ? '待确认问题：' + v.latest_outline.questions.join('；')
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
        {overview.volume_plan_confirmed && bibleNeedsConfirmation && (
          <div className="row">
            <button type="button" className="must-button" disabled={busy}
              onClick={() => run(() => queueBible(projectId))}>生成全局故事蓝图</button>
            <span className="muted small">蓝图按当前计划的每一卷大纲合成；缺卷会如实标为草稿，不能作为正式发布依据。</span>
          </div>
        )}
        {overview.pending_bible && bibleNeedsConfirmation && (
          <div className="row">
            <span>全局故事蓝图已生成，请阅读后确认。</span>
            <button type="button" className="must-button" disabled={busy}
              onClick={() => run(() => confirmBible(projectId, overview.pending_bible!.artifact_id, overview.pending_bible!.sha256))}>
              确认蓝图</button>
          </div>
        )}
        {!bibleNeedsConfirmation && <span className="muted">全局故事蓝图已确认。</span>}
        {overview.bible_confirmed && (overview.bible_status?.blockers ?? []).length > 0 && (
          <div className="required-action">
            <b>蓝图与当前大纲已不一致：</b>
            <ul>
              {(overview.bible_status?.blockers ?? []).map((code) => <li key={code}>{code}</li>)}
            </ul>
            <span className="muted small">请重新生成并确认蓝图，再继续生产。</span>
          </div>
        )}
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
                      <span>{sceneLabel(s)}</span>
                      <span className={s.confirmed ? 'ok' : 'pending'}>{s.confirmed ? '已确认' : '未确认'}</span>
                      {/* 终校只是一句中文结论：内部检查项与账本不进普通界面。 */}
                      {!s.confirmed && s.qa?.state === 'HARD_BLOCK' && (
                        <span className="pending">未通过终校：{s.qa.message ?? '请复核这一场'}</span>
                      )}
                      {!s.confirmed && s.qa && s.qa.state !== 'HARD_BLOCK'
                        && s.qa.state !== 'INFO' && (
                        <span className="muted small">终校建议：{s.qa.message ?? '请复核这一场'}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        {pendingScripts.map((s) => {
          // A hard verdict does not delete the scene: it refuses to publish it.
          // The person still has the two real exits - read it, or edit it.
          const blocked = s.qa?.state === 'HARD_BLOCK';
          return (
            <div className="row" key={s.artifact_id}>
              <span>场次 {s.scene_key} 的剧本已生成，请确认或编辑。</span>
              {blocked && (
                <span className="pending">
                  未通过终校：{s.qa?.message ?? '请复核这一场'}。正文已保存，可编辑后重新确认。
                </span>
              )}
              {!blocked && s.qa && s.qa.state !== 'INFO' && (
                <span className="muted small">终校建议：{s.qa.message ?? '请复核这一场'}</span>
              )}
              <button type="button" className="must-button" disabled={busy || blocked}
                title={blocked ? '先按终校提示修改正文，再确认这一场。' : ''}
                onClick={() => run(() => confirmSceneScript(projectId, s.artifact_id, s.sha256))}>
                确认这一场</button>
              <button type="button" className="secondary-button" disabled={busy}
                onClick={() => { setEditTarget(s.artifact_id); setEditText(s.text); }}>编辑</button>
            </div>
          );
        })}
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
        <span className="muted">草案不需要风格冻结；正式拍摄（卷交付）仍走原有全部门禁。</span>
        {!currentVolume && <span className="muted">先完成第①步并确认分卷。</span>}
        {currentVolume && (
          <>
            <div className="row">
              <span>{currentVolume.title}</span>
              <button type="button" className="secondary-button"
                disabled={busy || currentVolume.scripts_confirmed === 0}
                title={currentVolume.scripts_confirmed === 0
                  ? '先确认至少一场剧本；草案只覆盖已确认的场次'
                  : `草案只覆盖已确认的 ${currentVolume.scripts_confirmed}/${currentVolume.scripts_total} 场`
                    + (currentVolume.scripts_confirmed < currentVolume.scripts_total
                      ? '；其余场次确认后再生成一次即可补齐' : '')}
                onClick={() => draftAndRecord(currentVolume.volume_id)}>
                {draftUnits.length > 0
                  ? '重新生成本卷分镜草案'
                  : currentVolume.scripts_confirmed < currentVolume.scripts_total
                    ? '生成已确认场次的分镜草案'
                    : '生成分镜草案'}
              </button>
              <span className="muted small">按场逐次调用五个工种；项目再长也不会被单次输入上限卡住。</span>
            </div>
            {draftUnits.length > 0 && (
              <ul className="volume-list">
                {draftUnits.map((unit) => (
                  <li key={unit.unit_id}>
                    <span>{unit.title || unit.unit_id}
                      {unit.chunk_count && unit.chunk_count > 1
                        ? '（第 ' + unit.chunk_index + '/' + unit.chunk_count + ' 段）' : ''}</span>
                    <span className={unit.applied ? 'ok' : 'pending'}>
                      {unit.status === 'PROPOSED'
                        ? (unit.applied
                          ? '镜头已应用（' + (unit.shot_ids?.length ?? 0) + ' 个）'
                          : '待应用镜头')
                        : (STORYBOARD_STATUS_LABELS[unit.status ?? ''] ?? ('未完成：' + (unit.status ?? '未知')))}
                    </span>
                    {unit.dialogue_coverage && <span className="muted small">
                      对白节拍 {unit.dialogue_coverage.dialogue_beats}/{unit.dialogue_coverage.total_beats}
                      {unit.dialogue_coverage.target_percent > 0 && <> · 目标 {unit.dialogue_coverage.target_percent}%</>}
                      {' · '}改编台词 {unit.dialogue_coverage.adapted_lines} 条
                      {unit.dialogue_coverage.dialogue_beats * 100
                        < unit.dialogue_coverage.target_percent * unit.dialogue_coverage.total_beats && ' · 未达目标'}
                    </span>}
                    {(unit.missing_characters ?? []).length > 0 && (
                      <span className="muted small">
                        分镜已定稿，这一场要用的主素材还没备齐：
                        {(unit.missing_characters ?? []).join('、')}
                        。可以先落镜，但正式出片前要在「素材准备」里补齐；
                        缺人物参考时 H3 会拒绝提交，不会自行生成参考图。
                      </span>
                    )}
                    {unit.status === 'PROPOSED' && !unit.applied && (
                      <button type="button" className="must-button" disabled={busy}
                        title={(unit.missing_characters ?? []).length > 0
                          ? '这一场要用的主素材还没备齐：' + (unit.missing_characters ?? []).join('、')
                          + '。可以先落镜，但出片前要补齐；缺人物参考时 H3 会拒绝提交。'
                          : ''}
                        onClick={() => applyUnit(unit)}>应用这一场的镜头</button>
                    )}
                    {unit.status !== 'PROPOSED' && (
                      <button type="button" className="secondary-button" disabled={busy}
                        title="核对每条调用记录，服务端确认可以重做后才继续。"
                        onClick={() => reconcileUnit(unit)}>核对并只重做这一场</button>
                    )}
                    {unit.status !== 'PROPOSED' && (unit.rejections ?? []).length > 0 && (
                      <span className="muted small">
                        {(unit.rejections ?? []).map((row) => {
                          const code = row.rejection_reason_code ?? row.code ?? '';
                          return ERROR_CODE_LABELS[code] ?? code;
                        }).join('；')}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {draftUnits.length > 0 && draftStatus && draftStatus !== 'PROPOSED' && (
              <div className="muted small">
                有单元没有完成。逐场核对后，服务端会判断是否可以只重做这一场。
              </div>
            )}
            {contracts.length > 0 && (
              <div className="row">
                <span>分集合同：</span>
                {contracts.map((contract) => (
                  <span key={contract.artifact_id} className={contract.current ? 'ok' : 'pending'}>
                    {contract.title ?? '未命名分集'}（{contract.sections.length} 场）
                    {contract.current ? '已确认且有效' : '已失效：' + (contract.reason ?? '')}
                  </span>
                ))}
              </div>
            )}
            {currentVolume.scripts_confirmed > 0 && (
              <div className="row">
                <label htmlFor="four-stage-episode-title">按场分集：</label>
                <input id="four-stage-episode-title" aria-label="分集标题" value={episodeTitle}
                  onChange={(event) => setEpisodeTitle(event.target.value)}
                  placeholder="例如：第1集" />
                <span className="muted small">勾选要归入这一集的场次</span>
                {currentVolume.scripts.map((script) => (
                  <label key={script.section_id} className="muted small">
                    <input type="checkbox" checked={episodeSections.includes(script.section_id)}
                      onChange={(event) => setEpisodeSections((previous) => (
                        event.target.checked
                          ? [...previous, script.section_id]
                          : previous.filter((id) => id !== script.section_id)))} />
                    {sceneLabel(script)}
                  </label>
                ))}
                <button type="button" className="secondary-button"
                  disabled={busy || !episodeTitle.trim() || episodeSections.length === 0}
                  onClick={() => run(async () => {
                    const drafted = await buildEpisodeScript(
                      projectId, currentVolume.volume_id, episodeTitle.trim(), episodeSections);
                    const confirmed = await confirmEpisodeScript(
                      projectId, drafted.artifact_id, drafted.sha256);
                    setEpisodeTitle('');
                    setEpisodeSections([]);
                    return confirmed;
                  }, () => onMessage('这一集的分集合同已确认', 'success'))}>
                  生成并确认这一集
                </button>
              </div>
            )}
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
                onClick={() => run(
                  () => requestVolumeHandoff(projectId, currentVolume.volume_id, handoffProfile),
                  (result) => {
                    const body = result as {
                      package_id: string; reused: boolean; episodes: number; shots: number;
                      readiness: { startable: boolean; blockers: Array<{ code: string; message?: string }> };
                    };
                    if (body.readiness.startable) {
                      onMessage(body.reused
                        ? '这一卷已经交付过，返回的是同一份交付回执'
                        : '卷交付完成：' + body.episodes + ' 集 / ' + body.shots + ' 个镜头，可到导演包页创建生产运行',
                        'success');
                    } else {
                      onMessage('卷交付已建立，但有 ' + body.readiness.blockers.length + ' 项待解决', 'error');
                    }
                  })}>
                交付本卷制作
              </button>
            </div>
            {receipt && (
              <div className="muted small">
                <div>
                  交付回执：导演包 {receipt.package_id}（第 {receipt.package_revision} 版 · {receipt.episodes} 集 ·{' '}
                  {receipt.shots} 个镜头 · {receipt.episode_basis ?? '未知依据'}）
                </div>
                <div className="row">
                  <button type="button" className="secondary-button" disabled={busy}
                    onClick={() => openDirectorPackage(receipt.package_id)}>打开这个导演包</button>
                </div>
                {receipt.readiness.blockers.length > 0 && (
                  <ul>
                    {receipt.readiness.blockers.map((blocker) => (
                      <li key={blocker.code}>{blocker.message ?? blocker.code}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
        <div className="muted small">
          卷交付会把本卷已应用的镜头按场次顺序装进分集计划与导演包；缺少已批准母版、未冻结风格或方案未映射时会列出全部缺项。
        </div>
      </div>
    </section>
  );
}

/** The volume boundary editor: a new plan revision the person confirms. */
function VolumeBoundaryEditor({ projectId, plan, busy, onMessage, onDone }: {
  projectId: string;
  plan: { artifact_id: string; sha256: string; volumes: Array<{ title: string; start_offset: number; end_offset: number }> };
  busy: boolean;
  onMessage: (message: string, kind?: 'error' | 'success') => void;
  onDone: () => void;
}) {
  const [rows, setRows] = useState(plan.volumes.map((volume) => ({ ...volume })));
  return (
    <div className="edit-box">
      {rows.map((row, index) => (
        <div className="row" key={index}>
          <input aria-label={'第 ' + (index + 1) + ' 卷标题'} value={row.title}
            onChange={(event) => setRows((previous) => previous.map((item, at) => (
              at === index ? { ...item, title: event.target.value } : item)))} />
          <input aria-label={'第 ' + (index + 1) + ' 卷起点'} type="number" value={row.start_offset}
            onChange={(event) => setRows((previous) => previous.map((item, at) => (
              at === index ? { ...item, start_offset: Number(event.target.value) } : item)))} />
          <input aria-label={'第 ' + (index + 1) + ' 卷终点'} type="number" value={row.end_offset}
            onChange={(event) => setRows((previous) => previous.map((item, at) => (
              at === index ? { ...item, end_offset: Number(event.target.value) } : item)))} />
        </div>
      ))}
      <div className="row">
        <button type="button" className="must-button" disabled={busy}
          onClick={async () => {
            try {
              const adjusted = await adjustVolumeBoundaries(projectId, plan.artifact_id, rows);
              onMessage('新的分卷方案已生成，请到第①步确认', 'success');
              onDone();
              void adjusted;
            } catch (error) {
              onMessage((error as Error).message, 'error');
            }
          }}>生成新的分卷方案</button>
      </div>
    </div>
  );
}
