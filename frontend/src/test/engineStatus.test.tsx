/** The unified engine status entry reads only the derived backend snapshot
 *  and only offers the backend's safe action.  It must never invent a state
 *  from raw fetch results, and a STOPPED snapshot must not show a stop
 *  button; a DEGRADED snapshot offers "检查连接" (probe), never a restart. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { EngineStatusEntry } from '../components/EngineStatusEntry';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

const snapshot = (overrides: Record<string, unknown>) => jsonResponse({
  schema_version: 1,
  orchestrator_enabled: true,
  installation_id: 'inst-1',
  state: 'STOPPED',
  health: { worker: 'UP', comfy_http: 'UNKNOWN' },
  ownership: { managed: false, verified: false, port: 8188, pid: null },
  current: { owner_kind: null, task: null, operation_id: null },
  resource: { active_leases: 0, waiting_leases: 0, reconcile_leases: 0 },
  recovery: { required: false, reason_code: null, safe_action: 'ensure', advice: '可安全启动' },
  last_observed_at: 't',
  state_version: 0,
  ...overrides,
});

afterEach(() => {
  stub?.restore();
  vi.restoreAllMocks();
});

describe('EngineStatusEntry', () => {
  it('shows the derived state and only the backend safe action', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({ state: 'STOPPED' }) },
    ]);
    render(<EngineStatusEntry apiOnline />);
    expect(await screen.findByText('引擎已停止')).toBeTruthy();
    expect(screen.queryByText('停止引擎')).toBeNull();
    expect(screen.getByText('启动引擎')).toBeTruthy();
  });

  it('offers probe (not restart) while degraded', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
        state: 'DEGRADED',
        recovery: { required: false, reason_code: 'TRANSPORT_DEGRADED', safe_action: 'probe', advice: '进程仍在但 HTTP 暂时不可达。' },
      }) },
    ]);
    render(<EngineStatusEntry apiOnline />);
    expect(await screen.findByText('连接不稳定')).toBeTruthy();
    expect(screen.getByText('检查连接')).toBeTruthy();
    expect(screen.queryByText('启动引擎')).toBeNull();
  });

  it('submits the control intent through the API, never a process of its own', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({}) },
      { method: 'POST', pattern: /runtime-orchestrator\/ensure$/, respond: () => jsonResponse({ operation_id: 'op-1', status: 'PENDING' }, 202) },
    ]);
    const user = userEvent.setup();
    render(<EngineStatusEntry apiOnline />);
    await user.click(await screen.findByText('启动引擎'));
    await waitFor(() => {
      const call = stub.calls.find((entry) => entry.method === 'POST' && /runtime-orchestrator\/ensure$/.test(entry.url));
      expect(call).toBeTruthy();
      const headers = new Headers(call?.init?.headers);
      expect(headers.get('x-shine-local-action')).toBe('1');
    });
  });

  it('renders nothing while the backend is offline', () => {
    const { container } = render(<EngineStatusEntry apiOnline={false} />);
    expect(container.textContent).toBe('');
  });
});
