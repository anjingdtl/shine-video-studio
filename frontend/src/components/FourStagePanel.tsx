import { useCallback, useEffect, useRef, useState } from 'react';
import { CircleHelp, ListChecks } from 'lucide-react';
import {
  applyCameraPlan, confirmBible, confirmSceneScript, confirmVolumeOutline, confirmVolumePlan,
  editSceneScript, generateStoryboardDraft, getPipelineOverview, getTextConsentScope,
  grantTextConsent, importPipelineSource, listScriptDocuments, queueBible, queueVolumeOutline,
  queueVolumeScripts, reconcileFilmTask, requestVolumeHandoff, resumePipelineTask,
  startPipelineNewAttempt,
  type PipelineOverview, type PipelineTaskSummary, type StoryboardDraft, type StoryboardUnit,
  type WorkflowProfileLite,
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
  // The whole panel works on ONE selected volume: reviewing, drafting,
  // applying and delivering must never drift to a different volume than the
  // one the person is looking at.
  const [selectedVolumeId, setSelectedVolumeId] = useState<string>('');
  const [drafts, setDrafts] = useState<Record<string, StoryboardDraft>>({});
  const [handoff, setHandoff] = useState<{
    volumeId: string; packageId: string; reused: boolean;
    startable: boolean; blockers: Array<{ code: string; message?: string }>;
    episodes: number; shots: number;
  } | null>(null);

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
  // Switching project must not keep another project's selection or receipt.
  useEffect(() => {
    setSelectedVolumeId('');
    setDrafts({});
    setHandoff(null);
    setVolumePlanDraft(null);
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
  const currentVolumeId = selectedVolumeId || volumes[0]?.volume_id || '';
  const currentVolume = volumes.find((v) => v.volume_id === currentVolumeId);
  const currentDraft = drafts[currentVolumeId];

  async function draftAndRecord(volumeId: string) {
    setBusy(true);
    try {
      const result = await generateStoryboardDraft(projectId, volumeId);
      setDrafts((previous) => ({ ...previous, [volumeId]: result }));
      if (result.status === 'PROPOSED') {
        onMessage(`分镜草案已生成：${result.units.length} 个场次单元，请逐场应用`, 'success');
      } else {
        const codes = (result.rejections ?? []).map((row) => row.rejection_reason_code ?? row.code);
        onMessage(`分镜草案未全部完成：${result.units.filter((u) => u.status === 'PROPOSED').length}`
          + `/${result.units.length} 个单元已生成；${codes[0] ?? '请查看原因'}`, 'error');
      }
      await refresh();
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally { setBusy(false); }
  }

  async function applyUnit(unit: StoryboardUnit) {
    if (!unit.camera_artifact_id) return;
    await run(
      () => applyCameraPlan(projectId, unit.camera_artifact_id!, unit.continuity_artifact_id),
      (applied) => {
        const shotIds = (applied as { shot_ids?: string[] })?.shot_ids ?? [];
        setDrafts((previous) => {
          const draft = previous[currentVolumeId];
          if (!draft) return previous;
          return { ...previous, [currentVolumeId]: {
            ...draft,
            units: draft.units.map((row) => row.unit_id === unit.unit_id
              ? { ...row, applied: true, shot_ids: shotIds } : row),
          } };
        });
      });
  }

  async function reconcileAndRetry(volumeId: string, draft: StoryboardDraft) {
    setBusy(true);
    try {
      // Only a provably unsent call may be retried: the server decides that,
      // and records the conclusion before the request is attempted again.
      for (const rejection of draft.rejections ?? []) {
        if (!rejection.task_id || rejection.state_version === undefined) continue;
        if (rejection.external_outcome === 'UNKNOWN') continue;
        // A stale version is refused by the server; that refusal is the
        // answer, and the next refresh shows the task's real state.
        await reconcileFilmTask(projectId, rejection.task_id, rejection.state_version)
          .catch(() => undefined);
      }
      onMessage('已记录这些调用未发送，正在重新尝试', 'success');
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally { setBusy(false); }
    await draftAndRecord(volumeId);
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
            return (
              <div className="progress-row" key={task.task_id}>
                <span className="progress-name">{ROLE_LABELS[task.role] ?? task.role}</span>
                <progress aria-label={`${ROLE_LABELS[task.role] ?? task.role}进度`}
                  value={pct ?? undefined} max={100} />
                <span className="progress-detail">
                  {task.status === 'NEEDS_REVIEW'
                    ? `待核对：${task.error_code ?? task.stage_label}`
                    : `${task.stage_label}${pct !== null ? ` · ${task.progress_done}/${task.progress_total}` : ''}`}
                  {task.attempt > 1 ? ` · 第 ${task.attempt} 次尝试` : ''}
                </span>
                {consentBlocked && (
                  <button type="button" className="must-button" disabled={busy}
                    onClick={() => authorizeAndContinue(task)}>
                    授权本机文本发送并继续
                  </button>
                )}
                {actions.includes('resume') && !consentBlocked && (
                  <button type="button" className="secondary-button" disabled={busy}
                    onClick={() => run(() => resumePipelineTask(projectId, task.task_id, task.state_version))}>
                    修复后继续
                  </button>
                )}
                {actions.includes('new_attempt') && (
                  <button type="button" className="must-button" disabled={busy}
                    title="上一次可能已经发出去了。再次发送会多一次调用，上一次的结果会作为历史保留。"
                    onClick={() => run(() => startPipelineNewAttempt(
                      projectId, task.task_id, task.state_version))}>
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
            <span className="muted small">蓝图按当前计划的每一卷大纲合成；缺卷会如实标为草稿，不能作为正式发布依据。</span>
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
        {!currentVolume && <span className="muted">先完成第①步并确认分卷。</span>}
        {currentVolume && (
          <>
            <div className="row">
              <span>{currentVolume.title}</span>
              <button type="button" className="secondary-button"
                disabled={busy || !currentVolume.scripts_confirmed}
                title={currentVolume.scripts_confirmed ? '' : '先确认该卷全部场次剧本'}
                onClick={() => draftAndRecord(currentVolume.volume_id)}>
                {currentDraft ? '重新生成分镜草案' : '生成分镜草案'}
              </button>
              <span className="muted small">按场逐次调用五个工种；项目再长也不会被单次输入上限卡住。</span>
            </div>
            {currentDraft && (
              <ul className="volume-list">
                {currentDraft.units.map((unit) => (
                  <li key={unit.unit_id}>
                    <span>{unit.unit_id}</span>
                    <span className={unit.applied ? 'ok' : 'pending'}>
                      {unit.status === 'PROPOSED'
                        ? (unit.applied
                          ? `镜头已应用（${unit.shot_ids?.length ?? 0} 个）`
                          : '待应用镜头')
                        : `未完成：${unit.status ?? '未知'}`}
                    </span>
                    {unit.status === 'PROPOSED' && !unit.applied && (
                      <button type="button" className="must-button" disabled={busy}
                        onClick={() => applyUnit(unit)}>应用这一场的镜头</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {currentDraft && currentDraft.status !== 'PROPOSED' && (
              <div className="row">
                <span className="muted small">有单元没有完成。只有「确认未发送」后才会重新发起同样的调用。</span>
                <button type="button" className="secondary-button" disabled={busy}
                  onClick={() => reconcileAndRetry(currentVolume.volume_id, currentDraft)}>
                  核对未发送并重试
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
                    setHandoff({
                      volumeId: currentVolume.volume_id, packageId: body.package_id,
                      reused: body.reused, startable: body.readiness.startable,
                      blockers: body.readiness.blockers, episodes: body.episodes, shots: body.shots,
                    });
                    if (body.readiness.startable) {
                      onMessage(body.reused
                        ? '这一卷已经交付过，返回的是同一份交付回执'
                        : `卷交付完成：${body.episodes} 集 / ${body.shots} 个镜头，可到导演包页创建生产运行`,
                        'success');
                    } else {
                      onMessage(`卷交付已建立，但有 ${body.readiness.blockers.length} 项待解决`, 'error');
                    }
                  })}>
                交付本卷制作
              </button>
            </div>
            {handoff && handoff.volumeId === currentVolume.volume_id && (
              <div className="muted small">
                <div>
                  交付回执：导演包 {handoff.packageId.slice(0, 8)} · {handoff.episodes} 集 ·{' '}
                  {handoff.shots} 个镜头{handoff.reused ? '（复用既有交付，未重复建包）' : ''}
                </div>
                {handoff.blockers.length > 0 && (
                  <ul>
                    {handoff.blockers.map((blocker) => (
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
