import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { MaterialPromptModal } from '../components/ShotEditors';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

const VIEW = {
  requirement_id: 'req-1', family: 'character', entity_id: 'Lin',
  description: '主角：三十多岁的女人',
  prompt: 'A character reference photograph for a cinematic narrative production: one person.',
  negative_prompt: 'no text',
  source: 'composed', blocked: null, frozen: null, warnings: [],
  standard: {
    standard_id: 'qwen-image-2.1-prompt-standard', standard_version: '1.0',
    layer_order: ['anchor', 'subject', 'environment', 'lighting', 'material', 'framing', 'closing'],
    quality_slogans: ['masterpiece', '8k'],
  },
};

it('素材提示词可以人工编辑、保存并冻结，规范建议不阻止保存', async () => {
  const puts: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/requirements\/req-1\/prompt$/, respond: () => jsonResponse(VIEW) },
    { method: 'PUT', pattern: /\/requirements\/req-1\/prompt$/, respond: (_url, init) => {
      puts.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({
        requirement_id: 'req-1',
        prompt: 'masterpiece, a woman in a navy coat.',
        negative_prompt: '',
        frozen: { sha256: 'd'.repeat(64) },
        warnings: ['提示词包含质量口号（无画面信息）：masterpiece'],
      });
    } },
  ]);
  const user = userEvent.setup();
  render(<MaterialPromptModal projectId="proj-1"
    requirement={{ requirement_id: 'req-1', name: 'Lin', family: 'character',
      description: '主角：三十多岁的女人' }}
    onClose={vi.fn()} />);

  const editor = await screen.findByLabelText(/正向提示词/);
  expect(editor).toHaveValue(VIEW.prompt);
  await user.clear(editor);
  await user.type(editor, 'masterpiece, a woman in a navy coat.');
  await user.click(screen.getByRole('button', { name: /保存并冻结/ }));

  await waitFor(() => expect(puts).toHaveLength(1));
  expect(puts[0]).toMatchObject({
    prompt: 'masterpiece, a woman in a navy coat.', negative_prompt: 'no text',
  });
  expect(await screen.findByText(/质量口号/)).toBeInTheDocument();
  expect(await screen.findByText(/提示词已冻结/)).toBeInTheDocument();
  stub.restore();
});
