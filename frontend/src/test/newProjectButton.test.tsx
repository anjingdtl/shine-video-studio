/** 「新建作品」是进入一部新作品的动作：它必须一眼可见，
 *  且无障碍名称保持不变（真实浏览器旅程脚本按这个名字点击）。 */
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseRoutes } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

describe('我的作品页的新建入口', () => {
  it('新建作品是一个醒目的主操作按钮，而不是白底弱化按钮', async () => {
    render(<App />);
    const button = await screen.findByRole('button', { name: '新建作品' });
    expect(button.className).toContain('new-project-button');
    expect(button.className).not.toContain('subtle-button');
    // 图标不进入可访问名称：脚本按这个名字找按钮。
    expect(button.textContent).toBe('新建作品');
  });

  it('按钮在未填写名称时仍然可辨认（禁用态不是隐藏）', async () => {
    render(<App />);
    const button = await screen.findByRole('button', { name: '新建作品' });
    expect(button).toBeDisabled();
    expect(button.className).toContain('new-project-button');
  });
});
