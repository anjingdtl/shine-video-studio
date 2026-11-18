import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import { describeDetail } from '../api';
import { StudioStub, baseRoutes, jsonResponse, setupSummary } from './studioStub';

/**
 * The reported failure, on the reported machine.
 *
 * An RTX 4070 (12282 MiB), 32 GB class of RAM, ~500 GB free.  The H3 INT8
 * files are on disk (3/3), the global readiness is not READY, and the person
 * ticked "素材出图" and pressed the download button.  Nothing downloaded: the
 * answer was HOST_NOT_QUALIFIED from the *H3* admission gate.
 *
 * The fix under test: the H3 production gate belongs to the H3 capability
 * alone.  A plan carries one capability's components and nothing else, so an
 * unrelated hardware gate can never reach an unrelated download.
 */

const INT8 = {
  variant_id: 'h3-ref2va-int8-convrot', title: 'H3 Ref2VA INT8 ConvRot（当前生产基线）',
  acceptance_status: 'SUPPORTED', status: 'BLOCKED', usable_in_production: false,
  auto_download_allowed: true, catalog_id: 'h3-ref2va-windows-x64', total_bytes: 42_470_585_471,
  required_component_ids: ['minimax-h3-diffusion', 'minimax-h3-text-encoder', 'minimax-h3-vae'],
  install_component_ids: ['minimax-h3-diffusion', 'minimax-h3-text-encoder', 'minimax-h3-vae'],
  blockers: [{ code: 'VRAM_INSUFFICIENT', required: 15360, actual: 12282 }],
};

const IMAGE_BYTES = 17_283_091_112;

/** The 4070 sample: the three H3 weight files are already on disk. */
function routes4070(options: { h3FilesPresent?: boolean } = {}): ReturnType<typeof baseRoutes> {
  const present = options.h3FilesPresent ?? true;
  const routes = baseRoutes();
  routes.push(
    { method: 'GET', pattern: /setup\/connectivity$/, respond: () => jsonResponse({ blocked: [] }) },
    { method: 'GET', pattern: /setup\/variant-availability/, respond: () => jsonResponse({
      adopted_root: null,
      variants: present ? [{
        variant_id: INT8.variant_id, installed: true, verified: true, bound: true,
        profiles_registered: true, runtime_validated: true, available: false,
        selectable_for_preparation: true, trial_ready: false, production_ready: false,
        acceptance_status: 'SUPPORTED', reason: '本机已就绪',
      }] : [],
    }) },
    {
      method: 'GET', pattern: /setup\/variant-recommendations$/,
      respond: () => jsonResponse({
        tier: 'lowend_12gb', host: { gpu: 'NVIDIA GeForce RTX 4070', vram_mb: 12282, ram_mb: 31539 },
        recommended: [], candidates: [INT8], blocked: [],
      }),
    },
    {
      method: 'GET', pattern: /setup\/existing-installations$/,
      respond: () => jsonResponse({ installations: ['D:////ComfyUI'] }),
    },
    {
      method: 'POST', pattern: /setup\/existing-installations\/inspect$/,
      respond: () => jsonResponse({
        root: 'D:////ComfyUI', snapshot: 'a'.repeat(64), candidate_bytes: 1, scanned_files: 3,
        matches: present
          ? INT8.required_component_ids.map((id, index) => ({
            component_id: id, relative_path: `models/${id}.safetensors`, size: 1024 + index,
          }))
          : [],
        missing: [], software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH' },
      }),
    },
    {
      method: 'GET', pattern: /setup\/image-readiness$/,
      respond: () => jsonResponse({
        configured: false, layer: 'NOT_CONFIGURED', families: {}, renderer: { state: 'NOT_STARTED' },
        blockers: [], upstream: { required_bytes: IMAGE_BYTES },
      }),
    },
  );
  return routes;
}

/** The controlled download queue: approve, execute, and a finished attempt. */
function installQueueRoutes(): ReturnType<typeof baseRoutes> {
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
        attempt_id: 'attempt-image-1', plan_id: 'plan-image', stage: 'FILES_VERIFIED_PENDING_RUNTIME',
        status: 'SUCCEEDED', confirmed_bytes: IMAGE_BYTES, total_bytes: IMAGE_BYTES, speed_bps: 0,
        retry_count: 0, cancel_requested: false, pause_requested: false, terminal: true, resumable: false,
      }),
    },
  ];
}

let stub: StudioStub;
beforeEach(() => {
  window.sessionStorage.clear();
});
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

async function detect() {
  // 打开就检测：等「上次检测」出现，说明服务端事实已经回来。
  await screen.findByText(/上次检测/, undefined, { timeout: 20000 });
}

describe('CASE-01：4070 只下载素材出图权重', () => {
  it('一次提交只包含勾选的能力,浏览器不再逐能力提交安装计划', async () => {
    stub = new StudioStub([...routes4070(), ...installQueueRoutes()]);
    stub.add({
      method: 'POST', pattern: /setup\/prepare\/execute$/,
      respond: () => jsonResponse({ status: 'QUEUED', task_id: 'setup-1',
        steps: ['RECONCILE', 'DOWNLOAD', 'INSTANCE', 'TARGET', 'RECIPE', 'IMAGE_BIND', 'IMAGE_VERIFY'] }),
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();

    // 默认全空：由用户自行勾选素材出图。
    expect(screen.getByRole('checkbox', { name: /H3 视频生成/ })).not.toBeChecked();
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    expect(screen.getByRole('checkbox', { name: /素材出图/ })).toBeChecked();

    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(stub.callsOf('POST', /setup\/prepare\/execute/)).toHaveLength(1), { timeout: 20000 });
    const body = stub.callsOf('POST', /setup\/prepare\/execute/)[0].body as { capabilities: string[] };
    // 任务只带勾选的能力;H3 的硬件门禁由后端在下载步骤按能力各自判定。
    expect(body.capabilities).toEqual(['image']);
    // 浏览器不再串行驱动安装计划/审批/执行。
    expect(stub.callsOf('POST', /setup\/install-plan/)).toHaveLength(0);
    expect(stub.callsOf('POST', /comfyops\/install-plans\/[^/]+\/execute/)).toHaveLength(0);
    expect(screen.queryByText(/HOST_NOT_QUALIFIED/)).toBeNull();
  });

  it('提交后显示已交给后台,不替安装器下终态结论', async () => {
    stub = new StudioStub([...routes4070(), ...installQueueRoutes()]);
    stub.add({
      method: 'POST', pattern: /setup\/prepare\/execute$/,
      respond: () => jsonResponse({ status: 'QUEUED', task_id: 'setup-1',
        steps: ['RECONCILE', 'DOWNLOAD', 'TARGET', 'RECIPE', 'IMAGE_BIND', 'IMAGE_VERIFY'] }),
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(screen.getByText(/已交给后台准备/)).toBeTruthy(), { timeout: 20000 });
    expect(screen.getByText(/可以离开这个页面/)).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('CASE-07：一项能力被自己的条件挡住，不影响其它合法能力', () => {
  it('H3 与素材出图同时勾选时,同一个任务带上两项能力', async () => {
    stub = new StudioStub([...routes4070({ h3FilesPresent: false }), ...installQueueRoutes()]);
    stub.add({
      method: 'POST', pattern: /setup\/prepare\/execute$/,
      respond: () => jsonResponse({ status: 'QUEUED', task_id: 'setup-2',
        steps: ['RECONCILE', 'DOWNLOAD', 'INSTANCE', 'TARGET', 'RECIPE', 'IMAGE_BIND', 'PROFILES', 'VALIDATE', 'IMAGE_VERIFY'] }),
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();

    await user.click(screen.getByRole('checkbox', { name: /H3 视频生成/ }));
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    expect(screen.getByRole('checkbox', { name: /H3 视频生成/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /素材出图/ })).toBeChecked();
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(screen.getByText(/已交给后台准备/)).toBeTruthy(), { timeout: 20000 });
    const body = stub.callsOf('POST', /setup\/prepare\/execute/)[0].body as { capabilities: string[] };
    // 两项能力都交给同一个任务；哪项被自己的条件挡住由后端具名报告,
    // 合法的能力不会被浏览器连带拒绝。
    expect(body.capabilities.sort()).toEqual(['h3', 'image']);
    expect(stub.callsOf('POST', /setup\/prepare\/execute/)).toHaveLength(1);
  });

  it('已有文件的复用判断在后端,浏览器不预先下载也不重复提交', async () => {
    stub = new StudioStub([...routes4070(), ...installQueueRoutes()]);
    stub.add({
      method: 'GET', pattern: /setup\/summary/,
      respond: () => jsonResponse(setupSummary({
        installation: {
          selected: { root: 'F:////AI////ComfyUI', instance_id: 'inst-1', port: 8188,
            managed: true, adopted: false, source: 'managed', reason: '本工作台自己的受管环境', exists: true },
          candidates: [], choice_required: false,
        },
      })),
    });
    stub.add({
      method: 'POST', pattern: /setup\/prepare\/execute$/,
      respond: () => jsonResponse({ status: 'QUEUED', task_id: 'setup-3',
        steps: ['RECONCILE', 'DOWNLOAD', 'TARGET', 'RECIPE'] }),
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(screen.getByText(/已交给后台准备/)).toBeTruthy(), { timeout: 20000 });
    // 核验与「要不要下载」都由后台任务决定;浏览器没有提交任何安装计划,
    // 也没有重复提交第二个任务。
    expect(stub.callsOf('POST', /setup\/install-plan/)).toHaveLength(0);
    expect(stub.callsOf('POST', /setup\/prepare\/execute/)).toHaveLength(1);
  });
});

describe('按钮按实际选择命名', () => {
  it('只勾素材出图时，下载量与按钮只包含素材出图', async () => {
    stub = new StudioStub(routes4070());
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    expect(screen.getByRole('button', { name: /确认下载素材出图权重/ })).toBeEnabled();
    const breakdown = screen.getByRole('list', { name: '分项下载量' });
    expect(breakdown).toHaveTextContent('素材出图权重');
    expect(breakdown).not.toHaveTextContent('H3 视频权重');
  });

  it('多个能力同时选择时，显示分项下载量', async () => {
    // H3 权重不在本机时，用户勾选 H3 与素材出图后展示两项分项下载量。
    stub = new StudioStub(routes4070({ h3FilesPresent: false }));
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    await user.click(screen.getByRole('checkbox', { name: /H3 视频生成/ }));
    await user.click(screen.getByRole('checkbox', { name: /素材出图/ }));
    expect(screen.getByRole('button', { name: '确认整备所选能力' })).toBeEnabled();
    const breakdown = screen.getByRole('list', { name: '分项下载量' });
    expect(breakdown).toHaveTextContent('H3 视频权重');
    expect(breakdown).toHaveTextContent('素材出图权重');
  });
});

describe('CASE-06：结构化错误不再渲染成 [object Object]', () => {
  it('blockers 是对象数组时，错误里是能读懂的句子和原始代码', () => {
    const text = describeDetail({
      code: 'HOST_NOT_QUALIFIED',
      message: '这台电脑没有通过这套变体的硬件资格复核。',
      blockers: [{ code: 'VRAM_INSUFFICIENT', required: 15360, actual: 12282 }],
      unknowns: [{ code: 'GPU_NOT_IDENTIFIED', message: '本机显卡没有被识别出来。' }],
    });
    expect(text).not.toContain('[object Object]');
    expect(text).toContain('HOST_NOT_QUALIFIED');
    expect(text).toContain('VRAM_INSUFFICIENT');
    expect(text).toContain('15360');
    expect(text).toContain('12282');
    expect(text).toContain('GPU_NOT_IDENTIFIED');
  });

  it('多个对象 blocker 也不会退化成 [object Object]', () => {
    const text = describeDetail({
      code: 'HOST_NOT_QUALIFIED',
      message: '没有通过资格复核。',
      blockers: [
        { code: 'VRAM_INSUFFICIENT', required: 15360, actual: 12282 },
        { code: 'RAM_BELOW_FLOOR', required: 32768, actual: 31539 },
      ],
    });
    expect(text).not.toContain('[object Object]');
    expect(text).toContain('VRAM_INSUFFICIENT');
    expect(text).toContain('RAM_BELOW_FLOOR');
  });
});

describe('本机已有 H3 文件时的诚实表述', () => {
  it('文件已找到不等于生产就绪', async () => {
    stub = new StudioStub(routes4070());
    render(<SetupCenter projectId="proj-1" />);
    await detect();
    expect(screen.getByText(/本机已有这套权重，但当前硬件不具备这一方案的正式生产准入条件/)).toBeTruthy();
  });
});
