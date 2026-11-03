import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }));
vi.mock('../api', () => ({
  api: apiMock,
  json: (method: string, body: unknown) => ({
    method,
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' },
  }),
}));

import FilmCrewPanel from '../components/FilmCrewPanel';

function stubRoutes() {
  apiMock.mockReset();
  apiMock.mockImplementation((path: string) => {
    if (path === '/film-agents') return Promise.resolve([]);
    if (path === '/h3-rulebook') {
      return Promise.resolve({ packs: [], sources: [], rules: [], local_workflow_capabilities: [] });
    }
    if (path.endsWith('/rulepack')) return Promise.resolve({ rulepack_id: null });
    if (path.endsWith('/film-runs')) {
      return Promise.resolve([{
        id: 'run-unknown', role_id: 'director', status: 'NEEDS_REVIEW',
        model_id: 'm', error_code: 'LLM_TIMEOUT', rulepack_id: '', trace: {},
      }]);
    }
    if (path.endsWith('/film-artifacts')) return Promise.resolve([]);
    if (path.endsWith('/preparation/approval-grants')) {
      return Promise.resolve({ grant_id: 'g-1', snapshot: 's-1', expires_at: 'soon' });
    }
    return Promise.resolve({});
  });
}

it('个人桌面无口令：空凭据加说明即可弃置远端未知响应', async () => {
  stubRoutes();
  const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
  const user = userEvent.setup();
  render(<FilmCrewPanel projectId="p1" profiles={[]} shots={[]} onRefresh={async () => undefined} />);
  const btn = await screen.findByRole('button', { name: '放弃远端未知响应（不重试）' });
  await user.type(screen.getByLabelText('远端响应对账说明'), '本地会话结束，远端结果未知，显式弃置接收');
  expect(btn).toBeEnabled();
  await user.click(btn);
  expect(confirmSpy).toHaveBeenCalledTimes(1);
  await waitFor(() => {
    const grantCall = apiMock.mock.calls.find(c => String(c[0]).endsWith('/preparation/approval-grants'));
    const abandonCall = apiMock.mock.calls.find(c => String(c[0]).endsWith('/abandon-remote-response'));
    expect(grantCall).toBeTruthy();
    expect(abandonCall).toBeTruthy();
    const body = JSON.parse(String(abandonCall?.[1]?.body));
    expect(Object.keys(body).sort()).toEqual(['evidence', 'grant_id', 'snapshot']);
  });
  confirmSpy.mockRestore();
});

it('凭据 1 到 31 字符仍被拒绝', async () => {
  stubRoutes();
  const user = userEvent.setup();
  render(<FilmCrewPanel projectId="p1" profiles={[]} shots={[]} onRefresh={async () => undefined} />);
  const btn = await screen.findByRole('button', { name: '放弃远端未知响应（不重试）' });
  expect(btn).toBeDisabled();
  await user.type(screen.getByLabelText('远端响应对账说明'), '测试说明');
  await user.type(screen.getByLabelText('远端响应对账凭据'), 'short');
  expect(btn).toBeDisabled();
});
