import type { M4StageSummary } from '../api/client';

type Props = {
  stage?: M4StageSummary;
  onNextStage: () => void;
};

export function MaterialsStagePage({ stage, onNextStage }: Props) {
  const facts = stage?.facts ?? {};
  return (
    <section className="m4-panel" aria-label="素材生成">
      <div className="m4-panel-head">
        <div>
          <h2>② 素材生成</h2>
          <p className="m4-panel-desc">
            按①已冻结分镜归并人物 / 场景 / 道具需求；每镜至少具备 1 项已晋升并绑定的合法 Ref2VA 参考素材。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : 'warn'}`}>
          {String(facts.requirements_ready ?? 0)} / {String(facts.requirements_total ?? 0)} 项已就绪
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
        已晋升主素材：{String(facts.promoted_assets ?? 0)} / {String(facts.total_assets ?? 0)} · 已绑定参考镜头：{String(facts.shots_bound ?? 0)} / {String(facts.shots_total ?? 0)}
      </div>

      <div className="m4-footer-bar">
        <span className="m4-footer-note">所有素材版本、晋升授权与镜头绑定独立存于四阶库。</span>
        <button type="button" className="btn-primary" disabled={!stage?.done} onClick={onNextStage}>
          下一步：排片生产
        </button>
      </div>
    </section>
  );
}
