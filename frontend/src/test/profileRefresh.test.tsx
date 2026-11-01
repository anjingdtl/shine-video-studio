/** Counterexample (audit 2026-11-01 #2, frontend half): after the wizard
 *  registered the default workflow profiles, the template lists on the current
 *  page (queue pickup + production series) did not update — the user had to
 *  guess at extra refresh steps.  The backend scoping fix is covered by
 *  backend/tests/test_workflow_profile_scope.py; this test pins the frontend
 *  contract that registration immediately refreshes the shared list. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

const PROFILE = {
  id: 'profile-1', project_id: 'proj-1', name: 'local-h3-ref2va-pdd8-2img-1aud', mode: 'Ref2VA',
  graph_sha256: 'a'.repeat(64), parameter_map: {}, required_nodes: [], checkpoint_id: 'MiniMax-H3-Base',
  validated_against: null, created_at: '2026-11-01T00:00:00',
};

describe('登记工作流 Profile 后当前页面模板即时更新', () => {
  it('点击登记后重新读取工作流列表，模板下拉出现新档案', async () => {
    const user = userEvent.setup();
    let registered = false;
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/setup\/profiles$/,
      respond: () => {
        registered = true;
        return jsonResponse({ profiles: [{ name: PROFILE.name, status: 'REGISTERED', profile_id: PROFILE.id }] });
      },
    });
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/workflow-profiles/,
      respond: () => jsonResponse(registered ? [PROFILE] : []),
    });

    render(<App />);
    await screen.findAllByText(/当前制片项目/, {}, { timeout: 3000 });
    const before = stub.calls.filter((call) => call.method === 'GET' && /workflow-profiles/.test(call.url)).length;

    // Open the setup section through the real navigation.
    await user.click(await screen.findByTitle('打开设置板块'));
    await user.click(await screen.findByRole('button', { name: '登记工作流 Profile' }));

    await waitFor(() => {
      expect(screen.getByText(/REGISTERED/)).toBeInTheDocument();
    }, { timeout: 4000 });

    // The registration refreshed the shared workflow-profile state.
    const after = stub.calls.filter((call) => call.method === 'GET' && /workflow-profiles/.test(call.url)).length;
    expect(after).toBeGreaterThan(before);

    // Back in the production section, the queue template select lists it.
    await user.click(await screen.findByTitle('打开生产板块'));
    await user.selectOptions(await screen.findByLabelText(/生成方式/), 'comfyui');
    await waitFor(() => {
      expect(screen.getByLabelText(/生成模板/).textContent).toContain('local-h3-ref2va-pdd8-2img-1aud');
    });
  });
});
