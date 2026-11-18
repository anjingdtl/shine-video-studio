import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

/**
 * Mainline V3（归一后）：一次点击提交持久整备任务，准入与下载由后端负责。
 *
 * C-01: 浏览器不再做全局四站诊断一刀切，也不自己串行驱动安装计划；它把勾选
 *       的能力一次性交给 /setup/prepare/execute，按能力收窄的下载准入在后端。
 * C-02: 没有可安装 H3 方案的机器仍能准备独立能力（按钮不被 selectedVariant 锁死）。
 * C-03: 能力卡用「①文件 / ②运行」两步如实区分「文件已核验」与「运行已就绪」。
 */

const IMAGE_BYTES = 17_283_091_112;

const INT8 = {
  variant_id: 'h3-ref2va-int8-convrot', title: 'H3 Ref2VA INT8 ConvRot（当前生产基线）',
  acceptance_status: 'SUPPORTED', status: 'SUPPORTED', usable_in_production: true,
  auto_download_allowed: true, catalog_id: 'h3-ref2va-windows-x64', total_bytes: 42_470_585_471,
  required_component_ids: ['minimax-h3-diffusion', 'minimax-h3-text-encoder', 'minimax-h3-vae'],
  install_component_ids: ['minimax-h3-diffusion', 'minimax-h3-text-encoder', 'minimax-h3-vae'],
  blockers: [], unknowns: [],
};

function routesNoH3(): ReturnType<typeof baseRoutes> {
  const routes = baseRoutes();
  routes.push(
    { method: 'GET', pattern: /setup\/variant-availability/, respond: () => jsonResponse({ adopted_root: null, variants: [] }) },
    { method: 'GET', pattern: /setup\/variant-recommendations$/, respond: () => jsonResponse({
      tier: 'below_lowend', host: { gpu: 'Intel Arc A380', vram_mb: 6144, ram_mb: 31539 },
      recommended: [], candidates: [], blocked: [],
    }) },
    { method: 'GET', pattern: /setup\/image-readiness$/, respond: () => jsonResponse({
      configured: false, layer: 'NOT_CONFIGURED', families: {}, renderer: { state: 'NOT_STARTED' },
      blockers: [], upstream: { required_bytes: IMAGE_BYTES },
    }) },
  );
  return routes;
}

function routesWithH3(): ReturnType<typeof baseRoutes> {
  const routes = baseRoutes();
  routes.push(
    { method: 'GET', pattern: /setup\/variant-availability/, respond: () => jsonResponse({ adopted_root: null, variants: [] }) },
    { method: 'GET', pattern: /setup\/variant-recommendations$/, respond: () => jsonResponse({
      tier: 'standard', host: { gpu: 'NVIDIA GeForce RTX 5080', vram_mb: 16368, ram_mb: 31539 },
      recommended: [INT8], candidates: [], blocked: [],
    }) },
    { method: 'GET', pattern: /setup\/image-readiness$/, respond: () => jsonResponse({
      configured: false, layer: 'NOT_CONFIGURED', families: {}, renderer: { state: 'NOT_STARTED' },
      blockers: [], upstream: { required_bytes: IMAGE_BYTES },
    }) },
  );
  return routes;
}

/** 持久整备任务回执 + 进度快照。 */
function prepareRoutes(): ReturnType<typeof baseRoutes> {
  return [
    { method: 'POST', pattern: /setup\/prepare\/execute$/, respond: () => jsonResponse({
      status: 'QUEUED', task_id: 'setup-test', steps: ['RECONCILE', 'DOWNLOAD', 'TARGET', 'RECIPE', 'VALIDATE'],
      message: '等待后台开始',
    }) },
    { method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse({
      snapshot_version: 1, computed_at: '2026-11-16T00:00:00', project_id: 'proj-1',
      installation: { selected: null, candidates: [], choice_required: false },
      capabilities: {}, progress: [], primary_capability: 'image', action_required: null, blockers: [],
      preparation: { status: 'RUNNING', task_id: 'setup-test', current: 'DOWNLOAD',
        steps: [{ id: 'RECONCILE', label: '核验并登记已有字节', status: 'DONE' },
                { id: 'DOWNLOAD', label: '下载并安装缺失文件', status: 'RUNNING' }],
        done: 1, total: 2, message: '正在下载并安装' },
    }) },
  ];
}

let stub: StudioStub;
beforeEach(() => { window.sessionStorage.clear(); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

async function detect() {
  await screen.findByText(/上次检测/, undefined, { timeout: 20000 });
}

describe('C-01：一次提交后台任务，浏览器不做全局一刀切', () => {
  it('只准备素材出图时，把 image 能力一次性交给后台任务', async () => {
    stub = new StudioStub([...routesNoH3(), ...prepareRoutes()]);
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    expect(screen.getByRole('checkbox', { name: /H3 视频生成/ })).not.toBeChecked();
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备|并完成配置)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(stub.callsOf('POST', /setup\/prepare\/execute/)).toHaveLength(1), { timeout: 20000 });
    const body = stub.callsOf('POST', /setup\/prepare\/execute/)[0].body as { capabilities: string[] };
    expect(body.capabilities).toEqual(['image']);
    // 浏览器不再自己驱动安装计划，也不做全局四站诊断。
    expect(stub.callsOf('POST', /setup\/install-plan/)).toHaveLength(0);
    expect(stub.callsOf('GET', /setup\/connectivity$/)).toHaveLength(0);
    // 界面显示后台整备在推进（能力卡文字投影）；顶部机器进度条在页面级渲染。
    expect(await screen.findByText(/正在后台准备/)).toBeTruthy();
  });
});

describe('C-02：H3 方案只约束 H3 能力', () => {
  it('没有可安装 H3 方案的机器仍能准备独立能力', async () => {
    stub = new StudioStub([...routesNoH3(), ...prepareRoutes()]);
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    expect(screen.getByRole('checkbox', { name: /素材出图/ })).toBeChecked();
    const confirm = screen.getByRole('button', { name: /确认(下载|准备|整备|并完成配置)/ });
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await user.click(screen.getByRole('button', { name: '开始准备' }));
    await waitFor(() => expect(stub.callsOf('POST', /setup\/prepare\/execute/)).toHaveLength(1), { timeout: 20000 });
    const body = stub.callsOf('POST', /setup\/prepare\/execute/)[0].body as { capabilities: string[] };
    expect(body.capabilities).toContain('image');
  });
});

describe('C-03：文件已核验不冒充能力就绪', () => {
  it('能力卡用两步区分文件与运行状态', async () => {
    stub = new StudioStub([...routesWithH3(), ...prepareRoutes()]);
    stub.add({ method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse({
      snapshot_version: 1, computed_at: '2026-11-16T00:00:00', project_id: 'proj-1',
      installation: { selected: null, candidates: [], choice_required: false },
      capabilities: {
        h3: { key: 'h3', label: 'H3 视频生成', state: 'VERIFIED', optional: false,
          facts: { FILES_FOUND: true, FILES_VERIFIED: true, INSTANCE_BOUND: false, WORKFLOW_VALIDATED: false, RENDER_VERIFIED: false },
          summary: '资源已核验，还没有绑定到当前 ComfyUI 实例。', next_step: null,
          missing_files: [], missing_bytes: 0, advisories: [],
          two_step: { files: { state: 'READY', label: '文件已就绪并核验' },
                      run: { state: 'BLOCKED', label: '跑不通', reason: '资源尚未绑定到本机渲染器或运行合同未核对' } } },
      },
      progress: [], primary_capability: 'h3', action_required: null, blockers: [],
      preparation: { status: 'IDLE', task_id: null, current: null, steps: [], message: null },
    }) });
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    expect(await screen.findByText('文件已就绪并核验')).toBeTruthy();
    expect(screen.getByText(/跑不通/)).toBeTruthy();
    // 文件已核验 ≠ 运行已就绪：运行行明确未就绪，并提供一键修复。
    expect(screen.getByRole('button', { name: /一键绑定渲染器并核对运行合同/ })).toBeTruthy();
    expect(screen.queryByText('已验证可运行')).toBeNull();
  });
});