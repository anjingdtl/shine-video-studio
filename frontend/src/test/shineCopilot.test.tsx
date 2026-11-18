/** The global AI copilot must be a real connected surface: evidence, explicit
 *  preview/approval, report hand-off and an honest offline notice.  These tests
 *  also pin that dangerous requests are refused, not executed. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ShineCopilot, actionPayload, type CapabilityInfo } from '../components/ShineCopilot';
import { clearCopilotFault, reportCopilotFault } from '../copilotContext';
import { StudioStub, jsonResponse, type Route } from './studioStub';

const verdict = { mode: 'SETUP_REQUIRED', label: '需要配置真实制作', code: 'REAL_RUNTIME_NOT_ACTIVATED' };

const context = () => jsonResponse({
  snapshot: { setup: { verdict }, evidence_id: 'ev-1', provenance: [] },
  suggested_questions: ['帮我检查环境', '模型为什么用不了', '生成问题报告'],
  web_search_enabled: false,
});

const diagnosis = (overrides: Record<string, unknown> = {}) => ({
  category: 'CONFIG',
  category_label: '本机配置尚未就绪',
  severity: 'MEDIUM',
  symptom: '真实制作环境还没有完成配置。',
  facts: [{ key: '生产裁决', value: '需要配置真实制作（REAL_RUNTIME_NOT_ACTIVATED）', source: '/api/v1/setup/verdict' }],
  recommended_actions: [{
    kind: 'setup_repair',
    label: '补齐真实制作环境',
    reason: '补齐固定清单中的缺失文件。',
    requires_authorization: true,
    params: { capabilities: ['h3'], verify_runtime: true },
  }],
  next_step: '先在基础配置里检查并补齐环境。',
  outcome: 'CONFIG_ACTION_AVAILABLE',
  report_kind: 'BLOCKER',
  confidence: 'HIGH',
  llm: { used: false, status: 'NOT_CONFIGURED' },
  readiness_reference: { mode: 'SETUP_REQUIRED', label: '需要配置真实制作', code: 'REAL_RUNTIME_NOT_ACTIVATED' },
  evidence_id: 'ev-1',
  ...overrides,
});

const session = (overrides: Record<string, unknown> = {}) => ({
  id: 's1',
  page_id: 'setup',
  project_id: null,
  expires_at: '2026-12-16T00:00:00',
  created_at: '2026-11-16T00:00:00',
  updated_at: '2026-11-16T00:00:00',
  context: { snapshot: { setup: { verdict } } },
  messages: [
    {
      id: 'm1', role: 'user', kind: 'message', content: '帮我检查环境', payload: {},
      evidence_ids: [], created_at: '2026-11-16T00:00:01',
    },
    {
      id: 'm2', role: 'assistant', kind: 'diagnosis',
      content: '【结论】本机配置尚未就绪：真实制作环境还没有完成配置。\n【下一步】先在基础配置里检查并补齐环境。',
      payload: {
        diagnosis: diagnosis(),
        tool_trace: [{ tool: 'read_setup_verdict', status: 'OK' }, { tool: 'search_local_knowledge', status: 'OK' }],
        llm: { status: 'NOT_CONFIGURED' },
      },
      evidence_ids: ['ev-1'], created_at: '2026-11-16T00:00:02',
    },
  ],
  ...overrides,
});

const routeBase = (extra: Route[] = []) => new StudioStub([
  { method: 'GET', pattern: /\/support\/context/, respond: () => context() },
  { method: 'POST', pattern: /\/support\/sessions$/, respond: () => jsonResponse({ session: session(), diagnosis: diagnosis() }, 201) },
  ...extra,
]);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearCopilotFault();
  window.localStorage.clear();
});

describe('ShineCopilot', () => {
  let stub: StudioStub;

  beforeEach(() => {
    window.localStorage.clear();
  });

  it('opens from the global FAB and reads the single verdict with suggestions', async () => {
    stub = routeBase();
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" needsSetup />);
    expect(screen.getByLabelText('打开 AI 管家')).toBeTruthy();
    await user.click(screen.getByLabelText('打开 AI 管家'));
    expect(await screen.findByText('需要配置真实制作')).toBeTruthy();
    expect(await screen.findByText('帮我检查环境')).toBeTruthy();
    expect(screen.getByLabelText('开启联网检索（仅官方文档）')).toBeTruthy();
  });

  it('shows a plain-language build notice when the knowledge matches this version', async () => {
    stub = routeBase([
      {
        method: 'GET',
        pattern: /\/support\/capabilities\/actions/,
        respond: () => jsonResponse({
          capabilities: [{ action_id: 'setup_repair', params: ['capabilities'], scope: 'MACHINE' }],
          build: { state: 'FRESH', notice: 'AI 管家知识已匹配当前版本。', user_notice: 'AI 管家知识已匹配当前版本。' },
        }),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" needsSetup />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    expect(await screen.findByText('AI 管家知识已匹配当前版本。')).toBeTruthy();
  });

  it('explains a stale contract in user words without exposing hashes', async () => {
    stub = routeBase([
      {
        method: 'GET',
        pattern: /\/support\/capabilities\/actions/,
        respond: () => jsonResponse({
          capabilities: [],
          build: {
            state: 'STALE',
            notice: '合同指纹不一致 contract digest 0xabc',
            user_notice: '这个操作在当前版本已经调整，请重新确认。',
          },
        }),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" needsSetup />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    expect(await screen.findByText('这个操作在当前版本已经调整，请重新确认。')).toBeTruthy();
    expect(screen.queryByText(/0xabc/)).toBeNull();
  });

  it('asks a real diagnostic question and shows evidence plus offline notice', async () => {
    stub = routeBase();
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" projectId="p1" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    await user.click(await screen.findByText('帮我检查环境'));
    expect(await screen.findByText(/【结论】本机配置尚未就绪/)).toBeTruthy();
    const post = stub.calls.find((call) => call.method === 'POST' && /\/support\/sessions$/.test(call.url));
    expect(post?.body).toMatchObject({ page_id: 'setup', project_id: 'p1', question: '帮我检查环境' });
    expect(new Headers(post?.init?.headers).get('x-shine-local-action')).toBe('1');
    expect(screen.getByText(/智能解释暂不可用/)).toBeTruthy();
    await user.click(screen.getByText(/查看证据/));
    expect(screen.getByText('生产裁决')).toBeTruthy();
  });

  it('previews a repair, requires explicit approval and shows delegated progress', async () => {
    stub = routeBase([
      {
        method: 'POST',
        pattern: /\/support\/actions\/preview$/,
        respond: () => jsonResponse({
          action: {
            id: 'a1', kind: 'setup_repair', label: '补齐真实制作环境', status: 'PREVIEWED',
            preview_hash: 'hash-1',
            preview: {
              summary: '补齐固定清单中的缺失文件，并复用统一安装器的持久任务。',
              blocked: false, download_bytes: 1073741824, will_start_gpu: true,
              will_modify_user_comfyui: false,
              authorization_summary: '只下载缺失的固定文件，不会删除模型或修改节点源码。',
            },
            original_task_id: null,
            verification: {},
            executor: 'shine.comfyops.setup_preparation.enqueue',
          },
          reused: false,
        }),
      },
      {
        method: 'POST',
        pattern: /\/support\/actions\/a1\/approve$/,
        respond: () => jsonResponse({
          action: {
            id: 'a1', kind: 'setup_repair', label: '补齐真实制作环境', status: 'DELEGATED',
            preview_hash: 'hash-1', preview: { summary: '补齐固定清单中的缺失文件。' },
            original_task_id: 'setup-9', verification: { outcome: 'PENDING' },
          },
          reused: false,
        }),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    await user.click(await screen.findByText('帮我检查环境'));
    await user.click(await screen.findByText('预览补齐真实制作环境'));
    expect(await screen.findByText(/需要下载：1.00 GB/)).toBeTruthy();
    expect(screen.getByText(/修改你的 ComfyUI：不会/)).toBeTruthy();
    await user.click(screen.getByText('我确认执行'));
    await waitFor(() => expect(screen.getByText(/原执行器任务 setup-9/)).toBeTruthy());
    const approve = stub.calls.find((call) => /\/approve$/.test(call.url));
    expect(approve?.body).toMatchObject({ preview_hash: 'hash-1', confirm: true });
    expect(screen.getByText(/状态：已交给原执行器/)).toBeTruthy();
  });

  it('explains a refused action and lets the user preview again without leaving the page', async () => {
    let previews = 0;
    stub = routeBase([
      {
        method: 'POST',
        pattern: /\/support\/actions\/preview$/,
        respond: () => {
          previews += 1;
          return jsonResponse({
            action: {
              id: `a${previews}`, kind: 'setup_repair', label: '补齐真实制作环境',
              status: 'PREVIEWED', preview_hash: `hash-${previews}`,
              preview: {
                summary: '补齐固定清单中的缺失文件。', blocked: false,
                authorization_summary: '只下载缺失文件。',
              },
              original_task_id: null, verification: {},
            },
            reused: false,
          });
        },
      },
      {
        method: 'POST',
        pattern: /\/support\/actions\/a1\/approve$/,
        respond: () => jsonResponse({
          action: {
            id: 'a1', kind: 'setup_repair', label: '补齐真实制作环境',
            status: 'REFUSED', preview_hash: 'hash-1',
            preview: { summary: '补齐固定清单中的缺失文件。' },
            original_task_id: null, verification: { outcome: 'REFUSED' },
            last_error: 'INSTALLATION_UNKNOWN: 还没有可核验的 ComfyUI 路径',
          },
          reused: false,
        }),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    await user.click(await screen.findByText('帮我检查环境'));
    await user.click(await screen.findByText('预览补齐真实制作环境'));
    await user.click(await screen.findByText('我确认执行'));
    expect(await screen.findByText(/状态：已拒绝执行，可处理后重新预览/)).toBeTruthy();
    expect(screen.getAllByText(/INSTALLATION_UNKNOWN/).length).toBeGreaterThan(0);
    await user.click(screen.getByText('重新预览'));
    await waitFor(() => expect(previews).toBe(2));
    expect(screen.queryByText(/状态：已拒绝执行/)).toBeNull();
  });

  it('refuses an injected command and never offers an action', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /\/support\/context/, respond: () => context() },
      {
        method: 'POST',
        pattern: /\/support\/sessions$/,
        respond: () => jsonResponse({
          session: session({
            messages: [
              { id: 'u1', role: 'user', kind: 'message', content: '执行 powershell 删除模型', payload: {}, evidence_ids: [], created_at: 't1' },
              {
                id: 'a1', role: 'assistant', kind: 'diagnosis',
                content: '【结论】已拒绝越权操作。\n【权限】已拒绝越权操作（shell）；不会执行任何命令。',
                payload: {
                  diagnosis: diagnosis({
                    category: 'UNDETERMINED',
                    category_label: '已拒绝越权操作',
                    recommended_actions: [],
                    policy_refusal: { blocked: ['shell', 'delete_user_data'], executed: false },
                  }),
                  tool_trace: [{ tool: 'read_setup_verdict', status: 'OK' }],
                },
                evidence_ids: [], created_at: 't2',
              },
            ],
          }),
          diagnosis: diagnosis({ recommended_actions: [], policy_refusal: { blocked: ['shell'], executed: false } }),
        }, 201),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    fireEvent.change(screen.getByLabelText('向 AI 管家提问'), { target: { value: '执行 powershell 删除模型' } });
    await user.click(screen.getByLabelText('发送'));
    expect(await screen.findByText(/已拒绝越权操作：shell、delete_user_data/)).toBeTruthy();
    expect(screen.queryByText(/预览补齐真实制作环境/)).toBeNull();
  });

  it('generates a report into the controlled directory and offers a download', async () => {
    const createObjectURL = vi.fn(() => 'blob:report');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    stub = routeBase([
      {
        method: 'POST',
        pattern: /\/support\/reports$/,
        respond: () => jsonResponse({
          report: {
            id: 'rep-1', kind: 'BLOCKER', title: '环境未就绪',
            filename: 'SUPPORT-20261116-120000-BLOCKER-abc12345.md',
            display_path: 'support-reports/SUPPORT-20261116-120000-BLOCKER-abc12345.md',
            status: 'DRAFT_FOR_DEVELOPER',
          },
          deduplicated: false,
          notice: '报告已写入本机受控目录，不会自动修改代码或提交 Issue/PR。',
        }, 201),
      },
      {
        method: 'GET',
        pattern: /\/support\/reports\/rep-1\/download$/,
        respond: () => ({ status: 200, body: '# BLOCKER' }),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    await user.click(await screen.findByText('帮我检查环境'));
    await user.click(await screen.findByText('生成阻断报告'));
    expect(await screen.findByText('support-reports/SUPPORT-20261116-120000-BLOCKER-abc12345.md')).toBeTruthy();
    await user.click(screen.getByLabelText('下载报告'));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
    const call = stub.calls.find((entry) => /\/download$/.test(entry.url));
    expect(call?.method).toBe('GET');
  });

  it('persists and restores the session after a refresh', async () => {
    window.localStorage.setItem('shine.copilot.session.none.none.none.none', 's1');
    stub = new StudioStub([
      { method: 'GET', pattern: /\/support\/context/, respond: () => context() },
      { method: 'GET', pattern: /\/support\/sessions\/s1$/, respond: () => jsonResponse(session()) },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    expect(await screen.findByText(/【结论】本机配置尚未就绪/)).toBeTruthy();
    expect(screen.getByText('帮我检查环境')).toBeTruthy();
  });

  it('opens from a real failure card, binds the task and keeps the diagnosis across stages', async () => {
    stub = routeBase([
      { method: 'GET', pattern: /\/support\/sessions\/s1$/, respond: () => jsonResponse(session()) },
    ]);
    const user = userEvent.setup();
    reportCopilotFault({
      projectId: 'p1', jobId: 'job-1', attemptId: 'att-1', errorCode: 'OOM', label: '显存不足',
    });
    const first = render(<ShineCopilot pageId="produce" pageLabel="生产" projectId="p1" />);
    expect(await screen.findByText(/【结论】本机配置尚未就绪/)).toBeTruthy();
    const post = stub.callsOf('POST', /\/support\/sessions$/)[0];
    expect(post.body).toMatchObject({
      page_id: 'produce', project_id: 'p1', job_id: 'job-1', attempt_id: 'att-1', error_code: 'OOM',
    });
    expect(post.body).toMatchObject({ question: /显存不足/ });
    expect(window.localStorage.getItem('shine.copilot.session.p1.job-1.att-1.OOM')).toBe('s1');
    expect(screen.getByText(/已绑定/)).toBeTruthy();

    first.unmount();
    render(<ShineCopilot pageId="run" pageLabel="成片" projectId="p1" />);
    const fab = screen.queryByLabelText('打开 AI 管家');
    if (fab) await user.click(fab);
    expect(await screen.findByText(/【结论】本机配置尚未就绪/)).toBeTruthy();
    // Same failure, same session: switching the seven stages must not submit again.
    expect(stub.callsOf('POST', /\/support\/sessions$/)).toHaveLength(1);
    expect(stub.callsOf('GET', /\/support\/sessions\/s1$/).length).toBeGreaterThan(0);
  });

  it('keeps separate sessions for two different failures', async () => {
    stub = routeBase();
    reportCopilotFault({ projectId: 'p1', jobId: 'job-a', errorCode: 'OOM', label: '显存不足' });
    render(<ShineCopilot pageId="produce" pageLabel="生产" projectId="p1" />);
    expect(await screen.findByText(/【结论】/)).toBeTruthy();
    reportCopilotFault({ projectId: 'p1', jobId: 'job-b', errorCode: 'NODE_MISSING', label: '缺节点' });
    await waitFor(() => expect(stub.callsOf('POST', /\/support\/sessions$/).length).toBeGreaterThanOrEqual(2));
    const posts = stub.callsOf('POST', /\/support\/sessions$/);
    expect(posts.length).toBeGreaterThanOrEqual(2);
    const keys = posts.map((call) => (call.body as { job_id?: string }).job_id);
    expect(keys).toContain('job-a');
    expect(keys).toContain('job-b');
    expect(window.localStorage.getItem('shine.copilot.session.p1.job-a.none.OOM')).toBe('s1');
    expect(window.localStorage.getItem('shine.copilot.session.p1.job-b.none.NODE_MISSING')).toBe('s1');
  });

  it('toggles optional official-document search through the API', async () => {
    stub = routeBase([
      {
        method: 'POST',
        pattern: /\/support\/preferences$/,
        respond: () => jsonResponse({ web_search: true }),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    await user.click(await screen.findByLabelText('开启联网检索（仅官方文档）'));
    expect(await screen.findByText(/联网检索已开启/)).toBeTruthy();
    const call = stub.calls.find((entry) => entry.method === 'POST' && /\/support\/preferences$/.test(entry.url));
    expect(call?.body).toMatchObject({ web_search: true });
  });

  it('renders a bounded plan with knowledge citations and previews a plan step', async () => {
    const planned = session({
      messages: [
        { id: 'u1', role: 'user', kind: 'message', content: '帮我把第一集准备好', payload: {}, evidence_ids: [], created_at: 't1' },
        {
          id: 'a2', role: 'assistant', kind: 'diagnosis',
          content: '【结论】本机配置尚未就绪\n【操作计划】共 1 集：1 集可开拍。',
          payload: {
            diagnosis: diagnosis({ recommended_actions: [] }),
            intent: 'production',
            tool_trace: [
              { tool: 'search_project_knowledge', status: 'OK' },
              { tool: 'read_project_journey', status: 'OK' },
            ],
            project_knowledge: {
              answered: true,
              citations: [{
                title: '导演模式七阶段', status: '已实现', summary: '从原文到大纲、分镜与交付。',
                sources: ['backend/shine/director/overview.py'], verification: '有自动化测试',
              }],
              metadata: { state: 'UNKNOWN', notice: '当前构建未注入提交号，无法证明知识版本与当前代码一致。' },
            },
            plan: {
              intent: 'PRODUCTION',
              summary: '共 1 集：1 集可开拍，0 个前置步骤需要处理。',
              steps: [
                {
                  order: 1, title: '读取项目与每集真实状态', capability_id: 'read_project_journey',
                  state: 'READ_ONLY', state_label: '只读查询',
                  evidence: ['第一集：可开拍'], reason: '状态来自既有导演台账。',
                },
                {
                  order: 2, title: '开始真实拍摄选定集', capability_id: 'production_start',
                  state: 'READY', state_label: '可执行（仍需预览并确认）',
                  action: { kind: 'production_start', params: { project_id: 'p1', episode_ids: ['ep-1'] } },
                  reason: '只有原 Run 引擎能决定是否开拍。',
                },
              ],
              requires_input: [], notice: '计划只是只读建议；每个可执行步骤仍需单独预览并确认。', bounded: true,
            },
          },
          evidence_ids: [], created_at: 't2',
        },
      ],
    });
    stub = new StudioStub([
      { method: 'GET', pattern: /\/support\/context/, respond: () => context() },
      { method: 'POST', pattern: /\/support\/sessions$/, respond: () => jsonResponse({ session: planned, diagnosis: diagnosis({ recommended_actions: [] }) }, 201) },
      {
        method: 'POST',
        pattern: /\/support\/actions\/preview$/,
        respond: () => jsonResponse({
          action: {
            id: 'p-action', kind: 'production_start', label: '开始真实拍摄', status: 'PREVIEWED',
            preview_hash: 'hash-plan',
            preview: {
              summary: '把选中的集交给既有导演包与 Run 引擎开始真实拍摄。',
              blocked: false,
              episodes: [{ episode_id: 'ep-1', title: '第一集', state: 'READY_TO_SHOOT', state_label: '可开拍' }],
              authorization_summary: '确认后按原门禁真实开拍（占用 GPU 并生成视频）。',
            },
            original_task_id: null, verification: {},
          },
          reused: false,
        }),
      },
    ]);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="director" pageLabel="导演模式" projectId="p1" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    await user.click(await screen.findByText('帮我检查环境'));
    expect(await screen.findByText('操作计划')).toBeTruthy();
    expect(screen.getByText('开始真实拍摄选定集')).toBeTruthy();
    expect(screen.getByText('只读查询')).toBeTruthy();
    expect(screen.getByText('导演模式七阶段')).toBeTruthy();
    expect(screen.getByText(/当前构建未注入提交号/)).toBeTruthy();
    await user.click(screen.getByText('预览这一步'));
    expect(await screen.findByText(/涉及集数：1/)).toBeTruthy();
    const call = stub.calls.find((entry) => /\/support\/actions\/preview$/.test(entry.url));
    expect(call?.body).toMatchObject({ kind: 'production_start' });
  });

  it('allows dragging corner handle to resize width and height and double-click to reset', async () => {
    window.localStorage.removeItem('shine.copilot.panelSize.v1');
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 960 });
    const user = userEvent.setup();
    const { fireEvent } = await import('@testing-library/react');
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    const dialog = screen.getByRole('dialog', { name: /Shine AI 管家/ });
    expect(dialog.style.width).toBe('420px');
    expect(dialog.style.height).toBe('560px');

    const grip = screen.getByTestId('copilot-resize-nw');
    fireEvent.mouseDown(grip, { clientX: 800, clientY: 300 });
    fireEvent.mouseMove(window, { clientX: 680, clientY: 200 });
    fireEvent.mouseUp(window);
    expect(dialog.style.width).toBe('540px');
    expect(dialog.style.height).toBe('660px');
    expect(JSON.parse(window.localStorage.getItem('shine.copilot.panelSize.v1') || '{}')).toEqual({
      width: 540,
      height: 660,
    });

    fireEvent.doubleClick(grip);
    expect(dialog.style.width).toBe('420px');
    expect(dialog.style.height).toBe('560px');
  });
});

describe('action preview contracts', () => {
  const registry = (rows: CapabilityInfo[]) => Object.fromEntries(rows.map((row) => [row.action_id, row]));

  const capabilities = registry([
    { action_id: 'managed_release', params: ['only'], scope: 'MACHINE' },
    { action_id: 'project_create', params: ['description', 'language', 'title'], scope: 'MACHINE' },
    { action_id: 'document_import_text', params: ['filename', 'project_id', 'text'], scope: 'PROJECT' },
    { action_id: 'project_settings_update', params: ['language', 'project_id'], scope: 'PROJECT' },
    { action_id: 'story_outline_generate', params: ['document_id', 'locked_facts', 'pacing', 'project_id'], scope: 'PROJECT' },
    { action_id: 'storyboard_generate', params: ['plan_version', 'project_id'], scope: 'PROJECT' },
    { action_id: 'production_start', params: ['episode_ids', 'project_id', 'strategy', 'workflow_profile_id'], scope: 'PROJECT' },
    { action_id: 'document_switch_basis', params: ['document_id', 'project_id'], scope: 'PROJECT' },
    { action_id: 'production_control', params: ['operation', 'run_id'], scope: 'RUN' },
    { action_id: 'literary_scripts_generate', params: ['project_id', 'volume_id'], scope: 'PROJECT' },
    { action_id: 'workflow_user_copy', params: ['name', 'parameter_overrides', 'project_id', 'source_profile_id'], scope: 'PROJECT' },
    { action_id: 'setup_repair', params: ['capabilities', 'project_id', 'verify_runtime'], scope: 'PROJECT' },
  ]);

  it('never injects project_id into actions that do not accept it', () => {
    const release = actionPayload({ kind: 'managed_release', params: { only: ['comfy', 'replicate'] } }, capabilities, 'p1');
    expect(release).toEqual({ params: { only: ['comfy', 'replicate'] }, scopeProjectId: null });

    const control = actionPayload({ kind: 'production_control', params: { run_id: 'r1', operation: 'pause' } }, capabilities, 'p1');
    expect(control).toEqual({ params: { run_id: 'r1', operation: 'pause' }, scopeProjectId: null });
  });

  it('does not force the current project onto project_create', () => {
    const created = actionPayload({ kind: 'project_create', params: { title: '雾港回声' } }, capabilities, 'p1');
    expect(created.params).toEqual({ title: '雾港回声' });
    expect(created.scopeProjectId).toBeNull();
  });

  it('keeps an explicit target project instead of the active one', () => {
    const preview = actionPayload(
      { kind: 'project_settings_update', params: { language: 'en', project_id: 'p2' } },
      capabilities,
      'p1',
    );
    expect(preview.params.project_id).toBe('p2');
    expect(preview.scopeProjectId).toBe('p1');
  });

  it('inherits the current project only when the contract declares project_id', () => {
    const storyboard = actionPayload({ kind: 'storyboard_generate', params: {} }, capabilities, 'p1');
    expect(storyboard.params).toEqual({ project_id: 'p1' });
    expect(storyboard.scopeProjectId).toBe('p1');

    const release = actionPayload({ kind: 'managed_release', params: { only: [] } }, capabilities, 'p1');
    expect(release.params.project_id).toBeUndefined();
    expect(release.scopeProjectId).toBeNull();
  });

  it('drops fields the registry does not declare', () => {
    const preview = actionPayload(
      { kind: 'document_import_text', params: { project_id: 'p1', text: '正文', filename: 'a.txt', rogue: 'x' } },
      capabilities,
      'p1',
    );
    expect(preview.params).toEqual({ project_id: 'p1', text: '正文', filename: 'a.txt' });
  });
});
