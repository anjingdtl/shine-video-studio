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
beforeEach(() => { stub = new StudioStub(baseRoutes()); });
afterEach(() => { stub.restore(); });

describe('制作页单导航', () => {
  it('不再有旧四步聚焦条，只保留③④两个工作区', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await gotoDirectorPage(user, /第 3 阶段：镜头拆解/);
    expect(container.querySelector('.produce-focus-bar')).toBeNull();
    expect(screen.getByText(/③ 镜头拆解 ·/)).toBeInTheDocument();
    expect(screen.getByText(/④ 拍摄素材准备 ·/)).toBeInTheDocument();
    expect(screen.queryByText(/排产制作 ·/)).toBeNull();
    expect(screen.queryByText(/④ 进度与结果/)).toBeNull();
  });

  it('旅程条切换④不卸载③面板；排产与进度在⑥视频生产页', async () => {
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 3 阶段：镜头拆解/);
    expect(screen.getByLabelText('粘贴完整剧本文本')).toBeInTheDocument();
    await gotoDirectorPage(user, /第 4 阶段：拍摄素材准备/);
    // 切走后分镜面板仍在 DOM（挂载不卸载），运行中的链不中断。
    expect(screen.getByLabelText('粘贴完整剧本文本')).toBeInTheDocument();
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);
    expect(screen.getByText(/⑥ 排产与一键开拍/)).toBeInTheDocument();
    expect(screen.getByText(/⑥ 进度与结果 ·/)).toBeInTheDocument();
  });

  it('旧深链 stage=monitor 归并进视频生产页', async () => {
    window.history.replaceState(null, '', '#d/produce?stage=monitor');
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: /第 6 阶段：视频生产/ }));
    expect(screen.getByText(/⑥ 进度与结果 ·/)).toBeInTheDocument();
  });
});