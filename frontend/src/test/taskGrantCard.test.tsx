/** The V3.1 authorization card: one boundary, one click, honest status.
 *
 *  These tests pin the parts a person actually depends on: the boundary is
 *  shown before the button, the button sends the *previewed* digest, a blocked
 *  preview cannot be authorized, and an execution without an original receipt
 *  is never rendered as success.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TaskGrantCard, formatBytes, stageOf } from '../components/TaskGrantCard';
import { StudioStub, jsonResponse, type Route } from './studioStub';

const preview = (overrides: Record<string, unknown> = {}) => ({
  preview_id: 'grantpreview-abc',
  schema: 'shine.copilot.task-grant.v1',
  grant_digest: 'a'.repeat(64),
  purpose: '为当前项目装配并验证 H3 高级工作流',
  can_authorize: true,
  summary: '将在本次授权范围内自动检查与整备',
  capability_groups: ['inspect', 'resources', 'workflows'],
  capabilities: ['setup_reconcile', 'resource_install_plan', 'workflow_graph_register'],
  blocked_capabilities: [],
  limits: {
    max_download_bytes: 1073741824,
    max_new_workflow_profiles: 3,
    max_llm_tool_calls: 20,
  },
  risks: ['DISK_WRITE', 'THIRD_PARTY_CODE'],
  installation: { selected: { instance_id: 'inst-1', root: 'D:/ComfyUI' }, choice_required: false },
  model: { inherited: true, source_role: 'comfyops', profile_name: '本地模型', model: 'local-text' },
  expires_at: '2026-11-20T00:00:00',
  preview_expires_at: '2026-11-19T09:00:00',
  not_allowed: ['修改 Shine 或第三方源码', '执行任意 Shell / pip / git / 安装脚本'],
  ...overrides,
});

const grant = (overrides: Record<string, unknown> = {}) => ({
  grant_id: 'grant-abc',
  objective: '为当前项目装配并验证 H3 高级工作流',
  status: 'SUCCEEDED',
  status_label: '已成功',
  grant_digest: 'a'.repeat(64),
  validity: { expires_at: '2026-11-20T00:00:00' },
  sub_actions: [
    { action_id: 'act-1', tool_call_id: 'call-1', capability_id: 'workflow_graph_register', original_task_id: null },
  ],
  recent_tool_calls: [],
  ...overrides,
});

function stub(routes: Route[]): StudioStub {
  return new StudioStub(routes);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('task grant card', () => {
  it('shows the boundary before the single button', async () => {
    stub([
      { method: 'POST', pattern: /\/support\/task-grants\/preview$/, respond: () => jsonResponse(preview()) },
    ]);
    render(<TaskGrantCard sessionId="s1" projectId="p1" objective="装配 H3 高级工作流" />);

    expect(await screen.findByText('授权后 AI 会自动做的事')).toBeTruthy();
    expect(screen.getByText('只读检查本机已有资源')).toBeTruthy();
    expect(screen.getByText(/安装位置：D:\/ComfyUI/)).toBeTruthy();
    expect(screen.getByText(/最大下载体积：1 GB/)).toBeTruthy();
    expect(screen.getByText(/会引入第三方节点代码/)).toBeTruthy();
    expect(screen.getByText('修改 Shine 或第三方源码')).toBeTruthy();
    expect(screen.getByText(/继承自「comfyops」/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /授权 AI 管家完成本次任务/ })).toBeTruthy();
  });

  it('sends the previewed digest when the person authorizes', async () => {
    const stubber = stub([
      { method: 'POST', pattern: /\/support\/task-grants\/preview$/, respond: () => jsonResponse(preview()) },
      {
        method: 'POST',
        pattern: /\/support\/task-grants\/authorize$/,
        respond: () => jsonResponse({ grant_id: 'grant-abc', created: true, status: 'AUTHORIZED' }),
      },
      {
        method: 'POST',
        pattern: /\/support\/task-grants\/grant-abc\/run$/,
        respond: () => jsonResponse({
          status: 'SUCCEEDED',
          final: '工作流已注册',
          steps: [{ capability_id: 'workflow_graph_register', status: 'EXECUTED', satisfied: true }],
        }),
      },
      { method: 'GET', pattern: /\/support\/task-grants\/grant-abc$/, respond: () => jsonResponse(grant()) },
    ]);
    render(<TaskGrantCard sessionId="s1" objective="装配 H3 高级工作流" />);
    const button = await screen.findByRole('button', { name: /授权 AI 管家完成本次任务/ });
    fireEvent.click(button);

    await waitFor(() => expect(screen.getAllByText('已成功').length).toBeGreaterThan(0));
    const authorize = stubber.calls.find((call) => call.url.endsWith('/authorize'));
    expect(authorize).toBeTruthy();
    expect(authorize!.body).toMatchObject({
      session_id: 's1',
      preview_id: 'grantpreview-abc',
      grant_digest: 'a'.repeat(64),
      confirm: true,
      run: false,
    });
  });

  it('cannot authorize a blocked preview', async () => {
    stub([
      {
        method: 'POST',
        pattern: /\/support\/task-grants\/preview$/,
        respond: () => jsonResponse(preview({ can_authorize: false, blocked_capabilities: [{ capability_id: 'workflow_graph_register', state: 'CONTRACT_STALE' }] })),
      },
    ]);
    render(<TaskGrantCard sessionId="s1" objective="装配 H3" />);
    const button = await screen.findByRole('button', { name: /授权 AI 管家完成本次任务/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/以下能力当前不可由 AI 执行/)).toBeTruthy();
  });

  it('shows technical details only when asked', async () => {
    stub([
      { method: 'POST', pattern: /\/support\/task-grants\/preview$/, respond: () => jsonResponse(preview()) },
      {
        method: 'POST',
        pattern: /\/support\/task-grants\/authorize$/,
        respond: () => jsonResponse({ grant_id: 'grant-abc', created: true }),
      },
      {
        method: 'POST',
        pattern: /\/support\/task-grants\/grant-abc\/run$/,
        respond: () => jsonResponse({ status: 'SUCCEEDED', steps: [] }),
      },
      { method: 'GET', pattern: /\/support\/task-grants\/grant-abc$/, respond: () => jsonResponse(grant()) },
    ]);
    render(<TaskGrantCard sessionId="s1" objective="装配 H3" />);
    fireEvent.click(await screen.findByRole('button', { name: /授权 AI 管家完成本次任务/ }));
    await waitFor(() => expect(screen.getAllByText('已成功').length).toBeGreaterThan(0));
    expect(screen.queryByText('授权摘要')).toBeNull();
    fireEvent.click(screen.getByText('技术细节'));
    expect(await screen.findByText('授权摘要')).toBeTruthy();
    expect(screen.getByText('grant-abc')).toBeTruthy();
  });
});

describe('grant stage vocabulary', () => {
  it('maps server states onto the six user-facing stages', () => {
    expect(stageOf({ previewing: true }).label).toBe('正在理解目标');
    expect(stageOf({ authorized: false }).label).toBe('等待一次授权');
    expect(stageOf({ authorized: true, status: 'AUTHORIZED' }).label).toBe('正在调查环境');
    expect(stageOf({ authorized: true, status: 'RUNNING' }).label).toBe('自动执行中');
    expect(
      stageOf({ authorized: true, status: 'SUCCEEDED', steps: [{ satisfied: true }] }).label,
    ).toBe('已成功');
    // A "succeeded" status without an original receipt is not a success.
    expect(stageOf({ authorized: true, status: 'SUCCEEDED', steps: [] }).label).toBe('正在复核');
    expect(stageOf({ authorized: true, status: 'PAUSED_SCOPE' }).label).toBe('存在阻滞');
    expect(stageOf({ authorized: true, status: 'REVOKED' }).label).toBe('已停止后续 AI 操作');
  });

  it('formats sizes for the limits list', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(1024 * 1024 * 1024)).toBe('1 GB');
  });
});
