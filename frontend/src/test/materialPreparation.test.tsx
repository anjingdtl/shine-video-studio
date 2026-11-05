import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { MaterialPreparationPanel } from '../components/MaterialPreparationPanel';
import { StudioStub, jsonResponse, type StubResponse } from './studioStub';

let stub: StudioStub;

const STATE = {
  project_id: 'p1',
  stage: 'confirm_requirements',
  plan_revision_id: 'plan-1',
  plan_status: 'CONFIRMED',
  document_id: 'doc-1',
  visual_direction: null,
  confirmation: { confirmation_id: 'c'.repeat(64), confirmation_hash: 'd'.repeat(64) },
  expected_snapshot: 'a'.repeat(64),
  requirements: [
    {
      requirement_id: 'req-1',
      family: 'character',
      entity_id: '林知远',
      strategy: 'generate',
      status: 'CONFIRMED',
      bound_asset_id: null,
      brief_note: '',
      description: '三十岁上下，长风衣。',
    },
  ],
  runs: [
    {
      run_id: 'run-1',
      status: 'RUNNING',
      kind: 'production',
      state_version: 3,
      counts: { total: 1, candidate_ready: 0, failed: 0, needs_review: 0, queued: 1, running: 0 },
      items: [
        {
          item_id: 'item-1',
          requirement_id: 'req-1',
          family: 'character',
          entity_id: '林知远',
          generation_revision: 1,
          status: 'CANDIDATE_READY',
          error_code: null,
          candidates: [
            {
              candidate_id: 'cand-1',
              status: 'PENDING_HUMAN_APPROVAL',
              content_sha256: 'b'.repeat(64),
              is_mock: false,
              formal_asset_id: null,
              attempt_no: 1,
            },
          ],
        },
      ],
      allowed_actions: ['cancel'],
    },
  ],
  blockers: [],
};

function routes() {
  return [
    { method: 'GET', pattern: /projects\/p1\/material-preparation$/, respond: () => jsonResponse(STATE) },
    { method: 'GET', pattern: /projects\/p1\/documents$/, respond: () => jsonResponse([{ id: 'doc-1', filename: '剧本.txt', sha256: 'a'.repeat(64) }]) },
    { method: 'GET', pattern: /projects\/p2\/material-preparation$/, respond: () => jsonResponse({ ...STATE, project_id: 'p2', requirements: [], runs: [] }) },
    { method: 'GET', pattern: /projects\/p2\/documents$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /material-preparation\/scenes\/prepare$/, respond: () => jsonResponse({ preparations: [], scenes: [{ scene_key: 'S1', status: 'PREPARED' }] }) },
    { method: 'POST', pattern: /material-preparation\/analyze$/, respond: () => jsonResponse({ plan_revision_id: 'plan-1', requirements: STATE.requirements, expected_snapshot: STATE.expected_snapshot }) },
    { method: 'POST', pattern: /material-preparation\/confirm$/, respond: () => jsonResponse({ confirmation_id: 'c'.repeat(64), confirmation_hash: 'd'.repeat(64) }) },
    { method: 'POST', pattern: /material-preparation\/generate$/, respond: () => jsonResponse({ run_id: 'run-1' }) },
    { method: 'POST', pattern: /material-preparation\/runs\/run-1\/cancel$/, respond: () => jsonResponse({ status: 'CANCEL_REQUESTED' }) },
    {
      method: 'POST',
      pattern: /material-preparation\/complete$/,
      respond: () => jsonResponse({
        materials_ready: false,
        requirements: [{
          requirement_id: 'req-1', family: 'character', entity_id: '林知远',
          asset_id: null, status: 'MISSING', reason: 'ASSET_OR_VOICE_MISSING',
        }],
        missing: [{
          requirement_id: 'req-1', family: 'character', entity_id: '林知远',
          asset_id: null, status: 'MISSING', reason: 'ASSET_OR_VOICE_MISSING',
        }],
        reason: 'MATERIAL_REQUIREMENT_UNBOUND',
      }),
    },
    { method: 'POST', pattern: /preparation\/rights$/, respond: () => jsonResponse({ id: 'rights-1' }) },
    { method: 'POST', pattern: /preparation\/rights\/rights-1\/approve$/, respond: () => jsonResponse({ id: 'rights-1' }) },
    { method: 'POST', pattern: /preparation\/approval-grants$/, respond: () => jsonResponse({ grant_id: 'g1', snapshot: 's1' }) },
    { method: 'POST', pattern: /candidate-assets\/cand-1\/promote$/, respond: () => jsonResponse({ detail: { code: 'APPROVAL_REQUIRED_OR_REPLAYED' } }, 409) },
  ];
}

beforeEach(() => { localStorage.clear(); stub = new StudioStub(routes()); });
afterEach(() => { stub.restore(); localStorage.clear(); });

it('素材页读取服务器的需求清单，刷新不会重新提交分析', async () => {
  render(<MaterialPreparationPanel projectId="p1" />);
  expect((await screen.findAllByText(/林知远/)).length).toBeGreaterThan(0);
  expect(stub.callsOf('POST', /material-preparation\/analyze$/)).toHaveLength(0);
  expect(stub.callsOf('GET', /material-preparation$/)).toHaveLength(1);
});

it('切换作品不会带出上一个作品的需求', async () => {
  const view = render(<MaterialPreparationPanel projectId="p1" />);
  expect((await screen.findAllByText(/林知远/)).length).toBeGreaterThan(0);
  view.rerender(<MaterialPreparationPanel projectId="p2" />);
  await waitFor(() => expect(screen.queryByText('林知远')).toBeNull());
  expect(stub.callsOf('GET', /projects\/p2\/material-preparation$/)).toHaveLength(1);
});

it('服务器拒绝时给出可读原因，而不是只显示错误码', async () => {
  stub.add({
    method: 'POST',
    pattern: /material-preparation\/scenes\/prepare$/,
    respond: () => jsonResponse({ detail: { code: 'LLM_NOT_CONFIGURED' } }, 409),
  });
  const user = userEvent.setup();
  render(<MaterialPreparationPanel projectId="p1" />);
  await screen.findAllByText(/林知远/);
  await user.selectOptions(await screen.findByLabelText('剧本文件'), 'doc-1');
  await user.click(screen.getByRole('button', { name: /分析剧本/ }));
  expect(await screen.findByText(/还没有给这个角色配置模型服务/)).toBeTruthy();
});

it('停止批次带上服务端给的状态版本', async () => {
  const user = userEvent.setup();
  render(<MaterialPreparationPanel projectId="p1" />);
  await screen.findAllByText(/林知远/);
  await user.click(screen.getByRole('button', { name: '停止这个批次' }));
  await waitFor(() => expect(stub.callsOf('POST', /runs\/run-1\/cancel$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /runs\/run-1\/cancel$/)[0].body).toMatchObject({ expected_state_version: 3 });
});

it('批量晋升逐项显示结果，不会整体宣称成功', async () => {
  const user = userEvent.setup();
  render(<MaterialPreparationPanel projectId="p1" />);
  await screen.findAllByText(/林知远/);
  await user.click(screen.getByRole('button', { name: '确认使用所选素材' }));
  const report = await screen.findByText(/APPROVAL_REQUIRED_OR_REPLAYED/);
  expect(report).toBeTruthy();
  // The failure is reported per item; the batch is not announced as done.
  expect(screen.queryByText(/全部已晋升/)).toBeNull();
});

it('素材是否齐备由服务端判定，界面只如实显示未绑定项', async () => {
  const user = userEvent.setup();
  render(<MaterialPreparationPanel projectId="p1" />);
  await screen.findAllByText(/林知远/);
  await user.click(screen.getByRole('button', { name: '确认使用所选素材' }));
  expect(await screen.findByText(/还没有绑定有效的主素材/)).toBeTruthy();
  expect(screen.queryByText(/素材齐备/)).toBeNull();
  expect(stub.callsOf('POST', /material-preparation\/complete$/)).toHaveLength(1);
});

it('切换作品后，旧作品的在途响应不会写进新作品', async () => {
  let release: (value: StubResponse) => void = () => undefined;
  const gate = new Promise<StubResponse>((resolve) => { release = resolve; });
  stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation$/, respond: () => gate });
  const view = render(<MaterialPreparationPanel projectId="p1" />);
  view.rerender(<MaterialPreparationPanel projectId="p2" />);
  release(jsonResponse(STATE));
  await waitFor(() => expect(screen.queryAllByText(/林知远/)).toHaveLength(0));
  expect(stub.callsOf('GET', /projects\/p2\/material-preparation$/)).toHaveLength(1);
});

it('确认清单与排队出图分别显示结果，不合并成一句成功', async () => {
  const user = userEvent.setup();
  render(<MaterialPreparationPanel projectId="p1" />);
  await screen.findAllByText(/林知远/);
  const button = screen.getByRole('button', { name: '确认清单并补齐' });
  expect(button).toBeTruthy();
  await user.click(button);
  expect(await screen.findByText(/清单已确认（1 项）/)).toBeTruthy();
  expect(await screen.findByText(/已排队 1 项出图/)).toBeTruthy();
  expect(stub.callsOf('POST', /material-preparation\/confirm$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /material-preparation\/generate$/)).toHaveLength(1);
});

it('重新挂载只重新查询任务，不会重新提交', async () => {
  const first = render(<MaterialPreparationPanel projectId="p1" />);
  expect((await screen.findAllByText(/林知远/)).length).toBeGreaterThan(0);
  first.unmount();
  render(<MaterialPreparationPanel projectId="p1" />);
  expect((await screen.findAllByText(/林知远/)).length).toBeGreaterThan(0);
  expect(stub.callsOf('POST', /material-preparation/)).toHaveLength(0);
  expect(stub.callsOf('GET', /material-preparation$/).length).toBeGreaterThanOrEqual(2);
});

const FROZEN_STYLE = {
  id: 'style-1',
  project_id: 'p1',
  version: 3,
  sha256: 's'.repeat(64),
  snapshot: {
    photography_language: '平视中近景',
    shot_blocking: '对话保持视线轴线',
    color_palette: '暖色调',
    lighting_tone: '顶光',
    lens_texture: '干净',
    editing_rhythm: '对白停顿',
    sound_performance: '普通话自然语速',
  },
  approved_by: 'local-user',
  approval_note: '',
  frozen_at: '2026-11-04T10:00:00',
};

it('冻结风格与素材视觉方向并排展示，是否一致由用户判断', async () => {
  stub.add({
    method: 'GET',
    pattern: /material-preparation$/,
    respond: () => jsonResponse({
      ...STATE,
      visual_direction: { era: '1930年代', palette: '青灰', lighting: '单侧主光', texture: '颗粒', notes: '' },
    }),
  });
  render(<MaterialPreparationPanel projectId="p1" frozenStyle={FROZEN_STYLE} />);
  expect(await screen.findByText(/影视风格已冻结（版本 3）/)).toBeTruthy();
  expect(screen.getByText(/色调：素材方向「青灰」／已冻结风格「暖色调」/)).toBeTruthy();
  expect(screen.getByText(/请在下方「影视风格」新建一个版本/)).toBeTruthy();
});
