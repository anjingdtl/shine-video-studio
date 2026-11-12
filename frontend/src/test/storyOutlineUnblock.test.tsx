/** 大纲任务被卡住时，界面必须说清“为什么卡住、现在能做什么”。
 *  已判定（服务商答复但没有可用结果）的子任务可以恢复；结果未知的仍然
 *  不能恢复，也不会自动重发。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { StoryPreparationPanel } from '../components/StoryPreparationPanel';
import { OutlineConfirmationPanel } from '../components/OutlineConfirmationPanel';
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

function outlineTask(blocked: unknown[], reconciliation?: unknown) {
  return {
    task_id: 'task-1', role: 'story_outline_run', status: 'NEEDS_REVIEW', state_version: 5,
    error_code: 'STORY_EXTRACTION_NEEDS_REVIEW', outline: null,
    summary: { stage: 'EXTRACT', label: '逐场提取剧情要点', done: 0, total: 6, percent: 0,
      lease_expires_at: null, awaiting_reclaim: false, lease_remaining_s: null },
    progress: { stage: 'EXTRACT', cursor: 0, blocked_subtasks: blocked,
      ...(reconciliation ? { reconciliation } : {}) },
  };
}

function renderPanel() {
  return render(<StoryPreparationPanel projectId="p1" documentId="doc-1"
    documentHash={'b'.repeat(64)} styleFrozen onMaterials={() => undefined} />);
}

describe('大纲任务卡在提取剧情要点', () => {
  it('在大纲进度旁显示错误，并在确认未知结果后只提交一次重试', async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const retry = { failure: { code: 'LLM_TIMEOUT', scene_key: 'S1', outcome: 'UNKNOWN' },
      can_retry: true, unknown_subtask_id: 'sub-1', exhausted: false };
    let queued = false;
    stub.add({ method: 'GET', pattern: /text-consent\/doc-1$/, respond: () => jsonResponse({
      roles: [{ role: 'scriptwriter', configured: true, allowed: true }],
    }) });
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([{
      ...outlineTask([]), status: queued ? 'QUEUED' : 'NEEDS_REVIEW',
      retry: { ...retry, can_retry: !queued },
    }]) });
    stub.add({ method: 'POST', pattern: /tasks\/task-1\/retry$/, respond: () => {
      queued = true;
      return jsonResponse({ status: 'QUEUED' });
    } });
    render(<OutlineConfirmationPanel projectId="p1" documentId="doc-1" documentHash={'b'.repeat(64)} />);
    expect(await screen.findByRole('progressbar', { name: '大纲任务进度' })).toBeInTheDocument();
    expect(screen.getByText('LLM_TIMEOUT')).toBeInTheDocument();
    expect(screen.getByText(/远端模型响应超时/)).toBeInTheDocument();
    const button = screen.getByRole('button', { name: '重试大纲任务' });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    expect(stub.callsOf('POST', /\/retry$/)).toHaveLength(0);
    confirm.mockReturnValue(true);
    await user.dblClick(button);
    expect(stub.callsOf('POST', /\/retry$/)).toHaveLength(1);
    expect(stub.callsOf('POST', /\/retry$/)[0].body).toMatchObject({
      expected_state_version: 5, subtask_id: 'sub-1',
    });
    expect(await screen.findByText(/排队中/)).toBeInTheDocument();
    expect(screen.getByText('LLM_TIMEOUT')).toBeInTheDocument();
  });

  it('服务商已答复但没有可用结果时允许恢复，并显示阻塞原因', async () => {
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([
      outlineTask([{ scene_key: 'S2', status: 'REJECTED', error_code: 'SCHEMA_VALIDATION_FAILED',
        reconciliation: 'RESPONSE_RECEIVED_NO_ANSWER' }],
      { checked_at: '2026-11-07T10:00:00', outcomes: [{ subtask_id: 'sub-1',
        outcome: 'RESPONSE_RECEIVED_NO_ANSWER' }] }),
    ]) });
    renderPanel();
    expect(await screen.findByText(/阻塞的子任务：S2 SCHEMA_VALIDATION_FAILED/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复未完成工作' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '核对已有结果' })).toBeEnabled();
    expect(screen.getByText(/服务商已经答复但没有可用结果，可以重新尝试一次/)).toBeInTheDocument();
  });

  it('结果未知时仍然不能恢复，并说明合法的下一步', async () => {
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([
      outlineTask([{ scene_key: 'S2', status: 'NEEDS_REVIEW', error_code: 'LLM_TIMEOUT',
        reconciliation: 'UNCONFIRMABLE' }],
      { checked_at: '2026-11-07T10:00:00', outcomes: [{ subtask_id: 'sub-1',
        outcome: 'UNCONFIRMABLE' }] }),
    ]) });
    renderPanel();
    expect(await screen.findByText(/阻塞的子任务：S2 LLM_TIMEOUT/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复未完成工作' })).toBeDisabled();
    expect(screen.getByText(/请修改剧本后重新导入并重新提取，或取消本任务/)).toBeInTheDocument();
    expect(screen.getByText(/核对结果：结果未知，系统不会用同一请求重发/)).toBeInTheDocument();
  });

  it('已取消的大纲仍可核对原任务，释放可证明未发出的请求', async () => {
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([
      { ...outlineTask([{ scene_key: 'S3', status: 'RUNNING', error_code: null,
        external_outcome: 'UNKNOWN', dead_worker: true, outcome_unknown: true }]),
        task_id: 'task-cancelled', status: 'CANCELLED', state_version: 9 },
    ]) });
    renderPanel();
    expect(await screen.findByText(/本任务已取消：仍可核对原任务/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '核对已有结果' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '恢复未完成工作' })).toBeNull();
    expect(screen.getByText(/上一次推进被中断（Worker 已退出）/)).toBeInTheDocument();
  });

  it('结果未知的子任务可以由用户明确决定重发一次', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([
      outlineTask([{ scene_key: 'S3', status: 'NEEDS_REVIEW', error_code: 'LLM_TIMEOUT',
        external_outcome: 'UNKNOWN', outcome_unknown: true, task_id: 'sub-1', reconciliation: 'UNCONFIRMABLE' }]),
    ]) });
    stub.add({ method: 'POST', pattern: /story-preparation\/tasks\/task-1\/resend-unknown$/,
      respond: () => jsonResponse({ status: 'NEEDS_REVIEW', state_version: 6, decision: { resend_allowed: true } }) });
    renderPanel();
    await user.click(await screen.findByRole('button', { name: '明确重发这一场' }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /resend-unknown$/)).toHaveLength(1);
    });
    expect(stub.callsOf('POST', /resend-unknown$/)[0].body).toMatchObject({ subtask_id: 'sub-1' });
  });

  it('重试到上限时同样不能恢复', async () => {
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([
      outlineTask([{ scene_key: 'S2', status: 'REJECTED', error_code: 'SCHEMA_VALIDATION_FAILED',
        reconciliation: 'RETRIES_EXHAUSTED' }],
      { checked_at: '2026-11-07T10:00:00', outcomes: [{ subtask_id: 'sub-1',
        outcome: 'RETRIES_EXHAUSTED' }] }),
    ]) });
    renderPanel();
    expect(await screen.findByText(/已经重试到上限，需要修改输入或取消/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复未完成工作' })).toBeDisabled();
  });
});

describe('已确认大纲的修订路径', () => {
  it('补充锁定事实的输入与重新提取入口在已确认后仍然存在', async () => {
    stub.add({ method: 'GET', pattern: /text-consent\/doc-1$/, respond: () => jsonResponse({
      roles: [{ role: 'scriptwriter', configured: true, allowed: true }],
    }) });
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([{
      task_id: 'task-1', role: 'story_outline_run', status: 'SUCCEEDED', state_version: 5,
      error_code: null,
      summary: { label: '归并大纲', done: 6, total: 6, percent: 100 },
      progress: { stage: 'AWAITING_OUTLINE_CONFIRMATION', cursor: 6 },
      outline: { artifact_id: 'o1', sha256: 'e'.repeat(64), approved: true, stale: false,
        questions_sha256: 'q'.repeat(64),
        payload: { summary: '故事摘要', story_line: [], timeline: [], relationships: [],
          locked_facts: [], characters: [], questions: ['悬念一？'], production_sections: [] } },
    }]) });
    render(<StageRailProvider stageId="script">
      <OutlineConfirmationPanel projectId="p1" documentId="doc-1" documentHash={'b'.repeat(64)} />
    </StageRailProvider>);
    expect(await screen.findByLabelText(/补充锁定事实/)).toBeInTheDocument();
    const button = await screen.findByRole('button', { name: '补充锁定事实后重新提取大纲' });
    await waitFor(() => expect(button).toBeEnabled());
  });
});
