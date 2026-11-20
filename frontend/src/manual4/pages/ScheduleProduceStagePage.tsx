import type { M4Overview, M4StageSummary } from '../api/client';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onNextStage: () => void;
};

export function ScheduleProduceStagePage({ overview, stage, onNextStage }: Props) {
  const facts = stage?.facts ?? {};
  return (
    <section className="m4-panel" aria-label="排片生产">
      <div className="m4-panel-head">
        <div>
          <h2>③ 排片生产</h2>
          <p className="m4-panel-desc">
            逐镜填入并冻结 H3 原文提示词（严禁静默回退编译器文本），配置画幅与镜间 CUT / 延续后由四阶独立 Worker 批量拍摄。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : overview.gpu.busy ? 'busy' : 'warn'}`}>
          {stage?.done ? '全部镜头已通过' : overview.gpu.busy ? 'GPU 执行/占用中' : '待冻结与开拍'}
        </span>
      </div>

      {stage && stage.blockers.length > 0 && (
        <div className="m4-blocker-box" role="status">
          {stage.blockers.map((b) => (
            <div key={b.code}>• {b.message}</div>
          ))}
        </div>
      )}

      <div className="m4-empty-box">
        已冻结 H3 原文：{String(facts.prompts_frozen ?? 0)} / {String(facts.shots_total ?? 0)} · 四阶 Worker：{overview.worker_online ? '在线' : '离线'} · GPU：{overview.gpu.busy ? '占用中' : '空闲'}
      </div>

      <div className="m4-footer-bar">
        <span className="m4-footer-note">系统级 GPU 互斥租约保护本机显卡，不会中断七阶或跨线覆盖。</span>
        <button type="button" className="btn-primary" disabled={!stage?.done} onClick={onNextStage}>
          下一步：产出成片
        </button>
      </div>
    </section>
  );
}
