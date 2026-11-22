import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import type { AgentRoute, AgentRoutePreview, LlmRole, ProviderProfile } from '../api';
import { AgentModelRouting } from '../components/AgentModelRouting';
import { LLMSetupPanel } from '../components/LLMSetupPanel';
import { StudioStub, jsonResponse } from './studioStub';

const PROFILE: ProviderProfile = {
  id: 'profile-1', name: '创作主模型', kind: 'openai_compatible',
  base_url: 'https://provider.example/v1', model: 'model-one', has_api_key: true,
  secret_status: 'stored', capabilities: {}, timeout_s: 180, max_retries: 2,
  temperature: 0.2, enabled: true, created_at: '', updated_at: '',
  evidence: {
    secret_status: 'stored', connection_status: 'CONNECTED', catalog_status: 'LISTED',
    generation_status: 'PASSED', last_connection_checked_at: null, last_generation_checked_at: null,
  },
};

const SECOND_PROFILE: ProviderProfile = {
  ...PROFILE, id: 'profile-2', name: '备用模型', model: 'model-two',
};

function role(roleId: string, label: string, profileId: string | null): LlmRole {
  const profile = [PROFILE, SECOND_PROFILE].find(item => item.id === profileId);
  return {
    role: roleId, label_zh: label, group: '长文生产', business_entry: label, required: true,
    capability_type: 'structured_text', dependency_stages: ['long_form'], description: `${label}角色说明`,
    profile_id: profileId, profile_name: profile?.name ?? null, profile_kind: profile?.kind ?? null,
    profile_enabled: profile?.enabled ?? null, profile_valid: !!profileId, code: null, generation_code: null,
    readiness_status: profileId ? 'GENERATION_READY' : 'UNCONFIGURED', secret_status: 'stored',
    connection_status: profileId ? 'CONNECTED' : 'NOT_CHECKED', catalog_status: profileId ? 'LISTED' : 'NOT_CHECKED',
    generation_status: profileId ? 'PASSED' : 'NOT_CHECKED',
    last_connection_checked_at: null, last_generation_checked_at: null,
  };
}

it('复制配置时只发送明确选项，密钥复制默认为关闭', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/api\/v1\/llm\/overview$/, respond: () => jsonResponse({
      contract: 'llm-management-v1', profiles: [PROFILE], routes: [], route_revision: 'r'.repeat(64),
      roles: [], readiness: { status: 'BLOCKED', workflow: 'all_text', role_count: 0, roles: [],
        verification: 'CONNECTION_ONLY_NOT_GENERATION', generation_verification: 'GENERATION_TEST_NOT_RUN',
        generation_status: 'BLOCKED', text_agent_ready: false },
      multimodal_capabilities: [], workflows: ['all_text'],
    }) },
    { method: 'POST', pattern: /\/api\/v1\/provider-profiles\/profile-1\/duplicate$/, respond: () => jsonResponse({
      ...PROFILE, id: 'profile-copy', name: '副本', model: 'model-two',
    }, 201) },
  ]);
  const user = userEvent.setup();
  try {
    render(<LLMSetupPanel />);
    await screen.findByText('创作主模型');
    await user.click(screen.getByRole('button', { name: '复制配置' }));
    await user.clear(screen.getByLabelText('副本名称'));
    await user.type(screen.getByLabelText('副本名称'), '副本');
    await user.click(screen.getByRole('checkbox', { name: /由本机安全存储/ }));
    await user.click(screen.getByRole('button', { name: '创建副本' }));

    await waitFor(() => expect(stub.callsOf('POST', /provider-profiles\/profile-1\/duplicate/)).toHaveLength(1));
    expect(stub.callsOf('POST', /provider-profiles\/profile-1\/duplicate/)[0].body).toEqual({
      name: '副本', model: 'model-one', copy_credential: true,
    });
  } finally {
    stub.restore();
  }
});

it('只补齐未配置角色时保留已有 Agent 分配', async () => {
  const existing = role('scriptwriter', '编剧', PROFILE.id);
  const missing = role('four_stage_scene_script_run', '分场剧本', null);
  const routes: AgentRoute[] = [{ role: existing.role, provider_profile_id: PROFILE.id, profile_name: PROFILE.name }];
  const preview: AgentRoutePreview = {
    expected_revision: 'r'.repeat(64),
    updates: [{ role: missing.role, provider_profile_id: SECOND_PROFILE.id }],
    changes: [{
      role: missing.role, action: 'added', from_profile_id: null, to_profile_id: SECOND_PROFILE.id,
      from_profile_name: null, to_profile_name: SECOND_PROFILE.name,
    }],
    changed_role_count: 1, affected_tasks: [], blocking_tasks: [], blockers: [], can_apply: true,
  };
  const onPreview = vi.fn(async () => preview);
  const onApply = vi.fn(async () => true);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const user = userEvent.setup();

  render(<AgentModelRouting roles={[existing, missing]} profiles={[PROFILE, SECOND_PROFILE]} routes={routes}
    revision={preview.expected_revision} busy={false} multimodal={[]} onPreview={onPreview} onApply={onApply} />);
  await user.selectOptions(await screen.findByLabelText('批量分配模型'), SECOND_PROFILE.id);
  await user.click(screen.getByRole('button', { name: '只补齐未配置角色' }));
  await user.click(screen.getByRole('button', { name: /预览改派影响/ }));
  await waitFor(() => expect(onPreview).toHaveBeenCalledWith([
    { role: missing.role, provider_profile_id: SECOND_PROFILE.id },
  ]));
  expect(onApply).not.toHaveBeenCalled();
  await user.click(await screen.findByRole('button', { name: '确认保存 1 项分配' }));
  expect(onApply).toHaveBeenCalledWith(preview);
});

it('预览将待核对任务保留在旧模型，同时允许保存后续路由', async () => {
  const scriptwriter = role('scriptwriter', '故事大纲', PROFILE.id);
  const preview: AgentRoutePreview = {
    expected_revision: 'r'.repeat(64),
    updates: [{ role: 'scriptwriter', provider_profile_id: SECOND_PROFILE.id }],
    changes: [{
      role: 'scriptwriter', action: 'reassigned', from_profile_id: PROFILE.id,
      to_profile_id: SECOND_PROFILE.id, from_profile_name: PROFILE.name, to_profile_name: SECOND_PROFILE.name,
    }],
    changed_role_count: 1,
    affected_tasks: [{
      task_id: 'task-unknown', role: 'scriptwriter', status: 'NEEDS_REVIEW',
      model_profile_id: PROFILE.id, model_id: PROFILE.model,
      continuity: 'PINNED_MODEL', consumes_role: 'scriptwriter',
    }],
    blocking_tasks: [], blockers: [], can_apply: true,
  };
  const onPreview = vi.fn(async () => preview);
  const onApply = vi.fn(async () => true);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  const user = userEvent.setup();

  render(<AgentModelRouting roles={[scriptwriter]} profiles={[PROFILE, SECOND_PROFILE]}
    routes={[{ role: 'scriptwriter', provider_profile_id: PROFILE.id, profile_name: PROFILE.name }]}
    revision={preview.expected_revision} busy={false} multimodal={[]}
    onPreview={onPreview} onApply={onApply} />);
  await user.selectOptions(await screen.findByLabelText('为 故事大纲 选择模型'), SECOND_PROFILE.id);
  await user.click(screen.getByRole('button', { name: /预览改派影响/ }));

  expect(await screen.findByText(/UNKNOWN 结果和旧模型绑定保持不变/)).toBeInTheDocument();
  expect(screen.queryByText(/当前批次不能保存/)).not.toBeInTheDocument();
  const save = await screen.findByRole('button', { name: /确认保存 1 项分配/ });
  expect(save).toBeEnabled();
  await user.click(save);
  expect(onApply).toHaveBeenCalledWith(preview);
});
