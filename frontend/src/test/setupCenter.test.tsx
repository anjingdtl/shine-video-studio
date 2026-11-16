import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import { StudioStub, baseRoutes, jsonResponse, ENV_REPORT, setupSummary } from './studioStub';

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

async function detect() {
  // 打开就检测：等「上次检测」出现，说明服务端事实已经回来。
  await screen.findByText(/上次检测/, undefined, { timeout: 20000 });
}

describe('制作环境：检测快照与候选下载', () => {
  it('打开就自动检测，不需要先找检测入口', async () => {
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();
    expect(stub.callsOf('GET', /setup\/environment/)).toHaveLength(1);
    expect(stub.callsOf('GET', /setup\/summary/).length).toBeGreaterThan(0);
  });

  it('切页回来重新读取，不把浏览器快照当资格判定', async () => {
    const first = render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();
    first.unmount();
    expect(stub.callsOf('GET', /setup\/environment/)).toHaveLength(1);

    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await waitFor(() => expect(stub.callsOf('GET', /setup\/environment/)).toHaveLength(2));
    expect(screen.getByText(/上次检测/)).toBeTruthy();
  });

  it('已有一套权重不锁死候选：候选可选、BLOCKED 仍禁用', async () => {
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();
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
    await detect();
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
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();
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
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();
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
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();

    expect(screen.getByText('本机已核验 · 已绑定')).toBeTruthy();
    expect(screen.getByTestId('setup-next-step')).toHaveTextContent('把这个变体的生成方案登记到当前作品');
    // H3 is verified by the backend, and the image capability is already
    // present, so nothing needs downloading: the same 44GB is never planned twice.
    expect(screen.getByRole('button', { name: /确认并完成配置（不下载）/ })).toBeEnabled();
  });
});

describe('页面直连执行旁路已收掉', () => {
  it('不再有空点的「真实试镜一次」；运行核验交给一键准备', async () => {
    stub.add({ method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse(setupSummary({
      capabilities: {
        ...setupSummary().capabilities,
        h3: {
          ...setupSummary().capabilities.h3,
          facts: { ...setupSummary().capabilities.h3.facts, FILES_FOUND: true, FILES_VERIFIED: true },
          two_step: { files: { state: 'READY', label: '文件已就绪并核验' },
                      run: { state: 'UNVERIFIED', label: '运行合同已核对，待真实试镜' } },
        },
      },
    })) });
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();
    expect(screen.queryByText(/真实试镜一次/)).toBeNull();
    expect(screen.getByRole('button', { name: /一键绑定渲染器并核对运行合同/ })).toBeTruthy();
  });

  it('出图验证交给后台一键准备，不在页面直连启动渲染器', async () => {
    stub.add({ method: 'GET', pattern: /setup\/image-readiness$/, respond: () => jsonResponse({
      configured: true, layer: 'GRAPH_VERIFIED', families: {}, renderer: { state: 'NOT_STARTED' },
      blockers: [], upstream: { required_bytes: 0 },
    }) });
    stub.add({ method: 'POST', pattern: /setup\/prepare\/execute$/, respond: () => jsonResponse({
      status: 'QUEUED', task_id: 'setup-img', steps: ['TOOL_REPAIR', 'RECONCILE', 'DOWNLOAD', 'IMAGE_VERIFY'] }) });
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();
    await user.click(screen.getByRole('button', { name: /交给后台验证出图/ }));
    await waitFor(() => expect(stub.callsOf('POST', /setup\/prepare\/execute/)).toHaveLength(1), { timeout: 20000 });
    const body = stub.callsOf('POST', /setup\/prepare\/execute/)[0].body as { capabilities: string[] };
    expect(body.capabilities).toEqual(['image']);
    expect(stub.callsOf('POST', /setup\/image-runtime\/start/)).toHaveLength(0);
    expect(stub.callsOf('POST', /setup\/image-runtime\/validate/)).toHaveLength(0);
  });
});