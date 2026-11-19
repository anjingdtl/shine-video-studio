/** Direction 3: the card reconnects to the SAME grant after a closed panel,
 *  offers the installation choice once when several installs exist, and never
 *  previews a second authorization while one is live. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TaskGrantCard, type TaskGrant } from '../components/TaskGrantCard';
import { StudioStub, jsonResponse, type Route } from './studioStub';

const liveGrant = (overrides: Record<string, unknown> = {}): TaskGrant => ({
  grant_id: 'grant-live',
  objective: '准备本机 H3 环境',
  status: 'RUNNING',
  status_label: '自动执行中',
  grant_digest: 'b'.repeat(64),
  validity: { expires_at: '2026-11-20T00:00:00' },
  sub_actions: [],
  recent_tool_calls: [],
  ...overrides,
});

const preview = (overrides: Record<string, unknown> = {}) => ({
  preview_id: 'gp-1',
  schema: 'shine.copilot.task-grant.v1',
  grant_digest: 'c'.repeat(64),
  purpose: '准备本机 H3 环境',
  can_authorize: true,
  summary: '将在本次授权范围内自动检查与整备',
  capability_groups: ['inspect', 'resources'],
  capabilities: ['setup_reconcile'],
  blocked_capabilities: [],
  limits: { max_download_bytes: 1073741824, max_llm_tool_calls: 20 },
  risks: [],
  installation: { selected: { instance_id: 'inst-1', root: 'D:/ComfyUI' }, choice_required: false },
  model: { inherited: true, source_role: 'comfyops', model: 'local-text' },
  expires_at: '2026-11-20T00:00:00',
  preview_expires_at: '2026-11-19T09:00:00',
  not_allowed: ['修改源码'],
  ...overrides,
});

function stub(routes: Route[]): StudioStub {
  return new StudioStub(routes);
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('durable grant card', () => {
  it('reconnects to a live grant without previewing a second authorization', async () => {
    const s = stub([
      { method: 'GET', pattern: /\/support\/task-grants\/grant-live$/, respond: () => jsonResponse(liveGrant()) },
      { method: 'POST', pattern: /\/support\/task-grants\/grant-live\/run/, respond: () => jsonResponse({ status: 'RUNNING', stop_reason: 'LOOP_ALREADY_RUNNING', final: '已接回同一任务。', steps: [] }) },
    ]);
    render(<TaskGrantCard sessionId="s1" objective="准备本机 H3 环境" initialGrant={liveGrant()} />);
    expect((await screen.findAllByText('自动执行中')).length).toBeGreaterThan(0);
    expect(screen.getByText('继续推进')).toBeTruthy();
    expect(s.callsOf('POST', /task-grants\/preview/).length).toBe(0);
    await userEvent.click(screen.getByText('继续推进'));
    await waitFor(() => {
      expect(s.callsOf('POST', /task-grants\/grant-live\/run/).length).toBe(1);
    });
    expect(s.callsOf('POST', /task-grants\/preview/).length).toBe(0);
  });

  it('shows the installation choice once and re-previews with the selected id', async () => {
    const s = stub([
      {
        method: 'POST', pattern: /\/support\/task-grants\/preview/,
        respond: (_url, init) => {
          let body: Record<string, unknown> = {};
          try { body = JSON.parse(String(init?.body || '{}')); } catch { /* keep empty */ }
          if (body.installation_id === 'inst-a') {
            return jsonResponse(preview({
              installation: { selected: { instance_id: 'inst-a', root: 'D:/ComfyUI-A' }, choice_required: false },
            }));
          }
          return jsonResponse(preview({
            can_authorize: false,
            selection_required: ['INSTALLATION_SELECTION_REQUIRED'],
            installation: {
              selected: null,
              choice_required: true,
              candidates: [
                { instance_id: 'inst-a', root: 'D:/ComfyUI-A', managed: true, adopted: false },
                { instance_id: 'inst-b', root: 'E:/ComfyUI-B', managed: false, adopted: true },
              ],
            },
          }));
        },
      },
    ]);
    const user = userEvent.setup();
    render(<TaskGrantCard sessionId="s1" objective="准备本机 H3 环境" />);
    expect(await screen.findByText('发现多套安装，请先选定一套（只选一次，随后冻结进授权）')).toBeTruthy();
    const authorize = screen.getByText('授权 AI 管家完成本次任务') as HTMLButtonElement;
    expect(authorize.disabled).toBe(true);
    await user.click(screen.getByText('D:/ComfyUI-A'));
    await waitFor(() => {
      const calls = s.calls.filter((c) => c.method === 'POST' && /task-grants\/preview/.test(c.url));
      expect(calls.length).toBe(2);
      expect((calls[1].body as Record<string, unknown>).installation_id).toBe('inst-a');
      // A JSON body without the JSON content-type is parsed as a form by the
      // real server (422 model_type) — the stub alone never caught that.
      expect(new Headers(calls[1].init?.headers).get('Content-Type')).toBe('application/json');
    });
    await waitFor(() => {
      expect((screen.getByText('授权 AI 管家完成本次任务') as HTMLButtonElement).disabled).toBe(false);
    });
  });
});
