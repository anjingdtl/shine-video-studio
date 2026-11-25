import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, CheckCircle2, Clapperboard, RefreshCw } from 'lucide-react';
import { SETUP_CAPABILITY_LABEL } from '../api';
import './styles/manual4.css';
import {
  fetchM4Overview,
  fetchM4SystemStatus,
  type M4Overview,
  type M4StageKey,
  type M4SystemStatus,
} from './api/client';
import { StoryboardStagePage } from './pages/StoryboardStagePage';
import { MaterialsStagePage } from './pages/MaterialsStagePage';
import { ScheduleProduceStagePage } from './pages/ScheduleProduceStagePage';
import { DeliveryStagePage } from './pages/DeliveryStagePage';

type Props = {
  projectId: string;
  initialStage?: M4StageKey;
  onBackToProjects: () => void;
  onMessage?: (text: string, kind?: 'success' | 'error') => void;
};

const STAGE_ORDER: M4StageKey[] = ['storyboard', 'materials', 'production', 'delivery'];

const STAGE_META_LABELS: Record<M4StageKey, string> = {
  storyboard: '导入/编辑集镜表',
  materials: '候选/晋升/绑定',
  production: 'H3 原文/批量开拍',
  delivery: '装配/预览/验收',
};

const EXEC_PHASES: Array<{ key: string; label: string; phases: string[] }> = [
  { key: 'claim', label: '1. 认领与启动', phases: ['WAITING_WORKER', 'STARTING_RENDERER', 'PREPARING_NEXT'] },
  { key: 'submit', label: '2. 提交 H3', phases: ['SUBMITTING'] },
  { key: 'render', label: '3. H3 渲染', phases: ['RENDERING'] },
  { key: 'qc', label: '4. 技术质检', phases: ['QC'] },
  { key: 'done', label: '5. 完成入库', phases: ['COMPLETED'] },
];

const SCOPE_LABELS: Record<string, string> = {
  project: '全片排片',
  episode: '按集排片',
  shot: '单镜重拍',
};

/** Independent four-stage H3 autonomous production line shell.
 *  Reads ONLY `/api/v1/manual4/*` projections and never invokes seven-stage APIs. */
export function Manual4App({ projectId, initialStage, onBackToProjects, onMessage }: Props) {
  const [overview, setOverview] = useState<M4Overview | null>(null);
  const [sysStatus, setSysStatus] = useState<M4SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [activeStage, setActiveStage] = useState<M4StageKey>(initialStage ?? 'storyboard');
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const autoLandedProjectRef = useRef<string | null>(null);

  const refresh = useCallback(async (opts?: { silent?: boolean }) => {
    const silent = Boolean(opts?.silent);
    if (!silent) {
      setLoading(true);
      setLoadError('');
    }
    try {
      const [data, status] = await Promise.all([
        fetchM4Overview(projectId),
        fetchM4SystemStatus().catch(() => null),
      ]);
      setOverview(data);
      if (status) setSysStatus(status);
      if (!silent && !initialStage && autoLandedProjectRef.current !== projectId) {
        autoLandedProjectRef.current = projectId;
        setActiveStage(data.readiness.next_stage);
        window.history.replaceState(null, '', `#m4/${data.readiness.next_stage}`);
      }
    } catch (error) {
      if (!silent) {
        const msg = error instanceof Error ? error.message : '读取四阶概览失败';
        setLoadError(msg);
        onMessageRef.current?.(msg, 'error');
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [projectId, initialStage]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (initialStage) setActiveStage(initialStage);
  }, [initialStage]);

  const progress = overview?.production_progress ?? null;
  const shouldPollProgress = Boolean(progress?.is_working || overview?.gpu.busy);

  useEffect(() => {
    if (!shouldPollProgress) return;
    const timer = window.setInterval(() => {
      void refresh({ silent: true });
    }, 3000);
    return () => window.clearInterval(timer);
  }, [shouldPollProgress, refresh]);

  const stages = overview?.readiness.stages ?? [];
  const stageByKey = Object.fromEntries(stages.map((s) => [s.key, s]));
  const environment = sysStatus?.environment;
  const h3 = SETUP_CAPABILITY_LABEL[environment?.capabilities.h3?.state ?? 'UNKNOWN'];
  const image = SETUP_CAPABILITY_LABEL[environment?.capabilities.image?.state ?? 'UNKNOWN'];
  const comfyUI = environment?.services_verified ? '已就绪' : environment?.comfyui_root ? '待核验' : '待配置';

  const selectStage = (key: M4StageKey) => {
    autoLandedProjectRef.current = projectId;
    setActiveStage(key);
    window.history.replaceState(null, '', `#m4/${key}`);
  };

  return (
    <div className="m4-shell" data-testid="manual4-workbench">
      <header className="m4-header-card">
        <div className="m4-header-top">
          <div className="m4-title-group">
            <span className="m4-eyebrow">
              <Clapperboard size={13} aria-hidden="true" />
              H3 自主制片 · 四阶段独立生产线
            </span>
            <h1 className="m4-title">{overview?.project.title ?? '正在读取四阶作品…'}</h1>
            <p className="m4-subtitle">
              {environment
                ? `ComfyUI ${comfyUI} · H3 ${h3} · Qwen 出图${image}`
                : loading ? '正在读取本机基础环境…' : '本机基础环境状态读取失败'}
              {' · '}Worker {overview ? (overview.worker_online ? '在线' : '待启动') : '状态未知'}
            </p>
          </div>
          <div className="m4-header-actions">
            <span className={`m4-badge ${overview?.readiness.all_done ? 'ok' : 'busy'}`}>
              {overview ? (overview.readiness.all_done ? '全四阶已完成' : `当前建议：${stageByKey[overview.readiness.next_stage]?.title ?? overview.readiness.next_stage}`) : '检查中'}
            </span>
            <button type="button" className="btn-secondary" onClick={() => void refresh()} disabled={loading}>
              <RefreshCw size={14} aria-hidden="true" /> 刷新四阶状态
            </button>
            <button type="button" className="btn-secondary" onClick={onBackToProjects}>
              <ArrowLeft size={14} aria-hidden="true" /> 返回项目管理
            </button>
          </div>
        </div>

        <nav className="m4-stage-nav" aria-label="四阶段导航">
          {STAGE_ORDER.map((key) => {
            const summary = stageByKey[key];
            const isActive = activeStage === key;
            const statusText = summary?.done
              ? '已完成'
              : summary?.status === 'active'
                ? '进行中'
                : summary && summary.blocker_count > 0
                  ? `${summary.blocker_count} 项待处理`
                  : '待前置';
            return (
              <button
                key={key}
                type="button"
                className={`m4-stage-tab ${isActive ? 'active' : ''} ${summary?.done ? 'done' : ''}`}
                onClick={() => selectStage(key)}
              >
                <span className="m4-stage-tab-head">
                  <span>{summary?.title ?? key}</span>
                  <span className="m4-stage-status">
                    {summary?.done && <CheckCircle2 size={12} aria-hidden="true" />} {statusText}
                  </span>
                </span>
                <span className="m4-stage-meta">{STAGE_META_LABELS[key]}</span>
              </button>
            );
          })}
        </nav>
      </header>

      {loadError && (
        <div className="m4-blocker-box" role="alert">
          <div>读取四阶状态失败：{loadError}</div>
          <div>
            <button type="button" className="btn-secondary" onClick={() => void refresh()}>
              重试读取
            </button>
          </div>
        </div>
      )}

      {progress && (
        <section className="m4-run-progress" aria-label="四阶视频生产进度">
          <div className="m4-run-progress-head">
            <div className="m4-toolbar">
              <strong>
                {progress.status === 'COMPLETED'
                  ? `本批次生产完成（${progress.passed_jobs} / ${progress.total_jobs} 镜全部通过）`
                  : progress.is_working
                    ? `正在生成第 ${progress.current_shot_no} / ${progress.total_jobs} 镜${progress.active_job ? `（${progress.active_job.shot_code}）` : ''}`
                    : `排片批次 ${progress.status} · 已通过 ${progress.passed_jobs} / ${progress.total_jobs} 镜`}
              </strong>
              <span className={`m4-badge ${progress.status === 'COMPLETED' ? 'ok' : progress.is_working ? 'busy' : 'warn'}`}>
                {progress.phase_label}
              </span>
            </div>
            <span className="m4-run-progress-pct">{progress.progress_pct}%</span>
          </div>

          <div
            className="m4-run-progress-bar"
            role="progressbar"
            aria-valuenow={progress.progress_pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="四阶视频生产进度"
          >
            <i
              style={{ width: `${progress.progress_pct}%` }}
              className={progress.is_working ? 'is-working' : ''}
            />
          </div>

          <div className="m4-phase-steps" aria-label="当前生产阶段">
            {EXEC_PHASES.map((step, idx) => {
              const activeIdx = EXEC_PHASES.findIndex((p) => p.phases.includes(progress.phase));
              const isDone = progress.status === 'COMPLETED' || (activeIdx >= 0 && idx < activeIdx);
              const isCurrent = progress.status !== 'COMPLETED' && activeIdx === idx;
              return (
                <span
                  key={step.key}
                  className={`m4-phase-step ${isCurrent ? 'active' : isDone ? 'done' : ''}`}
                >
                  {step.label}
                </span>
              );
            })}
          </div>

          <div className="m4-ep-head">
            <span className="m4-footer-note">
              {SCOPE_LABELS[progress.scope_kind] ?? progress.scope_kind} · 已通过 {progress.passed_jobs} / {progress.total_jobs} 镜
              {progress.queued_jobs > 0 ? ` · 排队 ${progress.queued_jobs}` : ''}
              {progress.failed_jobs > 0 ? ` · 失败 ${progress.failed_jobs}` : ''}
              {progress.needs_review_jobs > 0 ? ` · 待核对 ${progress.needs_review_jobs}` : ''}
              {' · 后台 Worker 持续推进，页面跟读同一份台账。'}
            </span>
            <div className="m4-toolbar">
              {activeStage !== 'production' && (
                <button type="button" className="btn-secondary" onClick={() => selectStage('production')}>
                  查看③排片详情
                </button>
              )}
              {progress.passed_jobs === progress.total_jobs && activeStage !== 'delivery' && (
                <button type="button" className="btn-primary" onClick={() => selectStage('delivery')}>
                  去④合成成片
                </button>
              )}
            </div>
          </div>
        </section>
      )}

      {overview && activeStage === 'storyboard' && (
        <StoryboardStagePage
          overview={overview}
          stage={stageByKey.storyboard}
          onRefresh={refresh}
          onNextStage={() => selectStage('materials')}
          onMessage={onMessage}
        />
      )}
      {overview && activeStage === 'materials' && (
        <MaterialsStagePage
          overview={overview}
          stage={stageByKey.materials}
          onRefresh={refresh}
          onNextStage={() => selectStage('production')}
          onMessage={onMessage}
        />
      )}
      {overview && activeStage === 'production' && (
        <ScheduleProduceStagePage
          overview={overview}
          stage={stageByKey.production}
          onRefresh={refresh}
          onNextStage={() => selectStage('delivery')}
          onMessage={onMessage}
        />
      )}
      {overview && activeStage === 'delivery' && (
        <DeliveryStagePage
          overview={overview}
          stage={stageByKey.delivery}
          onRefresh={refresh}
          onMessage={onMessage}
        />
      )}
    </div>
  );
}

export default Manual4App;
