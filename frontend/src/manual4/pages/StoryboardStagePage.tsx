import type { M4Overview, M4StageSummary } from '../api/client';

type Props = {
  overview: M4Overview;
  stage?: M4StageSummary;
  onNextStage: () => void;
};

export function StoryboardStagePage({ overview, stage, onNextStage }: Props) {
  const facts = stage?.facts ?? {};
  return (
    <section className="m4-panel" aria-label="人工分镜书">
      <div className="m4-panel-head">
        <div>
          <h2>① 人工分镜书</h2>
          <p className="m4-panel-desc">
            粘贴或导入你的人工分镜文本，或在集镜表中直接编辑分集、镜序、目标秒数（4–{overview.project.max_shot_seconds}s）与素材需求后冻结。
          </p>
        </div>
        <span className={`m4-badge ${stage?.done ? 'ok' : 'warn'}`}>
          {stage?.done ? `已冻结第 ${String(facts.confirmed_revision ?? 1)} 版` : '待确认冻结'}
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
        已登记分集：{String(facts.episodes ?? 0)} 集 · 镜头：{String(facts.shots ?? 0)} 镜 · 计划总时长：{String(facts.planned_seconds ?? 0)}s
      </div>

      <div className="m4-footer-bar">
        <span className="m4-footer-note">仅解析并保留人工结构与原文偏移，不重写剧情或补镜。</span>
        <button type="button" className="btn-primary" disabled={!stage?.done} onClick={onNextStage}>
          下一步：素材生成
        </button>
      </div>
    </section>
  );
}
