import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleHelp, ListChecks } from 'lucide-react';
import {
  ERROR_CODE_LABELS,
  adjustVolumeBoundaries, confirmBible,
  confirmEpisodeScript, confirmSceneScript, confirmVolumeOutline, confirmVolumePlan,
  editSceneScript, getPipelineOverview,
  getTextConsentScope, grantTextConsent, importPipelineSource, listScriptDocuments,
  openDirectorPackage, queueBible, queueVolumeOutline, queueVolumeScripts,
  reconcilePipelineTask, requestVolumeHandoff, resumePipelineTask, startPipelineNewAttempt,
  authorizeBlockedScene, buildEpisodeScript,
  type PipelineOverview, type PipelineTaskSummary,
  type WorkflowProfileLite,
} from '../api';
import { failureLabels } from '../constants';
import { Meter } from './Meter';
import { StoryboardDraftSection } from './StoryboardDraftSection';

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

const ROLE_LABELS: Record<string, string> = {
  story_outline_run: '提取大纲',
  four_stage_bible_run: '生成全局故事蓝图',
  four_stage_scene_script_run: '编写场次剧本',
};

/** What a person should read for one scene: its title, else a short key, else a word. */
function sceneLabel(item: {
  scene_key?: string | null; scene_label?: string | null; title?: string | null;
}): string {
  const label = (item.scene_label ?? '').trim();
  if (label) return label;
  const title = (item.title ?? '').trim();
  const key = (item.scene_key ?? '').trim();
  if (title) return key && !UUID_LIKE.test(key) ? `${key} ${title}` : title;
  if (key && !UUID_LIKE.test(key)) return key;
  return '这一场';
}

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
  /** Per-episode target in seconds; the recommendation packs scenes up to it. */
  const [episodeTarget, setEpisodeTarget] = useState(60);
  const [episodeSections, setEpisodeSections] = useState<string[]>([]);
  const [boundaryMode, setBoundaryMode] = useState(false);
  /** The blocked scene a first click armed; the second click really re-sends. */
  const [armedScene, setArmedScene] = useState<string | null>(null);
  /** The whole-plan confirmation was refused for missing volume outlines. */
  const [bibleScopeOffer, setBibleScopeOffer] = useState(false);

  /**
   * Confirm the blueprint, and when the only obstacle is that other volumes
   * were never outlined, offer the one-volume scope instead of dead-ending.
   */
  async function confirmPendingBible(scopeVolumeId?: string) {
    const pending = overview?.pending_bible;
    if (!pending || actionInFlight.current) return;
    actionInFlight.current = true;
    setBusy(true);
    try {
      await confirmBible(projectId, pending.artifact_id, pending.sha256, scopeVolumeId);
      setBibleScopeOffer(false);
      await refresh();
      onMessage(scopeVolumeId
        ? '蓝图已按单卷范围确认：这份蓝图只能交付当前卷。'
        : '蓝图已确认。', 'success');
    } catch (error) {
      const text = (error as Error).message;
      if (!scopeVolumeId && text.includes('PIPELINE_BIBLE_OUTLINE_COVERAGE_INCOMPLETE')) {
        setBibleScopeOffer(true);
        onMessage('还有别的卷没有读过大纲，整书蓝图暂时不能确认；可以只为当前卷确认一次。', 'error');
      } else {
        onMessage(text, 'error');
      }
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  }

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
    if (actionInFlight.current) {
      // guard 只是防御性兜底：正常情况下按钮已 loading 不可再点。命中说明状态不同步，
      // 必须给出回声，不能静默吞点击。
      onMessage('上一操作仍在进行，请稍候。', 'error');
      return;
    }
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

  const volumes = overview?.volumes ?? [];
  const pendingScripts = overview?.pending_scripts ?? [];
  const activeTasks = overview?.active_tasks ?? [];
  const currentVolumeId = volumes.some((volume) => volume.volume_id === selectedVolumeId)
    ? selectedVolumeId : volumes[0]?.volume_id ?? '';
  const currentVolume = volumes.find((v) => v.volume_id === currentVolumeId);
  const bibleNeedsConfirmation = !overview?.bible_confirmed || !!overview?.bible_status?.blockers.length;
  // Scene -> episode assignment the server already holds.  A recommendation
  // must not re-offer a scene that is already in another episode contract.
  const assignedSections = new Set(
    (currentVolume?.episode_contracts ?? []).flatMap((contract) => contract.sections));
  const selectedEpisodeSeconds = (currentVolume?.scripts ?? [])
    .filter((script) => episodeSections.includes(script.section_id))
    .reduce((total, script) => total + (script.estimated_duration_s ?? 0), 0);
  /**
   * The next episode, recommended rather than invented by the user: walk the
   * confirmed scenes in order, skip the ones already assigned, and stop when
   * the planning estimate reaches the target.  One scene is always included -
   * a scene longer than the target still has to go somewhere.
   */
  const suggestedEpisodeSections = (() => {
    const picked: string[] = [];
    let seconds = 0;
    for (const script of currentVolume?.scripts ?? []) {
      if (!script.confirmed || assignedSections.has(script.section_id)) continue;
      picked.push(script.section_id);
      seconds += script.estimated_duration_s ?? 0;
      if (seconds >= episodeTarget) break;
    }
    return picked;
  })();
  // The draft is read from the server, never from local state: a refresh, a
  // crash or a restart must show the same pending work.
  // The plan draft is local state while generating, and a server field after a
  // refresh. Either way the person must still be able to confirm it.
  const pendingPlan = volumePlanDraft ?? overview?.pending_volume_plan ?? null;
  const receipt = currentVolume?.handoff ?? null;
  const contracts = currentVolume?.episode_contracts ?? [];

  // The recommendation is pre-checked from the script so the person only
  // adjusts or applies it. Once they touch the selection (or ask for a new
  // recommendation) the auto-pick stops overriding them; confirming an
  // episode re-arms it for the next one.
  const episodeTouchedRef = useRef(false);
  useEffect(() => { episodeTouchedRef.current = false; }, [currentVolumeId]);
  const suggestionKey = `${currentVolumeId}:${contracts.length}:${episodeTarget}:`
    + suggestedEpisodeSections.join(',');
  useEffect(() => {
    if (episodeTouchedRef.current) return;
    setEpisodeSections((previous) => (
      previous.length === suggestedEpisodeSections.length
      && previous.every((id, index) => id === suggestedEpisodeSections[index])
        ? previous : suggestedEpisodeSections));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestionKey]);

  if (!overview) {
    return <section className="card" id="four-stage"><div className="notice"><CircleHelp size={13} />
      {loadFailed
        ? <><p>四阶段进度读取失败。</p><button type="button" className="btn-secondary"
            onClick={() => void refresh()}>重新读取</button></>
        : <p>正在读取四阶段进度…</p>}
    </div></section>;
  }

  return (
    <section className="card" id="four-stage">
      <header className="stage-head"><ListChecks size={17} /><h3>AI 编导四阶段</h3>
        <span className="muted">导入 → 大纲 → 剧本 → 分镜</span></header>
      <div className="notice"><CircleHelp size={13} /><div>
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
                {inFlight && <Meter
                  label={(ROLE_LABELS[task.role] ?? task.role) + '进度'}
                  value={pct ?? 0} max={100} tone="busy" />}
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
                  <button type="button" className="btn-secondary" disabled={busy}
                    onClick={() => authorizeAndContinue(task)}>
                    授权本机文本发送并继续
                  </button>
                )}
                {!inFlight && actions.includes('reconcile') && !consentBlocked && (
                  <button type="button" className="btn-secondary" disabled={busy}
                    onClick={() => run(() => reconcilePipelineTask(
                      projectId, task.task_id, task.state_version))}>
                    核对这一项
                  </button>
                )}
                {!inFlight && actions.includes('resume') && !consentBlocked && (
                  <button type="button" className="btn-secondary" disabled={busy}
                    onClick={() => run(() => resumePipelineTask(projectId, task.task_id, task.state_version))}>
                    修复后继续
                  </button>
                )}
                {!inFlight && actions.includes('new_attempt') && (
                  <button type="button" className="btn-secondary" disabled={busy}
                    title="上一次可能已经发出去了。再次发送会多一次调用，上一次的结果会作为历史保留。"
                    onClick={() => {
                      if (!window.confirm('上一次模型请求的结果仍未知。确认发起一次新请求？')) return;
                      void run(() => startPipelineNewAttempt(projectId, task.task_id, task.state_version));
                    }}>
                    核对后重新发送一次
                  </button>
                )}
                {!inFlight && task.blocked_scene?.task_id && (
                  // Two steps inside the page: a native confirm() blocks the
                  // whole tab (and any automation driving it), while a paid
                  // retry still must not be one stray click away.
                  <button type="button" className="btn-secondary" disabled={busy}
                    title="这一幕的模型请求可能已经被回答过。授权只会对这一幕再发一次，原来那次保留为历史。"
                    onClick={() => {
                      if (armedScene === task.blocked_scene!.task_id) {
                        setArmedScene(null);
                        void run(() => authorizeBlockedScene(
                          projectId, task.task_id, task.blocked_scene!.task_id, task.state_version));
                        return;
                      }
                      setArmedScene(task.blocked_scene!.task_id);
                      onMessage('再点一次即确认为这一幕重发一次模型请求；原来那一次会作为历史保留。', 'success');
                    }}>
                    {armedScene === task.blocked_scene!.task_id ? '再点一次确认重发' : '授权重新发送这一幕'}
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
            <button type="button" className="btn-secondary" disabled={busy}
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
            <button type="button" className="btn-secondary" disabled={busy}
              onClick={() => run(() => confirmVolumePlan(projectId, pendingPlan.artifact_id, pendingPlan.sha256),
                () => setVolumePlanDraft(null))}>确认分卷方案</button>
          </div>
        )}
        {overview.volume_plan_confirmed && <span className="muted">母本已导入，分卷方案已确认。</span>}
        {overview.volume_plan_confirmed && (
          <div className="row">
            <button type="button" className="btn-secondary" disabled={busy}
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
        {overview.volume_plan_confirmed && volumes.length > 0 && (() => {
          // 84 volumes each rendered a button row: the current volume's script
          // and storyboard steps ended up thousands of pixels below the fold.
          // The volume being worked on stays visible; the rest waits in a fold.
          const current = volumes.find((v) => v.volume_id === currentVolumeId) ?? volumes[0];
          const others = volumes.filter((v) => v.volume_id !== current.volume_id);
          const row = (v: (typeof volumes)[number]) => (
            <li key={v.volume_id}>
              <span>{v.title}{v.volume_id === currentVolumeId ? '（当前）' : ''}</span>
              <span className="muted">大纲{v.outline_confirmed ? '已确认' : '未确认'}</span>
              <button type="button" className="btn-secondary" disabled={busy}
                onClick={() => run(() => queueVolumeOutline(projectId, v.volume_id))}>
                {v.outline_confirmed ? '重新提取大纲' : '提取大纲'}
              </button>
              {v.latest_outline && !v.outline_confirmed && (
                <button type="button" className="btn-secondary" disabled={busy}
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
          );
          return <>
            <ul className="volume-list">{row(current)}</ul>
            {others.length > 0 && <details className="volume-fold">
              <summary>其他 {others.length} 卷的大纲（按需展开，不影响当前卷）</summary>
              <ul className="volume-list">{others.map(row)}</ul>
            </details>}
          </>;
        })()}
        {overview.volume_plan_confirmed && bibleNeedsConfirmation && (
          <div className="row">
            <button type="button" className="btn-secondary" disabled={busy}
              onClick={() => run(() => queueBible(projectId))}>生成全局故事蓝图</button>
            <span className="muted small">蓝图按当前计划的每一卷大纲合成；缺卷会如实标为草稿，不能作为正式发布依据。</span>
          </div>
        )}
        {overview.pending_bible && bibleNeedsConfirmation && (
          <div className="row">
            <span>全局故事蓝图已生成，请阅读后确认。</span>
            <button type="button" className="btn-secondary" disabled={busy}
              onClick={() => void confirmPendingBible()}>
              确认蓝图</button>
          </div>
        )}
        {bibleScopeOffer && overview.pending_bible && (
          <div className="row">
            <button type="button" className="btn-secondary" disabled={busy}
              onClick={() => void confirmPendingBible(currentVolumeId)}>
              只为{volumes.find((v) => v.volume_id === currentVolumeId)?.title ?? '当前卷'}确认蓝图</button>
            <span className="muted small">
              整书蓝图需要每一卷都读过大纲；其余卷还没读，所以只能按「单卷范围」确认一次：
              这份蓝图只能交付这一卷，别的卷仍要重新生成并确认。
            </span>
          </div>
        )}
        {!bibleNeedsConfirmation && <span className="muted">全局故事蓝图已确认。</span>}
        {overview.bible_confirmed && (overview.bible_status?.blockers ?? []).length > 0 && (
          <div className="blocker-item is-action">
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
        {overview.bible_confirmed && volumes.length > 0 && (() => {
          // Same reason as step ②: 84 volume blocks bury the storyboard step.
          const current = volumes.find((v) => v.volume_id === currentVolumeId) ?? volumes[0];
          const others = volumes.filter((v) => v.volume_id !== current.volume_id);
          const block = (v: (typeof volumes)[number]) => (
            <div key={v.volume_id} className="volume-scripts">
              <div><b>{v.title}</b><span className="muted"> 剧本 {v.scripts_confirmed}/{v.scripts_total} 场已确认</span></div>
              <button type="button" className="btn-secondary" disabled={busy}
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
          );
          return <>
            <div className="scripts-grid">{block(current)}</div>
            {others.length > 0 && <details className="volume-fold">
              <summary>其他 {others.length} 卷的剧本（按需展开）</summary>
              <div className="scripts-grid">{others.map(block)}</div>
            </details>}
          </>;
        })()}
        {pendingScripts.map((s) => {
          // A hard verdict does not delete the scene: it refuses to publish it.
          // The person still has the two real exits - read it, or edit it.
          const blocked = s.qa?.state === 'HARD_BLOCK';
          return (
            <div className="row" key={s.artifact_id}>
              <span>「{sceneLabel(s)}」这一幕的剧本已生成，请确认或编辑。</span>
              {blocked && (
                <span className="pending">
                  未通过终校：{s.qa?.message ?? '请复核这一场'}。正文已保存，可编辑后重新确认。
                </span>
              )}
              {!blocked && s.qa && s.qa.state !== 'INFO' && (
                <span className="muted small">终校建议：{s.qa.message ?? '请复核这一场'}</span>
              )}
              <button type="button" className="btn-secondary" disabled={busy || blocked}
                title={blocked ? '先按终校提示修改正文，再确认这一场。' : ''}
                onClick={() => run(() => confirmSceneScript(projectId, s.artifact_id, s.sha256))}>
                确认这一场</button>
              <button type="button" className="btn-secondary" disabled={busy}
                onClick={() => { setEditTarget(s.artifact_id); setEditText(s.text); }}>编辑</button>
            </div>
          );
        })}
        {editTarget && (
          <div className="edit-box">
            <textarea aria-label="编辑剧本" value={editText} rows={8}
              onChange={(event) => setEditText(event.target.value)} />
            <div className="row">
              <button type="button" className="btn-secondary" disabled={busy || !editText.trim()}
                onClick={() => run(() => editSceneScript(projectId, editTarget, editText),
                  () => { setEditTarget(null); setEditText(''); })}>保存为新版本</button>
              <button type="button" className="btn-secondary" disabled={busy}
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
            <StoryboardDraftSection projectId={projectId} volume={currentVolume}
              onMessage={onMessage} onRefresh={refresh} />
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
              <div className="episode-picker">
                <div className="row episode-picker-toolbar">
                  <label htmlFor="four-stage-episode-title">按场分集：</label>
                  <input id="four-stage-episode-title" aria-label="分集标题" value={episodeTitle}
                    onChange={(event) => setEpisodeTitle(event.target.value)}
                    placeholder={'第' + (currentVolume.episode_contracts.length + 1) + '集'} />
                  <label className="muted small">每集目标
                    <input type="number" min={10} max={3600} aria-label="每集目标秒数"
                      value={episodeTarget}
                      onChange={(event) => setEpisodeTarget(Number(event.target.value) || 60)} />
                    秒
                  </label>
                  <button type="button" className="btn-secondary" disabled={busy}
                    onClick={() => { episodeTouchedRef.current = true; setEpisodeSections(suggestedEpisodeSections); }}
                    title="从未分集的已确认场次开始，按顺序装到目标时长为止；你仍可逐场改勾选">
                    推荐勾选下一集
                  </button>
                  <span className="muted small">
                    已选 {episodeSections.length} 场 · 约 {Math.round(selectedEpisodeSeconds)} 秒
                    （估算按原文字数/5，只用于规划；推荐已自动勾选，可直接调整或应用）
                  </span>
                </div>
                <div className="episode-scene-grid" role="group" aria-label="按场分集场次勾选">
                  {currentVolume.scripts.map((script, index) => {
                    const used = assignedSections.has(script.section_id);
                    const checked = episodeSections.includes(script.section_id);
                    return (
                      <label key={script.section_id}
                        className={`episode-scene-cell${checked ? ' is-selected' : ''}${used && !checked ? ' is-assigned' : ''}`}>
                        <span className="episode-scene-top">
                          <span className="episode-scene-order">{String(index + 1).padStart(2, '0')}</span>
                          <input type="checkbox" checked={checked}
                            disabled={used && !checked}
                            aria-label={sceneLabel(script)}
                            onChange={(event) => {
                              episodeTouchedRef.current = true;
                              setEpisodeSections((previous) => (
                                event.target.checked
                                  ? [...previous, script.section_id]
                                  : previous.filter((id) => id !== script.section_id)));
                            }} />
                        </span>
                        <span className="episode-scene-title">{sceneLabel(script)}</span>
                        <span className="episode-scene-meta">
                          {script.estimated_duration_s
                            ? `约 ${Math.round(script.estimated_duration_s)} 秒` : '时长待估'}
                          {used ? ' · 已归入别集' : ''}
                        </span>
                      </label>
                    );
                  })}
                </div>
                <div className="row">
                  <button type="button" className="btn-secondary"
                    disabled={busy || episodeSections.length === 0}
                    onClick={() => run(async () => {
                      const title = episodeTitle.trim() || '第' + (currentVolume.episode_contracts.length + 1) + '集';
                      const drafted = await buildEpisodeScript(
                        projectId, currentVolume.volume_id, title, episodeSections);
                      const confirmed = await confirmEpisodeScript(
                        projectId, drafted.artifact_id, drafted.sha256);
                      setEpisodeTitle('');
                      setEpisodeSections([]);
                      episodeTouchedRef.current = false;
                      return confirmed;
                    }, () => onMessage('这一集的分集合同已确认', 'success'))}>
                    生成并确认这一集
                  </button>
                </div>
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
              <button type="button" className="btn-secondary" disabled={busy || !handoffProfile}
                title={handoffProfile ? '把这一卷交付为导演包' : '未登记生成方案：先在基础配置登记并验证本机生成方案'}
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
                  <button type="button" className="btn-secondary" disabled={busy}
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
        <button type="button" className="btn-secondary" disabled={busy}
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
