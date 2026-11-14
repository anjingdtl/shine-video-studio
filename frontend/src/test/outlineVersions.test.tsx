/** 大纲版本管理：确认一次后保存修改为新版本，界面必须立刻显示新草稿、
 *  确认入口与版本历史；相同输入重新提取时如实报告复用，不谎报排队。 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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

const SHA1 = 'e'.repeat(64);
const SHA2 = 'f'.repeat(64);
const MD1 = '# 总体摘要\n\n旧正文。\n';
const MD2 = '# 总体摘要\n\n旧正文。改一笔。\n';

function version(id: string, sha: string, over: Record<string, unknown> = {}) {
  return { artifact_id: id, sha256: sha, created_at: '2026-11-14T01:00:00',
    approved: false, stale: false, user_edited: false, revision_of: null, confirmed: false, ...over };
}

function succeededTask() {
  return {
    task_id: 'task-1', role: 'story_outline_run', status: 'SUCCEEDED', state_version: 5,
    error_code: null,
    summary: { label: '归并大纲', done: 6, total: 6, percent: 100 },
    progress: { stage: 'AWAITING_OUTLINE_CONFIRMATION', cursor: 6 },
    outline: { artifact_id: 'o1', sha256: SHA1, approved: true, stale: false,
      questions_sha256: null,
      payload: { summary: '故事摘要', story_line: [], timeline: [], relationships: [],
        locked_facts: [], characters: [], questions: [], production_sections: [] } },
  };
}

function stubConsentAndTasks() {
  stub.add({ method: 'GET', pattern: /text-consent\/doc-1$/, respond: () => jsonResponse({
    roles: [{ role: 'scriptwriter', configured: true, allowed: true }],
  }) });
  stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/,
    respond: () => jsonResponse([succeededTask()]) });
}

describe('大纲版本管理', () => {
  it('保存新版本后立即显示待确认草稿、确认入口与版本历史', async () => {
    const user = userEvent.setup();
    stubConsentAndTasks();
    let revised = false;
    let confirmed2 = false;
    stub.add({ method: 'GET', pattern: /director\/outline(\?.*)?$/, respond: (url) => {
      if (url.includes('artifact_id=o1')) {
        return jsonResponse({ artifact_id: 'o1', sha256: SHA1, markdown: MD1, questions: [],
          approved: true, confirmed: true, confirmed_artifact_id: 'o1', user_edited: false,
          versions: [version('o1', SHA1, { approved: true, confirmed: true })] });
      }
      return revised
        ? jsonResponse({ artifact_id: 'o2', sha256: SHA2, markdown: MD2, questions: [],
          approved: confirmed2, confirmed: confirmed2,
          confirmed_artifact_id: confirmed2 ? 'o2' : 'o1', user_edited: true,
          versions: [version('o2', SHA2, { approved: confirmed2, confirmed: confirmed2,
            user_edited: true, revision_of: 'o1' }),
          version('o1', SHA1, { approved: true, confirmed: !confirmed2 })] })
        : jsonResponse({ artifact_id: 'o1', sha256: SHA1, markdown: MD1, questions: [],
          approved: true, confirmed: true, confirmed_artifact_id: 'o1', user_edited: false,
          versions: [version('o1', SHA1, { approved: true, confirmed: true })] });
    } });
    stub.add({ method: 'POST', pattern: /director\/outline\/revision$/, respond: () => {
      revised = true;
      return jsonResponse({ artifact_id: 'o2', sha256: SHA2, markdown: MD2, questions: [] }, 201);
    } });
    stub.add({ method: 'POST', pattern: /director\/outline\/confirm$/, respond: () => {
      confirmed2 = true;
      return jsonResponse({ artifact_id: 'o2', approved: true });
    } });

    render(<StageRailProvider stageId="script">
      <OutlineConfirmationPanel projectId="p1" documentId="doc-1" documentHash={'b'.repeat(64)} />
    </StageRailProvider>);

    expect(await screen.findByText(/大纲版本 o1 · 你已确认/)).toBeInTheDocument();
    const editor = screen.getByLabelText('大纲正文');
    await user.type(editor, '改一笔。');
    await user.click(screen.getByRole('button', { name: '保存修改为新版本' }));

    expect(await screen.findByText(/大纲版本 o2 · 待你审阅/)).toBeInTheDocument();
    expect(screen.getByText(/当前确认依据是版本 o1/)).toBeInTheDocument();
    const confirm = await screen.findByRole('button', { name: '确认这版总体大纲' });
    await waitFor(() => expect(confirm).toBeEnabled());

    await user.click(screen.getByText(/大纲版本历史（2）/));
    const list = screen.getByRole('list');
    const rows = within(list).getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('v1 · o1')).toBeInTheDocument();
    expect(within(rows[0]).getByText('确认依据')).toBeInTheDocument();
    expect(within(rows[1]).getByText('v2 · o2')).toBeInTheDocument();
    expect(within(rows[1]).getByText('用户编辑')).toBeInTheDocument();
    expect(within(rows[1]).getByRole('button', { name: '确认此版' })).toBeInTheDocument();

    await user.click(confirm);
    await waitFor(() => expect(stub.callsOf('POST', /director\/outline\/confirm$/)).toHaveLength(1));
    expect(stub.callsOf('POST', /director\/outline\/confirm$/)[0].body)
      .toMatchObject({ artifact_id: 'o2', expected_sha256: SHA2 });
    expect(await screen.findByText(/大纲版本 o2 · 你已确认/)).toBeInTheDocument();
  });

  it('相同输入重新提取时如实报告复用，不谎报排队', async () => {
    const user = userEvent.setup();
    stubConsentAndTasks();
    stub.add({ method: 'GET', pattern: /director\/outline$/, respond: () => jsonResponse({
      artifact_id: 'o1', sha256: SHA1, markdown: MD1, questions: [],
      approved: true, confirmed: true, confirmed_artifact_id: 'o1',
      versions: [version('o1', SHA1, { approved: true, confirmed: true })] }) });
    stub.add({ method: 'POST', pattern: /story-preparation\/outlines$/,
      respond: () => jsonResponse({ task_id: 'task-1', status: 'SUCCEEDED' }) });
    const onMessage = vi.fn();
    render(<OutlineConfirmationPanel projectId="p1" documentId="doc-1"
      documentHash={'b'.repeat(64)} onMessage={onMessage} />);
    await user.click(await screen.findByRole('button', { name: '后台提取并归并总体大纲' }));
    await waitFor(() => expect(onMessage).toHaveBeenCalledWith(
      expect.stringMatching(/复用已有大纲结果，没有重跑模型/)));
  });

  it('查看旧版正文后保存，以旧版为基础产生新版本', async () => {
    const user = userEvent.setup();
    stubConsentAndTasks();
    stub.add({ method: 'GET', pattern: /director\/outline(\?.*)?$/, respond: (url) => jsonResponse(
      url.includes('artifact_id=o1')
        ? { artifact_id: 'o1', sha256: SHA1, markdown: MD1, questions: [], approved: true,
          confirmed: true, confirmed_artifact_id: 'o1',
          versions: [version('o2', SHA2, { user_edited: true, revision_of: 'o1' }),
            version('o1', SHA1, { approved: true, confirmed: true })] }
        : { artifact_id: 'o2', sha256: SHA2, markdown: MD2, questions: [], approved: false,
          confirmed: false, confirmed_artifact_id: 'o1', user_edited: true,
          versions: [version('o2', SHA2, { user_edited: true, revision_of: 'o1' }),
            version('o1', SHA1, { approved: true, confirmed: true })] }) });
    stub.add({ method: 'POST', pattern: /director\/outline\/revision$/,
      respond: () => jsonResponse({ artifact_id: 'o3', sha256: 'a'.repeat(64), markdown: MD1, questions: [] }, 201) });

    render(<OutlineConfirmationPanel projectId="p1" documentId="doc-1" documentHash={'b'.repeat(64)} />);
    await user.click(await screen.findByText(/大纲版本历史（2）/));
    const rows = within(screen.getByRole('list')).getAllByRole('listitem');
    await user.click(within(rows[0]).getByRole('button', { name: '查看正文' }));
    expect(await screen.findByText(/编辑器内容基于版本 o1/)).toBeInTheDocument();
    await user.type(screen.getByLabelText('大纲正文'), '再改。');
    await user.click(screen.getByRole('button', { name: '保存修改为新版本' }));
    await waitFor(() => expect(stub.callsOf('POST', /director\/outline\/revision$/)).toHaveLength(1));
    expect(stub.callsOf('POST', /director\/outline\/revision$/)[0].body)
      .toMatchObject({ expected_sha256: SHA1 });
  });
});