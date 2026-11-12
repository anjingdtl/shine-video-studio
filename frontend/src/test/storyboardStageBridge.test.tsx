import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { DirectorStoryboardStage } from '../components/StoryboardDraftSection';
import { MaterialPreparationPanel } from '../components/MaterialPreparationPanel';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

const unit = (sectionId: string, applied: boolean) => ({
  unit_id: `u-${sectionId}`, section_id: sectionId, order: 1, status: 'PROPOSED',
  camera_artifact_id: `cam-${sectionId}`, continuity_artifact_id: `con-${sectionId}`,
  arbitration_artifact_id: null, applied, shot_ids: applied ? [`shot-${sectionId}`] : [],
  title: `场 ${sectionId}`, chunk_index: 1, chunk_count: 1, rejections: [],
  missing_characters: [],
});

function overview(applied: boolean) {
  return {
    master: { document_id: 'doc-1', sha256: 'a'.repeat(64) },
    volume_plan_confirmed: true, volume_plan: null, pending_volume_plan: null,
    bible_confirmed: true, pending_bible: null, bible_status: null,
    active_tasks: [], pending_scripts: [],
    volumes: [{
      volume_id: 'v1', order: 1, title: '第一卷', char_count: 100,
      outline_confirmed: true, latest_outline: null,
      scripts: [
        { section_id: 's1', scene_key: 'S1', title: '场一', confirmed: true },
        { section_id: 's2', scene_key: 'S2', title: '场二', confirmed: true },
      ],
      scripts_confirmed: 2, scripts_total: 2,
      storyboard: { status: 'PROPOSED', updated_at: null,
        units: [unit('s1', applied), unit('s2', applied)] },
      episode_contracts: [], handoff: null,
    }],
  };
}

const draftPlan = {
  id: 'pl2', project_id: 'p1', version: 2, status: 'DRAFT', sha256: 'sha2',
  target_seconds: 600, tolerance_seconds: 120, source: 'script_sections',
  payload: {
    mode: 'sections', target_seconds: 600, tolerance_seconds: 120,
    episodes: [{ episode_id: 'e1', order: 1, title: '第1集', shot_ids: [],
      section_ids: ['s1', 's2'], planned_seconds: 60, boundary_note: '' }],
  },
  created_at: '', confirmed_at: null,
};

it('③ 按场拆镜工作区列出单元，未应用完时落镜头按钮禁用', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /projects\/p1\/pipeline$/, respond: () => jsonResponse(overview(false)) },
    { method: 'GET', pattern: /projects\/p1\/episode-plans$/, respond: () => jsonResponse([draftPlan]) },
    { method: 'GET', pattern: /projects\/p1\/film-runs$/, respond: () => jsonResponse([]) },
  ]);
  render(<DirectorStoryboardStage projectId="p1" onMessage={vi.fn()} onJourneyRefresh={vi.fn()} />);
  expect(await screen.findAllByRole('button', { name: '应用这一场的镜头' })).toHaveLength(2);
  expect(screen.getAllByRole('button', { name: '重做这一场' })).toHaveLength(2);
  expect(screen.getByRole('button', { name: '把分集落到镜头' })).toBeDisabled();
  expect(document.getElementById('four-stage-storyboard')).not.toBeNull();
  expect(stub.callsOf('POST', /materialize$/)).toHaveLength(0);
  stub.restore();
});

it('全部应用后③可落镜头并确认分集，完成闭环', async () => {
  let materialized = false;
  const stub = new StudioStub([
    { method: 'GET', pattern: /projects\/p1\/pipeline$/, respond: () => jsonResponse(overview(true)) },
    { method: 'GET', pattern: /projects\/p1\/episode-plans$/, respond: () => jsonResponse([
      materialized
        ? { ...draftPlan, id: 'pl3', version: 3,
            payload: { ...draftPlan.payload, episodes: [
              { ...draftPlan.payload.episodes[0], shot_ids: ['shot-s1', 'shot-s2'] }] } }
        : draftPlan,
    ]) },
    { method: 'GET', pattern: /projects\/p1\/film-runs$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /episode-plans\/2\/materialize$/, respond: () => {
      materialized = true;
      return jsonResponse({ ...draftPlan, id: 'pl3', version: 3 });
    } },
    { method: 'POST', pattern: /episode-plans\/3\/confirm$/, respond: () => jsonResponse({ ...draftPlan, version: 3, status: 'CONFIRMED' }) },
  ]);
  const user = userEvent.setup();
  const journeyRefresh = vi.fn();
  render(<DirectorStoryboardStage projectId="p1" onMessage={vi.fn()} onJourneyRefresh={journeyRefresh} />);
  const close = await screen.findByRole('button', { name: '把分集落到镜头' });
  expect(close).toBeEnabled();
  await user.click(close);
  await waitFor(() => expect(stub.callsOf('POST', /episode-plans\/2\/materialize$/)).toHaveLength(1));
  const confirm = await screen.findByRole('button', { name: '确认这一版分集' });
  await user.click(confirm);
  await waitFor(() => expect(stub.callsOf('POST', /episode-plans\/3\/confirm$/)).toHaveLength(1));
  expect(journeyRefresh).toHaveBeenCalled();
  stub.restore();
});

it('③ 未完成时素材页主操作暂停并给出回③的入口', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /projects\/p1\/material-preparation$/, respond: () => jsonResponse({
      project_id: 'p1', stage: 'read_script', plan_status: null, document_id: 'doc-1',
      requirements: [], runs: [], blockers: [],
    }) },
    { method: 'GET', pattern: /projects\/p1\/documents$/, respond: () => jsonResponse([
      { id: 'doc-1', filename: '剧本.txt', sha256: 'a'.repeat(64) },
    ]) },
    { method: 'GET', pattern: /projects\/p1\/assets$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /material-preparation\/scenes\/status/, respond: () => jsonResponse({ scenes: [] }) },
    { method: 'GET', pattern: /projects\/p1\/pipeline$/, respond: () => jsonResponse({
      volumes: [{ scripts: [{ section_id: 's1', title: '场一' }],
        episode_contracts: [{ title: '第1集', sections: ['s1'], confirmed: true }] }],
    }) },
  ]);
  const view = render(<MaterialPreparationPanel projectId="p1" storyboardReady={false} />);
  expect(await screen.findByText(/③ 镜头拆解还没有完成/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '复用分镜分析生成素材清单' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '去③镜头拆解' })).toBeInTheDocument();
  view.rerender(<MaterialPreparationPanel projectId="p1" storyboardReady={true} />);
  await waitFor(() => expect(screen.getByRole('button', { name: '复用分镜分析生成素材清单' })).toBeEnabled());
  stub.restore();
});