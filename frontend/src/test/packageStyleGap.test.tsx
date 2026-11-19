/** 导演包预检风格缺口就地恢复 + 制片准备模式单一裁决（2026-11-19 用户报障）。
 *
 *  1. 预检报 SHOT_STYLE_MISMATCH 时，本页给出既有执行者（freeze/shots/style）的
 *     恢复动作，而不是只留一墙错误码；
 *  2. NVFP4 与 W6A8 是两个变体，不得显示同名；
 *  3. 制片准备面板的「当前模式」读 /setup/verdict（与顶栏同一裁决），
 *     本地 select 只是本次维护请求的执行器。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { describeVariantName } from '../components/DirectorShootPanel';
import { PreproductionPanel } from '../components/PreproductionPanel';
import {
  PRODUCTION_MODE_READY, StudioStub, SYSTEM_STATUS, baseProject, baseRoutes,
  gotoDirectorPage, jsonResponse,
} from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

function episode() {
  return {
    episode_id: 'ep-01', order: 1, title: '第1集', planned_seconds: 60,
    state: 'READY_TO_PACKAGE', state_label: '可打包',
    storyboard: { section_count: 2, landed_section_count: 2, pending_sections: [],
      ledger_task_id: 'task-1', ledger_task_status: 'APPLIED', state_version: 3, blocker: null },
    shots: { landed_count: 4, confirmed_count: 4, confirmed: true, problems: [] },
    materials: { missing_count: 0, missing: [] },
    package: null, production: null, delivery: null,
    next_action: { kind: 'BUILD_PACKAGE', label: '把这一集生成导演包' },
  };
}

function stubPackagePage() {
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/overview/,
    respond: () => jsonResponse({
      project_id: 'proj-1', title: '雾港夜账',
      plan: { version: 2, sha256: 'b'.repeat(64), status: 'CONFIRMED', episode_count: 1 },
      style: { version: 1, sha256: 'a'.repeat(64) },
      counts: { episode_count: 1, confirmed_episode_count: 1, shootable_episode_count: 1,
        delivered_episode_count: 0, shot_count: 4, passed_shot_count: 0 },
      episodes: [episode()], next_action: null, package: null, run: null,
      storyboard_task: { task_id: 'task-1', status: 'SUCCEEDED', state_version: 3 },
    }) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/variant-availability/,
    respond: () => jsonResponse({ project_id: 'proj-1', variants: [
      { variant_id: 'h3-ref2va-int8-convrot', available: true, reason: '本机已就绪' },
      { variant_id: 'ref2va-lowend-candidate', available: false, reason: '本机没有这套权重' },
      { variant_id: 'ref2va-nvfp4-lowend', available: false, reason: '本机没有这套权重' },
    ] }) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director\/strategy-options$/,
    respond: () => jsonResponse({
      model_variants: [
        { variant_id: 'h3-ref2va-int8-convrot', label: 'h3-ref2va-int8-convrot',
          acceptance_status: 'SUPPORTED', verified: true, default_recommended: true },
        { variant_id: 'ref2va-lowend-candidate', label: 'ref2va-lowend-candidate',
          acceptance_status: 'CANDIDATE', verified: false },
        { variant_id: 'ref2va-nvfp4-lowend', label: 'ref2va-nvfp4-lowend',
          acceptance_status: 'CANDIDATE', verified: false },
      ],
      accelerators: [{ accelerator_id: 'pdd8', steps: 8, verified: true }],
      second_pass: { supported: true, recipes: ['h3-tail-refine-v1'] },
      upscale: [],
    }) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/system\/status$/, respond: () => jsonResponse({
    ...SYSTEM_STATUS, render_mode: 'comfyui', h3_connected: true, production_mode: PRODUCTION_MODE_READY }) });
  stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/verdict$/, respond: () => jsonResponse({
    ...PRODUCTION_MODE_READY, code: null, services_verified: true, validation_blockers: [],
    has_active_runtime: true, has_candidate_runtime: false, runtime_error: null }) });
}

describe('导演包预检风格缺口：就地恢复，不另起执行链', () => {
  it('SHOT_STYLE_MISMATCH 时显示「让镜头执行当前冻结风格」，点击走 freeze/shots/style', async () => {
    stubPackagePage();
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director\/production\/start$/,
      respond: () => jsonResponse({ detail: {
        code: 'DIRECTOR_PACKAGE_NOT_READY',
        message: '这一版拍摄方案还没有通过预检；按下面的缺项处理后重试',
        package_id: 'package-1', revision: 1,
        blockers: [{ code: 'SHOT_STYLE_MISMATCH', message: '镜头「导演分镜-ep-01-shot-01」不执行当前冻结风格' }],
      } }, 409) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/freeze\/shots\/style$/,
      respond: () => jsonResponse({ style_version_id: 'style-1', revised_shot_ids: ['s1', 's2'] }) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);
    const [start] = await screen.findAllByRole('button', { name: /确认拍摄方案并开始真实制作/ });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);

    const recover = await screen.findByRole('button', { name: /让镜头执行当前冻结风格/ });
    await user.click(recover);
    await waitFor(() => expect(stub.callsOf('POST', /freeze\/shots\/style$/)).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole('button', { name: /让镜头执行当前冻结风格/ })).toBeNull());
    expect(await screen.findByText(/已让 2 个镜头执行当前冻结风格/)).toBeInTheDocument();
  });

  it('⑤ 是画幅/分辨率唯一控制节点：竖屏 0.5 档随开拍唯一赋值 output', async () => {
    stubPackagePage();
    let posted: { output?: { width: number; height: number; fps: number } } | null = null;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director\/production\/start$/,
      respond: (_url, init) => {
        posted = JSON.parse(String(init?.body));
        return jsonResponse({
          package_id: 'package-1', package_revision: 1, package_built: true,
          run_id: 'run-1', run_status: 'QUEUED', created: true, episode_ids: ['ep-01'],
          episodes: [{ episode_id: 'ep-01', position: 1, shot_count: 4 }],
        }, 201);
      } });
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);
    await screen.findByText('这次要拍哪几集');
    await user.click(screen.getByRole('radio', { name: '竖屏 9:16' }));
    await user.selectOptions(screen.getByLabelText('分辨率档位'), '0.5');
    const [start] = await screen.findAllByRole('button', { name: /确认拍摄方案并开始真实制作/ });
    await waitFor(() => expect(start).toBeEnabled());
    await user.click(start);
    await waitFor(() => expect(posted).not.toBeNull());
    const body = posted as { output?: { width: number; height: number; fps: number } } | null;
    expect(body?.output).toEqual({ width: 384, height: 672, fps: 24 });
  });

  it('NVFP4 与 W6A8 显示为两个不同变体，不再同名', async () => {
    stubPackagePage();
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);
    await screen.findByText('这次要拍哪几集');
    expect(screen.getAllByText('低显存候选 · W6A8')).toHaveLength(1);
    expect(screen.getAllByText('低显存候选 · NVFP4')).toHaveLength(1);
  });
});

describe('变体标签', () => {
  it('id 含 lowend 的 NVFP4 先于 W6A8 匹配', () => {
    expect(describeVariantName('ref2va-nvfp4-lowend')).toBe('低显存候选 · NVFP4');
    expect(describeVariantName('ref2va-lowend-candidate')).toBe('低显存候选 · W6A8');
    expect(describeVariantName('h3-ref2va-int8-convrot')).toBe('标准画质 · INT8');
  });
});

describe('制片准备面板模式挂单一裁决', () => {
  it('当前模式读 /setup/verdict，执行器默认由裁决派生', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/verdict$/, respond: () => jsonResponse({
      ...PRODUCTION_MODE_READY, code: null, services_verified: true, validation_blockers: [],
      has_active_runtime: true, has_candidate_runtime: false, runtime_error: null }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/comfyops\/inventory$/, respond: () => jsonResponse([]) });
    render(<PreproductionPanel projectId="proj-1" onRefresh={async () => {}} />);
    expect(await screen.findByText(/当前模式：真实制作已就绪/)).toBeInTheDocument();
    expect(screen.queryByText(/模拟演练/)).toBeNull();
    await waitFor(() => expect((screen.getByLabelText('本次维护请求的执行器') as HTMLSelectElement).value).toBe('local'));
  });

  it('裁决为演示时执行器默认模拟，标题仍读裁决', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/setup\/verdict$/, respond: () => jsonResponse({
      mode: 'DEMO', label: '演示体验', detail: '', action: 'enable_real', code: null,
      runtime_status: null, runtime_error: null, services_verified: false,
      validation_blockers: [], has_active_runtime: false, has_candidate_runtime: false }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/comfyops\/inventory$/, respond: () => jsonResponse([]) });
    render(<PreproductionPanel projectId="proj-1" onRefresh={async () => {}} />);
    expect(await screen.findByText(/当前模式：演示体验/)).toBeInTheDocument();
    await waitFor(() => expect((screen.getByLabelText('本次维护请求的执行器') as HTMLSelectElement).value).toBe('fake'));
  });
});
