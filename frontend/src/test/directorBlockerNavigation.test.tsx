/** D2 回归：主阻塞「分集方案尚未确认」的恢复动作写着「去剧本统筹」，实际却把用户送到导演包
 *  （#d/package）——比来源阶段更靠后，绕圈而不推进。
 *
 * 根因：ERROR_ACTIONS 把这个码映射到语义目标 'package-stage'，装配层再把
 * 'package-stage' 解析成导演包页。剧本统筹页（#d/assets）当时没有对应的语义目标。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { ERROR_ACTIONS } from '../errorActions';
import { EMPTY_JOURNEY, StudioStub, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

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

describe('分集未确认的恢复入口', () => {
  it('「去剧本统筹」必须映射到剧本统筹阶段，而不是导演包', () => {
    const action = ERROR_ACTIONS.EPISODE_PLAN_NOT_CONFIRMED.action;
    expect(action?.label).toBe('去剧本统筹');
    expect(action?.target).toBe('plan-stage');
    expect(action?.target).not.toBe('package-stage');
  });

  it('在别的阶段页点「去剧本统筹」，落到 #d/assets 而不是 #d/package', async () => {
    // 镜头拆解页上出现分集未确认的阻塞时，恢复动作必须落到剧本统筹。
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/, respond: () => jsonResponse({
      ...EMPTY_JOURNEY,
      stages: EMPTY_JOURNEY.stages.map((row) => row.id === 'storyboard'
        ? { ...row, blockers: [{ code: 'EPISODE_PLAN_NOT_CONFIRMED', message: '分集方案尚未确认' }] }
        : row),
    }) });

    const user = userEvent.setup();
    render(<App />);
    await switchToDirector(user);
    await gotoDirectorPage(user, /镜头拆解/);

    const actions = await screen.findAllByRole('button', { name: '去剧本统筹' });
    expect(actions.length).toBeGreaterThan(0);
    await user.click(actions[0]);

    await waitFor(() => expect(window.location.hash).toBe('#d/assets'));
    expect(window.location.hash).not.toBe('#d/package');
  });

  it('站在剧本统筹页时，不再给出指向本页的自环动作', async () => {
    const user = userEvent.setup();
    render(<App />);
    await switchToDirector(user);
    await gotoDirectorPage(user, /剧本统筹/);

    await waitFor(() => expect(screen.getAllByText(/分集方案尚未确认/).length).toBeGreaterThan(0));
    expect(screen.queryByRole('button', { name: '去剧本统筹' })).toBeNull();
  });
});
