/** D3 回归：镜头拆解被阻塞时，页面必须说清「哪一场、为什么、能点什么」。
 *
 * 2026-11-15 穿测实障：第 2 场 scriptwriter 两次 LLM_FORMAT（流式输出被截断
 * finish_reason=length），整集 BLOCKED。此前页面只说「第 1 集 · 被阻塞」和一句
 * 「先在专业面板核对」——没有场次、没有可点的下一步（用户原话：我都不知道哪个
 * 地方还有镜头）。本文件固定三条恢复契约：
 * 1) 已决拒绝（RESPONSE_RECEIVED）→ 报出场次与标题 + 「重试这一集」；
 * 2) 结果未知（UNKNOWN，含旧版误分类但有截断证据）→ 「核对这次请求并重试」：
 *    reconcile → resend-unknown → 该集重试，全部当场可点；
 * 3) 绝不再出现「先去别处核对」这种没有出口的提示。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { EpisodeStoryboardPanel } from '../components/StreamlinePanels';
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
};

function blockedState(rejection: RejectionOverrides) {
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
        unit_index: 1, status: 'REJECTED', failed_role: 'scriptwriter',
        failed_task_id: FAILED_TASK,
        rejections: [{
          role: 'scriptwriter', code: 'LLM_FORMAT', task_id: FAILED_TASK, state_version: 1,
          rejection_reason_code: 'LLM_FORMAT',
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
  it('已决拒绝（RESPONSE_RECEIVED）报出场次与标题，并可直接「重试这一集」', async () => {
    const retried = vi.fn();
    stubBlockedPanel({ external_outcome: 'RESPONSE_RECEIVED' }, retried);
    render(<EpisodeStoryboardPanel projectId={PROJECT} />);

    const callout = await screen.findByText(/第 2 场/);
    expect(callout.textContent).toContain('雨夜来客');
    // 标题自带的「第N场」前缀不得与序号重复成「第 2 场：第2场…」。
    expect(callout.textContent).not.toContain('第 2 场：第2场');
    expect(callout.textContent).toContain('编剧');
    expect(callout.textContent).toContain('LLM_FORMAT');

    await userEvent.setup().click(screen.getByRole('button', { name: '重试这一集' }));
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
    expect(screen.queryByRole('button', { name: '重试这一集' })).toBeNull();

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
    expect(screen.getByRole('button', { name: '重试这一集' })).toBeEnabled();
  });
});
