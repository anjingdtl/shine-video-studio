import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import App from '../App';
import { EMPTY_JOURNEY, StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

afterEach(() => {
  stub?.restore();
});

describe('镜头拆解生成中 JourneyRail 横幅不闪烁', () => {
  it('同项目同范围刷新或后台轮询期间不切回 loading 占位，避免横幅闪烁', async () => {
    let journeyCall = 0;
    let resolveDelayedJourney: ((res: ReturnType<typeof jsonResponse>) => void) | null = null;
    const storyboardJourney = {
      ...EMPTY_JOURNEY,
      stages: EMPTY_JOURNEY.stages.map((stage) => (
        stage.id === 'script' || stage.id === 'plan'
          ? { ...stage, done: true, blockers: [] }
          : stage
      )),
      next_stage: 'storyboard',
    };

    stub = new StudioStub(baseRoutes());
    stub.add({
      method: 'GET',
      pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
      respond: () => {
        journeyCall += 1;
        if (journeyCall === 1) {
          return jsonResponse(storyboardJourney);
        }
        return new Promise<ReturnType<typeof jsonResponse>>((resolve) => {
          resolveDelayedJourney = resolve;
        });
      },
    });
    stub.add({
      method: 'GET',
      pattern: /\/api\/v1\/projects\/proj-1\/director\/storyboard\/state$/,
      respond: () => jsonResponse({
        task_id: 'task-sb-1',
        status: 'RUNNING',
        state_version: 2,
        episodes: [{
          episode_id: 'ep-01',
          order: 1,
          status: 'IN_PROGRESS',
          shot_ids: [],
          units: [{ index: 0, production_section_id: 'sec-1', state: 'RUNNING' }],
          blocker: null,
        }],
      }),
    });

    window.location.hash = '#d/produce';
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText('本页任务')).toBeInTheDocument();
    expect(screen.getByText('逐场分镜、对白与声音安排')).toBeInTheDocument();

    // 触发刷新（模拟分镜轮询调用 refreshAll → dataEpoch 递增）
    await user.click(screen.getByRole('button', { name: '刷新全部数据' }));
    await waitFor(() => expect(journeyCall).toBeGreaterThanOrEqual(2));

    // 在 /journey 尚未返回期间，横幅必须保持 ready 文案，绝不闪烁到「正在确认七个阶段的真实状态」
    expect(screen.queryByText('正在确认七个阶段的真实状态')).toBeNull();
    expect(screen.getByText('本页任务')).toBeInTheDocument();
    expect(screen.getByText('逐场分镜、对白与声音安排')).toBeInTheDocument();

    await act(async () => {
      resolveDelayedJourney?.(jsonResponse(storyboardJourney));
    });
    expect(screen.getByText('逐场分镜、对白与声音安排')).toBeInTheDocument();
  });
});
