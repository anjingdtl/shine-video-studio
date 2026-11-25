import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { ScheduleProduceStagePage } from '../manual4/pages/ScheduleProduceStagePage';
import { imageStyleInput, StyleLoraAssemblyPanel, type StyleLoraConfig } from '../shared/StyleLoraAssemblyPanel';
import { PRODUCTION_MODE_READY, StudioStub, SYSTEM_STATUS, baseProject, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
  window.location.hash = '';
});

function makeLoraItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 'h3-realism-people',
    display_name: '电影化写实 LoRA (H3 Realism)',
    filename: 'h3-realism-people-t2v-i2v-r2v.safetensors',
    storage_path: 'D:/ComfyUI/models/loras/MiniMax-H3/h3-realism-people-t2v-i2v-r2v.safetensors',
    sha256: 'a'.repeat(64),
    file_size_bytes: 131_000_000,
    default_strength: 0.65,
    trigger_word: '',
    origin: 'discovered',
    comfy_lora_name: 'MiniMax-H3/h3-realism-people-t2v-i2v-r2v.safetensors',
    status: 'READY',
    created_at: '2026-11-25T00:00:00Z',
    updated_at: '2026-11-25T00:00:00Z',
    ...overrides,
  };
}

describe('StyleLoraAssemblyPanel（共享 LoRA 装配组件）', () => {
  it('读取 Qwen 配方，通过同一界面挂载可选多视角权重并自动展示触发词', async () => {
    const fix = makeLoraItem({ id: 'fix', display_name: '画质修复' });
    const angle = makeLoraItem({ id: 'angle', display_name: '多视角', trigger_word: '<mva>', trigger_position: 'prefix',
      negative_trigger_word: 'overexposed', sha256: 'b'.repeat(64) });
    const slot = (it: typeof fix) => ({ component_id: it.id, lora_id: it.id, strength_model: 0.8, status: 'READY' });
    const stub = new StudioStub([{ method: 'GET', pattern: /\/api\/v1\/loras\?base_model=qwen_image_21$/, respond: () => jsonResponse({
      items: [fix, angle], comfy_reachable: true, presets: [{ id: 'cyberpunk_neo_noir', display_name: '赛博朋克',
        loras: [slot(fix)], optional_loras: [slot(angle)] }],
    }) }]);
    function Example() {
      const [value, setValue] = useState<StyleLoraConfig[]>([]);
      const [preset, setPreset] = useState<string | null>(null);
      return <><StyleLoraAssemblyPanel baseModel="qwen_image_21" value={value} onChange={setValue}
        loraSupported stylePreset={preset} onPresetChange={setPreset} />
        <output>{JSON.stringify(imageStyleInput(value, preset))}</output></>;
    }
    const user = userEvent.setup();
    render(<Example />);
    const presetButton = await screen.findByRole('button', { name: '赛博朋克' });
    await user.click(presetButton);
    expect(presetButton).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: '多视角' }));
    expect(screen.getByText(/自动前置 <mva>/)).toHaveTextContent('负向 overexposed');
    expect(JSON.parse(screen.getAllByRole('status').find((row) => row.tagName === 'OUTPUT')!.textContent!)).toEqual({
      style_preset: 'cyberpunk_neo_noir', style_loras: [fix, angle].map((item) => ({
        lora_id: item.id, sha256: item.sha256, strength_model: 0.8,
      })),
    });
    await user.click(screen.getByRole('button', { name: '清空已选' }));
    expect(presetButton).toHaveAttribute('aria-pressed', 'false');
    stub.restore();
  });

  it('展示实时状态、限制最多 3 个并支持排序、改权重、触发词校验、导入与删阻断提示', async () => {
    const items = [
      makeLoraItem({ id: 'h3-realism-people', display_name: '写实基准', status: 'READY' }),
      makeLoraItem({ id: 'lora_2', display_name: '霓虹赛博', filename: 'neon.safetensors', sha256: 'b'.repeat(64), origin: 'imported', status: 'READY' }),
      makeLoraItem({ id: 'lora_3', display_name: '水墨国风', filename: 'ink.safetensors', sha256: 'c'.repeat(64), origin: 'imported', status: 'NEEDS_COMFY_REFRESH' }),
      makeLoraItem({ id: 'lora_4', display_name: '缺盘风', filename: 'miss.safetensors', sha256: 'd'.repeat(64), origin: 'imported', status: 'MISSING_FILE' }),
      makeLoraItem({ id: 'lora_5', display_name: '篡改风', filename: 'bad.safetensors', sha256: 'e'.repeat(64), origin: 'imported', status: 'HASH_MISMATCH' }),
    ];
    let localImportBody: Record<string, unknown> | null = null;
    let uploadedForm = false;

    const stub = new StudioStub([
      { method: 'GET', pattern: /^\/api\/v1\/loras$/, respond: () => jsonResponse({ items, comfy_reachable: true, max_style_loras: 3 }) },
      {
        method: 'POST',
        pattern: /^\/api\/v1\/loras\/import$/,
        respond: (_url, init) => {
          if (init?.body instanceof FormData) {
            uploadedForm = true;
          } else {
            localImportBody = JSON.parse(String(init?.body ?? '{}'));
          }
          return jsonResponse({ item: makeLoraItem({ id: 'lora_new', display_name: '新导入', sha256: 'f'.repeat(64), origin: 'imported' }), deduplicated: false, comfy_reachable: true }, 201);
        },
      },
      {
        method: 'DELETE',
        pattern: /^\/api\/v1\/loras\/lora_2$/,
        respond: () => jsonResponse({ detail: { code: 'LORA_IN_USE', message: '该 LoRA 正被排片引用，不能删除。' } }, 409),
      },
    ]);

    const user = userEvent.setup();
    let current: StyleLoraConfig[] = [];
    const { rerender } = render(
      <StyleLoraAssemblyPanel value={current} onChange={(next) => { current = next; }} loraSupported />,
    );

    expect(await screen.findByText('写实基准')).toBeInTheDocument();
    expect(screen.getByText('ComfyUI 暂未发现（需刷新/重启）')).toBeInTheDocument();
    expect(screen.getByText('磁盘文件缺失')).toBeInTheDocument();
    expect(screen.getByText('文件哈希不匹配')).toBeInTheDocument();

    // 缺失与哈希不匹配条目不可挂载
    const mountButtons = screen.getAllByRole('button', { name: '挂载' });
    expect(mountButtons[3]).toBeDisabled();
    expect(mountButtons[4]).toBeDisabled();

    // 挂载前 3 个并在超过上限时禁用剩余挂载
    await user.click(mountButtons[0]);
    rerender(<StyleLoraAssemblyPanel value={current} onChange={(next) => { current = next; }} loraSupported />);
    await user.click(screen.getAllByRole('button', { name: '挂载' })[0]);
    rerender(<StyleLoraAssemblyPanel value={current} onChange={(next) => { current = next; }} loraSupported />);
    await user.click(screen.getAllByRole('button', { name: '挂载' })[0]);
    rerender(<StyleLoraAssemblyPanel value={current} onChange={(next) => { current = next; }} loraSupported />);

    expect(current).toHaveLength(3);
    expect(screen.getByText('3 / 3（可选）')).toBeInTheDocument();

    // 调整排序与触发词 CJK 前端拦截提示
    await user.click(screen.getByRole('button', { name: /下移 写实基准/ }));
    rerender(<StyleLoraAssemblyPanel value={current} onChange={(next) => { current = next; }} loraSupported />);
    expect(current[1]?.lora_id).toBe('h3-realism-people');

    const triggerInputs = screen.getAllByPlaceholderText('例如 cyberpunk_neon');
    await user.type(triggerInputs[0], '中文触发词');
    rerender(<StyleLoraAssemblyPanel value={current} onChange={(next) => { current = next; }} loraSupported />);
    expect(screen.getByText(/触发词仅允许 ASCII 可打印字符/)).toBeInTheDocument();

    // 删除在途引用 返回 409 LORA_IN_USE 明确展示
    await user.click(screen.getByRole('button', { name: '删除 霓虹赛博' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('LORA_IN_USE');

    // 本地路径与文件上传导入均调同一 /import 执行口
    await user.click(screen.getByRole('button', { name: /导入 LoRA/ }));
    await user.type(screen.getByPlaceholderText(/输入本机 .safetensors 绝对路径/), 'D:\\Loras\\test.safetensors');
    await user.click(screen.getByRole('button', { name: '路径导入' }));
    await waitFor(() => expect(localImportBody?.['local_path']).toBe('D:\\Loras\\test.safetensors'));

    await user.click(screen.getByRole('button', { name: /导入 LoRA/ }));
    const fileInput = document.querySelector('input[type="file"][accept=".safetensors"]') as HTMLInputElement;
    fireEvent.change(fileInput, { target: { files: [new File(['abc'], 'film.safetensors')] } });
    await waitFor(() => expect(uploadedForm).toBe(true));

    // W6A8 等不支持变体禁用挂载并提示
    rerender(<StyleLoraAssemblyPanel value={current} onChange={(next) => { current = next; }} loraSupported={false} />);
    expect(screen.getByText(/当前所选量化变体暂不支持风格 LoRA/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /上移/ })[0]).toBeDisabled();

    stub.restore();
  });
});

describe('七阶导演包与四阶排片共用风格 LoRA 装配', () => {
  it('七阶导演包页挂载风格 LoRA 后提交到 start，并在切 W6A8 时禁用挂载', async () => {
    const loras = [makeLoraItem()];
    let posted: Record<string, unknown> | null = null;
    const stub = new StudioStub([
      ...baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })),
      { method: 'GET', pattern: /^\/api\/v1\/loras$/, respond: () => jsonResponse({ items: loras, comfy_reachable: true, max_style_loras: 3 }) },
      { method: 'GET', pattern: /^\/api\/v1\/system\/status$/, respond: () => jsonResponse({ ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: true, production_mode: PRODUCTION_MODE_READY }) },
      {
        method: 'GET',
        pattern: /^\/api\/v1\/setup\/variant-availability/,
        respond: () => jsonResponse({
          project_id: 'proj-1',
          variants: [
            { variant_id: 'h3-ref2va-int8-convrot', available: true, reason: '已就绪' },
            { variant_id: 'ref2va-lowend-candidate', available: true, reason: '已就绪' },
          ],
        }),
      },
      {
        method: 'GET',
        pattern: /^\/api\/v1\/projects\/proj-1\/director\/strategy-options$/,
        respond: () => jsonResponse({
          model_variants: [
            { variant_id: 'h3-ref2va-int8-convrot', label: 'INT8 主线', acceptance_status: 'SUPPORTED', verified: true, lora_supported: true, default_recommended: true },
            { variant_id: 'ref2va-lowend-candidate', label: 'W6A8 低显存', acceptance_status: 'SUPPORTED', verified: true, lora_supported: false },
          ],
          accelerators: [{ accelerator_id: 'pdd8', steps: 8, verified: true }],
          second_pass: { supported: false, recipes: [] },
          upscale: [],
        }),
      },
      {
        method: 'GET',
        pattern: /^\/api\/v1\/projects\/proj-1\/director\/overview/,
        respond: () => jsonResponse({
          project_id: 'proj-1',
          title: '测试剧',
          plan: { version: 1, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
          style: { version: 1, sha256: 'a'.repeat(64) },
          counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 1, delivered_episode_count: 0, shot_count: 1, passed_shot_count: 0 },
          episodes: [{
            episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 8,
            state: 'READY_TO_PACKAGE', state_label: '可打包',
            storyboard: { section_count: 1, landed_section_count: 1, pending_sections: [], ledger_task_id: 't1', ledger_task_status: 'APPLIED', state_version: 1, blocker: null },
            shots: { landed_count: 1, confirmed_count: 1, confirmed: true, problems: [] },
            materials: { missing_count: 0, missing: [] }, package: null, production: null, delivery: null,
            next_action: { kind: 'BUILD_PACKAGE', label: '打包' },
          }],
          next_action: null, package: null, run: null,
          storyboard_task: { task_id: 't1', status: 'SUCCEEDED', state_version: 1 },
        }),
      },
      {
        method: 'POST',
        pattern: /^\/api\/v1\/projects\/proj-1\/director\/production\/start$/,
        respond: (_url, init) => {
          posted = JSON.parse(String(init?.body ?? '{}'));
          return jsonResponse({ package_id: 'pkg-1', package_revision: 1, package_built: true, run_id: 'run-1', run_status: 'QUEUED', created: true, episode_ids: ['ep-01'], episodes: [{ episode_id: 'ep-01', position: 1, shot_count: 1 }] }, 201);
        },
      },
    ]);

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);

    expect(await screen.findByTestId('style-lora-panel')).toBeInTheDocument();

    // 等待 strategy-options 加载完毕（模型版本 radiogroup 含两个变体），切到 W6A8（lora_supported: false）验证禁用，再切回 INT8
    const variantGroup = await screen.findByRole('radiogroup', { name: '模型版本' });
    await waitFor(() => expect(within(variantGroup).getAllByRole('radio').length).toBeGreaterThanOrEqual(2));
    const radios = within(variantGroup).getAllByRole('radio');
    await user.click(radios[1]);
    expect(await screen.findByText(/当前所选量化变体暂不支持风格 LoRA/)).toBeInTheDocument();
    await user.click(radios[0]);

    await user.click(await screen.findByRole('button', { name: '挂载' }));
    expect(screen.getByText('1 / 3（可选）')).toBeInTheDocument();

    const [startBtn] = await screen.findAllByRole('button', { name: /确认拍摄方案并开始真实制作/ });
    await user.click(startBtn);
    await waitFor(() => expect(posted).not.toBeNull());
    const strategy = (posted as Record<string, unknown> | null)?.['strategy'] as Record<string, unknown>;
    expect(strategy?.['style_loras']).toHaveLength(1);

    stub.restore();
  });

  it('四阶排片生产页共用同一面板，提交并展示冻结风格 LoRA', async () => {
    const loras = [makeLoraItem()];
    let createPayload: Record<string, unknown> | null = null;
    const prodState = {
      confirmed_revision: { id: 'rev-1', revision: 1 },
      episodes: [{
        id: 'ep-1', episode_no: 1, episode_code: 'EP01', title: '第1集', planned_total_seconds: 8,
        shots: [{
          id: 'shot-1', episode_id: 'ep-1', shot_no: 1, shot_code: 'S01', revision: 1,
          target_seconds: 8, summary: '雨夜出场', dialogue: [], aspect_ratio: '16:9',
          continuity_mode: 'independent', reference_manifest: [],
          frozen_prompt: null, draft_prompt: null, active_prompt: null,
          prompt_valid_for_run: false, prompt_invalid_reason: null, latest_job: null,
        }],
      }],
      runs: [{
        id: 'run-hist-1', project_id: 'm4-1', execution_snapshot_id: 'snap-1',
        scope_kind: 'project', scope_target_ids: [], provider: 'comfyui',
        config: {
          variant_id: 'h3-ref2va-int8-convrot', accelerator_id: 'pdd8', resolution_scale: 0.5,
          style_loras: [{ lora_id: 'h3-realism-people', lora_name: 'MiniMax-H3/h3-realism-people-t2v-i2v-r2v.safetensors', sha256: 'a'.repeat(64), strength_model: 0.65, trigger_word: '' }],
        },
        status: 'COMPLETED', worker_id: null, error_code: null, error_detail: '', jobs: [], created_at: '2026-11-25T00:00:00Z',
      }],
      options: {
        variants: [
          { variant_id: 'h3-ref2va-int8-convrot', label: 'INT8', verified: true, lora_supported: true, accelerators: ['pdd8'] },
          { variant_id: 'ref2va-lowend-candidate', label: 'W6A8', verified: true, lora_supported: false, accelerators: ['native20'] },
        ],
        accelerators: [{ id: 'pdd8', steps: 8, requires_pdd: true, label: 'PDD 8步', verified: true }],
        second_pass: { supported: false },
        frames: [{ orientation: 'landscape', aspect_ratio: '16:9', scale: 0.5, width: 672, height: 384 }],
      },
      readiness: {
        project_id: 'm4-1', workflow_kind: 'manual4', next_stage: 'production', all_done: false,
        can_confirm_storyboard: true, can_start_production: true, can_assemble_delivery: false,
        summary: { episodes: 1, shots: 1, promoted_assets: 1, total_assets: 1, deliveries: 0, accepted_deliveries: 0 },
        stages: [],
      },
    };

    const stub = new StudioStub([
      { method: 'GET', pattern: /^\/api\/v1\/loras$/, respond: () => jsonResponse({ items: loras, comfy_reachable: true, max_style_loras: 3 }) },
      { method: 'GET', pattern: /^\/api\/v1\/manual4\/projects\/m4-1\/production\/state$/, respond: () => jsonResponse(prodState) },
      {
        method: 'POST',
        pattern: /^\/api\/v1\/manual4\/projects\/m4-1\/production\/runs$/,
        respond: (_url, init) => {
          createPayload = JSON.parse(String(init?.body ?? '{}'));
          return jsonResponse({ run_id: 'run-2', state: prodState }, 201);
        },
      },
    ]);

    const user = userEvent.setup();
    render(
      <ScheduleProduceStagePage
        overview={{
          project: { id: 'm4-1', title: '四阶测试', description: '', language: 'Chinese', max_shot_seconds: 15, workflow_kind: 'manual4', created_at: '2026-11-25T00:00:00Z' },
          readiness: prodState.readiness as never,
          worker_online: true,
          gpu: { busy: false, active_leases: [], reconcile_required: [] },
          recent_events: [],
        }}
        onRefresh={async () => {}}
        onNextStage={() => {}}
      />,
    );

    expect(await screen.findByTestId('style-lora-panel')).toBeInTheDocument();
    // 历史 run 卡展示了冻结 LoRA 信息
    expect(screen.getByText(/LoRA\(MiniMax-H3\/h3-realism-people-t2v-i2v-r2v\.safetensors@0\.65\)/)).toBeInTheDocument();
    // 从最新快照水合出的 1 个 LoRA 自动保留并随新建批提交
    await user.click(screen.getByRole('button', { name: '确认排片并开拍' }));
    await waitFor(() => expect(createPayload).not.toBeNull());
    expect(createPayload?.['style_loras']).toHaveLength(1);

    stub.restore();
  });
});
