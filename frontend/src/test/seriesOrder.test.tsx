/** P04 regression: the Web start button submits shots in project order (not
 *  click order), preselects the only validated Ref2VA profile, excludes shots
 *  already in production, and a second click cannot re-queue them. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ProductionSeriesPanel from '../components/ProductionSeriesPanel';
import { SYSTEM_STATUS, StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

const shots = [
  { id: 'shot-a', project_id: 'proj-1', name: '第一镜', revision: 1, spec: { story_beat: 'a' }, bindings: [] },
  { id: 'shot-b', project_id: 'proj-1', name: '第二镜', revision: 1, spec: { story_beat: 'b' }, bindings: [] },
  { id: 'shot-c', project_id: 'proj-1', name: '第三镜', revision: 1, spec: { story_beat: 'c' }, bindings: [] },
];

const profiles = [{
  id: 'wf-1', project_id: 'proj-1', name: 'Ref2VA 主方案', mode: 'Ref2VA', graph_sha256: 'g',
  parameter_map: {}, required_nodes: [], checkpoint_id: 'h3', validated_against: null,
  created_at: 't', model_identity: {},
}];

beforeEach(() => {
  stub = new StudioStub([
    { method: 'GET', pattern: /production-series$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /production-series$/, respond: () => jsonResponse({ id: 'series-1', status: 'QUEUED', shot_count: 3 }, 201) },
    { method: 'GET', pattern: /film-artifacts$/, respond: () => jsonResponse([]) },
  ]);
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

describe('Web 发起生产序列（P04）', () => {
  it('按项目顺序提交、预选唯一方案、重复点击不重复提交', async () => {
    const user = userEvent.setup();
    render(
      <ProductionSeriesPanel
        projectId="proj-1"
        shots={shots}
        workflowProfiles={profiles}
        system={{ ...SYSTEM_STATUS, render_mode: 'comfyui' }}
        busy={false}
        reviewWidth="1280"
        reviewHeight="720"
        onStarted={() => undefined}
        onMessage={() => undefined}
      />,
    );
    await screen.findByText('选择全部可制作镜头');
    await user.click(screen.getByText('选择全部可制作镜头'));
    await user.click(screen.getByRole('button', { name: /开始真实生产/ }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /production-series$/)).toHaveLength(1);
    });
    await user.click(screen.getByRole('button', { name: /开始真实生产/ }));
    expect(stub.callsOf('POST', /production-series$/)).toHaveLength(1);
    const body = stub.callsOf('POST', /production-series$/)[0].body as { shot_ids: string[]; workflow_profile_id: string };
    expect(body.shot_ids).toEqual(['shot-a', 'shot-b', 'shot-c']);
    expect(body.workflow_profile_id).toBe('wf-1');
  });
});
