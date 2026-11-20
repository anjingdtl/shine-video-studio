import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import type { M4SystemStatus } from '../manual4/api/client';
import { StudioStub, SYSTEM_STATUS, jsonResponse } from './studioStub';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.location.hash = '';
});

function makeM4Overview(projectId: string, title: string) {
  return {
    project: {
      id: projectId,
      title,
      description: '',
      language: 'Chinese',
      max_shot_seconds: 15,
      workflow_kind: 'manual4',
      created_at: '2026-11-20T00:00:00Z',
    },
    readiness: {
      project_id: projectId,
      workflow_kind: 'manual4',
      next_stage: 'storyboard',
      all_done: false,
      can_confirm_storyboard: false,
      can_start_production: false,
      can_assemble_delivery: false,
      summary: { episodes: 0, shots: 0, promoted_assets: 0, total_assets: 0, deliveries: 0, accepted_deliveries: 0 },
      stages: [
        { key: 'storyboard', title: '① 人工分镜书', done: false, status: 'active', blocker_count: 1, blockers: [{ code: 'M4_STORYBOARD_NOT_CONFIRMED', stage: 'storyboard', message: '尚未确认分镜' }], facts: { episodes: 0, shots: 0, planned_seconds: 0 } },
        { key: 'materials', title: '② 素材生成', done: false, status: 'blocked', blocker_count: 1, blockers: [{ code: 'M4_MATERIALS_WAIT_STORYBOARD', stage: 'materials', message: '请先确认分镜' }], facts: { requirements_ready: 0, requirements_total: 0, promoted_assets: 0, total_assets: 0, shots_bound: 0, shots_total: 0 } },
        { key: 'production', title: '③ 排片生产', done: false, status: 'blocked', blocker_count: 1, blockers: [{ code: 'M4_PRODUCTION_WAIT_MATERIALS', stage: 'production', message: '请先准备素材' }], facts: { prompts_frozen: 0, shots_total: 0 } },
        { key: 'delivery', title: '④ 产出成片', done: false, status: 'blocked', blocker_count: 1, blockers: [{ code: 'M4_DELIVERY_WAIT_PRODUCTION', stage: 'delivery', message: '请先完成拍摄' }], facts: { deliveries: 0, accepted_deliveries: 0, episodes: 0 } },
      ],
    },
    worker_online: true,
    gpu: { busy: false, active_leases: [], reconcile_required: [] },
    recent_events: [],
  };
}

describe('P1 平台选线与四阶隔离挂载', () => {
  it('选择四阶新建作品时只调用 manual4 接口，不触发七阶 journey/assets/shots 请求', async () => {
    const user = userEvent.setup();
    const d7Id = 'proj-d7-1';
    const m4Id = 'proj-m4-1';
    let projects: Array<Record<string, unknown>> = [
      { id: d7Id, title: '旧七阶作品', description: '', language: 'Chinese', created_at: '2026-11-20T00:00:00Z', frozen_style_version_id: null, style_draft: null, allow_remote_review: false, auto_repair_enabled: false, quality_policy: null },
    ];
    let platformProjects: Array<Record<string, unknown>> = [
      { id: d7Id, title: '旧七阶作品', description: '', language: 'Chinese', workflow_kind: 'director7', selection_revision: 1, created_at: '2026-11-20T00:00:00Z' },
    ];

    const stub = new StudioStub([
      { method: 'GET', pattern: /^\/api\/v1\/system\/status$/, respond: () => jsonResponse(SYSTEM_STATUS) },
      { method: 'GET', pattern: /^\/api\/v1\/projects$/, respond: () => jsonResponse(projects) },
      { method: 'GET', pattern: /^\/api\/v1\/platform\/projects$/, respond: () => jsonResponse(platformProjects) },
      { method: 'GET', pattern: new RegExp(`^/api/v1/projects/${d7Id}$`), respond: () => jsonResponse(projects[0]) },
      { method: 'GET', pattern: new RegExp(`^/api/v1/projects/${d7Id}/(assets|styles|shots|jobs|deliveries)$`), respond: () => jsonResponse([]) },
      { method: 'GET', pattern: new RegExp(`^/api/v1/projects/${d7Id}/events`), respond: () => jsonResponse({ items: [], total: 0 }) },
      { method: 'GET', pattern: /^\/api\/v1\/provider-profiles$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /^\/api\/v1\/agent-routes$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /^\/api\/v1\/workflow-profiles/, respond: () => jsonResponse([]) },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/projects/${d7Id}/journey$`),
        respond: () => jsonResponse({
          project_id: d7Id, next_stage: 'script',
          stages: [{ id: 'script', done: false, blockers: [], facts: {} }],
        }),
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/projects/${d7Id}/director/overview`),
        respond: () => jsonResponse({ project_id: d7Id, run: null, episodes: [] }),
      },
      {
        method: 'POST',
        pattern: /^\/api\/v1\/platform\/manual4-projects$/,
        respond: (_url, init) => {
          const payload = JSON.parse(String(init?.body ?? '{}'));
          const created = {
            id: m4Id,
            title: payload.title,
            description: '',
            language: 'Chinese',
            workflow_kind: 'manual4',
            selection_revision: 1,
            created_at: '2026-11-20T00:01:00Z',
          };
          projects = [{ ...created, frozen_style_version_id: null, style_draft: null, allow_remote_review: false, auto_repair_enabled: false, quality_policy: null }, ...projects];
          platformProjects = [created, ...platformProjects];
          return jsonResponse(created, 201);
        },
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/system/status$`),
        respond: () => jsonResponse({
          line: 'manual4',
          runtime_version: '2.0.0',
          schema_version: 'm4.1',
          environment: {
            schema: 'shine.environment.v1',
            comfyui_root: 'F:/ComfyUI',
            services_verified: true,
            capabilities: {
              h3: { state: 'READY', label: 'H3 制片' },
              image: { state: 'VERIFIED_RENDER', label: 'Qwen 出图' },
            },
          },
        } satisfies M4SystemStatus),
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/overview$`),
        respond: () => jsonResponse(makeM4Overview(m4Id, '四阶短片测试')),
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/llm-options$`),
        respond: () => jsonResponse([]),
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/storyboard/draft$`),
        respond: () => jsonResponse({
          project_id: m4Id,
          max_shot_seconds: 15,
          draft: {
            revision_id: null, revision: 0, status: 'EMPTY', source_sha256: '', summary_sha256: '',
            max_shot_seconds: 15, payload: { episodes: [], ambiguities: [], extractor: 'manual' }, confirmed_at: null,
          },
          confirmed: null,
          source_documents: [],
          confirmed_episodes: [],
          confirmed_shots: [],
        }),
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/materials/library`),
        respond: () => jsonResponse({ items: [], total: 0 }),
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/materials/requirements$`),
        respond: () => jsonResponse({
          confirmed_revision: null,
          slot_limits: { image: 6, audio: 2, video: 2 },
          requirements: [],
          assets: [],
          shots: [],
          readiness: makeM4Overview(m4Id, '四阶短片测试').readiness,
        }),
      },
    ]);

    render(<App />);
    await screen.findAllByText('旧七阶作品');

    await user.selectOptions(screen.getByLabelText('选择生产线'), 'manual4');
    await user.type(screen.getByLabelText('新作品名称'), '四阶短片测试');
    await user.click(screen.getByRole('button', { name: /新建作品/ }));

    await screen.findByTestId('manual4-workbench');
    expect(await screen.findByText('ComfyUI 已就绪 · H3 已就绪 · Qwen 出图 已就绪 · Worker 在线')).toBeVisible();
    expect(screen.getByRole('button', { name: /① 人工分镜书/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /② 素材生成/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /③ 排片生产/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /④ 产出成片/ })).toBeInTheDocument();

    // 切到第 ② 步
    await user.click(screen.getByRole('button', { name: /② 素材生成/ }));
    expect(screen.getByRole('heading', { name: '② 素材生成' })).toBeInTheDocument();

    // 返回项目管理页：列表中四阶项目走 /manual4/projects/{id}/overview，不走七阶 journey
    await user.click(screen.getByRole('button', { name: /返回项目管理/ }));
    await screen.findByText('H3 自主四阶');

    // 验证四阶项目从未向七阶子接口发过请求
    await waitFor(() => {
      const m4Calls = stub.callsOf('GET', new RegExp(`/manual4/projects/${m4Id}/overview`));
      expect(m4Calls.length).toBeGreaterThanOrEqual(2);
    });
    const forbiddenSevenCalls = stub.calls.filter((call) =>
      new RegExp(`/api/v1/projects/${m4Id}/`).test(call.url),
    );
    expect(forbiddenSevenCalls).toEqual([]);
  });

  it('P4 顶栏切换至四阶作品自动续接阶段、隐藏七阶导航并保留动态槽位与非 404 状态', async () => {
    const user = userEvent.setup();
    const d7Id = 'proj-d7-a';
    const m4Id = 'proj-m4-b';
    const projects = [
      { id: d7Id, title: '七阶老项目', description: '', language: 'Chinese', created_at: '2026-11-20T00:00:00Z', frozen_style_version_id: null, style_draft: null, allow_remote_review: false, auto_repair_enabled: false, quality_policy: null },
      { id: m4Id, title: '四阶续接项目', description: '', language: 'Chinese', created_at: '2026-11-20T00:01:00Z', frozen_style_version_id: null, style_draft: null, allow_remote_review: false, auto_repair_enabled: false, quality_policy: null },
    ];
    let failPlatform = false;
    const m4OverviewMaterials = makeM4Overview(m4Id, '四阶续接项目');
    m4OverviewMaterials.readiness.next_stage = 'materials';
    m4OverviewMaterials.readiness.stages[0].done = true;

    new StudioStub([
      { method: 'GET', pattern: /^\/api\/v1\/system\/status$/, respond: () => jsonResponse(SYSTEM_STATUS) },
      { method: 'GET', pattern: /^\/api\/v1\/projects$/, respond: () => jsonResponse(projects) },
      {
        method: 'GET',
        pattern: /^\/api\/v1\/platform\/projects$/,
        respond: () =>
          failPlatform
            ? { status: 500, body: { detail: 'platform temporarily down' } }
            : jsonResponse([
                { id: d7Id, title: '七阶老项目', description: '', language: 'Chinese', workflow_kind: 'director7', selection_revision: 1, created_at: '2026-11-20T00:00:00Z' },
                { id: m4Id, title: '四阶续接项目', description: '', language: 'Chinese', workflow_kind: 'manual4', selection_revision: 1, created_at: '2026-11-20T00:01:00Z' },
              ]),
      },
      { method: 'GET', pattern: new RegExp(`^/api/v1/projects/${d7Id}$`), respond: () => jsonResponse(projects[0]) },
      { method: 'GET', pattern: new RegExp(`^/api/v1/projects/${d7Id}/(assets|styles|shots|jobs|deliveries)$`), respond: () => jsonResponse([]) },
      { method: 'GET', pattern: new RegExp(`^/api/v1/projects/${d7Id}/events`), respond: () => jsonResponse({ items: [], total: 0 }) },
      { method: 'GET', pattern: /^\/api\/v1\/provider-profiles$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /^\/api\/v1\/agent-routes$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /^\/api\/v1\/workflow-profiles/, respond: () => jsonResponse([]) },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/projects/${d7Id}/journey$`),
        respond: () => jsonResponse({ project_id: d7Id, next_stage: 'script', stages: [{ id: 'script', done: false, blockers: [], facts: {} }] }),
      },
      { method: 'GET', pattern: new RegExp(`^/api/v1/projects/${d7Id}/director/overview`), respond: () => jsonResponse({ project_id: d7Id, run: null, episodes: [] }) },
      { method: 'GET', pattern: /^\/api\/v1\/manual4\/system\/status$/, respond: () => jsonResponse({ runtime_version: '2.0.0', schema_version: 'm4.1', worker_online: true, environment: { installation: { ready: true }, capabilities: {} } }) },
      { method: 'GET', pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/overview$`), respond: () => jsonResponse(m4OverviewMaterials) },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/materials/library`),
        respond: () => jsonResponse({ items: [], total: 0 }),
      },
      {
        method: 'GET',
        pattern: new RegExp(`^/api/v1/manual4/projects/${m4Id}/materials/requirements$`),
        respond: () => jsonResponse({
          confirmed_revision: { id: 'rev-1', revision: 1 },
          slot_limits: { image: 9, audio: 3, video: 3 },
          requirements: [],
          assets: [],
          shots: [],
          readiness: m4OverviewMaterials.readiness,
        }),
      },
    ]);

    render(<App />);
    await screen.findAllByText('七阶老项目');

    // 先进入七阶作品详情（或从项目管理页进入），使当前处于作品工作流页
    await user.click(screen.getByRole('button', { name: '打开当前作品' }));
    expect(screen.getByRole('button', { name: /^成片库$/ })).toBeInTheDocument();

    // 从顶栏切换到四阶作品 -> 不应停在 #m4/storyboard，而应自动续接到 next_stage='materials' 且隐藏七阶成片库导航
    const projectSelect = screen.getByLabelText('快速切换作品');
    await user.selectOptions(projectSelect, m4Id);

    await screen.findByTestId('manual4-workbench');
    await screen.findByRole('heading', { name: '② 素材生成' });
    expect(window.location.hash).toBe('#m4/materials');
    expect(screen.queryByRole('button', { name: /^成片库$/ })).toBeNull();
    expect(screen.getByText(/最多 9 图 \+ 3 音频 \+ 3 视频/)).toBeInTheDocument();

    // 若 /platform/projects 偶发 500，不得把已知四阶项目抹回七阶，应保留工作台与错误提示
    failPlatform = true;
    await user.click(screen.getByRole('button', { name: '刷新全部数据' }));
    await waitFor(() => {
      expect(screen.getByTestId('manual4-workbench')).toBeInTheDocument();
    });
  });
});
