/** 登记生成方案后共享列表即时更新（2026-11-19 高级设置收敛轮）：
 *  工作流映射细调收敛在基础配置「专业细调」，高级设置不再保留平行排产入口；
 *  真实生成选项只在后端 production_mode 为 READY 时可用。 */
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
  window.history.replaceState(null, '', '/');
});

const PROFILE = {
  id: 'profile-1', project_id: 'proj-1', name: 'local-h3-ref2va-pdd8-2img-1aud', mode: 'Ref2VA',
  graph_sha256: 'a'.repeat(64), parameter_map: {}, required_nodes: [], checkpoint_id: 'MiniMax-H3-Base',
  validated_against: null, created_at: '2026-11-01T00:00:00',
};

describe('登记生成方案后共享列表即时更新', () => {
  it('基础配置专业细调里登记后，管理列表立即出现新方案', async () => {
    const user = userEvent.setup();
    let registered = false;
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/system\/status$/,
      respond: () => jsonResponse({
        ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: false, production_mode: PRODUCTION_MODE_READY,
      }),
    });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/workflow-profiles$/,
      respond: () => {
        registered = true;
        return jsonResponse(PROFILE, 201);
      },
    });
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/workflow-profiles/,
      respond: () => jsonResponse(registered ? [PROFILE] : []),
    });

    render(<App />);
    // 顶部模式入口显示后端的 READY 状态，而不是前端默认的演示。
    expect((await screen.findAllByText('真实制作已就绪')).length).toBeGreaterThan(0);

    // 在基础配置的专业细调里登记生成方案（唯一工作流映射入口）。
    window.location.hash = '#d/setup';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await user.click(await screen.findByText(/专业细调：工作流映射 · 项目策略/));
    await user.click(await screen.findByText(/展开 工作流 Profile 注册/));
    await user.type(screen.getByPlaceholderText('Profile 名称'), PROFILE.name);
    await user.selectOptions(screen.getByDisplayValue('T2VA'), 'Ref2VA');
    await user.click(await screen.findByRole('button', { name: /注册工作流/ }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /\/api\/v1\/workflow-profiles$/).length).toBeGreaterThan(0);
    }, { timeout: 4000 });

    // 登记刷新了共享的工作流列表状态，管理列表立即出现新方案。
    await waitFor(() => {
      expect(screen.getByText(PROFILE.name)).toBeInTheDocument();
    }, { timeout: 4000 });
  });

  it('高级设置不再提供生成方式与排产的平行入口', async () => {
    window.location.hash = '#d/advanced';
    render(<App />);
    await screen.findByText(/显示偏好、制片规范与/);
    expect(screen.queryByText('多镜头连续生产与单镜排产(高级)')).toBeNull();
    expect(screen.queryByLabelText(/生成方式/)).toBeNull();
    expect(screen.queryByText('各类配置去哪儿找')).toBeNull();
    expect(screen.queryByText('存储、ComfyUI 与本机状态')).toBeNull();
  });
});
