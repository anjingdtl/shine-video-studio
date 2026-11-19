/** 制作意图收敛（2026-11-19 高级设置收敛轮）：手动切演示的平行开关已下线，
 *  演示只在后端 production_mode 为 DEMO 时可用；能力丢失不得把真实提交变成演示。 */
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
it.each(['BLOCKED', 'RECONCILE_REQUIRED'])('真实意图遇到 %s 不自动变成 mock 或提交任务', async mode => {
  ready();
  const view = renderHook(useStudio);
  await waitFor(() => expect(view.result.current.realSubmissionAllowed).toBe(true));
  stub.add({ method: 'GET', pattern: /system\/status$/, respond: () => jsonResponse({ ...SYSTEM_STATUS,
    production_mode: productionMode({ mode }) }) });
  await act(() => view.result.current.refreshAll());
  expect(view.result.current.realSubmissionAllowed).toBe(false);
  expect(view.result.current.queueProvider).toBe('comfyui');
  await act(() => view.result.current.queueShot({ id: 'shot', project_id: 'proj-1', name: 'shot', revision: 1, spec: {}, bindings: [] }));
  expect(stub.callsOf('POST', /jobs$/)).toHaveLength(0);
  expect(view.result.current.notice?.text).toContain('真实制作尚未就绪');
});
it('DEMO 机器单镜走演示引擎，不冒充真实', async () => {
  stub.add({ method: 'POST', pattern: /projects\/proj-1\/jobs$/, respond: () => jsonResponse({ id: 'job-1' }, 201) });
  const view = renderHook(useStudio);
  await waitFor(() => expect(view.result.current.projectId).toBe('proj-1'));
  expect(view.result.current.realSubmissionAllowed).toBe(false);
  expect(view.result.current.queueProvider).toBe('mock');
  await act(() => view.result.current.queueShot({ id: 'shot', project_id: 'proj-1', name: 'shot', revision: 1, spec: {}, bindings: [] }));
  const body = stub.callsOf('POST', /jobs$/)[0].body as { provider: string };
  expect(body.provider).toBe('mock');
});
it('READY 机器不再读旧的手动演示残留意图', async () => {
  localStorage.setItem('shine.productionIntent', 'mock');
  ready();
  const view = renderHook(useStudio);
  await waitFor(() => expect(view.result.current.realSubmissionAllowed).toBe(true));
  expect(view.result.current.queueProvider).toBe('comfyui');
});
