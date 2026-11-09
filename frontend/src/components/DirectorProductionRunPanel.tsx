import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, Check, Clock3, Film, Loader2, LockKeyhole, Pause, Play, RefreshCw,
  ShieldAlert, Sparkles, Square, Video,
} from 'lucide-react';
import {
  API, ApiError, confirmDirectorPackage, confirmEpisodePlan, controlProductionRun, createDirectorPackage,
  createEpisodePlan, createProductionRun, getJourney, getPackageReadiness, getProductionRun, getRunEvents,
  listDirectorPackages, listEpisodePlans, materializeEpisodePlan,
  type DirectorPackage, type EpisodePlan, type Journey, type PackageReadiness, type ProductionRun,
  type RunEvent, type WorkflowProfile,
} from '../api';
import { rememberProductionProfile, storedProductionProfile } from '../productionProfile';
import { EpisodePostEditor } from './EpisodePostEditor';
import { fetchProfileGates, gateReason, type ProfileGate } from './variantGate';

type Mode = 'plan' | 'package' | 'run' | 'post';

const RUN_ACTIVE = ['QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'RECONCILING'];

const STATUS_TEXT: Record<string, string> = {
  DRAFT: '草案',
  READY_FOR_CONFIRMATION: '待确认',
  CONFIRMED: '已确认',
  QUEUED: '已排队',
  RUNNING: '进行中',
  PAUSE_REQUESTED: '正在收尾当前片段',
  PAUSED: '已暂停',
  RECONCILING: '正在核对',
  NEEDS_REVIEW: '待人工处理',
  CANCEL_REQUESTED: '正在取消',
  CANCELLED: '已取消',
  FAILED: '失败',
  COMPLETED: '已全部交付',
  PLANNED: '待安排',
  PRODUCING: '拍摄中',
  DELIVERING: '剪辑中',
  DELIVERED: '已交付',
  BLOCKED: '有阻塞',
  PASSED: '已完成',
  SUPERSEDED: '已被新版本取代',
};

function statusText(status: string | null | undefined): string {
  if (!status) return '—';
  return STATUS_TEXT[status] ?? status;
}

function tone(status: string): string {
  if (['COMPLETED', 'DELIVERED', 'CONFIRMED', 'PASSED'].includes(status)) return 'ok';
  if (['FAILED', 'BLOCKED', 'NEEDS_REVIEW', 'CANCELLED'].includes(status)) return 'bad';
  if (['RUNNING', 'PRODUCING', 'DELIVERING', 'QUEUED', 'RECONCILING', 'PAUSE_REQUESTED'].includes(status)) return 'busy';
  return 'idle';
}

function seconds(value: number | null | undefined): string {
  if (!value || value <= 0) return '—';
  const total = Math.round(value);
  const minute = Math.floor(total / 60);
  const second = total % 60;
  return minute ? `${minute} 分 ${second} 秒` : `${second} 秒`;
}

/** Time-stamped facts come from the server; a missing value never renders a negative clock. */
function since(iso: string | null | undefined): string {
  if (!iso) return '—';
  const value = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  if (Number.isNaN(value)) return '—';
  const delta = Math.max(0, Math.round((Date.now() - value) / 1000));
  if (delta < 60) return `${delta} 秒前`;
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  return `${Math.floor(delta / 3600)} 小时前`;
}

type Props = {
  projectId: string;
  mode: Mode;
  /** Open the panel on this exact package instead of the newest one. */
  packageId?: string | null;
  demo?: boolean;
  workflowProfiles: WorkflowProfile[];
  onMessage: (text: string, kind: 'success' | 'error') => void;
  onRefresh?: () => void;
};

export function DirectorProductionRunPanel({ projectId, mode, packageId, demo, workflowProfiles, onMessage, onRefresh }: Props) {
  const [journey, setJourney] = useState<Journey | null>(null);
  const [plans, setPlans] = useState<EpisodePlan[]>([]);
  const [packages, setPackages] = useState<DirectorPackage[]>([]);
  const [readiness, setReadiness] = useState<PackageReadiness | null>(null);
  const [run, setRun] = useState<ProductionRun | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [targetSeconds, setTargetSeconds] = useState(60);
  const [toleranceSeconds, setToleranceSeconds] = useState(15);
  const [profileId, setProfileId] = useState('');
  /** 每个方案在本机能不能用于生产：服务端判定，浏览器只显示结论和原因。 */
  const [gates, setGates] = useState<Record<string, ProfileGate>>({});
  const ref2vaProfiles = useMemo(
    () => workflowProfiles.filter((profile) => profile.mode.toLowerCase() === 'ref2va'),
    [workflowProfiles],
  );
  const profileKey = ref2vaProfiles.map((profile) => profile.id).join(',');
  useEffect(() => {
    if (!projectId || !profileKey) { setGates({}); return; }
    let cancelled = false;
    fetchProfileGates(projectId, ref2vaProfiles)
      .then((rows) => { if (!cancelled) setGates(rows); })
      .catch(() => { if (!cancelled) setGates({}); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, profileKey]);
  const [selectedPackageId, setSelectedPackageId] = useState<string>(packageId ?? '');
  // A deep link names one exact package; the selection follows it.
  useEffect(() => { setSelectedPackageId(packageId ?? ''); }, [packageId]);
  const [episodePage, setEpisodePage] = useState(0);
  /** 进展日志只渲染最近几条：记录可以一直增长，页面高度不能跟着长。 */
  const [eventLimit, setEventLimit] = useState(8);
  const mounted = useRef(true);

  // 剧本统筹先分集：大纲确认后即可按场次分集，不需要先有镜头；
  // 已手工建好的镜头列表仍可用兼容入口按镜头分组。
  const shotCount = Number(
    (journey?.stages.find((stage) => stage.id === 'storyboard')?.facts?.['shot_count'] as number | undefined)
    ?? 0,
  );
  const outlineConfirmed = Boolean(
    journey?.stages.find((stage) => stage.id === 'script')?.facts?.['outline_confirmed'],
  );
  const [planEntry, setPlanEntry] = useState<'sections' | 'shots'>('sections');
  const planMode: 'sections' | 'shots' = outlineConfirmed
    ? (shotCount > 0 ? planEntry : 'sections')
    : 'shots';
  const canPlan = planMode === 'sections' ? outlineConfirmed : shotCount > 0;
  const latestPlan = plans[0] ?? null;
  const confirmedPlan = plans.find((row) => row.status === 'CONFIRMED') ?? null;
  const latestPlanIsSections = latestPlan?.payload.mode === 'sections';
  const latestPlanMaterialized = !!latestPlan && latestPlan.payload.episodes.every((row) => row.shot_ids.length > 0);
  // The package the person is looking at: the one they asked for, or the
  // newest one.  The delivery receipt of a volume names it exactly.
  const latestPackage = (selectedPackageId
    ? packages.find((row) => row.id === selectedPackageId)
    : null) ?? packages[0] ?? null;
  const activeRun = run;

  const load = useCallback(async () => {
    try {
      const [journeyRow, planRows, packageRows] = await Promise.all([
        // The panel projects the exact package it is showing: progress,
        // playback and delivery all read the same scope.
        getJourney(projectId, { packageId: selectedPackageId || undefined }),
        listEpisodePlans(projectId),
        listDirectorPackages(projectId),
      ]);
      if (!mounted.current) return;
      setJourney(journeyRow);
      setPlans(planRows);
      setPackages(packageRows);
      // The run comes from the journey's resolved scope, never from "the
      // newest run of this project".  A package that was never started shows
      // its待开拍 state instead of borrowing another package's controls (J11).
      const scopedRunId = journeyRow.scope?.run_id ?? journeyRow.run?.id ?? null;
      if (scopedRunId) {
        const detail = await getProductionRun(scopedRunId);
        if (!mounted.current) return;
        setRun(detail);
        setEvents(await getRunEvents(scopedRunId));
      } else {
        setRun(null);
        setEvents([]);
      }
      const scopedPackageId = journeyRow.scope?.package_id ?? selectedPackageId;
      const packageRow = (scopedPackageId
        ? packageRows.find((row) => row.id === scopedPackageId)
        : null) ?? packageRows[0] ?? null;
      if (packageRow) {
        setReadiness(await getPackageReadiness(packageRow.id));
        if (!profileId) {
          const remembered = storedProductionProfile(projectId);
          const candidate = remembered || packageRow.binding?.workflow_profile_id || '';
          if (candidate) setProfileId(candidate);
        }
      } else {
        setReadiness(null);
      }
      setError('');
    } catch (exc) {
      if (mounted.current) setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [projectId, profileId, selectedPackageId]);

  useEffect(() => {
    mounted.current = true;
    load().catch(() => undefined);
    return () => { mounted.current = false; };
  }, [load]);

  // One aggregate poll for the whole panel, and only while something is moving.
  const moving = !!activeRun && RUN_ACTIVE.includes(activeRun.status);
  useEffect(() => {
    if (!moving) return;
    const timer = window.setInterval(() => { load().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [moving, load]);

  async function act(name: string, action: () => Promise<unknown>, success?: string) {
    setBusy(name);
    setError('');
    try {
      await action();
      if (success) onMessage(success, 'success');
      await load();
      onRefresh?.();
    } catch (exc) {
      const text = exc instanceof ApiError ? exc.message : exc instanceof Error ? exc.message : String(exc);
      setError(text);
      onMessage(text, 'error');
    } finally {
      if (mounted.current) setBusy('');
    }
  }

  const derivePlan = () => act('plan', () => createEpisodePlan(projectId, targetSeconds, toleranceSeconds, planMode),
    planMode === 'sections' ? '已按剧本场次生成分集草案，请核对后确认' : '已按镜头生成分集草案，请核对后确认');
  const materializePlan = () => {
    if (!latestPlan) return;
    return act('plan-materialize', () => materializeEpisodePlan(projectId, latestPlan.version),
      '分集已落到各场次实际拆出的镜头，请核对后确认这一版');
  };
  const confirmPlan = () => {
    if (!latestPlan) return;
    return act('plan-confirm', () => confirmEpisodePlan(projectId, latestPlan.version, latestPlan.sha256),
      '分集方案已确认');
  };
  const buildPackage = () => {
    if (!profileId) {
      onMessage('先在基础配置里登记本机生成方案，再生成导演包', 'error');
      return;
    }
    // 候选方案（W6A8 等）在这台电脑上还不能跑时，导演包不会生成；
    // 也不会换成 INT8 基线继续——身份必须是同一个。
    const gate = gates[profileId];
    if (gate && !gate.selectable) {
      onMessage(`这个生成方案现在不能用于生产：${gateReason(gate)}`, 'error');
      return;
    }
    return act('package', () => createDirectorPackage(projectId, {
      workflow_profile_id: profileId,
      generation_mode: profileMode,
      policy: { batch_shot_limit: 6, max_repairs_per_shot: 2 },
    }), '导演包已生成，请查看就绪检查后确认');
  };
  const confirmPackage = () => {
    if (!latestPackage) return;
    return act('package-confirm', () => confirmDirectorPackage(latestPackage.id, latestPackage.manifest_sha256),
      '导演包已确认，可以启动整部作品生产');
  };
  const startRun = () => {
    if (!latestPackage) return;
    return act('run', () => createProductionRun(projectId, { package_id: latestPackage.id }),
      '整部作品已启动：后台会持续推进，无需你逐段点击');
  };
  const control = (action: 'pause' | 'resume' | 'cancel' | 'reconcile') => {
    if (!activeRun) return;
    const keyword = { pause: '已暂停：当前片段会先安全落盘',
      resume: '已恢复生产', cancel: '已取消：已产出数据全部保留',
      reconcile: '已按真实产物核对运行状态' }[action];
    return act(action, () => controlProductionRun(activeRun.id, action), keyword);
  };

  const profileMode = useMemo(() => {
    const profile = workflowProfiles.find((row) => row.id === profileId);
    return profile?.mode ?? null;
  }, [profileId, workflowProfiles]);

  const selectedGate = profileId ? gates[profileId] : undefined;

  // 就绪检查是即时事实（会随风格/绑定/文件变化）；七阶段摘要只在没有包时兜底。
  const shownBlockers = (readiness?.blockers
    ?? journey?.stages.find((stage) => stage.id === 'package')?.blockers
    ?? []).filter((blocker) => blocker.code !== 'PACKAGE_NOT_CONFIRMED');

  if (error && !journey) {
    return <section className="card section-card run-panel">
      <div className="blocked-callout"><ShieldAlert size={14} />{error}</div>
    </section>;
  }

  return <div className="run-panel">
    {(mode === 'package' || mode === 'plan') && <>
      <section className="card section-card run-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">① 分集方案</div>
            <h2>每集大约多长，由你定</h2>
            <p>先按已确认剧本的场次分集，再逐集拆镜头：分集只在场次之间切，不会把一场戏或一句对白拦腰截断；超长的一集会如实标注原因。</p>
          </div>
          <div className="run-head-actions">
            {outlineConfirmed && shotCount > 0 && <label className="run-inline-field">分集依据
              <select value={planEntry} onChange={(event) => setPlanEntry(event.target.value as 'sections' | 'shots')}>
                <option value="sections">按剧本场次（推荐）</option>
                <option value="shots">按已有镜头分组（兼容）</option>
              </select>
            </label>}
            <label className="run-inline-field">每集目标
              <input type="number" min={8} max={3600} value={targetSeconds}
                onChange={(event) => setTargetSeconds(Number(event.target.value) || 60)} />
              <span>秒</span>
            </label>
            <label className="run-inline-field">允许浮动
              <input type="number" min={0} max={900} value={toleranceSeconds}
                onChange={(event) => setToleranceSeconds(Number(event.target.value) || 0)} />
              <span>秒</span>
            </label>
            <button className="secondary-button" disabled={busy !== '' || !canPlan}
              title={canPlan ? (planMode === 'sections' ? '按已确认剧本的场次分集，每场恰好一次' : '按镜头顺序覆盖全部分镜')
                : '先在「创意与剧本」确认剧本大纲'}
              onClick={derivePlan}>
              <Sparkles size={13} /> 生成分集草案
            </button>
          </div>
        </div>

        {!canPlan && !latestPlan && <p className="empty-inline">
          还没有已确认的剧本大纲。先在「创意与剧本」输入想法或导入剧本，并确认大纲，再回来按场次分集。
        </p>}

        {latestPlan ? <div className="run-plan-summary">
          <div>
            <b>第 {latestPlan.version} 版 · {latestPlan.payload.episodes.length} 集
              {latestPlanIsSections ? ' · 按剧本场次' : ' · 按镜头'}</b>
            <span>{statusText(latestPlan.status)} · 目标 {latestPlan.payload.target_seconds} 秒
              ± {latestPlan.payload.tolerance_seconds} 秒</span>
          </div>
          {latestPlan.status !== 'CONFIRMED' && <button className="must-button" disabled={busy !== ''} onClick={confirmPlan}>
            <Check size={13} /> 确认分集方案
          </button>}
          {latestPlanIsSections && !latestPlanMaterialized && <button className="secondary-button" disabled={busy !== ''}
            title="各场次在「镜头拆解」应用分镜后，把每集落到它们真实拆出的镜头"
            onClick={materializePlan}>
            把分集落到镜头
          </button>}
          {confirmedPlan && confirmedPlan.version !== latestPlan.version && <span className="run-hint">
            当前确认的是第 {confirmedPlan.version} 版；这一版确认后会取代它。
          </span>}
          <details className="tech-details">
            <summary>技术详情：分集校验指纹</summary>
            <div className="tech-details-body">
              <span>方案编号 <code>{latestPlan.id}</code></span>
              <span>清单指纹 <code>{latestPlan.sha256}</code></span>
              <span>来源 <code>{latestPlan.source}</code></span>
            </div>
          </details>
        </div> : canPlan && <p className="empty-inline">还没有分集方案。先生成草案——它会覆盖全部场次（或全部镜头），不重不漏。</p>}

        {latestPlan && <ol className="run-episode-plan">
          {latestPlan.payload.episodes.map((episode) => <li key={episode.episode_id}>
            <span className="run-ep-no">{episode.order}</span>
            <div>
              <b>{episode.title}</b>
              <span>{latestPlanIsSections ? `${episode.section_ids?.length ?? 0} 场 · ` : ''}
                {episode.shot_ids.length ? `${episode.shot_ids.length} 镜` : '待拆镜头'} · 计划 {seconds(episode.planned_seconds)}</span>
              {episode.boundary_note && <em>{episode.boundary_note}</em>}
            </div>
          </li>)}
        </ol>}
      </section>
    </>}

    {/* 第 5 阶段的导演包只出现在导演包页面；第 2 阶段只决定怎么分集。 */}
    {mode === 'package' && <>
      <section className="card section-card run-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">② 导演包</div>
            <h2>把这次要拍的东西固定下来</h2>
            <p>导演包会冻结剧本版本、分集、镜头、素材版本、风格、生成方案和生产政策。确认之后才能开拍。</p>
          </div>
          <button className="secondary-button" disabled={busy !== ''} onClick={load}>
            <RefreshCw size={13} /> 重新检查
          </button>
        </div>

        <div className="run-binding-row">
          {packages.length > 1 && <label>选择导演包
            <select value={latestPackage?.id ?? ''}
              onChange={(event) => setSelectedPackageId(event.target.value)}>
              {packages.map((row) => <option key={row.id} value={row.id}>
                第 {row.revision} 版 · {statusText(row.status)} · {row.episode_count} 集 · {row.shot_count} 镜
              </option>)}
            </select>
          </label>}
          <label>本机生成方案
            <select value={profileId} onChange={(event) => {
              setProfileId(event.target.value);
              rememberProductionProfile(projectId, event.target.value);
            }}>
              <option value="">请选择…</option>
              {workflowProfiles.map((profile) => {
                const gate = gates[profile.id];
                const blocked = gate ? !gate.selectable : false;
                return <option key={profile.id} value={profile.id} disabled={blocked}>
                  {profile.name} · {profile.mode}{blocked ? '（本机暂不可用）' : ''}
                </option>;
              })}
            </select>
          </label>
          {selectedGate && !selectedGate.selectable && <span className="run-hint">
            <AlertTriangle size={13} /> 「{selectedGate.profile_name ?? profileId}」现在不能用于生产：{gateReason(selectedGate)}
          </span>}
          <button className="primary-button" disabled={busy !== '' || !confirmedPlan} onClick={buildPackage}>
            <LockKeyhole size={13} /> 生成导演包
          </button>
          {!confirmedPlan && <span className="run-hint">先确认分集方案，才能生成导演包。</span>}
        </div>

        {latestPackage && <div className="run-package">
          <div className="run-package-head">
            <div>
              <b>第 {latestPackage.revision} 版 · {latestPackage.episode_count} 集 · {latestPackage.shot_count} 镜</b>
              <span>{statusText(latestPackage.status)}{latestPackage.stale ? ' · 已失效，需要重新生成' : ''}</span>
            </div>
            {latestPackage.status !== 'CONFIRMED' && <button className="must-button"
              disabled={busy !== '' || !readiness?.ready} onClick={confirmPackage}>
              <Check size={13} /> 确认导演包
            </button>}
          </div>

          <dl className="run-facts">
            <div><dt>剧本</dt><dd>{latestPackage.script?.outline_confirmed ? '大纲已确认' : '无导入剧本（按分镜执行）'}</dd></div>
            <div><dt>风格</dt><dd>{latestPackage.style
              ? `第 ${latestPackage.style.version} 版 · 已冻结` : '未冻结'}</dd></div>
            <div><dt>生成方案</dt><dd>{latestPackage.binding
              ? latestPackage.binding.mode : '未绑定'}</dd></div>
            <div><dt>自动返工</dt><dd>{String(latestPackage.policy?.max_repairs_per_shot ?? '—')} 次/镜（有界）</dd></div>
          </dl>

          {readiness && <div className={`run-readiness ${readiness.startable ? 'ok' : readiness.ready ? 'warn' : 'bad'}`}>
            {readiness.startable
              ? <><Check size={13} /> 本机检查通过，可以启动整部作品生产</>
              : <><AlertTriangle size={13} /> {readiness.ready ? '内容已满足，等待你确认导演包' : '还不能开拍'}</>}
          </div>}

          {shownBlockers.length > 0 && <ul className="run-blockers">
            {shownBlockers.slice(0, 8).map((blocker, index) => <li key={`${blocker.code}-${index}`}
              title={blocker.code}>
              <ShieldAlert size={12} /><span>{blocker.message}</span>
            </li>)}
          </ul>}

          <details className="tech-details">
            <summary>技术详情：版本、范围与校验指纹</summary>
            <div className="tech-details-body">
              <span>导演包编号 <code>{latestPackage.id}</code></span>
              <span>范围摘要 <code>{latestPackage.scope_digest}</code></span>
              <span>清单指纹 <code>{latestPackage.manifest_sha256}</code></span>
              <span>配置指纹 <code>{readiness?.config_fingerprint ?? '尚未检查'}</code></span>
              <span>策略指纹 <code>{latestPackage.policy_hash}</code></span>
            </div>
          </details>

          {latestPackage.status === 'CONFIRMED' && readiness?.startable
            && (!run || !RUN_ACTIVE.includes(run.status)) && <button className="must-button wide"
              disabled={busy !== ''} onClick={startRun}>
              <Play size={14} /> 启动整部作品生产
              {latestPackage.episode_count > 1 ? `（${latestPackage.episode_count} 集一次跑完）` : ''}
            </button>}
          {run && <p className="run-hint">这个导演包已经启动过一次；重复点击会返回同一次运行，不会重复开拍。</p>}
        </div>}
      </section>
    </>}

    {(mode === 'run' || mode === 'post') && <section className="card section-card run-card">
      <div className="section-heading">
        <div>
          <div className="section-kicker">{mode === 'post' ? '逐集成片' : '整部作品运行'}</div>
          <h2>{mode === 'post' ? '每一集都能单独播放' : '后台会一直推进，关了浏览器也一样'}</h2>
          <p>{mode === 'post'
            ? '这里只展示真实装配完成的分集；缺片段的一集不会出现在成片里。'
            : '运行状态来自服务端记录：心跳只代表后台还活着，最近进展才代表真的拍出了东西。'}</p>
        </div>
        <button className="secondary-button" disabled={busy !== ''} onClick={load}>
          <RefreshCw size={13} /> 刷新
        </button>
      </div>

      {!activeRun ? <p className="empty-inline">还没有整部作品的运行记录。先在「导演包」阶段确认并启动。</p> : <>
        <div className="run-summary-row">
          <span className={`run-badge ${tone(activeRun.status)}`}>{statusText(activeRun.status)}</span>
          <div className="run-summary-facts">
            <span><b>{activeRun.progress.delivered_episode_count}</b> / {activeRun.progress.required_episode_count} 集已交付</span>
            <span><b>{activeRun.progress.passed_shot_count}</b> / {activeRun.scope.shot_count} 镜已通过</span>
            <span>批次 {activeRun.progress.passed_batch_count} / {activeRun.progress.batch_count}</span>
            <span><Clock3 size={11} /> 最近进展 {since(activeRun.last_progress_at)}</span>
            <span>成片 {seconds(activeRun.progress.delivered_seconds)}</span>
          </div>
          <div className="run-controls">
            {['QUEUED', 'RUNNING'].includes(activeRun.status) && <button className="subtle-button"
              disabled={busy !== ''} onClick={() => control('pause')}><Pause size={12} /> 暂停</button>}
            {activeRun.status === 'PAUSED' && <button className="subtle-button"
              disabled={busy !== ''} onClick={() => control('resume')}><Play size={12} /> 继续</button>}
            {activeRun.status === 'NEEDS_REVIEW' && <>
              <button className="secondary-button" disabled={busy !== ''}
                onClick={() => control('reconcile')}><RefreshCw size={12} /> 核对真实结果</button>
              <button className="subtle-button" disabled={busy !== ''}
                title="只恢复服务端能够证明尚未提交到 H3 的批次；其他阻塞保持待处理"
                onClick={() => control('resume')}><Play size={12} /> 继续安全可恢复批次</button>
            </>}
            {!['COMPLETED', 'CANCELLED'].includes(activeRun.status) && <button className="subtle-button danger"
              disabled={busy !== ''} onClick={() => control('cancel')}><Square size={12} /> 取消</button>}
          </div>
        </div>

        {demo && activeRun && ['QUEUED', 'RUNNING'].includes(activeRun.status) && <div className="blocked-callout">
          <AlertTriangle size={13} /> 当前是演示体验：后台不会真的开拍，这次运行会一直停在「已排队」。
          点顶部「启用真实制作」后，常驻 Worker 才会推进它。
        </div>}

        {activeRun.control_reason && <div className="blocked-callout">
          <AlertTriangle size={13} /> 当前原因：{activeRun.control_reason}
          {activeRun.status === 'PAUSED' && '（不会自动恢复，需要你点继续）'}
        </div>}

        {activeRun.status === 'NEEDS_REVIEW' && <p className="run-hint">
          续跑会逐项验证提交记录、冻结方案和素材身份；无法证明安全的批次继续保留在待处理状态。
        </p>}

        <ol className="run-episodes">
          {(activeRun.episodes ?? []).slice(episodePage * 8, episodePage * 8 + 8).map((episode) => <li key={episode.id}>
            <div className="run-ep-head">
              <span className="run-ep-no">{episode.position}</span>
              <div>
                <b>{episode.title || episode.episode_id}</b>
                <span>{episode.shot_count} 镜 · 通过 {episode.passed_shot_count}
                  {' '}· 计划 {seconds(episode.planned_seconds)}
                  {episode.actual_seconds ? ` · 成片 ${seconds(episode.actual_seconds)}` : ''}</span>
              </div>
              <span className={`run-badge ${tone(episode.status)}`}>{statusText(episode.status)}</span>
            </div>
            {episode.batches.length > 0 && <div className="run-batches">
              {episode.batches.map((batch) => <span key={batch.batch_key}
                className={`run-batch ${tone(batch.status)}`} title={batch.failure_code ?? batch.series_id ?? ''}>
                {batch.batch_key.replace(`${episode.episode_id}-`, '')} · {statusText(batch.status)}
              </span>)}
            </div>}
            {episode.blocker_code && <div className="run-ep-blocker">
              <ShieldAlert size={11} /> 阻塞：{episode.blocker_code}
            </div>}
            {episode.delivered_artifact_id && <div className="run-ep-media">
              <video src={`${API}/deliveries/${episode.delivered_artifact_id}/media`} controls preload="none" />
              <a href={`${API}/deliveries/${episode.delivered_artifact_id}/media`} download>下载这一集 MP4</a>
            </div>}
          </li>)}
        </ol>
        {(activeRun.episodes?.length ?? 0) > 8 && <div className="run-pager">
          <button className="subtle-button" disabled={episodePage === 0}
            onClick={() => setEpisodePage((page) => Math.max(0, page - 1))}>上一页</button>
          <span>第 {episodePage + 1} / {Math.ceil((activeRun.episodes?.length ?? 0) / 8)} 页</span>
          <button className="subtle-button"
            disabled={(episodePage + 1) * 8 >= (activeRun.episodes?.length ?? 0)}
            onClick={() => setEpisodePage((page) => page + 1)}>下一页</button>
        </div>}

        <details className="advanced-fold run-events">
          <summary>最近进展（{events.length}）</summary>
          <ul>
            {events.slice().reverse().slice(0, eventLimit).map((event) => <li key={event.id}>
              <span>{event.message}</span><em>{since(event.created_at)}</em>
            </li>)}
          </ul>
          {events.length > eventLimit && <button type="button" className="list-more"
            onClick={() => setEventLimit((limit) => limit + 20)}>
            显示更多（还有 {events.length - eventLimit} 条）
          </button>}
        </details>
      </>}
    </section>}

    {(mode === 'run' || mode === 'post') && activeRun && (activeRun.episodes ?? []).some((row) => row.delivered_artifact_id)
      && <section className="card section-card run-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">待审清单</div>
            <h2>技术完成 ≠ 你已经认可</h2>
            <p>自动交付只代表文件可播放、技术检查通过。艺术效果需要你自己看过之后决定。</p>
          </div>
          <Film size={16} />
        </div>
        <ul className="run-review-list">
          {(activeRun.episodes ?? []).filter((row) => row.delivered_artifact_id).map((episode) => <li key={episode.id}
            title={episode.delivered_sha256 ?? undefined}>
            <Video size={12} />
            <span>{episode.title || episode.episode_id} · {seconds(episode.actual_seconds)} · 待你确认</span>
            {mode === 'post' && <EpisodePostEditor runId={activeRun.id} episode={episode}
              onDone={(text, kind) => { onMessage(text, kind); if (kind === 'success') load().catch(() => undefined); }} />}
          </li>)}
        </ul>
      </section>}

    {busy && <div className="run-busy"><Loader2 size={12} className="spin" /> 正在处理…</div>}
    {error && <div className="blocked-callout"><AlertTriangle size={13} />{error}</div>}
    {!journey && <div className="run-busy"><Loader2 size={12} className="spin" /> 正在读取作品状态…</div>}
  </div>;
}

export default DirectorProductionRunPanel;
