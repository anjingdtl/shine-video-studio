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
  controlProductionRun,
  getProductionRun,
  getStrategyOptions,
  getVariantAvailability,
  startDirectorProduction,
  type DirectorEpisodeOverview,
  type DirectorOverview,
  type ProductionRun,
  type ProductionStrategy,
  type StrategyOptions,
  type VariantAvailability,
} from '../api';
import { useRailPresent, useStageRail } from './StageRail';
import type { RailAction } from './ActionRail';
import { StrategyResourceCard } from './StrategyResourceCard';
import { GuideTip } from './GuideTip';

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return String(error);
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
            : <span className="muted">{episode.next_action?.label ?? '等待下一步'}</span>}
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
  if (/lowend|w6a8/.test(lower)) return '低显存候选 · W6A8';
  if (/nvfp4/.test(lower)) return '低显存候选 · NVFP4';
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

/** 服务端读不到选项时的最保守回退：只给已验收的 INT8 + PDD8，其余不可选。 */
const FALLBACK_OPTIONS: StrategyOptions = {
  model_variants: [{
    variant_id: 'h3-ref2va-int8-convrot', label: 'h3-ref2va-int8-convrot',
    acceptance_status: 'SUPPORTED', verified: true, default_recommended: true,
  }],
  accelerators: [
    { accelerator_id: 'pdd8', steps: 8, verified: true },
    { accelerator_id: 'native20', steps: 20, verified: true },
  ],
  second_pass: { supported: true, recipes: ['h3-tail-refine-v1'] },
  upscale: [],
};

export function DirectorShootPanel({ projectId, overview, demo, mode, onMessage, onRefresh, onNavigate }: {
  projectId: string;
  overview: DirectorOverview | null;
  demo: boolean;
  /** package：第⑤步只做拍摄方案与开拍；run：第⑥步只看真实生产进度。 */
  mode: 'package' | 'run';
  onMessage?: (message: string, tone?: 'success' | 'error') => void;
  onRefresh?: () => void;
  onNavigate?: (page: 'package' | 'run') => void;
}) {
  const railPresent = useRailPresent();
  const [selected, setSelected] = useState<string[]>([]);
  const [options, setOptions] = useState<StrategyOptions>(FALLBACK_OPTIONS);
  const [variantId, setVariantId] = useState('h3-ref2va-int8-convrot');
  const [acceleratorId, setAcceleratorId] = useState('pdd8');
  const [secondPass, setSecondPass] = useState(false);
  const [upscale, setUpscale] = useState(false);
  const [busy, setBusy] = useState('');
  const [problem, setProblem] = useState('');
  const [run, setRun] = useState<ProductionRun | null>(null);
  const [touched, setTouched] = useState(false);
  /** 基础配置里已经准备好的方案：有就点亮，没有就灰色。但「未就绪」不等于
   *  「不可选择」——候选模型可以被选中进入资源准备，只是不能开拍。 */
  const [availability, setAvailability] = useState<Record<string, VariantAvailability>>({});
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
  useEffect(() => {
    let cancelled = false;
    getStrategyOptions(projectId)
      .then((rows) => { if (!cancelled) setOptions(rows); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [projectId]);

  const activeRunId = overview?.run?.run_id ?? null;
  useEffect(() => {
    if (!activeRunId) { setRun(null); return; }
    let cancelled = false;
    getProductionRun(activeRunId)
      .then((row) => { if (!cancelled) setRun(row); })
      .catch(() => { if (!cancelled) setRun(null); });
    return () => { cancelled = true; };
  }, [activeRunId]);

  // 模型版本状态直接来自基础配置的台账：不在这里另算一套「验收」。
  useEffect(() => {
    let cancelled = false;
    getVariantAvailability(projectId)
      .then((rows) => {
        if (cancelled) return;
        setAvailability(Object.fromEntries(rows.map((row) => [row.variant_id, row])));
        // 默认选第一个已就绪的方案；但用户已经选定的方案（哪怕未就绪、需要
        // 准备资源）保持不变，不被强行切回 INT8。
        setVariantId((current) => {
          if (rows.some((row) => row.variant_id === current)) return current;
          const ready = rows.find((row) => row.available);
          return (ready ?? rows[0])?.variant_id ?? current;
        });
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [projectId]);

  const chosen = availability[variantId];
  const chosenVariantAvailable = chosen?.available ?? false;
  const chosenVariantReason = chosen?.reason ?? '本机没有这套权重';
  const chosenSelectable = chosen?.selectable_for_preparation ?? true;
  const running = Boolean(run && ['DRAFT', 'QUEUED', 'RUNNING', 'PAUSED', 'BLOCKED'].includes(run.status));

  const toggle = (episodeId: string) => {
    setTouched(true);
    setSelected((current) => current.includes(episodeId)
      ? current.filter((item) => item !== episodeId)
      : [...current, episodeId]);
  };

  const start = useCallback(async () => {
    if (submitting.current || !selected.length || !chosenVariantAvailable) return;
    submitting.current = true;
    setBusy('start'); setProblem('');
    try {
      const strategy: ProductionStrategy = {
        schema_version: '1',
        generation_mode: 'Ref2VA',
        model_variant_id: variantId,
        accelerator_id: acceleratorId,
        second_pass: { enabled: secondPass, recipe_id: 'h3-tail-refine-v1', denoise: 0.25, latent_scale: 1.0 },
        upscale: upscale ? { enabled: true, engine: 'relayvsr', variant: 'x2', quality_preset: 'balanced' }
          : { enabled: false },
      };
      const result = await startDirectorProduction(projectId, {
        episode_ids: selected,
        strategy,
      });
      const shotCount = result.episodes.reduce((sum, row) => sum + row.shot_count, 0);
      onMessage?.(result.created
        ? `已开始真实制作：第 ${result.episodes.map((row) => row.position).join('、')} 集，系统已为 ${shotCount} 镜自动安排参考素材和生成策略。`
        : '这次选择已经开拍，返回了同一次运行，没有重复启动。', 'success');
      onRefresh?.();
      onNavigate?.('run');
    } catch (error) {
      setProblem(describeError(error));
      onMessage?.(describeError(error), 'error');
    } finally {
      submitting.current = false;
      setBusy('');
    }
  }, [acceleratorId, chosenVariantAvailable, onMessage, onNavigate, onRefresh, projectId, secondPass, selected, upscale, variantId]);

  const control = async (action: 'pause' | 'resume' | 'cancel' | 'reconcile') => {
    if (!run) return;
    setBusy(action); setProblem('');
    try {
      await controlProductionRun(run.id, action);
      const fresh = await getProductionRun(run.id);
      setRun(fresh);
      onRefresh?.();
    } catch (error) {
      setProblem(describeError(error));
    } finally {
      setBusy('');
    }
  };

  const primary: RailAction | null = mode === 'package' && !running
    ? { label: selected.length ? `确认拍摄方案并开始真实制作（${selected.length} 集）` : '先选择要拍的集',
        onClick: () => void start(),
        loading: busy === 'start',
        disabled: busy !== '' || !selected.length || demo || !chosenVariantAvailable,
        disabledReason: !chosenVariantAvailable ? chosenVariantReason : demo ? '当前是演示模式：先启用真实制作'
          : !selected.length ? '至少选择一集' : undefined,
        icon: <Clapperboard size={14} /> }
    : null;
  useStageRail(`director-shoot-${mode}`, { priority: 1, primary }, [mode, selected, busy, demo, running, chosenVariantAvailable]);

  const blockedEpisodes = episodes.filter((row) => !shootable.includes(row.episode_id));

  /* ---------------- ⑤ 导演包：只决定"拍哪几集、用什么方案" ---------------- */
  if (mode === 'package') {
    return <div className="shoot-panel">
      {demo && <div className="blocked-callout">
        <AlertTriangle size={14} />
        <span>当前是演示模式：不会生成真视频。先在顶部「启用真实制作」完成本机 H3 校验，再回来开拍。</span>
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

      <section className="card section-card">
        <div className="section-heading">
          <div>
            <div className="section-kicker">第 5 阶段 · 拍摄范围</div>
            <h2>这次要拍哪几集</h2>
            <p>只把准备好的集纳入本次拍摄；没准备好的集不参与，也不影响其他集。确认后系统会冻结这一版拍摄方案并直接开始真实制作。</p>
          </div>
          <span className={`head-chip ${selected.length ? 'ok' : ''}`}>{selected.length} 集已选</span>
        </div>
        <GuideTip>三步：勾选要拍的集 → 确认模型版本 → 点底部「确认拍摄方案并开始真实制作」</GuideTip>

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
            系统会按每一镜的分镜和已确认素材自动安排参考图/音频与生成流程；这里只需要选模型与生产策略。
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
          {!chosenVariantAvailable && <p className="strategy-note">
            {describeVariantName(variantId)}：{chosenVariantReason}。
            {chosenSelectable ? '可在下方「模型资源检测」里扫描本机、授权下载并校验；准备完成前不能开拍。'
              : '到「基础配置 · 安装与下载」准备后再回来开拍。'}
          </p>}
          {chosenSelectable && (!chosenVariantAvailable || chosen?.acceptance_status === 'CANDIDATE') && <StrategyResourceCard
            key={`${projectId}-${variantId}-${acceleratorId}-${secondPass}-${upscale}`}
            projectId={projectId} strategy={{ schema_version: '1', generation_mode: 'Ref2VA',
              model_variant_id: variantId, accelerator_id: acceleratorId,
              second_pass: { enabled: secondPass }, upscale: { enabled: upscale } }} />}
          <div className="strategy-row" role="radiogroup" aria-label="画面加速">
            <span className="strategy-label">画面加速</span>
            {options.accelerators.map((row) => (
              <button key={row.accelerator_id} type="button" role="radio"
                aria-checked={acceleratorId === row.accelerator_id}
                className={`strategy-option${acceleratorId === row.accelerator_id ? ' active' : ''}`}
                disabled={!row.verified || running || busy !== ''}
                title={row.verified ? undefined : '这个加速配方本机还没就绪'}
                onClick={() => setAcceleratorId(row.accelerator_id)}>
                {describeAccelerator(row.accelerator_id, row.steps)}
                {!row.verified && <em className="strategy-flag">本机未就绪</em>}
              </button>
            ))}
          </div>
          <div className="strategy-row">
            <span className="strategy-label">二次精修</span>
            <label className="strategy-toggle">
              <input type="checkbox" checked={secondPass} disabled={running || busy !== ''}
                onChange={(event) => setSecondPass(event.target.checked)} />
              <span>启用（第二次采样精修画面，成片更细）</span>
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
        </div>

        {problem && <p role="alert" className="form-error">{problem}</p>}
        {!railPresent && <button type="button" className="btn-primary" disabled={!selected.length || demo || busy !== '' || !chosenVariantAvailable}
          title={chosenVariantAvailable ? undefined : chosenVariantReason}
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
    <section className="card section-card">
      <div className="section-heading">
        <div>
          <div className="section-kicker">第 6 阶段 · 真实拍摄</div>
          <h2>{running ? '后台正在逐镜生成'
          : run ? (run.status === 'COMPLETED' ? '这次运行已经完成' : `运行${run.status === 'CANCELLED' ? '已取消' : '已停止'}`)
            : '还没有在拍的运行'}</h2>
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
        <div className="shoot-controls">
          {run.status === 'RUNNING' || run.status === 'QUEUED'
            ? <button type="button" className="btn-secondary" disabled={busy !== ''}
                onClick={() => void control('pause')}>{busy === 'pause'
                  ? <Loader2 size={13} className="spin" /> : <Square size={13} />} 暂停</button>
            : null}
          {run.status === 'PAUSED' && <button type="button" className="btn-secondary" disabled={busy !== ''}
            onClick={() => void control('resume')}><Play size={13} /> 继续</button>}
          {(running || run.status === 'NEEDS_REVIEW') && <button type="button" className="btn-text"
            disabled={busy !== ''}
            onClick={() => void control('reconcile')}><ShieldCheck size={13} /> 核对真实产物</button>}
          {run.status === 'NEEDS_REVIEW' && <button type="button" className="btn-secondary"
            disabled={busy !== ''}
            title="只恢复服务端能够证明尚未提交到 H3 的批次；其他阻塞保持待处理"
            onClick={() => void control('resume')}><Play size={13} /> 继续安全可恢复批次</button>}
          <button type="button" className="btn-text" disabled={busy !== ''}
            onClick={() => { onRefresh?.(); void (run && getProductionRun(run.id).then(setRun).catch(() => undefined)); }}>
            <RefreshCw size={13} /> 刷新进度
          </button>
        </div>
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
