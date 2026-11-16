/* 衔接确认入口与时长超差核对（三原则：后端早已支持，界面必须暴露）。
 *
 * 2026-11-16 穿测：衔接列只读「切开」，用户无法按剧情调整连贯性；后端
 * edit_shot_row 其实接受 continuity_mode。本文件固定：
 * 1) 衔接列可点击切换，PATCH 带上 continuity_mode（暴露既有后端确认链）；
 * 2) 落镜总时长超差时服务端具名拒绝，界面给出「接受偏差并确认」显式入口。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EpisodeStoryboardPanel } from '../components/StreamlinePanels';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;
beforeEach(() => { stub = new StudioStub([]); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

const PROJECT = 'p1';

function shotRow(overrides: Record<string, unknown> = {}) {
  return {
    shot_id: 's1', position: 1, name: '镜头 1', revision: 1, section_id: 'sec-1',
    story_beat: '老周把怀表贴近耳朵。', characters: ['老周'], dialogue: [],
    duration_target_s: 5, editorial_seconds: 5, h3_segment_count: 1, h3_generation_seconds: 5,
    internal_cuts: [], continuity_mode: 'cut', previous_shot_id: null, required_asset_ids: [],
    prompt: { status: 'compiled', reason: null, revision: null, mode: null, chars: null, frozen_at: null },
    ...overrides,
  };
}

function stubAppliedEpisode(confirmHandler?: (body: unknown) => ReturnType<typeof jsonResponse>) {
  stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
    respond: () => jsonResponse({
      task_id: 'run-1', status: 'SUCCEEDED', state_version: 3,
      episodes: [{ episode_id: 'ep-01', order: 1, status: 'APPLIED', shot_ids: ['s1'],
        units: [{ index: 0, production_section_id: 'sec-1', state: 'APPLIED', shot_count: 1,
          material_notices: [], section_order: 1, section_title: '第1场' }] }],
    }) });
  stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/shots$/,
    respond: () => jsonResponse({ episode_id: 'ep-01', shots: [shotRow()],
      total_editorial_seconds: 5, shot_count: 1 }) });
  stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/,
    respond: () => jsonResponse([]) });
  stub.add({ method: 'PATCH', pattern: /projects\/p1\/director\/storyboard\/shots\/s1$/,
    respond: () => jsonResponse({ shot_id: 's1', revision: 2, changed: true }) });
  stub.add({ method: 'POST', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/confirm$/,
    respond: (_url, init) => confirmHandler
      ? confirmHandler(JSON.parse(String(init?.body)))
      : jsonResponse({ plan_version: 2, episode_id: 'ep-01', shot_ids: ['s1'] }) });
}

describe('衔接确认入口', () => {
  it('衔接列可点击切换，把 continuity_mode 送到既有修订接口', async () => {
    stubAppliedEpisode();
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);
    const toggle = await screen.findByRole('button', { name: '切开' });
    await userEvent.setup().click(toggle);
    await waitFor(() => expect(stub.callsOf('PATCH', /storyboard\/shots\/s1$/)).toHaveLength(1));
    expect(stub.callsOf('PATCH', /storyboard\/shots\/s1$/)[0].body)
      .toMatchObject({ expected_revision: 1, continuity_mode: 'last_frame' });
  });
});

describe('落镜总时长超差：显式核对', () => {
  it('超差被具名拒绝后，界面给出「接受偏差并确认」并带确认标志重发', async () => {
    let calls = 0;
    stubAppliedEpisode((body) => {
      calls += 1;
      if (calls === 1) {
        return jsonResponse({ detail: { code: 'EPISODE_DURATION_OUT_OF_TARGET',
          message: '本集落镜总时长超出目标容差窗', actual_seconds: 5,
          target_seconds: 60, window_low_s: 30, window_high_s: 90 } }, 409);
      }
      expect(body).toMatchObject({ acknowledge_duration_deviation: true });
      return jsonResponse({ plan_version: 2, episode_id: 'ep-01', shot_ids: ['s1'] });
    });
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);
    const confirm = await screen.findByRole('button', { name: /确认本集镜头/ });
    await userEvent.setup().click(confirm);
    const accept = await screen.findByRole('button', { name: '接受偏差并确认本集' });
    await userEvent.setup().click(accept);
    await waitFor(() => expect(calls).toBe(2));
    expect(stub.callsOf('POST', /episodes\/ep-01\/confirm$/)[1].body)
      .toMatchObject({ acknowledge_duration_deviation: true });
  });
});