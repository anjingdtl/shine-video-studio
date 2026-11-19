/** ③ 三处显示同一份投影（2026-11-19 穿测报障回归）。
 *
 * 实障：折叠行「第 1 集 · 被阻塞」没有镜数；阻塞通知说第 3 场，下方表格却对不上；
 * 右轨「还有 9 个场次没有拆成镜头」与左侧报错各说各话。修复后：折叠行带动态镜数，
 * 逐场表与阻塞通知读同一份 units/sections，核对类拒绝码（占用身份旧请求未核对）
 * 路由到 核对→授权一次重发→重试，且目标是服务端命名的旧任务活版本。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { StageRailProvider } from '../components/StageRail';
import { EpisodeStoryboardPanel } from '../components/StreamlinePanels';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

const SUMMARY = '卡在第 3 镜（第 3 场：猝死）：场记 · 上一次同输入请求中断，结果未知，需要先核对。'
  + ' 这次请求的结果不确定，不会自动重发；点「核对这次请求并重试」先核对，仍未知时由你的点击授权一次新请求。';

const STATE = {
  task_id: 'run-1', status: 'NEEDS_REVIEW', state_version: 5, cursor: 2, total_units: 4,
  episodes: [{
    episode_id: 'ep-01', order: 1, status: 'BLOCKED', shot_ids: ['s1', 's2'],
    units: [
      { index: 0, production_section_id: 'sec-3', state: 'APPLIED', shot_count: 2,
        material_notices: [], section_order: 3, section_title: '第3场·猝死' },
      { index: 1, production_section_id: 'sec-3', state: 'BLOCKED', shot_count: 0,
        material_notices: [], section_order: 3, section_title: '第3场·猝死' },
      { index: 2, production_section_id: 'sec-4', state: 'PENDING', shot_count: 0,
        material_notices: [], section_order: 4, section_title: '第4场·梳理记忆' },
    ],
    sections: [
      { production_section_id: 'sec-3', section_order: 3, section_title: '第3场·猝死',
        shot_count: 2, state: 'BLOCKED', unit_count: 2, applied_count: 1 },
      { production_section_id: 'sec-4', section_order: 4, section_title: '第4场·梳理记忆',
        shot_count: 0, state: 'PENDING', unit_count: 1, applied_count: 0 },
    ],
    landed_shot_count: 2, pending_section_count: 1,
    blocker: {
      unit_index: 1, status: 'REJECTED', failed_role: 'continuity_supervisor',
      failed_task_id: 'holder-1',
      summary: SUMMARY, uncertain: true,
      reconcile_task_id: 'holder-1', reconcile_state_version: 7,
      rejections: [{
        role: 'continuity_supervisor', code: 'AGENT_REQUEST_RECONCILIATION_REQUIRED',
        task_id: 'holder-1', state_version: 0, external_outcome: null,
      }],
    },
  }],
};

function shot(id: string, position: number) {
  return {
    shot_id: id, position, name: id, revision: 1, section_id: 'sec-3',
    story_beat: '程岩睁眼成为王子。', characters: ['程岩'], dialogue: [],
    duration_target_s: 6, editorial_seconds: 6, h3_segment_count: 1,
    h3_generation_seconds: 6, internal_cuts: [], continuity_mode: 'cut',
    previous_shot_id: null, required_asset_ids: [],
    prompt: { status: 'compiled', revision: null, mode: null, chars: null, frozen_at: null },
    subtitle: { status: 'EMPTY', revision: 0, line_count: 0 },
  };
}

it('折叠行带动态镜数；逐场表与阻塞通知同源；核对按钮命中占用身份的旧任务', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director\/storyboard\/state$/, respond: () => jsonResponse(STATE) },
    { method: 'GET', pattern: /\/episodes\/ep-01\/shots$/, respond: () => jsonResponse({
      episode_id: 'ep-01', shots: [shot('s1', 1), shot('s2', 2)],
      total_editorial_seconds: 12, shot_count: 2,
      confirmed_version: null, confirmation_stale: false,
    }) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/film-runs\/holder-1\/reconcile$/,
      respond: () => jsonResponse({ outcome: 'UNKNOWN_REQUIRES_NEW_ATTEMPT', state_version: 8 }) },
    { method: 'POST', pattern: /\/film-runs\/holder-1\/resend-unknown$/,
      respond: () => jsonResponse({ outcome: 'USER_AUTHORIZED_NEW_REQUEST', state_version: 9 }) },
    { method: 'POST', pattern: /\/episodes\/ep-01\/retry$/,
      respond: () => jsonResponse({ task_id: 'run-1', status: 'QUEUED', episode_id: 'ep-01' }) },
  ]);
  render(<EpisodeStoryboardPanel projectId="p1" />);

  // 1) 折叠行：被阻塞也带动态镜数（与镜头表同一事实）。
  const summary = await screen.findByText(/第 1 集 · 被阻塞（2 镜）/);
  expect(summary).toBeInTheDocument();

  // 2) 阻塞通知读服务端摘要（镜粒度）；不铺满后续场次：镜头表尾只加
  //    「当前被挡的那一镜」frontier 行，镜位 = 同一 join 累加（2 已落 → 第 3 镜）。
  expect(screen.getByText(SUMMARY)).toBeInTheDocument();
  const tables = screen.getAllByRole('table');
  expect(tables).toHaveLength(1);
  const rows = within(tables[0]).getAllByRole('row');
  expect(rows).toHaveLength(4); // 表头 + 2 已落镜 + frontier
  expect(rows[3].textContent).toContain('3');
  expect(rows[3].textContent).toContain('被阻塞 · 待落镜');
  expect(screen.queryByText(/第 4 场/)).toBeNull();

  // 3) 核对类拒绝码路由到三步链，目标是旧任务活版本（7），不是拒绝记录里的 0。
  await userEvent.setup().click(screen.getByRole('button', { name: '核对这次请求并重试' }));
  await waitFor(() => {
    expect(stub.callsOf('POST', /episodes\/ep-01\/retry$/)).toHaveLength(1);
  });
  expect(stub.callsOf('POST', /film-runs\/holder-1\/reconcile$/)[0].body)
    .toMatchObject({ expected_state_version: 7 });
  expect(stub.callsOf('POST', /film-runs\/holder-1\/resend-unknown$/)[0].body)
    .toMatchObject({ expected_state_version: 8 });
  expect(stub.callsOf('POST', /episodes\/ep-01\/retry$/)[0].body)
    .toMatchObject({ expected_state_version: 5 });
  stub.restore();
});

it('③继承②确认的秒数目标并报名偏差；暂停时主按钮与页内同一执行者', async () => {
  const pausedState = {
    ...STATE, status: 'PAUSED',
    episodes: [{ ...STATE.episodes[0], status: 'IN_PROGRESS', blocker: null }],
  };
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director\/storyboard\/state$/, respond: () => jsonResponse(pausedState) },
    { method: 'GET', pattern: /\/episodes\/ep-01\/shots$/, respond: () => jsonResponse({
      episode_id: 'ep-01', shots: [shot('s1', 1), shot('s2', 2)],
      total_editorial_seconds: 30, shot_count: 2,
      target_seconds: 60, tolerance_seconds: 15,
      duration_verdict: { code: 'DURATION_TARGET_DEVIATION', actual_seconds: 30,
        target_seconds: 60, tolerance_seconds: 15, window_low_s: 45, window_high_s: 75,
        deviation_seconds: -30 },
      confirmed_version: null, confirmation_stale: false,
    }) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/story-preparation\/tasks\/run-1\/resume$/,
      respond: () => jsonResponse({ task_id: 'run-1', status: 'QUEUED' }) },
  ]);
  const goStage = vi.fn();
  render(
    <StageRailProvider stageId="storyboard" stageLabel="第③步 · 镜头拆解">
      <EpisodeStoryboardPanel projectId="p1" onGoStage={goStage} />
    </StageRailProvider>,
  );
  const hint = await screen.findByText(/②确认目标 60 秒/);
  expect(hint.textContent).toMatch(/低于目标窗 30 秒/);
  expect(hint.textContent).toContain('按②目标确认');
  // 暂停提示名实一致：没有可确认的集就不承诺「先处理已完成的集」，
  // 并给真实出口（范围由②决定）。
  expect(screen.queryByText(/或先确认已完成的集/)).toBeNull();
  await userEvent.setup().click(screen.getByRole('button', { name: '去②调整分镜书范围' }));
  expect(goStage).toHaveBeenCalledWith('plan-stage');
  const rail = screen.getByLabelText('本阶段操作');
  await userEvent.setup().click(within(rail).getByRole('button', { name: '继续后台分镜' }));
  await waitFor(() => {
    expect(stub.callsOf('POST', /tasks\/run-1\/resume$/)).toHaveLength(1);
  });
  expect(stub.callsOf('POST', /tasks\/run-1\/resume$/)[0].body)
    .toMatchObject({ expected_state_version: 5 });
  stub.restore();
});

it('超窗时主操作是「按②目标确认」取最接近前缀，服务端预览数字直接上按钮', async () => {
  const overrunState = {
    ...STATE, status: 'SUCCEEDED',
    episodes: [{ ...STATE.episodes[0], status: 'APPLIED', blocker: null }],
  };
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director\/storyboard\/state$/, respond: () => jsonResponse(overrunState) },
    { method: 'GET', pattern: /\/episodes\/ep-01\/shots$/, respond: () => jsonResponse({
      episode_id: 'ep-01', shots: [shot('s1', 1), shot('s2', 2), shot('s3', 3), shot('s4', 4)],
      total_editorial_seconds: 26, shot_count: 4,
      target_seconds: 20, tolerance_seconds: 5,
      duration_verdict: { code: 'DURATION_TARGET_DEVIATION', actual_seconds: 26, target_seconds: 20,
        tolerance_seconds: 5, window_low_s: 15, window_high_s: 25, deviation_seconds: 6 },
      target_fit: { shot_count: 3, seconds: 20, shot_ids: ['s1', 's2', 's3'] },
      confirmed_version: null, confirmation_stale: false,
    }) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/episodes\/ep-01\/confirm$/,
      respond: (_u, init) => jsonResponse({ plan_version: 2, episode_id: 'ep-01',
        shot_ids: JSON.parse(String(init?.body)).fit_target ? ['s1', 's2', 's3'] : ['s1', 's2', 's3', 's4'] }) },
  ]);
  render(<EpisodeStoryboardPanel projectId="p1" />);
  const fitBtn = await screen.findByRole('button', { name: /按②目标确认（取前 3 镜 · 20 秒）/ });
  await userEvent.setup().click(fitBtn);
  await waitFor(() => {
    expect(stub.callsOf('POST', /episodes\/ep-01\/confirm$/)).toHaveLength(1);
  });
  expect(stub.callsOf('POST', /episodes\/ep-01\/confirm$/)[0].body)
    .toMatchObject({ fit_target: true });
  stub.restore();
});
