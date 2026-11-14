/** D3 回归：必做动作失败后，屏上只有裸错误码或一句原因，没有可点的「去处理」。
 *
 * 根因：ERROR_ACTIONS 没有收录 STORYBOARD_STYLE_NOT_FROZEN / SCRIPT_OUTLINE_NOT_CONFIRMED，
 * describeBlocker 落到 fallback；三个面板又只把错误写进一行 form-error，
 * 于是裸码成了屏上唯一信息，用户拿不到下一步。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EpisodeStoryboardPanel, ShotMaterialsPanel, StoryboardBookPanel } from '../components/StreamlinePanels';
import { StageRailProvider } from '../components/StageRail';
import { ERROR_ACTIONS } from '../errorActions';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub([]);
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

function requiredBlock(): HTMLElement | null {
  return document.querySelector('.required-action');
}

describe('必做动作失败必须给出可点的下一步', () => {
  it('错误码表收录三个必做动作的阻塞码', () => {
    expect(ERROR_ACTIONS.STORYBOARD_STYLE_NOT_FROZEN.action?.target).toBe('plan-stage');
    expect(ERROR_ACTIONS.SCRIPT_OUTLINE_NOT_CONFIRMED.action?.target).toBe('creative-stage');
    expect(ERROR_ACTIONS.EPISODE_PLAN_NOT_MATERIALIZED.action?.target).toBe('produce-stage');
  });

  it('生成分镜被风格未冻结挡住时，给出「去冻结风格」', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
      respond: () => jsonResponse({ task_id: null, status: null, state_version: 1, episodes: [] }) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/storyboard\/generate$/,
      respond: () => jsonResponse({ detail: 'STORYBOARD_STYLE_NOT_FROZEN' }, 409) });
    const onGoStage = vi.fn();
    render(<EpisodeStoryboardPanel projectId="p1" onGoStage={onGoStage} />);

    await userEvent.setup().click(await screen.findByRole('button', { name: '生成分镜' }));

    await waitFor(() => expect(requiredBlock()).not.toBeNull());
    expect(requiredBlock()?.textContent).toContain('风格还没有冻结');
    expect(requiredBlock()?.textContent).toContain('为什么必须做');
    // 裸码不得是屏上唯一信息。
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('风格还没有冻结');

    await userEvent.setup().click(screen.getByRole('button', { name: '去冻结风格' }));
    expect(onGoStage).toHaveBeenCalledWith('plan-stage');
  });

  it('素材清单被大纲未确认挡住时，给出「去确认大纲」', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/materials\/requirements$/,
      respond: () => jsonResponse({ plan_revision_id: null, expected_snapshot: null, requirements: [] }) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/materials\/derive$/,
      respond: () => jsonResponse({ detail: 'SCRIPT_OUTLINE_NOT_CONFIRMED' }, 409) });
    const onGoStage = vi.fn();
    render(
      <StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
        <ShotMaterialsPanel projectId="p1" onGoStage={onGoStage} />
      </StageRailProvider>);

    await userEvent.setup().click(
      await screen.findByRole('button', { name: '按已确认镜头生成素材清单' }));

    await waitFor(() => expect(requiredBlock()).not.toBeNull());
    expect(requiredBlock()?.textContent).toContain('剧本大纲还没有确认');

    await userEvent.setup().click(screen.getByRole('button', { name: '去确认大纲' }));
    expect(onGoStage).toHaveBeenCalledWith('creative-stage');
  });

  it('生成分镜书被大纲未确认挡住时，给出「去确认大纲」', async () => {
    stub.add({ method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'wong-kar-wai', name_zh: '王家卫参考风格', summary_zh: '城市情绪', version: '1.0.0' },
    ]) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/, respond: () => jsonResponse([]) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/storyboard-book$/,
      respond: () => jsonResponse({ detail: 'SCRIPT_OUTLINE_NOT_CONFIRMED' }, 422) });
    const onGoStage = vi.fn();
    render(<StoryboardBookPanel projectId="p1" onGoStage={onGoStage} />);

    const button = await screen.findByRole('button', { name: '生成分镜书' });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.setup().click(button);

    await waitFor(() => expect(requiredBlock()).not.toBeNull());
    expect(requiredBlock()?.textContent).toContain('剧本大纲还没有确认');

    await userEvent.setup().click(screen.getByRole('button', { name: '去确认大纲' }));
    expect(onGoStage).toHaveBeenCalledWith('creative-stage');
  });
});
