import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import { describeDetail } from '../api';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

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

const IMAGE_COMPONENTS = ['qwen-image-2.1-unet', 'qwen-image-2.1-text-encoder', 'qwen-image-2.1-vae'];
const IMAGE_BYTES = 17_283_091_112;

/** The 4070 sample: the three H3 weight files are already on disk. */
function routes4070(options: { h3FilesPresent?: boolean } = {}): ReturnType<typeof baseRoutes> {
  const present = options.h3FilesPresent ?? true;
  const routes = baseRoutes();
  routes.push(
    { method: 'GET', pattern: /setup\/connectivity$/, respond: () => jsonResponse({ blocked: [] }) },
    { method: 'GET', pattern: /setup\/variant-availability/, respond: () => jsonResponse({ adopted_root: null }) },
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

async function detect(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '检测本机' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '重新检测' })).toBeEnabled(), { timeout: 20000 });
}

function planBodies(): Array<Record<string, unknown>> {
  return stub.callsOf('POST', /setup\/install-plan/).map((call) => call.body as Record<string, unknown>);
}

describe('CASE-01：4070 只下载素材出图权重', () => {
  it('不提交 H3 安装计划，只提交独立的图片计划并进入受控下载队列', async () => {
    stub = new StudioStub([...routes4070(), ...installQueueRoutes()]);
    stub.add({
      method: 'POST', pattern: /setup\/install-plan$/,
      respond: (_url, init) => {
        const body = JSON.parse(String(init?.body ?? '{}')) as { catalog?: string };
        if (body.catalog === 'qwen-image-2.1-windows-x64') {
          return jsonResponse({
            plan_id: 'plan-image', sha256: 'b'.repeat(64), status: 'AWAITING_APPROVAL',
            download_bytes: IMAGE_BYTES, disk_required: 40 * 1024 ** 3, packages: IMAGE_COMPONENTS,
            capability: 'image', variant_id: null,
          });
        }
        // The H3 plan would be refused by the production admission gate.
        return jsonResponse({
          detail: {
            code: 'HOST_NOT_QUALIFIED',
            message: '这台电脑没有通过这套变体的硬件资格复核。',
            blockers: [{ code: 'VRAM_INSUFFICIENT', required: 15360, actual: 12282 }],
          },
        }, 409);
      },
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);

    // 只勾素材出图：先取消默认勾选的 H3 视频生成。
    await user.click(screen.getByRole('checkbox', { name: /H3 视频生成/ }));
    expect(screen.getByRole('checkbox', { name: /H3 视频生成/ })).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /素材出图/ })).toBeChecked();

    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(stub.callsOf('POST', /comfyops\/install-plans\/[^/]+\/execute/)).toHaveLength(1), { timeout: 20000 });

    // 1. 没有提交 H3 INT8 安装计划；2. 提交了独立的图片计划。
    const plans = planBodies();
    expect(plans).toHaveLength(1);
    expect(plans[0].catalog).toBe('qwen-image-2.1-windows-x64');
    expect(plans[0].components).toEqual(IMAGE_COMPONENTS);
    expect(plans[0].variant_id).toBeUndefined();

    // 3. 进入了受控下载队列；4. 没有 H3 的 HOST_NOT_QUALIFIED 阻断。
    expect(stub.callsOf('POST', /comfyops\/install-plans\/[^/]+\/execute/)).toHaveLength(1);
    expect(screen.queryByText(/HOST_NOT_QUALIFIED/)).toBeNull();

    // 5. 没有自动触发 H3 生产激活。
    expect(stub.callsOf('POST', /setup\/profiles/)).toHaveLength(0);
    expect(stub.callsOf('POST', /setup\/runtime\/activate/)).toHaveLength(0);
    expect(stub.callsOf('POST', /setup\/next-instance-plan/)).toHaveLength(0);
  });

  it('图片资源完成后显示整备成功，不把整个任务误报为失败', async () => {
    stub = new StudioStub([...routes4070(), ...installQueueRoutes()]);
    stub.add({
      method: 'POST', pattern: /setup\/install-plan$/,
      respond: () => jsonResponse({
        plan_id: 'plan-image', sha256: 'b'.repeat(64), status: 'AWAITING_APPROVAL',
        download_bytes: IMAGE_BYTES, disk_required: 40 * 1024 ** 3, packages: IMAGE_COMPONENTS,
        capability: 'image', variant_id: null,
      }),
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    await user.click(screen.getByRole('checkbox', { name: /H3 视频生成/ }));
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(screen.getByText(/素材出图已就绪/)).toBeTruthy(), { timeout: 20000 });
    const outcomes = screen.getByRole('list', { name: '各项能力的准备结论' });
    expect(outcomes).toHaveTextContent('素材出图');
    expect(outcomes).toHaveTextContent('资源已就绪');
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('CASE-07：一项能力被自己的条件挡住，不影响其它合法能力', () => {
  it('H3 被硬件门禁挡住时，素材出图仍然完成', async () => {
    stub = new StudioStub([...routes4070(), ...installQueueRoutes()]);
    // 本机这套权重没有通过逐字节核验：按固定清单重新准备，于是走到
    // H3 的生产准入门禁。
    stub.add({
      method: 'POST', pattern: /setup\/existing-installations\/verify$/,
      respond: () => jsonResponse({
        status: 'DONE', verified: [], rejected: [{ component_id: 'minimax-h3-diffusion' }],
      }),
    });
    stub.add({
      method: 'POST', pattern: /setup\/install-plan$/,
      respond: (_url, init) => {
        const body = JSON.parse(String(init?.body ?? '{}')) as { catalog?: string };
        if (body.catalog === 'qwen-image-2.1-windows-x64') {
          return jsonResponse({
            plan_id: 'plan-image', sha256: 'b'.repeat(64), status: 'AWAITING_APPROVAL',
            download_bytes: IMAGE_BYTES, disk_required: 40 * 1024 ** 3, packages: IMAGE_COMPONENTS,
            capability: 'image', variant_id: null,
          });
        }
        return jsonResponse({
          detail: {
            code: 'HOST_NOT_QUALIFIED', capability: 'h3',
            message: '这台电脑没有通过这套变体的硬件资格复核。',
            blockers: [{ code: 'VRAM_INSUFFICIENT', required: 15360, actual: 12282 }],
          },
        }, 409);
      },
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);

    // H3 与素材出图同时勾选。
    expect(screen.getByRole('checkbox', { name: /H3 视频生成/ })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /素材出图/ })).toBeChecked();
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(screen.getByText(/素材出图已就绪/)).toBeTruthy(), { timeout: 20000 });
    const outcomes = screen.getByRole('list', { name: '各项能力的准备结论' });
    expect(outcomes).toHaveTextContent('H3 视频生成');
    expect(outcomes).toHaveTextContent('未完成');
    expect(outcomes).toHaveTextContent('VRAM_INSUFFICIENT');
    // 两份计划都提交了：合法的能力没有被连带拒绝。
    expect(planBodies()).toHaveLength(2);
  });

  it('本机已有的权重不会因为核验没做完就被重新下载', async () => {
    stub = new StudioStub([...routes4070(), ...installQueueRoutes()]);
    // 核验正在进行中：几十 GB 的权重不该被重新下载一遍。
    stub.add({
      method: 'POST', pattern: /setup\/existing-installations\/verify$/,
      respond: () => jsonResponse({ status: 'VERIFYING', verified: [], rejected: [] }),
    });
    stub.add({
      method: 'POST', pattern: /setup\/install-plan$/,
      respond: () => jsonResponse({
        plan_id: 'plan-image', sha256: 'b'.repeat(64), status: 'AWAITING_APPROVAL',
        download_bytes: IMAGE_BYTES, disk_required: 40 * 1024 ** 3, packages: IMAGE_COMPONENTS,
        capability: 'image', variant_id: null,
      }),
    });

    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    await user.click(screen.getByRole('button', { name: /确认(下载|准备|整备)/ }));
    await user.click(screen.getByRole('button', { name: '开始准备' }));

    await waitFor(() => expect(screen.getByText(/素材出图已就绪/)).toBeTruthy(), { timeout: 20000 });
    // 只提交了图片计划：H3 权重没有重新下载。
    const plans = planBodies();
    expect(plans).toHaveLength(1);
    expect(plans[0].catalog).toBe('qwen-image-2.1-windows-x64');
    const outcomes = screen.getByRole('list', { name: '各项能力的准备结论' });
    expect(outcomes).toHaveTextContent('没有重复下载');
  });
});

describe('按钮按实际选择命名', () => {
  it('只勾素材出图时，下载量与按钮只包含素材出图', async () => {
    stub = new StudioStub(routes4070());
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    await user.click(screen.getByRole('checkbox', { name: /H3 视频生成/ }));
    expect(screen.getByRole('button', { name: /确认下载素材出图权重/ })).toBeEnabled();
    const breakdown = screen.getByRole('list', { name: '分项下载量' });
    expect(breakdown).toHaveTextContent('素材出图权重');
    expect(breakdown).not.toHaveTextContent('H3 视频权重');
  });

  it('多个能力同时选择时，显示分项下载量', async () => {
    // H3 权重不在本机时，H3 与素材出图是两项待准备的能力。
    stub = new StudioStub(routes4070({ h3FilesPresent: false }));
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
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
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    expect(screen.getByText(/本机已有这套权重，但当前硬件不具备这一方案的正式生产准入条件/)).toBeTruthy();
  });
});
