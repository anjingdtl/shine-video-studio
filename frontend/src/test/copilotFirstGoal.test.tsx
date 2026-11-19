/** Direction 2: the FIRST natural-language objective enters the one-click grant
 *  flow without buying a diagnosis turn first, and a restored session never
 *  swallows a NEW fault — it rebinds and diagnoses again. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ShineCopilot } from '../components/ShineCopilot';
import { clearCopilotFault, reportCopilotFault } from '../copilotContext';
import { StudioStub, jsonResponse, type Route } from './studioStub';

const verdict = { mode: 'READY', label: '真实制作已就绪', code: null };

const context = () => jsonResponse({
  snapshot: { setup: { verdict }, evidence_id: 'ev-1', provenance: [] },
  suggested_questions: [],
  web_search_enabled: false,
});

const session = (overrides: Record<string, unknown> = {}) => ({
  id: 's1',
  page_id: 'run',
  project_id: null,
  expires_at: '2026-12-19T00:00:00',
  created_at: '2026-11-19T00:00:00',
  updated_at: '2026-11-19T00:00:00',
  context: { snapshot: { setup: { verdict } } },
  messages: [],
  ...overrides,
});

const grantPreview = () => jsonResponse({
  preview_id: 'gp1',
  schema: 'shine.copilot.task-grant.v1',
  grant_digest: 'd'.repeat(64),
  purpose: '给我的 H3 配齐加速 LoRA 并真实验收',
  can_authorize: true,
  summary: '将在本次授权范围内自动检查与修复。',
  capability_groups: ['inspect', 'resources', 'nodes', 'workflows', 'runtime', 'test'],
  capabilities: ['setup_repair'],
  blocked_capabilities: [],
  limits: { max_download_bytes: 200000000000, max_llm_tool_calls: 60 },
  risks: ['DISK_WRITE'],
  installation: { selected: null, choice_required: false },
  model: { inherited: true, source_role: 'comfyops', model: 'inherited-model' },
  expires_at: '2026-11-20T00:00:00',
  preview_expires_at: '2026-11-19T00:30:00',
  not_allowed: ['修改源码'],
});

const diagnosisPayload = () => ({
  session: session(),
  message: {
    id: 'm1', role: 'assistant', kind: 'diagnosis', content: '【结论】已重新诊断。',
    payload: { diagnosis: { category: 'OK', facts: [], recommended_actions: [] }, page: {} },
    evidence_ids: [], created_at: '2026-11-19T00:00:01',
  },
  diagnosis: { category: 'OK', facts: [], recommended_actions: [] },
});

const routeBase = (extra: Route[] = []) => new StudioStub([
  { method: 'GET', pattern: /\/support\/context/, respond: () => context() },
  { method: 'POST', pattern: /\/support\/sessions$/, respond: () => jsonResponse({ session: session(), diagnosis: null }, 201) },
  { method: 'GET', pattern: /\/support\/sessions\/s1$/, respond: () => jsonResponse(session()) },
  { method: 'POST', pattern: /\/support\/sessions\/s1\/messages/, respond: () => jsonResponse(diagnosisPayload()) },
  { method: 'POST', pattern: /\/support\/task-grants\/preview/, respond: () => grantPreview() },
  ...extra,
]);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearCopilotFault();
  window.localStorage.clear();
});

describe('first objective and new faults', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('enters the grant preview from the first objective without a prior diagnosis', async () => {
    const stub = routeBase();
    const user = userEvent.setup();
    render(<ShineCopilot pageId="setup" pageLabel="基础配置" />);
    await user.click(screen.getByLabelText('打开 AI 管家'));
    const input = await screen.findByLabelText('向 AI 管家提问');
    await user.type(input, '给我的 H3 配齐加速 LoRA 并真实验收');
    const button = await screen.findByText('一次授权，让 AI 自动完成这件事');
    await user.click(button);
    await waitFor(() => {
      expect(stub.callsOf('POST', /\/support\/task-grants\/preview/).length).toBe(1);
    });
    const created = stub.callsOf('POST', /\/support\/sessions$/);
    expect(created.length).toBe(1);
    expect((created[0].body as Record<string, unknown>).question).toBe('');
    const preview = stub.callsOf('POST', /\/support\/task-grants\/preview/)[0];
    expect((preview.body as Record<string, unknown>).objective).toBe('给我的 H3 配齐加速 LoRA 并真实验收');
    expect(await screen.findByText('授权 AI 管家完成本次任务')).toBeTruthy();
  });

  it('a restored session rebinds to the new fault instead of swallowing it', async () => {
    window.localStorage.setItem('shine.copilot.session.none.job1.none.CODE_A', 's1');
    const stub = routeBase();
    render(<ShineCopilot pageId="run" pageLabel="视频生产" />);
    reportCopilotFault({ jobId: 'job1', errorCode: 'CODE_A', label: '渲染失败' });
    await waitFor(() => {
      expect(stub.callsOf('POST', /\/support\/sessions\/s1\/messages/).length).toBe(1);
    });
    const body = stub.callsOf('POST', /\/support\/sessions\/s1\/messages/)[0].body as Record<string, unknown>;
    expect(body.job_id).toBe('job1');
    expect(body.error_code).toBe('CODE_A');
    expect(String(body.content)).toContain('诊断');
  });
});
