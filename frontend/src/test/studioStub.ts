import { vi } from 'vitest';
import { screen } from '@testing-library/react';

/** 导演模式导航：通过真实的步骤按钮进入目标页面（与用户操作一致，
 *  不直接改内部状态）。 */
export async function gotoDirectorPage(
  user: { click: (element: Element) => Promise<void> },
  name: RegExp,
): Promise<void> {
  await user.click(await screen.findByRole('button', { name }));
}

/** A tiny fetch stub with a route table.  All routes here are stubbed UI fixtures, not API contract evidence.
 *  Real route verification lives in test_director_setup_contract.py and
 *  scripts/test_director_browser.py.  Failure-injection
 *  tests explicitly return 4xx/5xx (they are marked in the test name). */
export type StubResponse = { status?: number; body?: unknown } | undefined;
export type StubHandler = (
  url: string,
  init: RequestInit | undefined,
) => StubResponse | Promise<StubResponse>;
export type Route = { method: string; pattern: RegExp; respond: StubHandler };

export class StudioStub {
  private routes: Route[] = [];
  readonly calls: Array<{ method: string; url: string; init?: RequestInit; body?: unknown }> = [];

  constructor(routes: Route[]) {
    this.routes = routes;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => this.handle(input, init));
  }

  private async handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = String(input);
    const path = url.replace(/^https?:\/\/[^/]+/, '').replace(/^\[::1\]/, '');
    const method = (init?.method ?? 'GET').toUpperCase();
    let parsedBody: unknown;
    if (init?.body && typeof init.body === 'string') {
      try { parsedBody = JSON.parse(init.body); } catch { parsedBody = init.body; }
    } else if (init?.body instanceof FormData) {
      parsedBody = '[FormData]';
    } else if (init?.body) {
      parsedBody = String(init.body);
    }
    this.calls.push({ method, url: path, init, body: parsedBody });
    // Later routes win: tests override the baseline by appending.
    for (let index = this.routes.length - 1; index >= 0; index -= 1) {
      const route = this.routes[index];
      if (route.method !== method) continue;
      const match = path.match(route.pattern);
      if (!match) continue;
      const result = await route.respond(path, init);
      const status = result?.status ?? 200;
      const body = result === undefined || result.body === undefined ? '' : result.body;
      return new Response(
        typeof body === 'string' ? body : JSON.stringify(body),
        { status, headers: { 'Content-Type': 'application/json' } },
      );
    }
    // No route matched: fail loudly so a test never silently talks to the void.
    throw new Error(`StudioStub: no route for ${method} ${path}`);
  }

  add(route: Route): void {
    this.routes.push(route);
  }

  callsOf(method: string, pattern: RegExp): Array<{ method: string; url: string; body?: unknown }> {
    return this.calls.filter((call) => call.method === method && pattern.test(call.url));
  }

  restore(): void {
    vi.unstubAllGlobals();
  }
}

export function jsonResponse(body: unknown, status = 200): StubResponse {
  return { status, body };
}

/** 基线：演示体验（后端 production_mode 的 DEMO 状态）。 */
export const PRODUCTION_MODE_DEMO: import('../api').ProductionModeStatus = {
  mode: 'DEMO', label: '演示体验', detail: '当前用演示引擎演练流程，不会生成真实影片。',
  action: 'enable_real', code: null, render_provider: 'mock', runtime_status: 'NOT_ACTIVATED',
  renderer_online: false, worker_online: true, running_jobs: 0, series_active: 0,
};

export const PRODUCTION_MODE_READY: import('../api').ProductionModeStatus = {
  ...PRODUCTION_MODE_DEMO,
  mode: 'READY', label: '真实制作已就绪', action: 'start_making',
  detail: '本机 H3 制作环境可用；渲染器会在开始制作时自动启动，平时空闲属于正常。',
  render_provider: 'comfyui', runtime_status: 'ACTIVE',
};

export function productionMode(overrides: Partial<import('../api').ProductionModeStatus> = {}) {
  return { ...PRODUCTION_MODE_DEMO, ...overrides };
}

export const SYSTEM_STATUS = {
  api_online: true, worker_online: true, mock_provider_available: true,
  h3_connected: false, render_mode: 'mock', pending_jobs: 0, running_jobs: 0,
  failed_jobs: 0, blocked_jobs: 0, workers: [], last_error: null,
  production_mode: PRODUCTION_MODE_DEMO,
};

export const ENV_REPORT = {
  platform: {}, tools: {}, gpu: {}, ram: { status: 'OK' },
  disk: { status: 'OK', free_gb: 100, at_floor: true },
  port: { status: 'OK', port: 8000 }, data_dir: { path: 'D:\\Shine', writable: true },
  blockers: [], bootstrap: '', advisory_reviews: {
    VLM: { status: 'NOT_READY', model_id: 'qwen3-vl-8b-fp8', model_files_present: 0, model_files_expected: 13 },
    ASR: { status: 'NOT_READY', model_id: 'qwen3-asr-0.6b', model_files_present: 0, model_files_expected: 10 },
  },
};

export const CATALOG = { catalog_id: 'c', catalog_version: '1', generated: 'g', platform: {}, total_bytes: 0, components: [] };

export const READINESS = { catalog: { id: 'c', version: '1' }, layers: [], production_gates: {}, human_steps: [] };

export const RUNTIME = { status: 'NOT_ACTIVATED', render_provider: 'mock', error: null, candidate: null };

export const STORAGE = {
  root: 'D:\\Shine', configured: true, system_drive: false, requires_selection: false,
  free_bytes: 1, free_gb: 100, paths: {}, drives: [],
};

export function baseProject(overrides: Record<string, unknown> = {}) {
  return {
    id: 'proj-1', title: '雾港夜账', description: '', language: 'Chinese',
    frozen_style_version_id: null as string | null, style_draft: null,
    allow_remote_review: false, auto_repair_enabled: false, created_at: '2026-11-01T00:00:00',
    ...overrides,
  };
}

export const FROZEN_STYLE = {
  id: 'style-1', project_id: 'proj-1', version: 1, sha256: 'a'.repeat(64),
  snapshot: {
    photography_language: '平视中近景', shot_blocking: '轴线稳定', color_palette: '青灰',
    lighting_tone: '单侧主光', lens_texture: '浅景深', editing_rhythm: '停顿后切', sound_performance: '自然语速',
  },
  approved_by: 'user', approval_note: '', frozen_at: '2026-11-01T00:00:00',
  impact: { affected_shot_ids: [], invalidated_job_ids: [] },
};

export const FILM_TYPES = [
  {
    id: 'live_action', label: '仿真人（写实实拍）',
    instruction_en: 'The entire shot is photorealistic live-action footage: real human performers with natural skin texture and pores, practical sets, physically based lighting and a real camera lens; no illustration, no cel shading, and no cartoon or anime rendering.',
  },
  {
    id: 'animation', label: '动画（2D 赛璐璐）',
    instruction_en: 'The entire shot is 2D cel-shaded animation: hand-drawn line art with clean contours, flat color fills, hard-edged shading and painted backgrounds; no photorealistic skin, no live-action footage, and no 3D CGI rendering.',
  },
];

export const DIRECTOR_STYLE_CATALOG = [
  {
    id: 'wong-kar-wai', name_zh: '王家卫参考风格', version: '1.0.0',
    summary_zh: '城市情绪、前景遮挡与克制留白。', categories: ['urban', 'emotion'],
    sha256: 'b'.repeat(64), source: 'builtin', technique_count: 7, availability: 'available',
  },
  {
    id: 'hayao-miyazaki', name_zh: '宫崎骏动画电影参考风格', version: '1.0.0',
    summary_zh: '自然环境、清晰动作、温暖光线与富有手绘感的表面质地。', categories: ['animation', 'nature'],
    sha256: 'c'.repeat(64), source: 'builtin', technique_count: 7, availability: 'available',
  },
  {
    id: 'mamoru-oshii', name_zh: '押井守动画电影参考风格', version: '1.0.0',
    summary_zh: '建筑空间、克制运镜、冷静色调、信息留白与低调环境声。', categories: ['animation', 'urban'],
    sha256: 'd'.repeat(64), source: 'builtin', technique_count: 7, availability: 'available',
  },
];

/** Baseline route table mirroring the loopback API contract for one project. */
export function baseRoutes(project = baseProject()): Route[] {
  return [
    { method: 'POST', pattern: /\/api\/v1\/preparation\/approval-grants$/, respond: () => jsonResponse({ grant_id: 'local-grant', snapshot: 'local-snapshot' }) },
    { method: 'GET', pattern: /\/api\/v1\/system\/status$/, respond: () => jsonResponse(SYSTEM_STATUS) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/llm-readiness$/, respond: () => jsonResponse({ status: 'READY', roles: [] }) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/voice-readiness$/, respond: () => jsonResponse({ status: 'BLOCKED', voice_master_capable: false, roles: [] }) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/environment$/, respond: () => jsonResponse(ENV_REPORT) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/existing-installations$/, respond: () => jsonResponse({ installations: [] }) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/catalog$/, respond: () => jsonResponse(CATALOG) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/variant-recommendations$/, respond: () => jsonResponse({
      tier: 'standard', host: { gpu: null, vram_mb: null, ram_mb: null, free_gb: null },
      recommended: [], candidates: [], blocked: [], notes: [],
    }) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/readiness$/, respond: () => jsonResponse(READINESS) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/runtime$/, respond: () => jsonResponse(RUNTIME) },
    { method: 'GET', pattern: /\/api\/v1\/setup\/storage$/, respond: () => jsonResponse(STORAGE) },
    { method: 'GET', pattern: /\/api\/v1\/comfyops\/execution-context$/, respond: () => jsonResponse({ local_execution_enabled: true }) },
    { method: 'GET', pattern: /\/api\/v1\/comfyops\/install-plans/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/workflow-capabilities$/, respond: () => jsonResponse({ revisions: [] }) },
    { method: 'GET', pattern: /\/api\/v1\/comfyops\/inventory$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/comfyops\/models$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/comfyops\/nodes$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/comfyops\/resource-status$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([project]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1$/, respond: () => jsonResponse(project) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/assets$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/styles$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/director-styles$/, respond: () => jsonResponse(DIRECTOR_STYLE_CATALOG) },
    { method: 'GET', pattern: /\/api\/v1\/film-types$/, respond: () => jsonResponse(FILM_TYPES) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director-style$/, respond: () => jsonResponse({
      draft_sha256: 'c'.repeat(64), draft_source: null, frozen_source: null,
    }) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/shots$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/events/, respond: () => jsonResponse({ items: [], total: 0, limit: 30, offset: 0 }) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/terminology$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/subtitles$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/documents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/creative-briefs$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/api\/v1\/setup\/deployment-recommendations$/, respond: () => jsonResponse({
      recommended: [], blocked: [], options: [], host: { gpu: null, vram_mb: null, ram_mb: null, free_gb: null }, notes: [],
    }) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/candidate-assets$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/voice-profiles$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/production-series$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/film-artifacts$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/deliveries$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/delivery-orders$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/video-library$/, respond: () => jsonResponse({
      project_id: 'proj-1', project_title: project.title, entries: [],
      summary: { total: 0, current: 0, history: 0, pending_review: 0, missing: 0, deletable: 0 },
    }) },
    { method: 'PUT', pattern: /\/api\/v1\/projects\/proj-1\/style\/draft$/, respond: (_url, init) => jsonResponse({ project_id: 'proj-1', style_draft: JSON.parse(String(init?.body)), frozen: false }) },
    { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/style\/freeze$/, respond: () => jsonResponse(FROZEN_STYLE) },
    { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/shots$/, respond: (_url, init) => jsonResponse({ id: 'shot-new', project_id: 'proj-1', name: '镜头 1', revision: 1, spec: JSON.parse(String(init?.body)).spec, bindings: [] }, 201) },
    { method: 'PUT', pattern: /\/api\/v1\/shots\/[^/]+\/bindings$/, respond: () => jsonResponse({}) },
    { method: 'GET', pattern: /\/api\/v1\/film-agents$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/rulepack$/, respond: () => jsonResponse({ rulepack_id: null }) },
    { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/rulepack\/freeze$/, respond: () => jsonResponse({ rulepack_id: 'shine-h3-rules/1.1.0', status: 'FROZEN' }) },
    { method: 'GET', pattern: /\/api\/v1\/h3-rulebook$/, respond: () => jsonResponse({ packs: [{ id: 'shine-h3-rules/1.1.0' }] }) },
    { method: 'GET', pattern: /\/api\/v1\/provider-profiles$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/agent-routes$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/workflow-profiles/, respond: () => jsonResponse([]) },
    // 七阶段的完成态来自服务端；基线里是一个“还没开始制作”的真实形状。
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/, respond: () => jsonResponse(EMPTY_JOURNEY) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director-packages$/, respond: () => jsonResponse([]) },
    { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/production-runs$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/episode-plans$/, respond: () => jsonResponse({
      id: 'plan-1', project_id: 'proj-1', version: 1, status: 'DRAFT', target_seconds: 60,
      tolerance_seconds: 15, sha256: 'b'.repeat(64), source: 'derived',
      payload: { target_seconds: 60, tolerance_seconds: 15, episodes: [] },
      created_at: '2026-11-01T00:00:00', confirmed_at: null,
    }, 201) },
    { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director-packages$/, respond: () => jsonResponse({
      id: 'package-1', project_id: 'proj-1', revision: 1, status: 'DRAFT', stale: false,
      stale_reason: null, scope_digest: 'c'.repeat(64), manifest_sha256: 'd'.repeat(64),
      policy_hash: 'e'.repeat(64), episode_count: 0, shot_count: 0, episode_plan: null,
      script: null, style: null, binding: null, policy: {}, episodes: [],
      created_at: '2026-11-01T00:00:00', confirmed_at: null,
    }, 201) },
    { method: 'GET', pattern: /\/api\/v1\/director-packages\/[^/]+\/readiness$/, respond: () => jsonResponse({
      package_id: 'package-1', revision: 1, status: 'DRAFT', stale: false,
      scope_digest: 'c'.repeat(64), manifest_sha256: 'd'.repeat(64),
      config_fingerprint: 'f'.repeat(64), ready: false, startable: false,
      blockers: [{ code: 'PACKAGE_NOT_CONFIRMED', message: '导演包尚未确认' }],
      episode_count: 0, shot_count: 0, checked_at: '2026-11-01T00:00:00Z',
    }) },
  ];
}

/** 新建作品、还没有任何物料时的七阶段状态（服务端真实会返回的形状）。 */
export const EMPTY_JOURNEY = {
  project_id: 'proj-1',
  stages: [
    { id: 'script', label: '创意与剧本', done: false, summary: '还没有剧本',
      blockers: [{ code: 'SCRIPT_NOT_PREPARED', message: '导入剧本或直接建立分镜后才进入统稿' }], facts: {} },
    { id: 'plan', label: '剧本统筹', done: false, summary: '分集方案待确认',
      blockers: [{ code: 'EPISODE_PLAN_NOT_CONFIRMED', message: '分集方案尚未确认' }], facts: {} },
    { id: 'storyboard', label: '镜头拆解', done: false, summary: '还没有分镜',
      blockers: [], facts: {} },
    { id: 'assets', label: '拍摄素材准备', done: false, summary: '等待分镜定稿',
      blockers: [], facts: {} },
    { id: 'package', label: '导演包', done: false, summary: '尚未生成导演包', blockers: [{ code: 'NO_PACKAGE', message: '尚未生成导演包' }], facts: {} },
    { id: 'production', label: '视频生产', done: false, summary: '等待导演包', blockers: [], facts: {} },
    { id: 'post', label: '后期成片', done: false, summary: '还没有整部作品运行', blockers: [], facts: {} },
  ],
  next_stage: 'script',
  package: null,
  episode_plan: null,
  run: null,
};
