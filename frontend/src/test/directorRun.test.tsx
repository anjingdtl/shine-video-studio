/** ⑤⑥ 唯一开拍入口与按集进度（V2 §8）。
 *
 * 导演模式只保留一个“确认拍摄方案并开始真实制作”主操作，它复用导演包与
 * Run 引擎；没准备好的集是具名拒绝，不会拖住已就绪的集。这里用 stub 的
 * 服务端形状验证界面按真实事实行事，不是生产证据。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';

import App from '../App';
import { PRODUCTION_MODE_READY, StudioStub, SYSTEM_STATUS, baseProject, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

function episode(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 60,
    state: 'READY_TO_PACKAGE', state_label: '可打包',
    storyboard: { section_count: 2, landed_section_count: 2, pending_sections: [],
      ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 3, blocker: null },
    shots: { landed_count: 4, confirmed_count: 4, confirmed: true, problems: [] },
    materials: { missing_count: 0, missing: [] },
    package: null,
    production: null,
    delivery: null,
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
    episodes,
    next_action: null,
    package: null,
    run,
    storyboard_task: { task_id: 'task-1', status: 'SUCCEEDED', state_version: 3 },
  };
}

function stubOverview(body: unknown) {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse(body) });
}

function stubRef2vaProfile() {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/workflow-profiles\?project_id=proj-1$/,
    respond: () => jsonResponse([{
      id: 'profile-1', project_id: 'proj-1', name: 'local-h3-ref2va-pdd8-2img-1aud',
      mode: 'Ref2VA', created_at: '2026-11-01T00:00:00Z',
    }]) });
}

function stubStrategyOptions() {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/strategy-options$/,
    respond: () => jsonResponse({
      model_variants: [
        { variant_id: 'h3-ref2va-int8-convrot', label: 'h3-ref2va-int8-convrot',
          acceptance_status: 'SUPPORTED', verified: true, default_recommended: true },
        { variant_id: 'ref2va-lowend-candidate', label: 'ref2va-lowend-candidate',
          acceptance_status: 'CANDIDATE', verified: false },
      ],
      accelerators: [
        { accelerator_id: 'pdd8', steps: 8, verified: true },
        { accelerator_id: 'native20', steps: 20, verified: true },
        { accelerator_id: 'turbo4', steps: 4, verified: false, unverified_reason: 'ACCELERATOR_UNVERIFIED' },
      ],
      second_pass: { supported: true, recipes: ['h3-tail-refine-v1'] },
      upscale: [{ engine: 'relayvsr', variant: 'x2', enabled: false,
        status: 'BLOCKED', blockers: [{ code: 'RELAYVSR_WEIGHTS_NOT_PINNED' }] }],
    }) });
}

describe('⑤ 拍摄范围', () => {
  it('只允许选择就绪的集，并用一次主操作开拍', async () => {
    stubOverview(overviewBody([
      episode({}),
      episode({ episode_id: 'ep-02', order: 2, title: '第2集', state: 'STORYBOARD_BLOCKED',
        state_label: '分镜待修复', shots: { landed_count: 0, confirmed_count: 0, confirmed: false, problems: [] },
        next_action: { kind: 'RETRY_STORYBOARD', label: '修复这一集的分镜' } }),
    ]));
    stubRef2vaProfile();
    stubStrategyOptions();
    // 真实制作已就绪：主按钮可用；演示模式下的阻断另有专门测试。
    stub.add({ method: 'GET', pattern: /\/api\/v1\/system\/status$/, respond: () => jsonResponse({
      ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: true, production_mode: PRODUCTION_MODE_READY }) });
    let posted: Record<string, unknown> | null = null;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director\/production\/start$/,
      respond: (_url, init) => {
        posted = JSON.parse(String(init?.body));
        return jsonResponse({
          package_id: 'package-1', package_revision: 1, package_built: true,
          run_id: 'run-1', run_status: 'QUEUED', created: true,
          episode_ids: ['ep-01'],
          episodes: [{ episode_id: 'ep-01', position: 1, shot_count: 4 }],
        }, 201);
      } });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);

    expect(await screen.findByText('这次要拍哪几集')).toBeInTheDocument();
    // 就绪集默认勾选；未就绪集的勾选框不可点。
    const [first, second] = await screen.findAllByRole('checkbox');
    expect(first).toBeChecked();
    expect(second).toBeDisabled();
    expect(screen.getByText(/1 集这次还不能拍/)).toBeInTheDocument();

    const [start] = await screen.findAllByRole('button', { name: /确认拍摄方案并开始真实制作/ });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted?.['episode_ids']).toEqual(['ep-01']);
    // 开拍提交的是生产策略（模型+加速），不再是静态方案 id；页面上不出现方案下拉。
    const strategy = posted?.['strategy'] as unknown as Record<string, unknown>;
    expect(strategy?.['model_variant_id']).toBe('h3-ref2va-int8-convrot');
    expect(strategy?.['accelerator_id']).toBe('pdd8');
    expect(posted?.['workflow_profile_id']).toBeUndefined();
  });

  it('没有任何就绪集时不开拍，而是列出每一集的真实状态', async () => {
    stubOverview(overviewBody([
      episode({ state: 'STORYBOARD_BLOCKED', state_label: '分镜待修复',
        shots: { landed_count: 0, confirmed_count: 0, confirmed: false, problems: [] } }),
    ]));
    stubRef2vaProfile();
    stubStrategyOptions();
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);

    expect(await screen.findByText('这次要拍哪几集')).toBeInTheDocument();
    expect(screen.getByText('0 集已选')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /确认拍摄方案并开始真实制作（/ })).toBeNull();
  });
});

describe('⑥ 真实拍摄进度', () => {
  const RUN = {
    id: 'run-1', project_id: 'proj-1', package_id: 'package-1', package_revision: 3,
    manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64), status: 'RUNNING',
    state_version: 7, control_reason: null, blocker_count: 1,
    scope: { episode_ids: ['ep-01', 'ep-02'], all_episodes: true, shot_count: 4 },
    counters: {},
    progress: {
      required_episode_count: 2, delivered_episode_count: 1, producing_episode_count: 1,
      blocked_episode_count: 1, batch_count: 3, passed_batch_count: 2,
      delivered_seconds: 61.5, passed_shot_count: 3,
    },
    created_at: '2026-11-01T00:05:00Z', started_at: '2026-11-01T00:05:01Z',
    finished_at: null, last_progress_at: new Date().toISOString(), heartbeat_at: null,
    episodes: [],
  };

  function stubRun(status = 'RUNNING') {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/production-runs$/,
      respond: () => jsonResponse([{ id: 'run-1', status }]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
      respond: () => jsonResponse({ ...RUN, status }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1\/events/,
      respond: () => jsonResponse([]) });
  }

  it('按集展示真实进度，并把可审片的集单独标出来', async () => {
    stubRun();
    stubOverview(overviewBody([
      episode({ state: 'REVIEW_READY', state_label: '可审片',
        production: { run_id: 'run-1', status: 'RUNNING', shot_count: 2, passed_shot_count: 2 },
        delivery: { artifact_id: 'artifact-1', sha256: '8'.repeat(64), delivered_at: '2026-11-01T00:20:00Z',
          actual_seconds: 61.5, status: 'DELIVERED', post_contract: null },
        next_action: { kind: 'REVIEW_EPISODE', label: '播放并确认这一集' } }),
      episode({ episode_id: 'ep-02', order: 2, title: '第2集', state: 'SHOOTING', state_label: '拍摄中',
        production: { run_id: 'run-1', status: 'RUNNING', shot_count: 2, passed_shot_count: 1 },
        next_action: { kind: 'WAIT', label: '拍摄进行中，可离开页面' } }),
    ], { run_id: 'run-1', status: 'RUNNING', active: true, scope_episode_ids: ['ep-01', 'ep-02'] }));

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);

    expect(await screen.findByText(/本次范围：2 集 · 4 镜/)).toBeInTheDocument();
    expect(screen.getByText(/已通过：3 镜/)).toBeInTheDocument();
    expect(screen.getByText(/已交付：1\/2 集/)).toBeInTheDocument();
    expect(screen.getAllByText('可审片').length).toBeGreaterThan(0);
    expect(screen.getAllByText('拍摄中').length).toBeGreaterThan(0);
  });

  it('还没有运行时说明还没开拍，而不是假装在跑', async () => {
    stubOverview(overviewBody([episode({})]));
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);

    expect(await screen.findByText('还没有在拍的运行')).toBeInTheDocument();
    expect(screen.queryByText(/已交付：/)).toBeNull();
  });

  it('待人工处理时提供核对与安全续跑，不会新建 Run', async () => {
    stubRun('NEEDS_REVIEW');
    let resumed = 0;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/production-runs\/run-1\/resume$/,
      respond: () => { resumed += 1; return jsonResponse({ id: 'run-1', status: 'RUNNING' }); } });
    stubOverview(overviewBody([episode({
      state: 'SHOOTING', state_label: '拍摄中',
      production: { run_id: 'run-1', status: 'NEEDS_REVIEW', shot_count: 2, passed_shot_count: 1 },
      next_action: { kind: 'WAIT', label: '等待核对' },
    })], { run_id: 'run-1', status: 'NEEDS_REVIEW', active: true, scope_episode_ids: ['ep-01'] }));

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);

    const resume = await screen.findByRole('button', { name: /继续安全可恢复批次/ });
    await user.click(resume);
    await waitFor(() => expect(resumed).toBe(1));
    expect(stub.calls.filter((call) => call.method === 'POST'
      && /production-runs$/.test(call.url))).toHaveLength(0);
  });
});

describe('⑤ 续拍下一集', () => {
  it('已交付的集不默认勾选，拍第二集不会顺手重拍第一集', async () => {
    stubOverview(overviewBody([
      episode({ state: 'DELIVERED', state_label: '已交付',
        production: { run_id: 'run-1', status: 'COMPLETED', shot_count: 4, passed_shot_count: 4,
          in_current_run: false },
        delivery: { artifact_id: 'artifact-1', sha256: '8'.repeat(64), run_id: 'run-1',
          delivered_at: '2026-11-01T00:20:00Z', actual_seconds: 61.5, status: 'DELIVERED',
          post_contract: null },
        next_action: null }),
      episode({ episode_id: 'ep-02', order: 2, title: '第2集', state: 'READY_TO_SHOOT',
        state_label: '可开拍', production: null, next_action: null }),
    ]));
    stubRef2vaProfile();
    stubStrategyOptions();
    stub.add({ method: 'GET', pattern: /\/api\/v1\/system\/status$/, respond: () => jsonResponse({
      ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: true, production_mode: PRODUCTION_MODE_READY }) });
    let posted: Record<string, unknown> | null = null;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director\/production\/start$/,
      respond: (_url, init) => {
        posted = JSON.parse(String(init?.body));
        return jsonResponse({
          package_id: 'package-2', package_revision: 2, package_built: true,
          run_id: 'run-2', run_status: 'QUEUED', created: true,
          episode_ids: ['ep-02'],
          episodes: [{ episode_id: 'ep-02', position: 2, shot_count: 2 }],
        }, 201);
      } });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);

    expect(await screen.findByText('这次要拍哪几集')).toBeInTheDocument();
    const [first, second] = await screen.findAllByRole('checkbox');
    expect(first).not.toBeChecked();
    expect(second).toBeChecked();
    // 想重拍必须自己勾，而且要看见"勾选即重拍"。
    expect(screen.getByText(/勾选即重拍这一集/)).toBeInTheDocument();

    const [start] = await screen.findAllByRole('button', { name: /确认拍摄方案并开始真实制作/ });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted?.['episode_ids']).toEqual(['ep-02']);
  });
});

describe('⑦ 逐集成片', () => {
  it('开始拍第二集后，第一集已交付的成片仍然在册并可播放', async () => {
    stubOverview(overviewBody([
      episode({ state: 'DELIVERED', state_label: '已交付',
        production: { run_id: 'run-1', status: 'COMPLETED', shot_count: 3, passed_shot_count: 3,
          in_current_run: false },
        delivery: { artifact_id: 'artifact-1', sha256: '8'.repeat(64), run_id: 'run-1',
          delivered_at: '2026-11-01T00:20:00Z', actual_seconds: 20, status: 'DELIVERED',
          post_contract: null },
        next_action: null }),
      episode({ episode_id: 'ep-02', order: 2, title: '第2集', state: 'SHOOTING', state_label: '拍摄中',
        production: { run_id: 'run-2', status: 'RUNNING', shot_count: 1, passed_shot_count: 0,
          in_current_run: true },
        next_action: { kind: 'WAIT', label: '拍摄进行中，可离开页面' } }),
    ]));
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/production-runs$/,
      respond: () => jsonResponse([{ id: 'run-2', status: 'RUNNING' }]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-2$/,
      respond: () => jsonResponse({
        id: 'run-2', project_id: 'proj-1', package_id: 'package-2', package_revision: 2,
        manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64), status: 'RUNNING',
        state_version: 1, control_reason: null, blocker_count: 0,
        scope: { episode_ids: ['ep-02'], all_episodes: false, shot_count: 1 },
        counters: {},
        progress: { required_episode_count: 1, delivered_episode_count: 0, producing_episode_count: 1,
          blocked_episode_count: 0, batch_count: 1, passed_batch_count: 0, delivered_seconds: 0,
          passed_shot_count: 0 },
        created_at: '2026-11-01T00:30:00Z', started_at: '2026-11-01T00:30:01Z', finished_at: null,
        last_progress_at: new Date().toISOString(), heartbeat_at: null,
        episodes: [],
      }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-2\/events/,
      respond: () => jsonResponse([]) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 7 阶段/);

    // 两集同时出现：第一集是成片，第二集是"拍摄中"。
    expect(await screen.findByText('每一集都能单独播放')).toBeInTheDocument();
    expect(await screen.findByText('第1集')).toBeInTheDocument();
    expect(screen.getByText('第2集')).toBeInTheDocument();
    expect(screen.getAllByText('已交付').length).toBeGreaterThan(0);
    expect(screen.getAllByText('拍摄中').length).toBeGreaterThan(0);
    const player = document.querySelector(
      'video[src*="/api/v1/deliveries/artifact-1/media"]');
    expect(player).not.toBeNull();
  });
});
