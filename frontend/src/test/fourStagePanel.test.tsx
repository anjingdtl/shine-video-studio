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
});

async function waitForConfirmation(stub: StudioStub) {
  await new Promise((r) => setTimeout(r, 50));
  expect(stub.callsOf('POST', new RegExp('scene-scripts/art-9/confirm$'))).toHaveLength(1);
  const call = stub.callsOf('POST', new RegExp('scene-scripts/art-9/confirm$'))[0];
  expect((call?.body as { expected_sha256?: string })?.expected_sha256).toBe('c'.repeat(64));
}
