/** ⑤ 拍摄范围的占用裁决只读服务端 overview.run.active（2026-11-26 真机穿测）。
 *
 *  缺陷：前端自己抄了一份「哪些状态算在拍」的清单，与后端 ACTIVE_RUN_STATUSES
 *  漂移。暂停收尾中的运行（PAUSE_REQUESTED）在⑥页被显示成「运行已停止」且没有
 *  取消入口；取消之后用同一导演包再次开拍，又被幂等键返还成已取消的旧运行。
 *  修复后：⑤/⑥ 都只读 overview.run.active 这一份裁决；运行终结后同一导演包可再开拍。
 *  这里用 stub 的服务端形状验证界面按服务端裁决行事，不是生产证据。 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';

import App from '../App';
import {
  PRODUCTION_MODE_READY, StudioStub, SYSTEM_STATUS, baseProject, baseRoutes,
  gotoDirectorPage, jsonResponse,
} from './studioStub';

let stub: StudioStub;
let runStatus: string;
let runActive: boolean;

function overviewBody() {
  return {
    project_id: 'proj-1', title: '灰烬圣剑',
    plan: { version: 9, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
    style: { version: 2, sha256: 'a'.repeat(64) },
    counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 1,
      delivered_episode_count: 0, shot_count: 8, passed_shot_count: 3 },
    episodes: [{
      episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 60,
      state: 'READY_TO_PACKAGE', state_label: '可打包',
      storyboard: { section_count: 2, landed_section_count: 2, pending_sections: [],
        ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 3, blocker: null },
      shots: { landed_count: 8, confirmed_count: 8, confirmed: true, problems: [] },
      materials: { missing_count: 0, missing: [] },
      package: null, production: null, delivery: null,
      next_action: { kind: 'BUILD_PACKAGE', label: '把这一集生成导演包' },
    }],
    next_action: null, package: null,
    run: { run_id: 'run-1', status: runStatus, active: runActive, scope_episode_ids: ['ep-01'] },
    storyboard_task: { task_id: 'task-1', status: 'SUCCEEDED', state_version: 3 },
  };
}

function runBody() {
  return {
    id: 'run-1', project_id: 'proj-1', package_id: 'package-1', package_revision: 1,
    manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64), status: runStatus,
    state_version: 7, control_reason: 'USER_PAUSED', blocker_count: 0,
    scope: { episode_ids: ['ep-01'], all_episodes: false, shot_count: 8 },
    counters: {},
    progress: { required_episode_count: 1, delivered_episode_count: 0, producing_episode_count: 1,
      blocked_episode_count: 0, batch_count: 2, passed_batch_count: 0,
      delivered_seconds: 0, passed_shot_count: 3 },
    created_at: '2026-11-26T00:00:00Z', started_at: '2026-11-26T00:00:01Z',
    finished_at: null, last_progress_at: '2026-11-26T00:10:00Z', heartbeat_at: null,
    episodes: [],
  };
}

beforeEach(() => {
  runStatus = 'PAUSE_REQUESTED';
  runActive = true;
  stub = new StudioStub(baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })));
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse(overviewBody()) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
    respond: () => jsonResponse(runBody()) });
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
      accelerators: [{ accelerator_id: 'native20', steps: 20, verified: true }],
      second_pass: { supported: false, recipes: [] },
      upscale: [],
    }) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/system\/status$/, respond: () => jsonResponse({
    ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: true, production_mode: PRODUCTION_MODE_READY }) });
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

describe('⑤ 拍摄范围的占用裁决只读服务端 overview.run.active', () => {
  it('暂停收尾中的运行仍占着拍摄范围：不给开拍入口，并指向视频生产页', async () => {
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);

    expect(await screen.findByText('这次拍摄正在视频生产里进行')).toBeInTheDocument();
    expect(screen.queryAllByRole('button', { name: /确认拍摄方案并开始真实制作/ })).toHaveLength(0);
    expect(screen.getByRole('button', { name: '去视频生产看进度' })).toBeInTheDocument();
  });

  it('运行取消后（active=false）同一导演包重新给出开拍入口', async () => {
    runStatus = 'CANCELLED';
    runActive = false;
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);

    const [start] = await screen.findAllByRole('button', { name: /确认拍摄方案并开始真实制作/ });
    await waitFor(() => expect(start).toBeEnabled());
    expect(screen.queryByText('这次拍摄正在视频生产里进行')).toBeNull();
  });
});
