/** Counterexample (audit 2026-11-01 #1): the user saved draft A, edited the
 *  form to B without saving, clicked freeze — and the server froze A while
 *  the screen showed B.  The fix freezes exactly the on-screen form: save
 *  first, then grant, then freeze; failures keep the form. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { FROZEN_STYLE, StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

const PUT_DRAFT = /\/api\/v1\/projects\/proj-1\/style\/draft$/;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
});

async function fillSevenFields(user: ReturnType<typeof userEvent.setup>, suffix: string) {
  const labels = ['摄影语言', '镜头调度', '色彩风格', '影调与灯光', '镜头质感', '剪辑与节奏', '声音表演'];
  for (const labelText of labels) {
    // Await each label: the workbench renders after async loads settle.
    await user.type(await screen.findByLabelText(new RegExp(labelText)), suffix);
  }
}

describe('风格冻结：批准内容必须等于冻结快照', () => {
  it('未保存修改直接冻结：先保存当前表单再冻结（原反例）', async () => {
    const user = userEvent.setup();
    const project = baseProject({
      style_draft: {
        photography_language: '手持近景，缓慢推近。（未保存的新修改）', shot_blocking: 'A调度', color_palette: 'A色',
        lighting_tone: 'A影调', lens_texture: 'A质感', editing_rhythm: 'A节奏', sound_performance: 'A声音',
      },
    });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([project]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1$/, respond: () => jsonResponse(project) });
    // The saved draft B the user typed; after the fix the freeze consumes it.
    let savedDraft: unknown = project.style_draft;
    stub.add({
      method: 'PUT', pattern: /\/api\/v1\/projects\/proj-1\/style\/draft$/,
      respond: (_url, init) => {
        savedDraft = JSON.parse(String(init?.body));
        return jsonResponse({ project_id: 'proj-1', style_draft: savedDraft, frozen: false });
      },
    });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: (_url, init) => {
        const body = JSON.parse(String(init?.body));
        return jsonResponse({ ...FROZEN_STYLE, snapshot: savedDraft, sha256: body.approval_note === 'x' ? 'f'.repeat(64) : 'f'.repeat(64) });
      },
    });

    render(<App />);
    const beat = await screen.findByLabelText(/摄影语言/);
    await waitFor(() => {
      expect(beat).toHaveValue('手持近景，缓慢推近。（未保存的新修改）');
    });

    // The user edits the form to B and does NOT click 保存草案.
    await user.clear(screen.getByLabelText(/摄影语言/));
    await user.type(screen.getByLabelText(/摄影语言/), '固定远景，禁止推近。（准备批准的最终修改）');

    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));

    await waitFor(() => {
      expect(screen.getByText(/已冻结第 1 版/)).toBeInTheDocument();
    });
    // Order: the current form was saved before freezing; the frozen snapshot is B.
    const draftPuts = stub.callsOf('PUT', /style\/draft$/);
    const freezes = stub.callsOf('POST', /style\/freeze$/);
    expect(draftPuts).toHaveLength(1);
    expect(freezes).toHaveLength(1);
    expect(freezes[0].body).toMatchObject({ expected_style: savedDraft });
    expect(draftPuts[0].body).toMatchObject({ photography_language: '固定远景，禁止推近。（准备批准的最终修改）' });
    const putIndex = stub.calls.findIndex((call) => call.method === 'PUT' && /style\/draft$/.test(call.url));
    const postIndex = stub.calls.findIndex((call) => call.method === 'POST' && /style\/freeze$/.test(call.url));
    expect(putIndex).toBeLessThan(postIndex);
    expect(savedDraft).toMatchObject({ photography_language: '固定远景，禁止推近。（准备批准的最终修改）' });
    // Passwordless local actions still obtain an exact, one-use grant.
    expect(stub.callsOf('POST', /approval-grants/)).toHaveLength(1);
  });

  it('免密冻结仍保持保存→授权→冻结顺序，授权后草稿变化会被拒绝并保留表单', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/preparation\/approval-grants$/,
      respond: () => jsonResponse({ grant_id: 'grant-1', snapshot: 'snap-1' }),
    });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      // Failure injection (marked): the draft changed after the grant was
      // issued, so the server refuses with the snapshot-mismatch code.
      respond: () => jsonResponse({ detail: 'PLAN_HASH_CHANGED' }, 409),
    });

    render(<App />);
    await user.type(await screen.findByLabelText(/摄影语言/), '新项目的风格 A');
    await fillSevenFields(user, '（授权路径）');
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));

    await waitFor(() => {
      expect(screen.getByText(/PLAN_HASH_CHANGED/)).toBeInTheDocument();
    });
    const draftPuts = stub.callsOf('PUT', /style\/draft$/);
    const grants = stub.callsOf('POST', /approval-grants/);
    const freezes = stub.callsOf('POST', /style\/freeze$/);
    expect(draftPuts).toHaveLength(1);
    expect(grants).toHaveLength(1);
    expect(freezes).toHaveLength(1);
    const putIndex = stub.calls.findIndex((call) => PUT_DRAFT.test(call.url));
    const grantIndex = stub.calls.findIndex((call) => call.method === 'POST' && /approval-grants/.test(call.url));
    const postIndex = stub.calls.findIndex((call) => call.method === 'POST' && /style\/freeze$/.test(call.url));
    expect(putIndex < grantIndex && grantIndex < postIndex).toBe(true);
    // The rejected form keeps the user's input.
    expect(screen.getByLabelText(/摄影语言/)).toHaveValue('新项目的风格 A（授权路径）');
  });

  it('首次填完七项直接点击批准并冻结即可成功（第二个表现）', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: (_url, init) => jsonResponse({
        ...FROZEN_STYLE, snapshot: JSON.parse(String(init?.body)).expected_style,
      }),
    });

    render(<App />);
    await fillSevenFields(user, '（首冻内容）');
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await waitFor(() => {
      expect(screen.getByText(/已冻结第 1 版/)).toBeInTheDocument();
    });
    expect(stub.callsOf('PUT', /style\/draft$/)).toHaveLength(1);
    expect(stub.callsOf('POST', /style\/freeze$/)).toHaveLength(1);
  });

  it('保存失败（网络错误）时表单输入保留（故障注入）', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => { throw new Error('网络中断'); },
    });

    render(<App />);
    await fillSevenFields(user, '（断网冻结）');
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await waitFor(() => {
      expect(screen.getByText(/网络中断/)).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/摄影语言/)).toHaveValue('（断网冻结）');
  });

  it('携带七项确认内容，签发前的其他页面修改被拒绝时保留输入', async () => {
    const user = userEvent.setup();
    stub.add({ method: 'POST', pattern: /\/api\/v1\/preparation\/approval-grants$/,
      respond: () => jsonResponse({ grant_id: 'grant-for-other-tab', snapshot: 'other-tab-snapshot' }) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => jsonResponse({ detail: 'STYLE_APPROVAL_CONTENT_CHANGED' }, 409) });
    render(<App />);
    await fillSevenFields(user, 'Displayed A');
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await screen.findByText(/风格草稿已在其他页面修改/);
    const decision = stub.callsOf('POST', /style\/freeze$/)[0].body;
    expect(decision).toMatchObject({ expected_style: {
      photography_language: 'Displayed A', shot_blocking: 'Displayed A', color_palette: 'Displayed A',
      lighting_tone: 'Displayed A', lens_texture: 'Displayed A', editing_rhythm: 'Displayed A',
      sound_performance: 'Displayed A',
    } });
    expect(screen.getByLabelText(/摄影语言/)).toHaveValue('Displayed A');
    expect(screen.queryByText(/已冻结第 1 版/)).not.toBeInTheDocument();
  });

  it('不把不匹配的服务端冻结结果报告为与屏幕一致', async () => {
    const user = userEvent.setup();
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => jsonResponse(FROZEN_STYLE) });
    render(<App />);
    await fillSevenFields(user, 'Displayed A');
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await screen.findByText(/冻结结果与本次确认的风格内容不一致/);
    expect(screen.getByLabelText(/摄影语言/)).toHaveValue('Displayed A');
    expect(screen.queryByText(/已冻结第 1 版/)).not.toBeInTheDocument();
  });
});
