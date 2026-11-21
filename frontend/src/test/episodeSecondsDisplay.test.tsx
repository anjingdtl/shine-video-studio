/** 每集秒数显示契约（2026-11-21 用户报障）：② 分集书每集行只展示视频计划秒数，
 *  「与目标差」与每集覆盖边界警告不再冒充每集计划；覆盖超出只留在头部覆盖标识。
 *  即使服务端旧字段仍带 deviation/boundary_note，显示层也不得渲染它们。 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

const BOOK = {
  plan_version: 1,
  plan_sha256: 'e'.repeat(64),
  status: 'DRAFT',
  preview: {
    mode: 'sections',
    target_seconds: 60,
    tolerance_seconds: 15,
    max_shot_seconds: 8,
    episode_count: 2,
    total_planned_seconds: 52,
    covered_scope: {
      covered_section_count: 4, total_section_count: 12,
      from_order: 1, to_order: 4, from_title: '第1场', to_title: '第4场',
      covered_seconds: 1105, total_outline_seconds: 4910, coverage_percent: 23,
      note: '本次 2 集覆盖大纲第 1 场至第 4 场（共 4/12 场，原文预计约 1105 / 4910 秒，占 23%）。',
    },
    episodes: [
      { episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 26,
        deviation_seconds: -34, boundary_note: 'planned 592.8s exceeds 75s: one scene is longer than the target',
        section_count: 3, sections: [] },
      { episode_id: 'ep-02', order: 2, title: '第2集', planned_seconds: 26,
        deviation_seconds: -34, boundary_note: 'planned 512.0s exceeds 75s: one scene is longer than the target',
        section_count: 1, sections: [] },
    ],
  },
};

describe('每集秒数显示契约', () => {
  it('每集行展示视频计划秒数，不渲染与目标差/覆盖边界警告；覆盖只在头部', async () => {
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director\/storyboard-book$/,
      respond: () => jsonResponse(BOOK, 201) });
    window.location.hash = '#d/assets';
    render(<App />);

    const user = userEvent.setup();
    const generateButtons = await screen.findAllByRole('button', { name: /生成分镜书/ });
    await user.click(generateButtons[0]);
    expect(await screen.findByText(/3 场 · 计划 26 秒/)).toBeInTheDocument();
    expect(screen.getByText(/1 场 · 计划 26 秒/)).toBeInTheDocument();
    expect(screen.queryByText(/与目标差/)).toBeNull();
    expect(screen.queryByText(/one scene is longer than the target/)).toBeNull();
    // 头部覆盖标识保留总剧本超出信息。
    expect(screen.getByText(/原文预计约 1105 \/ 4910 秒/)).toBeInTheDocument();
  });
});