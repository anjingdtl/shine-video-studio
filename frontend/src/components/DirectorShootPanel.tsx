/**
 * V2 导演模式的按集状态卡与唯一开拍入口。
 *
 * 这两个组件只读后端投影（`/director/overview`），不自己猜完成度：一张集卡的
 * 状态、缺项和下一步全部来自权威台账。⑤⑥ 只保留一个主操作 ——「确认拍摄方案并
 * 开始真实制作」；它复用既有导演包与 Run 引擎，不另起执行链。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, Check, Clapperboard, Film, Loader2, Play, RefreshCw, ShieldCheck, Square,
} from 'lucide-react';
import {
  ApiError,
  applyCurrentStyleToShots,
  controlProductionRun,
  getProductionRun,
  getStrategyOptions,
  getVariantAvailability,
  listDirectorPackages,
  startDirectorProduction,
  type DirectorEpisodeOverview,
  type DirectorOverview,
  type DirectorPackage,
  type Job,
  type ProductionRun,
  type ProductionStrategy,
  type StrategyOptions,
  type StyleLoraConfig,
  type VariantAvailability,
} from '../api';
import { StyleLoraAssemblyPanel } from '../shared/StyleLoraAssemblyPanel';
import { frameSize, ORIENTATION_LABELS, RESOLUTION_SCALES, scaleLabel, type Orientation } from '../outputFrame';
import { useRailPresent, useStageRail } from './StageRail';
import type { RailAction } from './ActionRail';
import { GuideTip } from './GuideTip';
import { ArmedButton } from './ArmedButton';
import { rememberShootStrategy, storedShootStrategy } from '../productionProfile';

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return String(error);
}

const PACKAGE_STATUS_TEXT: Record<string, string> = {
  DRAFT: '待确认', CONFIRMED: '已确认', STALE: '已失效', SUPERSEDED: '已被新版取代',
};

/** 运行仍在逐镜推进或等待当前镜头落盘的状态：此期间正在渲染的任务属于整片运行，
 *  不显示成「单镜重拍」。只是展示归类，不是第二份占用裁决。 */
const RUN_RENDERING = ['QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'CANCEL_REQUESTED'];

function runHeading(status: string): string {
  if (status === 'QUEUED' || status === 'RUNNING') return '后台正在逐镜生成';
  if (status === 'PAUSE_REQUESTED') return '正在等待当前镜头落盘后暂停';
  if (status === 'PAUSED') return '运行已暂停';
  if (status === 'CANCEL_REQUESTED') return '正在安全取消（等待当前镜头与资源收尾）';
  if (status === 'RECONCILING') return '正在核对受管产物与资源';
  if (status === 'COMPLETED') return '这次运行已经完成';
  if (status === 'NEEDS_REVIEW') return '运行等待人工核对';
  if (status === 'CANCELLED') return '运行已取消';
  return '运行已停止';
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

const STATE_TONE: Record<string, string> = {
  WAITING_STORYBOARD: '',
  STORYBOARD_RUNNING: 'run',
  STORYBOARD_BLOCKED: 'warn',
  SHOTS_TO_CONFIRM: 'warn',
  MATERIAL_MISSING: 'warn',
  READY_TO_PACKAGE: 'ok',
  READY_TO_SHOOT: 'ok',
  SHOOTING: 'run',
  REVIEW_READY: 'ok',
  DELIVERED: 'ok',
};

/* ============================ 按集状态卡 ============================ */

export function EpisodeCards({ episodes, onAction, compact }: {
  episodes: DirectorEpisodeOverview[];
  onAction?: (episode: DirectorEpisodeOverview) => void;
  compact?: boolean;
}) {
  if (!episodes.length) {
    return <p className="empty-inline">还没有分集：先在「剧本统筹」确认分集与风格。</p>;
  }
  return <div className={`episode-cards${compact ? ' compact' : ''}`}>
    {episodes.map((episode) => {
      const tone = STATE_TONE[episode.state] ?? '';
      const missing = episode.materials.missing.slice(0, 3);
      return <article key={episode.episode_id} className={`episode-card ${tone}`}>
        <header>
          <span className="episode-card-order">第 {episode.order ?? '—'} 集</span>
          <span className={`episode-card-state ${tone}`}>{episode.state_label}</span>
        </header>
        <h3>{episode.title || episode.episode_id}</h3>
        <p className="episode-card-facts">
          {episode.storyboard.section_count} 场 · {episode.shots.confirmed_count || episode.shots.landed_count} 镜
          {episode.planned_seconds ? ` · 约 ${Math.round(episode.planned_seconds)} 秒` : ''}
        </p>
        {episode.production && (episode.state === 'SHOOTING' || episode.state === 'REVIEW_READY') && (
          <p className="episode-card-progress">
            已通过 {episode.production.passed_shot_count}/{episode.production.shot_count} 镜
          </p>
        )}
        {missing.length > 0 && episode.state !== 'READY_TO_SHOOT' && (
          <ul className="episode-card-gaps">
            {missing.map((gap) => (
              <li key={`${gap.code}-${gap.entity_id ?? ''}`}>
                {gap.entity_id ? `「${gap.entity_id}」` : '参考素材'}
                {gap.shot_count > 1 ? ` 等 ${gap.shot_count} 镜` : ''} 缺主素材
              </li>
            ))}
          </ul>
        )}
        {episode.storyboard.pending_sections.length > 0 && (
          <p className="episode-card-gaps">
            还有 {episode.storyboard.pending_sections.length} 场没拆成镜头
          </p>
        )}
        <footer>
          {episode.next_action && onAction
            ? <button type="button" className="btn-secondary" onClick={() => onAction(episode)}>
                {episode.next_action.label}
              </button>
            : <span className="muted">{episode.next_action?.label
              ?? (episode.state === 'DELIVERED' ? '到「后期成片」播放并确认' : '等待下一步')}</span>}
        </footer>
      </article>;
    })}
  </div>;
}

/* ============================ ⑤ 导演包 / ⑥ 视频生产 ============================ */

/** 模型版本的普通人说法（工程名保留，中文放在括号里）。 */
export function describeVariantName(variantId: string): string {
  const lower = (variantId || '').toLowerCase();
  if (/int8-convrot/.test(lower)) return '标准画质 · INT8';
  // NVFP4 的 id 也含 lowend：必须先于 W6A8 匹配，否则两个不同变体显示同名。
  if (/nvfp4/.test(lower)) return '低显存候选 · NVFP4';
  if (/lowend|w6a8/.test(lower)) return '低显存候选 · W6A8';
  if (/w4/.test(lower)) return '候选 · W4';
  return variantId;
}

/** 加速配方的普通人说法。 */
export function describeAccelerator(acceleratorId: string, steps?: number): string {
  const lower = (acceleratorId || '').toLowerCase();
  if (lower === 'pdd8') return '快速 · 8 步加速';
  if (lower === 'native20') return '高质量 · 原生 20 步';
  if (lower === 'turbo4') return '极速 · 4 步';
  return steps ? `${acceleratorId} · ${steps} 步` : acceleratorId;
}

/** 服务端读不到选项时的最保守回退：只列已验收的 INT8 + PDD8，但「已核验」
 *  是服务端事实，读不到就是未知，显示层不得伪造 verified（R5 B6/INV-14）。
 *  提交仍由服务端门禁最终裁决。 */
const FALLBACK_OPTIONS: StrategyOptions = {
  model_variants: [{
    variant_id: 'h3-ref2va-int8-convrot', label: 'h3-ref2va-int8-convrot',
    acceptance_status: 'SUPPORTED', verified: false, lora_supported: true, default_recommended: true,
  }],
  accelerators: [
    { accelerator_id: 'pdd8', steps: 8, verified: false },
    { accelerator_id: 'native20', steps: 20, verified: false },
  ],
  second_pass: { supported: false, recipes: ['h3-tail-refine-v1'] },
  upscale: [],
};

function sanitizeStoredLoras(raw: unknown): StyleLoraConfig[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((row): row is StyleLoraConfig => (
      Boolean(row) && typeof row === 'object'
      && typeof (row as Record<string, unknown>).lora_id === 'string'
      && typeof (row as Record<string, unknown>).lora_name === 'string'
      && typeof (row as Record<string, unknown>).sha256 === 'string'
    ))
    .slice(0, 3)
    .map((row) => ({
      lora_id: row.lora_id,
      lora_name: row.lora_name,
      sha256: row.sha256,
      strength_model: typeof row.strength_model === 'number' && Number.isFinite(row.strength_model) ? row.strength_model : 0.65,
      trigger_word: typeof row.trigger_word === 'string' ? row.trigger_word : '',
    }));
}

export function DirectorShootPanel({ projectId, overview, demo, mode, refreshEpoch, activeJob, attentionJobs, onMessage, onRefresh, onNavigate, onGoSetup, onAbandon }: {
  projectId: string;
  overview: DirectorOverview | null;
  demo: boolean;
  /** package：第⑤步只做拍摄方案与开拍；run：第⑥步只看真实生产进度。 */
  mode: 'package' | 'run';
  /** 与 App 的投影跟读同节奏：运行卡不能比集卡旧。 */
  refreshEpoch?: number;
  /**  job 台账里正在渲染的任务（同一事实，App 透传）：进度条「第几镜」用它。 */
  activeJob?: { shot_id: string; shot_name: string; status: string; attempts?: { progress: number }[] } | null;
  /** 本次运行范围内等待人工决定的镜头（未知结果 / 已放弃）：同一份 job 台账。 */
  attentionJobs?: Job[];
  onMessage?: (message: string, tone?: 'success' | 'error') => void;
  onRefresh?: () => void;
  onNavigate?: (page: 'package' | 'run' | 'deliver') => void;
  /** 模型下载统一在基础配置完成；本页只把未就绪的变体导航过去，不另设下载入口。 */
  onGoSetup?: () => void;
  /** 既有唯一放弃执行者（与任务明细同一入口），不另起执行链。 */
  onAbandon?: (jobId: string) => void;
}) {
  const railPresent = useRailPresent();
  // 切页回来组件重挂载：从项目级存储恢复上次选的拍摄方案，不回默认。
  const stored = storedShootStrategy(projectId);
  const [selected, setSelected] = useState<string[]>([]);
  const [options, setOptions] = useState<StrategyOptions>(FALLBACK_OPTIONS);
  const [variantId, setVariantId] = useState(typeof stored?.variantId === 'string' ? stored.variantId : 'h3-ref2va-int8-convrot');
  const [acceleratorId, setAcceleratorId] = useState(typeof stored?.acceleratorId === 'string' ? stored.acceleratorId : 'native20');
  // 画幅/分辨率在七阶段里只有这一个控制节点：产出 OutputSpec 宽高，
  // 随开拍冻结进导演包，派发读同一份 binding.output。
  const [orientation, setOrientation] = useState<Orientation>(stored?.orientation === 'portrait' ? 'portrait' : 'landscape');
  const [resolutionScale, setResolutionScale] = useState(typeof stored?.resolutionScale === 'number' ? stored.resolutionScale : 1.0);
  const [secondPass, setSecondPass] = useState(typeof stored?.secondPass === 'boolean' ? stored.secondPass : false);
  const [upscale, setUpscale] = useState(typeof stored?.upscale === 'boolean' ? stored.upscale : false);
  const [styleLoras, setStyleLoras] = useState<StyleLoraConfig[]>(() => sanitizeStoredLoras(stored?.styleLoras));
  useEffect(() => {
    rememberShootStrategy(projectId, { variantId, acceleratorId, orientation, resolutionScale, secondPass, upscale, styleLoras });
  }, [projectId, variantId, acceleratorId, orientation, resolutionScale, secondPass, upscale, styleLoras]);

  const selectedVariantLoraSupported = useMemo(() => {
    const found = options.model_variants.find((row) => row.variant_id === variantId);
    if (found && typeof found.lora_supported === 'boolean') return found.lora_supported;
    return variantId === 'h3-ref2va-int8-convrot';
  }, [options.model_variants, variantId]);
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  const [styleGap, setStyleGap] = useState(false);
  const [run, setRun] = useState<ProductionRun | null>(null);
  /** 导演包版本台账（服务端 director_package_versions 唯一事实）：每次生成/重拍
   *  递增一版，这里只读展示每一版的配置，供人核对与管理。 */
  const [packages, setPackages] = useState<DirectorPackage[]>([]);
  const [touched, setTouched] = useState(false);
  /** 基础配置里已经准备好的方案：有就点亮，没有就灰色。但「未就绪」不等于
   *  「不可选择」——候选模型可以被选中进入资源准备，只是不能开拍。 */
  const [availability, setAvailability] = useState<Record<string, VariantAvailability>>({});
  const [availabilityLoaded, setAvailabilityLoaded] = useState(false);
  const submitting = useRef(false);

  // 稳定的空数组：每次渲染新建 [] 会让下面的选择 effect 无限触发。
  const episodes = useMemo(() => overview?.episodes ?? [], [overview]);
  // 后端允许重拍，但"默认要拍的集"只包含真的还没拍的：已交付的集被默认勾上，
  // 会让"拍第 2 集"顺手把第 1 集重拍一遍。
  const shootable = useMemo(
    () => episodes.filter((row) => ['READY_TO_SHOOT', 'READY_TO_PACKAGE', 'SHOOTING', 'REVIEW_READY', 'DELIVERED']
      .includes(row.state)).map((row) => row.episode_id),
    [episodes],
  );
  const pendingShoot = useMemo(
    () => episodes.filter((row) => ['READY_TO_SHOOT', 'READY_TO_PACKAGE'].includes(row.state))
      .map((row) => row.episode_id),
    [episodes],
  );
  useEffect(() => {
    if (touched) return;
    setSelected((current) => (current.length === pendingShoot.length
      && current.every((id, index) => id === pendingShoot[index]) ? current : pendingShoot));
  }, [pendingShoot, touched]);

  // 生产策略选项来自服务端能力矩阵；读不到时保持最保守回退，
  // 由服务端在提交时做最终裁决（前端不发明拒绝理由）。
  // 瞬态超时或刷新时允许自动补拉一次，避免页面一直停在“本机未就绪”回退态。
  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    const load = (allowRetry: boolean) => {
      getStrategyOptions(projectId)
        .then((rows) => { if (!cancelled) setOptions(rows); })
        .catch(() => {
          if (!cancelled && allowRetry) {
            retryTimer = window.setTimeout(() => load(false), 1500);
          }
        });
    };
    load(true);
    return () => {
      cancelled = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [projectId, refreshEpoch]);

  const activeRunId = overview?.run?.run_id ?? null;
  useEffect(() => {
    if (!activeRunId) { setRun(null); return; }
    let cancelled = false;
    getProductionRun(activeRunId)
      .then((row) => { if (!cancelled) setRun(row); })
      .catch(() => { if (!cancelled) setRun(null); });
    return () => { cancelled = true; };
  }, [activeRunId, refreshEpoch]);

  useEffect(() => {
    let cancelled = false;
    listDirectorPackages(projectId)
      .then((rows) => { if (!cancelled) setPackages(rows); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [projectId, refreshEpoch]);

  // 模型版本状态直接来自基础配置的台账：不在这里另算一套「验收」。
  useEffect(() => {
    let cancelled = false;
    let retryTimer: number | undefined;
    const load = (allowRetry: boolean) => {
      getVariantAvailability(projectId)
        .then((rows) => {
          if (cancelled) return;
          setAvailability(Object.fromEntries(rows.map((row) => [row.variant_id, row])));
          setAvailabilityLoaded(true);
          // 默认选第一个已就绪的方案；但用户已经选定的方案（哪怕未就绪、需要
          // 准备资源）保持不变，不被强行切回 INT8。
          setVariantId((current) => {
            if (rows.some((row) => row.variant_id === current)) return current;
            const ready = rows.find((row) => row.available);
            return (ready ?? rows[0])?.variant_id ?? current;
          });
        })
        .catch(() => {
          if (!cancelled && allowRetry) {
            retryTimer = window.setTimeout(() => load(false), 1500);
          }
        });
    };
    load(true);
    return () => {
      cancelled = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [projectId, refreshEpoch]);

  const chosen = availability[variantId];
  const chosenVariantAvailable = chosen?.available ?? false;
  const chosenVariantReason = chosen?.reason
    ?? (availabilityLoaded ? '本机没有这套权重' : '正在读取本机模型状态…');
  // 是否已有运行占着本次拍摄范围（占着就不许并发开第二轮）：只读服务端
  // overview.run.active 这一份裁决——它是后端 ACTIVE_RUN_STATUSES 的唯一投影；
  // 前端不再另抄一份状态清单，免得两处清单各自漂移。
  const running = Boolean(run && overview?.run?.active);

  // 进度条只读两份既有事实：run 的 passed/total 与 job 台账正在渲染的任务；
  // 前端不自己累计进度，也不另设第二套判定（三原则）。
  const totalShots = run?.scope.shot_count ?? 0;
  const passedShots = run?.progress.passed_shot_count ?? 0;
  const productionDone = Boolean(run)
    && (run?.status === 'COMPLETED' || (totalShots > 0 && passedShots >= totalShots));
  const progressPct = productionDone ? 100
    : totalShots > 0 ? Math.min(100, Math.round((passedShots / totalShots) * 100)) : 0;
  const orderedShotIds = useMemo(() => (run?.episodes ?? [])
    .slice().sort((a, b) => a.position - b.position)
    .flatMap((episode) => episode.shot_ids ?? []), [run]);
  const activeShotNo = activeJob ? orderedShotIds.indexOf(activeJob.shot_id) + 1 : 0;
  const currentShotNo = activeShotNo > 0 ? activeShotNo : Math.min(passedShots + 1, Math.max(totalShots, 1));
  const barWorking = !productionDone
    && (Boolean(activeJob) || Boolean(run && ['RUNNING', 'QUEUED', 'PAUSE_REQUESTED'].includes(run.status)));
  // 单镜重拍进度：读同一份 job/attempt 台账；整片运行在跑时不叠加显示。
  const retakePct = activeJob
    ? (activeJob.status === 'PASSED' ? 100
      : activeJob.attempts?.[activeJob.attempts.length - 1]?.progress ?? 0)
    : 0;
  // 运行范围内等待人工决定的镜头：未知结果→放弃+重拍；已放弃→重拍。
  // 事实读同一份 job 台账，动作复用既有执行者，不另起链路。
  const scopeShotIds = useMemo(
    () => new Set((run?.episodes ?? []).flatMap((episode) => episode.shot_ids ?? [])),
    [run],
  );
  const decisionJobs = useMemo(
    () => (attentionJobs ?? []).filter((job) => scopeShotIds.has(job.shot_id)),
    [attentionJobs, scopeShotIds],
  );

  const toggle = (episodeId: string) => {
    setTouched(true);
    setSelected((current) => current.includes(episodeId)
      ? current.filter((item) => item !== episodeId)
      : [...current, episodeId]);
  };

  const start = useCallback(async () => {
    if (submitting.current || !selected.length || !chosenVariantAvailable || running) return;
    submitting.current = true;
    setBusy('start'); setProblem(''); setStyleGap(false);
    try {
      const secondPassReady = options.second_pass.supported;
      const upscaleReady = options.upscale.some((row) => row.enabled);
      const strategy: ProductionStrategy = {
        schema_version: '1',
        generation_mode: 'Ref2VA',
        model_variant_id: variantId,
        accelerator_id: acceleratorId,
        second_pass: {
          enabled: secondPass && secondPassReady,
          recipe_id: 'h3-tail-refine-v1',
          denoise: 0.25,
          latent_scale: 1.0,
        },
        upscale: upscale && upscaleReady
          ? { enabled: true, engine: 'relayvsr', variant: 'x2', quality_preset: 'balanced' }
          : { enabled: false },
        style_loras: styleLoras,
      };
      const frame = frameSize(orientation, resolutionScale);
      const result = await startDirectorProduction(projectId, {
        episode_ids: selected,
        strategy,
        output: { width: frame.width, height: frame.height, fps: 24 },
      });
      const shotCount = result.episodes.reduce((sum, row) => sum + row.shot_count, 0);
      onMessage?.(result.created
        ? `已开始真实制作：第 ${result.episodes.map((row) => row.position).join('、')} 集，系统已为 ${shotCount} 镜自动安排参考素材和生成策略。`
        : '这次选择已经开拍，返回了同一次运行，没有重复启动。', 'success');
      onRefresh?.();
      onNavigate?.('run');
    } catch (error) {
      // 预检报镜头执行旧冻结风格时，就地给出既有执行者的恢复动作，
      // 而不是只留一墙错误码让人自己找入口。
      const blockers = error instanceof ApiError
        ? (error.detail as { blockers?: Array<{ code?: string }> } | undefined)?.blockers ?? []
        : [];
      setStyleGap(error instanceof ApiError && error.code === 'DIRECTOR_PACKAGE_NOT_READY'
        && blockers.some((row) => row.code === 'SHOT_STYLE_MISMATCH'));
      setProblem(describeError(error));
      onMessage?.(describeError(error), 'error');
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }, [acceleratorId, chosenVariantAvailable, onMessage, onNavigate, onRefresh,
    options.second_pass.supported, options.upscale, orientation,
    projectId, resolutionScale, secondPass, selected, styleLoras, upscale, variantId, running]);

  const applyStyle = async () => {
    setBusy('style'); setProblem('');
    try {
      const result = await applyCurrentStyleToShots(projectId, '导演包预检：镜头执行当前冻结风格');
      setStyleGap(false);
      onMessage?.(`已让 ${result.revised_shot_ids.length} 个镜头执行当前冻结风格，可重新确认拍摄方案。`, 'success');
      onRefresh?.();
    } catch (error) {
      setProblem(describeError(error));
      onMessage?.(describeError(error), 'error');
    } finally {
      setBusy('');
    }
  };

  const control = async (action: 'pause' | 'resume' | 'cancel' | 'reconcile') => {
    if (!run) return;
    setBusy(action); setProblem('');
    try {
      const result = await controlProductionRun(run.id, action);
      const fresh = await getProductionRun(run.id);
      setRun(fresh);
      onRefresh?.();
      if (action === 'pause') {
        onMessage?.(
          result.status === 'PAUSE_REQUESTED'
            ? '已请求暂停：等待当前镜头安全落盘后暂停，已产出镜头全部保留。'
            : '已暂停运行，已产出镜头全部保留。',
          'success',
        );
      } else if (action === 'resume') {
        if (result.status === 'NEEDS_REVIEW') {
          const msg = result.message || '当前批次没有可安全续拍的未提交镜头，请先处理阻塞项。';
          setProblem(msg);
          onMessage?.(msg, 'error');
        } else {
          onMessage?.('已恢复安全可恢复批次，后台 Worker 正在继续推进。', 'success');
        }
      } else if (action === 'cancel') {
        onMessage?.(
          result.status === 'CANCEL_REQUESTED'
            ? '已请求取消：等待当前镜头与受管资源收尾，已产出镜头全部保留。'
            : '已取消运行，已产出数据全部保留，可回「导演包」重新开拍。',
          'success',
        );
      } else if (action === 'reconcile') {
        onMessage?.('已按真实产物核对运行状态。', 'success');
      }
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  };

  // 开拍按钮的真实文案与真实禁用原因：指引与右卡都必须照着它说，
  // 否则会出现「指向一个页面上根本不存在的按钮」的假指引。
  const startLabel = selected.length
    ? `确认拍摄方案并开始真实制作（${selected.length} 集）` : '先选择要拍的集';
  const startBlockedReason = !chosenVariantAvailable ? chosenVariantReason
    : demo ? '当前是演示模式：先启用真实制作'
      : !selected.length ? '至少选择一集'
        : running ? '这次拍摄正在视频生产里进行，完成或取消后再开拍' : undefined;
  const primary: RailAction | null = mode === 'package' && !running
    ? { label: startLabel,
        onClick: () => void start(),
        loading: busy === 'start',
        disabled: busy !== '' || !selected.length || demo || !chosenVariantAvailable,
        disabledReason: startBlockedReason,
        icon: <Clapperboard size={14} /> }
    : null;
  // ⑥ 还没有运行时不留空卡：右栏必须说清这一步该做什么。
  const secondary: RailAction[] = mode === 'run' && !run && onNavigate
    ? [{ label: '去导演包选择要拍的集', onClick: () => onNavigate('package') }]
    : [];
  useStageRail(`director-shoot-${mode}`, { priority: 1, primary, secondary },
    [mode, selected, busy, demo, running, chosenVariantAvailable,
      variantId, acceleratorId, secondPass, upscale, run, orientation, resolutionScale, styleLoras]);

  const blockedEpisodes = episodes.filter((row) => !shootable.includes(row.episode_id));

  /* ---------------- ⑤ 导演包：只决定"拍哪几集、用什么方案" ---------------- */
  if (mode === 'package') {
    return <div className="shoot-panel">
      {demo && <div className="blocked-callout">
        <AlertTriangle size={14} />
        <span>当前是演示模式：不会生成真视频。先在顶部「启用真实制作」完成本机 H3 校验，再回来开拍。</span>
      </div>}

      {styleGap && <div className="required-action">
        <b><AlertTriangle size={13} /> 镜头还在执行旧一版冻结风格</b>
        <p>冻结新风格后，旧镜头要改执行当前冻结风格才能通过预检。这一步只改镜头的风格引用，不重拍、不删产物。</p>
        <button type="button" className="must-button" disabled={busy !== ''}
          onClick={() => void applyStyle()}>
          {busy === 'style' ? <Loader2 size={13} className="spin" /> : <ShieldCheck size={13} />}
          让镜头执行当前冻结风格
        </button>
      </div>}

      {running && <section className="card section-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">第 5 阶段 · 已经开拍的运行</div>
            <h2>这次拍摄正在视频生产里进行</h2>
            <p>进度、暂停和核对都在「视频生产」页；这里不再重复一份，避免两处按钮互相覆盖。</p>
          </div>
          <button type="button" className="btn-secondary" onClick={() => onNavigate?.('run')}>
            去视频生产看进度
          </button>
        </div>
      </section>}

      {packages.length > 0 && <section className="card section-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">第 5 阶段 · 导演包版本历史</div>
            <h2>每一版拍摄方案的配置记录</h2>
            <p>每次开拍冻结一版导演包；重拍递增新版，旧版保留可核对。版本台账在服务端，切页回来不丢。</p>
          </div>
          <span className="head-chip">{packages.length} 版</span>
        </div>
        <div className="pvh-list" aria-label="导演包版本历史">
          {packages.map((row) => <div key={row.id} className={`pvh-item${row.stale ? ' stale' : ''}`}>
            <span className="pvh-rev">第 {row.revision} 版</span>
            <span className={`head-chip ${row.status === 'CONFIRMED' ? 'ok' : row.stale ? 'warn' : ''}`}>
              {PACKAGE_STATUS_TEXT[row.status] ?? row.status}{row.stale ? ' · 失效' : ''}</span>
            <span className="pvh-config">
              {row.style ? `风格 v${row.style.version}` : '风格未冻结'}
              {' · '}{row.binding ? row.binding.mode : '方案未绑定'}
              {row.binding?.output?.width && row.binding?.output?.height
                ? ` · ${row.binding.output.width}×${row.binding.output.height}` : ''}
              {row.binding?.strategy?.style_loras?.length
                ? ` · LoRA(${row.binding.strategy.style_loras.map((l) => `${l.lora_name}@${l.strength_model}`).join('+')})`
                : ''}
              {' · '}{row.episode_count} 集 {row.shot_count} 镜
            </span>
            <span className="pvh-time">{versionTime(row.created_at)}{row.confirmed_at ? ' · 已确认' : ' · 待确认'}</span>
          </div>)}
        </div>
      </section>}

      <section className="card section-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">第 5 阶段 · 拍摄范围</div>
            <h2>这次要拍哪几集</h2>
            <p>只把准备好的集纳入本次拍摄；没准备好的集不参与，也不影响其他集。确认后系统会冻结这一版拍摄方案并直接开始真实制作。</p>
          </div>
          <span className={`head-chip ${selected.length ? 'ok' : ''}`}>{selected.length} 集已选</span>
        </div>
        <GuideTip>三步：勾选要拍的集 → 确认模型与画幅/分辨率 → 点{railPresent ? '右侧操作栏的' : '下方的'}「{startLabel}」
          {startBlockedReason ? `（当前不可点：${startBlockedReason}）` : ''}</GuideTip>

        <div className="shoot-episode-list">
          {episodes.map((episode) => {
            const selectable = shootable.includes(episode.episode_id);
            // 已交付/待确认的集仍然可以勾选（等于重拍），但必须让人看见代价。
            const reshoot = episode.state === 'DELIVERED' || episode.state === 'REVIEW_READY';
            return <label key={episode.episode_id} className={`shoot-episode ${selectable ? '' : 'disabled'}`}>
              <input type="checkbox" checked={selected.includes(episode.episode_id)}
                disabled={!selectable || running || busy !== ''}
                onChange={() => toggle(episode.episode_id)} />
              <span>
                <b>第 {episode.order ?? '—'} 集 · {episode.title || episode.episode_id}</b>
                <em>{episode.state_label}
                  {episode.shots.confirmed_count ? ` · ${episode.shots.confirmed_count} 镜` : ''}
                  {episode.materials.missing_count ? ` · 缺 ${episode.materials.missing_count} 项必需素材` : ''}
                  {reshoot ? ' · 勾选即重拍这一集' : ''}</em>
              </span>
            </label>;
          })}
        </div>

        {/* 生产策略：模型版本 / 加速 / 二次精修 / 高清视频。参考素材和每一镜的工作流由系统自动安排。 */}
        <div className="strategy-picker">
          <p className="strategy-note">
            选择模型和生产策略。Ref2VA 工作流会根据每镜已确认的参考素材生成执行图。
          </p>
          <div className="strategy-row" role="radiogroup" aria-label="模型版本">
            <span className="strategy-label">模型版本</span>
            {options.model_variants.map((row) => {
              const state = availability[row.variant_id];
              const ready = state?.available ?? false;
              // 可选择用于资源准备 ≠ 已就绪可开拍。未安装/候选模型仍可选中，
              // 选中后下方出现资源整备卡；开拍闸门另由 chosenVariantAvailable 把守。
              const selectable = state?.selectable_for_preparation ?? true;
              return <button key={row.variant_id} type="button" role="radio"
                aria-checked={variantId === row.variant_id}
                className={`strategy-option${variantId === row.variant_id ? ' active' : ''}${ready ? '' : ' not-ready'}`}
                disabled={!selectable || running || busy !== ''}
                title={ready ? row.label : state?.reason ?? '本机没有这套权重'}
                onClick={() => setVariantId(row.variant_id)}>
                {describeVariantName(row.variant_id)}
                {!ready && <em className="strategy-flag">{state?.reason ?? '本机没有'}</em>}
              </button>;
            })}
          </div>
          {(!chosenVariantAvailable || chosen?.acceptance_status === 'CANDIDATE') && <div className="notice">
            <p>{describeVariantName(variantId)}：{chosenVariantReason}。
              模型的检测、下载、登记与校验统一在「基础配置 · 安装与下载」完成，本页不另设下载入口，避免两处配置互相冲突；准备好后回来开拍。</p>
            {onGoSetup && <button type="button" className="btn-secondary" onClick={onGoSetup}>
              去基础配置准备模型
            </button>}
          </div>}
          <div className="strategy-row" role="radiogroup" aria-label="画幅">
            <span className="strategy-label">画幅</span>
            {(Object.keys(ORIENTATION_LABELS) as Orientation[]).map((key) => (
              <button key={key} type="button" role="radio"
                aria-checked={orientation === key}
                className={`strategy-option${orientation === key ? ' active' : ''}`}
                disabled={running || busy !== ''}
                onClick={() => setOrientation(key)}>
                {ORIENTATION_LABELS[key]}
              </button>
            ))}
          </div>
          <div className="strategy-row">
            <span className="strategy-label">分辨率</span>
            <select aria-label="分辨率档位" value={resolutionScale}
              disabled={running || busy !== ''}
              onChange={(event) => setResolutionScale(Number(event.target.value))}>
              {RESOLUTION_SCALES.map((scale) => {
                const frame = frameSize(orientation, scale);
                return <option key={scale} value={scale}>
                  {scaleLabel(scale)} · {frame.width}×{frame.height}
                </option>;
              })}
            </select>
          </div>
          <div className="strategy-row" role="radiogroup" aria-label="画面加速">
            <span className="strategy-label">画面加速</span>
            {options.accelerators.map((row) => (
              <button key={row.accelerator_id} type="button" role="radio"
                aria-checked={acceleratorId === row.accelerator_id}
                className={`strategy-option${acceleratorId === row.accelerator_id ? ' active' : ''}`}
                disabled={!row.verified || running || busy !== ''}
                title={row.verified ? undefined : (row.blockers?.[0]?.detail || row.unverified_reason || '这个加速配方本机还没就绪')}
                onClick={() => setAcceleratorId(row.accelerator_id)}>
                {describeAccelerator(row.accelerator_id, row.steps)}
                {!row.verified && <em className="strategy-flag">本机未就绪</em>}
              </button>
            ))}
          </div>
          <div className="strategy-row">
            <span className="strategy-label">二次精修</span>
            <label className="strategy-toggle">
              <input type="checkbox" checked={secondPass && options.second_pass.supported}
                disabled={!options.second_pass.supported || running || busy !== ''}
                title={options.second_pass.supported
                  ? undefined
                  : (options.second_pass.blockers?.[0]?.detail || options.second_pass.blockers?.[0]?.code || '本机还没就绪')}
                onChange={(event) => setSecondPass(event.target.checked && options.second_pass.supported)} />
              <span>启用（第二次采样精修画面，成片更细）{options.second_pass.supported ? '' : '（本机还没就绪）'}</span>
            </label>
          </div>
          <div className="strategy-row">
            <span className="strategy-label">高清视频</span>
            {options.upscale.length === 0 && (
              <label className="strategy-toggle">
                <input type="checkbox" checked={upscale} disabled
                  onChange={() => setUpscale(false)} />
                <span>RelayVSR 2 倍超分（本机还没就绪）</span>
              </label>
            )}
            {options.upscale.map((row) => (
              <label key={row.engine} className="strategy-toggle">
                <input type="checkbox" checked={upscale && row.enabled}
                  disabled={!row.enabled || running || busy !== ''}
                  title={row.enabled ? undefined : (row.blockers[0]?.detail || row.blockers[0]?.code || '暂不可用')}
                  onChange={(event) => setUpscale(event.target.checked && row.enabled)} />
                <span>RelayVSR 2 倍超分{row.enabled ? '' : '（本机还没就绪）'}</span>
              </label>
            ))}
          </div>
          <StyleLoraAssemblyPanel
            value={styleLoras}
            onChange={setStyleLoras}
            loraSupported={selectedVariantLoraSupported}
            disabled={running || busy !== ''}
          />
        </div>

        {problem && <p role="alert" className="form-error">{problem}</p>}
        {!railPresent && <button type="button" className="btn-primary"
          disabled={!selected.length || demo || busy !== '' || !chosenVariantAvailable || running}
          title={startBlockedReason}
          onClick={() => void start()}>
          <Clapperboard size={14} /> 确认拍摄方案并开始真实制作
        </button>}

        {/* 没有可拍的集时，必须说清下一步在哪里，而不是只留一个灰色按钮。 */}
        {pendingShoot.length === 0 && blockedEpisodes.length > 0 && <p className="shoot-empty-hint">
          {episodes.every((row) => row.state === 'DELIVERED')
            ? '这部作品已经全部交付：到「后期成片」逐集播放确认即可。'
            : '这次没有可开拍的集：先到「镜头拆解」把待修复的集修好，或到「拍摄素材准备」补齐必需素材，再回到这一页。'}
        </p>}

        {blockedEpisodes.length > 0 && <details className="advanced-fold">
          <summary>{blockedEpisodes.length} 集这次还不能拍（点击看原因）</summary>
          <ul className="shoot-blocked-list">
            {blockedEpisodes.map((episode) => <li key={episode.episode_id}>
              <b>第 {episode.order ?? '—'} 集</b>
              <span>{episode.state_label}</span>
              {episode.next_action && <em>{episode.next_action.label}</em>}
            </li>)}
          </ul>
        </details>}
      </section>
    </div>;
  }

  /* ---------------- ⑥ 视频生产：只读进度与运行控制 ---------------- */
  return <div className="shoot-panel">
    {run && <section className="card section-card run-progress" aria-label="视频生产进度">
      <div className="run-progress-head">
        <b>{productionDone ? '本阶段生产完成' : `正在生成第 ${currentShotNo} / ${totalShots} 镜`}</b>
        <span className="run-progress-pct">{progressPct}%</span>
      </div>
      <div className="run-progress-bar" role="progressbar" aria-valuenow={progressPct}
        aria-valuemin={0} aria-valuemax={100} aria-label="视频生产进度">
        <i style={{ width: `${progressPct}%` }} className={barWorking ? 'is-working' : ''} />
      </div>
      <p className="run-progress-note">
        {productionDone
          ? `${totalShots} 镜全部通过。到「后期成片」逐集播放并确认。`
          : decisionJobs.length > 0
            ? `已通过 ${passedShots} / ${totalShots} 镜；有镜头的结果未知，等待你的决定，其余已通过镜头不受影响。`
            : `已通过 ${passedShots} / ${totalShots} 镜${activeJob ? `，正在渲染「${activeJob.shot_name}」` : ''}。后台 Worker 持续推进，页面跟读同一份台账。`}
      </p>
    </section>}
    {activeJob && (!run || !RUN_RENDERING.includes(run.status)) && <section className="card section-card run-progress" aria-label="单镜重拍进度">
      <div className="run-progress-head">
        <b>{activeJob.status === 'PASSED' ? '重拍已通过质检' : `正在重拍「${activeJob.shot_name}」`}</b>
        <span className="run-progress-pct">{retakePct}%</span>
      </div>
      <div className="run-progress-bar" role="progressbar" aria-valuenow={retakePct}
        aria-valuemin={0} aria-valuemax={100} aria-label="单镜重拍进度">
        <i style={{ width: `${retakePct}%` }} className={activeJob.status !== 'PASSED' ? 'is-working' : ''} />
      </div>
      <p className="run-progress-note">
        {activeJob.status === 'PASSED'
          ? '到「后期成片」把新版本选为当前装配版本，再重新生成字幕并装配。'
          : '单镜重拍独立于整片运行；进度读同一份任务台账，通过后由你选用。'}
      </p>
    </section>}
    <section className="card section-card">
      <div className="section-heading">
        <div>
          <div className="section-kicker">第 6 阶段 · 真实拍摄</div>
          <h2>{run ? runHeading(run.status) : '还没有在拍的运行'}</h2>
          <p>拍摄由常驻 Worker 推进，关掉浏览器也会继续；每一镜的产物、质检和成片都落在这台电脑上。</p>
        </div>
        {run && <span className={`head-chip ${run.status === 'COMPLETED' ? 'ok' : ''}`}>{run.status}</span>}
      </div>

      {!run && <div className="empty-inline">
        还没有运行。到「导演包」选择要拍的集并确认拍摄方案，开拍后这里会逐集显示进度。
        {onNavigate && <button type="button" className="btn-text" onClick={() => onNavigate('package')}>去导演包</button>}
        <GuideTip tight>第一次开拍：先在「导演包」勾选集数并确认方案</GuideTip>
      </div>}
      {run && <>
        <div className="shoot-run-facts">
          <span>本次范围：{run.scope.episode_ids.length} 集 · {run.scope.shot_count} 镜</span>
          <span>已通过：{run.progress.passed_shot_count} 镜</span>
          <span>已交付：{run.progress.delivered_episode_count}/{run.progress.required_episode_count} 集</span>
        </div>
        {run.blocker_count > 0 && <p className="run-hint">
          <AlertTriangle size={12} /> 有 {run.blocker_count} 处阻塞，见下方集卡与⑦成片页。
        </p>}
        {decisionJobs.length > 0 && !['COMPLETED', 'CANCELLED'].includes(run.status) && (
          <div className="required-action">
            <b><AlertTriangle size={13} /> 拍摄停在等待你决定的镜头</b>
            <p>渲染器已不在，系统不会自动重投。放弃只结束这一次任务（产物保留）；之后到⑦重拍该镜并选用，整集继续。</p>
            {decisionJobs.map((job) => (
              <div key={job.id} className="repair-actions">
                <span>「{job.shot_name}」{job.status === 'NEEDS_REVIEW' ? '结果未知' : '已放弃未知结果'}</span>
                {job.status === 'NEEDS_REVIEW' && onAbandon && (
                  <button type="button" className="must-button" disabled={busy !== ''}
                    onClick={() => onAbandon(job.id)}>放弃未知结果（保留产物，不重投）</button>
                )}
                {onNavigate && (
                  <button type="button" className="btn-secondary" disabled={busy !== ''}
                    onClick={() => onNavigate('deliver')}>去⑦重拍此镜</button>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="shoot-controls">
          {run.status === 'RUNNING' || run.status === 'QUEUED'
            ? <button type="button" className="btn-secondary" disabled={busy !== ''}
                onClick={() => void control('pause')}>{busy === 'pause'
                  ? <Loader2 size={13} className="spin" /> : <Square size={13} />} 暂停</button>
            : null}
          {(run.status === 'PAUSED' || run.status === 'PAUSE_REQUESTED') && <button type="button" className="btn-secondary" disabled={busy !== ''}
            onClick={() => void control('resume')}><Play size={13} /> 继续</button>}
          {(running || run.status === 'NEEDS_REVIEW') && <button type="button" className="btn-text"
            disabled={busy !== ''}
            onClick={() => void control('reconcile')}><ShieldCheck size={13} /> 核对真实产物</button>}
          {run.status === 'NEEDS_REVIEW' && <button type="button" className="btn-secondary"
            disabled={busy !== ''}
            title="只恢复服务端能够证明尚未提交到 H3 的批次；其他阻塞保持待处理"
            onClick={() => void control('resume')}><Play size={13} /> 继续安全可恢复批次</button>}
          {!['COMPLETED', 'CANCELLED', 'FAILED'].includes(run.status) && <ArmedButton
            label="取消" armedLabel="再点一次确认取消" disabled={busy !== ''}
            icon={<Square size={13} />} onConfirm={() => void control('cancel')} />}
          <button type="button" className="btn-text" disabled={busy !== ''}
            onClick={() => { onRefresh?.(); void (run && getProductionRun(run.id).then(setRun).catch(() => undefined)); }}>
            <RefreshCw size={13} /> 刷新进度
          </button>
        </div>
        {problem && <p role="alert" className="form-error">{problem}</p>}
      </>}
    </section>

    <section className="card section-card">
      <div className="section-heading">
        <div>
          <div className="section-kicker">按集进度</div>
          <h2>每一集走到哪一步</h2>
        </div>
      </div>
      <EpisodeCards episodes={episodes} compact />
      <p className="run-hint">
        <Film size={12} /> 成片在「后期成片」逐集播放；技术完成不等于艺术认可。
        {overview?.counts.delivered_episode_count ? ` 已交付 ${overview.counts.delivered_episode_count} 集。` : ''}
      </p>
      <p className="run-hint"><Check size={12} /> 已通过的镜头不会被重拍；返修只影响你选中的镜头和它所在的集。</p>
    </section>
  </div>;
}
