import { useEffect, useState } from 'react';

export type AgentRunProgress = {
  id: string; role_id: string; status: string; created_at: string;
  error_code?: string | null;
  trace?: { tool?: string; http_status?: number; provider_code?: number; external_outcome?: string;
    rejection_reason_code?: string; diagnostic_category?: string };
};

const roles = [
  ['scriptwriter', '编剧'], ['director', '导演'], ['cinematographer', '摄影'],
  ['continuity_supervisor', '场记'], ['editor', '剪辑'],
] as const;

export function AgentProgress({ runs }: { runs: AgentRunProgress[] }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const normalRuns = runs.filter(run => run.trace?.tool !== 'continuity_arbitration');
  const completed = roles.filter(([role]) => normalRuns.some(run => run.role_id === role && run.status === 'SUCCEEDED')).length;
  const arbitration = runs.find(run => run.trace?.tool === 'continuity_arbitration');
  const statusText = (run: AgentRunProgress | undefined) => {
    if (!run) return '等待';
    const state = run.status === 'RUNNING' ? '进行中' : run.status === 'SUCCEEDED' ? '已完成' : '未通过';
    const codes = [run.trace?.http_status === undefined ? null : `HTTP ${run.trace.http_status}`,
      run.trace?.provider_code === undefined ? null : `服务商代码 ${run.trace.provider_code}`,
      run.error_code || null, run.trace?.rejection_reason_code || null,
      run.trace?.diagnostic_category || null,
      run.trace?.external_outcome === 'UNKNOWN' ? '外部结果未知' : null].filter(Boolean);
    const start = Date.parse(run.created_at.endsWith('Z') ? run.created_at : `${run.created_at}Z`);
    const elapsed = run.status === 'RUNNING' && Number.isFinite(start)
      ? ` · 已等待 ${Math.max(0, Math.floor((now - start) / 1000))} 秒` : '';
    return `${state} · ${codes.length ? codes.join(' · ') : '尚无返回码'}${elapsed}`;
  };
  return <div aria-label="Agent 调用进度">
    <p>角色完成 {completed}/5；进度按已完成角色计算，不能代表当前模型的内部进度。</p>
    <progress aria-label="已完成的 Agent" max={5} value={completed} />
    {normalRuns.some(run => run.status === 'RUNNING') && <p>正在等待模型返回；这里显示实际等待时间。长时间无响应时请查看运行记录，不要重复点击生成。</p>}
    <ul>{roles.map(([role, label]) => {
      const matching = normalRuns.filter(run => run.role_id === role);
      return <li key={role}>{label}：{matching.length
        ? matching.map((run, index) => <span key={run.id}>{index > 0 ? '；' : ''}{statusText(run)}</span>)
        : '等待'}</li>;
    })}</ul>
    {arbitration && <p>导演连续性裁定：{statusText(arbitration)}</p>}
  </div>;
}
