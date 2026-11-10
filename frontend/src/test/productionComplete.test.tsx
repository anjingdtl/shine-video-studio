/** 排产制作区：范围已全部出片时，不能再解释"为什么不能开始"。 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import ProductionSeriesPanel from '../components/ProductionSeriesPanel';
import type { Job, Shot, SystemStatus } from '../api';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

const READY_SYSTEM = {
  production_mode: { mode: 'READY', detail: '本机渲染器可用', action: null },
} as unknown as SystemStatus;

const SHOT = {
  id: 'shot-1', project_id: 'proj-1', name: '本机样片', revision: 1,
  spec: { duration_target_s: 5, characters: ['CHAR_A'], continuity: { mode: 'cut' } },
  bindings: [],
} as unknown as Shot;

const PASSED_JOB = {
  id: 'job-1', project_id: 'proj-1', shot_id: 'shot-1', shot_name: '本机样片',
  shot_revision: 1, status: 'PASSED', provider: 'comfyui', simulated: false,
  artifact_id: 'artifact-1', attempts: [], repairs: [],
} as unknown as Job;

beforeEach(() => {
  stub = new StudioStub([
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/production-series$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/film-artifacts/, respond: () => jsonResponse([]) },
  ]);
});

afterEach(() => { stub.restore(); });

function renderPanel(jobs: Job[]) {
  render(<ProductionSeriesPanel
    projectId="proj-1"
    shots={[SHOT]}
    assets={[]}
    jobs={jobs}
    workflowProfiles={[]}
    system={READY_SYSTEM}
    busy={false}
    reviewWidth="1344"
    reviewHeight="768"
    onStarted={vi.fn()}
    onMessage={vi.fn()}
    onSaveBindings={vi.fn(async () => undefined)}
    onSetContinuity={vi.fn(async () => undefined)}
    onModeAction={vi.fn()}
  />);
}

it('范围已全部出片时显示已完成，而不是"现在为什么不能开始制作"', async () => {
  renderPanel([PASSED_JOB]);
  expect(await screen.findByText(/已全部出片/)).toBeInTheDocument();
  expect(screen.queryByText('现在为什么不能开始制作')).not.toBeInTheDocument();
});

it('还没有真实产物时仍然说明缺什么', async () => {
  renderPanel([]);
  await waitFor(() => expect(screen.getByText('现在为什么不能开始制作')).toBeInTheDocument());
  expect(screen.getByText(/还没有勾选镜头/)).toBeInTheDocument();
});
