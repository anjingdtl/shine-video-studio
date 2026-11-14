import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

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
    expect(screen.getByRole('button', { name: /确认并下载安装/ })).toBeEnabled();
  });
});