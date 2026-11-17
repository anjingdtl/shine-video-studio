/** ⑥ 页的集卡 / 右轨 / 运行卡必须随后台运行实时收敛到同一份事实。
 *
 * 回归用例：运行在后台拍完并交付后，页面若只刷新运行卡而 journey/overview
 * 停留旧值，就会同时显示「这次运行已经完成 6/6」与「拍摄中 0/6 · 还没有当前
 * 有效成片」——一套事实多处显示的反例。这里用 stub 服务端形状验证：推进期间
 * 跟读权威投影，停止后一次收敛，⑥ 不再残留旧事实；最终验收提示只指向⑦。
 */
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseRoutes, baseProject, jsonResponse } from './studioStub';

let stub: StudioStub;
let done: boolean;

function journeyBody() {
  const stages = [
    { id: 'script', label: '创意与剧本', done: true, summary: '大纲已确认', blockers: [], facts: {} },
    { id: 'plan', label: '剧本统筹', done: true, summary: '1 集', blockers: [], facts: {} },
    { id: 'storyboard', label: '镜头拆解', done: true, summary: '1 镜', blockers: [],
      facts: { shot_count: 1, planned_shot_count: 1 } },
    { id: 'assets', label: '拍摄素材准备', done: true, summary: '已核对', blockers: [], facts: {} },
    { id: 'package', label: '导演包', done: true, summary: '第 1 版', blockers: [], facts: {} },
    { id: 'production', label: '视频生产', done, summary: done ? '1 / 1 镜已通过' : '0 / 1 镜已通过',
      blockers: done ? [] : [{ code: 'SHOT_NOT_PRODUCED', message: '「镜1」还没有当前有效成片', shot_id: 'shot-1' }],
      facts: { passed_shot_count: done ? 1 : 0, scope_shot_count: 1 } },
    { id: 'post', label: '后期成片', done, summary: done ? '1 / 1 集已交付' : '等待交付', blockers: [], facts: {} },
  ];
  return { project_id: 'proj-1', stages, next_stage: done ? null : 'production',
    package: null, episode_plan: null, run: null };
}

function episodeRow() {
  return {
    episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 10,
    state: done ? 'DELIVERED' : 'SHOOTING', state_label: done ? '已交付' : '拍摄中',
    storyboard: { section_count: 1, landed_section_count: 1, pending_sections: [],
      ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 1, blocker: null },
    shots: { landed_count: 1, confirmed_count: 1, confirmed: true, problems: [] },
    materials: { missing_count: 0, missing: [] },
    package: null,
    production: { run_id: 'run-1', status: done ? 'COMPLETED' : 'RUNNING',
      shot_count: 1, passed_shot_count: done ? 1 : 0, in_current_run: true },
    delivery: done ? { artifact_id: 'art-1', sha256: '8'.repeat(64), delivered_at: '2026-11-17T00:20:00Z',
      actual_seconds: 10, status: 'DELIVERED', post_contract: null, run_id: 'run-1', acceptance: null } : null,
    next_action: done ? null : { kind: 'WAIT', label: '拍摄进行中，可离开页面' },
  };
}

function overviewBody() {
  return {
    project_id: 'proj-1', title: '雨夜归人',
    plan: { version: 1, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
    style: { version: 1, sha256: 'a'.repeat(64) },
    counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 1,
      delivered_episode_count: done ? 1 : 0, shot_count: 1, passed_shot_count: done ? 1 : 0 },
    episodes: [episodeRow()],
    next_action: null, package: null,
    run: { run_id: 'run-1', status: done ? 'COMPLETED' : 'RUNNING', active: !done,
      scope_episode_ids: ['ep-01'] },
    storyboard_task: { task_id: 'task-1', status: 'SUCCEEDED', state_version: 1 },
  };
}

function jobRow() {
  return {
    id: 'job-1', project_id: 'proj-1', shot_id: 'shot-1', shot_name: '镜1',
    status: done ? 'PASSED' : 'RUNNING', provider: 'comfyui', generation_mode: 'Ref2VA',
    acceptance_status: 'pending_review', stale: false, shot_revision: 1, attempts: [],
    semantic_retries: 0, max_semantic_retries: 2, last_qc_result: 'needs_review',
    request: {}, paused: false, state_version: 1, failure_code: null, artifact_id: null,
    quality_verification: 'real', advisory_reviews: [], repairs: [],
  };
}

function runBody() {
  return {
    id: 'run-1', project_id: 'proj-1', package_id: 'package-1', package_revision: 1,
    manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64),
    status: done ? 'COMPLETED' : 'RUNNING', state_version: 2, control_reason: null, blocker_count: 0,
    scope: { episode_ids: ['ep-01'], all_episodes: true, shot_count: 1 },
    counters: {},
    progress: { required_episode_count: 1, delivered_episode_count: done ? 1 : 0,
      producing_episode_count: done ? 0 : 1, blocked_episode_count: 0, batch_count: 1,
      passed_batch_count: done ? 1 : 0, delivered_seconds: done ? 10 : 0, passed_shot_count: done ? 1 : 0 },
    created_at: '2026-11-17T00:00:00Z', started_at: '2026-11-17T00:00:01Z',
    finished_at: null, last_progress_at: '2026-11-17T00:10:00Z', heartbeat_at: null,
    episodes: [],
  };
}

beforeEach(() => {
  done = false;
  window.location.hash = '#d/run';
  vi.useFakeTimers();
  stub = new StudioStub(baseRoutes(baseProject()));
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
    respond: () => jsonResponse(journeyBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse(overviewBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
    respond: () => jsonResponse([jobRow()]) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
    respond: () => jsonResponse(runBody()) });
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.location.hash = '';
});

describe('⑥ 投影实时收敛', () => {
  it('后台拍完交付后，集卡/右轨/运行卡一起收敛，不再残留拍摄中与旧阻塞', async () => {
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });

    // 推进中：三处都读同一份「还在拍」的事实。
    expect(screen.getAllByText('拍摄中').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/还没有当前有效成片/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0\/1 个计划镜头已通过/).length).toBeGreaterThan(0);

    // 后台完成：服务端事实翻转为已交付，浏览器不点任何刷新。
    done = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });

    expect(screen.queryByText('拍摄中')).toBeNull();
    expect(screen.queryByText(/还没有当前有效成片/)).toBeNull();
    expect(screen.getAllByText('已交付').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1\/1 个计划镜头已通过/).length).toBeGreaterThan(0);
    expect(screen.getByText('这次运行已经完成')).toBeInTheDocument();
    // 已交付的集在⑥只留指针，验收动作在⑦。
    expect(screen.getByText('到「后期成片」播放并确认')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '观看并确认' })).toBeNull();
    // ⑥ 任务明细的验收徽标是指向⑦的事实，不是「待人工确认」的催办。
    expect(screen.getByText('最终验收在⑦后期成片')).toBeInTheDocument();
  });

  it('没有运行时不启动跟读轮询（不空转请求）', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
      respond: () => jsonResponse({ ...overviewBody(), run: null,
        episodes: [{ ...episodeRow(), state: 'READY_TO_PACKAGE', state_label: '可打包',
          production: null, delivery: null,
          next_action: { kind: 'BUILD_PACKAGE', label: '把这一集生成导演包' } }] }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
      respond: () => jsonResponse([]) });
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const before = stub.callsOf('GET', /director\/overview/).length;
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(stub.callsOf('GET', /director\/overview/).length).toBe(before);
  });
});
