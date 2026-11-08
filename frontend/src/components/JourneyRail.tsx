import { AlertCircle, ArrowLeft, ArrowRight, Check, ChevronRight, Loader2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type JourneyStageView = {
  id: string;
  text: string;
  /** 轨道上的短名；完整名称仍用于无障碍标签与提示。 */
  short?: string;
  icon: LucideIcon;
  page: string;
  hint: string;
};

type RailProps = {
  stages: JourneyStageView[];
  /** 当前页面；一个页面可能承载两个阶段（剧本与素材）。 */
  currentPage: string;
  /** 当前页面对应、且最该被高亮的那一个阶段。 */
  activeStageId: string | null;
  done: Record<string, boolean>;
  blockers: Record<string, number>;
  nextStage: JourneyStageView | null;
  state: 'idle' | 'loading' | 'ready' | 'error';
  error?: string;
  onNavigate: (stage: JourneyStageView) => void;
  onRetry?: () => void;
};

/**
 * 导演模式唯一的阶段导航：一条轨道说明“我在哪、还剩什么、下一步去哪”。
 * 完成态只来自服务端 /journey；这里只负责呈现，不自行推断。
 */
export function JourneyRail({
  stages, currentPage, activeStageId, done, blockers, nextStage, state, error, onNavigate, onRetry,
}: RailProps) {
  const doneCount = stages.filter((stage) => done[stage.id]).length;
  const nextIsCurrent = !!nextStage && nextStage.id === activeStageId;

  return (
    <nav className={`journey-rail journey-${state}`} aria-label="制作六阶段">
      <div className="journey-track-head">
        <span className="journey-total" aria-live="polite">
          {state === 'loading' ? <><Loader2 size={12} className="spin" /> 正在读取进度</> : `${doneCount} / ${stages.length} 阶段完成`}
        </span>
      </div>

      <ol className="journey-track">
        {stages.map((stage, index) => {
          const Icon = stage.icon;
          const stageDone = done[stage.id] ?? false;
          const blocked = blockers[stage.id] ?? 0;
          const active = stage.id === activeStageId;
          const nextUp = nextStage?.id === stage.id;
          return (
            <li key={stage.id} className={`journey-stage-item ${active ? 'active' : ''} ${stageDone ? 'done' : ''}`}>
              <button
                type="button"
                className={`journey-stage ${active ? 'active' : ''} ${stageDone ? 'done' : ''} ${nextUp ? 'next-up' : ''}`}
                aria-label={`第 ${index + 1} 阶段：${stage.text}${blocked ? `，${blocked} 项待处理` : ''}`}
                aria-current={active ? 'step' : undefined}
                title={`${stage.hint}${blocked ? ` · ${blocked} 项待处理` : ''}`}
                onClick={() => onNavigate(stage)}
              >
                <span className="journey-index" aria-hidden="true">
                  {stageDone ? <Check size={12} /> : index + 1}
                </span>
                <Icon size={15} className="journey-icon" aria-hidden="true" />
                <span className="journey-label">{stage.short ?? stage.text}</span>
                {blocked > 0 && !stageDone && <em className="journey-count" aria-hidden="true">{blocked}</em>}
              </button>
              {index < stages.length - 1 && <span className="journey-link" aria-hidden="true" />}
            </li>
          );
        })}
      </ol>

      <div className="journey-context" role="status">
        {state === 'error' ? (
          <>
            <AlertCircle size={15} className="journey-context-icon" />
            <div className="journey-context-text">
              <b>进度暂时不可用</b>
              <span>{error || '服务未响应'}。读取成功前不会显示任何“已完成”结论。</span>
            </div>
            {onRetry && <button type="button" className="secondary-button" onClick={onRetry}>重试读取</button>}
          </>
        ) : state !== 'ready' ? (
          <>
            <Loader2 size={15} className="spin journey-context-icon" />
            <div className="journey-context-text">
              <b>正在确认六个阶段的真实状态</b>
              <span>完成情况以本机服务返回为准，读取完成前不会显示“全部完成”。</span>
            </div>
          </>
        ) : nextStage ? (
          <>
            <span className="journey-context-tag">{nextIsCurrent ? '本页任务' : '下一步'}</span>
            <div className="journey-context-text">
              <b>{nextIsCurrent ? nextStage.hint : nextStage.text}</b>
              {!nextIsCurrent && <span>{nextStage.hint}</span>}
            </div>
            {nextStage.page !== currentPage && currentPage !== 'projects' && (
              <button type="button" className="must-button" onClick={() => onNavigate(nextStage)}>
                去这一步 <ChevronRight size={14} />
              </button>
            )}
          </>
        ) : (
          <>
            <Check size={15} className="journey-context-icon" />
            <div className="journey-context-text">
              <b>六个阶段全部完成</b>
              <span>全部选定分集已交付；艺术验收仍需你自己确认。</span>
            </div>
          </>
        )}

      </div>
    </nav>
  );
}

type BridgeProps = {
  stages: JourneyStageView[];
  activeStageId: string | null;
  onNavigate: (stage: JourneyStageView) => void;
  onHome: () => void;
};

/** 页尾衔接：上一阶段 / 作品总览 / 下一阶段，避免做完一步不知道该去哪。 */
export function JourneyBridge({ stages, activeStageId, onNavigate, onHome }: BridgeProps) {
  const index = stages.findIndex((stage) => stage.id === activeStageId);
  const previous = index > 0 ? stages[index - 1] : null;
  const next = index >= 0 && index < stages.length - 1 ? stages[index + 1] : null;
  return (
    <nav className="journey-bridge" aria-label="阶段衔接">
      <button type="button" className="bridge-step" disabled={!previous}
        title={previous ? '返回上一阶段' : '已经在第一阶段'}
        onClick={() => previous && onNavigate(previous)}>
        <ArrowLeft size={14} />
        <span>{previous ? '上一阶段' : '已经是第一阶段'}</span>
      </button>
      <button type="button" className="bridge-home" onClick={onHome}>回到项目管理</button>
      <span className="bridge-hint">{next ? `接下来：${next.text}` : '全部阶段已完成'}</span>
      <button type="button" className="bridge-step next" disabled={!next}
        title={next ? '前往下一阶段' : '最后阶段：交付完成'}
        onClick={() => next && onNavigate(next)}>
        <span>{next ? '下一阶段' : '最后阶段'}</span>
        <ArrowRight size={14} />
      </button>
    </nav>
  );
}
