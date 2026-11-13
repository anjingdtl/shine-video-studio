import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { StoryboardBookPanel } from '../components/StreamlinePanels';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

const BOOK = {
  plan_version: 1,
  plan_sha256: 'e'.repeat(64),
  preview: {
    mode: 'sections', target_seconds: 60, tolerance_seconds: 15,
    episode_count: 1, total_planned_seconds: 60,
    episodes: [{
      episode_id: 'e1', order: 1, title: '第 1 集', planned_seconds: 60,
      deviation_seconds: 0, boundary_note: null, section_count: 1, sections: [],
    }],
  },
};

it('对白与旁白设定随分镜书一起提交，两项都进入冻结风格', async () => {
  const confirms: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'noir', name_zh: '黑色电影', summary_zh: '低照度、硬光', version: '1.0' },
    ]) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/director\/storyboard-book$/, respond: () => jsonResponse(BOOK) },
    { method: 'POST', pattern: /\/director\/storyboard-book\/confirm$/, respond: (_url, init) => {
      confirms.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ plan_version: 1, style: { style_version_id: 'style-1' } });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);

  await screen.findByRole('option', { name: /黑色电影/ });
  await user.click(screen.getByRole('button', { name: '多对白' }));
  await user.click(screen.getByRole('button', { name: '多旁白' }));
  await user.click(screen.getByRole('button', { name: /生成分镜书/ }));
  await screen.findByText(/计划总时长/);
  await user.click(screen.getByRole('button', { name: /确认分镜书/ }));

  await waitFor(() => expect(confirms).toHaveLength(1));
  expect(confirms[0].style).toMatchObject({
    profile_id: 'noir',
    dialogue_ratio_percent: 70,
    narration_level: 'high',
  });
  stub.restore();
});
