import { useEffect, useState } from 'react';

export type AgentRunProgress = {
  id: string; role_id: string; status: string; created_at: string;
  model_id?: string;
  error_code?: string | null;
  trace?: { tool?: string; http_status?: number; provider_code?: number; external_outcome?: string;
    rejection_reason_code?: string; diagnostic_category?: string;
    phase?: string; phase_started_at?: string; network_timeout_s?: number; input_revision_ids?: string[];
    local_interruption_verified?: boolean };
};

type AgentArtifactProgress = { id: string; kind: string; producer_role: string; stale: boolean };

/** Only count saved artifacts actually consumed by this invocation's tasks. */
export function reusedRolesFromInputs(runs: AgentRunProgress[], artifacts: AgentArtifactProgress[]): string[] {
  const used = new Set(runs.flatMap(run => run.trace?.input_revision_ids ?? []));
  const roleByKind: Record<string, string> = {
    ScriptBreakdown: 'scriptwriter', DirectorialPlan: 'director', CameraPlan: 'cinematographer',
    ContinuityReport: 'continuity_supervisor', EditIntentPlan: 'editor',
  };
  return [...new Set(artifacts.filter(artifact => used.has(artifact.id) && !artifact.stale
    && roleByKind[artifact.kind] === artifact.producer_role).map(artifact => artifact.producer_role))];
}

const roles = [
  ['scriptwriter', '编剧'], ['director', '导演'], ['cinematographer', '摄影'],
  ['continuity_supervisor', '场记'], ['editor', '剪辑'],
] as const;

export const agentPhaseLabels: Record<string, string> = {
  WAITING_RESOURCE: '等待前一个模型释放资源，尚未调用本角色模型',
  PREPARING_PROVIDER: '已取得资源，正在准备模型服务',
  WAITING_PROVIDER: '已进入模型请求阶段，等待服务商返回完整结果',
  RELEASING_PROVIDER: '模型已返回，正在结束本阶段并释放资源',
  VALIDATING_RESULT: '模型已返回，正在校验结构与剧情约束',
};

export function AgentProgress({ runs, completedRoles = [], awaitingCharacters = false, charactersConfirmed = false, onConfirmCharacters, lastCheckedAt, paused = false }: {
  runs: AgentRunProgress[]; completedRoles?: string[]; awaitingCharacters?: boolean;
  onConfirmCharacters?: () => void; lastCheckedAt?: number;
  charactersConfirmed?: boolean;
  paused?: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (paused) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [paused]);
  const normalRuns = runs.filter(run => run.trace?.tool !== 'continuity_arbitration');
  const completed = roles.filter(([role]) => completedRoles.includes(role)
    || normalRuns.some(run => run.role_id === role && run.status === 'SUCCEEDED')).length;
  const active = runs.find(run => run.status === 'RUNNING');
  const arbitration = runs.find(run => run.trace?.tool === 'continuity_arbitration');
  const statusText = (run: AgentRunProgress | undefined) => {
    if (!run) return '等待';
    const state = run.status === 'RUNNING' ? paused ? '状态待核对' : '进行中' : run.status === 'SUCCEEDED' ? '已完成' : '未通过';
    const codes = [run.trace?.http_status === undefined ? null : `HTTP ${run.trace.http_status}`,
      run.trace?.provider_code === undefined ? null : `服务商代码 ${run.trace.provider_code}`,
      run.error_code || null, run.trace?.rejection_reason_code || null,
      run.trace?.diagnostic_category || null,
      run.trace?.external_outcome === 'UNKNOWN' ? '外部结果未知' : null].filter(Boolean);
    const start = Date.parse(run.created_at.endsWith('Z') ? run.created_at : `${run.created_at}Z`);
    const elapsed = !paused && run.status === 'RUNNING' && Number.isFinite(start)
      ? ` · 已等待 ${Math.max(0, Math.floor((now - start) / 1000))} 秒` : '';
    return `${state} · ${codes.length ? codes.join(' · ') : '尚无返回码'}${elapsed}`;
  };
  return <div aria-label="Agent 调用进度">
    {awaitingCharacters && <div className="required-action" role="region" aria-label="分镜交接状态" aria-live="polite">
      <div className="required-text">
        <b>{charactersConfirmed ? '人物已确认，导演尚未启动' : '等待你确认人物，导演尚未启动'}</b>
        <small>{charactersConfirmed ? '请完成下方的剧本选择和发送同意，再点「复用已确认人物，继续导演」。已保存的名字无需重写。' : '编剧已完成。请核对每个人物的名字和素材；确认后才会调用导演，不会重跑编剧。继续等待不会自动进入下一步。'}</small>
      </div>
      <button className="must-button" type="button" onClick={onConfirmCharacters}>{charactersConfirmed ? '去继续导演' : '去确认人物并继续导演'}</button>
    </div>}
    {active && paused && <p role="status">实时进度不可确认。计时已暂停，下方保留最后读取的记录。</p>}
    {active && !paused && <div className="module-guide">
      <div><b>后台当前阶段：{agentPhaseLabels[active.trace?.phase ?? ''] ?? '任务已登记，正在等待后台更新具体阶段'}</b>
        {active.model_id && <p>当前模型：{active.model_id}</p>}
        {active.trace?.network_timeout_s && <p>单次网络等待设置为 {active.trace.network_timeout_s} 秒；它不是整条制作流程的总时限。</p>}
      </div>
    </div>}
    {lastCheckedAt && <p>最近一次成功查询后台：{new Date(lastCheckedAt).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}。下列状态来自已保存的运行记录。</p>}
    <p>角色完成 {completed}/5；进度按已完成角色计算，不能代表当前模型的内部进度。</p>
    <progress aria-label="已完成的 Agent" max={5} value={completed} />
    {active && !paused && <p>正在等待模型返回。长时间无响应时请查看运行记录。</p>}
    <ul>{roles.map(([role, label]) => {
      const matching = normalRuns.filter(run => run.role_id === role);
      return <li key={role}>{label}：{matching.length
        ? matching.map((run, index) => <span key={run.id}>{index > 0 ? '；' : ''}{statusText(run)}</span>)
        : completedRoles.includes(role) ? '已完成（复用已保存结果）'
          : role === 'director' && awaitingCharacters ? charactersConfirmed ? '未启动：等待继续导演' : '未启动：等待人物确认' : '尚未启动'}</li>;
    })}</ul>
    {arbitration && <p>导演连续性裁定：{statusText(arbitration)}</p>}
  </div>;
}
