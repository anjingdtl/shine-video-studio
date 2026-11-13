import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ShotSubtitleModal } from '../components/ShotEditors';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

it('每镜字幕按镜内时间保存并冻结，走外挂字幕轨而不是 H3 提示词', async () => {
  const puts: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'PUT', pattern: /\/shots\/shot-1\/subtitles$/, respond: (_url, init) => {
      puts.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({
        shot_id: 'shot-1', episode_id: 'e1', revision: 1, status: 'FROZEN',
        lines: [], sha256: 'c'.repeat(64), frozen_at: null,
      });
    } },
  ]);
  const user = userEvent.setup();
  render(<ShotSubtitleModal projectId="proj-1"
    shot={{ shot_id: 'shot-1', name: '第一镜', duration_target_s: 6 }}
    initialLines={[{ start_s: 0, end_s: 2, text: '第一行' }]}
    onClose={vi.fn()} />);

  expect(screen.getByLabelText('第 1 行字幕')).toHaveValue('第一行');
  await user.click(screen.getByRole('button', { name: /添加一行/ }));
  await user.type(screen.getByLabelText('第 2 行字幕'), '第二行');
  await user.click(screen.getByRole('button', { name: /保存并冻结/ }));

  await waitFor(() => expect(puts).toHaveLength(1));
  expect(puts[0]).toMatchObject({ freeze: true });
  expect((puts[0].lines as Array<Record<string, unknown>>)).toEqual([
    { start_s: 0, end_s: 2, text: '第一行' },
    { start_s: 0, end_s: 3, text: '第二行' },
  ]);
  expect(await screen.findByText(/它属于外挂字幕轨/)).toBeInTheDocument();
  stub.restore();
});
