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
    await user.type(await screen.findByLabelText('模型配置名称'), '本地模型');
    await user.type(screen.getByLabelText('接口地址'), 'http://127.0.0.1:11434/v1');
    await user.type(screen.getByLabelText('Model ID'), 'qwen-local');
    await user.click(screen.getByRole('button', { name: '添加' }));

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
    await user.type(await screen.findByLabelText('模型配置名称'), '本地模型');
    await user.type(screen.getByLabelText('接口地址'), 'http://127.0.0.1:11434/v1');
    await user.type(screen.getByLabelText('Model ID'), 'qwen-local');
    await user.click(screen.getByRole('button', { name: '添加' }));

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
      onDuplicateProfile={vi.fn()} onTestGeneration={vi.fn()}
      onSetKey={() => Promise.resolve(false)} onDeleteProfile={vi.fn()} />);

    await user.click(screen.getByText('更新密钥'));
    const keyField = screen.getByLabelText('更新密钥 本地文本模型');
    await user.type(keyField, 'sk-new');
    await user.click(screen.getByRole('button', { name: '保存新密钥' }));
    await waitFor(() => {
      expect((screen.getByLabelText('更新密钥 本地文本模型') as HTMLInputElement).value).toBe('sk-new');
    });
  });

  it('保存成功后清空草稿', async () => {
    const user = userEvent.setup();
    render(<LLMConfiguration profiles={[PROFILE]} routes={[]} busy={false}
      onCreateProfile={vi.fn()} onUpdateProfile={vi.fn()} onTestProfile={vi.fn()}
      onDuplicateProfile={vi.fn()} onTestGeneration={vi.fn()}
      onSetKey={() => Promise.resolve(true)} onDeleteProfile={vi.fn()} />);

    await user.click(screen.getByText('更新密钥'));
    const keyField = screen.getByLabelText('更新密钥 本地文本模型');
    await user.type(keyField, 'sk-new');
    await user.click(screen.getByRole('button', { name: '保存新密钥' }));
    await waitFor(() => {
      expect((screen.getByLabelText('更新密钥 本地文本模型') as HTMLInputElement).value).toBe('');
    });
  });
});

describe('文本工种事务性路由', () => {
  it('先预览影响，再以版本号发送一笔异构批量保存', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    stub.add({ method: 'GET', pattern: /\/api\/v1\/provider-profiles$/, respond: () => jsonResponse([PROFILE]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/llm\/roles$/, respond: () => jsonResponse({ route_revision: 'r'.repeat(64) }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/llm-readiness$/, respond: () => jsonResponse({
      status: 'READY', generation_status: 'CONNECTION_READY', text_agent_ready: false,
      roles: [{ role: 'director', code: null }, { role: 'editor', code: null }],
    }) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/agent-routes\/preview$/, respond: () => jsonResponse({
      expected_revision: 'r'.repeat(64),
      updates: [{ role: 'director', provider_profile_id: 'profile-1' }, { role: 'editor', provider_profile_id: 'profile-1' }],
      changes: [
        { role: 'director', action: 'added', from_profile_id: null, to_profile_id: 'profile-1', from_profile_name: null, to_profile_name: '本地文本模型' },
        { role: 'editor', action: 'added', from_profile_id: null, to_profile_id: 'profile-1', from_profile_name: null, to_profile_name: '本地文本模型' },
      ], changed_role_count: 2, affected_tasks: [], blockers: [], can_apply: true,
    }) });
    stub.add({ method: 'PUT', pattern: /\/api\/v1\/agent-routes\/batch$/, respond: () => jsonResponse({
      routes: [], route_revision: 'n'.repeat(64), readiness: { status: 'READY', roles: [] }, changed_roles: ['director', 'editor'],
    }) });

    render(<LLMSetupPanel />);
    await user.selectOptions(await screen.findByLabelText('批量分配模型'), 'profile-1');
    await user.click(screen.getByText('按工作阶段选择'));
    await user.click(screen.getByRole('button', { name: '全部文本 Agent' }));
    await user.click(screen.getByRole('button', { name: /分配给选中角色/ }));
    await user.click(screen.getByRole('button', { name: /预览改派影响/ }));
    await user.click(await screen.findByRole('button', { name: /确认保存 2 项分配/ }));

    await waitFor(() => {
      expect(stub.callsOf('POST', /agent-routes\/preview/)).toHaveLength(1);
      expect(stub.callsOf('PUT', /agent-routes\/batch/)).toHaveLength(1);
    });
    expect(stub.callsOf('PUT', /\/api\/v1\/agent-routes$/)).toHaveLength(0);
    expect(stub.callsOf('PUT', /agent-routes\/batch/)[0].body).toEqual({
      updates: [{ role: 'director', provider_profile_id: 'profile-1' }, { role: 'editor', provider_profile_id: 'profile-1' }],
      expected_revision: 'r'.repeat(64),
    });
  });
});
