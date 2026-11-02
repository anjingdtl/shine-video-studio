import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RealProductionWizard } from '../components/RealProductionWizard';
import { StudioStub, baseRoutes, jsonResponse, CATALOG, SYSTEM_STATUS, productionMode } from './studioStub';

let stub: StudioStub;
const callbacks = { onClose: vi.fn(), onFinished: vi.fn(), onOpenExpertSetup: vi.fn(), onRefresh: vi.fn() };
function show() { return render(<RealProductionWizard mode="enable" projectId="proj-1" {...callbacks} />); }
function route(method: string, suffix: string, body: unknown, status = 200) {
  stub.add({ method, pattern: new RegExp(suffix + '$'), respond: () => jsonResponse(body, status) });
}
function fullPath() {
  route('GET', 'system/status', { ...SYSTEM_STATUS, production_mode: productionMode({ mode: 'READY' }) });
  let installed = false;
  stub.add({ method: 'GET', pattern: /setup\/readiness$/, respond: () => jsonResponse({ layers: [
    { layer: 'L2_files_verified', status: installed ? 'READY' : 'NOT_STARTED' },
  ] }) });
  route('POST', 'setup/install-plan', { plan_id: 'install', sha256: 'old', status: 'AWAITING_APPROVAL' });
  route('POST', 'install-plans/install/approve', { sha256: 'approved-install' });
  stub.add({ method: 'POST', pattern: /install-plans\/install\/execute$/, respond: () => {
    installed = true; return jsonResponse({ status: 'FILES_VERIFIED' });
  } });
  let next = 0;
  stub.add({ method: 'POST', pattern: /setup\/next-instance-plan$/, respond: () => jsonResponse(next++ === 0
    ? { complete: false, plan_id: 'instance', sha256: 'old-instance', status: 'AWAITING_APPROVAL' }
    : { complete: true }) });
  route('POST', 'install-plans/instance/approve', { sha256: 'approved-instance' });
  route('POST', 'instance-plans/instance/execute', { status: 'COMPLETED' });
  route('POST', 'setup/profiles', { profiles: [] });
  route('POST', 'setup/configuration', {});
  route('GET', 'setup/runtime', { status: 'NOT_ACTIVATED', candidate: { project_id: 'proj-1', recipe_sha256: 'a'.repeat(64) } });
  route('POST', 'setup/services/validate', { status: 'READY', advisory_reviews: {
    VLM: { status: 'NOT_READY' }, ASR: { status: 'UNAVAILABLE' },
  } });
  route('POST', 'setup/runtime/activate', { status: 'ACTIVE' });
}
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  stub = new StudioStub(baseRoutes());
  fullPath();
});
afterEach(() => { stub.restore(); vi.restoreAllMocks(); localStorage.clear(); });

it('完整审批安装、实例、服务与激活路径使用最新哈希', async () => {
  show();
  await screen.findByText(/真实制作已准备好/);
  expect(stub.callsOf('POST', /approval-grants$/)).toHaveLength(2);
  expect(stub.callsOf('POST', /install-plans\/install\/execute$/)[0].body).toEqual({ plan_hash: 'approved-install', executor: 'local' });
  expect(stub.callsOf('POST', /instance-plans\/instance\/execute$/)[0].body).toEqual({ plan_hash: 'approved-instance' });
  for (const call of stub.callsOf('POST', /approval-grants$/)) expect(call.body).toMatchObject({ action: 'install' });
  for (const call of stub.callsOf('POST', /approve$/)) expect(call.body).toHaveProperty('evidence');
});

it('建议审片资源缺失仍通过 H3 验证并完成真实制作配置', async () => {
  show();
  await screen.findByText(/真实制作已准备好/);
  expect(stub.callsOf('POST', /setup\/runtime\/activate$/)).toHaveLength(1);
  expect(screen.getByTestId('wizard-stages')).toHaveTextContent(/未就绪时仍可制作/);
  expect(screen.queryByRole('button', { name: '到专业设置处理' })).toBeNull();
});

it('默认部署计划不下载可选 ASR/VLM 审片模型', async () => {
  route('GET', 'setup/catalog', { ...CATALOG, components: [
    { component_id: 'minimax-h3-diffusion', files: 3, role: null },
    { component_id: 'qwen3-vl-8b-fp8', files: 13, role: 'VLM' },
    { component_id: 'qwen3-asr-0.6b', files: 10, role: 'ASR' },
  ] });
  show();
  await screen.findByText(/真实制作已准备好/);
  const body = stub.callsOf('POST', /setup\/install-plan$/)[0].body as { components: string[] };
  expect(body.components).toEqual(['minimax-h3-diffusion']);
  expect(window.confirm).toHaveBeenCalledWith(expect.not.stringContaining('60.8 GB'));
});

it.each(['setup/environment', 'setup/storage', 'setup/existing-installations', 'setup/readiness', 'setup/catalog',
  'setup/install-plan', 'approval-grants', 'install-plans/install/approve', 'install-plans/install/execute',
  'setup/next-instance-plan', 'instance-plans/instance/execute', 'setup/profiles', 'setup/configuration',
  'setup/runtime', 'setup/services/validate', 'setup/runtime/activate'])('失败不留转圈并保留诊断：%s', async suffix => {
  const method = ['setup/environment', 'setup/storage', 'setup/existing-installations', 'setup/readiness', 'setup/catalog', 'setup/runtime'].includes(suffix) ? 'GET' : 'POST';
  route(method, suffix, { detail: 'CONTRACT_TEST_FAILURE' }, 503);
  show();
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent('CONTRACT_TEST_FAILURE'));
  expect(screen.queryByText(/真实制作已准备好/)).toBeNull();
});

it('取消确认不执行，重新检查读取实际后端状态', async () => {
  vi.mocked(window.confirm).mockReturnValue(false);
  show();
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent('已取消下载'));
  expect(stub.callsOf('POST', /execute$/)).toHaveLength(0);
  const before = stub.callsOf('GET', /install-plans/).length;
  await userEvent.click(screen.getByRole('button', { name: /重新检查/ }));
  await waitFor(() => expect(stub.callsOf('GET', /install-plans/).length).toBeGreaterThan(before));
});

it('未知计划不重投，已激活且空闲的配置无需重装', async () => {
  route('GET', 'comfyops/install-plans\\?project_id=proj-1', [{ id: 'unknown', status: 'NEEDS_REVIEW' }]);
  const view = show();
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent('存在执行中或需要对账'));
  expect(stub.callsOf('POST', /execute$/)).toHaveLength(0);
  view.unmount();
  route('GET', 'comfyops/install-plans\\?project_id=proj-1', []);
  route('GET', 'setup/runtime', { status: 'ACTIVE', candidate: { project_id: 'proj-1' }, service_validation: { status: 'READY' } });
  show();
  await screen.findByText(/渲染器空闲停止时也无需重新安装/);
  expect(stub.callsOf('POST', /execute$/)).toHaveLength(0);
});

it('关闭或切换作品后不继续发起后续副作用', async () => {
  let finish!: (value: ReturnType<typeof jsonResponse>) => void;
  stub.add({ method: 'POST', pattern: /install-plans\/install\/execute$/, respond: () => new Promise(resolve => { finish = resolve; }) });
  const view = show();
  await waitFor(() => expect(stub.callsOf('POST', /install-plans\/install\/execute$/)).toHaveLength(1));
  view.rerender(<RealProductionWizard mode="enable" projectId="proj-2" {...callbacks} />);
  finish(jsonResponse({ status: 'FILES_VERIFIED' }));
  await new Promise(resolve => setTimeout(resolve, 25));
  expect(stub.callsOf('POST', /next-instance-plan$/)).toHaveLength(0);
  expect(callbacks.onRefresh).not.toHaveBeenCalled();
});

it('核心 LLM 未配置时在下载前阻断，并提供同一配置入口', async () => {
  route('GET', 'setup/llm-readiness', { status: 'BLOCKED', roles: [{ role: 'director', code: 'LLM_ROUTE_REQUIRED' }] });
  show();
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent('LLM_SETUP_REQUIRED'));
  expect(screen.getByTestId('llm-setup')).toBeInTheDocument();
  expect(stub.callsOf('POST', /install-plan$/)).toHaveLength(0);
  route('GET', 'setup/llm-readiness', { status: 'READY', roles: [] });
  await userEvent.click(screen.getByRole('button', { name: '重新检查' }));
  await screen.findByText(/真实制作已准备好/);
});
it('识别并确认复用已安装 ComfyUI 与 H3，完成准备时不重复下载', async () => {
  route('GET', 'setup/catalog', { ...CATALOG, components: [{ component_id: 'minimax-h3-diffusion', files: 1 }] });
  route('GET', 'setup/readiness', { layers: [{ layer: 'L2_files_verified', status: 'READY' }] });
  stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({
    installations: ['F:\\AI\\ComfyUI'],
  }) });
  stub.add({ method: 'POST', pattern: /existing-installations\/inspect$/, respond: () => jsonResponse({
    root: 'F:\\AI\\ComfyUI', snapshot: 'b'.repeat(64), candidate_bytes: 13,
    matches: [{ component_id: 'minimax-h3-diffusion', size: 13 }],
    software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
      runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false },
  }) });
  stub.add({ method: 'POST', pattern: /existing-installations\/verify$/, respond: () => jsonResponse({
    status: 'INSTALLED_VERIFIED', software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
      runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false },
    verified: [{ component_id: 'minimax-h3-diffusion', size: 13 }], rejected: [], verified_bytes: 13, copied_bytes: 0,
  }) });
  route('POST', 'existing-installations/adopt', {
    status: 'ADOPTED', installed_h3_bytes: 13, copied_bytes: 0, downloaded_bytes: 0,
  });
  show();
  await screen.findByTestId('wizard-reuse');
  await userEvent.click(screen.getByRole('button', { name: '核验并确认复用' }));
  await screen.findByText(/真实制作已准备好/);
  expect(stub.callsOf('POST', /existing-installations\/verify$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /existing-installations\/adopt$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /existing-installations\/import-models$/)).toHaveLength(0);
  expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
  expect(stub.callsOf('POST', /setup\/next-instance-plan$/)).toHaveLength(0);
  expect(stub.callsOf('POST', /setup\/configuration$/)).toHaveLength(1);
});
it('长时间核验使用宽限时限，并对账进行中的请求而不重复扫权重', async () => {
  const timeout = vi.spyOn(AbortSignal, 'timeout');
  const software = { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
    runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false };
  stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({
    installations: ['F:\\AI\\ComfyUI'],
  }) });
  stub.add({ method: 'POST', pattern: /existing-installations\/inspect$/, respond: () => jsonResponse({
    root: 'F:\\AI\\ComfyUI', snapshot: 'c'.repeat(64), candidate_bytes: 13,
    matches: [{ component_id: 'h3', size: 13 }], software,
  }) });
  let verifyCalls = 0;
  stub.add({ method: 'POST', pattern: /existing-installations\/verify$/, respond: () => jsonResponse(
    verifyCalls++ === 0
        ? { status: 'VERIFYING', retry_after_ms: 0, software }
      : { status: 'INSTALLED_VERIFIED', software, verified: [{ component_id: 'minimax-h3-diffusion', size: 13 }],
        rejected: [], verified_bytes: 13, copied_bytes: 0, verification_reused: true },
  ) });
  vi.mocked(window.confirm).mockReturnValue(false);
  show();
  await screen.findByTestId('wizard-reuse');
  await userEvent.click(screen.getByRole('button', { name: '核验并确认复用' }));
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent(/你取消了本次接入/));
  expect(verifyCalls).toBe(2);
  expect(timeout).toHaveBeenCalledWith(3_600_000);
  expect(screen.getByTestId('wizard-stages')).toHaveTextContent(/已恢复此前完成的核验结果，未重复读取权重/);
});
it('确认复用后若其他固定文件缺失，只提示，不自动下载或重装', async () => {
  route('GET', 'setup/catalog', { ...CATALOG, components: [{ component_id: 'minimax-h3-diffusion', files: 1 }] });
  route('GET', 'setup/readiness', { layers: [{
    layer: 'L2_files_verified', status: 'PENDING', missing_components: ['qwen3-asr-0.6b'],
  }] });
  stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({
    installations: ['F:\\AI\\ComfyUI'],
  }) });
  stub.add({ method: 'POST', pattern: /existing-installations\/inspect$/, respond: () => jsonResponse({
    root: 'F:\\AI\\ComfyUI', snapshot: 'd'.repeat(64), candidate_bytes: 13,
    matches: [{ component_id: 'minimax-h3-diffusion', size: 13 }],
    software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
      runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false },
  }) });
  stub.add({ method: 'POST', pattern: /existing-installations\/verify$/, respond: () => jsonResponse({
    status: 'INSTALLED_VERIFIED', software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
      runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false },
    verified: [{ component_id: 'minimax-h3-diffusion', size: 13 }], rejected: [], verified_bytes: 13, copied_bytes: 0,
  }) });
  route('POST', 'existing-installations/adopt', {
    status: 'ADOPTED', installed_h3_bytes: 13, copied_bytes: 0, downloaded_bytes: 0,
  });
  show();
  await screen.findByTestId('wizard-reuse');
  await userEvent.click(screen.getByRole('button', { name: '核验并确认复用' }));
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent(/不会因为复用而自动下载新模型/));
  expect(stub.callsOf('POST', /existing-installations\/adopt$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
  expect(stub.callsOf('POST', /setup\/next-instance-plan$/)).toHaveLength(0);
});
it('已有安装登记失败时给出中文原因与错误码，不把原始 JSON 留给用户', async () => {
  route('GET', 'setup/catalog', { ...CATALOG, components: [{ component_id: 'minimax-h3-diffusion', files: 1 }] });
  stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({
    installations: ['F:\\AI\\ComfyUI'],
  }) });
  stub.add({ method: 'POST', pattern: /existing-installations\/inspect$/, respond: () => jsonResponse({
    root: 'F:\\AI\\ComfyUI', snapshot: 'e'.repeat(64), candidate_bytes: 13,
    matches: [{ component_id: 'minimax-h3-diffusion', size: 13 }],
    software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH', runtime: { status: 'OK' } },
  }) });
  stub.add({ method: 'POST', pattern: /existing-installations\/verify$/, respond: () => jsonResponse({
    status: 'INSTALLED_VERIFIED', software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH', runtime: { status: 'OK' } },
    verified: [{ component_id: 'minimax-h3-diffusion', size: 13 }], rejected: [], verified_bytes: 13, copied_bytes: 0,
  }) });
  route('POST', 'existing-installations/adopt', { detail: {
    code: 'EXISTING_H3_MODEL_FILES_INCOMPLETE', missing_components: ['minimax-h3-diffusion'], downloaded_bytes: 0,
  } }, 409);

  show();
  await screen.findByTestId('wizard-reuse');
  await userEvent.click(screen.getByRole('button', { name: '核验并确认复用' }));
  const result = await screen.findByTestId('wizard-result');
  await waitFor(() => expect(result).toHaveTextContent(/ComfyUI 已确认安装/));
  expect(result).toHaveTextContent('EXISTING_H3_MODEL_FILES_INCOMPLETE');
  expect(result).not.toHaveTextContent('missing_components');
  expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
});
it('React 严格模式初次挂载仍完成，不能并发重复创建计划', async () => {
  render(<StrictMode><RealProductionWizard mode="enable" projectId="proj-1" {...callbacks} /></StrictMode>);
  await screen.findByText(/真实制作已准备好/);
  expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /install-plans\/install\/execute$/)).toHaveLength(1);
});
it('执行断线保留未确认标记，重新检查不能重投', async () => {
  stub.add({ method: 'POST', pattern: /install-plans\/install\/execute$/, respond: () => { throw new TypeError('connection lost'); } });
  show();
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent('EXECUTION_UNCONFIRMED'));
  expect(localStorage.getItem('shine.setupUnconfirmed.install')).toBe('1');
  route('GET', 'comfyops/install-plans\\?project_id=proj-1', [{ id: 'install', status: 'APPROVED', payload: { deployment: true } }]);
  await userEvent.click(screen.getByRole('button', { name: '重新检查' }));
  await waitFor(() => expect(screen.getByTestId('wizard-result')).toHaveTextContent('EXECUTION_UNCONFIRMED'));
  expect(stub.callsOf('POST', /install-plans\/install\/execute$/)).toHaveLength(1);
});
