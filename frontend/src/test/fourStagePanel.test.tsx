/** 四阶段面板：真实 API 合同与人工确认流。
 *  每一步的主操作接到真实路由；确认动作必须带上用户看到的 sha256。 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FourStagePanel } from '../components/FourStagePanel';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;
const PROJECT = 'proj-1';

function baseOverview(extra = {}) {
  return {
    master: { document_id: 'doc-1', sha256: 'a'.repeat(64) },
    volume_plan_confirmed: false, bible_confirmed: false,
    pending_bible: null, pending_scripts: [], volumes: [],
    ...extra,
  };
}

beforeEach(() => { stub = new StudioStub([]); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

describe('四阶段面板', () => {
  it('上传完成后立即更新母本选项并排除派生卷文档', async () => {
    let uploaded = false;
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/documents$/,
      respond: () => jsonResponse([
        { id: 'derived', filename: '派生卷.md', sha256: 'b'.repeat(64), source_format: 'volume' },
        ...(uploaded ? [{ id: 'novel', filename: '新上传的小说.txt', sha256: 'a'.repeat(64) }] : []),
      ]) });
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/pipeline$/,
      respond: () => jsonResponse(baseOverview()) });
    const onMessage = vi.fn();
    const { rerender } = render(<FourStagePanel projectId={PROJECT} documentsVersion={0} onMessage={onMessage} />);
    await screen.findByText(/请先在上方上传母本/);
    uploaded = true;
    rerender(<FourStagePanel projectId={PROJECT} documentsVersion={1} onMessage={onMessage} />);
    expect(await screen.findByRole('option', { name: '新上传的小说.txt' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: '派生卷.md' })).toBeNull();
  });

  it('导入步骤：选择母本后生成并确认分卷方案', async () => {
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([{ id: 'doc-1', filename: 'novel.txt', sha256: 'a'.repeat(64) }]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(baseOverview()) });
    stub.add({ method: 'POST', pattern: new RegExp('/pipeline/sources$'),
               respond: () => jsonResponse({ volume_plan_id: 'plan-1', sha256: 'b'.repeat(64),
                                            volumes: [{ volume_id: 'v1' }], coverage: { complete: true } }) });
    stub.add({ method: 'POST', pattern: new RegExp('/pipeline/volume-plans/plan-1/confirm$'),
               respond: () => jsonResponse({ confirmed: true }) });
    const user = userEvent.setup();
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await screen.findByText('生成分卷方案');
    const select = screen.getByLabelText('选择母本文档');
    await user.selectOptions(select, 'doc-1');
    await user.click(screen.getByRole('button', { name: '生成分卷方案' }));
    expect(await screen.findByText(/分卷方案已生成（1 卷）/, {}, { timeout: 3000 })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '确认分卷方案' }));
    expect(stub.callsOf('POST', new RegExp('volume-plans/plan-1/confirm$'))).toHaveLength(1);
  });

  it('剧本步骤：确认按钮携带用户看到的 sha256', async () => {
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/(documents|pipeline)$'),
               respond: () => jsonResponse(baseOverview({
                 volume_plan_confirmed: true, bible_confirmed: true,
                 volumes: [{ volume_id: 'v1', order: 1, title: '第1卷', char_count: 100,
                             outline_confirmed: true,
                             scripts: [{ section_id: 's1', scene_key: 'S1', title: '出海', confirmed: false }],
                             scripts_confirmed: 0, scripts_total: 1 }],
                 pending_scripts: [{ volume_id: 'v1', section_id: 's1', artifact_id: 'art-9',
                                     sha256: 'c'.repeat(64), scene_key: 'S1', title: '出海', text: '林岚：你来了。' }],
               })) });
    stub.add({ method: 'POST', pattern: new RegExp('/pipeline/scene-scripts/art-9/confirm$'),
               respond: () => jsonResponse({ confirmed: true }) });
    const user = userEvent.setup();
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await screen.findByText(/场次 S1 的剧本已生成/);
    await user.click(screen.getByRole('button', { name: '确认这一场' }));
    await waitForConfirmation(stub);
  });

  it('交付按钮交付的是当前选中的卷，不是第一卷', async () => {
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/(documents|pipeline)$'),
               respond: () => jsonResponse(baseOverview({
                 volume_plan_confirmed: true, bible_confirmed: true,
                 volumes: [
                   { volume_id: 'v1', order: 1, title: '第1卷', char_count: 100,
                     outline_confirmed: true, scripts: [], scripts_confirmed: 0, scripts_total: 0 },
                   { volume_id: 'v2', order: 2, title: '第2卷', char_count: 100,
                     outline_confirmed: true, scripts: [], scripts_confirmed: 0, scripts_total: 0 },
                 ],
               })) });
    stub.add({ method: 'POST', pattern: new RegExp('/pipeline/volumes/v2/handoff$'),
               respond: () => jsonResponse({
                 handoff_id: 'h2', plan_version: 2, package_revision: 2, package_id: 'pkg-2',
                 reused: false, episodes: 2, shots: 5,
                 readiness: { ready: true, startable: true, blockers: [] } }) });
    const user = userEvent.setup();
    render(<FourStagePanel projectId={PROJECT}
      workflowProfiles={[{ id: 'wf-1', name: '方案甲', mode: 'Ref2VA' } as never]}
      onMessage={vi.fn()} />);
    await screen.findByLabelText('当前处理的卷：');
    await user.selectOptions(screen.getByLabelText('当前处理的卷：'), 'v2');
    await user.selectOptions(screen.getByLabelText('选择生成方案'), 'wf-1');
    await user.click(screen.getByRole('button', { name: '交付本卷制作' }));
    await new Promise((r) => setTimeout(r, 60));
    expect(stub.callsOf('POST', new RegExp('/pipeline/volumes/v2/handoff$'))).toHaveLength(1);
    expect(stub.callsOf('POST', new RegExp('/pipeline/volumes/v1/handoff$'))).toHaveLength(0);
  });

  it('终校硬阻断时用中文说明并保留编辑出口，确认按钮不可点', async () => {
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/(documents|pipeline)$'),
               respond: () => jsonResponse(baseOverview({
                 volume_plan_confirmed: true, bible_confirmed: true,
                 volumes: [{ volume_id: 'v1', order: 1, title: '第1卷', char_count: 100,
                             outline_confirmed: true,
                             scripts: [{ section_id: 's1', scene_key: 'S1', title: '出海',
                                         confirmed: false,
                                         qa: { state: 'HARD_BLOCK', level: 'L0',
                                               codes: ['SCENE_SOURCE_RANGE_INVALID'],
                                               message: '这一场引用的原文范围无效，不能确认。',
                                               artifact_id: 'qa-1', script_sha256: 'd'.repeat(64) } }],
                             scripts_confirmed: 0, scripts_total: 1 }],
                 pending_scripts: [{ volume_id: 'v1', section_id: 's1', artifact_id: 'art-9',
                                     sha256: 'c'.repeat(64), scene_key: 'S1', title: '出海',
                                     text: '林岚：你来了。',
                                     qa: { state: 'HARD_BLOCK', level: 'L0',
                                           codes: ['SCENE_SOURCE_RANGE_INVALID'],
                                           message: '这一场引用的原文范围无效，不能确认。',
                                           artifact_id: 'qa-1', script_sha256: 'd'.repeat(64) } }],
               })) });
    const user = userEvent.setup();
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await screen.findByText(/场次 S1 的剧本已生成/);
    // The reason is readable, the raw check code is not the message.
    expect(screen.getAllByText(/未通过终校：这一场引用的原文范围无效/).length).toBeGreaterThan(0);
    const confirm = screen.getByRole('button', { name: '确认这一场' });
    expect(confirm).toBeDisabled();
    // The two real exits remain: read it, or edit it.
    expect(screen.getByRole('button', { name: '编辑' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: '编辑' }));
    expect(screen.getByLabelText('编辑剧本')).toHaveValue('林岚：你来了。');
  });

  it('未知发送结果只提供核对与显式新尝试，不提供普通继续', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/documents$'),
               respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: new RegExp('/projects/proj-1/pipeline$'),
               respond: () => jsonResponse(baseOverview({
                 active_tasks: [{
                   task_id: 'task-1', role: 'four_stage_scene_script_run', state_version: 4,
                   status: 'NEEDS_REVIEW', stage: 'NEEDS_REVIEW', stage_label: '待你核对',
                   progress_done: 0, progress_total: 0, error_code: 'LLM_HTTP',
                   attempt: 1, external_outcome: 'UNKNOWN',
                   blocked_reason: 'LLM_HTTP', available_actions: ['reconcile', 'new_attempt'],
                   updated_at: null, consent_document_id: null,
                   consent_document_sha256: null, consent_role: 'four_stage_scene_script_run',
                 }],
               })) });
    stub.add({ method: 'POST', pattern: new RegExp('/pipeline/tasks/task-1/new-attempt$'),
               respond: () => jsonResponse({ task_id: 'task-2', attempt: 2, status: 'QUEUED',
                                             supersedes: 'task-1' }) });
    const user = userEvent.setup();
    render(<FourStagePanel projectId={PROJECT} onMessage={vi.fn()} />);
    await screen.findByText(/待核对：LLM_HTTP/);
    expect(screen.queryByRole('button', { name: '修复后继续' })).toBeNull();
    await user.click(screen.getByRole('button', { name: '核对后重新发送一次' }));
    await new Promise((r) => setTimeout(r, 60));
    expect(stub.callsOf('POST', new RegExp('/tasks/task-1/new-attempt$'))).toHaveLength(1);
  });
});

async function waitForConfirmation(stub: StudioStub) {
  await new Promise((r) => setTimeout(r, 50));
  expect(stub.callsOf('POST', new RegExp('scene-scripts/art-9/confirm$'))).toHaveLength(1);
  const call = stub.callsOf('POST', new RegExp('scene-scripts/art-9/confirm$'))[0];
  expect((call?.body as { expected_sha256?: string })?.expected_sha256).toBe('c'.repeat(64));
}
