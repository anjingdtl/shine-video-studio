import { afterEach, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import StoryboardPanel from '../components/StoryboardPanel';
import { StudioStub, jsonResponse } from './studioStub';

it('锁定剧情事实可以逐行输入并完整发送给分镜流程', async () => {
  const submit = vi.fn<(body: Record<string, unknown>) => void>();
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/projects\/proj-1\/storyboard\/generate$/, respond: (_url, init) => {
      submit(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ status: 'BLOCKED_MISSING_MASTERS', script_source: 'inline', steps: {}, missing_characters: ['林澈'] });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '三幕测试剧本');
  const facts = screen.getByLabelText('锁定剧情事实（每行一条，可选）');
  await user.type(facts, '信封始终未拆封\n账册第十七页缺失');
  expect(facts).toHaveValue('信封始终未拆封\n账册第十七页缺失');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));

  await waitFor(() => expect(stub.callsOf('POST', /storyboard\/generate$/)).toHaveLength(1));
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({
    locked_story_facts: ['信封始终未拆封', '账册第十七页缺失'],
  }));
  expect(await screen.findByRole('status')).toHaveTextContent(/这些人物没有匹配到唯一的已确认人物素材/);
  stub.restore();
});

it('对白语言保存到项目，并说明它会进入 H3 提示词', async () => {
  const saved: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/projects\/proj-1$/, respond: () => jsonResponse({ id: 'proj-1', language: 'Chinese' }) },
    { method: 'PATCH', pattern: /\/projects\/proj-1\/settings$/, respond: (_url, init) => {
      saved.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ language: 'Japanese' });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('对白语言')).toHaveValue('Chinese'));
  await user.selectOptions(screen.getByLabelText('对白语言'), 'Japanese');

  await waitFor(() => expect(saved).toEqual([{ language: 'Japanese' }]));
  expect(await screen.findByRole('status')).toHaveTextContent(/对白语言已设为/);
  stub.restore();
});

it('对白语言保存失败时如实显示失败，并保留已选语言', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/projects\/proj-1$/, respond: () => jsonResponse({ id: 'proj-1', language: 'Chinese' }) },
    { method: 'PATCH', pattern: /\/projects\/proj-1\/settings$/, respond: () => jsonResponse({ detail: 'boom' }, 500) },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('对白语言')).toHaveValue('Chinese'));
  await user.selectOptions(screen.getByLabelText('对白语言'), 'Korean');

  expect(await screen.findByText(/保存失败/)).toBeInTheDocument();
  expect(screen.getByLabelText('对白语言')).toHaveValue('Korean');
  stub.restore();
});

it('项目语言不在可选集合内时显示默认语言而不是空白', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/projects\/proj-1$/, respond: () => jsonResponse({ id: 'proj-1', language: '火星语' }) },
  ]);
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('对白语言')).toHaveValue('Chinese'));
  stub.restore();
});

it('仅有一份已导入剧本时在进入分镜页后自动选中', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([{
      id: 'doc-1', filename: 'rainy-night-page-17.txt', sha256: 'a'.repeat(64),
      source_format: 'text', text_preview: '三幕测试剧本', text_length: 547, created_at: '2026-11-02T00:00:00Z',
    }]) },
  ]);
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText('或选择已导入剧本')).toHaveValue('doc-1'));
  expect(screen.queryByLabelText('粘贴完整剧本文本')).not.toBeInTheDocument();
  stub.restore();
});

it('远端结果未知时说明不自动重发并锁住同版剧本', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/projects\/proj-1\/storyboard\/generate$/, respond: () => jsonResponse({
      status: 'REJECTED', script_source: 'inline', steps: {},
      rejections: [{ role: 'chain', code: 'LLM_TIMEOUT' }],
    }) },
  ]);
  const user = userEvent.setup();
  render(<StoryboardPanel projectId="proj-1" onRefresh={vi.fn()} />);

  await user.type(screen.getByLabelText('粘贴完整剧本文本'), '三幕测试剧本');
  await user.click(screen.getByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: /生成 AI 分镜方案/ }));

  expect(await screen.findByText(/远端结果未确认/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '先核实前次请求' })).toBeDisabled();
  expect(screen.queryByText(/请复核后重试/)).not.toBeInTheDocument();
  stub.restore();
});

afterEach(() => vi.unstubAllGlobals());
