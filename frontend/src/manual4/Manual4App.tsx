import { useCallback, useEffect, useState } from 'react';
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

/** Independent four-stage H3 autonomous production line shell.
 *  Reads ONLY `/api/v1/manual4/*` projections and never invokes seven-stage APIs. */
export function Manual4App({ projectId, initialStage, onBackToProjects, onMessage }: Props) {
  const [overview, setOverview] = useState<M4Overview | null>(null);
  const [sysStatus, setSysStatus] = useState<M4SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [activeStage, setActiveStage] = useState<M4StageKey>(initialStage ?? 'storyboard');

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const [data, status] = await Promise.all([
        fetchM4Overview(projectId),
        fetchM4SystemStatus().catch(() => null),
      ]);
      setOverview(data);
      setSysStatus(status);
      if (!initialStage) {
        setActiveStage(data.readiness.next_stage);
        window.history.replaceState(null, '', `#m4/${data.readiness.next_stage}`);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : '读取四阶概览失败';
      setLoadError(msg);
      onMessage?.(msg, 'error');
    } finally {
      setLoading(false);
    }
  }, [projectId, initialStage, onMessage]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (initialStage) setActiveStage(initialStage);
  }, [initialStage]);

  const stages = overview?.readiness.stages ?? [];
  const stageByKey = Object.fromEntries(stages.map((s) => [s.key, s]));
  const environment = sysStatus?.environment;
  const h3 = SETUP_CAPABILITY_LABEL[environment?.capabilities.h3?.state ?? 'UNKNOWN'];
  const image = SETUP_CAPABILITY_LABEL[environment?.capabilities.image?.state ?? 'UNKNOWN'];
  const comfyUI = environment?.services_verified ? '已就绪' : environment?.comfyui_root ? '待核验' : '待配置';

  const selectStage = (key: M4StageKey) => {
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
