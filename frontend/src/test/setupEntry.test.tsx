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
import { StudioStub, SYSTEM_STATUS, baseProject, baseRoutes, jsonResponse } from './studioStub';

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
    // 唯一性原则：连接探针归一到 /system/status（与制作模式同源），作品读取仍走
    // /projects。本用例分别控制这两条请求的落定时刻，验证重连两段进度与失败原因。
    let online = false;
    let hangStatus = false;
    let hangProjects = false;
    let finishStatus: (value: ReturnType<typeof jsonResponse>) => void = () => undefined;
    let finishProjects: (value: ReturnType<typeof jsonResponse>) => void = () => undefined;
    const statusProbe = new Promise<ReturnType<typeof jsonResponse>>(resolve => { finishStatus = resolve; });
    const projectsProbe = new Promise<ReturnType<typeof jsonResponse>>(resolve => { finishProjects = resolve; });
    stub.add({ method: 'GET', pattern: /system\/status$/, respond: () => {
      if (hangStatus) { hangStatus = false; return statusProbe; }
      return jsonResponse({ ...SYSTEM_STATUS, api_online: online });
    } });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => {
      if (hangProjects) { hangProjects = false; return projectsProbe; }
      return online ? jsonResponse([baseProject({ id: 'proj-1', title: '雾港夜账' })]) : jsonResponse({}, 503);
    } });
    render(<App />);
    await screen.findByRole('button', { name: '重新连接' });
    hangStatus = true;
    await userEvent.click(screen.getByRole('button', { name: '重新连接' }));
    expect(screen.getByRole('progressbar', { name: '正在检查后端…' })).toBeInTheDocument();
    finishStatus(jsonResponse({ ...SYSTEM_STATUS, api_online: false }));
    expect(await screen.findByText(/后端未响应/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重启后台' })).toBeInTheDocument();
    online = true;
    hangProjects = true;
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
    stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/storage$/, respond: () => jsonResponse({
      root: 'D:\\Shine', free_bytes: 100 * 1024 ** 3, free_gb: 100, requires_selection: false,
    }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/variant-recommendations$/, respond: () => jsonResponse({
      tier: 'standard', host: { gpu: 'NVIDIA GeForce RTX 5080', vram_mb: 16303, ram_mb: 32768 },
      recommended: [{ variant_id: 'h3-ref2va-int8-convrot', title: 'H3 INT8', acceptance_status: 'SUPPORTED',
        status: 'SUPPORTED', usable_in_production: true, auto_download_allowed: true,
        catalog_id: 'h3-ref2va-windows-x64', total_bytes: 1024 }],
      candidates: [], blocked: [],
    }) });
    const user = userEvent.setup();
    render(<App />);
    // 首次使用体验：明确的第一步，而不是一堆面板。
    expect(await screen.findByText('创建我的第一部AI短剧')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/给作品起个名字/)).toBeInTheDocument();
    // 新用户不需要切到专业模式：基础配置是导演模式自己的一页。
    await user.click(screen.getByRole('button', { name: '打开基础配置' }));
    expect(await screen.findByRole('heading', { name: /先把机器调好/ })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: '制作环境' })).toBeInTheDocument();
    // 空库也能整备机器：能力默认不预勾，用户勾选后作品仍是可选的、不挡确认按钮。
    expect(screen.getByText('没有作品也可以先整备机器')).toBeInTheDocument();
    expect(await screen.findByTestId('setup-environment')).toBeInTheDocument();
    await user.click(await screen.findByRole('checkbox', { name: /H3 视频生成/ }));
    const confirms = screen.getAllByRole('button', { name: /确认(下载|准备|整备|并完成配置)/ });
    expect(confirms.length).toBeGreaterThan(0);
    for (const button of confirms) expect(button).toBeEnabled();
  });

  it('无项目时整备仍可开始,作品绑定稍后自动完成', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: '打开基础配置' }));
    await screen.findByRole('heading', { name: '制作环境' });
    expect(screen.getByText('没有作品也可以先整备机器')).toBeInTheDocument();
    expect(screen.getByText(/建好作品后，已准备的能力会自动绑定到它/)).toBeInTheDocument();
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
    await user.click(screen.getByRole('button', { name: '② 文本模型' }));
    expect(await screen.findByRole('heading', { name: /AI 模型/ })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '③ 独立能力（可选）' }));
    expect(await screen.findByText(/这三项都不影响出片/)).toBeInTheDocument();
  });

  it('已有项目时默认导演工作台;专业切换已下线,高级设置可达', async () => {
    const user = userEvent.setup();
    render(<App />);
    // 默认进入导演模式（普通用户层）。
    await waitFor(() => {
      expect(screen.getAllByText('雾港夜账').length).toBeGreaterThan(0);
    });
    expect(document.querySelector('.ui-toggle')).toBeNull();
    expect(document.querySelector('.app-shell.ui-expert')).toBeNull();
    // 细调与诊断收进左栏「高级设置」,不再是第二套界面。
    await user.click(screen.getByRole('button', { name: /高级设置/ }));
    await waitFor(() => expect(document.querySelector('#advanced')).not.toBeNull());
    expect(screen.getByText('回到基础配置')).toBeInTheDocument();
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

  it('全局刷新会重新读取基础配置启动时失败的本机存储状态', async () => {
    let attempts = 0;
    // 基础配置挂载即检测；存储读取首次失败时整轮检测报错。全局刷新是用户
    // 主动动作，会重新检测（并重扫磁盘），这次存储成功，错误随之消失。
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/setup\/storage$/,
      respond: () => attempts++ < 1
        ? jsonResponse({ detail: 'API is starting' }, 500)
        : jsonResponse({ root: 'D:\\Shine', configured: true, system_drive: false,
          requires_selection: false, free_bytes: 1, free_gb: 100, paths: {}, drives: [] }),
    });
    const user = userEvent.setup();
    render(<App />);
    await screen.findAllByText('雾港夜账');
    await user.click(screen.getByRole('button', { name: /基础配置/ }));
    expect(await screen.findByText(/检测没有完成/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '刷新全部数据' }));
    await waitFor(() => expect(screen.queryByText(/检测没有完成/)).not.toBeInTheDocument());
    expect(attempts).toBeGreaterThanOrEqual(2);
  });
});
