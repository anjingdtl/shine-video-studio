import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import { StudioStub, baseRoutes, jsonResponse, ENV_REPORT } from './studioStub';

const INT8 = {
  variant_id: 'h3-ref2va-int8-convrot', title: 'H3 Ref2VA INT8 ConvRot（当前生产基线）',
  acceptance_status: 'SUPPORTED', status: 'SUPPORTED', usable_in_production: true,
  auto_download_allowed: true, catalog_id: 'h3-ref2va-windows-x64', total_bytes: 1000,
  required_component_ids: ['minimax-h3-diffusion'], install_component_ids: ['minimax-h3-diffusion'],
};
const W6A8 = {
  variant_id: 'ref2va-lowend-candidate', title: 'H3 Ref2VA 低显存候选（W6A8）',
  acceptance_status: 'CANDIDATE', status: 'UNKNOWN', usable_in_production: false,
  auto_download_allowed: false, catalog_id: 'h3-ref2va-lowend-candidate-windows-x64',
  total_bytes: 2000, required_component_ids: [], install_component_ids: ['minimax-h3-diffusion-w6a8'],
};
const BLOCKED_SLOT = {
  variant_id: 'third-party-unverified', title: '第三方未核实槽位（BLOCKED）',
  acceptance_status: 'BLOCKED', status: 'BLOCKED', usable_in_production: false,
  auto_download_allowed: false, required_component_ids: [],
};

let stub: StudioStub;
beforeEach(() => {
  window.sessionStorage.clear();
  stub = new StudioStub(baseRoutes());
  stub.add({ method: 'GET', pattern: /setup\/variant-availability/, respond: () => jsonResponse({ adopted_root: null }) });
  stub.add({ method: 'GET', pattern: /setup\/variant-recommendations$/, respond: () => jsonResponse({
    tier: 'standard', host: { gpu: 'RTX 5080', vram_mb: 16384, ram_mb: 32768 },
    recommended: [INT8], candidates: [W6A8], blocked: [BLOCKED_SLOT],
  }) });
});
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

async function detect(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '检测本机' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '重新检测' })).toBeEnabled());
}

describe('制作环境：检测快照与候选下载', () => {
  it('挂载不自动检测，只有用户点击才检测', async () => {
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    expect(screen.getByRole('button', { name: '检测本机' })).toBeEnabled();
    expect(stub.callsOf('GET', /setup\/environment/)).toHaveLength(0);
    await detect(user);
    expect(stub.callsOf('GET', /setup\/environment/)).toHaveLength(1);
  });

  it('检测完保存快照，切页回来读快照不重复检测', async () => {
    const user = userEvent.setup();
    const first = render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    first.unmount();
    expect(stub.callsOf('GET', /setup\/environment/)).toHaveLength(1);

    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    expect(screen.getByRole('button', { name: '重新检测' })).toBeEnabled();
    expect(screen.getByText(/上次检测/)).toBeTruthy();
    expect(stub.callsOf('GET', /setup\/environment/)).toHaveLength(1);
  });

  it('已有一套权重不锁死候选：候选可选、BLOCKED 仍禁用', async () => {
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    const candidate = screen.getByRole('radio', { name: /低显存候选/ });
    expect(candidate).toBeEnabled();
    await user.click(candidate);
    expect(candidate).toBeChecked();
    expect(screen.getByRole('radio', { name: /第三方未核实槽位/ })).toBeDisabled();
  });

  it('已有权重标红；勾选时二次确认是否重复下载', async () => {
    stub.add({ method: 'GET', pattern: /setup\/image-readiness$/, respond: () => jsonResponse({
      configured: true, layer: 'GRAPH_VERIFIED', families: {}, renderer: { state: 'RUNNING' },
      blockers: [], upstream: { required_bytes: 0 },
    }) });
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    const found = screen.getByText('本机已找到权重');
    expect(found.className).toContain('cap-found-warn');

    const checkbox = screen.getByRole('checkbox', { name: /素材出图/ });
    await user.click(checkbox); // 取消勾选
    await user.click(checkbox); // 再勾选 → 二次确认
    const group = screen.getByRole('group', { name: '重复下载确认' });
    expect(group).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '重复下载' }));
    expect(screen.queryByRole('group', { name: '重复下载确认' })).toBeNull();
    expect(checkbox).toBeChecked();
    expect(screen.getByRole('button', { name: /确认(下载|准备|整备|并完成配置)/ })).toBeEnabled();
  });

  it('可自动补装的基础工具不阻断准备入口，并显示具体软件与端口', async () => {
    stub.add({ method: 'GET', pattern: /setup\/environment$/, respond: () => jsonResponse({
      ...ENV_REPORT,
      findings: [
        { code: 'TOOL_MISSING_INSTALLABLE', repairability: 'AUTO_REPAIRABLE', scopes: ['BOOTSTRAP'],
          package_id: 'ffmpeg', tools: ['ffmpeg', 'ffprobe'], message: '缺少 FFmpeg + FFprobe' },
        { code: 'GPU_VRAM_BELOW_FLOOR', repairability: 'HARDWARE_INCOMPATIBLE',
          scopes: ['VARIANT_QUALIFICATION', 'PRODUCTION_ACTIVATION'], message: '显存低于标准档门槛' },
      ],
      repair_plan: {
        plan_id: 'toolrepair-' + 'c'.repeat(32), status: 'COMPLETE', download_bytes: 194_182_300,
        packages: [{ package_id: 'ffmpeg', label: 'FFmpeg + FFprobe' }],
      },
      port: { status: 'OK', port: 8188, selected: 8189, identity: 'FOREIGN_PROCESS' },
    }) });
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    expect(screen.getByRole('group', { name: '基础工具补装' })).toHaveTextContent('FFmpeg + FFprobe');
    expect(screen.getByText(/服务端口 8189/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /确认(下载|准备|整备|并完成配置)/ })).toBeEnabled();
    expect(screen.queryByText(/先处理硬件或工具阻断项/)).toBeNull();
  });

  it('真正占用资源准备的条件仍阻断自动准备', async () => {
    stub.add({ method: 'GET', pattern: /setup\/environment$/, respond: () => jsonResponse({
      ...ENV_REPORT,
      findings: [{ code: 'RAM_BELOW_FLOOR', repairability: 'HARDWARE_INCOMPATIBLE',
        scopes: ['RESOURCE_PREPARATION', 'VARIANT_QUALIFICATION'], message: '实装内存不足' }],
      repair_plan: { plan_id: 'toolrepair-' + 'd'.repeat(32), status: 'NOTHING_TO_REPAIR',
        download_bytes: 0, packages: [] },
    }) });
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);
    expect(screen.getByText(/先处理硬件或工具阻断项/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /确认(下载|准备|整备|并完成配置)/ })).toBeDisabled();
  });

  it('后端已核验并绑定的权重不被界面当成缺失而重新下载', async () => {
    stub.add({ method: 'GET', pattern: /setup\/image-readiness$/, respond: () => jsonResponse({
      configured: true, layer: 'GRAPH_VERIFIED', families: {}, renderer: { state: 'RUNNING' },
      blockers: [], upstream: { required_bytes: 0 },
    }) });
    stub.add({ method: 'GET', pattern: /setup\/variant-availability/, respond: () => jsonResponse({
      adopted_root: 'D:\\Trae\\ComfyUI',
      variants: [{
        variant_id: 'h3-ref2va-int8-convrot', label: '标准', title: '标准',
        acceptance_status: 'SUPPORTED', downloadable: true,
        installed: true, verified: true, bound: true,
        profiles_registered: false, runtime_validated: false,
        selectable_for_preparation: true, trial_ready: false, production_ready: false,
        available: false, reason: '生成方案还没登记',
        facts: {
          FILES_FOUND: true, FILES_VERIFIED: true, RESOURCE_REGISTERED: true,
          INSTANCE_BOUND: true, WORKFLOW_VALIDATED: false, TRIAL_READY: false,
          PRODUCTION_READY: false,
        },
        binding: {
          instance_id: 'local-8188', mode: 'ADOPTED_EXTERNAL_INSTALLATION',
          source: 'ADOPTED_REGISTRATION', root: 'D:\\Trae\\ComfyUI',
          evidence: ['ADOPTED_INSTALLATION_FAMILY_DIRECTORY'],
        },
        blocking_reasons: [{ code: 'PROFILES_NOT_REGISTERED', message: '生成方案还没登记到当前作品' }],
        next_step: {
          code: 'PROFILE_NOT_REGISTERED', label: '把这个变体的生成方案登记到当前作品',
          reason: '当前作品还没有登记这套生成方案。', action: 'REGISTER_PROFILES', blocked: false,
        },
      }],
    }) });
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect(user);

    expect(screen.getByText('本机已核验 · 已绑定')).toBeTruthy();
    expect(screen.getByTestId('setup-next-step')).toHaveTextContent('把这个变体的生成方案登记到当前作品');
    // H3 is verified by the backend, and the image capability is already
    // present, so nothing needs downloading: the same 44GB is never planned twice.
    expect(screen.getByRole('button', { name: /确认并完成配置（不下载）/ })).toBeEnabled();
  });
});