/** 节点包诊断必须在前端可见：缺哪些包、本次运行会怎样、下一步去哪补装。

node_pack_notes 是后端唯一的「节点包缺失」出口。前端不消费它，门禁降级就等于静默通过——
用户以为配好了，实际那几个节点在本次运行里被禁用。向导与基础配置面板共用同一段文案。

这两层断言各管一件事：
1) 文案函数本身：缺哪几个包、会禁用、下一步去哪补装；
2) 向导跑完之后屏上仍然看得到这段事实。阶段 note 会被后续阶段覆盖，
   降级提示必须有一个不被覆盖的载体，否则等于静默通过。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RealProductionWizard } from '../components/RealProductionWizard';
import { missingNodePacks, nodePackWarning } from '../components/nodePackNotes';
import { StudioStub, baseRoutes, jsonResponse, SYSTEM_STATUS, productionMode } from './studioStub';

let stub: StudioStub;
const callbacks = { onClose: vi.fn(), onFinished: vi.fn(), onOpenExpertSetup: vi.fn(), onRefresh: vi.fn() };
function route(method: string, suffix: string, body: unknown, status = 200) {
  stub.add({ method, pattern: new RegExp(suffix + '(?:\\?.*)?$'), respond: () => jsonResponse(body, status) });
}
function show() { return render(<RealProductionWizard mode="enable" projectId="proj-1" {...callbacks} />); }

/** 完整 happy-path 桩：模型 → 实例 → 工作流 → 运行配置 → 服务验证 → 激活。
 *  setup/readiness 保留 L2 层，否则向导在模型阶段就停下，走不到 /setup/configuration。 */
function fullPath() {
  route('GET', 'system/status', { ...SYSTEM_STATUS, production_mode: productionMode({ mode: 'READY' }) });
  let installed = false;
  stub.add({ method: 'GET', pattern: /setup\/readiness$/, respond: () => jsonResponse({ layers: [
    { layer: 'L2_files_verified', status: installed ? 'READY' : 'NOT_STARTED' },
  ] }) });
  route('POST', 'setup/install-plan', { plan_id: 'install', sha256: 'old', status: 'AWAITING_APPROVAL' });
  route('POST', 'install-plans/install/approve', { sha256: 'approved-install' });
  stub.add({ method: 'POST', pattern: /install-plans\/install\/execute$/, respond: () => {
    installed = true;
    return jsonResponse({ attempt_id: 'attempt-install', status: 'QUEUED', stage: 'QUEUED', plan_hash: 'approved-install' });
  } });
  stub.add({ method: 'GET', pattern: /installation-attempts\/attempt-install$/, respond: () => jsonResponse({
    attempt_id: 'attempt-install', plan_id: 'install', stage: 'FILES_VERIFIED_PENDING_RUNTIME',
    status: 'FILES_VERIFIED_PENDING_RUNTIME', confirmed_bytes: 4, total_bytes: 4, speed_bps: 0, retry_count: 0,
    cancel_requested: false, pause_requested: false, terminal: true, resumable: false,
  }) });
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

const MISSING_PACKS = ['ComfyUI-KJNodes', 'comfyui-videohelpersuite'];
/** 后端在门禁降级时写入的诊断：缺哪几个节点包。 */
function withMissingNodePacks() {
  route('POST', 'setup/configuration', {
    recipe: 'C:/tmp/managed_recipe.json',
    node_pack_notes: [{
      code: 'NODE_PACK_DIRECTORY_MISSING',
      missing: MISSING_PACKS,
      message: '这些节点包在安装的 custom_nodes 里找不到对应目录；请先在「安装与下载」补装节点。',
    }],
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  stub = new StudioStub(baseRoutes());
  fullPath();
});
afterEach(() => { stub.restore(); vi.restoreAllMocks(); localStorage.clear(); });

describe('节点包诊断文案', () => {
  it('列出缺失的节点包并给出下一步', () => {
    const warning = nodePackWarning([{
      code: 'NODE_PACK_DIRECTORY_MISSING',
      missing: ['ComfyUI-KJNodes', 'comfyui-videohelpersuite'],
      message: '这些节点包在安装的 custom_nodes 里找不到对应目录。',
    }]);
    expect(warning).toContain('ComfyUI-KJNodes');
    expect(warning).toContain('comfyui-videohelpersuite');
    expect(warning).toContain('2');
    expect(warning).toContain('安装与下载');
    expect(warning).toContain('禁用');
  });

  it('没有缺失时不产生噪音', () => {
    expect(nodePackWarning([])).toBe('');
    expect(nodePackWarning(undefined)).toBe('');
    expect(nodePackWarning([{ code: 'OTHER' }])).toBe('');
    expect(missingNodePacks(undefined)).toEqual([]);
  });
});

describe('向导里的节点包降级提示', () => {
  it('向导跑完之后仍然看得见缺哪些节点包、会禁用、下一步去哪补装', async () => {
    withMissingNodePacks();
    show();
    await screen.findByText(/真实制作已准备好/);
    expect(stub.callsOf('POST', /setup\/configuration$/)).toHaveLength(1);
    const notice = screen.getByTestId('wizard-node-pack-notice');
    expect(notice).toHaveTextContent('ComfyUI-KJNodes');
    expect(notice).toHaveTextContent('comfyui-videohelpersuite');
    expect(notice).toHaveTextContent('安装与下载');
    expect(notice).toHaveTextContent('禁用');
  });

  it('最终阶段文案不会把降级事实顶掉：缺包这件事必须留在屏上', async () => {
    withMissingNodePacks();
    show();
    await screen.findByText(/真实制作已准备好/);
    // 阶段区在最后确实显示「已验证」——所以降级事实不能只挂在阶段 note 上。
    expect(screen.getByTestId('wizard-stages')).toHaveTextContent('已验证');
    expect(document.body.textContent).toContain('ComfyUI-KJNodes');
    expect(document.body.textContent).toContain('安装与下载');
  });

  it('没有节点包缺失时不出现警示行', async () => {
    show();
    await screen.findByText(/真实制作已准备好/);
    expect(screen.queryByTestId('wizard-node-pack-notice')).toBeNull();
  });

  it('重新检查时按本次实际结果更新警示，不留上一次的残留', async () => {
    withMissingNodePacks();
    show();
    await screen.findByText(/真实制作已准备好/);
    expect(screen.getByTestId('wizard-node-pack-notice')).toBeInTheDocument();
    route('POST', 'setup/configuration', {});
    await userEvent.click(screen.getByRole('button', { name: /重新检查/ }));
    await waitFor(() => expect(screen.queryByTestId('wizard-node-pack-notice')).toBeNull());
  });
});
