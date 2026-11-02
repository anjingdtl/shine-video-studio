import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import SettingsPanel from '../components/SettingsPanel';
import { StudioStub, baseRoutes } from './studioStub';

it('工作流注册显式保留媒体传输配置并提供节点验证入口', async () => {
  const stub = new StudioStub(baseRoutes());
  const register = vi.fn();
  const validate = vi.fn();
  const noop = vi.fn();
  const profile = { id: 'wf-1', project_id: null, name: 'candidate', mode: 'Ref2VA',
    graph_sha256: 'a'.repeat(64), parameter_map: {}, required_nodes: [], checkpoint_id: '',
    validated_against: null, created_at: '' };
  const user = userEvent.setup();
  render(<SettingsPanel onRefresh={noop} profiles={[]} routes={[]} workflowProfiles={[profile]}
    system={null} busy={false} onProjectSettings={noop} onCreateProfile={noop} onTestProfile={noop}
    onSetKey={noop} onDeleteProfile={noop} onRoute={noop} onTestComfy={vi.fn()}
    onRegisterWorkflow={register} onValidateWorkflow={validate} onDeleteWorkflow={noop} />);
  await user.click(screen.getByRole('button', { name: '展开 工作流 Profile 注册' }));
  await user.clear(screen.getByLabelText(/媒体传输配置 JSON/));
  await user.paste('{"audio":{"upload_path":"/upload/image"}}');
  await user.click(screen.getByRole('button', { name: '注册工作流' }));
  expect(register).toHaveBeenCalledWith(expect.objectContaining({
    media_transfers: { audio: { upload_path: '/upload/image' } },
  }));
  await user.click(screen.getByRole('button', { name: '验证本机节点' }));
  expect(validate).toHaveBeenCalledWith('wf-1');
  stub.restore();
});
