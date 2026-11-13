/** 七阶段单导航契约（2026-11-12 归并）：
 *  - 旧四步聚焦条移除；制作页只承载③镜头拆解与④拍摄素材准备两个工作区。
 *  - 排产与进度归⑥视频生产页；旅程条是唯一阶段导航。
 *  - 切换阶段不卸载面板（运行中的分镜链保持挂载）。 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { StudioStub, baseRoutes, gotoDirectorPage } from './studioStub';

let stub: StudioStub;
beforeEach(() => {
  // 每个用例从项目管理页开始：阶段条只在制作页出现。
  window.history.replaceState(null, '', '#');
  window.localStorage.clear();
  stub = new StudioStub(baseRoutes());
});
afterEach(() => { stub.restore(); });

describe('制作页单导航', () => {
  it('③镜头拆解与④素材准备是两页，各自只有自己的工作区', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await gotoDirectorPage(user, /第 3 阶段：镜头拆解/);
    // 旧的四步聚焦条与同页双折叠都已移除。
    expect(container.querySelector('.produce-focus-bar')).toBeNull();
    expect(container.querySelector('#storyboard-dock')).toBeNull();
    expect(container.querySelector('#assets-dock')).toBeNull();
    expect(screen.getByText(/一次生成，按集落镜/)).toBeInTheDocument();
    expect(screen.queryByText(/镜头要什么，就准备什么/)).not.toBeVisible();
  });

  it('切到素材页后，镜头页虽保留挂载但不可见；素材页只承载素材需求', async () => {
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 3 阶段：镜头拆解/);
    const stagePanel = await screen.findByText(/一次生成，按集落镜/);
    expect(stagePanel).toBeVisible();
    // 已在制作页：直接点阶段条切到④。
    await user.click(await screen.findByRole('button', { name: /第 4 阶段：拍摄素材准备/ }));
    // 两页互斥显示；素材页出现的是需求清单，不是分镜工作区。
    expect(await screen.findByText(/镜头要什么，就准备什么/)).toBeVisible();
    expect(screen.getByText(/一次生成，按集落镜/)).not.toBeVisible();
    expect(window.location.hash).toBe('#d/materials');
  });

  it('旧深链 stage=monitor 归并进视频生产页', async () => {
    window.history.replaceState(null, '', '#d/produce?stage=monitor');
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: /第 6 阶段：视频生产/ }));
    expect(screen.getByText('第 6 阶段 · 真实拍摄')).toBeInTheDocument();
    expect(screen.getByText(/⑥ 任务明细（诊断）/)).toBeInTheDocument();
  });

  it('旧深链 stage=assets 归并进独立的素材页', async () => {
    window.history.replaceState(null, '', '#d/produce?stage=assets');
    render(<App />);
    expect(await screen.findByText(/镜头要什么，就准备什么/)).toBeVisible();
    expect(window.location.hash).toBe('#d/materials');
  });
});
