/** 空数据库与两层界面的入口契约（2026-11-02 重构后）：
 *  1. 空库首次进入 → 导演模式「创建我的第一部AI短剧」，不强迫先装机；
 *  2. 制作环境检查不需要项目：从首启卡片一键进入专业模式的设置板块；
 *  3. 建立第一个项目后，导演模式四步流程完整出现；
 *  4. 已有项目时默认进入导演模式，专业模式保留完整四板块。
 *  历史契约（空库可达设置板块）继续被第 1、2 条覆盖。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  // jsdom keeps location.hash across tests; a setup deep link from the
  // previous case must not decide where the next case starts.
  window.history.replaceState(null, '', '#');
  stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '雾港夜账' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('空数据库与两层界面入口', () => {
  it('中央重新连接显示检查进度、失联原因和恢复后的作品读取进度', async () => {
    let online = false;
    let probeNext = false;
    let finishProbe: (value: ReturnType<typeof jsonResponse>) => void = () => undefined;
    let finishProjects: (value: ReturnType<typeof jsonResponse>) => void = () => undefined;
    const probe = new Promise<ReturnType<typeof jsonResponse>>(resolve => { finishProbe = resolve; });
    const projects = new Promise<ReturnType<typeof jsonResponse>>(resolve => { finishProjects = resolve; });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => {
      if (probeNext) {
        probeNext = false;
        return online ? jsonResponse([]) : probe;
      }
      return online ? projects : jsonResponse({}, 503);
    } });
    render(<App />);
    probeNext = true;
    await userEvent.click(screen.getByRole('button', { name: '重新连接' }));
    expect(screen.getByRole('progressbar', { name: '正在检查后端…' })).toBeInTheDocument();
    finishProbe(jsonResponse({}, 503));
    expect(await screen.findByText(/后端未响应。请双击/)).toBeInTheDocument();
    online = true;
    probeNext = true;
    await userEvent.click(screen.getByRole('button', { name: '重新连接' }));
    expect(await screen.findByRole('progressbar', { name: '后端已连接，正在读取作品…' })).toBeInTheDocument();
    finishProjects(jsonResponse([baseProject({ id: 'proj-1', title: '雾港夜账' })]));
    await waitFor(() => expect(screen.queryByRole('button', { name: '重新连接' })).not.toBeInTheDocument());
    expect(screen.getByText('工作台后端在线')).toBeInTheDocument();
  });
  function emptyDatabase() {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([]) });
  }

  it('无任何项目：首启卡片出现，基础配置在导演模式内即可进入', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    // 首次使用体验：明确的第一步，而不是一堆面板。
    expect(await screen.findByText('创建我的第一部AI短剧')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/给作品起个名字/)).toBeInTheDocument();
    // 新用户不需要切到专业模式：基础配置是导演模式自己的一页。
    await user.click(screen.getByRole('button', { name: '打开基础配置' }));
    expect(await screen.findByRole('heading', { name: /先把机器调好/ })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: '制作环境' })).toBeInTheDocument();
    expect(screen.getByText(/1 · 环境检查/)).toBeInTheDocument();
  });

  it('无项目时依赖项目的操作有明确说明', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: '打开基础配置' }));
    await screen.findByRole('heading', { name: '制作环境' });
    expect(screen.getByText(/计划生成、Profile 登记、配置生成与激活需要先创建一个项目/)).toBeInTheDocument();
  });

  it('建立第一个项目后导演流程完整出现', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText('创建我的第一部AI短剧')).toBeInTheDocument();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects$/,
      respond: (_url, init) => {
        const body = JSON.parse(String(init?.body));
        return jsonResponse(baseProject({ id: 'proj-new', title: body.title }), 201);
      },
    });
    await user.type(screen.getByPlaceholderText(/给作品起个名字/), '第一个项目');
    await user.click(screen.getByRole('button', { name: '创建作品' }));
    // 建项后进入导演流程，下一步引导由真实服务端状态驱动。
    await waitFor(() => {
      expect(screen.getAllByText('第一个项目').length).toBeGreaterThan(0);
    });
    const order = ['创意与剧本', '剧本统筹', '镜头拆解', '拍摄素材准备',
                   '导演包', '视频生产', '后期成片'];
    for (const stage of order) {
      expect(screen.getByRole('button', { name: new RegExp(stage) })).toBeInTheDocument();
    }
    // 素材为剧本和分镜服务：它排在「镜头拆解」之后，不是之前。
    const stageButtons = screen.getAllByRole('button')
      .map((button) => button.getAttribute('aria-label') ?? '')
      .filter((text) => /阶段：/.test(text));
    const positions = order.map((name) =>
      stageButtons.findIndex((text) => text.includes(name)));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect([...positions].sort((left, right) => left - right)).toEqual(positions);
  });

  it('基础配置在导演模式内提供安装、模型与可选音色三个入口', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findAllByText('雾港夜账');
    await user.click(screen.getByRole('button', { name: /基础配置/ }));
    expect(await screen.findByRole('heading', { name: /先把机器调好/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '② 模型与生成方案' }));
    expect(await screen.findByRole('heading', { name: '模型、ComfyUI 与本机状态' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '③ 固定音色（可选）' }));
    expect(await screen.findByText(/固定音色是加分项，不是开拍前提/)).toBeInTheDocument();
  });

  it('已有项目时默认导演模式；专业模式保留完整四板块', async () => {
    const user = userEvent.setup();
    render(<App />);
    // 默认进入导演模式（普通用户层）。
    await waitFor(() => {
      expect(screen.getAllByText('雾港夜账').length).toBeGreaterThan(0);
    });
    expect(screen.queryByTitle('打开设置板块')).not.toBeInTheDocument();
    // 切到专业模式：完整四板块布局仍在。
    await user.click(screen.getByRole('button', { name: /专业模式/ }));
    expect(await screen.findByTitle('打开设置板块')).toBeInTheDocument();
    await waitFor(() => {
      expect(document.querySelector('.date-chip')?.textContent).toContain('1 个');
    });
  });

  it('首次读取作品遇到短暂 API 启动延迟时自动恢复已有作品', async () => {
    let attempts = 0;
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/projects$/,
      respond: () => ++attempts === 1
        ? jsonResponse({ detail: 'API is starting' }, 503)
        : jsonResponse([baseProject({ id: 'proj-1', title: '雾港夜账' })]),
    });

    render(<App />);
    expect(await screen.findAllByText('雾港夜账')).not.toHaveLength(0);
    expect(attempts).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('本机工作区暂时不可用')).not.toBeInTheDocument();
  });

  it('全局刷新会重新读取设置页启动时失败的本机存储状态', async () => {
    let attempts = 0;
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/setup\/storage$/,
      respond: () => ++attempts === 1
        ? jsonResponse({ detail: 'API is starting' }, 500)
        : jsonResponse({ root: 'D:\\Shine', configured: true, system_drive: false,
          requires_selection: false, free_bytes: 1, free_gb: 100, paths: {}, drives: [] }),
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findAllByText('雾港夜账');
    await user.click(screen.getByRole('button', { name: /专业模式/ }));
    await user.click(await screen.findByTitle('打开设置板块'));
    expect(await screen.findByText(/本机存储信息暂时不可用/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '刷新全部数据' }));
    await waitFor(() => expect(screen.queryByText(/本机存储信息暂时不可用/)).not.toBeInTheDocument());
    expect(attempts).toBeGreaterThanOrEqual(2);
  });
});
