/** D-R4-05：术语面板无数据时要有空态说明。 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const apiMock = vi.hoisted(() => ({
  api: vi.fn(async (_path: string) => [] as unknown[]),
}));

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, api: apiMock.api };
});

import TerminologyPanel from '../components/TerminologyPanel';

afterEach(() => { cleanup(); vi.clearAllMocks(); window.localStorage.clear(); });

it('术语面板无数据时说明现状与下一步', async () => {
  apiMock.api.mockResolvedValue([]);
  render(<TerminologyPanel projectId="proj-1" busy={false} onMessage={vi.fn()} />);
  expect(await screen.findByText(/还没有人名对照表/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /保存为新版本/ })).toBeInTheDocument();
});