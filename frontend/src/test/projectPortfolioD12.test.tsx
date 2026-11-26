import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProjectPortfolio } from '../components/ProjectPortfolio';
import { ShotMaterialsPanel } from '../components/StreamlinePanels';
import { StageRailProvider } from '../components/StageRail';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub | null = null;
afterEach(() => {
  cleanup();
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

describe('ProjectPortfolio 与 D12 诚实绑定展示', () => {
  it('项目管理读取已确认镜头数（8 镜）而非历史总行数，且空作品显示 0 镜', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /\/assets$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /\/styles$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /\/deliveries$/, respond: () => jsonResponse([]) },
      {
        method: 'GET',
        pattern: /\/projects\/p-8of35\/journey$/,
        respond: () => jsonResponse({
          project_id: 'p-8of35',
          next_stage: 'post',
          scope: { confirmed_shot_count: 8, shot_count: 8 },
          stages: [
            { id: 'storyboard', label: '镜头拆解', done: true, summary: '', blockers: [],
              facts: { shot_count: 35, planned_shot_count: 8, confirmed_shot_count: 8 } },
          ],
        }),
      },
      {
        method: 'GET',
        pattern: /\/projects\/p-empty\/journey$/,
        respond: () => jsonResponse({
          project_id: 'p-empty',
          next_stage: 'script',
          scope: { confirmed_shot_count: 0, shot_count: 0 },
          stages: [
            { id: 'storyboard', label: '镜头拆解', done: false, summary: '', blockers: [],
              facts: { shot_count: 0, planned_shot_count: 0, confirmed_shot_count: 0 } },
          ],
        }),
      },
    ]);

    render(
      <ProjectPortfolio
        projects={[
          { id: 'p-8of35', title: '灰烬圣剑：古堡守望', description: '', language: 'Chinese',
            frozen_style_version_id: null, style_draft: null, allow_remote_review: false,
            auto_repair_enabled: false, created_at: '' },
          { id: 'p-empty', title: '空作品', description: '', language: 'Chinese',
            frozen_style_version_id: null, style_draft: null, allow_remote_review: false,
            auto_repair_enabled: false, created_at: '' },
        ]}
        currentProjectId="p-8of35"
        productionLabel="本机真实 H3"
        projectTitle=""
        setProjectTitle={() => undefined}
        busy={false}
        refreshEpoch={0}
        onOpen={() => undefined}
        onCreate={() => undefined}
      />,
    );

    await waitFor(() => expect(screen.getByText('灰烬圣剑：古堡守望')).toBeInTheDocument());
    await waitFor(() => {
      const eight = screen.getAllByText((_, el) => el?.textContent === '8 镜');
      expect(eight.length).toBeGreaterThanOrEqual(1);
    });
    const zero = screen.getAllByText((_, el) => el?.textContent === '0 镜');
    expect(zero.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/35 镜/)).not.toBeInTheDocument();
  });

  it('D12：新底图入库但 0 镜回绑时诚实展示「已入库·镜头仍用旧图」及旧图镜头列表，并提供「按修订换图」', async () => {
    stub = new StudioStub([
      { method: 'GET', pattern: /loras\?base_model=qwen_image_21$/, respond: () => jsonResponse({
        items: [], presets: [], comfy_reachable: false, max_style_loras: 3,
        character_sheet: { lora_id: 'x', lora_sha256: 'y', lora_status: 'NOT_INSTALLED', lora_ready: false },
      }) },
      { method: 'GET', pattern: /material-preparation\/generation-progress$/, respond: () => jsonResponse({ runs: [] }) },
      {
        method: 'GET',
        pattern: /projects\/p1\/director\/materials\/requirements/,
        respond: () => jsonResponse({
          plan_revision_id: 'plan-1',
          expected_snapshot: 'a'.repeat(64),
          preferences: { character_sheet_mode: false, effective_character_sheet_mode: false, character_sheet_lora_ready: false },
          requirements: [{
            requirement_id: 'req-sanctuary',
            family: 'scene',
            entity_id: '圣殿残垣祭坛',
            name: '圣殿残垣祭坛',
            description: '古堡祭坛',
            critical: true,
            optional: false,
            strategy: 'reuse_master',
            matched_master: { asset_id: 'bca2133a-0000-0000-0000-000000000000', sha256: 's'.repeat(64) },
            shot_binding: {
              status: 'LIBRARY_ONLY',
              current_asset_id: 'bca2133a-0000-0000-0000-000000000000',
              target_shot_count: 4,
              bound_to_current_count: 0,
              bound_to_current_shot_ids: [],
              other_asset_shots: [
                { shot_id: 's5', shot_name: 'S5', shot_number: 5, revision: 6,
                  asset_id: '9616e8b2-0000-0000-0000-000000000000', asset_filename: '06-old.png',
                  protected_reason: 'SHOT_IN_CONFIRMED_PACKAGE' },
                { shot_id: 's8', shot_name: 'S8', shot_number: 8, revision: 8,
                  asset_id: '9616e8b2-0000-0000-0000-000000000000', asset_filename: '06-old.png',
                  protected_reason: 'SHOT_IN_CONFIRMED_PACKAGE' },
              ],
              unbound_shot_ids: [],
            },
            used_shot_ids: ['s5', 's6', 's7', 's8'],
            used_shot_count: 4,
            used_episodes: ['ep-01'],
            variants: [],
          }],
        }),
      },
      {
        method: 'POST',
        pattern: /\/asset-center\/adoptions\/preview$/,
        respond: () => jsonResponse({
          project_id: 'p1',
          project_asset_id: 'bca2133a-0000-0000-0000-000000000000',
          asset_sha256: 's'.repeat(64),
          shot_count: 2,
          blocked: [],
          shots: [
            { shot_id: 's5', shot_name: 'S5', revision: 6, already_bound: false, current_binding_count: 3,
              use_as: 'picture', will_bump_revision: true, confirmed_package_id: 'b32a',
              replaces_asset_id: '9616e8b2-0000-0000-0000-000000000000', replaces_asset_filename: '06-old.png',
              conflict: null, action: null },
            { shot_id: 's8', shot_name: 'S8', revision: 8, already_bound: false, current_binding_count: 5,
              use_as: 'picture', will_bump_revision: true, confirmed_package_id: 'b32a',
              replaces_asset_id: '9616e8b2-0000-0000-0000-000000000000', replaces_asset_filename: '06-old.png',
              conflict: null, action: null },
          ],
        }),
      },
    ]);

    const user = userEvent.setup();
    render(
      <StageRailProvider stageId="assets">
        <ShotMaterialsPanel projectId="p1" />
      </StageRailProvider>,
    );

    await waitFor(() => expect(screen.getByText(/已入库·镜头仍用旧图（0\/4）/)).toBeInTheDocument());
    expect(screen.getByText(/第5镜\(9616e8b2\).*第8镜\(9616e8b2\)/)).toBeInTheDocument();
    expect(screen.getByText(/已入库（镜头仍用旧图 0\/4）/)).toBeInTheDocument();
    expect(screen.queryByText(/^✓ 已采用$/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '按修订换图' }));
    await waitFor(() => expect(stub!.callsOf('POST', /adoptions\/preview$/)).toHaveLength(1));
    expect(stub!.callsOf('POST', /adoptions\/preview$/)[0].body).toMatchObject({
      allow_revise_frozen: true,
    });
  });
});
