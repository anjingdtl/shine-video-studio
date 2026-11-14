/** D7/D8 回归：导演包指引指向页面上不存在的按钮；视频生产阶段的右侧任务卡是空卡。
 *
 * 根因：
 * - 指引写死「点底部『确认拍摄方案并开始真实制作』」，但有右栏时按钮在右栏，
 *   未选集时按钮名又是「先选择要拍的集」——指引指向的按钮既不在底部、也可能不存在。
 * - #d/run 没有注册任何操作栏动作，右栏只剩一个标题。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import {
  PRODUCTION_MODE_READY, SYSTEM_STATUS, StudioStub, baseProject, baseRoutes, gotoDirectorPage, jsonResponse,
} from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

function episode(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 60,
    state: 'READY_TO_PACKAGE', state_label: '可打包',
    storyboard: { section_count: 2, landed_section_count: 2, pending_sections: [],
      ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 3, blocker: null },
    shots: { landed_count: 4, confirmed_count: 4, confirmed: true, problems: [] },
    materials: { missing_count: 0, missing: [] },
    package: null, production: null, delivery: null,
    next_action: { kind: 'BUILD_PACKAGE', label: '把这一集生成导演包' },
    ...overrides,
  };
}

function overviewBody(episodes: Array<Record<string, unknown>>, run: unknown = null) {
  return {
    project_id: 'proj-1', title: '雨夜钟表铺',
    plan: { version: 2, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: episodes.length },
    style: { version: 1, sha256: 'a'.repeat(64) },
    counts: {
      episode_count: episodes.length,
      confirmed_episode_count: episodes.filter((row) => (row.shots as { confirmed: boolean }).confirmed).length,
      shootable_episode_count: episodes.filter((row) => ['READY_TO_SHOOT', 'READY_TO_PACKAGE']
        .includes(String(row.state))).length,
      delivered_episode_count: episodes.filter((row) => row.state === 'DELIVERED').length,
      shot_count: episodes.reduce((sum, row) => sum + Number((row.shots as { confirmed_count: number }).confirmed_count), 0),
      passed_shot_count: 0,
    },
    episodes, next_action: null, package: null, run,
    storyboard_task: { task_id: 'task-1', status: 'SUCCEEDED', state_version: 3 },
  };
}

function stubOverview(body: unknown) {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse(body) });
}

function stubStrategyOptions() {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/variant-availability/,
    respond: () => jsonResponse({ project_id: 'proj-1', variants: [
      { variant_id: 'h3-ref2va-int8-convrot', available: true, reason: '本机已就绪' },
    ] }) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/strategy-options$/,
    respond: () => jsonResponse({
      model_variants: [
        { variant_id: 'h3-ref2va-int8-convrot', label: 'h3-ref2va-int8-convrot',
          acceptance_status: 'SUPPORTED', verified: true, default_recommended: true },
      ],
      accelerators: [{ accelerator_id: 'pdd8', steps: 8, verified: true }],
      second_pass: { supported: true, recipes: ['h3-tail-refine-v1'] },
      upscale: [],
    }) });
}

function stepTip(): Element | undefined {
  return Array.from(document.querySelectorAll('.guide-tip'))
    .find((node) => node.textContent?.includes('三步：'));
}

describe('导演包指引与视频生产右卡', () => {
  it('指引指向真实存在的开拍按钮名', async () => {
    stubOverview(overviewBody([episode({})]));
    stubStrategyOptions();
    stub.add({ method: 'GET', pattern: /\/api\/v1\/system\/status$/, respond: () => jsonResponse({
      ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: true, production_mode: PRODUCTION_MODE_READY }) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);
    await screen.findByText('这次要拍哪几集');

    const tip = stepTip();
    expect(tip).toBeTruthy();
    const start = await screen.findByRole('button', { name: /先选择要拍的集|确认拍摄方案并开始真实制作/ });
    expect(tip?.textContent).toContain(start.textContent?.trim() ?? '');
  });

  it('还没勾选集时，指引说清按钮为什么不可点', async () => {
    stubOverview(overviewBody([]));
    stubStrategyOptions();
    // 真实制作已就绪：此时按钮不可点的唯一原因是「还没勾选集」。
    stub.add({ method: 'GET', pattern: /\/api\/v1\/system\/status$/, respond: () => jsonResponse({
      ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: true, production_mode: PRODUCTION_MODE_READY }) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);
    await screen.findByText('这次要拍哪几集');

    const tip = stepTip();
    expect(tip?.textContent).toContain('先选择要拍的集');
    expect(tip?.textContent).toMatch(/当前不可点：/);
    expect(tip?.textContent).toContain('至少选择一集');
  });

  it('视频生产阶段还没有运行时，右侧任务卡说清该做什么', async () => {
    stubOverview(overviewBody([episode({})]));
    stubStrategyOptions();

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);
    await screen.findByText('还没有在拍的运行');

    const rail = document.querySelector('.director-page:not([hidden]) .action-rail');
    expect(rail, '右侧任务卡不能是空的').not.toBeNull();
    expect(rail?.textContent).toContain('去导演包选择要拍的集');
    expect(screen.getByRole('button', { name: '去导演包选择要拍的集' })).toBeInTheDocument();
  });
});
