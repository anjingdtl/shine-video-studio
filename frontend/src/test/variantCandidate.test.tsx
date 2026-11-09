/** The W6A8 low-end candidate in the production area.

 * A candidate variant is offered, never substituted.  These tests pin three
 * things a person would otherwise have to guess:

 * 1. the candidate has its own quality tier and never mixes with the
 *    audited native tier;
 * 2. a candidate this machine cannot execute is disabled and says why;
 * 3. an explicitly selected candidate keeps its identity through 一键开拍 -
 *    it is never silently swapped for the INT8 baseline.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ProductionSeriesPanel from '../components/ProductionSeriesPanel';
import { SYSTEM_STATUS, StudioStub, jsonResponse, productionMode } from './studioStub';

let stub: StudioStub;

const shots = [
  { id: 'shot-a', project_id: 'proj-1', name: '第一镜', revision: 1, spec: { story_beat: 'a' }, bindings: [] },
];

const baselineProfile = {
  id: 'wf-int8', project_id: 'proj-1', name: 'local-h3-ref2va-2img-1aud', mode: 'Ref2VA',
  graph_sha256: 'g-int8', parameter_map: {}, required_nodes: [], checkpoint_id: 'h3',
  validated_against: null, created_at: 't', model_identity: {},
};

const candidateProfile = {
  id: 'wf-w6a8', project_id: 'proj-1', name: 'local-h3-ref2va-lowend-pdd-free-2img', mode: 'Ref2VA',
  graph_sha256: 'g-w6a8', parameter_map: {}, required_nodes: [], checkpoint_id: 'w6a8',
  validated_against: null, created_at: 't',
  model_identity: {
    variant_id: 'ref2va-lowend-candidate',
    variant_acceptance_status: 'CANDIDATE',
  },
};

const readySystem = { ...SYSTEM_STATUS, render_mode: 'comfyui', production_mode: productionMode({ mode: 'READY' }) };

function gateRoutes(candidateSelectable: boolean) {
  return {
    method: 'GET',
    pattern: /setup\/production-gate/,
    respond: (url: string) => {
      const id = new URL(url, 'http://studio').searchParams.get('profile_id') ?? '';
      if (id === 'wf-w6a8') {
        return jsonResponse({
          profile_id: id, profile_name: candidateProfile.name, variant_id: 'ref2va-lowend-candidate',
          acceptance_status: 'CANDIDATE', candidate: true, selectable: candidateSelectable, readable: true,
          blockers: candidateSelectable ? [] : [{
            code: 'PROFILE_WEIGHTS_NOT_AVAILABLE',
            message: '这个变体的权重还没有在本机就位并逐字节核验。',
          }],
          reference_slots: ['reference_picture_1', 'reference_subject_1'], size_mapped: true,
        });
      }
      return jsonResponse({
        profile_id: id, profile_name: baselineProfile.name, variant_id: 'h3-ref2va-int8-convrot',
        acceptance_status: 'SUPPORTED', candidate: false, selectable: true, readable: true,
        blockers: [], reference_slots: [], size_mapped: true,
      });
    },
  };
}

function installStub(candidateSelectable: boolean) {
  stub = new StudioStub([
    { method: 'GET', pattern: /production-series$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /production-series$/, respond: () => jsonResponse({ id: 'series-1', status: 'QUEUED', shot_count: 1 }, 201) },
    { method: 'GET', pattern: /film-artifacts$/, respond: () => jsonResponse([]) },
    gateRoutes(candidateSelectable),
  ]);
}

beforeEach(() => {
  installStub(false);
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

function renderPanel(onMessage: (message: string, kind?: 'success' | 'error') => void = () => undefined) {
  return render(
    <ProductionSeriesPanel
      projectId="proj-1"
      shots={shots}
      assets={[]}
      jobs={[]}
      workflowProfiles={[baselineProfile, candidateProfile]}
      system={readySystem}
      busy={false}
      reviewWidth="1280"
      reviewHeight="720"
      onStarted={() => undefined}
      onMessage={onMessage}
      onSaveBindings={() => Promise.resolve()}
      onModeAction={() => undefined}
    />,
  );
}

describe('W6A8 候选方案在排产区的身份', () => {
  it('候选方案有自己的质量档，不与原生 20 步混在一起', async () => {
    renderPanel();
    await screen.findByText('选择全部可制作镜头');

    const lowend = screen.getByRole('radio', { name: /低显存候选/ });
    expect(lowend).toBeTruthy();
    // The baseline keeps the native tier; the candidate never joins it.
    const native = screen.getByRole('radio', { name: /高质量/ });
    expect(native.className).not.toContain('active');
  });

  it('本机不能执行的候选方案被禁用，并说明原因', async () => {
    renderPanel();
    await screen.findByText('选择全部可制作镜头');

    await waitFor(() => {
      expect(screen.getByText(/这个变体的权重还没有在本机就位并逐字节核验/)).toBeTruthy();
    });
    const select = screen.getByLabelText('生成方案') as HTMLSelectElement;
    const option = Array.from(select.options).find((row) => row.value === 'wf-w6a8');
    expect(option).toBeTruthy();
    expect(option?.disabled).toBe(true);
    expect(option?.textContent).toContain('本机暂不可用');
  });

  it('门禁通过后，显式选中的候选方案带着同一个身份进生产序列', async () => {
    const user = userEvent.setup();
    stub.restore();
    installStub(true);
    renderPanel();
    await screen.findByText('选择全部可制作镜头');

    // Pick the candidate explicitly, then use 一键开拍.
    await user.selectOptions(screen.getByLabelText('生成方案'), 'wf-w6a8');
    await waitFor(() => {
      expect((screen.getByLabelText('生成方案') as HTMLSelectElement).value).toBe('wf-w6a8');
    });

    await user.click(screen.getByRole('button', { name: /一键开拍/ }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /production-series$/)).toHaveLength(1);
    });
    const body = stub.callsOf('POST', /production-series$/)[0].body as { workflow_profile_id: string };
    // The candidate was not swapped for the INT8 baseline.
    expect(body.workflow_profile_id).toBe('wf-w6a8');
  });

  it('用户自己选的方案不被质量档替换——最终用哪套由用户决定', async () => {
    const user = userEvent.setup();
    stub.restore();
    installStub(true);
    renderPanel();
    await screen.findByText('选择全部可制作镜头');

    // The quality tier defaults to 快速, whose pool is empty in this project.
    // The user picks a profile by hand: that choice is the decision.
    await user.selectOptions(screen.getByLabelText('生成方案'), 'wf-int8');
    await waitFor(() => {
      expect((screen.getByLabelText('生成方案') as HTMLSelectElement).value).toBe('wf-int8');
    });

    await user.click(screen.getByRole('button', { name: /一键开拍/ }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /production-series$/)).toHaveLength(1);
    });
    const body = stub.callsOf('POST', /production-series$/)[0].body as { workflow_profile_id: string };
    // The chosen profile was not swapped for another tier's profile.
    expect(body.workflow_profile_id).toBe('wf-int8');
  });

  it('门禁未通过时，候选方案不能被选中，也就没有可提交的身份', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('选择全部可制作镜头');

    const select = screen.getByLabelText('生成方案') as HTMLSelectElement;
    const option = Array.from(select.options).find((row) => row.value === 'wf-w6a8');
    expect(option?.disabled).toBe(true);
    // The user cannot pick it, so 一键开拍 never carries the candidate's id.
    await user.click(screen.getByRole('button', { name: /一键开拍/ }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /production-series$/).length).toBeLessThanOrEqual(1);
    });
    const posted = stub.callsOf('POST', /production-series$/)[0];
    if (posted) {
      const body = posted.body as { workflow_profile_id: string };
      expect(body.workflow_profile_id).not.toBe('wf-w6a8');
    }
  });
});