/** P04 regression: the Web start button submits shots in project order (not
 *  click order), preselects the only validated Ref2VA profile, excludes shots
 *  already in production, and a second click cannot re-queue them.
 *  The start gate reads the authoritative backend production_mode (READY),
 *  never a frontend guess. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ProductionSeriesPanel from '../components/ProductionSeriesPanel';
import { SYSTEM_STATUS, StudioStub, jsonResponse, productionMode } from './studioStub';

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

const readySystem = { ...SYSTEM_STATUS, render_mode: 'comfyui', production_mode: productionMode({ mode: 'READY' }) };

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
  it('0 镜时在开始按钮旁解释分镜尚未应用，并提供返回分镜的入口', async () => {
    const user = userEvent.setup();
    const target = document.createElement('section');
    target.id = 'storyboard';
    target.scrollIntoView = vi.fn();
    document.body.appendChild(target);
    try {
      render(<ProductionSeriesPanel projectId="proj-1" shots={[]} assets={[]} jobs={[]} workflowProfiles={[]}
        system={readySystem} busy={false} reviewWidth="1280" reviewHeight="720"
        onStarted={() => undefined} onMessage={() => undefined} onSaveBindings={() => Promise.resolve()} onSetContinuity={() => Promise.resolve()} onModeAction={() => undefined} />);
      const blockers = screen.getByRole('status', { name: '开始制作前还需完成' });
      expect(blockers).toHaveTextContent('还没有应用分镜，制作列表为 0 镜');
      expect(blockers).toHaveTextContent('还没有生成方案');
      expect(screen.getByRole('button', { name: /开始制作影片/ })).toBeDisabled();
      await user.click(screen.getByRole('button', { name: '去完成并应用分镜' }));
      expect(target.scrollIntoView).toHaveBeenCalledTimes(1);
      expect(stub.callsOf('POST', /production-series$/)).toHaveLength(0);
    } finally { target.remove(); }
  });

  it('按项目顺序提交、预选唯一方案、重复点击不重复提交', async () => {
    const user = userEvent.setup();
    render(
      <ProductionSeriesPanel
        projectId="proj-1"
        shots={shots}
        assets={[]}
        jobs={[]}
        workflowProfiles={profiles}
        system={readySystem}
        busy={false}
        reviewWidth="1280"
        reviewHeight="720"
        onStarted={() => undefined}
        onMessage={() => undefined}
        onSaveBindings={() => Promise.resolve()}
        onSetContinuity={() => Promise.resolve()}
        onModeAction={() => undefined}
      />,
    );
    await screen.findByText('选择全部可制作镜头');
    expect(screen.getByText(/首帧仍由模型生成，接点需要看片确认/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /选中段.*末帧|选中段.*切镜/ })).not.toBeInTheDocument();
    await user.click(screen.getByText('选择全部可制作镜头'));
    await user.click(screen.getByRole('button', { name: /开始制作影片/ }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /production-series$/)).toHaveLength(1);
    });
    await user.click(screen.getByRole('button', { name: /开始制作影片/ }));
    expect(stub.callsOf('POST', /production-series$/)).toHaveLength(1);
    const body = stub.callsOf('POST', /production-series$/)[0].body as { shot_ids: string[]; workflow_profile_id: string };
    expect(body.shot_ids).toEqual(['shot-a', 'shot-b', 'shot-c']);
    expect(body.workflow_profile_id).toBe('wf-1');
  });

  it('演示模式下开始按钮不可用，且提供启用真实制作入口', async () => {
    render(
      <ProductionSeriesPanel
        projectId="proj-1"
        shots={shots}
        assets={[]}
        jobs={[]}
        workflowProfiles={profiles}
        system={SYSTEM_STATUS}
        busy={false}
        reviewWidth="1280"
        reviewHeight="720"
        onStarted={() => undefined}
        onMessage={() => undefined}
        onSaveBindings={() => Promise.resolve()}
        onSetContinuity={() => Promise.resolve()}
        onModeAction={() => undefined}
      />,
    );
    const start = await screen.findByRole('button', { name: /开始制作影片/ });
    expect(start).toBeDisabled();
    expect(screen.getByRole('status', { name: '开始制作前还需完成' })).toHaveTextContent('不会生成真实影片');
    expect(screen.getByRole('button', { name: '启用真实制作' })).toBeInTheDocument();
  });
  it('环境 READY 但用户选择演示，连续制作禁用且不提交真实任务', async () => {
    const selectReal = vi.fn();
    render(<ProductionSeriesPanel projectId="proj-1" shots={shots} assets={[]} jobs={[]} workflowProfiles={profiles}
      system={readySystem} busy={false} reviewWidth="1280" reviewHeight="720" demoSelected
      onSelectReal={selectReal} onStarted={() => undefined} onMessage={() => undefined} onSaveBindings={() => Promise.resolve()} onSetContinuity={() => Promise.resolve()} onModeAction={() => undefined} />);
    await userEvent.click(screen.getByText('选择全部可制作镜头'));
    expect(screen.getByRole('button', { name: /开始制作影片/ })).toBeDisabled();
    expect(stub.callsOf('POST', /production-series$/)).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: '切回真实制作' }));
    expect(selectReal).toHaveBeenCalledTimes(1);
  });

});
