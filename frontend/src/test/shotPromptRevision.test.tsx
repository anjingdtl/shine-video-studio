import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ShotPromptModal } from '../components/ShotEditors';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

const PREVIEW = {
  shot_id: 'shot-1', shot_revision: 2, duration_target_s: 6,
  characters: [], dialogue: [], has_bindings: false,
  mode: 'T2VA',
  prompt: 'integrated_multimodal_description:\nA woman waits by the window.',
  sections: { integrated_multimodal_description: 'A woman waits by the window.' },
  prompt_sha256: 'a'.repeat(64), labels: {}, warnings: [], compiler_version: 'shine-h3-compiler/1.2.3',
  frozen: null,
};

it('人工修订可以保存并冻结，冻结后提交的正文就是编辑框里的文本', async () => {
  const posts: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/shots\/shot-1\/prompt$/, respond: () => jsonResponse(PREVIEW) },
    { method: 'POST', pattern: /\/shots\/shot-1\/prompt$/, respond: (_url, init) => {
      posts.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({
        shot_id: 'shot-1', revision: 1, shot_revision: 3, status: 'FROZEN',
        prompt_sha256: 'b'.repeat(64), sections: {}, warnings: [],
      });
    } },
    { method: 'GET', pattern: /\/shots\/shot-1\/prompt/, respond: () => jsonResponse(PREVIEW) },
  ]);
  const user = userEvent.setup();
  render(<ShotPromptModal projectId="proj-1"
    shot={{ shot_id: 'shot-1', name: '第一镜', revision: 2, duration_target_s: 6 }}
    onClose={vi.fn()} />);

  const editor = await screen.findByLabelText(/提示词（冻结后原样提交/);
  expect(editor).toHaveValue(PREVIEW.prompt);
  await user.clear(editor);
  await user.type(editor, 'integrated_multimodal_description: A hand-written shot.');
  await user.click(screen.getByRole('button', { name: /保存并冻结/ }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    freeze: true, mode: 'T2VA', expected_revision: 2,
    prompt: 'integrated_multimodal_description: A hand-written shot.',
  });
  expect(await screen.findByText(/已冻结第 1 版/)).toBeInTheDocument();
  stub.restore();
});
