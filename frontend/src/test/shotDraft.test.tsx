/** Counterexample (audit 2026-11-01 #4): submitting a shot with an illegal
 *  duration (16s) returned 422, and the form wiped the story beat and the
 *  asset bindings while showing raw English JSON.  The fix: await the server,
 *  keep every field on failure, show a field-level message, and clear only
 *  after a confirmed create. */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import App from '../App';
import { FROZEN_STYLE, StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  const project = baseProject({ frozen_style_version_id: 'style-1' });
  stub = new StudioStub([
    ...baseRoutes(project),
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/styles$/, respond: () => jsonResponse([FROZEN_STYLE]) },
  ]);
});

afterEach(() => {
  stub.restore();
});

async function fillBeat(user: ReturnType<typeof userEvent.setup>, text: string) {
  const beat = await screen.findByLabelText(/镜头意图/);
  // The shot form is disabled until the frozen style finishes loading.
  await waitFor(() => {
    expect(beat).not.toBeDisabled();
  });
  await user.type(beat, text);
}

async function clickSave(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /保存分镜/ }));
}

async function switchToExpert(user: ReturnType<typeof userEvent.setup>) {
  // 界面会记住上次的模式（localStorage）：已经在专业模式时再点一次是“回到导演模式”。
  if (document.querySelector('.app-shell.ui-expert')) return;
  const toggle = await waitFor(() => {
    const node = document.querySelector('.ui-toggle');
    expect(node).not.toBeNull();
    return node as HTMLElement;
  });
  await user.click(toggle);
  await waitFor(() => expect(document.querySelector('.app-shell.ui-expert')).not.toBeNull());
}

describe('分镜提交失败必须保留输入', () => {
  it('422 时长越界：输入与素材选择保留，错误可读且定位到字段（故障注入）', async () => {
    const user = userEvent.setup();
    const asset = {
      id: 'asset-1', project_id: 'proj-1', filename: '主角定妆.png', kind: 'image',
      content_type: 'image/png', sha256: 'd'.repeat(64), byte_size: 10, role: null,
      entity_id: null, tags: [], duration_s: null, width: 8, height: 8,
      approved: true, locked: true, approved_at: 't', locked_at: 't',
    };
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/assets$/, respond: () => jsonResponse([asset]) });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/shots$/,
      respond: () => jsonResponse({
        detail: [{ type: 'less_than_equal', loc: ['body', 'spec', 'duration_target_s'], msg: 'Input should be less than or equal to 15', input: 16 }],
      }, 422),
    });

    render(<App />);
    await switchToExpert(user);
    await user.click(await screen.findByText('单镜头制作与任务明细（高级）'));
    await fillBeat(user, '雨夜里两人交换账册线索');
    await user.clear(screen.getByLabelText(/时长（4–15 秒）/));
    await user.type(screen.getByLabelText(/时长（4–15 秒）/), '16');
    // Choose the approved asset in the binding row (scoped to the builder).
    const builder = within(screen.getByText('镜头引用素材').closest('div')!);
    const select = builder.getAllByRole('combobox')[0];
    await waitFor(() => {
      expect(within(select).getByRole('option', { name: /主角定妆/ })).toBeInTheDocument();
    });
    await user.selectOptions(select, asset.id);
    await clickSave(user);

    // The client-side range guard explains the problem before the server.
    await waitFor(() => {
      expect(screen.getByText(/时长需在 4–15 秒之间（当前：16）/)).toBeInTheDocument();
    });
    // No request left the page for an out-of-range duration.
    expect(stub.calls.filter((call) => call.method === 'POST' && /shots$/.test(call.url))).toHaveLength(0);
    // Every user input survives the failed submit.
    expect(screen.getByLabelText(/镜头意图/)).toHaveValue('雨夜里两人交换账册线索');
    expect((builder.getAllByRole('combobox')[0] as HTMLSelectElement).value).toBe(asset.id);
  });

  it('失败后修正参数重新提交：只创建一个镜头，成功后按约定清空', async () => {
    const user = userEvent.setup();
    let postCount = 0;
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/shots$/,
      respond: (_url, init) => {
        postCount += 1;
        const body = JSON.parse(String(init?.body));
        if (body.spec.duration_target_s > 15) {
          return jsonResponse({ detail: [{ loc: ['body', 'spec', 'duration_target_s'], msg: 'too big' }] }, 422);
        }
        return jsonResponse({ id: `shot-${postCount}`, project_id: 'proj-1', name: body.name, revision: 1, spec: body.spec, bindings: [] }, 201);
      },
    });

    render(<App />);
    await switchToExpert(user);
    await user.click(await screen.findByText('单镜头制作与任务明细（高级）'));
    await fillBeat(user, '第一次提交的内容');
    await user.clear(screen.getByLabelText(/时长（4–15 秒）/));
    await user.type(screen.getByLabelText(/时长（4–15 秒）/), '16');
    await clickSave(user);
    await waitFor(() => {
      // Client-side guard explains the range before the server is hit.
      expect(screen.getByText(/时长需在 4–15 秒之间/)).toBeInTheDocument();
    });
    // Failure kept the draft.
    expect(screen.getByLabelText(/镜头意图/)).toHaveValue('第一次提交的内容');

    await user.clear(screen.getByLabelText(/时长（4–15 秒）/));
    await user.type(screen.getByLabelText(/时长（4–15 秒）/), '6');
    await clickSave(user);
    await waitFor(() => {
      expect(screen.getByText(/分镜已保存/)).toBeInTheDocument();
    });
    expect(postCount).toBe(1);
    await waitFor(() => {
      expect(screen.getByLabelText(/镜头意图/)).toHaveValue('');
    });
  });

  it('保存进行中不重复提交（一次点击只创建一个镜头）', async () => {
    const user = userEvent.setup();
    let resolvePost: (() => void) | undefined;
    let postCount = 0;
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/shots$/,
      respond: () => new Promise((resolve) => {
        postCount += 1;
        resolvePost = () => resolve(jsonResponse({ id: 'shot-1', project_id: 'proj-1', name: '镜头 1', revision: 1, spec: {}, bindings: [] }, 201));
      }),
    });

    render(<App />);
    await switchToExpert(user);
    await user.click(await screen.findByText('单镜头制作与任务明细（高级）'));
    await fillBeat(user, '慢请求期间的镜头');
    await clickSave(user);
    // The button reads 正在保存… and is disabled while the request is pending.
    const saving = await screen.findByRole('button', { name: /正在保存/ });
    expect(saving).toBeDisabled();
    // A second click while pending must not create another shot.
    await user.click(saving).catch(() => undefined);
    resolvePost?.();
    await waitFor(() => {
      expect(screen.getByText(/分镜已保存/)).toBeInTheDocument();
    });
    expect(postCount).toBe(1);
  });

  it('服务端 422（直接命中服务器）：对白越界时长，字段级错误且输入保留（故障注入）', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/shots$/,
      respond: () => jsonResponse({
        detail: [{ type: 'value_error', loc: ['body', 'spec'], msg: 'Value error, dialogue must fit inside the target shot duration' }],
      }, 422),
    });

    render(<App />);
    await switchToExpert(user);
    await user.click(await screen.findByText('单镜头制作与任务明细（高级）'));
    await fillBeat(user, '直接命中服务端校验');
    // The client guard only covers 时长 itself; end_s beyond the target
    // duration is validated by the server, so this really reaches it.
    await user.clear(screen.getByLabelText(/结束秒/));
    await user.type(screen.getByLabelText(/结束秒/), '8');
    await clickSave(user);
    await waitFor(() => {
      expect(screen.getByText(/服务器拒绝了这次提交/)).toBeInTheDocument();
      expect(screen.getByText(/对白.*时长|dialogue must fit/)).toBeInTheDocument();
    });
    expect(screen.getByLabelText(/镜头意图/)).toHaveValue('直接命中服务端校验');
  });
});

describe('镜头选择器写入真实 ShotSpec（P01/P02）', () => {
  const characterAsset = {
    id: 'asset-char', project_id: 'proj-1', filename: '林迟定妆.png', kind: 'image',
    content_type: 'image/png', sha256: 'e'.repeat(64), byte_size: 10, role: 'character',
    entity_id: 'CHAR_LINCHI', tags: [], duration_s: null, width: 8, height: 8,
    approved: true, locked: true, approved_at: 't', locked_at: 't',
  };

  it('人物来自本项目已批准主资产，选择后进入 POST spec 并带景别/运镜', async () => {
    const user = userEvent.setup();
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/assets$/, respond: () => jsonResponse([characterAsset]) });
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/shots$/,
      respond: (_url, init) => jsonResponse({ id: 'shot-new', name: '镜头 1', revision: 1, spec: JSON.parse(String(init?.body)).spec, bindings: [] }, 201),
    });
    render(<App />);
    await switchToExpert(user);
    await user.click(await screen.findByText('单镜头制作与任务明细（高级）'));
    const beat = await screen.findByLabelText(/镜头意图/);
    await waitFor(() => expect(beat).not.toBeDisabled());
    await user.type(beat, '林迟在雨夜里核对账册');
    await user.click(await screen.findByRole('checkbox', { name: 'CHAR_LINCHI' }));
    await user.selectOptions(screen.getByLabelText('对白角色'), 'CHAR_LINCHI');
    await user.type(screen.getByLabelText('对白内容'), '账册在哪儿？');
    await user.selectOptions(screen.getByLabelText('景别'), 'close_up');
    await user.selectOptions(screen.getByLabelText('运镜'), 'slow_push');
    await clickSave(user);
    await waitFor(() => {
      expect(stub.callsOf('POST', /shots$/)).toHaveLength(1);
    });
    const spec = (stub.callsOf('POST', /shots$/)[0].body as { spec: {
      characters: string[]; dialogue: Array<{ speaker_id: string }>; camera: Record<string, string>;
    } }).spec;
    expect(spec.characters).toEqual(['CHAR_LINCHI']);
    expect(spec.dialogue[0].speaker_id).toBe('CHAR_LINCHI');
    expect(spec.camera).toMatchObject({ shot_size: 'close_up', movement: 'slow_push' });
  });
});