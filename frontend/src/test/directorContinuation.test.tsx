import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import GenerationSegmentsPanel from '../components/GenerationSegmentsPanel';
import { StoryPreparationPanel } from '../components/StoryPreparationPanel';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;
afterEach(() => stub.restore());

it('三段网格只为明确点击的第二段请求重拍，播放器按需加载', async () => {
  const segments = [1, 2, 3].map((n) => ({ shot_id: `shot-${n}`, position: n, story_beat: `剧情${n}`,
    duration_s: 5, shot_revision: 1, job_id: `job-${n}`, attempt_no: 1, status: 'PASSED', artifact_id: `a-${n}`,
    blocker: null, can_regenerate: true, dependent_job_ids: [], replacement_job_id: null,
    replacement_status: null, can_select_replacement: false, editorial_cuts: [] }));
  stub = new StudioStub([
    { method: 'GET', pattern: /generation-segments$/, respond: () => jsonResponse({ series_id: 'series-1', segments }) },
    { method: 'POST', pattern: /shot-2\/regenerate$/, respond: () => jsonResponse({ job_id: 'job-2-new' }, 202) },
  ]);
  const user = userEvent.setup();
  const view = render(<GenerationSegmentsPanel projectId="p1" fingerprint="a" />);
  await screen.findByText('生成段 2 · 5 秒');
  expect(view.container.querySelectorAll('video')).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: '预览 生成段 2 当前版本' }));
  expect(view.container.querySelector('video')).toHaveAttribute('src', '/api/v1/jobs/job-2/media');
  await user.click(screen.getAllByRole('button', { name: '重新生成此段' })[1]);
  await waitFor(() => expect(stub.callsOf('POST', /regenerate$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /regenerate$/)[0].body).toMatchObject({ source_job_id: 'job-2', expected_shot_revision: 1 });
  expect(stub.callsOf('POST', /shot-[13]\/regenerate$/)).toHaveLength(0);
});

it('已确认大纲仍不默认全选，只为勾选的生产段落排正式分镜', async () => {
  const outline = { artifact_id: 'outline-1', sha256: 'a'.repeat(64), approved: true, stale: false,
    payload: { summary: '主线', story_line: ['开端'], timeline: [], relationships: [], locked_facts: [], characters: [], questions: [],
      production_sections: [1, 2].map((n) => ({ production_section_id: `section-${n}`, order: n, title: `章节${n}`,
        start_offset: (n - 1) * 100, end_offset: n * 100, estimated_duration_s: 20 })) } };
  stub = new StudioStub([
    { method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([
      { task_id: 'task-1', role: 'story_outline_run', status: 'SUCCEEDED', state_version: 4,
        progress: { stage: 'AWAITING_OUTLINE_CONFIRMATION', cursor: 2 }, error_code: null, outline },
    ]) },
    { method: 'GET', pattern: /outlines\/outline-1\/continuity$/, respond: () => jsonResponse({ outline_artifact_id: 'outline-1',
      outline_sha256: 'a'.repeat(64), sections: [
        { production_section_id: 'section-1', order: 1, prior_production_section_id: null,
          verified_prior_available: false, prior_production_status: 'NOT_PRODUCED', own_production: { status: 'NOT_PRODUCED', missing: [] } },
        { production_section_id: 'section-2', order: 2, prior_production_section_id: 'section-1',
          verified_prior_available: false, prior_production_status: 'PARTIAL', own_production: { status: 'NOT_PRODUCED', missing: [] } },
      ] }) },
    { method: 'POST', pattern: /story-preparation\/sections$/, respond: () => jsonResponse({ task_id: 'work-1', status: 'QUEUED' }, 202) },
  ]);
  const user = userEvent.setup();
  render(<StoryPreparationPanel projectId="p1" documentId="doc-1" documentHash={'b'.repeat(64)} styleFrozen onMaterials={() => undefined} />);
  await screen.findByText('主线');
  const button = screen.getByRole('button', { name: '为所选生产段落生成正式分镜' });
  expect(button).toBeDisabled();
  expect(stub.callsOf('POST', /outlines$/)).toHaveLength(0);
  await user.click(screen.getByRole('checkbox', { name: '1. 章节1' }));
  await user.click(button);
  await waitFor(() => expect(stub.callsOf('POST', /sections$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /sections$/)[0].body).toMatchObject({ production_section_ids: ['section-1'], outline_artifact_id: 'outline-1' });
});

it('上一段末状态已验证时，后段免填承接事实即可排正式分镜', async () => {
  const outline = { artifact_id: 'outline-1', sha256: 'a'.repeat(64), approved: true, stale: false,
    payload: { summary: '主线', story_line: ['开端'], timeline: [], relationships: [], locked_facts: [], characters: [], questions: [],
      production_sections: [1, 2].map((n) => ({ production_section_id: `section-${n}`, order: n, title: `章节${n}`,
        start_offset: (n - 1) * 100, end_offset: n * 100, estimated_duration_s: 20 })) } };
  stub = new StudioStub([
    { method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([
      { task_id: 'task-1', role: 'story_outline_run', status: 'SUCCEEDED', state_version: 4,
        progress: { stage: 'AWAITING_OUTLINE_CONFIRMATION', cursor: 2 }, error_code: null, outline },
    ]) },
    { method: 'GET', pattern: /outlines\/outline-1\/continuity$/, respond: () => jsonResponse({ outline_artifact_id: 'outline-1',
      outline_sha256: 'a'.repeat(64), sections: [
        { production_section_id: 'section-1', order: 1, prior_production_section_id: null,
          verified_prior_available: false, prior_production_status: 'NOT_PRODUCED', own_production: { status: 'VERIFIED', missing: [] } },
        { production_section_id: 'section-2', order: 2, prior_production_section_id: 'section-1',
          verified_prior_available: true, prior_production_status: 'VERIFIED', own_production: { status: 'NOT_PRODUCED', missing: [] } },
      ] }) },
    { method: 'POST', pattern: /story-preparation\/sections$/, respond: () => jsonResponse({ task_id: 'work-1', status: 'QUEUED' }, 202) },
  ]);
  const user = userEvent.setup();
  render(<StoryPreparationPanel projectId="p1" documentId="doc-1" documentHash={'b'.repeat(64)} styleFrozen onMaterials={() => undefined} />);
  await screen.findByText('上一段末状态已验证（来自已通过的成片），本段无需填写承接事实。');
  expect(screen.getByText('本段成片已全部通过，可作为后段的验证承接。')).toBeTruthy();
  const button = screen.getByRole('button', { name: '为所选生产段落生成正式分镜' });
  await user.click(screen.getByRole('checkbox', { name: '2. 章节2' }));
  await user.click(button);
  await waitFor(() => expect(stub.callsOf('POST', /sections$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /sections$/)[0].body).toMatchObject({
    production_section_ids: ['section-2'],
  });
  expect(JSON.stringify(stub.callsOf('POST', /sections$/)[0].body)).toContain('"continuity_facts":{}');
});
