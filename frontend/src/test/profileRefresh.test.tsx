/** 登记生成方案后当前页面模板即时更新（2026-11-02 模式门禁版）：
 *  模板下拉的数据来源是共享的工作流列表；真实生成选项只在后端
 *  production_mode 为 READY 时可选——顶部模式、单镜头选择与连续生产
 *  使用同一后端状态，前端不自行推断。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { PRODUCTION_MODE_READY, StudioStub, SYSTEM_STATUS, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

const PROFILE = {
  id: 'profile-1', project_id: 'proj-1', name: 'local-h3-ref2va-pdd8-2img-1aud', mode: 'Ref2VA',
  graph_sha256: 'a'.repeat(64), parameter_map: {}, required_nodes: [], checkpoint_id: 'MiniMax-H3-Base',
  validated_against: null, created_at: '2026-11-01T00:00:00',
};

describe('登记生成方案后当前页面模板即时更新', () => {
  it('登记后模板下拉出现新方案；真实生成由后端 READY 状态解锁', async () => {
    const user = userEvent.setup();
    let registered = false;
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/system\/status$/,
      respond: () => jsonResponse({
        ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: false, production_mode: PRODUCTION_MODE_READY,
      }),
    });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/setup\/profiles$/,
      respond: () => {
        registered = true;
        return jsonResponse({ profiles: [{ name: PROFILE.name, status: 'REGISTERED', profile_id: PROFILE.id }] });
      },
    });
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/workflow-profiles/,
      respond: () => jsonResponse(registered ? [PROFILE] : []),
    });

    render(<App />);
    // 顶部模式入口显示后端的 READY 状态，而不是前端默认的演示。
    expect((await screen.findAllByText('真实制作已就绪')).length).toBeGreaterThan(0);

    // 在专业模式的设置板块登记生成方案。
    await user.click(screen.getByRole('button', { name: /专业模式/ }));
    await user.click(await screen.findByTitle('打开设置板块'));
    await user.click(await screen.findByRole('button', { name: '登记工作流 Profile' }));
    await waitFor(() => {
      expect(screen.getByText(/REGISTERED/)).toBeInTheDocument();
    }, { timeout: 4000 });

    // 登记刷新了共享的工作流列表状态。
    const before = stub.calls.filter((call) => call.method === 'GET' && /workflow-profiles/.test(call.url)).length;
    expect(before).toBeGreaterThan(0);

    // 回到生产板块：真实生成已解锁，模板下拉立即出现新方案。
    await user.click(await screen.findByTitle('打开生产板块'));
    const provider = await screen.findByLabelText(/生成方式/);
    await user.selectOptions(provider, 'comfyui');
    await waitFor(() => {
      expect(screen.getByLabelText(/生成模板/).textContent).toContain('local-h3-ref2va-pdd8-2img-1aud');
    });
  });

  it('演示模式下真实生成不可选：选择器被锁在演示体验', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole('button', { name: /专业模式/ }));
    await user.click(await screen.findByTitle('打开生产板块'));
    const provider = await screen.findByLabelText(/生成方式/);
    expect((provider as HTMLSelectElement).disabled).toBe(true);
    expect((provider as HTMLSelectElement).value).toBe('mock');
    expect(screen.getByText(/真实制作未启用/)).toBeInTheDocument();
  });
});
