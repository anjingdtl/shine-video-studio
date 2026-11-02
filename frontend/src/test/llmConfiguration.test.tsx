import { expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LLMConfiguration } from '../components/LLMConfiguration';

const emptyHandlers = () => ({
  onCreateProfile: vi.fn(), onUpdateProfile: vi.fn(), onTestProfile: vi.fn(),
  onSetKey: vi.fn(), onDeleteProfile: vi.fn(), onRoute: vi.fn(),
});

it('新建文本模型默认提供较充足的响应等待时间', async () => {
  const handlers = emptyHandlers();
  const user = userEvent.setup();
  render(<LLMConfiguration profiles={[]} routes={[]} busy={false} {...handlers} />);

  await user.click(screen.getByRole('button', { name: /添加/ }));
  expect(handlers.onCreateProfile).toHaveBeenCalledWith(expect.objectContaining({ timeout_s: 180 }));
});

it('允许在模型设置里用分钟选项延长响应等待', async () => {
  const handlers = emptyHandlers();
  const user = userEvent.setup();
  render(<LLMConfiguration profiles={[{
    id: 'profile-1', name: 'MiniMax 测试', kind: 'openai_compatible', base_url: 'https://example.invalid/v1',
    model: 'MiniMax-M3.1-Flash', has_api_key: true, capabilities: {}, timeout_s: 60, max_retries: 2,
    temperature: 0.2, enabled: true, created_at: '', updated_at: '',
  }]} routes={[]} busy={false} {...handlers} />);

  await user.click(screen.getByText('模型回复较慢？调整等待时间'));
  await user.selectOptions(screen.getByLabelText('最长等待时间 MiniMax 测试'), '180');
  await user.click(screen.getByRole('button', { name: '保存等待时间' }));
  expect(handlers.onUpdateProfile).toHaveBeenCalledWith('profile-1', { timeout_s: 180 });
});

it('模型配置正在保存或检查时禁用其他操作按钮', () => {
  const handlers = emptyHandlers();
  render(<LLMConfiguration profiles={[{
    id: 'profile-1', name: 'MiniMax 测试', kind: 'openai_compatible', base_url: 'https://example.invalid/v1',
    model: 'MiniMax-M3.1-Flash', has_api_key: true, capabilities: {}, timeout_s: 180, max_retries: 2,
    temperature: 0.2, enabled: true, created_at: '', updated_at: '',
  }]} routes={[]} busy {...handlers} />);

  expect(screen.getByRole('button', { name: '测试' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '用作剧本规划' })).toBeDisabled();
});

it('显示模型报告的长上下文窗口并允许清除偏小的固定回复上限', async () => {
  const handlers = emptyHandlers();
  const user = userEvent.setup();
  render(<LLMConfiguration profiles={[{
    id: 'profile-1', name: 'MiniMax 测试', kind: 'openai_compatible', base_url: 'https://api.minimax.cn/v1',
    model: 'MiniMax-M3.1-Flash', has_api_key: true,
    capabilities: { model_context: { model: 'MiniMax-M3.1-Flash', context_window_tokens: 1_000_000, source: 'minimax_official_m3_1_flash_1m' } },
    timeout_s: 180, max_retries: 2, temperature: 0.2, enabled: true, created_at: '', updated_at: '',
    generation_options: { max_completion_tokens: 4096, reasoning_effort: 'medium' },
  }]} routes={[]} busy={false} {...handlers} />);

  expect(screen.getByText(/输入上下文：1,000,000 tokens/)).toBeInTheDocument();
  await user.click(screen.getByText('调整结构化回复上限（可选）'));
  await user.clear(screen.getByLabelText('最大输出 token MiniMax 测试'));
  await user.click(screen.getByRole('button', { name: '保存回复上限' }));
  expect(handlers.onUpdateProfile).toHaveBeenCalledWith('profile-1', {
    generation_options: { reasoning_effort: 'medium' },
  });
});
