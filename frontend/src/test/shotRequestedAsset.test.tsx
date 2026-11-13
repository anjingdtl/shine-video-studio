import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { ShotAssetRequestModal } from '../components/ShotEditors';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

it('用户指定素材会以类别与名称送到素材准备，并说明 LLM 改写不是前提', async () => {
  const posts: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'POST', pattern: /\/shots\/shot-1\/requested-assets$/, respond: (_url, init) => {
      posts.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({
        shot_id: 'shot-1', shot_revision: 3, task_id: 'task-1',
        task_status: 'QUEUED', task_created: true, requested_assets: [],
      });
    } },
  ]);
  const user = userEvent.setup();
  render(<ShotAssetRequestModal projectId="proj-1"
    shot={{ shot_id: 'shot-1', name: '第一镜' }} onClose={vi.fn()} />);

  await user.selectOptions(screen.getByLabelText('类别'), 'prop');
  await user.type(screen.getByLabelText('名称'), '旧怀表');
  await user.type(screen.getByLabelText(/用途／外观描述/), '父亲留下的黄铜怀表');
  await user.click(screen.getByRole('button', { name: /加入本镜并送素材准备/ }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    family: 'prop', name: '旧怀表', description: '父亲留下的黄铜怀表', carrier: 'object',
  });
  expect(await screen.findByText(/已加入本镜素材需求/)).toBeInTheDocument();
  stub.restore();
});
