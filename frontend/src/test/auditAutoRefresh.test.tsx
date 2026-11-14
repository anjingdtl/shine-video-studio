/** D-R4-06：进入交付视图自动读一次审计，不必先手动点刷新。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from '../App';
import { StudioStub, baseProject, baseRoutes } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  window.history.replaceState(null, '', '#');
  stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '雾港夜账' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

it('切到审计追踪时自动再读一次操作记录', async () => {
  const user = userEvent.setup();
  render(<App />);
  await screen.findAllByText('雾港夜账');
  await waitFor(() => expect(stub.callsOf('GET', /\/projects\/proj-1\/events/).length).toBeGreaterThan(0));
  const before = stub.callsOf('GET', /\/projects\/proj-1\/events/).length;

  await user.click(screen.getByRole('button', { name: /专业模式/ }));
  await user.click(screen.getAllByRole('button', { name: /交付板块/ })[0]);

  await waitFor(() => expect(stub.callsOf('GET', /\/projects\/proj-1\/events/).length).toBeGreaterThan(before));
});