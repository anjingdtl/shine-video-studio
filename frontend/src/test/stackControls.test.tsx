import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

const stopWorkerAndWaitOffline = vi.fn(async () => {});
const startWorker = vi.fn(async () => ({ status: 'RUNNING' }));
const stopStack = vi.fn(async () => ({ status: 'STOPPING' }));
const lifecycleStatus = vi.fn(async () => ({
  idle_shutdown_minutes: 5, ever_seen: true, last_seen_age_s: 1,
  stack_running: true, worker: 'running', last_stop: null,
}));

vi.mock('../api', () => ({
  api: vi.fn(async () => ({
    stacks: [], workers: [], jobs: [],
    counts: { stacks: 0, working_stacks: 0, workers_online: 1, running_jobs: 0 },
    process_error: null,
  })),
  json: (method: string, body: unknown) => ({ method, body: JSON.stringify(body) }),
  lifecycleStatus: () => lifecycleStatus(),
  startWorker: () => startWorker(),
  stopStack: () => stopStack(),
  stopWorker: vi.fn(),
  stopWorkerAndWaitOffline: () => stopWorkerAndWaitOffline(),
}));

import { WorkerValidationGate } from '../components/WorkerValidationGate';
import { WorkStackMonitor } from '../components/WorkStackMonitor';

describe('内联验证门', () => {
  afterEach(() => cleanup());

  it('停 Worker 并重试，验证后可恢复', async () => {
    const onRetry = vi.fn();
    render(<WorkerValidationGate onRetry={onRetry} />);
    fireEvent.click(screen.getByText('停止 Worker 并继续验证'));
    await waitFor(() => expect(onRetry).toHaveBeenCalled());
    expect(stopWorkerAndWaitOffline).toHaveBeenCalled();
    fireEvent.click(await screen.findByText('恢复 Worker'));
    await waitFor(() => expect(startWorker).toHaveBeenCalled());
  });
});

describe('工作栈监控生命周期控制', () => {
  afterEach(() => cleanup());

  it('显示自动退出策略与退出/Worker 控制，退出需二次确认', async () => {
    render(<WorkStackMonitor />);
    expect(await screen.findByText(/关页空闲 5 分钟后自动退出/)).toBeTruthy();
    expect(screen.getByText('停止 Worker')).toBeTruthy();
    fireEvent.click(screen.getByText('停止后台并退出'));
    expect(screen.getByText('确认退出')).toBeTruthy();
    fireEvent.click(screen.getByText('确认退出'));
    await waitFor(() => expect(stopStack).toHaveBeenCalled());
  });
});
describe('工作栈监控的配置等待项口径', () => {
  afterEach(() => cleanup());

  it('台账读取失败时不得显示为“没有等待项”', async () => {
    const { api } = await import('../api');
    (api as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      stacks: [], workers: [], jobs: [],
      idle_blockers: null,
      idle_blockers_unreadable: true,
      counts: { stacks: 0, working_stacks: 0, workers_online: 1, running_jobs: 0, idle_blockers: 0 },
      process_error: null,
    });
    render(<WorkStackMonitor />);
    expect(await screen.findByText(/配置等待项暂时读取失败/)).toBeTruthy();
    expect(screen.queryByText('当前没有后台进程在跑。')).toBeNull();
  });

  it('有阻塞台账项时逐条显示，不再误报空闲', async () => {
    const { api } = await import('../api');
    (api as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      stacks: [], workers: [], jobs: [],
      idle_blockers: [
        { kind: 'gpu_lease', id: 'lease-1', status: 'RECONCILE_REQUIRED', label: 'GPU 租约未释放（h3:job-9 · RECONCILE_REQUIRED）' },
      ],
      counts: { stacks: 0, working_stacks: 0, workers_online: 1, running_jobs: 0, idle_blockers: 1 },
      process_error: null,
    });
    render(<WorkStackMonitor />);
    expect(await screen.findByText(/GPU 租约未释放/)).toBeTruthy();
    expect(screen.queryByText('当前没有后台进程在跑。')).toBeNull();
  });
});
