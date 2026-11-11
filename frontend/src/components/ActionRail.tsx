import { useState, type ReactNode } from 'react';
import { AlertTriangle, ChevronRight, Loader2 } from 'lucide-react';

/** 右侧操作栏的动作契约（设计方案 §3.1）。disabled 必须带原因，最好带「去补齐」出口。 */
export type RailAction = {
  label: string;
  onClick: () => void;
  loading?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  guide?: { label: string; go: () => void };
  icon?: ReactNode;
  danger?: boolean;
  armed?: boolean;
};

export type RailBlocker = {
  code?: string;
  reason: string;
  action?: { label: string; go: () => void };
};

export type RailRunning = { label: string; done?: number; total?: number };

export type ActionRailProps = {
  stageId: string;
  stageLabel?: string;
  primary: RailAction | null;
  running?: RailRunning | null;
  secondary?: RailAction[];
  blockers?: RailBlocker[];
  progress?: { done: number; total: number; label: string };
};

function ActionButton({ action, kind }: { action: RailAction; kind: 'primary' | 'secondary' }) {
  const className = action.danger ? 'btn-danger' : kind === 'primary' ? 'btn-primary' : 'btn-secondary';
  return (
    <button
      type="button"
      className={`${className}${action.armed ? ' is-armed' : ''}`}
      disabled={action.disabled || action.loading}
      title={action.disabled ? action.disabledReason : undefined}
      onClick={action.onClick}
    >
      {action.loading ? <Loader2 size={14} className="spin" aria-hidden="true" /> : action.icon}
      <span>{action.loading ? `正在${action.label}…` : action.label}</span>
    </button>
  );
}

/** 每页唯一的右侧吸顶操作栏：主操作 + 次级纵列 + 待办阻塞 + 阶段进度。
 *  主操作唯一性由页面装配层（StageRailProvider）保证；这里只负责呈现。 */
export function ActionRail({ stageLabel, primary, running, secondary = [], blockers = [], progress }: ActionRailProps) {
  const [moreOpen, setMoreOpen] = useState(false);
  const [blockersOpen, setBlockersOpen] = useState(false);
  const shownSecondary = moreOpen ? secondary : secondary.slice(0, 4);
  const shownBlockers = blockersOpen ? blockers : blockers.slice(0, 3);

  return (
    <aside className="action-rail" aria-label="本阶段操作">
      {stageLabel && <div className="rail-stage">{stageLabel}</div>}

      <div className="rail-primary">
        {running ? (
          <div className="rail-running" role="status">
            <span className="rail-running-label"><Loader2 size={15} className="spin" aria-hidden="true" />{running.label}</span>
            {typeof running.done === 'number' && typeof running.total === 'number' && running.total > 0 && (
              <div className="meter is-busy">
                <span className="meter-track"><span className="meter-fill" style={{ width: `${Math.min(100, (running.done / running.total) * 100)}%` }} /></span>
                <span className="meter-count">{running.done}/{running.total}</span>
              </div>
            )}
          </div>
        ) : primary ? (
          <>
            <ActionButton action={primary} kind="primary" />
            {primary.disabled && primary.disabledReason && (
              <div className="rail-disabled-reason">{primary.disabledReason}</div>
            )}
            {primary.disabled && primary.guide && (
              <button type="button" className="btn-text rail-guide" onClick={primary.guide.go}>
                {primary.guide.label} <ChevronRight size={13} aria-hidden="true" />
              </button>
            )}
          </>
        ) : null}
      </div>

      {shownSecondary.length > 0 && (
        <div className="rail-secondary">
          {shownSecondary.map((action, index) => (
            <ActionButton key={`${action.label}-${index}`} action={action} kind="secondary" />
          ))}
          {secondary.length > 4 && (
            <button type="button" className="btn-text" onClick={() => setMoreOpen((open) => !open)}>
              {moreOpen ? '收起' : `更多（${secondary.length - 4}）`}
            </button>
          )}
        </div>
      )}

      {blockers.length > 0 && (
        <div className="rail-blockers">
          <div className="rail-blockers-title">待处理 · {blockers.length}</div>
          <ul className="blocker-list">
            {shownBlockers.map((blocker, index) => (
              <li className="blocker-item" key={`${blocker.code ?? blocker.reason}-${index}`}>
                <AlertTriangle size={13} aria-hidden="true" />
                <span className="blocker-reason">{blocker.reason}</span>
                {blocker.action && (
                  <button type="button" className="btn-text" onClick={blocker.action.go}>{blocker.action.label}</button>
                )}
              </li>
            ))}
          </ul>
          {blockers.length > 3 && (
            <button type="button" className="blocker-more" onClick={() => setBlockersOpen((open) => !open)}>
              {blockersOpen ? '收起' : `展开全部 ${blockers.length} 条`}
            </button>
          )}
        </div>
      )}

      {progress && progress.total > 0 && (
        <div className="rail-progress">
          <div className="meter">
            <span className="meter-track"><span className="meter-fill" style={{ width: `${Math.min(100, (progress.done / progress.total) * 100)}%` }} /></span>
            <span className="meter-count">{progress.done}/{progress.total}</span>
          </div>
          <span className="meter-label">{progress.label}</span>
        </div>
      )}
    </aside>
  );
}

export default ActionRail;