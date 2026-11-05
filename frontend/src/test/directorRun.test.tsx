/** 导演包与全片运行面板：状态必须来自服务端，按钮必须只在真的可以推进时出现。
 *
 *  这些是界面契约测试（stub 出来的服务端形状），不是真实生产的证据。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseProject, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })));
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
});

const CONFIRMED_PLAN = {
  id: 'plan-1', project_id: 'proj-1', version: 2, status: 'CONFIRMED', target_seconds: 60,
  tolerance_seconds: 15, sha256: 'b'.repeat(64), source: 'derived',
  payload: {
    target_seconds: 60, tolerance_seconds: 15,
    episodes: [{
      episode_id: 'ep-01', order: 1, title: '第1集', shot_ids: ['shot-1', 'shot-2'],
      planned_seconds: 12, boundary_note: 'final_episode',
    }],
  },
  created_at: '2026-11-01T00:00:00', confirmed_at: '2026-11-01T00:01:00',
};

const CONFIRMED_PACKAGE = {
  id: 'package-1', project_id: 'proj-1', revision: 3, status: 'CONFIRMED', stale: false,
  stale_reason: null, scope_digest: 'c'.repeat(64), manifest_sha256: 'd'.repeat(64),
  policy_hash: 'e'.repeat(64), episode_count: 1, shot_count: 2,
  episode_plan: { plan_id: 'plan-1', version: 2, sha256: 'b'.repeat(64), target_seconds: 60 },
  script: { outline_confirmed: true }, style: { style_version_id: 'style-1', version: 1, sha256: 'a'.repeat(64) },
  binding: {
    workflow_profile_id: 'profile-1', mode: 'Ref2VA', graph_sha256: 'f'.repeat(64),
    checkpoint_id: 'MiniMax-H3-Base', generation_mode: 'Ref2VA', output: {},
  },
  policy: { max_repairs_per_shot: 2 }, episodes: [
    { episode_id: 'ep-01', position: 1, title: '第1集', shot_count: 2,
      planned_seconds: 12, manifest_sha256: '9'.repeat(64) },
  ],
  created_at: '2026-11-01T00:02:00', confirmed_at: '2026-11-01T00:03:00',
};

function stubPackageStage({ startable, blockers }: { startable: boolean; blockers: unknown[] }) {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/episode-plans$/,
    respond: () => jsonResponse([CONFIRMED_PLAN]) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director-packages$/,
    respond: () => jsonResponse([CONFIRMED_PACKAGE]) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/director-packages\/package-1\/readiness$/,
    respond: () => jsonResponse({
      package_id: 'package-1', revision: 3, status: 'CONFIRMED', stale: false,
      scope_digest: 'c'.repeat(64), manifest_sha256: 'd'.repeat(64),
      config_fingerprint: '7'.repeat(64), ready: startable, startable,
      blockers, episode_count: 1, shot_count: 2, checked_at: '2026-11-01T00:04:00Z',
    }) });
}

describe('导演包阶段', () => {
  it('已确认且本机可执行时，才出现“启动整部作品生产”', async () => {
    stubPackageStage({ startable: true, blockers: [] });
    let posted: Record<string, unknown> | null = null;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/production-runs$/,
      respond: (_url, init) => {
        posted = JSON.parse(String(init?.body));
        return jsonResponse({ id: 'run-1', status: 'QUEUED', created: true }, 201);
      } });
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 4 阶段：导演包/);

    expect(await screen.findByText(/第 3 版 · 1 集 · 2 镜/)).toBeInTheDocument();
    expect(await screen.findByText(/本机检查通过/)).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: /启动整部作品生产/ }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted?.['package_id']).toBe('package-1');
  });

  it('有阻塞时不给启动按钮，而是列出原因', async () => {
    stubPackageStage({
      startable: false,
      blockers: [{ code: 'STYLE_SUPERSEDED', message: '冻结风格已变更，请重新生成导演包' }],
    });
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 4 阶段：导演包/);

    expect(await screen.findByText('冻结风格已变更，请重新生成导演包')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /启动整部作品生产/ })).toBeNull();
    // 没有任何分镜时不能生成分集：按钮先禁用，别让人点出一个 422。
    expect(screen.getByRole('button', { name: /生成分集草案/ })).toBeDisabled();
  });
});

describe('全片生产阶段', () => {
  it('按集展示真实进度、批次与阻塞，并保留播放已交付那一集', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/production-runs$/,
      respond: () => jsonResponse([{ id: 'run-1', status: 'RUNNING' }]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
      respond: () => jsonResponse({
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
        episodes: [
          { id: 're-1', episode_id: 'ep-01', position: 1, title: '第1集', status: 'DELIVERED',
            shot_count: 2, passed_shot_count: 2, planned_seconds: 12, actual_seconds: 61.5,
            delivery_order_id: 'order-1', delivered_artifact_id: 'artifact-1',
            delivered_sha256: '8'.repeat(64), blocker_code: null,
            delivered_at: '2026-11-01T00:20:00Z',
            batches: [{ batch_key: 'ep-01-b01', status: 'PASSED', series_id: 'series-1',
              shot_count: 1, failure_code: null, dispatched_at: null },
              { batch_key: 'ep-01-b02', status: 'PASSED', series_id: 'series-2',
                shot_count: 1, failure_code: null, dispatched_at: null }] },
          { id: 're-2', episode_id: 'ep-02', position: 2, title: '第2集', status: 'BLOCKED',
            shot_count: 2, passed_shot_count: 1, planned_seconds: 12, actual_seconds: null,
            delivery_order_id: null, delivered_artifact_id: null, delivered_sha256: null,
            blocker_code: 'EPISODE_SHOT_NOT_PASSED', delivered_at: null,
            batches: [{ batch_key: 'ep-02-b01', status: 'BLOCKED', series_id: 'series-3',
              shot_count: 1, failure_code: 'SERIES_FAILED', dispatched_at: null }] },
        ],
      }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1\/events$/,
      respond: () => jsonResponse([{ id: 1, kind: 'RUN_CREATED', message: '启动整部作品：2 集',
        payload: {}, created_at: '2026-11-01T00:05:00Z' }]) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：视频生产/);

    await waitFor(() => expect(document.querySelector('.run-summary-facts')).not.toBeNull());
    const summary = document.querySelector('.run-summary-facts');
    expect(summary?.textContent).toContain('1 / 2 集已交付');
    expect(summary?.textContent).toContain('3 / 4 镜已通过');
    expect(await screen.findByText(/阻塞：EPISODE_SHOT_NOT_PASSED/)).toBeInTheDocument();
    const video = document.querySelector('video');
    expect(video?.getAttribute('src')).toBe('/api/v1/deliveries/artifact-1/media');
    expect(screen.getByRole('button', { name: /暂停/ })).toBeInTheDocument();
    // 演示态必须说清楚后台不会真的开拍，而不是让人一直等。
    expect(screen.getByText(/当前是演示体验：后台不会真的开拍/)).toBeInTheDocument();
  });

  it('还没有运行时明确说明先确认导演包，而不是假装在跑', async () => {
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：视频生产/);
    expect(await screen.findByText(/还没有整部作品的运行记录/)).toBeInTheDocument();
  });
});
