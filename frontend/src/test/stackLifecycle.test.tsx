import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { presencePing, stopWorkerAndWaitOffline } from '../api';
import OutputDirectoryPanel from '../components/OutputDirectoryPanel';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return {
    ...actual,
    api: vi.fn(async (path: string) => {
      if (path === '/settings/output-directory') {
        return {
          root: 'D:\\out', default_root: 'D:\\out', custom: false,
          layout: { material: '素材', video: '视频', voice: '音色', material_center: '素材中心' },
          projects: [], file_count: 0, byte_size: 0, free_gb: 10, migration: null,
          blockers: ['1 个镜头任务待执行或正在生成'],
          blocker_details: {
            jobs: [{ id: 'job-12345678', status: 'QUEUED', project_title: '雾港' }],
            series: [], runs: [], library_tasks: [],
          },
          worker_online: false,
        };
      }
      return {};
    }),
    startWorker: vi.fn(async () => ({ status: 'RUNNING' })),
  };
});

describe('生命周期客户端', () => {
  beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
  afterEach(() => { vi.unstubAllGlobals(); cleanup(); });

  it('presence 心跳带本地动作标记', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({ json: async () => ({ status: 'ok' }) });
    await presencePing();
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('http://127.0.0.1:8790/presence');
    expect(init.headers['X-Shine-Local-Action']).toBe('1');
  });

  it('停 Worker 后轮询生命周期直到心跳过期', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (url.endsWith('/stop-worker')) {
        return Promise.resolve({ json: async () => ({ status: 'STOPPED' }) });
      }
      return Promise.resolve({
        json: async () => ({
          worker: 'stopped', idle_shutdown_minutes: 5, ever_seen: true,
          last_seen_age_s: 1, stack_running: true, last_stop: null,
        }),
      });
    });
    await stopWorkerAndWaitOffline();
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls
      .some(([url]) => String(url).endsWith('/lifecycle'))).toBe(true);
  });
});

describe('迁移阻塞明细', () => {
  afterEach(() => cleanup());

  it('列出具体任务并在 Worker 离线时给启动入口', async () => {
    const { startWorker } = await import('../api');
    render(<OutputDirectoryPanel />);
    expect(await screen.findByText(/镜头任务 job-1234/)).toBeTruthy();
    const button = await screen.findByText('启动 Worker，让任务继续完成');
    fireEvent.click(button);
    expect(startWorker).toHaveBeenCalled();
  });
});