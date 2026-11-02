import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { useStudio } from '../useStudio';
import { StudioStub, baseRoutes, SYSTEM_STATUS, jsonResponse, productionMode } from './studioStub';
let stub: StudioStub;
beforeEach(() => { localStorage.clear(); stub = new StudioStub(baseRoutes()); });
afterEach(() => { stub.restore(); localStorage.clear(); });
function ready() {
  stub.add({ method: 'GET', pattern: /system\/status$/, respond: () => jsonResponse({ ...SYSTEM_STATUS,
    production_mode: productionMode({ mode: 'READY' }) }) });
}
it('用户演示选择在刷新与项目切换后保持，两种入口共享意图', async () => {
  ready();
  const first = renderHook(useStudio);
  await waitFor(() => expect(first.result.current.realSubmissionAllowed).toBe(true));
  act(() => first.result.current.setDemoOverride(true));
  act(() => first.result.current.setProjectId('another-project'));
  expect(first.result.current.queueProvider).toBe('mock');
  first.unmount();
  const second = renderHook(useStudio);
  await waitFor(() => expect(second.result.current.realSubmissionAllowed).toBe(true));
  expect(second.result.current.demoOverride).toBe(true);
  expect(second.result.current.queueProvider).toBe('mock');
});
it.each(['BLOCKED', 'RECONCILE_REQUIRED', 'DEMO'])('真实意图遇到 %s 不自动变成 mock 或提交任务', async mode => {
  ready();
  const view = renderHook(useStudio);
  await waitFor(() => expect(view.result.current.realSubmissionAllowed).toBe(true));
  act(() => view.result.current.setDemoOverride(false));
  stub.add({ method: 'GET', pattern: /system\/status$/, respond: () => jsonResponse({ ...SYSTEM_STATUS,
    production_mode: productionMode({ mode }) }) });
  await act(() => view.result.current.refreshAll());
  expect(view.result.current.realSubmissionAllowed).toBe(false);
  expect(view.result.current.queueProvider).toBe('comfyui');
  await act(() => view.result.current.queueShot({ id: 'shot', project_id: 'proj-1', name: 'shot', revision: 1, spec: {}, bindings: [] }));
  expect(stub.callsOf('POST', /jobs$/)).toHaveLength(0);
  expect(view.result.current.notice?.text).toContain('真实制作尚未就绪');
});
