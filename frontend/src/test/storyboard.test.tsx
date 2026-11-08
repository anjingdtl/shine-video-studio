import { afterEach, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StoryboardPanel from '../components/StoryboardPanel';
import App from '../App';
import { StudioStub, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

it('锁定剧情事实可以逐行输入并完整发送给分镜流程', async () => {
  const submit = vi.fn<(body: Record<string, unknown>) => void>();
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/projects\/proj-1\/storyboard\/generate$/, respond: (_url, init) => {
      submit(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ status: 'BLOCKED_MISSING_MASTERS', script_source: 'inline', steps: {}, missing_characters: ['林澈'] });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '三幕测试剧本');
  const facts = screen.getByLabelText('锁定剧情事实（每行一条，可选）');
  await user.type(facts, '信封始终未拆封\n账册第十七页缺失');
  expect(facts).toHaveValue('信封始终未拆封\n账册第十七页缺失');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));

  await waitFor(() => expect(stub.callsOf('POST', /storyboard\/generate$/)).toHaveLength(1));
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({
    locked_story_facts: ['信封始终未拆封', '账册第十七页缺失'],
  }));
  expect(await screen.findByRole('status')).toHaveTextContent(/这些人物没有匹配到唯一的已确认人物素材/);
  stub.restore();
});

it('对白语言保存到项目，并说明它会进入 H3 提示词', async () => {
  const saved: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/projects\/proj-1$/, respond: () => jsonResponse({ id: 'proj-1', language: 'Chinese' }) },
    { method: 'PATCH', pattern: /\/projects\/proj-1\/settings$/, respond: (_url, init) => {
      saved.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ language: 'Japanese' });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('对白语言')).toHaveValue('Chinese'));
  await user.selectOptions(screen.getByLabelText('对白语言'), 'Japanese');

  await waitFor(() => expect(saved).toEqual([{ language: 'Japanese' }]));
  expect(await screen.findByRole('status')).toHaveTextContent(/对白语言已设为/);
  stub.restore();
});

it('对白语言保存失败时如实显示失败，并保留已选语言', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/projects\/proj-1$/, respond: () => jsonResponse({ id: 'proj-1', language: 'Chinese' }) },
    { method: 'PATCH', pattern: /\/projects\/proj-1\/settings$/, respond: () => jsonResponse({ detail: 'boom' }, 500) },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('对白语言')).toHaveValue('Chinese'));
  await user.selectOptions(screen.getByLabelText('对白语言'), 'Korean');

  expect(await screen.findByText(/保存失败/)).toBeInTheDocument();
  expect(screen.getByLabelText('对白语言')).toHaveValue('Korean');
  stub.restore();
});

it('项目语言不在可选集合内时显示默认语言而不是空白', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/projects\/proj-1$/, respond: () => jsonResponse({ id: 'proj-1', language: '火星语' }) },
  ]);
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('对白语言')).toHaveValue('Chinese'));
  stub.restore();
});

it('仅有一份已导入剧本时在进入分镜页后自动选中', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([{
      id: 'doc-1', filename: 'rainy-night-page-17.txt', sha256: 'a'.repeat(64),
      source_format: 'text', text_preview: '三幕测试剧本', text_length: 547, created_at: '2026-11-02T00:00:00Z',
    }]) },
  ]);
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('或选择已导入剧本')).toHaveValue('doc-1'));
  expect(screen.queryByLabelText('粘贴完整剧本文本')).not.toBeInTheDocument();
  stub.restore();
});

it.each([[false, false, false], [true, false, false], [true, true, false], [false, true, false], [false, false, true]])(
  '原页检查后启动分镜（未送达=%s，从头=%s，中断已核对=%s）', async (notSent, restartAll, interrupted) => {
  const submitted: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /film-runs$/, respond: () => jsonResponse([{
      id: 'previous-director', role_id: 'director', status: interrupted ? 'ABANDONED' : notSent ? 'REJECTED' : 'RUNNING',
      created_at: '', trace: { external_outcome: notSent ? 'NOT_SENT' : 'UNKNOWN', input_revision_ids: ['saved-script'],
        local_interruption_verified: interrupted },
    }]) },
    { method: 'POST', pattern: /\/projects\/proj-1\/storyboard\/generate$/, respond: (_url, init) => {
      submitted.push(JSON.parse(String(init?.body)));
      return jsonResponse(submitted.length === 1 ? {
        status: 'REJECTED', script_source: 'inline', steps: {},
        rejections: [{ role: 'director', code: 'LLM_TIMEOUT', task_id: 'previous-director' }],
      } : { status: 'PROPOSED', script_source: 'inline', steps: {} });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '三幕测试剧本');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));

  expect(await screen.findByText(/远端结果未确认/)).toBeInTheDocument();
  if (restartAll) await user.click(screen.getByText('前次请求详情'));
  await user.click(screen.getByRole('button', { name: restartAll ? '检查后从头重跑' : '检查并自动继续导演' }));
  if (interrupted) {
    const start = await screen.findByRole('button', { name: '开始新一轮导演' });
    expect(submitted).toHaveLength(1);
    await user.click(start);
    await waitFor(() => expect(submitted).toHaveLength(2));
    expect(submitted[1]).toEqual(expect.objectContaining({
      start_role: 'director', resume_steps: { scriptwriter: 'saved-script' }, replace_interrupted_task_id: 'previous-director',
    }));
  } else if (notSent) {
    await waitFor(() => expect(submitted).toHaveLength(2));
    if (restartAll) {
      expect(submitted[1]).not.toHaveProperty('start_role');
      expect(submitted[1]).not.toHaveProperty('resume_steps');
      expect(submitted[1]).toHaveProperty('confirm_characters', false);
    } else {
      expect(submitted[1]).toEqual(expect.objectContaining({
        start_role: 'director', resume_steps: { scriptwriter: 'saved-script' },
      }));
    }
  } else {
    expect(await screen.findByText(/仍无可续跑的确认结果/)).toBeInTheDocument();
    expect(submitted).toHaveLength(1);
  }
  expect(screen.queryByText(/请复核后重试/)).not.toBeInTheDocument();
  stub.restore();
});

it('本项目没绑定官方规则包时，第 3 步直接给出入口', async () => {
  const navigated: string[] = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/projects\/proj-1\/rulepack$/, respond: () => jsonResponse({ rulepack_id: null }) },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} onNavigate={(page) => navigated.push(page)} />);

  expect(await screen.findByText(/本项目还没绑定官方规则包/)).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /去启用官方规则包/ }));
  expect(navigated).toEqual(['assets']);
  stub.restore();
});

afterEach(() => vi.unstubAllGlobals());

it('旧裁定失败响应也能复用五个步骤重试裁定', async () => {
  const submitted: Array<Record<string, unknown>> = [];
  let finish!: (value: ReturnType<typeof jsonResponse>) => void;
  const pending = new Promise<ReturnType<typeof jsonResponse>>(resolve => { finish = resolve; });
  const names = ['scriptwriter', 'director', 'cinematographer', 'continuity_supervisor', 'editor_edit_intent'];
  const steps = Object.fromEntries([...names, 'director_arbitration'].map(name => [name,
    { artifact_id: `saved-${name}`, kind: name, sha256: 'x' }]));
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/documents$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /storyboard\/generate$/, respond: (_url, init) => {
      submitted.push(JSON.parse(String(init?.body)));
      return submitted.length === 1 ? jsonResponse({ status: 'REJECTED', steps,
        rejections: [{ role: 'director_arbitration', code: 'AGENT_REPLACEMENT_NOT_AVAILABLE' }] }) : pending;
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);
  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '测试剧本');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));
  await user.click(await screen.findByRole('button', { name: /连续性裁定.*这一步重试/ }));
  await waitFor(() => expect(submitted).toHaveLength(2));
  expect(submitted[1]).toEqual(expect.objectContaining({ start_role: 'director_arbitration',
    resume_steps: Object.fromEntries([...names, 'director_arbitration'].map(name => [name, `saved-${name}`])) }));
  expect(screen.getByText(/已复用 5 个已完成步骤/)).toBeInTheDocument();
  await act(async () => { finish(jsonResponse({ status: 'PROPOSED', steps: {}, script_source: 'inline' })); });
  stub.restore();
});

it('轮询时将本次场记任务引用的导演方案计为已复用，并同步顶部计数', async () => {
  let finish!: (value: ReturnType<typeof jsonResponse>) => void;
  const pending = new Promise<ReturnType<typeof jsonResponse>>(resolve => { finish = resolve; });
  let polls = 0;
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/film-runs$/, respond: () => jsonResponse(++polls === 1 ? [] : [
      { id: 'camera', role_id: 'cinematographer', status: 'SUCCEEDED', created_at: '',
        trace: { input_revision_ids: ['writer', 'director'] } },
      { id: 'continuity', role_id: 'continuity_supervisor', status: 'RUNNING', created_at: '',
        trace: { input_revision_ids: ['writer', 'director', 'camera-artifact'] } },
    ]) },
    { method: 'GET', pattern: /\/film-artifacts$/, respond: () => jsonResponse([
      { id: 'writer', kind: 'ScriptBreakdown', producer_role: 'scriptwriter', stale: false },
      { id: 'director', kind: 'DirectorialPlan', producer_role: 'director', stale: false },
      { id: 'camera-artifact', kind: 'CameraPlan', producer_role: 'cinematographer', stale: false },
      { id: 'unrelated', kind: 'EditIntentPlan', producer_role: 'editor', stale: false },
    ]) },
    { method: 'POST', pattern: /storyboard\/generate$/, respond: () => pending },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);
  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '测试剧本');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));
  await waitFor(() => expect(screen.getByText(/导演：已完成（复用已保存结果）/)).toBeInTheDocument(), { timeout: 5000 });
  expect(screen.getByText(/第 4\/5 步：场记 Agent.*已完成 3\/5 步/)).toBeInTheDocument();
  expect(screen.getByRole('progressbar')).toHaveValue(3);
  expect(screen.getByText(/剪辑：尚未启动/)).toBeInTheDocument();
  await act(async () => { finish(jsonResponse({ status: 'PROPOSED', steps: {}, script_source: 'inline' })); });
  stub.restore();
});

it('读取已确认人物后先明确补齐文本同意，再直接从导演续跑且不重复写名字', async () => {
  const submitted: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/documents$/, respond: () => jsonResponse([
      { id: 'doc-1', filename: 'script.md', text_length: 100 },
    ]) },
    { method: 'POST', pattern: /character-binding\/proposal$/, respond: () => jsonResponse({
      proposal_id: 'saved-proposal', breakdown_artifact_id: 'confirmed-breakdown', already_confirmed: true,
      characters: [{ script_name: '阿德', suggested_asset_id: 'asset-1', suggested_name: '阿德', candidates: [] }],
      masters: [{ id: 'asset-1', filename: 'photo.png', entity_id: '阿德', approved: true, locked: true }],
    }) },
    { method: 'POST', pattern: /storyboard\/generate$/, respond: (_url, init) => {
      submitted.push(JSON.parse(String(init?.body)));
      return jsonResponse({ status: 'PROPOSED', script_source: 'document:doc-1', steps: {} });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: /读取已完成的编剧人物对照/ }));
  const continueButton = await screen.findByRole('button', { name: '复用已确认人物，继续导演' });
  expect(continueButton).toBeDisabled();
  expect(screen.getByText('导演还未启动：需要同意发送剧本文本')).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '分镜交接状态' })).toHaveTextContent('人物已确认，导演尚未启动');
  expect(stub.callsOf('POST', /character-binding\/apply$/)).toHaveLength(0);
  await user.click(screen.getByRole('checkbox', { name: /同意把当前剧本文本/ }));
  await user.click(continueButton);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0]).toEqual(expect.objectContaining({
    document_id: 'doc-1', start_role: 'director', resume_steps: { scriptwriter: 'confirmed-breakdown' },
  }));
  expect(stub.callsOf('POST', /character-binding\/apply$/)).toHaveLength(0);
  stub.restore();
});

it('切换素材步骤再返回时保留正在运行的分镜请求和最终结果', async () => {
  window.history.replaceState(null, '', '#d/produce');
  let finish!: (value: ReturnType<typeof jsonResponse>) => void;
  const response = new Promise<ReturnType<typeof jsonResponse>>(resolve => { finish = resolve; });
  const stub = new StudioStub([
    ...baseRoutes(),
    { method: 'POST', pattern: /storyboard\/generate$/, respond: () => response },
  ]);
  const user = userEvent.setup();
  render(<App />);
  await user.type(await screen.findByLabelText('粘贴完整剧本文本'), '剧本不会因切页丢失');
  await user.click(screen.getByRole('checkbox', { name: /我同意把该剧本/ }));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));
  await waitFor(() => expect(stub.callsOf('POST', /storyboard\/generate$/)).toHaveLength(1));
  await gotoDirectorPage(user, /剧本统筹/);
  await gotoDirectorPage(user, /镜头拆解/);
  expect(screen.getByLabelText('粘贴完整剧本文本')).toHaveValue('剧本不会因切页丢失');
  expect(screen.getByRole('button', { name: /生成 AI 分镜方案/ })).toBeDisabled();
  await act(async () => { finish(jsonResponse({ status: 'PROPOSED', steps: {}, script_source: 'inline' })); });
  expect(await screen.findByText(/五个 Agent 已完成真实调用/)).toBeInTheDocument();
  expect(stub.callsOf('POST', /storyboard\/generate$/)).toHaveLength(1);
  stub.restore();
});

it('人物对不上时给出人物对照，并可确认名图后从导演这一步继续', async () => {
  const submitted: Array<Record<string, unknown>> = [];
  const applied: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/storyboard\/character-binding\/proposal$/, respond: () => jsonResponse({
      proposal_id: 'p-1', breakdown_artifact_id: 'b-2',
      characters: [{
        script_name: 'S1', needs_real_name: true, confidence: 'low',
        suggested_asset_id: 'asset-1', suggested_name: '阿德',
        candidates: [{ asset_id: 'asset-1', filename: '001.png', entity_id: '001', tags: ['主角', '阿德'],
          score: 0.3, reason: '剧本第 1 位人物，按上传顺序对应，请人工核对', approved: false, locked: false }],
      }],
      masters: [{ id: 'asset-1', filename: '001.png', entity_id: '001', tags: ['主角', '阿德'], approved: false, locked: false }],
    }) },
    { method: 'POST', pattern: /\/storyboard\/character-binding\/apply$/, respond: (_url, init) => {
      applied.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ artifact_id: 'b-3', assets: [{ id: 'asset-1', entity_id: '阿德', locked: false }] });
    } },
    { method: 'POST', pattern: /\/preparation\/approval-grants$/, respond: () => jsonResponse({ grant_id: 'g-1', snapshot: 's-1' }) },
    { method: 'POST', pattern: /\/assets\/asset-1\/(approve|lock)$/, respond: () => jsonResponse({ id: 'asset-1' }) },
    { method: 'POST', pattern: /\/projects\/proj-1\/storyboard\/generate$/, respond: (_url, init) => {
      submitted.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return submitted.length === 1
        ? jsonResponse({ status: 'AWAITING_CHARACTER_CONFIRMATION', script_source: 'inline',
            steps: { scriptwriter: { artifact_id: 'b-2', kind: 'ScriptBreakdown', sha256: 'a' } } })
        : jsonResponse({ status: 'PROPOSED', script_source: 'inline', steps: {}, next: null });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '三幕测试剧本');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));

  expect(await screen.findByText(/人物对照（剧本人物/)).toBeInTheDocument();
  expect(screen.getByDisplayValue('阿德')).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '分镜交接状态' })).toHaveTextContent('导演尚未启动');
  expect(screen.getByRole('progressbar')).toHaveValue(1);
  expect(submitted).toHaveLength(1);
  // Designing continues without the matching step: a missing master is a
  // material requirement, so the UI no longer asks the server to stop.
  expect(submitted[0].confirm_characters).toBe(false);
  // A backend that still answers the legacy state is handled the same way.
  expect(stub.callsOf('POST', /character-binding\/proposal$/)[0].body)
    .toEqual({ breakdown_artifact_id: 'b-2' });
  await user.click(screen.getByRole('button', { name: /确认并写入名字/ }));

  await waitFor(() => expect(applied).toHaveLength(1));
  expect(applied[0]).toEqual(expect.objectContaining({
    breakdown_artifact_id: 'b-2',
    bindings: [{ script_name: 'S1', asset_id: 'asset-1', name: '阿德' }],
  }));
  await waitFor(() => expect(submitted).toHaveLength(2));
  expect(submitted[1]).toEqual(expect.objectContaining({
    start_role: 'director',
    resume_steps: { scriptwriter: 'b-3' },
  }));
  stub.restore();
});

it('某一步失败后可以从那一步重试，不重跑已完成的步骤', async () => {
  const submitted: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/projects\/proj-1\/storyboard\/generate$/, respond: (_url, init) => {
      submitted.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return submitted.length === 1
        ? jsonResponse({
            status: 'REJECTED', script_source: 'inline',
            steps: { scriptwriter: { artifact_id: 'b-1', kind: 'ScriptBreakdown', sha256: 'a'.repeat(64) } },
            rejections: [{ role: 'director', code: 'CONTRACT_OR_POLICY_REJECTED' }],
            failed_role: 'director',
            resume_steps: { scriptwriter: 'b-1' },
          })
        : jsonResponse({ status: 'PROPOSED', script_source: 'inline', steps: {}, next: null });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '三幕测试剧本');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));

  const retry = await screen.findByRole('button', { name: /从「导演 Agent · 镜头拆分」这一步重试/ });
  await user.click(retry);
  await waitFor(() => expect(submitted).toHaveLength(2));
  expect(submitted[1]).toEqual(expect.objectContaining({
    start_role: 'director',
    resume_steps: { scriptwriter: 'b-1' },
  }));
  stub.restore();
});
