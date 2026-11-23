import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, Check, Clock3, Film, Loader2, LockKeyhole, Pause, Play, RefreshCw,
  ShieldAlert, Sparkles, Square,
} from 'lucide-react';
import {
  API, ApiError, api, confirmDirectorPackage, confirmEpisodePlan, controlProductionRun, createDirectorPackage,
  createEpisodePlan, createProductionRun, getJourney, getPackageReadiness, getProductionRun, getRunEvents,
  json, listDirectorPackages, listEpisodePlans, materializeEpisodePlan,
  type DeliveryAcceptance, type DirectorEpisodeOverview, type DirectorPackage, type EpisodePlan, type Journey,
  type PackageReadiness, type ProductionRun, type RunEpisode, type RunEvent, type WorkflowProfile,
} from '../api';
import { rememberProductionProfile, storedProductionProfile, rememberDirectorPackage, storedDirectorPackage } from '../productionProfile';
import { EpisodeAcceptance } from './EpisodeAcceptance';
import { EpisodeSubtitleFold } from './EpisodeSubtitleFold';
import GenerationSegmentsPanel from './GenerationSegmentsPanel';
import { OpenVideoFolderButton } from './OpenVideoFolderButton';
import { fetchProfileGates, gateReason, type ProfileGate } from './variantGate';
import { useStageRail, useRailPresent, type StageRegistration } from './StageRail';
import type { RailAction } from './ActionRail';
import { ArmedButton } from './ArmedButton';

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

/** 技术交付之外的第二个事实：人看过之后怎么决定。没有决定就是「待你确认」。 */
function acceptanceLabel(acceptance: DeliveryAcceptance | null | undefined): { text: string; tone: string } {
  if (!acceptance) return { text: '待你确认', tone: 'idle' };
  return acceptance.decision === 'accepted'
    ? { text: '已确认接受', tone: 'ok' }
    : { text: '已标记问题', tone: 'bad' };
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

/** 版本历史的绝对时间（本地时区）：每一版生成的时刻是可核对的记录。 */
function versionTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const value = Date.parse(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  if (Number.isNaN(value)) return '—';
  const d = new Date(value);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

type Props = {
  projectId: string;
  mode: Mode;
  /** Open the panel on this exact package instead of the newest one. */
  packageId?: string | null;
  demo?: boolean;
  workflowProfiles: WorkflowProfile[];
  /** 按集只读投影：成片要跨运行展示，不能只显示当前这次运行。 */
  episodes?: import('../api').DirectorEpisodeOverview[] | null;
  onMessage: (text: string, kind: 'success' | 'error') => void;
  onRefresh?: () => void;
  /** post 页：全部交付后去成片库。 */
  onOpenLibrary?: () => void;
};

export function DirectorProductionRunPanel({ projectId, mode, packageId, demo, workflowProfiles, episodes: overviewEpisodes, onMessage, onRefresh, onOpenLibrary }: Props) {
  const railPresent = useRailPresent();
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
  const [selectedPackageId, setSelectedPackageId] = useState<string>(packageId || storedDirectorPackage(projectId));
  // A deep link names one exact package; otherwise the person's last viewed
  // version is restored (per project), so switching pages keeps the record.
  useEffect(() => { setSelectedPackageId(packageId || storedDirectorPackage(projectId)); }, [projectId, packageId]);
  const selectPackage = (id: string) => {
    setSelectedPackageId(id);
    rememberDirectorPackage(projectId, id);
  };
  const [episodePage, setEpisodePage] = useState(0);
  /** ⑦ 单一选集：预览/验收/字幕/镜网格都只作用于手动选中的这一集。 */
  const [selectedEp, setSelectedEp] = useState('');
  /** 包范围：null 表示跟随默认（全部已确认分镜的集）；手动勾选后按勾选集合。 */
  const [scopeIds, setScopeIds] = useState<string[] | null>(null);
  /** 进展日志只渲染最近几条：记录可以一直增长，页面高度不能跟着长。 */
  const [eventLimit, setEventLimit] = useState(8);
  /** 「观看并确认」对话框的目标：技术交付之后，艺术验收由人决定。 */
  const [acceptTarget, setAcceptTarget] = useState<{
    artifactId: string; sha256: string; title: string; acceptance: DeliveryAcceptance | null;
  } | null>(null);
  const mounted = useRef(true);
  /** 快切包、作品或整片运行时，只有最后一次读取的结果算数。 */
  const loadEpoch = useRef(0);

  // 剧本统筹先分集：大纲确认后即可按场次分集，不需要先有镜头；
  // 已手工建好的镜头列表仍可用兼容入口按镜头分组。
  const shotCount = Number(
    (journey?.stages.find((stage) => stage.id === 'storyboard')?.facts?.['shot_count'] as number | undefined)
    ?? 0,
  );
  const outlineConfirmed = Boolean(
    journey?.stages.find((stage) => stage.id === 'script')?.facts?.['outline_confirmed'],
  );
  // `null` means "nobody has chosen yet", so the entry follows the work that
  // already exists. A project whose shots are already built gets the shot
  // grouping by default; asking it to plan by section first is what forced a
  // detour back to "materialise the plan" after the shots were done.
  const [planEntry, setPlanEntry] = useState<'sections' | 'shots' | null>(null);
  const planMode: 'sections' | 'shots' = outlineConfirmed
    ? (shotCount > 0 ? (planEntry ?? 'shots') : 'sections')
    : 'sections';
  const canPlan = planMode === 'sections' ? outlineConfirmed : shotCount > 0;
  const latestPlan = plans[0] ?? null;
  const confirmedPlan = plans.find((row) => row.status === 'CONFIRMED') ?? null;
  const storyboardFacts = (journey?.stages.find((stage) => stage.id === 'storyboard')?.facts ?? {}) as {
    ready_episode_ids?: string[];
  };
  const readyEpisodeIds = storyboardFacts.ready_episode_ids ?? [];
  const planEpisodeIds = (confirmedPlan ?? latestPlan)?.payload.episodes.map((row) => row.episode_id) ?? [];
  const effectiveScope = scopeIds ?? readyEpisodeIds;
  const latestPlanIsSections = latestPlan?.payload.mode === 'sections';
  const latestPlanMaterialized = !!latestPlan && latestPlan.payload.episodes.every((row) => row.shot_ids.length > 0);
  // The package the person is looking at: the one they asked for, or the
  // newest one.  The delivery receipt of a volume names it exactly.
  const latestPackage = (selectedPackageId
    ? packages.find((row) => row.id === selectedPackageId)
    : null) ?? packages[0] ?? null;
  const activeRun = run;

  const load = useCallback(async () => {
    // 快切包、作品或整片运行时丢弃旧异步结果：只有最后一次读取算数。
    const epoch = ++loadEpoch.current;
    const current = () => mounted.current && epoch === loadEpoch.current;
    try {
      const [journeyRow, planRows, packageRows] = await Promise.all([
        // The panel projects the exact package it is showing: progress,
        // playback and delivery all read the same scope.
        getJourney(projectId, { packageId: selectedPackageId || undefined }),
        listEpisodePlans(projectId),
        listDirectorPackages(projectId),
      ]);
      if (!current()) return;
      setJourney(journeyRow);
      setPlans(planRows);
      setPackages(packageRows);
      // The run comes from the journey's resolved scope, never from "the
      // newest run of this project".  A package that was never started shows
      // its待开拍 state instead of borrowing another package's controls (J11).
      const scopedRunId = journeyRow.scope?.run_id ?? journeyRow.run?.id ?? null;
      if (scopedRunId) {
        const detail = await getProductionRun(scopedRunId);
        if (!current()) return;
        setRun(detail);
        const scopedEvents = await getRunEvents(scopedRunId);
        if (!current()) return;
        setEvents(scopedEvents);
      } else {
        setRun(null);
        setEvents([]);
      }
      const scopedPackageId = journeyRow.scope?.package_id ?? selectedPackageId;
      const packageRow = (scopedPackageId
        ? packageRows.find((row) => row.id === scopedPackageId)
        : null) ?? packageRows[0] ?? null;
      if (packageRow) {
        const scopedReadiness = await getPackageReadiness(packageRow.id);
        if (!current()) return;
        setReadiness(scopedReadiness);
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
      if (current()) setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [projectId, profileId, selectedPackageId]);

  useEffect(() => {
    mounted.current = true;
    load().catch(() => undefined);
    return () => { mounted.current = false; };
  }, [load]);

  // One aggregate poll for the whole panel, and only while something is moving.
  const activeRunIdForPoll = journey?.scope?.run_id ?? journey?.run?.id ?? run?.id ?? '';
  const assembling = (overviewEpisodes ?? []).some((row) =>
    ['PENDING', 'ASSEMBLING'].includes(row.delivery?.assembly?.status ?? ''),
  ) || (run?.id === activeRunIdForPoll && (run?.episodes ?? []).some((row) =>
    ['PENDING', 'ASSEMBLING'].includes(row.assembly?.status ?? ''),
  ));
  const moving = (!!run && RUN_ACTIVE.includes(run.status)) || assembling;
  useEffect(() => {
    if (!moving) return;
    const intervalMs = assembling ? 1200 : 5000;
    const timer = window.setInterval(() => {
      load().catch(() => undefined);
      if (assembling) onRefresh?.();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [moving, assembling, load, onRefresh]);

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
  const unlockPlan = () => {
    if (!latestPlan || latestPlan.status !== 'CONFIRMED') return;
    return act('plan-unlock', () => api(
      `/projects/${projectId}/freeze/episode-plans/${latestPlan.version}/unlock`,
      json('POST', { reason: '用户手动解锁分集方案' }),
    ), '分集方案已解锁为草案；依赖它的已确认导演包已标记失效，请重新生成');
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
    // 画幅/分辨率的唯一控制节点在⑤导演包页（DirectorShootPanel），这里不再赋值。
    return act('package', () => createDirectorPackage(projectId, {
      workflow_profile_id: profileId,
      generation_mode: profileMode,
      policy: { batch_shot_limit: 6, max_repairs_per_shot: 2 },
      episode_ids: effectiveScope.length ? effectiveScope : null,
    }), '导演包已生成，请查看就绪检查后确认');
  };
  const confirmPackage = () => {
    if (!latestPackage) return;
    return act('package-confirm', () => confirmDirectorPackage(latestPackage.id, latestPackage.manifest_sha256),
      '导演包已确认，可以启动整部作品生产');
  };
  const unlockPackage = () => {
    if (!latestPackage || latestPackage.status !== 'CONFIRMED') return;
    return act('package-unlock', () => api(
      `/projects/${projectId}/freeze/director-packages/${latestPackage.id}/unlock`,
      json('POST', { reason: '用户手动解锁导演包' }),
    ), '导演包已标记失效，不能再开拍；请按当前内容生成新版本并重新确认');
  };
  const startRun = () => {
    if (!latestPackage) return;
    return act('run', () => createProductionRun(projectId, { package_id: latestPackage.id }),
      '整部作品已启动：后台会持续推进，无需你逐段点击');
  };
  const control = async (action: 'pause' | 'resume' | 'cancel' | 'reconcile') => {
    if (!activeRun) return;
    if (action === 'resume') {
      return act(action, async () => {
        const result = await controlProductionRun(activeRun.id, 'resume');
        if (result.status === 'NEEDS_REVIEW') {
          throw new Error(result.message || '当前批次没有可安全续拍的未提交镜头，请先处理阻塞项。');
        }
        return result;
      }, '已恢复生产');
    }
    const keyword = { pause: '已暂停：当前片段会先安全落盘',
      cancel: '已取消：已产出数据全部保留',
      reconcile: '已按真实产物核对运行状态' }[action];
    return act(action, () => controlProductionRun(activeRun.id, action), keyword);
  };

  const profileMode = useMemo(() => {
    const profile = workflowProfiles.find((row) => row.id === profileId);
    return profile?.mode ?? null;
  }, [profileId, workflowProfiles]);

  const selectedGate = profileId ? gates[profileId] : undefined;

  // 主操作收敛到页面吸顶栏：按模式与真实状态算出当前唯一 primary。
  const busyAny = busy !== '';
  const confirmPlanAction: RailAction | null = latestPlan && latestPlan.status !== 'CONFIRMED'
    ? { label: '确认分集方案', onClick: () => void confirmPlan(), loading: busy === 'plan-confirm', disabled: busyAny, icon: <Check size={14} /> }
    : null;
  const buildPackageAction: RailAction = {
    label: '生成导演包', onClick: () => void buildPackage(), loading: busy === 'package',
    disabled: busyAny || !confirmedPlan || !profileId || effectiveScope.length === 0,
    disabledReason: !profileId ? '先在基础配置登记本机生成方案' : !confirmedPlan ? '先确认分集方案，才能生成导演包' : effectiveScope.length === 0 ? '没有已确认分镜的集可以打包' : undefined,
    icon: <LockKeyhole size={14} />,
  };
  const confirmPackageAction: RailAction | null = latestPackage && latestPackage.status !== 'CONFIRMED'
    ? { label: '确认导演包', onClick: () => void confirmPackage(), loading: busy === 'package-confirm',
        disabled: busyAny || !readiness?.ready,
        disabledReason: !readiness?.ready ? '就绪检查未通过，先处理下方阻塞' : undefined, icon: <Check size={14} /> }
    : null;
  const canStartRun = !!latestPackage && latestPackage.status === 'CONFIRMED' && !!readiness?.startable
    && (!run || !RUN_ACTIVE.includes(run.status));
  const startRunAction: RailAction | null = canStartRun
    ? { label: latestPackage && latestPackage.episode_count > 1 ? `启动整部作品生产（${latestPackage.episode_count} 集）` : '启动整部作品生产',
        onClick: () => void startRun(), loading: busy === 'run', disabled: busyAny, icon: <Play size={14} /> }
    : null;

  let registration: StageRegistration = { priority: 1, primary: null };
  if (mode === 'plan') {
    registration = { priority: 1, primary: confirmPlanAction };
  } else if (mode === 'package') {
    registration = { priority: 1, primary: !latestPackage ? buildPackageAction : (confirmPackageAction ?? startRunAction) };
  } else if (mode === 'run') {
    registration = {
      priority: 1,
      primary: !activeRun ? startRunAction : null,
      running: activeRun && RUN_ACTIVE.includes(activeRun.status)
        ? { label: `整部作品${statusText(activeRun.status)}`, done: activeRun.progress.passed_shot_count, total: activeRun.scope.shot_count }
        : null,
    };
  } else if (mode === 'post') {
    registration = {
      priority: 1,
      primary: onOpenLibrary ? { label: '打开成片库', onClick: onOpenLibrary, icon: <Film size={14} /> } : null,
    };
  }
  useStageRail(`run-panel-${mode}`, registration,
    [mode, busy, latestPlan, confirmedPlan, latestPackage, readiness, run, profileId, onOpenLibrary, gates, workflowProfiles]);

  // 就绪检查是即时事实（会随风格/绑定/文件变化）；七阶段摘要只在没有包时兜底。
  const shownBlockers = (readiness?.blockers
    ?? journey?.stages.find((stage) => stage.id === 'package')?.blockers
    ?? []).filter((blocker) => blocker.code !== 'PACKAGE_NOT_CONFIRMED');

  /** 装配合同编辑器读运行分集行；跨运行的历史集用总览投影兜底，入口仍只有⑦这一处。 */
  function postEditorEpisode(episode: DirectorEpisodeOverview): RunEpisode {
    const scoped = (run?.episodes ?? []).find((row) => row.episode_id === episode.episode_id);
    if (scoped) return scoped;
    return {
      id: episode.episode_id, episode_id: episode.episode_id, position: episode.order ?? 0,
      title: episode.title, status: episode.state, shot_count: episode.shots.landed_count,
      passed_shot_count: episode.production?.passed_shot_count ?? 0,
      planned_seconds: episode.planned_seconds, shot_ids: [],
      post_contract: (episode.delivery?.post_contract as RunEpisode['post_contract']) ?? null,
      actual_seconds: episode.delivery?.actual_seconds ?? null, delivery_order_id: null,
      delivered_artifact_id: episode.delivery?.artifact_id ?? null,
      delivered_sha256: episode.delivery?.sha256 ?? null,
      blocker_code: null, delivered_at: episode.delivery?.delivered_at ?? null, batches: [],
    };
  }

  if (error && !journey) {
    return <section className="card section-card run-panel">
      <div className="blocked-callout"><ShieldAlert size={14} />{error}</div>
    </section>;
  }

  // ⑦ 后期成片：自上而下「集预览（含验收+折叠字幕）→ 该集镜预览」，单一选集。
  if (mode === 'post') {
    const eps = overviewEpisodes ?? [];
    const selected = eps.find((row) => row.episode_id === selectedEp) ?? eps[0] ?? null;
    const selRunId = selected
      ? (selected.delivery?.run_id ?? selected.production?.run_id ?? run?.id ?? '') : '';
    return <div className="run-panel post-panel">
      <section className="card section-card run-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">逐集成片</div>
            <h2>每一集都能单独播放与确认</h2>
            <p>选一集观看；确认接受、字幕烧录都只作用于当前选中的这一集。</p>
          </div>
          <button className="btn-secondary" disabled={busy !== ''} onClick={load}>
            <RefreshCw size={13} /> 刷新
          </button>
        </div>
        {eps.length === 0 && <p className="empty-inline">还没有可预览的集。先在「视频生产」完成拍摄与装配。</p>}
        {eps.length > 0 && <div className="post-ep-selector" role="tablist" aria-label="选择集">
          {eps.map((row) => <button key={row.episode_id} type="button" role="tab"
            aria-selected={selected?.episode_id === row.episode_id}
            className={`head-chip ${selected?.episode_id === row.episode_id ? 'active' : ''}`}
            onClick={() => setSelectedEp(row.episode_id)}>
            {row.title || row.episode_id} · {row.state_label}
          </button>)}
        </div>}
        {selected && (() => {
          const liveAssembly = selected.delivery?.assembly
            ?? (activeRun?.episodes ?? []).find((row) => row.episode_id === selected.episode_id)?.assembly
            ?? null;
          return <div className="post-ep-preview">
            <div className="run-ep-head">
              <span className="run-ep-no">{selected.order ?? '—'}</span>
              <div>
                <b>{selected.title || selected.episode_id}</b>
                <span>{selected.shots.confirmed_count || selected.shots.landed_count} 镜
                  {' '}· 成片 {selected.delivery?.actual_seconds ? `${Math.round(selected.delivery.actual_seconds)} 秒` : '—'}</span>
                <em className="run-ep-sub">
                  {liveAssembly && ['PENDING', 'ASSEMBLING'].includes(liveAssembly.status)
                    ? `正在重新装配 ${liveAssembly.percent}% · ${liveAssembly.detail}`
                    : <>
                      {selected.delivery?.sha256 ? `文件 ${selected.delivery.sha256.slice(0, 10)}…（SHA-256）` : '尚无成片文件'}
                      {' · '}
                      {selected.delivery?.post_contract?.subtitle_revision
                        ? `字幕第 ${Number(selected.delivery.post_contract.subtitle_revision)} 版` : '无字幕轨'}
                    </>}
                </em>
              </div>
              <span className={`run-badge ${acceptanceLabel(selected.delivery?.acceptance).tone}`}>
                {acceptanceLabel(selected.delivery?.acceptance).text}
              </span>
            </div>
            {selected.delivery?.artifact_id
              ? <div className="run-ep-media">
                <video key={selected.delivery.artifact_id} controls preload="metadata"
                  src={`${API}/deliveries/${selected.delivery.artifact_id}/media`} />
                <div className="row run-ep-acceptance">
                  <OpenVideoFolderButton artifactId={selected.delivery.artifact_id} />
                  <button type="button" className="btn-secondary" onClick={() => setAcceptTarget({
                    artifactId: selected.delivery!.artifact_id!,
                    sha256: selected.delivery!.sha256 ?? '',
                    title: selected.title || selected.episode_id,
                    acceptance: selected.delivery?.acceptance ?? null,
                  })}>观看并确认</button>
                </div>
              </div>
              : <p className="run-hint">这一集还没有装配成片；全部镜头通过后会自动装配。</p>}
            {selRunId && <EpisodeSubtitleFold projectId={projectId} episode={selected} runId={selRunId}
              runEpisode={postEditorEpisode(selected)} assembly={liveAssembly} onMessage={onMessage}
              onApplied={() => { void load(); onRefresh?.(); }} />}
          </div>;
        })()}
      </section>
      {selected && <section className="card section-card run-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">第 {selected.order ?? '—'} 集 · 每一镜</div>
            <h2>该集的成片文件</h2>
            <p>只展示当前选中集的镜；切换上方选集查看其它集。单镜返修在这里。</p>
          </div>
          <Film size={16} />
        </div>
        <GenerationSegmentsPanel projectId={projectId} fixedEpisode={String(selected.order ?? '')}
          fingerprint={`post-${selected.episode_id}-${selected.delivery?.artifact_id ?? ''}`}
          onChanged={() => { void load(); onRefresh?.(); }} />
      </section>}
      {acceptTarget && <EpisodeAcceptance
        artifactId={acceptTarget.artifactId} sha256={acceptTarget.sha256}
        title={acceptTarget.title} acceptance={acceptTarget.acceptance}
        onClose={() => setAcceptTarget(null)}
        onDecided={() => {
          setAcceptTarget(null);
          void load();
          onRefresh?.();
        }}
      />}
    </div>;
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
              <select value={planMode} onChange={(event) => setPlanEntry(event.target.value as 'sections' | 'shots')}>
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
            <button className="btn-secondary" disabled={busy !== '' || !canPlan}
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
          {!railPresent && latestPlan.status !== 'CONFIRMED' && <button className="btn-primary" disabled={busy !== ''} onClick={confirmPlan}>
            <Check size={13} /> 确认分集方案
          </button>}
          {latestPlan.status === 'CONFIRMED' && <button className="btn-secondary" disabled={busy !== ''}
            title="把这一版退回草案；依赖它的已确认导演包会标记失效，旧数据保留"
            onClick={unlockPlan}>
            解锁分集方案
          </button>}
          {latestPlanIsSections && !latestPlanMaterialized && <button className="btn-secondary" disabled={busy !== ''}
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
          <button className="btn-secondary" disabled={busy !== ''} onClick={load}>
            <RefreshCw size={13} /> 重新检查
          </button>
        </div>

        <div className="run-binding-row">
          {packages.length > 0 && <div className="package-version-history" aria-label="导演包版本历史">
            <span className="pvh-title">版本历史（每次生成/重拍递增一版，点击切换查看该版配置）</span>
            <div className="pvh-list">
              {packages.map((row) => <button type="button" key={row.id}
                className={`pvh-item${latestPackage?.id === row.id ? ' active' : ''}${row.stale ? ' stale' : ''}`}
                aria-pressed={latestPackage?.id === row.id}
                onClick={() => selectPackage(row.id)}>
                <span className="pvh-rev">第 {row.revision} 版</span>
                <span className={`head-chip ${row.status === 'CONFIRMED' ? 'ok' : row.stale ? 'warn' : ''}`}>
                  {statusText(row.status)}{row.stale ? ' · 失效' : ''}</span>
                <span className="pvh-config">
                  {row.style ? `风格 v${row.style.version}` : '风格未冻结'}
                  {' · '}{row.binding ? row.binding.mode : '方案未绑定'}
                  {row.binding?.output?.width && row.binding?.output?.height
                    ? ` · ${row.binding.output.width}×${row.binding.output.height}` : ''}
                  {' · '}{row.episode_count} 集 {row.shot_count} 镜
                </span>
                <span className="pvh-time">{versionTime(row.created_at)}{row.confirmed_at ? ' · 已确认' : ' · 待确认'}</span>
              </button>)}
            </div>
          </div>}
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
          {!railPresent && <button className="btn-secondary" disabled={busy !== '' || !confirmedPlan || effectiveScope.length === 0} onClick={buildPackage}>
            <LockKeyhole size={13} /> 生成导演包
          </button>}
          {!confirmedPlan && <span className="run-hint">先确认分集方案，才能生成导演包。</span>}
        </div>

        {planEpisodeIds.length > 0 && <div className="run-binding-row package-scope-row">
          <span>包范围：</span>
          {(confirmedPlan ?? latestPlan)?.payload.episodes.map((episode) => {
            const id = episode.episode_id;
            const ready = readyEpisodeIds.includes(id);
            return <label key={id} className="head-chip">
              <input type="checkbox" checked={effectiveScope.includes(id)} disabled={!ready || busy !== ''}
                onChange={() => {
                  const base = scopeIds ?? readyEpisodeIds;
                  const next = base.includes(id) ? base.filter((row) => row !== id) : [...base, id];
                  setScopeIds(planEpisodeIds.filter((row) => next.includes(row)));
                }} />
              {episode.title || id}{!ready ? '（分镜未确认）' : ''}
            </label>;
          })}
          {effectiveScope.length < planEpisodeIds.length && <span className="run-hint">
            只打包勾选的集；未确认的集等分镜确认后可单独打包。
          </span>}
        </div>}

        {latestPackage && <div className="run-package">
          <div className="run-package-head">
            <div>
              <b>第 {latestPackage.revision} 版 · {latestPackage.episode_count} 集 · {latestPackage.shot_count} 镜</b>
              <span>{statusText(latestPackage.status)}{latestPackage.stale ? ' · 已失效，需要重新生成' : ''}</span>
            </div>
            {!railPresent && latestPackage.status !== 'CONFIRMED' && <button className="btn-primary"
              disabled={busy !== '' || !readiness?.ready} onClick={confirmPackage}>
              <Check size={13} /> 确认导演包
            </button>}
            {latestPackage.status === 'CONFIRMED' && <button className="btn-secondary"
              disabled={busy !== '' || latestPackage.stale}
              title="确认后的导演包保持只读；解锁会把它标记失效，请生成新版本再确认"
              onClick={unlockPackage}>
              解锁导演包
            </button>}
          </div>

          <dl className="run-facts">
            <div><dt>剧本</dt><dd>{latestPackage.script?.outline_confirmed ? '大纲已确认' : '无导入剧本（按分镜执行）'}</dd></div>
            <div><dt>风格</dt><dd>{latestPackage.style
              ? `第 ${latestPackage.style.version} 版 · 已冻结` : '未冻结'}</dd></div>
            <div><dt>生成方案</dt><dd>{latestPackage.binding
              ? latestPackage.binding.mode : '未绑定'}</dd></div>
            <div><dt>画幅</dt><dd>{latestPackage.binding?.output?.width && latestPackage.binding?.output?.height
              ? `${latestPackage.binding.output.width}×${latestPackage.binding.output.height}` : '沿用生成方案'}</dd></div>
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

          {!railPresent && latestPackage.status === 'CONFIRMED' && readiness?.startable
            && (!run || !RUN_ACTIVE.includes(run.status)) && <button className="btn-primary btn-wide"
              disabled={busy !== ''} onClick={startRun}>
              <Play size={14} /> 启动整部作品生产
              {latestPackage.episode_count > 1 ? `（${latestPackage.episode_count} 集一次跑完）` : ''}
            </button>}
          {run && <p className="run-hint">这个导演包已经启动过一次；重复点击会返回同一次运行，不会重复开拍。</p>}
        </div>}
      </section>
    </>}

    {mode === 'run' && <section className="card section-card run-card">
      <div className="section-heading">
        <div>
          <div className="section-kicker">整部作品运行</div>
          <h2>后台会一直推进，关了浏览器也一样</h2>
          <p>运行状态来自服务端记录：心跳只代表后台还活着，最近进展才代表真的拍出了东西。</p>
        </div>
        <button className="btn-secondary" disabled={busy !== ''} onClick={load}>
          <RefreshCw size={13} /> 刷新
        </button>
      </div>

      {!activeRun
        ? <p className="empty-inline">还没有整部作品的运行记录。先在「导演包」阶段确认并启动。</p>
        : <>
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
            {['QUEUED', 'RUNNING'].includes(activeRun.status) && <button className="btn-secondary"
              disabled={busy !== ''} onClick={() => control('pause')}><Pause size={12} /> 暂停</button>}
            {activeRun.status === 'PAUSED' && <button className="btn-secondary"
              disabled={busy !== ''} onClick={() => control('resume')}><Play size={12} /> 继续</button>}
            {activeRun.status === 'NEEDS_REVIEW' && <>
              <button className="btn-secondary" disabled={busy !== ''}
                onClick={() => control('reconcile')}><RefreshCw size={12} /> 核对真实结果</button>
              <button className="btn-secondary" disabled={busy !== ''}
                title="只恢复服务端能够证明尚未提交到 H3 的批次；其他阻塞保持待处理"
                onClick={() => control('resume')}><Play size={12} /> 继续安全可恢复批次</button>
            </>}
            {!['COMPLETED', 'CANCELLED'].includes(activeRun.status) && <ArmedButton
              label="取消" armedLabel="再点一次确认取消" disabled={busy !== ''}
              icon={<Square size={12} />} onConfirm={() => control('cancel')} />}
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

        {mode === 'run' && <ol className="run-episodes">
          {(activeRun.episodes ?? []).slice(episodePage * 8, episodePage * 8 + 8).map((episode) => <li key={episode.id}>
            <div className="run-ep-head">
              <span className="run-ep-no">{episode.position}</span>
              <div>
                <b>{episode.title || episode.episode_id}</b>
                <span>{episode.shot_count} 镜 · 通过 {episode.passed_shot_count}
                  {' '}· 计划 {seconds(episode.planned_seconds)}
                  {episode.actual_seconds ? ` · 成片 ${seconds(episode.actual_seconds)}` : ''}</span>
                <em className="run-ep-sub">
                  {episode.delivered_sha256
                    ? `文件 ${episode.delivered_sha256.slice(0, 10)}…（SHA-256）`
                    : '尚无成片文件'}
                  {' · '}
                  {episode.post_contract?.subtitle_revision
                    ? `字幕第 ${episode.post_contract.subtitle_revision} 版`
                    : '无字幕轨（按默认剪辑）'}
                  {' · 历史版本见成片库'}
                </em>
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
              {/* 最终验收的提示与动作只在⑦后期成片（与成片库）；⑥只展示产物。 */}
              <div className="row run-ep-acceptance">
                <OpenVideoFolderButton artifactId={episode.delivered_artifact_id} />
              </div>
            </div>}
          </li>)}
        </ol>}
        {(mode === 'run' && (activeRun.episodes?.length ?? 0) > 8) && <div className="run-pager">
          <button className="btn-secondary" disabled={episodePage === 0}
            onClick={() => setEpisodePage((page) => Math.max(0, page - 1))}>上一页</button>
          <span>第 {episodePage + 1} / {Math.ceil((activeRun.episodes?.length ?? 0) / 8)} 页</span>
          <button className="btn-secondary"
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

    {busy && <div className="run-busy"><Loader2 size={12} className="spin" /> 正在处理…</div>}
    {error && <div className="blocked-callout"><AlertTriangle size={13} />{error}</div>}
    {!journey && <div className="run-busy"><Loader2 size={12} className="spin" /> 正在读取作品状态…</div>}
    {acceptTarget && <EpisodeAcceptance
      artifactId={acceptTarget.artifactId}
      sha256={acceptTarget.sha256}
      title={acceptTarget.title}
      acceptance={acceptTarget.acceptance}
      onClose={() => setAcceptTarget(null)}
      onDecided={(acceptance) => {
        // 立即更新本页的分集徽标，再让服务端状态刷新覆盖（含总览投影）。
        setRun((current) => current ? {
          ...current,
          episodes: (current.episodes ?? []).map((episode) =>
            episode.delivered_artifact_id === acceptTarget.artifactId ? { ...episode, acceptance } : episode),
        } : current);
        setAcceptTarget(null);
        void load();
        onRefresh?.();
      }}
    />}
  </div>;
}

export default DirectorProductionRunPanel;
