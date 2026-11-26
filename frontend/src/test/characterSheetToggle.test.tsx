import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MaterialPreparationPanel } from '../components/MaterialPreparationPanel';
import { ShotMaterialsPanel } from '../components/StreamlinePanels';
import { StageRailProvider } from '../components/StageRail';
import { StudioStub, jsonResponse } from './studioStub';

function lorasPayload(loraReady: boolean) {
  return {
    items: loraReady
      ? [
          {
            id: 'qwen-image-2.1-lora-multiple-angles',
            display_name: 'Qwen 多视角镜头',
            filename: 'angles.safetensors',
            storage_path: 'D:/Shine/isolated-packages/qwen-image-2.1-lora-multiple-angles/angles.safetensors',
            sha256: '548c57977c3f1c09c2c9e5f4e83e930cbc6f719c878a576ab8666ce29fef39b6',
            file_size_bytes: 318819792,
            default_strength: 0.8,
            trigger_word: '<mva>',
            origin: 'discovered',
            comfy_lora_name: 'angles.safetensors',
            status: 'READY',
            created_at: '',
            updated_at: '',
          },
        ]
      : [],
    presets: [],
    comfy_reachable: true,
    max_style_loras: 3,
    character_sheet: {
      lora_id: 'qwen-image-2.1-lora-multiple-angles',
      lora_sha256: '548c57977c3f1c09c2c9e5f4e83e930cbc6f719c878a576ab8666ce29fef39b6',
      lora_status: loraReady ? 'READY' : 'NOT_INSTALLED',
      lora_ready: loraReady,
      comfy_reachable: true,
      display_name: 'Qwen 多视角镜头',
      setup_hint: loraReady
        ? ''
        : '请前往「设置 → 制作环境 → 角色五视图定妆照」勾选下载并完成纳管；角色定妆照不会静默降级为无 LoRA 生图。',
    },
  };
}

let stub: StudioStub | null = null;
afterEach(() => {
  cleanup();
  stub?.restore();
  stub = null;
  vi.restoreAllMocks();
});

describe('MaterialPreparationPanel：人物 16:9 五视图定妆照开关', () => {
  function setupMaterialRoutes(loraReady: boolean) {
    const state = {
      project_id: 'p1',
      stage: 'confirm_requirements',
      plan_revision_id: 'plan-1',
      plan_status: 'CONFIRMED',
      document_id: 'doc-1',
      visual_direction: null,
      confirmation: { confirmation_id: 'c'.repeat(64), confirmation_hash: 'd'.repeat(64) },
      expected_snapshot: 'a'.repeat(64),
      requirements: [
        {
          requirement_id: 'req-char-1',
          family: 'character',
          entity_id: '燕三娘',
          strategy: 'generate',
          status: 'CONFIRMED',
          bound_asset_id: null,
          brief_note: '',
          description: '明代武侠女侠。',
        },
      ],
      runs: [],
      blockers: [],
    };
    return new StudioStub([
      { method: 'GET', pattern: /loras\?base_model=qwen_image_21$/, respond: () => jsonResponse(lorasPayload(loraReady)) },
      { method: 'GET', pattern: /projects\/p1\/material-preparation$/, respond: () => jsonResponse(state) },
      { method: 'GET', pattern: /projects\/p1\/assets$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /projects\/p1\/documents$/, respond: () => jsonResponse([{ id: 'doc-1', filename: '剧本.txt', sha256: 'a'.repeat(64) }]) },
      { method: 'GET', pattern: /material-preparation\/scenes\/status/, respond: () => jsonResponse({ scenes: [] }) },
      { method: 'POST', pattern: /material-preparation\/confirm$/, respond: () => jsonResponse({ confirmation_id: 'c'.repeat(64), confirmation_hash: 'd'.repeat(64) }) },
      { method: 'POST', pattern: /material-preparation\/generate$/, respond: () => jsonResponse({ run_id: 'run-1', items: ['item-1'] }, 202) },
    ]);
  }

  it('未整备多视角 LoRA 时开关默认关闭且禁用，展示中文引导并可跳转设置', async () => {
    stub = setupMaterialRoutes(false);
    window.location.hash = '#d/assets';
    const user = userEvent.setup();
    render(<MaterialPreparationPanel projectId="p1" />);

    const toggle = await screen.findByRole('checkbox', { name: /人物生成 16:9 五视图定妆照/ });
    await waitFor(() => expect(screen.getByText(/设置 → 制作环境 → 角色五视图定妆照/)).toBeInTheDocument());
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /前往「设置 → 制作环境」下载/ }));
    expect(window.location.hash).toBe('#d/setup');
  });

  it('多视角 LoRA 就绪后可一步勾选并在生成请求中传入 character_sheet_mode: true', async () => {
    stub = setupMaterialRoutes(true);
    const user = userEvent.setup();
    render(<MaterialPreparationPanel projectId="p1" />);

    const toggle = await screen.findByRole('checkbox', { name: /人物生成 16:9 五视图定妆照/ });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();

    await user.click(toggle);
    expect(toggle).toBeChecked();

    await user.click(screen.getByRole('button', { name: '确认清单并补齐' }));
    await waitFor(() => expect(stub!.callsOf('POST', /material-preparation\/generate$/)).toHaveLength(1));
    expect(stub!.callsOf('POST', /material-preparation\/generate$/)[0].body).toMatchObject({
      character_sheet_mode: true,
      mode: 'real',
    });
  });
});

describe('ShotMaterialsPanel（精简流）：人物 16:9 五视图定妆照开关', () => {
  function setupStreamlineRoutes(loraReady: boolean) {
    return new StudioStub([
      { method: 'GET', pattern: /loras\?base_model=qwen_image_21$/, respond: () => jsonResponse(lorasPayload(loraReady)) },
      { method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/, respond: () => jsonResponse({ runs: [] }) },
      {
        method: 'GET',
        pattern: /projects\/p1\/director\/materials\/requirements/,
        respond: () => jsonResponse({
          plan_revision_id: 'plan-1',
          expected_snapshot: 'a'.repeat(64),
          requirements: [
            {
              requirement_id: 'req-char-1',
              family: 'character',
              entity_id: '燕三娘',
              name: '燕三娘',
              description: '明代武侠女侠',
              critical: true,
              strategy: 'generate',
              matched_master: null,
              used_shot_count: 2,
              used_episodes: ['第1集'],
              variants: [],
              cited_by_shot: true,
            },
          ],
        }),
      },
      {
        method: 'POST',
        pattern: /projects\/p1\/director\/materials\/commit$/,
        respond: () => jsonResponse({
          confirmation: { plan_revision_id: 'plan-1', confirmation_id: 'c'.repeat(64), confirmation_hash: 'd'.repeat(64), status: 'CONFIRMED', decisions: [] },
          generate_count: 1,
          batch: { run_id: 'run-1', item_count: 1, status: 'QUEUED' },
          batch_error: null,
          request_key: 'rk-1',
          replayed: false,
        }, 201),
      },
      {
        method: 'POST',
        pattern: /projects\/p1\/material-preparation\/regenerate-all$/,
        respond: () => jsonResponse({ run_id: 'run-2', items: ['item-2'] }, 202),
      },
    ]);
  }

  it('未整备时禁用开关并触发 setup-env 导航', async () => {
    stub = setupStreamlineRoutes(false);
    const goStage = vi.fn();
    const user = userEvent.setup();
    render(
      <StageRailProvider stageId="assets">
        <ShotMaterialsPanel projectId="p1" onGoStage={goStage} />
      </StageRailProvider>,
    );

    const toggle = await screen.findByRole('checkbox', { name: /人物生成 16:9 五视图定妆照/ });
    await waitFor(() => expect(screen.getByText(/设置 → 制作环境 → 角色五视图定妆照/)).toBeInTheDocument());
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /前往「设置 → 制作环境」下载/ }));
    expect(goStage).toHaveBeenCalledWith('setup-env');
  });

  it('就绪后一步勾选，提交与重新生成均携带 character_sheet_mode: true', async () => {
    stub = setupStreamlineRoutes(true);
    const user = userEvent.setup();
    render(
      <StageRailProvider stageId="assets">
        <ShotMaterialsPanel projectId="p1" />
      </StageRailProvider>,
    );

    const toggle = await screen.findByRole('checkbox', { name: /人物生成 16:9 五视图定妆照/ });
    await waitFor(() => expect(toggle).toBeEnabled());
    await user.click(toggle);
    expect(toggle).toBeChecked();

    await user.click(screen.getByRole('button', { name: '确认决策并开始准备素材' }));
    await waitFor(() => expect(stub!.callsOf('POST', /director\/materials\/commit$/)).toHaveLength(1));
    expect(stub!.callsOf('POST', /director\/materials\/commit$/)[0].body).toMatchObject({
      queue_generation: true,
      character_sheet_mode: true,
    });

    await user.click(screen.getByRole('button', { name: '重新生成素材' }));
    await waitFor(() => expect(stub!.callsOf('POST', /material-preparation\/regenerate-all$/)).toHaveLength(1));
    expect(stub!.callsOf('POST', /material-preparation\/regenerate-all$/)[0].body).toMatchObject({
      character_sheet_mode: true,
    });
  });
});
