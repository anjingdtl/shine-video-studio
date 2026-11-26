/** ⑥ 显性生产进度条：第几镜 / 百分比 / 动态前进 / 100% 完成（2026-11-19 用户要求）。
 *
 *  三原则：进度只读 run 的 passed/total 与 job 台账正在渲染的任务（同一事实），
 *  前端不累计自己的进度；跟读节奏与集卡/运行卡相同，后台推进时一起前进。 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseRoutes, baseProject, jsonResponse } from './studioStub';

let stub: StudioStub;
let passed: number;
let done: boolean;

const SHOT_IDS = Array.from({ length: 9 }, (_, i) => `shot-${i + 1}`);

function journeyBody() {
  const stages = ['script', 'plan', 'storyboard', 'assets', 'package'].map((id) => ({
    id, label: id, done: true, summary: '', blockers: [], facts: {},
  }));
  stages.push(
    { id: 'production', label: '视频生产', done, summary: `${passed} / 9 镜已通过`,
      blockers: [], facts: { passed_shot_count: passed, scope_shot_count: 9 } },
    { id: 'post', label: '后期成片', done, summary: '', blockers: [], facts: {} },
  );
  return { project_id: 'proj-1', stages, next_stage: done ? null : 'production',
    package: null, episode_plan: null, run: null };
}

function overviewBody() {
  return {
    project_id: 'proj-1', title: '女巫1119',
    plan: { version: 1, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
    style: { version: 3, sha256: 'a'.repeat(64) },
    counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 1,
      delivered_episode_count: done ? 1 : 0, shot_count: 9, passed_shot_count: passed },
    episodes: [{
      episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 60,
      state: done ? 'DELIVERED' : 'SHOOTING', state_label: done ? '已交付' : '拍摄中',
      storyboard: { section_count: 2, landed_section_count: 2, pending_sections: [],
        ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 1, blocker: null },
      shots: { landed_count: 9, confirmed_count: 9, confirmed: true, problems: [] },
      materials: { missing_count: 0, missing: [] },
      package: null,
      production: { run_id: 'run-1', status: done ? 'COMPLETED' : 'RUNNING',
        shot_count: 9, passed_shot_count: passed, in_current_run: true },
      delivery: null,
      next_action: done ? null : { kind: 'WAIT', label: '拍摄进行中，可离开页面' },
    }],
    next_action: null, package: null,
    run: { run_id: 'run-1', status: done ? 'COMPLETED' : 'RUNNING', active: !done,
      scope_episode_ids: ['ep-01'] },
    storyboard_task: { task_id: 'task-1', status: 'SUCCEEDED', state_version: 1 },
  };
}

function jobsBody() {
  return SHOT_IDS.map((shotId, i) => ({
    id: `job-${i + 1}`, project_id: 'proj-1', shot_id: shotId, shot_name: `镜${i + 1}`,
    status: i < passed ? 'PASSED' : (!done && i === passed ? 'RUNNING' : 'PLANNED'),
    provider: 'comfyui', generation_mode: 'Ref2VA', acceptance_status: 'pending_review',
    stale: false, shot_revision: 1, attempts: [], semantic_retries: 0, max_semantic_retries: 2,
    last_qc_result: 'needs_review', request: {}, paused: false, state_version: 1,
    failure_code: null, artifact_id: null, quality_verification: 'real',
    advisory_reviews: [], repairs: [],
  }));
}

function runBody() {
  return {
    id: 'run-1', project_id: 'proj-1', package_id: 'package-1', package_revision: 1,
    manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64),
    status: done ? 'COMPLETED' : 'RUNNING', state_version: 2, control_reason: null, blocker_count: 0,
    scope: { episode_ids: ['ep-01'], all_episodes: true, shot_count: 9 },
    counters: {},
    progress: { required_episode_count: 1, delivered_episode_count: done ? 1 : 0,
      producing_episode_count: done ? 0 : 1, blocked_episode_count: 0, batch_count: 2,
      passed_batch_count: done ? 2 : 0, delivered_seconds: 0, passed_shot_count: passed },
    created_at: '2026-11-19T00:00:00Z', started_at: '2026-11-19T00:00:01Z',
    finished_at: null, last_progress_at: '2026-11-19T00:10:00Z', heartbeat_at: null,
    episodes: [{
      id: 're-1', episode_id: 'ep-01', position: 1, title: '第1集',
      status: done ? 'DELIVERED' : 'PRODUCING', shot_count: 9, passed_shot_count: passed,
      planned_seconds: 60, shot_ids: SHOT_IDS, actual_seconds: null,
      delivery_order_id: null, delivered_artifact_id: null, delivered_sha256: null,
      blocker_code: null, delivered_at: null, batches: [],
    }],
  };
}

beforeEach(() => {
  passed = 3;
  done = false;
  window.location.hash = '#d/run';
  vi.useFakeTimers();
  stub = new StudioStub(baseRoutes(baseProject()));
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
    respond: () => jsonResponse(journeyBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse(overviewBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
    respond: () => jsonResponse(jobsBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
    respond: () => jsonResponse(runBody()) });
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.location.hash = '';
});

describe('⑥ 显性生产进度条', () => {
  it('显示第几镜与百分比，随台账动态前进，拍完到 100%', async () => {
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });

    const bar = screen.getByRole('progressbar', { name: '视频生产进度' });
    expect(bar).toHaveAttribute('aria-valuenow', '33');
    expect(screen.getByText('正在生成第 4 / 9 镜')).toBeInTheDocument();
    expect(screen.getByText(/正在渲染「镜4」/)).toBeInTheDocument();

    // 后台又拍完一镜：不点刷新，跟读同一份台账，进度条前进。
    passed = 4;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(screen.getByRole('progressbar', { name: '视频生产进度' }))
      .toHaveAttribute('aria-valuenow', '44');
    expect(screen.getByText('正在生成第 5 / 9 镜')).toBeInTheDocument();

    // 全部通过：100% 且明确本阶段生产完成。
    passed = 9;
    done = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(screen.getByRole('progressbar', { name: '视频生产进度' }))
      .toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByText('本阶段生产完成')).toBeInTheDocument();
    expect(screen.getByText(/9 镜全部通过/)).toBeInTheDocument();
  });

  it('整片运行不在跑时，单镜重拍有自己的进度条（读同一份 attempt 台账）', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
      respond: () => jsonResponse({ ...overviewBody(), run: null,
        episodes: [{ ...overviewBody().episodes[0], production: null, delivery: null,
          state: 'READY_TO_PACKAGE', state_label: '可打包' }] }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
      respond: () => jsonResponse([{ ...jobsBody()[3], status: 'RUNNING',
        attempts: [{ id: 'att-1', attempt_no: 1, status: 'RUNNING', progress: 42 }] }]) });
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const bar = screen.getByRole('progressbar', { name: '单镜重拍进度' });
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(screen.getByText('正在重拍「镜4」')).toBeInTheDocument();
  });

  it('PAUSE_REQUESTED 期间显示收尾文案而非已停止/单镜重拍，并提供二次确认取消入口', async () => {
    let runStatus = 'PAUSE_REQUESTED';
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
      respond: () => jsonResponse({
        ...overviewBody(),
        run: { run_id: 'run-1', status: runStatus, active: runStatus !== 'CANCELLED',
          scope_episode_ids: ['ep-01'] },
      }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
      respond: () => jsonResponse({ ...runBody(), status: runStatus }) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/production-runs\/run-1\/cancel$/,
      respond: () => {
        runStatus = 'CANCELLED';
        return jsonResponse({ id: 'run-1', status: 'CANCELLED', state_version: 3 });
      } });

    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });

    expect(screen.getByRole('heading', { name: '正在等待当前镜头落盘后暂停' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '运行已停止' })).toBeNull();
    expect(screen.queryByRole('progressbar', { name: '单镜重拍进度' })).toBeNull();
    expect(screen.getByRole('button', { name: '继续' })).toBeInTheDocument();

    const cancel = screen.getByRole('button', { name: '取消' });
    fireEvent.click(cancel);
    expect(screen.getByRole('button', { name: '再点一次确认取消' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '再点一次确认取消' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(stub.callsOf('POST', /\/api\/v1\/production-runs\/run-1\/cancel$/)).toHaveLength(1);
    expect(screen.getByRole('heading', { name: '运行已取消' })).toBeInTheDocument();
  });
});
