/** P3 produce 单阶段聚焦契约（设计方案 §3.2）：
 *  - 迷你聚焦条四段，状态圆点；一次只展开一个阶段。
 *  - 拍摄进行中离开进度段需要确认（守卫），不静默切走正在跑的运行。
 *  - 切换聚焦不卸载面板（运行中的分镜链保持挂载）。 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { StudioStub, baseRoutes, gotoDirectorPage } from './studioStub';

let stub: StudioStub;
beforeEach(() => { stub = new StudioStub(baseRoutes()); });
afterEach(() => { stub.restore(); });

describe('produce 迷你聚焦条', () => {
  it('渲染四段聚焦项，当前段高亮', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await gotoDirectorPage(user, /镜头拆解/);
    const bar = container.querySelector('.produce-focus-bar');
    expect(bar).not.toBeNull();
    expect(bar!.querySelectorAll('.produce-focus-item')).toHaveLength(4);
    expect(bar!.querySelectorAll('.produce-focus-item.active')).toHaveLength(1);
  });

  it('点击聚焦项切换唯一展开段，面板保持挂载不卸载', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await gotoDirectorPage(user, /镜头拆解/);
    // 分镜面板已挂载（粘贴剧本文本框存在）。
    expect(screen.getByLabelText('粘贴完整剧本文本')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /② 素材准备/ }));
    const bar = container.querySelector('.produce-focus-bar');
    const active = bar!.querySelectorAll('.produce-focus-item.active');
    expect(active).toHaveLength(1);
    expect(active[0]).toHaveTextContent('素材准备');
    // 切走后分镜面板仍在 DOM（挂载不卸载），运行中的链不中断。
    expect(screen.getByLabelText('粘贴完整剧本文本')).toBeInTheDocument();
  });
});