/** 12GB 低端机的变体推荐必须真的出现在安装向导里，
 *  而且候选要标成候选：未完成真机验收不是生产可用。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SetupPanel } from '../components/SetupPanel';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

describe('安装向导里的 H3 变体推荐', () => {
  it('显示本机能用的变体，并标明候选尚未验收', async () => {
    stub.add({ method: 'GET', pattern: /\/setup\/variant-recommendations$/, respond: () => jsonResponse({
      tier: 'low_end', host: { gpu: 'RTX 4070S', vram_mb: 12288, ram_mb: 32768, free_gb: 200 },
      recommended: [],
      candidates: [{
        variant_id: 'h3-ref2va-nvfp4-lowend', title: 'H3 Ref2VA NVFP4 候选（12GB 级）',
        summary: '官方目录里体积最小的一组文件', acceptance_status: 'CANDIDATE', status: 'UNKNOWN',
        usable_in_production: false, requires_explicit_user_ack: true, total_bytes: 11663443448,
        workflow_profile_ids: ['ref2va-nvfp4-turbo-4step-2img'],
        evidence_gaps: [{ code: 'NO_REAL_ACCEPTANCE', text: '还没有真机最小镜头证据' }],
        third_party_claims: [{ claim: '8-12GB 仅 RTX 50/Blackwell', source: 'DmitryDB/MiniMax-H3-ComfyUI-Quants README' }],
      }],
      blocked: [],
      notes: ['候选变体不会进入默认安装路径'],
    }) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={vi.fn()} />);
    // The card belongs to the environment step, so the check runs first.
    await user.click(screen.getByRole('button', { name: '检测本机' }));
    expect(await screen.findByText('这台电脑能用哪套 H3 变体')).toBeInTheDocument();
    expect(await screen.findByText('H3 Ref2VA NVFP4 候选（12GB 级）')).toBeInTheDocument();
    expect(screen.getByText(/未完成真机验收，不能当作生产可用/)).toBeInTheDocument();
    expect(screen.getByText(/第三方声明（非本项目实测）/)).toBeInTheDocument();
    await waitFor(() => {
      expect(stub.callsOf('GET', /variant-recommendations$/)).toHaveLength(1);
    });
  });
});
