import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { MaterialsStagePage } from '../manual4/pages/MaterialsStagePage';
import { ScheduleProduceStagePage } from '../manual4/pages/ScheduleProduceStagePage';
import { Manual4App } from '../manual4/Manual4App';
import type { M4MaterialsState, M4Overview, M4ProductionState } from '../manual4/api/client';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

const REQUIREMENT = {
  id: 'req-1', project_id: 'p1', storyboard_revision_id: 'sb-1', family: 'character',
  entity_name: '林知远', variant_name: '', canonical_key: '林知远', description: '长风衣。',
  shot_ids: ['shot-1'], status: 'pending', bound_asset_version_id: null, user_declined: false,
  bound_asset: null, candidates: [], reusable_assets: [],
};

const STATE: M4MaterialsState = {
  confirmed_revision: null, requirements: [REQUIREMENT], assets: [], shots: [],
  readiness: {} as M4MaterialsState['readiness'],
};

const LIBRARY_ITEM = {
  library_asset_id: 'la-1', library_version_id: 'lv-1', content_sha256: 'e'.repeat(64),
  media_kind: 'image', category: 'character', rights_id: 'r-1', rights_status: 'approved',
  filename: 'lin.png', title: '林知远 · 长风衣', project_authorized: true,
  media_url: '/api/v1/asset-center/versions/lv-1/media',
};

function routes() {
  return [
    { method: 'GET', pattern: /manual4\/projects\/p1\/materials\/requirements$/, respond: () => jsonResponse(STATE) },
    { method: 'GET', pattern: /manual4\/projects\/p1\/materials\/library/, respond: () => jsonResponse({ items: [LIBRARY_ITEM], total: 1 }) },
    { method: 'POST', pattern: /manual4\/projects\/p1\/materials\/library\/adopt$/, respond: () => jsonResponse({ asset: { id: 'lv-1' }, bound_shots: ['shot-1'], state: STATE }) },
  ];
}

beforeEach(() => { localStorage.clear(); stub = new StudioStub(routes()); });
afterEach(() => { stub.restore(); localStorage.clear(); });

it('四阶来源下拉「从素材中心导入」弹窗选素材并走既有采纳执行者', async () => {
  const user = userEvent.setup();
  render(<MaterialsStagePage
    overview={{ project: { id: 'p1' } } as unknown as M4Overview}
    onRefresh={async () => {}} onNextStage={() => {}} />);
  const source = await screen.findByLabelText('素材来源');
  await user.selectOptions(source, 'import_center');
  const dialog = await screen.findByRole('dialog', { name: /从素材中心导入 · 林知远/ });
  expect(dialog).toBeInTheDocument();
  await user.click(await screen.findByRole('option', { name: /林知远 · 长风衣/ }));
  await waitFor(() => expect(stub.callsOf('POST', /materials\/library\/adopt$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /materials\/library\/adopt$/)[0].body).toMatchObject({
    version_id: 'lv-1', requirement_id: 'req-1', family: 'character',
  });
});

it('四阶归并素材需求支持全选/单选与批量操作菜单', async () => {
  const req2 = {
    ...REQUIREMENT,
    id: 'req-2',
    family: 'scene',
    entity_name: '雪山营地',
    canonical_key: '雪山营地',
  };
  const multiState: M4MaterialsState = {
    ...STATE,
    requirements: [REQUIREMENT, req2],
  };
  stub.restore();
  stub = new StudioStub([
    { method: 'GET', pattern: /manual4\/projects\/p1\/materials\/requirements$/, respond: () => jsonResponse(multiState) },
    { method: 'GET', pattern: /manual4\/projects\/p1\/materials\/library/, respond: () => jsonResponse({ items: [], total: 0 }) },
    {
      method: 'POST',
      pattern: /manual4\/projects\/p1\/materials\/batch$/,
      respond: () => jsonResponse({ action: 'decline', applied_ids: ['req-1', 'req-2'], skipped: [], errors: [], state: multiState }),
    },
  ]);

  const user = userEvent.setup();
  render(<MaterialsStagePage
    overview={{ project: { id: 'p1' } } as unknown as M4Overview}
    onRefresh={async () => {}} onNextStage={() => {}} />);

  const selectAll = await screen.findByLabelText('全选归并素材需求');
  await user.click(selectAll);
  expect(screen.getByText('已选 2 项')).toBeInTheDocument();

  await user.selectOptions(screen.getByLabelText('批量操作'), 'decline');
  await user.click(screen.getByRole('button', { name: '应用到所选' }));

  await waitFor(() => expect(stub.callsOf('POST', /materials\/batch$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /materials\/batch$/)[0].body).toMatchObject({
    action: 'decline',
    requirement_ids: ['req-1', 'req-2'],
  });
});

it('四阶排片生产支持一键批量冻结已输入提示词的镜头', async () => {
  const prodState: M4ProductionState = {
    confirmed_revision: { id: 'sb-1', revision: 1 },
    episodes: [
      {
        id: 'ep-1',
        episode_no: 1,
        episode_code: 'ep01',
        title: '大漠迷窟',
        planned_total_seconds: 16,
        shots: [
          {
            id: 'shot-1',
            episode_id: 'ep-1',
            shot_no: 1,
            shot_code: 'ep01-s01',
            revision: 1,
            target_seconds: 8,
            summary: '广角镜头',
            dialogue: [],
            aspect_ratio: '16:9',
            continuity_mode: 'cut',
            reference_manifest: [],
            frozen_prompt: null,
            draft_prompt: {
              id: 'p1', shot_id: 'shot-1', revision: 1, status: 'DRAFT',
              prompt_text: 'Shot 1 prompt', prompt_sha256: 'a'.repeat(64),
              shot_revision: 1, target_seconds: 8, generation_mode: 'Ref2VA',
              reference_manifest: [], reference_manifest_sha256: 'b'.repeat(64),
              warnings: [], frozen_by: null, frozen_at: null, invalidated_reason: null, updated_at: '',
            },
            active_prompt: {
              id: 'p1', shot_id: 'shot-1', revision: 1, status: 'DRAFT',
              prompt_text: 'Shot 1 prompt', prompt_sha256: 'a'.repeat(64),
              shot_revision: 1, target_seconds: 8, generation_mode: 'Ref2VA',
              reference_manifest: [], reference_manifest_sha256: 'b'.repeat(64),
              warnings: [], frozen_by: null, frozen_at: null, invalidated_reason: null, updated_at: '',
            },
            prompt_valid_for_run: false,
            prompt_invalid_reason: 'M4_PROMPT_NOT_FROZEN',
            latest_job: null,
          },
          {
            id: 'shot-2',
            episode_id: 'ep-1',
            shot_no: 2,
            shot_code: 'ep01-s02',
            revision: 1,
            target_seconds: 8,
            summary: '特写镜头',
            dialogue: [],
            aspect_ratio: '16:9',
            continuity_mode: 'cut',
            reference_manifest: [],
            frozen_prompt: null,
            draft_prompt: null,
            active_prompt: null,
            prompt_valid_for_run: false,
            prompt_invalid_reason: 'M4_PROMPT_NOT_FROZEN',
            latest_job: null,
          },
        ],
      },
    ],
    runs: [],
    options: { variants: [], accelerators: [], frames: [] },
    readiness: {} as M4Overview['readiness'],
  };
  stub.restore();
  stub = new StudioStub([
    { method: 'GET', pattern: /manual4\/projects\/p1\/production\/state$/, respond: () => jsonResponse(prodState) },
    {
      method: 'POST',
      pattern: /manual4\/projects\/p1\/prompts\/batch-freeze$/,
      respond: () => jsonResponse({ frozen_shot_ids: ['shot-1', 'shot-2'], skipped: [], errors: [], state: prodState }),
    },
  ]);

  const user = userEvent.setup();
  render(
    <ScheduleProduceStagePage
      overview={{ project: { id: 'p1' }, worker_online: true, gpu: { busy: false } } as unknown as M4Overview}
      onRefresh={async () => {}}
      onNextStage={() => {}}
    />,
  );

  // Initially only shot-1 has saved draft prompt (1 shot)
  const batchBtn = await screen.findByRole('button', { name: /批量冻结已输入提示词（1 镜）/ });
  expect(batchBtn).toBeEnabled();

  // Typing into shot-2 textarea updates target count to 2 shots
  const textareas = screen.getAllByPlaceholderText(/填写这一镜提交给 MiniMax-H3/);
  await user.type(textareas[1], 'Shot 2 entered prompt');
  const batchBtn2 = screen.getByRole('button', { name: /批量冻结已输入提示词（2 镜）/ });
  await user.click(batchBtn2);

  await waitFor(() => expect(stub.callsOf('POST', /prompts\/batch-freeze$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /prompts\/batch-freeze$/)[0].body).toMatchObject({
    items: [
      { shot_id: 'shot-1', prompt_text: 'Shot 1 prompt' },
      { shot_id: 'shot-2', prompt_text: 'Shot 2 entered prompt' },
    ],
  });
});

it('四阶按集排片选择第一集时隐藏第二集未冻结阻滞，且首集通过即可进成片阶段', async () => {
  const prodState: M4ProductionState = {
    confirmed_revision: { id: 'sb-1', revision: 1 },
    episodes: [
      {
        id: 'ep-1',
        episode_no: 1,
        episode_code: 'ep01',
        title: '大漠迷窟',
        planned_total_seconds: 8,
        shots: [
          {
            id: 'shot-1', episode_id: 'ep-1', shot_no: 1, shot_code: 'ep01-s01',
            revision: 1, target_seconds: 8, summary: '第1集镜头', dialogue: [],
            aspect_ratio: '16:9', continuity_mode: 'cut', reference_manifest: [],
            frozen_prompt: null, draft_prompt: null, active_prompt: null,
            prompt_valid_for_run: true, prompt_invalid_reason: null,
            latest_job: {
              id: 'j1', run_id: 'r1', episode_id: 'ep-1', shot_id: 'shot-1',
              shot_revision: 1, ordinal: 0, status: 'PASSED', attempt_count: 1,
              target_seconds: 8, continuity_mode: 'cut', provider: 'mock',
              output_sha256: 'a'.repeat(64), output_bytes: 100,
              measured_duration_s: 8, measured_width: 672, measured_height: 384, measured_fps: 24,
              qc_result: {}, failure_code: null, failure_detail: '', media_url: null,
            },
          },
        ],
      },
      {
        id: 'ep-2',
        episode_no: 2,
        episode_code: 'ep02',
        title: '幽冥玄棺',
        planned_total_seconds: 8,
        shots: [
          {
            id: 'shot-2', episode_id: 'ep-2', shot_no: 1, shot_code: 'ep02-s01',
            revision: 1, target_seconds: 8, summary: '第2集镜头', dialogue: [],
            aspect_ratio: '16:9', continuity_mode: 'cut', reference_manifest: [],
            frozen_prompt: null, draft_prompt: null, active_prompt: null,
            prompt_valid_for_run: false, prompt_invalid_reason: 'M4_PROMPT_NOT_FROZEN',
            latest_job: null,
          },
        ],
      },
    ],
    runs: [],
    options: { variants: [], accelerators: [], frames: [] },
    readiness: {} as M4Overview['readiness'],
  };
  stub.restore();
  stub = new StudioStub([
    { method: 'GET', pattern: /manual4\/projects\/p1\/production\/state$/, respond: () => jsonResponse(prodState) },
  ]);

  const user = userEvent.setup();
  const stage = {
    key: 'production' as const,
    title: '③ 排片生产',
    done: false,
    status: 'active' as const,
    blocker_count: 1,
    blockers: [
      {
        code: 'M4_SHOT_PROMPT_NOT_FROZEN',
        stage: 'production' as const,
        episode_id: 'ep-2',
        episode_code: 'ep02',
        shot_id: 'shot-2',
        shot_code: 'ep02-s01',
        message: '镜头 ep02-s01 尚未冻结有效的当前版本 H3 提示词。',
      },
    ],
    facts: { prompts_frozen: 1, shots_total: 2 },
  };
  render(
    <ScheduleProduceStagePage
      overview={{ project: { id: 'p1' }, worker_online: true, gpu: { busy: false } } as unknown as M4Overview}
      stage={stage}
      onRefresh={async () => {}}
      onNextStage={() => {}}
    />,
  );

  // Switch to 按集排片 -> defaults to ep01, so ep02 blocker is hidden and ep01 shows ready
  await user.selectOptions(await screen.findByLabelText(/排片范围/), 'episode');
  expect(screen.queryByText(/ep02-s01 尚未冻结/)).not.toBeInTheDocument();
  expect(screen.getByText('ep01 已就绪可开拍')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '下一步：产出成片' })).toBeEnabled();
});

it('四阶已完成镜头显示拍摄耗时并支持打开所在文件夹', async () => {
  const prodState: M4ProductionState = {
    confirmed_revision: { id: 'sb-1', revision: 1 },
    episodes: [
      {
        id: 'ep-1',
        episode_no: 1,
        episode_code: 'ep01',
        title: '大漠迷窟',
        planned_total_seconds: 8,
        shots: [
          {
            id: 'shot-1', episode_id: 'ep-1', shot_no: 1, shot_code: 'ep01-s01',
            revision: 1, target_seconds: 8, summary: '沙丘全景', dialogue: [],
            aspect_ratio: '16:9', continuity_mode: 'cut', reference_manifest: [],
            frozen_prompt: null, draft_prompt: null, active_prompt: null,
            prompt_valid_for_run: true, prompt_invalid_reason: null,
            latest_job: {
              id: 'j1', run_id: 'r1', episode_id: 'ep-1', shot_id: 'shot-1',
              shot_revision: 1, ordinal: 0, status: 'PASSED', attempt_count: 1,
              target_seconds: 8, continuity_mode: 'cut', provider: 'comfyui',
              output_sha256: 'a'.repeat(64), output_bytes: 100,
              measured_duration_s: 8, measured_width: 2016, measured_height: 1152, measured_fps: 24,
              qc_result: {}, failure_code: null, failure_detail: '',
              elapsed_seconds: 125,
              media_url: '/api/v1/manual4/projects/p1/jobs/j1/media',
            },
          },
        ],
      },
    ],
    runs: [],
    options: { variants: [], accelerators: [], frames: [] },
    readiness: {} as M4Overview['readiness'],
  };
  stub.restore();
  stub = new StudioStub([
    { method: 'GET', pattern: /manual4\/projects\/p1\/production\/state$/, respond: () => jsonResponse(prodState) },
    { method: 'POST', pattern: /manual4\/projects\/p1\/jobs\/j1\/open-folder$/, respond: () => jsonResponse({ folder: 'F:/renders', file: 'F:/renders/ep01-s01.mp4' }) },
  ]);

  const user = userEvent.setup();
  render(
    <ScheduleProduceStagePage
      overview={{ project: { id: 'p1' }, worker_online: true, gpu: { busy: false } } as unknown as M4Overview}
      onRefresh={async () => {}}
      onNextStage={() => {}}
    />,
  );

  expect(await screen.findByText(/拍摄耗时 2分5秒（125秒）/)).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /打开所在文件夹/ }));
  await waitFor(() => expect(stub.callsOf('POST', /jobs\/j1\/open-folder$/)).toHaveLength(1));
});

it('四阶开拍后全局顶部显示动态进度条与当前执行阶段', async () => {
  const overviewWithProgress: M4Overview = {
    project: {
      id: 'p1', title: '手搓1122', description: '', language: 'Chinese',
      max_shot_seconds: 15, workflow_kind: 'manual4', created_at: '2026-11-22T00:00:00Z',
    },
    readiness: {
      project_id: 'p1',
      workflow_kind: 'manual4',
      next_stage: 'production',
      all_done: false,
      stages: [
        { key: 'storyboard', title: '① 人工分镜书', done: true, status: 'done', blocker_count: 0, blockers: [], facts: {} },
        { key: 'materials', title: '② 素材生成', done: true, status: 'done', blocker_count: 0, blockers: [], facts: {} },
        { key: 'production', title: '③ 排片生产', done: false, status: 'active', blocker_count: 0, blockers: [], facts: {} },
        { key: 'delivery', title: '④ 产出成片', done: false, status: 'pending', blocker_count: 0, blockers: [], facts: {} },
      ],
      can_confirm_storyboard: true,
      can_start_production: true,
      can_assemble_delivery: false,
      summary: {},
    },
    worker_online: true,
    gpu: { busy: true, active_leases: [], reconcile_required: [] },
    production_progress: {
      run_id: 'run-1',
      status: 'RUNNING',
      scope_kind: 'episode',
      provider: 'comfyui',
      phase: 'RENDERING',
      phase_label: 'H3 正在渲染视频',
      is_working: true,
      progress_pct: 32,
      total_jobs: 8,
      passed_jobs: 2,
      failed_jobs: 0,
      needs_review_jobs: 0,
      queued_jobs: 5,
      current_shot_no: 3,
      active_job: { id: 'j3', shot_id: 's3', shot_code: 'ep01-s03', status: 'RUNNING', ordinal: 2 },
    },
    recent_events: [],
  };
  stub.restore();
  stub = new StudioStub([
    { method: 'GET', pattern: /manual4\/system\/status$/, respond: () => jsonResponse({ line: 'manual4', runtime_version: '2.0', schema_version: '1', environment: { capabilities: {} } }) },
    { method: 'GET', pattern: /manual4\/projects\/p1\/overview$/, respond: () => jsonResponse(overviewWithProgress) },
    { method: 'GET', pattern: /manual4\/projects\/p1\/production\/state$/, respond: () => jsonResponse({ confirmed_revision: null, episodes: [], runs: [], options: { variants: [], accelerators: [], frames: [] }, readiness: overviewWithProgress.readiness }) },
  ]);

  render(<Manual4App projectId="p1" initialStage="production" onBackToProjects={() => {}} />);
  const bar = await screen.findByRole('progressbar', { name: '四阶视频生产进度' });
  expect(bar).toHaveAttribute('aria-valuenow', '32');
  expect(screen.getByText('H3 正在渲染视频')).toBeInTheDocument();
  expect(screen.getByText(/正在生成第 3 \/ 8 镜（ep01-s03）/)).toBeInTheDocument();
  expect(screen.getByText('3. H3 渲染')).toHaveClass('active');
});

it('四阶排片参数在切换页面后保留快照，且无本地缓存时从最近执行快照恢复', async () => {
  const prodState: M4ProductionState = {
    confirmed_revision: { id: 'sb-1', revision: 1 },
    episodes: [
      { id: 'ep-1', episode_no: 1, episode_code: 'ep01', title: '大漠迷窟', planned_total_seconds: 8, shots: [] },
      { id: 'ep-2', episode_no: 2, episode_code: 'ep02', title: '幽冥玄棺', planned_total_seconds: 8, shots: [] },
    ],
    runs: [
      {
        id: 'run-1',
        project_id: 'p1',
        execution_snapshot_id: 'snap-1',
        scope_kind: 'episode',
        scope_target_ids: ['ep-2'],
        provider: 'comfyui',
        config: {
          variant_id: 'h3-ref2va-int8-convrot',
          accelerator_id: 'pdd8',
          resolution_scale: 1.0,
          second_pass: { enabled: true, denoise: 0.25, latent_scale: 1.5, nfe: '8' },
          seed: 123,
        },
        status: 'COMPLETED',
        worker_id: null,
        error_code: null,
        error_detail: '',
        jobs: [],
        created_at: '2026-11-22T00:00:00Z',
      },
    ],
    options: {
      variants: [{ variant_id: 'h3-ref2va-int8-convrot', label: 'H3 Ref2VA INT8 ConvRot', verified: true, accelerators: ['pdd8', 'native20'] }],
      accelerators: [
        { id: 'pdd8', steps: 8, requires_pdd: true, label: 'PDD 8 步', verified: true },
        { id: 'native20', steps: 20, requires_pdd: false, label: '原生 20 步', verified: true },
      ],
      second_pass: { supported: true },
      frames: [
        { orientation: 'landscape', aspect_ratio: '16:9', scale: 0.5, width: 672, height: 384 },
        { orientation: 'landscape', aspect_ratio: '16:9', scale: 1.0, width: 1344, height: 768 },
      ],
    },
    readiness: {} as M4Overview['readiness'],
  };
  stub.restore();
  stub = new StudioStub([
    { method: 'GET', pattern: /manual4\/projects\/p1\/production\/state$/, respond: () => jsonResponse(prodState) },
  ]);

  // 1) With empty localStorage, seeds from latest backend execution snapshot runs[0].config
  const user = userEvent.setup();
  const first = render(
    <ScheduleProduceStagePage
      overview={{ project: { id: 'p1' }, worker_online: true, gpu: { busy: false } } as unknown as M4Overview}
      onRefresh={async () => {}}
      onNextStage={() => {}}
    />,
  );
  await waitFor(() => expect(screen.getByLabelText(/排片范围/)).toHaveValue('episode'));
  expect(screen.getByLabelText(/选择分集/)).toHaveValue('ep-2');
  expect(screen.getByLabelText(/Ref2VA 采样/)).toHaveValue('pdd8');
  expect(screen.getByLabelText(/分辨率档位/)).toHaveValue('1');
  expect(screen.getByLabelText(/随机种子/)).toHaveValue(123);
  expect(screen.getByLabelText(/二采精修/)).toBeChecked();
  expect(screen.getByLabelText(/放大倍率/)).toHaveValue(1.5);

  // 2) User changes to ep-1 + seed 888, unmounts (simulating tab switch), and remounts -> preserved!
  await user.selectOptions(screen.getByLabelText(/选择分集/), 'ep-1');
  const seedInput = screen.getByLabelText(/随机种子/);
  await user.clear(seedInput);
  await user.type(seedInput, '888');
  first.unmount();

  render(
    <ScheduleProduceStagePage
      overview={{ project: { id: 'p1' }, worker_online: true, gpu: { busy: false } } as unknown as M4Overview}
      onRefresh={async () => {}}
      onNextStage={() => {}}
    />,
  );
  expect(await screen.findByLabelText(/选择分集/)).toHaveValue('ep-1');
  expect(screen.getByLabelText(/随机种子/)).toHaveValue(888);
  expect(screen.getByLabelText(/Ref2VA 采样/)).toHaveValue('pdd8');
  expect(screen.getByLabelText(/分辨率档位/)).toHaveValue('1');
  expect(screen.getByLabelText(/二采精修/)).toBeChecked();
});

it('远程分镜解析未勾选授权前直接提示并阻止 409 往返，且排片批次卡片内提供对账按钮', async () => {
  let importCalls = 0;
  stub.restore();
  stub = new StudioStub([
    {
      method: 'GET',
      pattern: /manual4\/projects\/p1\/storyboard\/draft$/,
      respond: () =>
        jsonResponse({
          draft: {
            revision_id: null,
            revision: 0,
            status: 'EMPTY',
            source_sha256: '',
            summary_sha256: '',
            max_shot_seconds: 15,
            payload: { episodes: [], ambiguities: [], extractor: 'manual' },
            confirmed_at: null,
          },
          confirmed: null,
          source_documents: [],
          confirmed_episodes: [],
          confirmed_shots: [],
        }),
    },
    {
      method: 'GET',
      pattern: /manual4\/projects\/p1\/llm-options$/,
      respond: () =>
        jsonResponse([
          { id: 'llm-remote', name: 'DeepSeek', provider: 'deepseek', model: 'deepseek-chat', base_url: 'https://api.deepseek.com', is_local: false },
        ]),
    },
    {
      method: 'POST',
      pattern: /manual4\/projects\/p1\/storyboard\/import$/,
      respond: () => {
        importCalls += 1;
        return jsonResponse({ detail: { code: 'M4_REMOTE_LLM_CONSENT_REQUIRED' } }, 409);
      },
    },
  ]);

  const user = userEvent.setup();
  const { StoryboardStagePage } = await import('../manual4/pages/StoryboardStagePage');
  const sbView = render(
    <StoryboardStagePage
      overview={{ project: { id: 'p1', max_shot_seconds: 15 } } as unknown as M4Overview}
      onRefresh={async () => {}}
      onNextStage={() => {}}
    />,
  );
  await user.type(await screen.findByLabelText('粘贴分镜文本'), '第1集 第1镜 沙丘');
  await user.click(screen.getByLabelText('使用辅助模型解析非结构化文本'));
  expect(await screen.findByText(/需先勾选上方远程授权确认/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '解析粘贴文本' })).toBeDisabled();
  expect(importCalls).toBe(0);
  sbView.unmount();

  const prodState: M4ProductionState = {
    confirmed_revision: { id: 'sb-1', revision: 1 },
    episodes: [
      {
        id: 'ep-1',
        episode_no: 1,
        episode_code: 'ep01',
        title: '水乡雨夜',
        planned_total_seconds: 15,
        shots: [
          {
            id: 'shot-1', episode_id: 'ep-1', shot_no: 1, shot_code: 'ep01-s01',
            revision: 1, target_seconds: 15, summary: 'Water-Town Gaze', dialogue: [],
            aspect_ratio: '16:9', continuity_mode: 'cut', reference_manifest: [],
            frozen_prompt: null, draft_prompt: null, active_prompt: null,
            prompt_valid_for_run: true, prompt_invalid_reason: null,
            latest_job: null,
          },
        ],
      },
    ],
    runs: [
      {
        id: 'run-review-1',
        project_id: 'p1',
        execution_snapshot_id: 'snap-1',
        scope_kind: 'project',
        provider: 'comfyui',
        status: 'NEEDS_REVIEW',
        worker_id: null,
        error_code: null,
        error_detail: '',
        jobs: [
          {
            id: 'job-review-1',
            run_id: 'run-review-1',
            episode_id: 'ep-1',
            shot_id: 'shot-1',
            shot_revision: 1,
            ordinal: 0,
            status: 'NEEDS_REVIEW',
            attempt_count: 1,
            target_seconds: 15,
            continuity_mode: 'cut',
            provider: 'comfyui',
            output_sha256: '',
            output_bytes: 0,
            measured_duration_s: null,
            measured_width: null,
            measured_height: null,
            measured_fps: null,
            qc_result: {},
            failure_code: 'M4_COMFY_SUBMISSION_OUTCOME_UNKNOWN',
            failure_detail: 'ComfyUI 轮询超时，需要核对队列与历史。',
            media_url: null,
            attempts: [{ id: 'att-1', attempt_no: 1, status: 'NEEDS_REVIEW', prompt_id: 'eb4230d0-test', created_at: '2026-11-22T06:00:00Z' }],
          },
        ],
        created_at: '2026-11-22T06:00:00Z',
      },
    ],
    options: { variants: [], accelerators: [], frames: [] },
    readiness: {} as M4Overview['readiness'],
  };
  stub.restore();
  stub = new StudioStub([
    { method: 'GET', pattern: /manual4\/projects\/p1\/production\/state$/, respond: () => jsonResponse(prodState) },
  ]);
  render(
    <ScheduleProduceStagePage
      overview={{ project: { id: 'p1' }, worker_online: true, gpu: { busy: false } } as unknown as M4Overview}
      onRefresh={async () => {}}
      onNextStage={() => {}}
    />,
  );
  expect(await screen.findByText(/prompt_id=eb4230d0-test/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '人工对账重排' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '确认失败' })).toBeEnabled();
});