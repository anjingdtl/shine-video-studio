/** ⑦ 是字幕的唯一生成入口：生成读对白事实、批准与装配选择同页完成。
 *
 * 复现过的缺陷：生成执行者只认③人工冻结字幕，作品有对白却报
 * EPISODE_SUBTITLE_EMPTY；且「修订剪辑 / 字幕」装配编辑器没有挂载，
 * 生成后无处选择版本烧录。这里验证整条链在⑦一次走通。
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseRoutes, baseProject, jsonResponse } from './studioStub';

let stub: StudioStub;
let tracks: Array<Record<string, unknown>>;

function journeyBody() {
  const stages = ['script', 'plan', 'storyboard', 'assets', 'package', 'production', 'post']
    .map((id) => ({ id, label: id, done: true, summary: '', blockers: [], facts: {} }));
  return { project_id: 'proj-1', stages, next_stage: null, package: null, episode_plan: null,
    run: { id: 'run-1' }, scope: { run_id: 'run-1', package_id: 'package-1' } };
}

function overviewBody() {
  return {
    project_id: 'proj-1', title: '雨夜归人',
    plan: { version: 1, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
    style: { version: 1, sha256: 'a'.repeat(64) },
    counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 1,
      delivered_episode_count: 1, shot_count: 1, passed_shot_count: 1 },
    episodes: [{
      episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 10,
      state: 'DELIVERED', state_label: '已交付',
      storyboard: { section_count: 1, landed_section_count: 1, pending_sections: [],
        ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 1, blocker: null },
      shots: { landed_count: 1, confirmed_count: 1, confirmed: true, problems: [] },
      materials: { missing_count: 0, missing: [] },
      package: null,
      production: { run_id: 'run-1', status: 'COMPLETED', shot_count: 1, passed_shot_count: 1,
        in_current_run: true },
      delivery: { artifact_id: 'art-1', sha256: '8'.repeat(64), delivered_at: '2026-11-19T00:20:00Z',
        actual_seconds: 10, status: 'DELIVERED', post_contract: null, run_id: 'run-1', acceptance: null },
      next_action: null,
    }],
    next_action: null, package: null,
    run: { run_id: 'run-1', status: 'COMPLETED', active: false, scope_episode_ids: ['ep-01'] },
    storyboard_task: { task_id: 'task-1', status: 'SUCCEEDED', state_version: 1 },
  };
}

function runBody() {
  return {
    id: 'run-1', project_id: 'proj-1', package_id: 'package-1', package_revision: 1,
    manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64),
    status: 'COMPLETED', state_version: 2, control_reason: null, blocker_count: 0,
    scope: { episode_ids: ['ep-01'], all_episodes: true, shot_count: 1 },
    counters: {},
    progress: { required_episode_count: 1, delivered_episode_count: 1, producing_episode_count: 0,
      blocked_episode_count: 0, batch_count: 1, passed_batch_count: 1, delivered_seconds: 10,
      passed_shot_count: 1 },
    created_at: '2026-11-19T00:00:00Z', started_at: '2026-11-19T00:00:01Z',
    finished_at: '2026-11-19T00:10:00Z', last_progress_at: '2026-11-19T00:10:00Z', heartbeat_at: null,
    episodes: [{
      id: 're-1', episode_id: 'ep-01', position: 1, title: '第1集', status: 'DELIVERED',
      shot_count: 1, passed_shot_count: 1, planned_seconds: 10, shot_ids: ['shot-1'],
      post_contract: null, actual_seconds: 10, delivery_order_id: 'order-1',
      delivered_artifact_id: 'art-1', delivered_sha256: '8'.repeat(64),
      blocker_code: null, delivered_at: '2026-11-19T00:20:00Z', batches: [],
    }],
  };
}

beforeEach(() => {
  tracks = [];
  window.location.hash = '#d/deliver';
  vi.useFakeTimers();
  stub = new StudioStub(baseRoutes(baseProject()));
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
    respond: () => jsonResponse(journeyBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse(overviewBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
    respond: () => jsonResponse([]) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
    respond: () => jsonResponse(runBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1\/events$/,
    respond: () => jsonResponse([]) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/subtitles$/,
    respond: () => jsonResponse(tracks) });
  stub.add({ method: 'POST', pattern: /\/subtitles\/compile$/,
    respond: () => {
      tracks = [{ id: 'track-1', project_id: 'proj-1', revision: 1, status: 'DRAFT',
        episode_id: 'ep-01', cues: [{ index: 1, start_s: 0.8, end_s: 2.1, text: '殿下，醒醒……' }],
        sha256: 'f'.repeat(64), source: 'shot_dialogue', source_artifact_sha256: null,
        created_by: 'local-user', created_at: '2026-11-19T01:00:00Z', updated_at: '2026-11-19T01:00:00Z' }];
      return jsonResponse({ revision: 1, episode_id: 'ep-01', status: 'DRAFT', cue_count: 1,
        basis: 'actual', warnings: [], sha256: 'f'.repeat(64), manual_shots: 0, dialogue_shots: 1 }, 201);
    } });
  stub.add({ method: 'POST', pattern: /\/subtitles\/1\/approve$/,
    respond: () => {
      tracks = tracks.map((row) => ({ ...row, status: 'APPROVED' }));
      return jsonResponse(tracks[0]);
    } });
  stub.add({ method: 'PUT', pattern: /\/production-runs\/run-1\/episodes\/ep-01\/post-contract$/,
    respond: () => jsonResponse({ delivery_order_id: 'order-2' }, 202) });
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.location.hash = '';
});

describe('⑦ 字幕唯一生成入口', () => {
  it('选集→展开字幕折叠→生成→就地批准应用，同页一次走通', async () => {
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });

    // 单一选集：默认选中第一集；字幕模块折叠在该集预览卡内。
    expect(screen.getByRole('tab', { name: /第1集/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByText(/字幕与台词核对/));
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });

    // 生成：读对白事实直接出轨，不再要求先回③手写冻结。
    fireEvent.click(screen.getByRole('button', { name: '按真实成片时长生成 / 更新字幕轨' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(screen.getByText(/本集字幕第 1 版已按真实成片时长生成/)).toBeInTheDocument();
    expect(stub.callsOf('POST', /subtitles\/compile/)).toHaveLength(1);

    // 应用：折叠内的合同编辑器选草稿版，就地批准再烧录。
    fireEvent.click(screen.getByText(/修订剪辑 \/ 字幕（只重装配，不重拍）/));
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    fireEvent.change(screen.getByLabelText('烧录字幕版本'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: '批准该版字幕并按新合同重新装配' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(stub.callsOf('POST', /subtitles\/1\/approve/).length).toBeGreaterThanOrEqual(1);
    const put = stub.callsOf('PUT', /post-contract/);
    expect(put).toHaveLength(1);
    expect((put[0].body as { subtitle_revision: number }).subtitle_revision).toBe(1);
    expect(screen.getByText(/已按新的剪辑\/字幕合同排队重新装配/)).toBeInTheDocument();
  });

  it('重拍进行中：⑦ 分段卡显示重拍进度条并跟读', async () => {
    stub.add({ method: 'GET', pattern: /\/generation-segments$/,
      respond: () => jsonResponse({ series_id: 'series-1', run_id: 'run-1', segments: [{
        shot_id: 'shot-1', position: 4, episode_position: 1, episode_title: '第1集',
        story_beat: 'beat', duration_s: 4, shot_revision: 16, job_id: 'job-old', attempt_no: 1,
        status: 'NEEDS_REVIEW', artifact_id: 'art-old', blocker: 'RETAKE_AWAITS_SELECTION_OR_REVIEW',
        can_regenerate: false, editorial_cuts: [], dependent_job_ids: [],
        replacement_job_id: 'job-new', replacement_status: 'RUNNING', replacement_progress: 42,
        can_select_replacement: false,
      }] }) });
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    const bars = screen.getAllByRole('progressbar', { name: '重拍第 4 镜进度' });
    expect(bars.length).toBeGreaterThan(0);
    bars.forEach((bar) => expect(bar).toHaveAttribute('aria-valuenow', '42'));
    expect(screen.getAllByText('重拍 · 生成中').length).toBeGreaterThan(0);
  });
});
