/** 中断拍摄的恢复入口（2026-11-21 用户报障）：
 *  顶栏「有任务待核对 · 去处理」必须直达卡住作品的视频生产页；⑥ 运行卡必须
 *  就地露出既有执行者（放弃未知结果 / 去⑦重拍），而不是把人留在一个假装在跑
 *  的页面里。这里用 stub 的服务端形状验证界面按真实事实行事，不是生产证据。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';

import App from '../App';
import { StudioStub, SYSTEM_STATUS, baseProject, baseRoutes, gotoDirectorPage, jsonResponse, productionMode } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

const RECONCILE_MODE = productionMode({
  mode: 'RECONCILE_REQUIRED', label: '有任务待核对', action: 'check_fix',
  code: 'LEASE_RECONCILE_REQUIRED', render_provider: 'comfyui', runtime_status: 'ACTIVE',
  detail: '有结果不确定的生成任务需要人工核对，系统不会自动重试或覆盖。',
  focus: { project_id: 'proj-1', run_id: 'run-1' },
});

function stubSystem() {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/system\/status$/,
    respond: () => jsonResponse({ ...SYSTEM_STATUS, render_mode: 'comfyui', production_mode: RECONCILE_MODE }) });
}

function stuckJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job-1', project_id: 'proj-1', shot_id: 'shot-1', shot_name: '镜头1', shot_revision: 1,
    status: 'NEEDS_REVIEW', state_version: 3, provider: 'comfyui', simulated: false,
    video_created: false, paused: false, stale: false,
    failure_code: 'COMFY_ACTIVATION_RECONCILIATION_REQUIRED',
    provider_job_id: 'prompt-1', semantic_retries: 0, max_semantic_retries: 2,
    style_version_id: 'style-1', generation_mode: 'Ref2VA', workflow_profile_id: null,
    compiled_prompt_sha256: null, artifact_id: null, last_qc_result: null, last_repair_plan: null,
    request: { reference_manifest: [] }, attempts: [], repairs: [],
    created_at: '2026-11-21T00:00:00Z', updated_at: '2026-11-21T00:00:00Z',
    ...overrides,
  };
}

function stubStuckRun(jobs: Array<Record<string, unknown>>) {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
    respond: () => jsonResponse(jobs) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
    respond: () => jsonResponse({
      id: 'run-1', project_id: 'proj-1', package_id: 'package-1', package_revision: 1,
      manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64), status: 'NEEDS_REVIEW',
      state_version: 7, control_reason: 'SERIES_SHOT_NEEDS_REVIEW', blocker_count: 1,
      scope: { episode_ids: ['ep-01'], all_episodes: true, shot_count: 2 },
      counters: {},
      progress: { required_episode_count: 1, delivered_episode_count: 0, producing_episode_count: 0,
        blocked_episode_count: 1, batch_count: 1, passed_batch_count: 0,
        delivered_seconds: 0, passed_shot_count: 1 },
      created_at: '2026-11-21T00:00:00Z', started_at: '2026-11-21T00:00:01Z',
      finished_at: null, last_progress_at: '2026-11-21T00:10:00Z', heartbeat_at: null,
      episodes: [{ id: 'ep-row-1', episode_id: 'ep-01', position: 1, title: '第1集',
        status: 'BLOCKED', shot_count: 2, shot_ids: ['shot-1', 'shot-2'],
        passed_shot_count: 1, batches: [] }],
    }) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse({
      project_id: 'proj-1', title: '雨夜钟表铺',
      plan: { version: 1, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
      style: { version: 1, sha256: 'a'.repeat(64) },
      counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 0,
        delivered_episode_count: 0, shot_count: 2, passed_shot_count: 1 },
      episodes: [{
        episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 12,
        state: 'SHOOTING', state_label: '拍摄中',
        storyboard: { section_count: 1, landed_section_count: 1, pending_sections: [],
          ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 1, blocker: null },
        shots: { landed_count: 2, confirmed_count: 2, confirmed: true, problems: [] },
        materials: { missing_count: 0, missing: [] },
        package: null,
        production: { run_id: 'run-1', status: 'NEEDS_REVIEW', shot_count: 2, passed_shot_count: 1 },
        delivery: null, next_action: { kind: 'WAIT', label: '等待核对' },
      }],
      next_action: null, package: null,
      run: { run_id: 'run-1', status: 'NEEDS_REVIEW', active: true, scope_episode_ids: ['ep-01'] },
      storyboard_task: null,
    }) });
}

describe('顶栏待核对入口', () => {
  it('「去处理」直达卡住作品的视频生产页，而不是基础配置', async () => {
    stubSystem();
    stubStuckRun([stuckJob()]);
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole('button', { name: /去处理/ }));
    await waitFor(() => expect(window.location.hash).toBe('#d/run'));
    expect(await screen.findByText('运行等待人工核对')).toBeInTheDocument();
    expect(screen.queryByText('先把机器调好，再让 AI 开工。')).toBeNull();
  });
});

describe('⑥ 运行卡的人工决定面', () => {
  it('未知结果镜头就地提供放弃（同一执行者）与去⑦重拍', async () => {
    stubSystem();
    stubStuckRun([stuckJob()]);
    let abandoned: string | null = null;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/operator\/jobs\/job-1\/abandon$/,
      respond: () => { abandoned = 'job-1'; return jsonResponse({ status: 'FAILED', resubmitted: false }); } });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);

    expect(await screen.findByText('拍摄停在等待你决定的镜头')).toBeInTheDocument();
    expect(screen.getByText(/「镜头1」结果未知/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /放弃未知结果（保留产物，不重投）/ }));
    await waitFor(() => expect(abandoned).toBe('job-1'));

    await user.click(screen.getByRole('button', { name: /去⑦重拍此镜/ }));
    await waitFor(() => expect(window.location.hash).toBe('#d/deliver'));
  });

  it('已放弃的镜头只指去重拍，不再重复放弃', async () => {
    stubSystem();
    stubStuckRun([stuckJob({ status: 'FAILED', failure_code: 'USER_ABANDONED_UNRECONCILED' })]);
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);

    expect(await screen.findByText(/「镜头1」已放弃未知结果/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /放弃未知结果/ })).toBeNull();
    expect(screen.getByRole('button', { name: /去⑦重拍此镜/ })).toBeInTheDocument();
  });

  it('范围外的待核对镜头不出现在本运行卡里', async () => {
    stubSystem();
    stubStuckRun([stuckJob({ shot_id: 'shot-other', shot_name: '别的镜头' })]);
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);

    expect(await screen.findByText('运行等待人工核对')).toBeInTheDocument();
    expect(screen.queryByText('拍摄停在等待你决定的镜头')).toBeNull();
  });
});