/** LLM 准备的写入与刷新是两个结果：
 *  写入成功但刷新失败时，界面必须说“已保存”，不能诱导用户重复添加；
 *  密钥草稿只在服务端确认后清空；文本工种一次事务绑定。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LLMConfiguration } from '../components/LLMConfiguration';
import { LLMSetupPanel } from '../components/LLMSetupPanel';
import { StudioStub, jsonResponse } from './studioStub';

const PROFILE = {
  id: 'profile-1', name: '本地文本模型', kind: 'openai_compatible',
  base_url: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:7b',
  has_api_key: true, secret_status: 'stored', capabilities: {},
  timeout_s: 180, max_retries: 2, temperature: 0.2, enabled: true,
  created_at: '', updated_at: '',
};

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub([
    { method: 'GET', pattern: /\/api\/v1\/provider-profiles$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/agent-routes$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/llm-readiness$/, respond: () => jsonResponse({ status: 'BLOCKED', roles: [] }) },
  ]);
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

describe('LLM 写入与状态刷新是两个结果', () => {
  it('写入成功但刷新失败时显示已保存，不诱导重复添加', async () => {
    const user = userEvent.setup();
    stub.add({ method: 'POST', pattern: /\/api\/v1\/provider-profiles$/, respond: () => jsonResponse(PROFILE, 201) });
    // 保存成功，但随后的状态读取失败。
    stub.add({ method: 'GET', pattern: /\/api\/v1\/provider-profiles$/, respond: () => jsonResponse({ detail: 'boom' }, 503) });

    render(<LLMSetupPanel />);
    await user.click(await screen.findByRole('button', { name: /添加/ }));

    const alert = await screen.findByRole('alert');
    await waitFor(() => {
      expect(alert.textContent).toContain('配置已保存');
    });
    expect(alert.textContent).not.toContain('LLM 配置未完成');
  });

  it('写入失败时如实显示未完成', async () => {
    const user = userEvent.setup();
    stub.add({ method: 'POST', pattern: /\/api\/v1\/provider-profiles$/, respond: () => jsonResponse({ detail: '名称已存在' }, 409) });

    render(<LLMSetupPanel />);
    await user.click(await screen.findByRole('button', { name: /添加/ }));

    const alert = await screen.findByRole('alert');
    await waitFor(() => {
      expect(alert.textContent).toContain('操作未完成');
    });
    expect(alert.textContent).not.toContain('配置已保存');
  });
});

describe('密钥草稿只在服务端确认后清空', () => {
  it('保存失败时保留草稿', async () => {
    const user = userEvent.setup();
    render(<LLMConfiguration profiles={[PROFILE]} routes={[]} busy={false}
      onCreateProfile={vi.fn()} onUpdateProfile={vi.fn()} onTestProfile={vi.fn()}
      onSetKey={() => Promise.resolve(false)} onDeleteProfile={vi.fn()} onRoute={vi.fn()} />);

    await user.type(screen.getByPlaceholderText('更新密钥'), 'sk-new');
    await user.click(screen.getByRole('button', { name: '保存密钥' }));
    await waitFor(() => {
      expect((screen.getByPlaceholderText('更新密钥') as HTMLInputElement).value).toBe('sk-new');
    });
  });

  it('保存成功后清空草稿', async () => {
    const user = userEvent.setup();
    render(<LLMConfiguration profiles={[PROFILE]} routes={[]} busy={false}
      onCreateProfile={vi.fn()} onUpdateProfile={vi.fn()} onTestProfile={vi.fn()}
      onSetKey={() => Promise.resolve(true)} onDeleteProfile={vi.fn()} onRoute={vi.fn()} />);

    await user.type(screen.getByPlaceholderText('更新密钥'), 'sk-new');
    await user.click(screen.getByRole('button', { name: '保存密钥' }));
    await waitFor(() => {
      expect((screen.getByPlaceholderText('更新密钥') as HTMLInputElement).value).toBe('');
    });
  });
});

describe('文本工种一次事务绑定', () => {
  it('只发一次批量绑定请求', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    stub.add({ method: 'GET', pattern: /\/api\/v1\/provider-profiles$/, respond: () => jsonResponse([PROFILE]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/llm-readiness$/, respond: () => jsonResponse({
      status: 'READY', generation_status: 'CONNECTION_READY', text_agent_ready: false,
      roles: [{ role: 'director', code: null }, { role: 'editor', code: null }],
    }) });
    stub.add({ method: 'PUT', pattern: /\/api\/v1\/agent-routes\/bulk$/, respond: () => jsonResponse({
      roles: [], bound_roles: ['director', 'editor'], readiness: { status: 'READY', roles: [] },
    }) });

    render(<LLMSetupPanel />);
    await user.selectOptions(await screen.findByLabelText('用于文本工种的模型'), 'profile-1');
    await user.click(screen.getByRole('button', { name: '用于全部文本工种' }));

    await waitFor(() => {
      expect(stub.callsOf('PUT', /agent-routes\/bulk/)).toHaveLength(1);
    });
    expect(stub.callsOf('PUT', /\/api\/v1\/agent-routes$/)).toHaveLength(0);
    expect(stub.callsOf('PUT', /agent-routes\/bulk/)[0].body).toEqual({
      provider_profile_id: 'profile-1', roles: ['director', 'editor'],
    });
  });
});
