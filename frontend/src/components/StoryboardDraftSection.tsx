import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  ERROR_CODE_LABELS, api, applyCameraPlan, confirmEpisodePlan, generateStoryboardDraft,
  generateStoryboardDraftFor, getPipelineOverview, listEpisodePlans, materializeEpisodePlan,
  reconcileFilmTask, resendUnknownFilmTask, timeoutSignal,
  type EpisodePlan, type PipelineOverview, type PipelineVolume, type StoryboardUnit,
} from '../api';
import { AgentProgress, type AgentRunProgress } from './AgentProgress';

/** A storyboard unit's state in words, never the raw enum. */
const STORYBOARD_STATUS_LABELS: Record<string, string> = {
  REJECTED: '这一场被拒绝',
  NEEDS_REVIEW: '这一场待人工核对',
  INCOMPLETE: '这一场还没完成',
  FAILED: '这一场失败了',
  RUNNING: '这一场生成中',
  WAITING_DEPENDENCY: '等前面的部分先完成',
};

/** The draft chain's five crew roles; their live tasks are the draft's progress. */
const CREW_ROLES = ['scriptwriter', 'director', 'cinematographer', 'continuity_supervisor', 'editor'];

/**
 * The per-scene storyboard workspace of one volume: draft the confirmed
 * scenes, watch the five crew roles live, apply each scene's shots, or
 * reconcile and redo exactly one scene.  Shared by the four-stage panel and
 * the director's ③ stage so the long-script path is never a dead end.
 */
export function StoryboardDraftSection({ projectId, volume, onMessage, onRefresh }: {
  projectId: string;
  volume: PipelineVolume;
  onMessage: (message: string, kind?: 'error' | 'success') => void;
  onRefresh: () => void | Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const actionInFlight = useRef(false);
  const [crewRuns, setCrewRuns] = useState<AgentRunProgress[]>([]);
  const crewActive = crewRuns.some((run) => CREW_ROLES.includes(run.role_id)
    && ['QUEUED', 'RUNNING'].includes(run.status));

  const loadCrew = useCallback(() => {
    api<AgentRunProgress[]>(`/projects/${projectId}/film-runs`, { signal: timeoutSignal(5000) })
      .then(setCrewRuns).catch(() => undefined);
  }, [projectId]);
  useEffect(() => { void loadCrew(); }, [loadCrew]);
  const crewPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if ((busy || crewActive) && crewPollRef.current === null) {
      crewPollRef.current = setInterval(() => { void loadCrew(); void onRefresh(); }, 5000);
    } else if (!busy && !crewActive && crewPollRef.current !== null) {
      clearInterval(crewPollRef.current);
      crewPollRef.current = null;
    }
    return () => {
      if (crewPollRef.current !== null) { clearInterval(crewPollRef.current); crewPollRef.current = null; }
    };
  }, [busy, crewActive, loadCrew, onRefresh]);

  async function run(action: () => Promise<unknown>, after?: (result: unknown) => void) {
    if (actionInFlight.current) {
      onMessage('上一操作仍在进行，请稍候。', 'error');
      return;
    }
    actionInFlight.current = true;
    setBusy(true);
    try {
      const result = await action();
      await onRefresh();
      after?.(result);
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally {
      actionInFlight.current = false;
      setBusy(false);
    }
  }

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
      await onRefresh();
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally { setBusy(false); }
  }

  async function applyUnit(unit: StoryboardUnit) {
    if (!unit.camera_artifact_id) {
      onMessage('这一场还没有摄影方案，请先完成摄影工种再应用。', 'error');
      return;
    }
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
        for (const entry of unknown) {
          const resent = await resendUnknownFilmTask(
            projectId, entry.task_id, entry.state_version);
          conclusions.push(resent.note ?? '已登记你的确认，将以一次新的模型请求重问这一场。');
        }
        mayContinue = true;
      }
      await onRefresh();
      if (!mayContinue) {
        onMessage(conclusions.join('；'), 'error');
        return;
      }
      onMessage(conclusions.join('；') + ' 正在只重做这一场', 'success');
      setBusy(false);
      await draftAndRecord(volume.volume_id, [unit.section_id]);
    } catch (error) {
      onMessage((error as Error).message, 'error');
    } finally { setBusy(false); }
  }

  const draftUnits = volume.storyboard?.units ?? [];
  const draftStatus = volume.storyboard?.status ?? null;
  return <>
    <div className="row">
      <span>{volume.title}</span>
      <button type="button" className="btn-secondary"
        disabled={busy || crewActive || volume.scripts_confirmed === 0}
        title={volume.scripts_confirmed === 0
          ? '先确认至少一场剧本；草案只覆盖已确认的场次'
          : `草案只覆盖已确认的 ${volume.scripts_confirmed}/${volume.scripts_total} 场`
            + (volume.scripts_confirmed < volume.scripts_total
              ? '；其余场次确认后再生成一次即可补齐' : '')}
        onClick={() => draftAndRecord(volume.volume_id)}>
        {draftUnits.length > 0
          ? '重新生成本卷分镜草案'
          : volume.scripts_confirmed < volume.scripts_total
            ? '生成已确认场次的分镜草案'
            : '生成分镜草案'}
      </button>
      <span className="muted small">按场逐次调用五个工种；项目再长也不会被单次输入上限卡住。</span>
    </div>
    {crewActive && (
      <div className="notice" role="status" aria-label="分镜草案后台进度">
        <div>
          <b>分镜草案后台生成中：{volume.title}</b>
          <span className="muted small">
            整卷已完成 {crewRuns.filter((run) => run.role_id === 'editor' && run.status === 'SUCCEEDED').length}
            /{volume.scripts_total} 场；下面是当前一场五个工种的实时状态。
          </span>
          <AgentProgress runs={(() => {
            const crewOnly = crewRuns.filter((run) => CREW_ROLES.includes(run.role_id));
            const sceneStart = crewOnly.filter((run) => run.role_id === 'scriptwriter')
              .map((run) => run.created_at).sort().at(-1) ?? '';
            return crewOnly.filter((run) => run.created_at >= sceneStart);
          })()} />
          <span className="muted small">
            整卷链完成后，下方会列出各场的「应用这一场的镜头」；刷新页面不会中断后台链。
          </span>
        </div>
      </div>
    )}
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
            {unit.status === 'PROPOSED' && !unit.applied && !unit.camera_artifact_id && (
              <div className="blocker-item">
                <AlertTriangle size={13} />
                <span className="blocker-reason">这一场还没有摄影方案，先完成摄影工种再应用。</span>
              </div>
            )}
            {unit.status === 'PROPOSED' && !unit.applied && (
              <button type="button" className="btn-secondary" disabled={busy || !unit.camera_artifact_id}
                title={!unit.camera_artifact_id ? '这一场还没有摄影方案，先完成摄影工种。'
                  : (unit.missing_characters ?? []).length > 0
                  ? '这一场要用的主素材还没备齐：' + (unit.missing_characters ?? []).join('、')
                  + '。可以先落镜，但出片前要补齐；缺人物参考时 H3 会拒绝提交。'
                  : ''}
                onClick={() => applyUnit(unit)}>应用这一场的镜头</button>
            )}
            {unit.status === 'PROPOSED' && !unit.applied && (
              <button type="button" className="btn-text" disabled={busy || crewActive}
                title="冻结风格或主素材在草案之后变化时，应用会被拒绝（INPUT_CONTEXT_CHANGED）。按当前上下文只重跑这一场的五个工种，会再次调用模型；其他场不受影响。"
                onClick={() => draftAndRecord(volume.volume_id, [unit.section_id])}>重做这一场</button>
            )}
            {unit.status !== 'PROPOSED' && (
              <button type="button" className="btn-secondary" disabled={busy}
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
  </>;
}

/**
 * The director ③ stage's long-script entry: the four-stage per-scene
 * storyboard workspace plus the closing step that binds the confirmed
 * episode plan to the shots the scenes really produced.  Without this the
 * single-document panel dead-ends on scripts over 20000 characters.
 */
export function DirectorStoryboardStage({ projectId, onMessage, onJourneyRefresh }: {
  projectId: string;
  onMessage: (message: string, kind?: 'error' | 'success') => void;
  onJourneyRefresh: () => void | Promise<unknown>;
}) {
  const [overview, setOverview] = useState<PipelineOverview | null>(null);
  const [plans, setPlans] = useState<EpisodePlan[]>([]);
  const [volumeId, setVolumeId] = useState('');
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const [data, rows] = await Promise.all([
      getPipelineOverview(projectId), listEpisodePlans(projectId),
    ]);
    setOverview(data);
    setPlans(rows ?? []);
  }, [projectId]);
  useEffect(() => { void load().catch(() => undefined); }, [load]);

  const volumes = overview?.volumes ?? [];
  const current = volumes.find((v) => v.volume_id === volumeId) ?? volumes[0];
  if (!overview || !current || current.scripts_total === 0) return null;

  const latestPlan = [...plans].sort((a, b) => b.version - a.version)[0] ?? null;
  const sectionsPlan = latestPlan && latestPlan.payload.mode === 'sections' ? latestPlan : null;
  const planSections = sectionsPlan
    ? sectionsPlan.payload.episodes.flatMap((episode) => episode.section_ids ?? []) : [];
  const allUnits = volumes.flatMap((v) => v.storyboard?.units ?? []);
  const allApplied = planSections.length > 0 && planSections.every((sectionId) =>
    allUnits.some((unit) => unit.section_id === sectionId && unit.applied
      && (unit.shot_ids?.length ?? 0) > 0));
  const materialized = !!sectionsPlan
    && sectionsPlan.payload.episodes.every((episode) => (episode.shot_ids?.length ?? 0) > 0);

  async function closePlan() {
    if (!sectionsPlan || busy) return;
    setBusy(true);
    try {
      if (!materialized) {
        await materializeEpisodePlan(projectId, sectionsPlan.version);
        onMessage('分集已落到各场次实际拆出的镜头，请核对后确认这一版', 'success');
      } else {
        await confirmEpisodePlan(projectId, sectionsPlan.version, sectionsPlan.sha256);
        onMessage('分集方案已确认，③ 镜头拆解完成', 'success');
      }
      await load();
      await onJourneyRefresh();
    } catch (error) {
      onMessage((error as Error).message, 'error');
      await load().catch(() => undefined);
    } finally { setBusy(false); }
  }

  return <section className="card section-card" id="four-stage-storyboard">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">③ 镜头拆解 · 按场拆镜（长剧本 / 已分集作品）</div>
        <h2>逐场生成分镜，再落到镜头</h2>
        <p>已确认场次的剧本按场调用五个工种，不受单次 20000 字限制；每场应用后，分集方案才能落到真实镜头。刷新页面不中断后台链。</p>
      </div>
    </div>
    {volumes.length > 1 && (
      <div className="row">
        <label htmlFor="director-storyboard-volume">当前处理的卷：</label>
        <select id="director-storyboard-volume" value={current.volume_id}
          onChange={(event) => setVolumeId(event.target.value)}>
          {volumes.map((v) => (
            <option key={v.volume_id} value={v.volume_id}>{v.title}</option>
          ))}
        </select>
      </div>
    )}
    <StoryboardDraftSection projectId={projectId} volume={current}
      onMessage={onMessage} onRefresh={load} />
    {sectionsPlan && !materialized && (
      <div className="blocker-item is-action">
        <div className="blocker-reason">
          <b>{allApplied ? '必做：把分集落到镜头' : '分集还没有全部拆成镜头'}</b>
          <small>{allApplied
            ? '所有场次都已应用分镜。点击后按应用回执生成分集新版本，再确认。'
            : '先把上面每一场的镜头应用完；缺哪一场，这里会如实列出。'}</small>
        </div>
        <button type="button" className="btn-primary" disabled={busy || !allApplied}
          onClick={() => void closePlan()}>把分集落到镜头</button>
      </div>
    )}
    {sectionsPlan && materialized && sectionsPlan.status !== 'CONFIRMED' && (
      <div className="blocker-item is-action">
        <div className="blocker-reason">
          <b>必做：确认这一版分集</b>
          <small>分集已落到 {sectionsPlan.payload.episodes.reduce((n, e) => n + (e.shot_ids?.length ?? 0), 0)} 个真实镜头；确认后③完成，素材按定稿分镜准备。</small>
        </div>
        <button type="button" className="btn-primary" disabled={busy}
          onClick={() => void closePlan()}>确认这一版分集</button>
      </div>
    )}
    {sectionsPlan && materialized && sectionsPlan.status === 'CONFIRMED' && (
      <p className="muted small">分集方案第 {sectionsPlan.version} 版已确认并落到镜头；③ 镜头拆解完成。</p>
    )}
  </section>;
}