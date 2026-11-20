import type { M4StageSummary } from '../api/client';

type Props = {
  stage?: M4StageSummary;
};

export function DeliveryStagePage({ stage }: Props) {
  const facts = stage?.facts ?? {};
  return (
    <section className="m4-panel" aria-label="产出成片">
      <div className="m4-panel-head">
        <div>
          <h2>④ 产出成片</h2>
          <p className="m4-panel-desc">
            按①冻结顺序从本次四阶 Run 已通过的真实视频产物生成 EDL、FFmpeg 装配、校验哈希并逐集观看验收。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : 'warn'}`}>
          已验收 {String(facts.accepted_deliveries ?? 0)} / {String(facts.episodes ?? 0)} 集
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
        已交付成片：{String(facts.deliveries ?? 0)} 个 · 待验收：{Math.max(0, Number(facts.episodes ?? 0) - Number(facts.accepted_deliveries ?? 0))} 集
      </div>

      <div className="m4-footer-bar">
        <span className="m4-footer-note">仅接受来自四阶本轮已通过真实产物的 FFmpeg 装配结果。</span>
        <button type="button" className="btn-primary" disabled={!stage?.done}>
          成片验收完成
        </button>
      </div>
    </section>
  );
}
