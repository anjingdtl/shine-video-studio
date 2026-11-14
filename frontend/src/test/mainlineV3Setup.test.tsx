import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

/**
 * Mainline V3 WP1: plan-scoped download admission and honest capability
 * outcomes.
 *
 * C-01: the old one-shot four-site check (``GET /setup/connectivity`` with
 * any ``blocked`` entry aborting the whole preparation) turned an unrelated
 * host outage into a global refusal.  The image capability only downloads
 * from huggingface.co, so codeload/PyPI/torch being down must not stop it.
 *
 * C-02: ``selectedVariant`` was a condition of every capability.  A machine
 * without any installable H3 scheme must still be able to prepare image or
 * review weights.
 *
 * C-03: an attempt that ends at FILES_VERIFIED_PENDING_RUNTIME is "files
 * verified", never "capability ready".
 */

const IMAGE_COMPONENTS = ['qwen-image-2.1-unet', 'qwen-image-2.1-text-encoder', 'qwen-image-2.1-vae'];
const IMAGE_BYTES = 17_283_091_112;

const INT8 = {
  variant_id: 'h3-ref2va-int8-convrot', title: 'H3 Ref2VA INT8 ConvRot（当前生产基线）',
  acceptance_status: 'SUPPORTED', status: 'SUPPORTED', usable_in_production: true,
  auto_download_allowed: true, catalog_id: 'h3-ref2va-windows-x64', total_bytes: 42_470_585_471,
  required_component_ids: ['minimax-h3-diffusion', 'minimax-h3-text-encoder', 'minimax-h3-vae'],
  install_component_ids: ['minimax-h3-diffusion', 'minimax-h3-text-encoder', 'minimax-h3-vae'],
  blockers: [], unknowns: [],
};

/** No H3 scheme at all on this machine (empty recommendation lists). */
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

/** A capable machine with the INT8 variant recommended and no weights on disk. */
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

/** Plan-scoped preflight answers per catalog. */
function preflightRoutes(byCatalog: Record<string, string[]>): ReturnType<typeof baseRoutes> {
  return [{
    method: 'POST', pattern: /setup\/connectivity\/for-plan$/,
    respond: (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? '{}')) as { catalog?: string; components?: string[] };
      const blocked = byCatalog[body.catalog ?? ''] ?? [];
      const rows = blocked.map((probeId) => ({
        probe_id: probeId, label: 'Hugging Face 模型库', host: 'huggingface.co',
        status: 'BLOCKED', component_ids: body.components ?? [], download_hosts: ['huggingface.co'],
      }));
      return jsonResponse({
        status: blocked.length ? 'BLOCKED' : 'OK', checked_at: '2026-11-14T00:00:00',
        blocked, degraded: [], required: rows, guidance: [],
        catalog: body.catalog ?? '', capability: 'fixture',
      });
    },
  }];
}

/** The controlled download queue: approve, execute, and a finished attempt. */
function installQueueRoutes(stage = 'FILES_VERIFIED_PENDING_RUNTIME'): ReturnType<typeof baseRoutes> {
  return [
    { method: 'POST', pattern: /comfyops\/install-plans\/[^/]+\/approve$/, respond: () => jsonResponse({ sha256: 'b'.repeat(64) }) },
    {
      method: 'POST', pattern: /comfyops\/install-plans\/[^/]+\/execute$/,
      respond: () => jsonResponse({
        attempt_id: 'attempt-image-1', status: 'QUEUED', stage: 'QUEUED', plan_hash: 'b'.repeat(64),
      }),
    },
    {
      method: 'GET', pattern: /comfyops\/installation-attempts\/attempt-image-1$/,
      respond: () => jsonResponse({
        attempt_id: 'attempt-image-1', plan_id: 'plan-image', stage, status: 'SUCCEEDED',
        confirmed_bytes: IMAGE_BYTES, total_bytes: IMAGE_BYTES, speed_bps: 0,
        retry_count: 0, cancel_requested: false, pause_requested: false, terminal: true, resumable: false,
      }),
    },
  ];
}

function imagePlanRoutes(): ReturnType<typeof baseRoutes> {
  return [{
    method: 'POST', pattern: /setup\/install-plan$/,
    respond: () => jsonResponse({
      plan_id: 'plan-image', sha256: 'b'.repeat(64), status: 'AWAITING_APPROVAL',
      download_bytes: IMAGE_BYTES, disk_required: 40 * 1024 ** 3, packages: IMAGE_COMPONENTS,
      capability: 'image', variant_id: null, runtime_advisories: [],
    }),
  }];
}

let stub: StudioStub;
beforeEach(() => { window.sessionStorage.clear(); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

async function detect(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '检测本机' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '重新检测' })).toBeEnabled(), { timeout: 20000 });
}

async function prepareImageOnly(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox', { name: /H3 视频生成/ }));
  expect(screen.getByRole('checkbox', { name: /H3 视频生成/ })).not.toBeChecked();
  await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
  await user.click(screen.getByRole('button', { name: '开始准备' }));
}

describe('C-01：下载准入按计划收窄，不再被无关站点一刀切', () => {
  it('只准备素材出图时，codeload 失联不拦下载（旧实现会整体中止）', async () => {
    stub = new StudioStub([
      ...routesNoH3(),
      ...preflightRoutes({ 'qwen-image-2.1-windows-x64': [] }),
      ...imagePlanRoutes(),
      ...installQueueRoutes(),
    ]);
    // 旧版全局诊断视图：四个站点里 codeload 失联。这个路由在下载准入里
    // 必须不再被调用；它只作为诊断保留。
    stub.add({
      method: 'GET', pattern: /setup\/connectivity$/,
      respond: () => jsonResponse({ status: 'PARTIAL', blocked: ['codeload'], degraded: [], upstreams: [], guidance: [] }),
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    await prepareImageOnly(user);

    // 图片计划进入了受控下载队列——无关站点失联没有拦下合法能力。
    await waitFor(() => expect(stub.callsOf('POST', /comfyops\/install-plans\/[^/]+\/execute/)).toHaveLength(1), { timeout: 20000 });
    const plans = stub.callsOf('POST', /setup\/install-plan/).map((call) => call.body as Record<string, unknown>);
    expect(plans).toHaveLength(1);
    expect(plans[0].catalog).toBe('qwen-image-2.1-windows-x64');
    // 准入走的是按计划的 for-plan 预检，不是全局四站诊断。
    expect(stub.callsOf('GET', /setup\/connectivity$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /setup\/connectivity\/for-plan$/)).toHaveLength(1);
    expect(screen.queryByText(/下载源无法连接/)).toBeNull();
  });

  it('H3 的必需源失联时，按能力具名阻断并给恢复指引，不产生虚假成功', async () => {
    stub = new StudioStub([
      ...routesWithH3(),
      ...preflightRoutes({
        'h3-ref2va-windows-x64': ['huggingface'],
        'qwen-image-2.1-windows-x64': ['huggingface'],
      }),
    ]);

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    expect(screen.getByRole('checkbox', { name: /H3 视频生成/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /素材出图/ })).toBeChecked();
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    // 两项能力都拿到自己的具名结论，而不是一条全局报错。
    await waitFor(() => expect(screen.getByRole('list', { name: '各项能力的准备结论' })).toBeTruthy(), { timeout: 20000 });
    const outcomes = screen.getByRole('list', { name: '各项能力的准备结论' });
    expect(outcomes).toHaveTextContent('H3 视频生成');
    expect(outcomes).toHaveTextContent('素材出图');
    expect(outcomes).toHaveTextContent('未完成');
    expect(outcomes).toHaveTextContent('无法连接');
    // 没有任何计划被提交，也没有任何能力被写成已就绪。
    expect(stub.callsOf('POST', /setup\/install-plan/)).toHaveLength(0);
    expect(stub.callsOf('POST', /comfyops\/install-plans\/[^/]+\/execute/)).toHaveLength(0);
    expect(outcomes).not.toHaveTextContent('已就绪');
  });
});

describe('C-02：H3 方案只约束 H3 能力', () => {
  it('没有可安装 H3 方案的机器仍能准备独立能力（按钮不再被 selectedVariant 锁死）', async () => {
    stub = new StudioStub([
      ...routesNoH3(),
      ...preflightRoutes({ 'qwen-image-2.1-windows-x64': [] }),
      ...imagePlanRoutes(),
      ...installQueueRoutes(),
    ]);

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    await user.click(screen.getByRole('checkbox', { name: /H3 视频生成/ }));
    expect(screen.getByRole('checkbox', { name: /素材出图/ })).toBeChecked();

    // 旧实现：selectedVariant 为空 → canPrepare 永远 false，按钮禁用。
    expect(screen.getByRole('button', { name: /确认(下载|准备|整备)/ })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));
    await waitFor(() => expect(stub.callsOf('POST', /comfyops\/install-plans\/[^/]+\/execute/)).toHaveLength(1), { timeout: 20000 });
  });
});

describe('C-03：文件已核验不冒充能力就绪', () => {
  it('终态 FILES_VERIFIED_PENDING_RUNTIME 显示「文件已核验 · 运行待验证」，不写「资源已就绪」', async () => {
    stub = new StudioStub([
      ...routesNoH3(),
      ...preflightRoutes({ 'qwen-image-2.1-windows-x64': [] }),
      ...imagePlanRoutes(),
      ...installQueueRoutes('FILES_VERIFIED_PENDING_RUNTIME'),
    ]);

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    await prepareImageOnly(user);

    await waitFor(() => expect(screen.getByRole('list', { name: '各项能力的准备结论' })).toBeTruthy(), { timeout: 20000 });
    const outcomes = screen.getByRole('list', { name: '各项能力的准备结论' });
    expect(outcomes).toHaveTextContent('文件已核验 · 运行待验证');
    expect(outcomes).not.toHaveTextContent('资源已就绪');
    await waitFor(() => expect(screen.getByText(/素材出图文件已核验（运行待验证）/)).toBeTruthy(), { timeout: 20000 });
  });
});
