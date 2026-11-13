/** The unified engine status entry reads only the derived backend snapshot
 *  and only offers the backend's safe action.  It must never invent a state
 *  from raw fetch results, and a STOPPED snapshot must not show a stop
 *  button; a DEGRADED snapshot offers "检查连接" (probe), never a restart. */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  vi.useRealTimers();
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

  it('passes the snapshot state version as the compare-and-set when submitting', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({ state_version: 4 }) },
      { method: 'POST', pattern: /runtime-orchestrator\/ensure$/, respond: () => jsonResponse({
        operation_id: 'op-1', action: 'ensure', installation_id: 'inst-1',
        status: 'SUCCEEDED', state_version: 5, failure_code: null, result: { outcome: 'STARTED' },
      }, 202) },
    ]);
    const user = userEvent.setup();
    render(<EngineStatusEntry apiOnline />);
    await user.click(await screen.findByText('启动引擎'));
    // 本次操作的结论要当场说出来，不是只默默刷新快照。
    expect(await screen.findByText('引擎已启动。')).toBeTruthy();
    const call = stub.calls.find((entry) => entry.method === 'POST' && /runtime-orchestrator\/ensure$/.test(entry.url));
    expect(call?.body).toMatchObject({ expected_state_version: 4 });
  });

  it('refreshes the snapshot instead of leaking the stale-version code on 409', async () => {
    let statusCalls = 0;
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => {
        statusCalls += 1;
        return snapshot({ state_version: statusCalls > 1 ? 1 : 0 });
      } },
      { method: 'POST', pattern: /runtime-orchestrator\/ensure$/, respond: () => jsonResponse(
        { detail: { code: 'RUNTIME_STATE_VERSION_STALE', detail: 'expected 0, current 1' } }, 409) },
    ]);
    const user = userEvent.setup();
    render(<EngineStatusEntry apiOnline />);
    await user.click(await screen.findByText('启动引擎'));
    expect(await screen.findByText('引擎状态已更新，请确认后再操作。')).toBeTruthy();
    // 自动重拉：一次初始轮询 + 一次过期后的刷新。
    expect(statusCalls).toBe(2);
    expect(screen.queryByText(/RUNTIME_STATE_VERSION_STALE/)).toBeNull();
  });

  it('says a manual check is still needed when reconcile returns NEEDS_REVIEW', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
        state: 'BLOCKED_UNVERIFIED',
        recovery: { required: true, reason_code: 'OWNERSHIP_UNVERIFIED', safe_action: 'reconcile',
          advice: '渲染器归属或外部提交结果未核对。' },
      }) },
      { method: 'POST', pattern: /runtime-orchestrator\/reconcile$/, respond: () => jsonResponse({
        operation_id: 'op-2', action: 'reconcile', installation_id: 'inst-1',
        status: 'SUCCEEDED', state_version: 1, failure_code: null,
        result: { outcome: 'NEEDS_REVIEW' },
      }, 202) },
    ]);
    const user = userEvent.setup();
    render(<EngineStatusEntry apiOnline />);
    await user.click(await screen.findByText('核对恢复'));
    expect(await screen.findByText(/仍需人工核对/)).toBeTruthy();
    expect(screen.queryByText(/NEEDS_REVIEW/)).toBeNull();
  });

  it('reads the final result of an operation that was still running when accepted', async () => {
    vi.useFakeTimers();
    const operation = { operation_id: 'op-11', action: 'ensure', installation_id: 'inst-1',
      status: 'RUNNING', state_version: 2, failure_code: null, result: {} };
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({}) },
      { method: 'POST', pattern: /runtime-orchestrator\/ensure$/, respond: () => jsonResponse(operation, 202) },
      { method: 'GET', pattern: /runtime-orchestrator\/operations\/op-11$/, respond: () => jsonResponse({
        ...operation, status: 'SUCCEEDED', state_version: 3, result: { outcome: 'STARTED' },
      }) },
    ]);
    render(<EngineStatusEntry apiOnline />);
    await act(async () => {});
    fireEvent.click(screen.getByText('启动引擎'));
    await act(async () => {});
    expect(screen.getByText(/操作已提交/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(screen.getByText('引擎已启动。')).toBeTruthy();
    expect(stub.callsOf('POST', /runtime-orchestrator/)).toHaveLength(1);
  });

  it('shows why a stale stop button was refused while production is running', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
        state: 'READY_IDLE', recovery: { required: false, safe_action: 'stop' },
      }) },
      { method: 'POST', pattern: /runtime-orchestrator\/stop$/, respond: () => jsonResponse({
        detail: { code: 'RUNTIME_WORK_IN_FLIGHT' },
      }, 409) },
    ]);
    const user = userEvent.setup();
    render(<EngineStatusEntry apiOnline />);
    await user.click(await screen.findByText('停止引擎'));
    expect(await screen.findByText('引擎正在制作，等手头的活做完再操作。')).toBeTruthy();
    expect(screen.queryByText(/状态已更新/)).toBeNull();
  });

  it('disables the action while a control operation is already running', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
        current: { owner_kind: null, task: null, operation_id: 'op-9',
          operation_action: 'ensure', operation_status: 'PENDING' },
      }) },
    ]);
    render(<EngineStatusEntry apiOnline />);
    const button = (await screen.findByText('处理中…')).closest('button') as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.queryByText('启动引擎')).toBeNull();
    expect(stub.callsOf('POST', /runtime-orchestrator/)).toHaveLength(0);
  });

  it('keeps the read-only probe usable while a control operation is running', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
        state: 'DEGRADED',
        current: { owner_kind: null, task: null, operation_id: 'op-9',
          operation_action: 'stop', operation_status: 'RUNNING' },
        recovery: { required: false, reason_code: 'TRANSPORT_DEGRADED', safe_action: 'probe',
          advice: '进程仍在但 HTTP 暂时不可达。' },
      }) },
      { method: 'POST', pattern: /runtime-orchestrator\/probe$/, respond: () => jsonResponse({
        operation_id: 'op-10', action: 'probe', installation_id: 'inst-1',
        status: 'SUCCEEDED', state_version: 2, failure_code: null, result: { probed: true },
      }, 202) },
    ]);
    const user = userEvent.setup();
    render(<EngineStatusEntry apiOnline />);
    const probe = (await screen.findByText('检查连接')).closest('button') as HTMLButtonElement;
    expect(probe.disabled).toBe(false);
    await user.click(probe);
    expect(await screen.findByText('已重新检查连接，结果见上方状态。')).toBeTruthy();
  });

  it('explains a refusal in plain Chinese without the machine code', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({}) },
      { method: 'POST', pattern: /runtime-orchestrator\/ensure$/, respond: () => jsonResponse({
        operation_id: 'op-3', action: 'ensure', installation_id: 'inst-1',
        status: 'REFUSED', state_version: 1,
        failure_code: 'IMAGE_RENDERER_BUSY_TASK_IN_FLIGHT', result: {},
      }, 202) },
    ]);
    const user = userEvent.setup();
    render(<EngineStatusEntry apiOnline />);
    await user.click(await screen.findByText('启动引擎'));
    expect(await screen.findByText(/现在还不能执行这个操作：引擎正在制作/)).toBeTruthy();
    expect(screen.queryByText(/IMAGE_RENDERER_BUSY_TASK_IN_FLIGHT/)).toBeNull();
  });

  it('reports the running configuration of the engine in the detail panel', async () => {
    const cases: Array<[string, string]> = [
      ['VERIFIED', '运行配置：工作流已固定'],
      ['MISMATCH', '运行配置：工作流与配置不一致'],
      ['NOT_RUNNING', '运行配置：未核对'],
    ];
    const user = userEvent.setup();
    for (const [workflow, expected] of cases) {
      stub?.restore();
      stub = new StudioStub([
        { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
          health: { worker: 'UP', comfy_http: 'UP', workflow },
        }) },
      ]);
      const { unmount } = render(<EngineStatusEntry apiOnline />);
      await user.click(await screen.findByText('引擎已停止'));
      expect(await screen.findByText(expected)).toBeTruthy();
      unmount();
    }
  });
});

it('copies only the allowed connection diagnostics', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  stub = new StudioStub([{ method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
    diagnostics: { transport: 'UNKNOWN', ownership: 'UNVERIFIED', capability: 'MISSING_NODES',
      missing_nodes: ['SageNode'], reason_code: 'COMFY_REQUIRED_NODE_MISSING', secret_token: 'do-not-copy' },
  }) }]);
  render(<EngineStatusEntry apiOnline />);
  fireEvent.click(await screen.findByText('引擎已停止'));
  fireEvent.click(screen.getByText('复制诊断信息'));
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
  expect(writeText.mock.calls[0][0]).toContain('SageNode');
  expect(writeText.mock.calls[0][0]).not.toContain('do-not-copy');
});

it('does not treat a reachable unowned service as production ready', async () => {
  stub = new StudioStub([
    { method: 'GET', pattern: /runtime-orchestrator\/status$/, respond: () => snapshot({
      state: 'EXTERNAL_CONFLICT',
      recovery: { required: true, reason_code: 'EXTERNAL_INSTANCE_ON_PORT', safe_action: 'probe', advice: '端口被占用' },
    }) },
    { method: 'POST', pattern: /runtime-orchestrator\/probe$/, respond: () => jsonResponse({
      operation_id: 'probe-1', action: 'probe', installation_id: 'inst-1', status: 'SUCCEEDED',
      state_version: 1, failure_code: null, result: { transport: 'UP', listener_ownership: 'UNVERIFIED' },
    }, 202) },
  ]);
  render(<EngineStatusEntry apiOnline />);
  fireEvent.click(await screen.findByText('检查连接'));
  expect(await screen.findByText(/服务可连接，但无法证明属于本工作台/)).toBeTruthy();
});
