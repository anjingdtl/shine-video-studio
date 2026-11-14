import { afterEach, describe, expect, it, vi } from 'vitest';
import { restartStack, restartStatus, supervisorBase, supervisorPort } from '../api';

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('页面重启后台：监督进程客户端', () => {
  it('默认端口与启动器一致，可经 localStorage 覆盖', () => {
    expect(supervisorPort()).toBe(8790);
    expect(supervisorBase()).toBe('http://127.0.0.1:8790');
    window.localStorage.setItem('shine-supervisor-port', '9911');
    expect(supervisorPort()).toBe(9911);
    expect(supervisorBase()).toBe('http://127.0.0.1:9911');
  });

  it('重启请求带本地动作标记且只打环回监督端口', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ status: 'RESTARTING' }), { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);
    const result = await restartStack();
    expect(result.status).toBe('RESTARTING');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:8790/restart-stack');
    expect(init?.method).toBe('POST');
    expect(new Headers(init?.headers).get('X-Shine-Local-Action')).toBe('1');
  });

  it('读取重启状态供页内进度与具名拒绝', async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ phase: 'refused', detail: 'RUNTIME_CONFIGURATION_REQUIRES_IDLE_AND_RECONCILED_WORK' }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const status = await restartStatus();
    expect(status.phase).toBe('refused');
    expect(status.detail).toContain('IDLE');
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8790/restart-status');
  });
});