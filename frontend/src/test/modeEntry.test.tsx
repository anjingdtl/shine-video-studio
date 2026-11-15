/** 导演工作台唯一入口后的状态链契约：
 *  1. 顶部制作状态只展示与跳转:环境缺项落到基础配置,不再打开第二套向导;
 *  2. 旧专业深链(#setup、#v/…)按用途映射到导演页,高级设置可达;
 *  3. READY 状态下单镜头提交 provider=comfyui;演示状态仍为 mock;
 *  4. 无 LLM 配置时不假装 AI 分镜可用,手工入口就在同一页。 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import {
  PRODUCTION_MODE_READY, SYSTEM_STATUS, StudioStub,
  baseProject, baseRoutes, jsonResponse, productionMode,
} from './studioStub';

let stub: StudioStub;

const SHOT = {
  id: 'shot-1', project_id: 'proj-1', name: '第一镜', revision: 1,
  spec: { story_beat: '雨夜交接' }, bindings: [],
};
const FROZEN = {
  id: 'style-1', project_id: 'proj-1', version: 1, sha256: 'a'.repeat(64),
  snapshot: {}, approved_by: 'local-user', approval_note: '', frozen_at: 't',
  impact: { affected_shot_ids: [], invalidated_job_ids: [] },
};
const WORKFLOW = {
  id: 'wf-1', project_id: 'proj-1', name: 'Ref2VA 主方案', mode: 'Ref2VA', graph_sha256: 'g',
  parameter_map: {}, required_nodes: [], checkpoint_id: 'h3', validated_against: null, created_at: 't',
};

/** 冻结风格 + 一个镜头 + 一个方案：单镜头提交的前置条件。 */
function realReadyProject() {
  stub.add({ method: 'GET', pattern: /projects\/proj-1$/, respond: () => jsonResponse(baseProject({ frozen_style_version_id: 'style-1' })) });
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/styles$/, respond: () => jsonResponse([FROZEN]) });
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/shots$/, respond: () => jsonResponse([SHOT]) });
  stub.add({ method: 'GET', pattern: /workflow-profiles\?project_id=proj-1$/, respond: () => jsonResponse([WORKFLOW]) });
  stub.add({ method: 'POST', pattern: /projects\/proj-1\/jobs$/, respond: () => jsonResponse({ id: 'job-1' }, 201) });
}

/** 顶栏模式入口组件（.mode-entry 是组件样式合同的一部分）。 */
async function topbarModeEntry(): Promise<HTMLElement> {
  const entry = await waitFor(() => {
    const node = document.querySelector('.mode-entry');
    expect(node).not.toBeNull();
    return node as HTMLElement;
  });
  return entry;
}

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
  window.location.hash = '';
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.location.hash = '';
});

describe('顶部模式入口（唯一权威状态，只做跳转）', () => {
  it('后端失联时显示重连入口，点击后按实际响应恢复在线灯', async () => {
    let online = false;
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => online
      ? jsonResponse([baseProject()]) : jsonResponse({ detail: 'offline' }, 503) });
    stub.add({ method: 'GET', pattern: /system\/status$/, respond: () => online
      ? jsonResponse({ ...SYSTEM_STATUS, api_online: true })
      : jsonResponse({ detail: 'slow probe' }, 504) });
    render(<App />);
    expect(await screen.findByText('工作台后端失联')).toBeInTheDocument();
    const reconnect = screen.getByRole('button', { name: '重连工作台' });
    online = true;
    await userEvent.click(reconnect);
    expect(await screen.findByText('工作台后端在线')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('button', { name: /重连/ })).not.toBeInTheDocument());
  });
  it('初始加载赶上后端短暂不可用时，连接恢复后自动补拉作品列表，无需手动刷新', async () => {
    let online = false;
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => online
      ? jsonResponse([baseProject()]) : jsonResponse({ detail: 'offline' }, 503) });
    stub.add({ method: 'GET', pattern: /system\/status$/, respond: () => online
      ? jsonResponse({ ...SYSTEM_STATUS, api_online: true })
      : jsonResponse({ detail: 'slow probe' }, 504) });
    render(<App />);
    expect(await screen.findByText('工作台后端失联')).toBeInTheDocument();
    online = true;
    expect((await screen.findAllByText('雾港夜账', undefined, { timeout: 9000 })).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.queryByRole('button', { name: '重连工作台' })).toBeNull(),
      { timeout: 9000 });
  }, 15000);

  it('演示徽章常驻;点击落到基础配置而不是第二套向导', async () => {
    const user = userEvent.setup();
    render(<App />);
    const entry = await topbarModeEntry();
    expect(entry.textContent).toContain('演示体验');
    expect(entry.textContent).toContain('启用真实制作');
    await user.click(entry);
    await waitFor(() => expect(window.location.hash).toBe('#d/setup'));
    expect(screen.queryByRole('dialog', { name: '启用真实制作' })).toBeNull();
    expect(document.querySelector('.app-shell.ui-expert')).toBeNull();
    expect(document.querySelector('.ui-toggle')).toBeNull();
  });

  it('BLOCKED 状态的检查并修复同样落到基础配置', async () => {
    stub.add({
      method: 'GET', pattern: /system\/status$/,
      respond: () => jsonResponse({
        ...SYSTEM_STATUS, render_mode: 'unavailable',
        production_mode: productionMode({
          mode: 'BLOCKED', label: '真实制作暂不可用', action: 'check_fix',
          detail: '本机制作环境的固定文件发生了变化，需要重新检查配置。', code: 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED',
        }),
      }),
    });
    const user = userEvent.setup();
    render(<App />);
    const entry = await topbarModeEntry();
    expect(entry.textContent).toContain('真实制作暂不可用');
    expect(entry.textContent).toContain('检查并修复');
    await user.click(entry);
    await waitFor(() => expect(window.location.hash).toBe('#d/setup'));
    expect(screen.queryByRole('dialog', { name: '检查真实制作环境' })).toBeNull();
  });
});

describe('旧深链映射到统一工作台', () => {
  it('#setup(安装器落地)打开导演基础配置', async () => {
    window.location.hash = '#setup';
    render(<App />);
    expect(await screen.findByText('先把机器调好，')).toBeInTheDocument();
    expect(document.querySelector('.app-shell.ui-director')).not.toBeNull();
  });

  it('旧 #v/setup/settings 落到高级设置,模型细调可达', async () => {
    window.location.hash = '#v/setup/settings';
    render(<App />);
    await waitFor(() => expect(document.querySelector('#advanced')).not.toBeNull());
    expect(screen.getByText('回到基础配置')).toBeInTheDocument();
  });

  it('旧 #v/production/assets 落到拍摄素材准备', async () => {
    window.location.hash = '#v/production/assets';
    realReadyProject();
    render(<App />);
    await waitFor(() => expect(window.location.hash).toBe('#d/materials'));
  });

  it('旧素材中心 #a/images 仍打开图片工坊,返回链接指向导演工作台', async () => {
    window.location.hash = '#a/images';
    render(<App />);
    expect(await screen.findByRole('heading', { name: '图片工坊' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /导演工作台/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /高级设置/ })).toBeInTheDocument();
  });
});

describe('任务提交与模式一致', () => {
  it('READY 状态下单镜头真实提交 provider=comfyui', async () => {
    stub.add({
      method: 'GET', pattern: /system\/status$/,
      respond: () => jsonResponse({ ...SYSTEM_STATUS, render_mode: 'comfyui', production_mode: PRODUCTION_MODE_READY }),
    });
    realReadyProject();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    window.location.hash = '#d/produce';
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByText('手工分镜草稿与单镜任务(高级)'));
    const dock = document.querySelector('#manual-storyboard') as HTMLElement;
    const queue = await within(dock).findByRole('button', { name: /加入队列/ });
    await waitFor(() => { expect(queue).toBeEnabled(); });
    await user.click(queue);
    // 真实生成是二次确认：第一下展开，第二下才提交。
    await user.click(await within(dock).findByRole('button', { name: /确认真实生成/ }));
    await waitFor(() => {
      const body = stub.callsOf('POST', /projects\/proj-1\/jobs$/)[0].body as { provider: string; workflow_profile_id: string };
      expect(body.provider).toBe('comfyui');
      expect(body.workflow_profile_id).toBe('wf-1');
    });
  });

  it('演示状态下任务只能是 mock，真实选项被锁', async () => {
    realReadyProject();
    window.location.hash = '#d/advanced';
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByText('多镜头连续生产与单镜排产(高级)'));
    const card = document.querySelector('.queue-provider-card') as HTMLElement;
    const provider = await within(card).findByLabelText(/生成方式/);
    expect((provider as HTMLSelectElement).disabled).toBe(true);
    // 单镜提交入口在镜头拆解页,演示状态下同样只能提交 mock。
    window.location.hash = '#d/produce';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    await user.click(await screen.findByText('手工分镜草稿与单镜任务(高级)'));
    const dock = document.querySelector('#manual-storyboard') as HTMLElement;
    await user.click(within(dock).getByRole('button', { name: /加入队列/ }));
    await user.click(await within(dock).findByRole('button', { name: /确认真实生成/ }));
    await waitFor(() => {
      const body = stub.callsOf('POST', /projects\/proj-1\/jobs$/)[0].body as { provider: string };
      expect(body.provider).toBe('mock');
    });
  });

  it('无 LLM 配置时明确提示 AI 分镜未就绪，手工入口就在同一页', async () => {
    realReadyProject();
    window.location.hash = '#d/produce';
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText(/AI 分镜能力未就绪/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /去配置模型服务/ })).toBeInTheDocument();
    await user.click(await screen.findByText('手工分镜草稿与单镜任务(高级)'));
    const dock = document.querySelector('#manual-storyboard') as HTMLElement;
    await waitFor(() => {
      expect(within(dock).getByText(/时长（4–15 秒）/)).toBeInTheDocument();
    });
  });
});