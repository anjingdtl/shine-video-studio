/** The global AI copilot must be a real connected surface: evidence, explicit
 *  preview/approval, report hand-off and an honest offline notice.  These tests
 *  also pin that dangerous requests are refused, not executed. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ShineCopilot } from '../components/ShineCopilot';
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
    expect(screen.getByText(/状态：DELEGATED/)).toBeTruthy();
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
    window.localStorage.setItem('shine.copilot.session.setup.none', 's1');
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
    const call = stub.calls.find((entry) => /\/support\/preferences$/.test(entry.url));
    expect(call?.body).toMatchObject({ web_search: true });
  });
});
