import { act, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AgentProgress, reusedRolesFromInputs } from '../components/AgentProgress';

it('仅根据本次任务引用的有效产物识别复用步骤，忽略旧记录及错误类型', () => {
  const runs = [{ id: 'camera', role_id: 'cinematographer', status: 'RUNNING', created_at: '',
    trace: { input_revision_ids: ['writer', 'director'] } }];
  const artifacts = [
    { id: 'writer', kind: 'ScriptBreakdown', producer_role: 'scriptwriter', stale: false },
    { id: 'director', kind: 'DirectorialPlan', producer_role: 'director', stale: false },
    { id: 'old-camera', kind: 'CameraPlan', producer_role: 'cinematographer', stale: false },
    { id: 'director', kind: 'CameraPlan', producer_role: 'director', stale: false },
  ];
  const reused = reusedRolesFromInputs(runs, artifacts);
  expect(reused).toEqual(['scriptwriter', 'director']);
  render(<AgentProgress runs={runs} completedRoles={reused} />);
  expect(screen.getByRole('progressbar')).toHaveValue(2);
  expect(screen.getByText(/导演：已完成（复用已保存结果）/)).toBeInTheDocument();
  expect(reusedRolesFromInputs(runs, artifacts.map(a => ({ ...a, stale: true })))).toEqual([]);
});

it('人物确认是人工交接，明确说明导演未启动并保留已完成编剧', () => {
  const confirm = vi.fn();
  render(<AgentProgress runs={[]} completedRoles={['scriptwriter']} awaitingCharacters onConfirmCharacters={confirm} />);
  expect(screen.getByRole('progressbar')).toHaveValue(1);
  expect(screen.getByRole('region', { name: '分镜交接状态' })).toHaveTextContent('等待你确认人物，导演尚未启动');
  expect(screen.getByText(/导演：未启动：等待人物确认/)).toBeInTheDocument();
  screen.getByRole('button', { name: '去确认人物并继续导演' }).click();
  expect(confirm).toHaveBeenCalledTimes(1);
});

it('运行阶段来自后台记录，同时显示模型与最近成功查询时间', () => {
  render(<AgentProgress runs={[
    { id: 'phase-1', role_id: 'director', status: 'RUNNING', created_at: '', model_id: 'configured-model',
      trace: { phase: 'WAITING_RESOURCE', network_timeout_s: 180 } },
  ]} completedRoles={['scriptwriter']} lastCheckedAt={Date.parse('2026-11-03T15:00:00Z')} />);
  expect(screen.getByText(/后台当前阶段：等待前一个模型释放资源/)).toBeInTheDocument();
  expect(screen.getByText(/当前模型：configured-model/)).toBeInTheDocument();
  expect(screen.getByText(/最近一次成功查询后台：23:00:00/)).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveValue(1);
});

it('即使模型尚无新返回，等待秒数也持续更新', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-11-03T12:00:00Z'));
  try {
    const runs = [
      { id: 'waiting', role_id: 'director', status: 'RUNNING', created_at: '2026-11-03T12:00:00Z' },
    ];
    const view = render(<AgentProgress runs={runs} />);
    expect(screen.getByText(/已等待 0 秒/)).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText(/已等待 3 秒/)).toBeInTheDocument();
    view.rerender(<AgentProgress runs={runs} paused />);
    act(() => vi.advanceTimersByTime(10000));
    expect(screen.queryByText(/已等待 \d+ 秒/)).not.toBeInTheDocument();
    expect(screen.getByText(/状态待核对/)).toBeInTheDocument();
    view.unmount();
  } finally { vi.useRealTimers(); }
});

it('按完成角色计数，并保留失败返回码和导演裁定状态', () => {
  render(<AgentProgress runs={[
    { id: 'a', role_id: 'director', status: 'RUNNING', created_at: '', trace: { tool: 'continuity_arbitration' } },
    { id: 'b', role_id: 'director', status: 'SUCCEEDED', created_at: '', trace: { http_status: 200, provider_code: 0 } },
    { id: 'c', role_id: 'director', status: 'REJECTED', created_at: '', error_code: 'LLM_ERROR',
      trace: { http_status: 200, provider_code: 1008 } },
    { id: 'd', role_id: 'scriptwriter', status: 'SUCCEEDED', created_at: '', trace: { http_status: 200 } },
  ]} />);
  expect(screen.getByRole('progressbar')).toHaveValue(2);
  expect(screen.getByText(/服务商代码 1008/)).toBeInTheDocument();
  expect(screen.getByText(/导演连续性裁定：进行中 · 尚无返回码/)).toBeInTheDocument();
});
