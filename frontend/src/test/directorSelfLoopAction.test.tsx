/** D4 回归：已经站在某一页时，右侧待办不再给出指向本页的动作。
 *
 * 症状：#d/creative 上点「去创意与剧本」，地址栏与页面完全不变——一个零进展的按钮。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseRoutes, gotoDirectorPage } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

async function switchToDirector(user: ReturnType<typeof userEvent.setup>) {
  if (document.querySelector('.app-shell.ui-director')) return;
  const toggle = await waitFor(() => {
    const node = document.querySelector('.ui-toggle');
    expect(node).not.toBeNull();
    return node as HTMLElement;
  });
  await user.click(toggle);
  await waitFor(() => expect(document.querySelector('.app-shell.ui-director')).not.toBeNull(), { timeout: 5000 });
}

describe('同页不提供指向本页的动作', () => {
  it('创意与剧本页的待办里没有「去创意与剧本」自环按钮，但保留原因说明', async () => {
    const user = userEvent.setup();
    render(<App />);
    await switchToDirector(user);
    await gotoDirectorPage(user, /创意与剧本/);

    // 阻塞原因仍然告诉用户缺什么。
    await waitFor(() => expect(screen.getAllByText(/还没有剧本/).length).toBeGreaterThan(0));
    // 但不再给一个点了不会前进的按钮。
    expect(screen.queryByRole('button', { name: '去创意与剧本' })).toBeNull();
  });
});
