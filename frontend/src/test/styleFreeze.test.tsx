/** Counterexamples (audit 2026-11-01 + the 2026-11-02 style catalog round):
 *  the user saved draft A, edited the form to B without saving, clicked
 *  freeze — and the server froze A while the screen showed B.  The fix freezes
 *  exactly the on-screen form: save first, then grant, then freeze.  The
 *  2026-11-02 round adds the preset/dropdown interaction: every scenario now
 *  goes through the catalog dropdowns instead of hand-typed prose, and custom
 *  text keeps its own verbatim path. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { STYLE_DIMENSION_OPTIONS } from '../styleCatalog';
import { FROZEN_STYLE, StudioStub, baseProject, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

let stub: StudioStub;

const PUT_DRAFT = /\/api\/v1\/projects\/proj-1\/style\/draft$/;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
});

/** Select the first catalog option of every dimension through the real UI. */
async function fillSevenOptions(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /调整细节/ }));
  const labels = ['摄影语言', '镜头调度', '色彩风格', '影调与灯光', '镜头质感', '剪辑与节奏', '声音表演'];
  for (const labelText of labels) {
    const select = (await screen.findByLabelText(labelText)) as HTMLSelectElement;
    const firstOption = select.options[1].value;
    await user.selectOptions(select, firstOption);
  }
}


describe('风格冻结：批准内容必须等于冻结快照', () => {
  it('未保存修改直接冻结：先保存当前表单再冻结（原反例）', async () => {
    const user = userEvent.setup();
    const custom = {
      photography_language: '手持近景，缓慢推近。（未保存的新修改）', shot_blocking: 'A调度', color_palette: 'A色',
      lighting_tone: 'A影调', lens_texture: 'A质感', editing_rhythm: 'A节奏', sound_performance: 'A声音',
    };
    const project = baseProject({ style_draft: custom });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([project]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1$/, respond: () => jsonResponse(project) });
    let savedDraft: unknown = project.style_draft;
    stub.add({
      method: 'PUT', pattern: PUT_DRAFT,
      respond: (_url, init) => {
        savedDraft = JSON.parse(String(init?.body));
        return jsonResponse({ project_id: 'proj-1', style_draft: savedDraft, frozen: false });
      },
    });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => jsonResponse({ ...FROZEN_STYLE, snapshot: savedDraft }),
    });

    render(<App />);
    await gotoDirectorPage(user, /剧本统筹与素材/);
    const textarea = await screen.findByLabelText('摄影语言（自定义）');
    await waitFor(() => {
      expect(textarea).toHaveValue('手持近景，缓慢推近。（未保存的新修改）');
    });

    await user.clear(textarea);
    await user.type(textarea, '固定远景，禁止推近。（准备批准的最终修改）');

    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await waitFor(() => {
      expect(screen.getByText(/已冻结第 1 版/)).toBeInTheDocument();
    });
    const draftPuts = stub.callsOf('PUT', /style\/draft$/);
    expect(draftPuts).toHaveLength(1);
    const freezes = stub.callsOf('POST', /style\/freeze$/);
    expect(freezes).toHaveLength(1);
    expect(freezes[0].body).toMatchObject({ expected_style: savedDraft });
    expect(draftPuts[0].body).toMatchObject({ photography_language: '固定远景，禁止推近。（准备批准的最终修改）' });
    const putIndex = stub.calls.findIndex((call) => call.method === 'PUT' && /style\/draft$/.test(call.url));
    const postIndex = stub.calls.findIndex((call) => call.method === 'POST' && /style\/freeze$/.test(call.url));
    expect(putIndex).toBeLessThan(postIndex);
    expect(stub.callsOf('POST', /approval-grants/)).toHaveLength(1);
  });
});

describe('风格冻结（下拉交互）', () => {
  it('免密冻结仍保持保存→授权→冻结顺序，授权后草稿变化被拒绝并保留选项', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/preparation\/approval-grants$/,
      respond: () => jsonResponse({ grant_id: 'grant-1', snapshot: 'snap-1' }),
    });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => jsonResponse({ detail: 'PLAN_HASH_CHANGED' }, 409),
    });

    render(<App />);
    await gotoDirectorPage(user, /剧本统筹与素材/);
    await fillSevenOptions(user);
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
    const putIndex = stub.calls.findIndex((call) => call.method === 'PUT' && /style\/draft$/.test(call.url));
    const grantIndex = stub.calls.findIndex((call) => call.method === 'POST' && /approval-grants/.test(call.url));
    const postIndex = stub.calls.findIndex((call) => call.method === 'POST' && /style\/freeze$/.test(call.url));
    expect(putIndex < grantIndex && grantIndex < postIndex).toBe(true);
    expect(screen.getByLabelText('摄影语言')).toHaveValue(STYLE_DIMENSION_OPTIONS.photography_language[0].id);
  });

  it('首次通过下拉选满七项后直接点击批准并冻结即可成功', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: (_url, init) => jsonResponse({
        ...FROZEN_STYLE, snapshot: JSON.parse(String(init?.body)).expected_style,
      }),
    });

    render(<App />);
    await gotoDirectorPage(user, /剧本统筹与素材/);
    await fillSevenOptions(user);
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await waitFor(() => {
      expect(screen.getByText(/已冻结第 1 版/)).toBeInTheDocument();
    });
    const freezes = stub.callsOf('POST', /style\/freeze$/);
    expect(freezes).toHaveLength(1);
    expect(freezes[0].body).toMatchObject({ expected_style: {
      photography_language: STYLE_DIMENSION_OPTIONS.photography_language[0].text,
      sound_performance: STYLE_DIMENSION_OPTIONS.sound_performance[0].text,
    } });
    expect(stub.callsOf('PUT', /style\/draft$/)).toHaveLength(1);
  });

  it('保存失败（网络错误）时下拉选择保留（故障注入）', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => { throw new Error('网络中断'); },
    });

    render(<App />);
    await gotoDirectorPage(user, /剧本统筹与素材/);
    await fillSevenOptions(user);
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await waitFor(() => {
      expect(screen.getByText(/网络中断/)).toBeInTheDocument();
    });
    expect(screen.getByLabelText('摄影语言')).toHaveValue(STYLE_DIMENSION_OPTIONS.photography_language[0].id);
  });

  it('携带七项确认内容，签发前的其他页面修改被拒绝时保留选项', async () => {
    const user = userEvent.setup();
    stub.add({ method: 'POST', pattern: /\/api\/v1\/preparation\/approval-grants$/,
      respond: () => jsonResponse({ grant_id: 'grant-for-other-tab', snapshot: 'other-tab-snapshot' }) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => jsonResponse({ detail: 'STYLE_APPROVAL_CONTENT_CHANGED' }, 409) });
    render(<App />);
    await gotoDirectorPage(user, /剧本统筹与素材/);
    await fillSevenOptions(user);
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await screen.findByText(/风格草稿已在其他页面修改/);
    const decision = stub.callsOf('POST', /style\/freeze$/)[0].body;
    expect(decision).toMatchObject({ expected_style: {
      photography_language: STYLE_DIMENSION_OPTIONS.photography_language[0].text,
      shot_blocking: STYLE_DIMENSION_OPTIONS.shot_blocking[0].text,
      color_palette: STYLE_DIMENSION_OPTIONS.color_palette[0].text,
      lighting_tone: STYLE_DIMENSION_OPTIONS.lighting_tone[0].text,
      lens_texture: STYLE_DIMENSION_OPTIONS.lens_texture[0].text,
      editing_rhythm: STYLE_DIMENSION_OPTIONS.editing_rhythm[0].text,
      sound_performance: STYLE_DIMENSION_OPTIONS.sound_performance[0].text,
    } });
    expect(screen.getByLabelText('摄影语言')).toHaveValue(STYLE_DIMENSION_OPTIONS.photography_language[0].id);
    expect(screen.queryByText(/已冻结第 1 版/)).not.toBeInTheDocument();
  });

  it('不把不匹配的服务端冻结结果报告为与屏幕一致', async () => {
    const user = userEvent.setup();
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/,
      respond: () => jsonResponse(FROZEN_STYLE) });
    render(<App />);
    await gotoDirectorPage(user, /剧本统筹与素材/);
    await fillSevenOptions(user);
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await screen.findByText(/冻结结果与本次确认的风格内容不一致/);
    expect(screen.getByLabelText('摄影语言')).toHaveValue(STYLE_DIMENSION_OPTIONS.photography_language[0].id);
  });
});
