/** D3 回归：镜头拆解被阻塞时，页面必须说清「哪一场、为什么、能点什么」。
 *
 * 2026-11-15 穿测实障：第 2 场 scriptwriter 两次 LLM_FORMAT（流式输出被截断
 * finish_reason=length），整集 BLOCKED。此前页面只说「第 1 集 · 被阻塞」和一句
 * 「先在专业面板核对」——没有场次、没有可点的下一步（用户原话：我都不知道哪个
 * 地方还有镜头）。本文件固定三条恢复契约：
 * 1) 已决拒绝（RESPONSE_RECEIVED）→ 报出场次与标题 + 「重试这一场」；
 * 2) 结果未知（UNKNOWN，含旧版误分类但有截断证据）→ 「核对这次请求并重试」：
 *    reconcile → resend-unknown → 该集重试，全部当场可点；
 * 3) 绝不再出现「先去别处核对」这种没有出口的提示。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EpisodeStoryboardPanel } from '../components/StreamlinePanels';
import { StageRailProvider } from '../components/StageRail';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub([]);
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

const PROJECT = 'p1';
const FAILED_TASK = 'task-b3a4';

type RejectionOverrides = {
  external_outcome?: string;
  provider_finish_reason?: string | null;
  provider_text_chars?: number | null;
  /** V4 戏剧层的两步也是这条链上的工种名：集纲架构 / 戏剧改编。 */
  role?: string;
  reason?: string;
  code?: string;
};

function blockedState(rejection: RejectionOverrides) {
  const role = rejection.role ?? 'scriptwriter';
  return {
    task_id: 'run-1', status: 'NEEDS_REVIEW', state_version: 6,
    error_code: 'DIRECTOR_STORYBOARD_EPISODE_BLOCKED', cursor: 6, total_units: 6,
    episodes: [{
      episode_id: 'ep-01', order: 1,
      units: [
        { index: 0, production_section_id: 'sec-1', state: 'APPLIED', shot_count: 2,
          material_notices: [], section_order: 1, section_title: '第1场·深夜旧铺' },
        { index: 1, production_section_id: 'sec-2', state: 'BLOCKED', shot_count: 0,
          material_notices: [], section_order: 2, section_title: '第2场·雨夜来客' },
        { index: 2, production_section_id: 'sec-3', state: 'BLOCKED', shot_count: 0,
          material_notices: [], section_order: 3, section_title: '第3场·潮汐档案室' },
      ],
      shot_ids: ['s1', 's2'],
      status: 'BLOCKED',
      blocker: {
        unit_index: 1, status: 'REJECTED', failed_role: role,
        failed_task_id: FAILED_TASK,
        rejections: [{
          role, code: rejection.code ?? 'LLM_FORMAT', task_id: FAILED_TASK, state_version: 1,
          rejection_reason_code: rejection.reason ?? 'LLM_FORMAT',
          provider_finish_reason: rejection.provider_finish_reason ?? null,
          provider_text_chars: rejection.provider_text_chars ?? null,
          external_outcome: rejection.external_outcome ?? 'UNKNOWN',
        }],
      },
    }],
  };
}

function stubBlockedPanel(rejection: RejectionOverrides, onRetry?: () => void) {
  stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
    respond: () => jsonResponse(blockedState(rejection)) });
  stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/shots$/,
    respond: () => jsonResponse({ episode_id: 'ep-01', shots: [], total_editorial_seconds: 0, shot_count: 0 }) });
  stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/,
    respond: () => jsonResponse([]) });
  stub.add({ method: 'POST', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/retry$/,
    respond: () => { onRetry?.(); return jsonResponse({ task_id: 'run-1', status: 'QUEUED', episode_id: 'ep-01' }); } });
  stub.add({ method: 'POST', pattern: /film-runs\/task-b3a4\/reconcile$/,
    respond: () => jsonResponse({ outcome: 'UNKNOWN_REQUIRES_NEW_ATTEMPT', state_version: 2, resubmittable: false }) });
  stub.add({ method: 'POST', pattern: /film-runs\/task-b3a4\/resend-unknown$/,
    respond: () => jsonResponse({ outcome: 'USER_AUTHORIZED_NEW_REQUEST', state_version: 3, resubmittable: true }) });
}

describe('镜头拆解被阻塞：哪一场、为什么、能点什么', () => {
  it('已决拒绝（RESPONSE_RECEIVED）报出场次与标题，并可直接「重试这一场」', async () => {
    const retried = vi.fn();
    stubBlockedPanel({ external_outcome: 'RESPONSE_RECEIVED' }, retried);
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 2 场/);
    expect(callout.textContent).toContain('雨夜来客');
    // 标题自带的「第N场」前缀不得与序号重复成「第 2 场：第2场…」。
    expect(callout.textContent).not.toContain('第 2 场：第2场');
    expect(callout.textContent).toContain('编剧');
    expect(callout.textContent).toContain('LLM_FORMAT');
    // 动作是「回退到失败单元续跑」，标签与说明须与之一致：只重跑这一场，已落镜保留。
    expect(callout.textContent).toContain('已落镜的场次保留');

    await userEvent.setup().click(screen.getByRole('button', { name: '重试这一场' }));
    await waitFor(() => expect(retried).toHaveBeenCalledTimes(1));
    expect(stub.callsOf('POST', /episodes\/ep-01\/retry$/)[0].body)
      .toMatchObject({ expected_state_version: 6 });
  });

  it('结果未知（含截断证据的旧版误分类）给出「核对这次请求并重试」三步链', async () => {
    const retried = vi.fn();
    stubBlockedPanel(
      { external_outcome: 'UNKNOWN', provider_finish_reason: 'length', provider_text_chars: 3140 },
      retried);
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 2 场/);
    expect(callout.textContent).toContain('结果不确定');
    // 旧版提示「先在专业面板核对」不再出现：当场就有可点的下一步。
    expect(callout.textContent).not.toContain('先在专业面板核对');
    expect(screen.queryByRole('button', { name: '重试这一场' })).toBeNull();

    await userEvent.setup().click(screen.getByRole('button', { name: '核对这次请求并重试' }));

    await waitFor(() => expect(retried).toHaveBeenCalledTimes(1));
    const reconcile = stub.callsOf('POST', /film-runs\/task-b3a4\/reconcile$/);
    const resend = stub.callsOf('POST', /film-runs\/task-b3a4\/resend-unknown$/);
    const retry = stub.callsOf('POST', /episodes\/ep-01\/retry$/);
    expect(reconcile).toHaveLength(1);
    expect(resend).toHaveLength(1);
    expect(retry).toHaveLength(1);
    expect(reconcile[0].body).toMatchObject({ expected_state_version: 1 });
    expect(resend[0].body).toMatchObject({ expected_state_version: 2 });
    expect(retry[0].body).toMatchObject({ expected_state_version: 6 });
    // 顺序必须是核对 → 授权重发 → 该集重试。
    const order = stub.calls
      .filter((call) => /film-runs\/task-b3a4\/(reconcile|resend-unknown)|episodes\/ep-01\/retry/.test(call.url))
      .map((call) => call.url);
    expect(order).toEqual([
      `/api/v1/projects/${PROJECT}/film-runs/${FAILED_TASK}/reconcile`,
      `/api/v1/projects/${PROJECT}/film-runs/${FAILED_TASK}/resend-unknown`,
      `/api/v1/projects/${PROJECT}/director/storyboard/episodes/ep-01/retry`,
    ]);
  });

  it('真正未知（无任何已决证据）同样给出「核对这次请求并重试」，而非死提示', async () => {
    stubBlockedPanel({ external_outcome: 'UNKNOWN' });
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 2 场/);
    expect(callout.textContent).toContain('结果不确定');
    expect(callout.textContent).not.toContain('先在专业面板核对');
    expect(screen.getByRole('button', { name: '核对这次请求并重试' })).toBeEnabled();
  });

  it('策略拒绝码（未批准素材）给出人话原因而不是裸码', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
      respond: () => jsonResponse({
        task_id: 'run-1', status: 'NEEDS_REVIEW', state_version: 7,
        error_code: 'DIRECTOR_STORYBOARD_EPISODE_BLOCKED', cursor: 6, total_units: 6,
        episodes: [{
          episode_id: 'ep-01', order: 1,
          units: [
            { index: 0, production_section_id: 'sec-1', state: 'APPLIED', shot_count: 2,
              material_notices: [], section_order: 1, section_title: '第1场' },
            { index: 1, production_section_id: 'sec-2', state: 'BLOCKED', shot_count: 0,
              material_notices: [], section_order: 2, section_title: '第2场·潮汐档案室' },
          ],
          shot_ids: ['s1', 's2'], status: 'BLOCKED',
          blocker: {
            unit_index: 1, status: 'REJECTED', failed_role: 'director',
            failed_task_id: FAILED_TASK,
            rejections: [{
              role: 'director', code: 'CONTRACT_OR_POLICY_REJECTED',
              task_id: FAILED_TASK, state_version: 1,
              rejection_reason_code: 'UNAPPROVED_ASSET_OR_UNKNOWN_BEAT',
              external_outcome: 'RESPONSE_RECEIVED',
            }],
          },
        }],
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/shots$/,
      respond: () => jsonResponse({ episode_id: 'ep-01', shots: [], total_editorial_seconds: 0, shot_count: 0 }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/,
      respond: () => jsonResponse([]) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/retry$/,
      respond: () => jsonResponse({ task_id: 'run-1', status: 'QUEUED', episode_id: 'ep-01' }) });
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 2 场/);
    expect(callout.textContent).toContain('镜头引用了未批准的主素材');
    expect(callout.textContent).toContain('UNAPPROVED_ASSET_OR_UNKNOWN_BEAT');
    expect(screen.getByRole('button', { name: '重试这一场' })).toBeEnabled();
  });

  it('LLM 超时无回应：报出具体秒数与未知结果，并给出核对重试入口', async () => {
    // 实障：摄影 streaming 拿到 200 后 600s 无首事件 → LLM_CONNECTION/UNKNOWN。
    // 旧文案「连不上远端模型」与事实不符（连接已建立）；须用记录自带的死线事实
    // 说清「超过 600 秒无回应」，重试仍走唯一的核对+授权重发入口。
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
      respond: () => jsonResponse({
        task_id: 'run-1', status: 'NEEDS_REVIEW', state_version: 8,
        error_code: 'DIRECTOR_STORYBOARD_EPISODE_BLOCKED', cursor: 4, total_units: 7,
        episodes: [{
          episode_id: 'ep-01', order: 1,
          units: [
            { index: 3, production_section_id: 'sec-4', state: 'APPLIED', shot_count: 2,
              material_notices: [], section_order: 4, section_title: '第4场·雨夜独行' },
            { index: 4, production_section_id: 'sec-5', state: 'BLOCKED', shot_count: 0,
              material_notices: [], section_order: 5, section_title: '第5场·伞下停留' },
          ],
          shot_ids: ['s1'], status: 'BLOCKED',
          blocker: {
            unit_index: 4, status: 'NEEDS_REVIEW', failed_role: 'cinematographer',
            failed_task_id: FAILED_TASK,
            rejections: [{
              role: 'cinematographer', code: 'LLM_CONNECTION',
              rejection_reason_code: 'LLM_CONNECTION', task_id: FAILED_TASK, state_version: 1,
              external_outcome: 'UNKNOWN', network_timeout_s: 600, phase: 'WAITING_PROVIDER',
            }],
          },
        }],
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/shots$/,
      respond: () => jsonResponse({ episode_id: 'ep-01', shots: [], total_editorial_seconds: 0, shot_count: 0 }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/,
      respond: () => jsonResponse([]) });
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 5 场/);
    expect(callout.textContent).toContain('模型超过 600 秒无回应');
    expect(callout.textContent).toContain('结果未知');
    expect(callout.textContent).not.toContain('连不上远端模型');
    expect(screen.getByRole('button', { name: '核对这次请求并重试' })).toBeEnabled();
  });

  it('V4 戏剧改编被拒：报出「戏剧改编」与人话原因，仍由同一个重试入口续跑', async () => {
    // 小说先改编成戏再进分镜；一句台词一个镜头说不完时，服务器按字数具名拒绝。
    // 页面必须说清是改编这一步、为什么，而不是裸码或错报成「编剧」。
    const retried = vi.fn();
    stubBlockedPanel({
      external_outcome: 'RESPONSE_RECEIVED', role: 'drama_scene',
      code: 'CONTRACT_OR_POLICY_REJECTED', reason: 'DRAMA_LINE_TOO_LONG',
    }, retried);
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 2 场/);
    expect(callout.textContent).toContain('戏剧改编');
    expect(callout.textContent).not.toContain('编剧');
    expect(callout.textContent).toContain('有一句台词太长');
    expect(callout.textContent).toContain('DRAMA_LINE_TOO_LONG');
    await userEvent.setup().click(screen.getByRole('button', { name: '重试这一场' }));
    await waitFor(() => expect(retried).toHaveBeenCalledTimes(1));
  });

  it('V4 集纲架构缺场：报出「集纲架构」与缺的场次槽位，而不是裸码', async () => {
    stubBlockedPanel({
      external_outcome: 'RESPONSE_RECEIVED', role: 'series_architecture',
      code: 'CONTRACT_OR_POLICY_REJECTED', reason: 'ARCHITECTURE_SCENE_COVERAGE',
    });
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 2 场/);
    expect(callout.textContent).toContain('集纲架构');
    expect(callout.textContent).toContain('没有给每一场各排一个场次槽位');
    expect(screen.getByRole('button', { name: '重试这一场' })).toBeEnabled();
  });
});

describe('重跑拆镜后：当前镜头事实跟随最新 run，而非陈旧确认方案', () => {
  // 活缺陷（2026-11-16）：确认过 v6（17 镜）后重跑拆镜落了 6 镜，页面仍显示
  // 「已确认 v6，17 镜」，重跑看起来只是在叠加记录。镜头表必须读最新落镜，
  // 徽章区分「已确认 17 镜」与「最新 6 镜待重新确认」，并给出重新确认入口。
  function liveShot(id: string, position: number) {
    return {
      shot_id: id, position, name: id, revision: 1, section_id: 'sec-1',
      story_beat: '阿远走在雨街上。', characters: ['阿远'], dialogue: [],
      duration_target_s: 10, editorial_seconds: 10, h3_segment_count: 1,
      h3_generation_seconds: 10, internal_cuts: [], continuity_mode: 'cut',
      previous_shot_id: null, required_asset_ids: [],
      prompt: { status: 'compiled', revision: null, mode: null, chars: null, frozen_at: null },
      subtitle: { status: 'EMPTY', revision: 0, line_count: 0 },
    };
  }

  it('确认快照与最新落镜不同时，徽章如实区分并给出重新确认入口', async () => {
    const confirmed = vi.fn();
    const oldIds = Array.from({ length: 17 }, (_, i) => `old-${i + 1}`);
    const newShots = Array.from({ length: 6 }, (_, i) => liveShot(`new-${i + 1}`, i + 1));
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
      respond: () => jsonResponse({
        task_id: 'run-1', status: 'SUCCEEDED', state_version: 7, cursor: 6, total_units: 6,
        episodes: [{ episode_id: 'ep-01', order: 1, units: [],
          shot_ids: newShots.map((s) => s.shot_id), status: 'APPLIED' }],
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/shots$/,
      respond: () => jsonResponse({
        episode_id: 'ep-01', shots: newShots, total_editorial_seconds: 60, shot_count: 6,
        confirmed_shot_ids: oldIds, confirmed_version: 6, confirmation_stale: true,
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/,
      respond: () => jsonResponse([{ version: 6, status: 'CONFIRMED',
        payload: { episodes: [{ episode_id: 'ep-01', shot_ids: oldIds }] } }]) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/confirm$/,
      respond: () => { confirmed(); return jsonResponse({ plan_version: 7, episode_id: 'ep-01', shot_ids: newShots.map((s) => s.shot_id) }); } });
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const summary = await screen.findByText(/最新拆镜 6 镜 · 待重新确认/);
    // 徽章主数字与下方镜头表一致（都是最新 6 镜）；旧确认 17 镜只在重新确认说明里。
    expect(summary.textContent).not.toContain('17 镜');
    const hint = await screen.findByText(/本集已落 6 镜 · 合计 60 秒/);
    expect(hint.textContent).toContain('60 秒');
    expect(document.querySelectorAll('.shot-table-row').length).toBe(6);
    expect(screen.getByText(/与已确认的 v6（17 镜）不同/)).toBeTruthy();
    const reconfirm = screen.getByRole('button', { name: '重新确认本集（采用最新 6 镜）' });
    await userEvent.setup().click(reconfirm);
    await waitFor(() => expect(confirmed).toHaveBeenCalledTimes(1));
  });

  it('全部场次已确认且无新拆镜时，主操作是去素材准备，而不是空跑的再跑按钮', async () => {
    // 三原则（2026-11-16）：操作栏名实一致，且遵循旅程顺序。6 场全确认（v7）后
    // 没有「未确认的场次」，再挂「再跑一遍」就是名实不符；下一步是拍摄素材准备
    // （第④阶段），不能跳过素材直接去导演包。
    const ids = Array.from({ length: 6 }, (_, i) => `s-${i + 1}`);
    const shots = ids.map((id, i) => liveShot(id, i + 1));
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
      respond: () => jsonResponse({
        task_id: 'run-1', status: 'SUCCEEDED', state_version: 8, cursor: 6, total_units: 6,
        episodes: [{ episode_id: 'ep-01', order: 1, units: [], shot_ids: ids, status: 'APPLIED' }],
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/shots$/,
      respond: () => jsonResponse({
        episode_id: 'ep-01', shots, total_editorial_seconds: 60, shot_count: 6,
        confirmed_shot_ids: ids, confirmed_version: 7, confirmation_stale: false,
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/,
      respond: () => jsonResponse([{ version: 7, status: 'CONFIRMED',
        payload: { episodes: [{ episode_id: 'ep-01', shot_ids: ids }] } }]) });
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    expect(await screen.findByRole('button', { name: '去素材准备' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /再跑一遍（未确认的场次）/ })).toBeNull();
    expect(screen.queryByRole('button', { name: '去导演包' })).toBeNull();
  });

  it('两集都已整集落镜但未确认时，右栏提供确认入口而非「再跑一遍」，且每集按钮可点击', async () => {
    const ep1Shots = [liveShot('ep1-s1', 1), liveShot('ep1-s2', 2)];
    const ep2Shots = [liveShot('ep2-s1', 1), liveShot('ep2-s2', 2)];
    const confirmedMap: Record<string, string[]> = {};
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/state$/,
      respond: () => jsonResponse({
        task_id: 'run-1', status: 'SUCCEEDED', state_version: 5, cursor: 4, total_units: 4,
        episodes: [
          { episode_id: 'ep-01', order: 1, units: [], shot_ids: ['ep1-s1', 'ep1-s2'],
            status: 'APPLIED', landed_shot_count: 2 },
          { episode_id: 'ep-02', order: 2, units: [], shot_ids: ['ep2-s1', 'ep2-s2'],
            status: 'APPLIED', landed_shot_count: 2 },
        ],
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-01\/shots$/,
      respond: () => jsonResponse({
        episode_id: 'ep-01', shots: ep1Shots, total_editorial_seconds: 60, shot_count: 2,
        target_seconds: 60, tolerance_seconds: 30, duration_verdict: null,
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/storyboard\/episodes\/ep-02\/shots$/,
      respond: () => jsonResponse({
        episode_id: 'ep-02', shots: ep2Shots, total_editorial_seconds: 60, shot_count: 2,
        target_seconds: 60, tolerance_seconds: 30, duration_verdict: null,
      }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/episode-plans$/,
      respond: () => jsonResponse([{
        version: Object.keys(confirmedMap).length + 1, status: 'CONFIRMED',
        payload: {
          episodes: [
            { episode_id: 'ep-01', shot_ids: confirmedMap['ep-01'] ?? [] },
            { episode_id: 'ep-02', shot_ids: confirmedMap['ep-02'] ?? [] },
          ],
        },
      }]) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/storyboard\/episodes\/(ep-0\d)\/confirm$/,
      respond: (url) => {
        const epId = /episodes\/(ep-0\d)\/confirm/.exec(url)?.[1] ?? '';
        confirmedMap[epId] = epId === 'ep-01' ? ['ep1-s1', 'ep1-s2'] : ['ep2-s1', 'ep2-s2'];
        return jsonResponse({ plan_version: 2, episode_id: epId, shot_ids: confirmedMap[epId] });
      } });

    render(
      <StageRailProvider stageId="storyboard" stageLabel="第③步 · 镜头拆解">
        <EpisodeStoryboardPanel projectId={PROJECT} />
      </StageRailProvider>,
    );

    const railConfirm = await screen.findByRole('button', { name: '确认全部已落镜（2 集）' });
    expect(railConfirm).toBeEnabled();
    expect(screen.queryByRole('button', { name: /再跑一遍（未确认的场次）/ })).toBeNull();
    const perEpisodeButtons = screen.getAllByRole('button', { name: /确认全部落镜（2 镜）/ });
    expect(perEpisodeButtons).toHaveLength(2);
    perEpisodeButtons.forEach((btn) => expect(btn).toBeEnabled());

    await userEvent.setup().click(railConfirm);
    await waitFor(() => {
      expect(stub.callsOf('POST', /episodes\/ep-01\/confirm$/)).toHaveLength(1);
      expect(stub.callsOf('POST', /episodes\/ep-02\/confirm$/)).toHaveLength(1);
    });
    // 确认的是整集：请求体只带「是否接受偏差」，没有按目标取前缀的开关。
    expect(stub.callsOf('POST', /episodes\/ep-01\/confirm$/)[0].body)
      .toEqual({ acknowledge_duration_deviation: false });
    expect(stub.callsOf('POST', /episodes\/ep-02\/confirm$/)[0].body)
      .toEqual({ acknowledge_duration_deviation: false });
    expect(await screen.findByRole('button', { name: '去素材准备' })).toBeInTheDocument();
  });
});
