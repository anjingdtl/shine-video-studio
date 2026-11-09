/** 四阶段面板的恢复闭环：刷新后仍然有活儿可做，核对结论说了算。
 *  面板渲染的是服务端状态，不是本地 state：刷新、崩溃、重启之后都���恢复。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FourStagePanel } from '../components/FourStagePanel';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;
const PROJECT = 'proj-1';

function overview(extra = {}) {
  return {
    master: { document_id: 'doc-1', sha256: 'a'.repeat(64) },
    volume_plan_confirmed: true, bible_confirmed: true,
    volume_plan: { artifact_id: 'plan-1', sha256: 'b'.repeat(64), volumes: [] },
    bible_status: { artifact_id: 'bible-1', sha256: 'c'.repeat(64), blockers: [] },
    pending_bible: null, pending_scripts: [], volumes: [],
    ...extra,
  };
}

function volume(extra = {}) {
  return {
    volume_id: 'v1', order: 1, title: '第1卷', char_count: 100, outline_confirmed: true,
    scripts: [{ section_id: 's1', scene_key: 'S1', title: '出海', confirmed: true }],
    scripts_confirmed: 1, scripts_total: 1,
    storyboard: { status: 'INCOMPLETE', updated_at: '2026-11-08T00:00:00Z', units: [] },
    episode_contracts: [], handoff: null,
    ...extra,
  };
}

beforeEach(() => { stub = new StudioStub([]); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

describe('四阶段面板的恢复闭环', () => {
  it('刷新之后仍能看到待应用的镜头：面板渲染的是服务端状态', async () => {
    const state = overview({ volumes: [volume({ storyboard: {
      status: 'INCOMPLETE', updated_at: null,
      units: [
        { unit_id: 's1', section_id: 's1', order: 1, status: 'PROPOSED',
          camera_artifact_id: 'cam-1', continuity_artifact_id: 'con-1',
          arbitration_artifact_id: 'arb-1', applied: false, shot_ids: [] },
        { unit_id: 's2', section_id: 's2', order: 2, status: 'REJECTED',
          camera_artifact_id: null, continuity_artifact_id: null,
          arbitration_artifact_id: null, applied: false, shot_ids: [],
          rejections: [{ code: 'LLM_HTTP', task_id: 'task-9', state_version: 3 }] },
      ],
    } })] });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(state) });
    stub.add({ method: 'POST', pattern: /\/film-artifacts\/cam-1\/apply-camera$/,
               respond: () => jsonResponse({ shot_ids: ['shot-1'] }) });
    const first = render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await screen.findByRole('button', { name: '应用这一场的镜头' });
    expect(screen.getByRole('button', { name: '核对并只重做这一场' })).toBeInTheDocument();
    // a refresh: the panel is mounted again and reads the same server state
    first.unmount();
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    expect(await screen.findByRole('button', { name: '应用这一场的镜头' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '核对并只重做这一场' })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: '应用这一场的镜头' }));
    expect(stub.callsOf('POST', /\/apply-camera$/)[0]?.body).toEqual({
      continuity_artifact_id: 'con-1', arbitration_artifact_id: 'arb-1',
    });
  });

  it('核对得出「确实没有发出」才只重做那一场，不会重跑整卷', async () => {
    const state = overview({ volumes: [volume({ storyboard: {
      status: 'INCOMPLETE', updated_at: null,
      units: [
        { unit_id: 's2', section_id: 's2', order: 2, status: 'REJECTED',
          camera_artifact_id: null, continuity_artifact_id: null,
          arbitration_artifact_id: null, applied: false, shot_ids: [],
          rejections: [{ code: 'LLM_HTTP', task_id: 'task-9', state_version: 3 }] },
      ],
    } })] });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(state) });
    stub.add({ method: 'POST', pattern: new RegExp('/film-runs/task-9/reconcile$'),
               respond: () => jsonResponse({ outcome: 'NOT_SENT_PROVEN', resubmittable: true,
                                             state_version: 4, note: '已确认上一次没有发出，可以继续这一项。' }) });
    stub.add({ method: 'POST', pattern: new RegExp('/storyboard-draft$'),
               respond: () => jsonResponse({ status: 'PROPOSED', units: [], steps: {} }) });
    const user = userEvent.setup();
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await user.click(await screen.findByRole('button', { name: '核对并只重做这一场' }));
    await waitFor(() => expect(stub.callsOf('POST', new RegExp('/storyboard-draft$'))).toHaveLength(1));
    const call = stub.callsOf('POST', new RegExp('/storyboard-draft$'))[0];
    expect((call?.body as { section_ids?: string[] })?.section_ids).toEqual(['s2']);
  });

  it('核对结论是「未知」时如实显示，不重跑任何调用', async () => {
    const state = overview({ volumes: [volume({ storyboard: {
      status: 'INCOMPLETE', updated_at: null,
      units: [
        { unit_id: 's2', section_id: 's2', order: 2, status: 'REJECTED',
          camera_artifact_id: null, continuity_artifact_id: null,
          arbitration_artifact_id: null, applied: false, shot_ids: [],
          rejections: [{ code: 'LLM_HTTP', task_id: 'task-9', state_version: 3 }] },
      ],
    } })] });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(state) });
    stub.add({ method: 'POST', pattern: new RegExp('/film-runs/task-9/reconcile$'),
               respond: () => jsonResponse({ outcome: 'UNCONFIRMABLE',
                                             resubmittable: false, state_version: 3 }) });
    const user = userEvent.setup();
    const messages: string[] = [];
    render(<FourStagePanel projectId={PROJECT}
      onMessage={(message) => { messages.push(message); }} />);
    await user.click(await screen.findByRole('button', { name: '核对并只重做这一场' }));
    await waitFor(() => expect(messages.join(' ')).toContain('无法确认服务商是否已处理这次调用'));
    expect(stub.callsOf('POST', new RegExp('/storyboard-draft$'))).toHaveLength(0);
  });

  it('交付回执给出确切的导演包 ID，并能跳到那个包', async () => {
    const receipt = {
      handoff_id: 'h1', package_id: 'pkg-exact-1234', package_revision: 2,
      plan_version: 1, episodes: 2, shots: 5, episode_basis: 'episode_script',
      lineage_sha256: 'd'.repeat(64), created_at: '2026-11-08T00:00:00Z',
      readiness: { startable: true, blockers: [] },
    };
    const state = overview({ volumes: [volume({ handoff: receipt })] });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(state) });
    const user = userEvent.setup();
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    expect(await screen.findByText(/pkg-exact-1234/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '打开这个导演包' }));
    await waitFor(() => expect(window.location.hash).toContain('pkg-exact-1234'));
  });

  it('分集合同与失效原因如实显示', async () => {
    const state = overview({ volumes: [volume({ episode_contracts: [
      { artifact_id: 'ep-1', sha256: 'e'.repeat(64), title: '第一集',
        sections: ['s1'], confirmed: true, current: false,
        reason: 'PIPELINE_EPISODE_SCRIPT_SUPERSEDED:s1' },
    ] })] });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(state) });
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    expect(await screen.findByText(/PIPELINE_EPISODE_SCRIPT_SUPERSEDED/)).toBeInTheDocument();
  });
});

  it('已结束的任务不再显示会一直动的进度条', async () => {
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(overview({
                 active_tasks: [{
                   task_id: 'task-1', role: 'four_stage_scene_script_run', state_version: 4,
                   status: 'SUCCEEDED', stage: 'DOCUMENT', stage_label: '已完成',
                   progress_done: 1, progress_total: 1, error_code: null,
                   attempt: 1, external_outcome: 'RESPONSE_RECEIVED',
                   blocked_reason: null, available_actions: [],
                   updated_at: null, consent_document_id: null,
                   consent_document_sha256: null, consent_role: 'four_stage_scene_script_run',
                 }],
               })) });
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await screen.findByText('编写场次剧本');
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.getAllByText('已完成').length).toBeGreaterThan(0);
  });
