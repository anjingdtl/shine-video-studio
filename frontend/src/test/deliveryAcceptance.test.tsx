/** 「待你确认」必须有真实动作：分集级人工验收与单镜分项审片入口。
 *
 * 技术交付不等于艺术验收。这里验证：成片条目与待审清单都有「观看并确认」，
 * 接受必须声明看完、拒绝必须写原因，决定绑定看到的 SHA-256；导演层也能
 * 打开单镜分项审片，不必切去专家层。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import EpisodeAcceptance from '../components/EpisodeAcceptance';
import GenerationSegmentsPanel from '../components/GenerationSegmentsPanel';
import { StudioStub, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

const SHA = 'a'.repeat(64);

function deliveryEntry(overrides: Record<string, unknown> = {}) {
  return {
    artifact_id: 'reel-1',
    sha256: SHA,
    kind: 'delivery',
    project_id: 'proj-1',
    project_title: '雾港夜账',
    episode_position: 1,
    episode_label: '01集',
    shot_numbers: [1, 2],
    shot_range_label: '001-002',
    shot_names: ['镜头 1', '镜头 2'],
    shot_name: null,
    job_id: null,
    take: 1,
    version_label: 'v1',
    review_state: 'pending_review',
    assembly_review_state: 'pending_review',
    acceptance: null,
    adopted: true,
    file_state: 'ok',
    deletable: false,
    delete_blockers: [{ code: 'CURRENT_VERSION', message: '这是当前采用的版本' }],
    shared_file: false,
    duration_s: 12.5,
    width: 1344,
    height: 768,
    fps: 24,
    generated_at: '2026-11-15T07:50:00',
    byte_size: 265045,
    file_name: '雾港夜账_视频_001-002镜_01集.mp4',
    folder: 'current',
    is_mock: false,
    deleted: false,
    preview_url: '/api/v1/deliveries/reel-1/thumbnail?poster=1s',
    play_url: '/api/v1/deliveries/reel-1/media',
    ...overrides,
  };
}

function library(entries: Array<Record<string, unknown>>) {
  return {
    project_id: 'proj-1', project_title: '雾港夜账', entries,
    summary: {
      total: entries.length,
      current: entries.filter((row) => row.adopted).length,
      history: entries.filter((row) => !row.adopted).length,
      pending_review: entries.filter((row) => ['pending_review', 'running', 'needs_attention'].includes(String(row.review_state))).length,
      missing: 0,
      deletable: entries.filter((row) => row.deletable).length,
    },
  };
}

describe('成片库的人工验收', () => {
  it('「观看并确认」记录接受：必须声明看完，且绑定看到的 SHA-256', async () => {
    let accepted = false;
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/video-library$/,
      respond: () => jsonResponse(library([deliveryEntry(accepted
        ? { review_state: 'accepted', acceptance: { id: 'acc-1', decision: 'accepted', watched: true, evidence: '', actor: 'local-user', artifact_sha256: SHA, created_at: '2026-11-15T08:00:00Z' } }
        : {})])),
    });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/deliveries\/reel-1\/acceptance$/,
      respond: (_path, init) => {
        const body = JSON.parse(String(init?.body ?? '{}'));
        expect(body.decision).toBe('accepted');
        expect(body.watched).toBe(true);
        expect(body.expected_sha256).toBe(SHA);
        expect(typeof body.request_id).toBe('string');
        accepted = true;
        return jsonResponse({
          artifact_id: 'reel-1',
          acceptance: { id: 'acc-1', decision: 'accepted', watched: true, evidence: '', actor: 'local-user', artifact_sha256: SHA, created_at: '2026-11-15T08:00:00Z' },
        });
      },
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: /成片库/ }));
    await screen.findByRole('heading', { name: /成片库/ });

    await user.click(await screen.findByRole('button', { name: '观看并确认' }));
    const dialog = await screen.findByRole('dialog', { name: /验收/ });
    const accept = screen.getByRole('button', { name: '确认接受这一版' });
    expect(accept).toBeDisabled();
    await user.click(screen.getByLabelText(/我已完整观看这一版成片/));
    expect(accept).toBeEnabled();
    await user.click(accept);

    await waitFor(() => expect(screen.getByText('已确认接受')).toBeInTheDocument());
    expect(stub.callsOf('POST', /acceptance/)).toHaveLength(1);
    expect(dialog).not.toBeInTheDocument();
  });

  it('拒绝必须写清问题；未勾选观看不能接受', async () => {
    const onDecided = vi.fn();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/deliveries\/reel-1\/acceptance$/,
      respond: (_path, init) => {
        const body = JSON.parse(String(init?.body ?? '{}'));
        expect(body.decision).toBe('rejected');
        expect(body.evidence).toBe('第 3 秒口型不对');
        return jsonResponse({
          artifact_id: 'reel-1',
          acceptance: { id: 'acc-2', decision: 'rejected', watched: false, evidence: body.evidence, actor: 'local-user', artifact_sha256: SHA, created_at: '2026-11-15T08:00:00Z' },
        });
      },
    });
    const user = userEvent.setup();
    render(<EpisodeAcceptance artifactId="reel-1" sha256={SHA} title="第1集" acceptance={null}
      onDecided={onDecided} onClose={() => undefined} />);

    expect(screen.getByRole('button', { name: '确认接受这一版' })).toBeDisabled();
    const reject = screen.getByRole('button', { name: /有问题，拒绝这一版/ });
    expect(reject).toBeDisabled();
    await user.type(screen.getByPlaceholderText(/第 3 镜口型/), '第 3 秒口型不对');
    expect(reject).toBeEnabled();
    await user.click(reject);
    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(onDecided.mock.calls[0][0].decision).toBe('rejected');
  });
});

describe('导演模式待审清单', () => {
  it('已交付的分集显示「待你确认」徽标与「观看并确认」动作', async () => {
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
      respond: () => jsonResponse({
        project_id: 'proj-1', title: '雨夜钟表铺',
        plan: { version: 1, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
        style: { version: 1, sha256: 'a'.repeat(64) },
        counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 1,
          delivered_episode_count: 1, shot_count: 2, passed_shot_count: 2 },
        episodes: [{
          episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 20,
          state: 'DELIVERED', state_label: '已交付',
          storyboard: { section_count: 1, landed_section_count: 1, pending_sections: [],
            ledger_task_id: null, ledger_task_status: null, state_version: null, blocker: null },
          shots: { landed_count: 2, confirmed_count: 2, confirmed: true, problems: [] },
          materials: { missing_count: 0, missing: [] },
          package: null,
          production: { run_id: 'run-1', status: 'COMPLETED', shot_count: 2, passed_shot_count: 2, in_current_run: true },
          delivery: { artifact_id: 'reel-1', sha256: SHA, run_id: 'run-1',
            delivered_at: '2026-11-15T00:20:00Z', actual_seconds: 20, status: 'DELIVERED',
            post_contract: null, acceptance: null },
          next_action: null,
        }],
        next_action: null, package: null,
        run: { run_id: 'run-1', status: 'COMPLETED', active: false, scope_episode_ids: ['ep-01'] },
        storyboard_task: { task_id: null, status: null, state_version: null },
      }),
    });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/production-runs$/,
      respond: () => jsonResponse([{ id: 'run-1', status: 'COMPLETED' }]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1$/,
      respond: () => jsonResponse({
        id: 'run-1', project_id: 'proj-1', package_id: 'package-1', package_revision: 1,
        manifest_sha256: 'd'.repeat(64), scope_digest: 'c'.repeat(64), status: 'COMPLETED',
        state_version: 1, control_reason: null, blocker_count: 0,
        scope: { episode_ids: ['ep-01'], all_episodes: true, shot_count: 2 },
        counters: {},
        progress: { required_episode_count: 1, delivered_episode_count: 1, producing_episode_count: 0,
          blocked_episode_count: 0, batch_count: 1, passed_batch_count: 1, delivered_seconds: 20,
          passed_shot_count: 2 },
        created_at: '2026-11-15T00:00:00Z', started_at: '2026-11-15T00:00:01Z',
        finished_at: '2026-11-15T00:20:00Z', last_progress_at: new Date().toISOString(), heartbeat_at: null,
        episodes: [],
      }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/production-runs\/run-1\/events/,
      respond: () => jsonResponse([]) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 7 阶段/);

    expect(await screen.findByText('技术完成 ≠ 你已经认可')).toBeInTheDocument();
    expect(screen.getByText('待你确认')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '观看并确认' })).toBeInTheDocument();
  });
});

describe('导演层的单镜分项审片', () => {
  it('已通过的分段卡片提供「分项审片」入口，打开人工审片面板', async () => {
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/generation-segments$/,
      respond: () => jsonResponse({
        series_id: 'series-1', run_id: 'run-1',
        segments: [{
          shot_id: 'shot-1', position: 1, story_beat: '雨夜推门', duration_s: 6,
          shot_revision: 1, job_id: 'job-1', attempt_no: 1, series_id: 'series-1',
          episode_position: 1, episode_title: '第1集', status: 'PASSED', artifact_id: 'art-1',
          blocker: null, can_regenerate: true, editorial_cuts: [], dependent_job_ids: [],
          replacement_job_id: null, replacement_status: null, can_select_replacement: false,
          upscale: null,
        }],
      }),
    });
    const user = userEvent.setup();
    render(<GenerationSegmentsPanel projectId="proj-1" fingerprint="f1" />);

    await user.click(await screen.findByRole('button', { name: '分项审片' }));
    expect(await screen.findByRole('dialog', { name: '单镜分项审片' })).toBeInTheDocument();
    expect(screen.getByText('本地操作员分项审片')).toBeInTheDocument();
  });
});