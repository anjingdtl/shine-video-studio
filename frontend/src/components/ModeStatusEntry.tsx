import { Clapperboard, Loader2, MonitorPlay, ShieldAlert, ShieldCheck, Sparkles } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ProductionModeStatus, SystemStatus } from '../api';

/** 顶部常驻的「演示 / 真实制作」状态入口。唯一数据来源是后端
 * /system/status 的 production_mode；这里只做展示和发起操作，
 * 绝不自行推断本机环境是否就绪。 */
export type ModeActionHandler = (action: NonNullable<ProductionModeStatus['action']>) => void;

type Visual = { icon: LucideIcon; tone: 'demo' | 'ready' | 'running' | 'blocked' | 'unknown'; cta: string };

const VISUALS: Record<string, Visual> = {
  DEMO: { icon: Sparkles, tone: 'demo', cta: '启用真实制作' },
  SETUP_REQUIRED: { icon: Sparkles, tone: 'demo', cta: '继续配置' },
  VALIDATION_REQUIRED: { icon: ShieldAlert, tone: 'blocked', cta: '检查并修复' },
  READY: { icon: ShieldCheck, tone: 'ready', cta: '开始制作' },
  RUNNING: { icon: Loader2, tone: 'running', cta: '查看进度' },
  BLOCKED: { icon: ShieldAlert, tone: 'blocked', cta: '检查并修复' },
  RECONCILE_REQUIRED: { icon: ShieldAlert, tone: 'blocked', cta: '去处理' },
};

export function ModeStatusEntry({ system, onAction }: { system: SystemStatus | null; onAction: ModeActionHandler }) {
  const production = system?.production_mode ?? null;
  // 制作环境探测与 API 连通性分开显示。
  if (!production) {
    return (
      <button
        type="button"
        className="mode-entry unknown"
        title="暂时无法确认制作环境状态"
        onClick={() => onAction('check_fix')}
      >
        <MonitorPlay size={15} />
        <span className="mode-entry-label">制作状态暂不可用</span>
        <span className="mode-entry-cta">重试</span>
      </button>
    );
  }
  const visual = VISUALS[production.mode] ?? VISUALS.BLOCKED;
  const Icon = visual.icon;
  const clickable = production.action !== null;
  return (
    <button
      type="button"
      className={`mode-entry ${visual.tone}`}
      title={production.detail}
      disabled={!clickable}
      onClick={() => production.action && onAction(production.action)}
    >
      <Icon size={15} className={production.mode === 'RUNNING' ? 'spin' : ''} />
      <span className="mode-entry-label">{production.label}</span>
      {clickable && <span className="mode-entry-cta">{visual.cta}</span>}
      {production.mode === 'RUNNING' && (
        <span className="mode-entry-count">{production.series_active > 0 ? '连续制作中' : `${production.running_jobs} 个镜头`}</span>
      )}
    </button>
  );
}

/** 导演模式页面顶部的提示条：演示体验必须始终可见，不允许悄悄消失。 */
export function DemoRibbon({ production, onEnable }: { production: ProductionModeStatus; onEnable: () => void }) {
  if (production.mode !== 'DEMO' && production.mode !== 'SETUP_REQUIRED') return null;
  return (
    <div className="demo-ribbon" role="status">
      <Clapperboard size={15} />
      <span>{production.mode === 'DEMO'
        ? '演示体验：当前只演练流程，不会生成真实影片。'
        : '真实制作配置尚未完成，当前仍是演示体验。'}</span>
      <button type="button" className="btn-text" onClick={onEnable}>
        {production.mode === 'DEMO' ? '启用真实制作' : '继续完成配置'}
      </button>
    </div>
  );
}
