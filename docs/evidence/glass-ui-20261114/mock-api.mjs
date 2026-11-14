import http from 'node:http';

const project = {
  id: 'p1',
  title: '巷口的灯',
  description: '一部关于夜归人的短片',
  language: 'zh-CN',
  frozen_style_version_id: 'sv1',
  style_draft: {
    visual_style: '写实电影感，低饱和冷调',
    lighting: '夜间街灯与霓虹反射',
    color_palette: '青蓝 + 琥珀暖光',
    camera_language: '手持跟拍，浅景深',
    composition: '三分法，留白',
    texture: '细腻颗粒，柔和高光',
    negative: '变形、过曝、文字水印',
    film_type: 'SHORT_FILM',
    dialogue_ratio_percent: 45,
  },
  allow_remote_review: false,
  auto_repair_enabled: true,
  created_at: '2026-11-10T10:00:00Z',
};

const styleVersion = {
  id: 'sv1', project_id: 'p1', version: 1, sha256: 'a'.repeat(64),
  snapshot: project.style_draft, director_style: null,
  approved_by: 'local', approval_note: '初版', frozen_at: '2026-11-10T11:00:00Z',
  impact: { affected_shot_ids: [], invalidated_job_ids: [] },
};

const shotSpecs = [
  { name: '镜头 01 · 雨夜归途', beat: '主角穿过雨夜街口', duration: 6 },
  { name: '镜头 02 · 窗前的灯', beat: '窗内暖灯亮起', duration: 5 },
  { name: '镜头 03 · 门后的沉默', beat: '推门进入，停在门口', duration: 7 },
];
const shots = shotSpecs.map((spec, index) => ({
  id: `s${index + 1}`, project_id: 'p1', name: spec.name, revision: 1,
  spec: {
    story_beat: spec.beat, duration_target_s: spec.duration, style_version_id: 'sv1',
    characters: ['lin'], required_asset_ids: [], dialogue: [],
    continuity: { carries_from_previous: index > 0 },
  },
  bindings: [],
}));

const assets = [
  {
    id: 'a1', project_id: 'p1', filename: 'lin-portrait.png', kind: 'image', content_type: 'image/png',
    sha256: 'b'.repeat(64), byte_size: 128000, role: 'character', entity_id: 'lin',
    tags: ['主角', '定妆'], duration_s: null, width: 768, height: 1024,
    approved: true, locked: true, approved_at: '2026-11-10T12:00:00Z', locked_at: '2026-11-10T12:00:00Z',
  },
  {
    id: 'a2', project_id: 'p1', filename: 'street-rain.png', kind: 'image', content_type: 'image/png',
    sha256: 'c'.repeat(64), byte_size: 256000, role: 'scene', entity_id: 'street',
    tags: ['场景', '雨夜'], duration_s: null, width: 1344, height: 768,
    approved: true, locked: true, approved_at: '2026-11-10T12:00:00Z', locked_at: '2026-11-10T12:00:00Z',
  },
];

const jobs = [
  {
    id: 'j1', project_id: 'p1', shot_id: 's1', shot_name: shots[0].name, shot_revision: 1,
    status: 'PASSED', state_version: 1, provider: 'mock', simulated: true, video_created: true,
    paused: false, stale: false, failure_code: null, provider_job_id: null, semantic_retries: 0,
    max_semantic_retries: 2, style_version_id: 'sv1', generation_mode: 'REF2VA',
    workflow_profile_id: null, compiled_prompt_sha256: null, artifact_id: 'artifact-1',
    last_qc_result: 'PASSED', last_repair_plan: null, request: { reference_manifest: [] },
    attempts: [], repairs: [], created_at: '2026-11-11T08:00:00Z', updated_at: '2026-11-11T08:10:00Z',
    acceptance_status: 'accepted', acceptance_note: null, acceptance_final_gate: null,
  },
  {
    id: 'j2', project_id: 'p1', shot_id: 's2', shot_name: shots[1].name, shot_revision: 1,
    status: 'RUNNING', state_version: 1, provider: 'mock', simulated: true, video_created: false,
    paused: false, stale: false, failure_code: null, provider_job_id: null, semantic_retries: 0,
    max_semantic_retries: 2, style_version_id: 'sv1', generation_mode: 'REF2VA',
    workflow_profile_id: null, compiled_prompt_sha256: null, artifact_id: null,
    last_qc_result: null, last_repair_plan: null, request: { reference_manifest: [] },
    attempts: [], repairs: [], created_at: '2026-11-11T08:20:00Z', updated_at: '2026-11-11T08:21:00Z',
    acceptance_status: 'pending_review', acceptance_note: null, acceptance_final_gate: null,
  },
];

const journey = {
  project_id: 'p1',
  stages: [
    { id: 'script', label: '创意与剧本', done: true, summary: '剧本已确认', blockers: [], facts: { outline_confirmed: true } },
    { id: 'plan', label: '剧本统筹', done: true, summary: '2 集已分集', blockers: [], facts: { episodes: 2 } },
    { id: 'storyboard', label: '镜头拆解', done: true, summary: '3 个镜头', blockers: [], facts: { shots: 3 } },
    { id: 'assets', label: '拍摄素材准备', done: true, summary: '2 项已锁定', blockers: [], facts: { approved: 2 } },
    { id: 'package', label: '导演包', done: false, summary: '待生成导演包', blockers: [], facts: {} },
    { id: 'production', label: '视频生产', done: false, summary: '尚未开拍', blockers: [], facts: {} },
    { id: 'post', label: '后期成片', done: false, summary: '尚未进入后期', blockers: [], facts: {} },
  ],
  next_stage: 'package',
  scope: { package_id: null, package_revision: null, run_id: null, shot_count: 3 },
  package: null,
  episode_plan: { id: 'ep1', version: 1, episodes: 2 },
  run: null,
};

const directorOverview = {
  project_id: 'p1', title: '巷口的灯',
  plan: { version: 1, sha256: 'd'.repeat(64), status: 'CONFIRMED', episode_count: 2 },
  style: { version: 1, sha256: 'a'.repeat(64) },
  counts: { episode_count: 2, confirmed_episode_count: 2, shootable_episode_count: 1, delivered_episode_count: 0, shot_count: 3, passed_shot_count: 1 },
  episodes: [
    {
      episode_id: 'e1', order: 1, title: '第一集 · 雨夜', planned_seconds: 60,
      state: 'IN_PRODUCTION', state_label: '制作中',
      storyboard: { section_count: 2, landed_section_count: 2, pending_sections: [], ledger_task_id: null, ledger_task_status: null, state_version: 1, blocker: null },
      shots: { landed_count: 2, confirmed_count: 2, confirmed: true, problems: [] },
      materials: { missing_count: 0, missing: [] },
      package: { package_id: 'pkg1', revision: 1, startable: true, blockers: [] },
      production: { run_id: 'run1', status: 'RUNNING', shot_count: 2, passed_shot_count: 1, in_current_run: true },
      delivery: null,
      next_action: null,
    },
    {
      episode_id: 'e2', order: 2, title: '第二集 · 灯下', planned_seconds: 45,
      state: 'PENDING', state_label: '待制作',
      storyboard: { section_count: 1, landed_section_count: 1, pending_sections: [], ledger_task_id: null, ledger_task_status: null, state_version: 1, blocker: null },
      shots: { landed_count: 1, confirmed_count: 1, confirmed: true, problems: [] },
      materials: { missing_count: 0, missing: [] },
      package: null, production: null, delivery: null, next_action: null,
    },
  ],
  next_action: null,
  package: null,
  run: { run_id: 'run1', status: 'RUNNING', active: true, scope_episode_ids: ['e1'] },
  storyboard_task: { task_id: null, status: null, state_version: 1 },
};

const system = {
  api_online: true, worker_online: false, mock_provider_available: true, h3_connected: false,
  render_mode: 'mock', pending_jobs: 0, running_jobs: 1, failed_jobs: 0, blocked_jobs: 0,
  workers: [], last_error: null,
  production_mode: {
    mode: 'READY', label: '可以开始制作', detail: '本机演示渲染可用；真实生成需配置 ComfyUI。',
    action: 'start_making', code: null, render_provider: 'mock', runtime_status: 'READY',
    renderer_online: false, worker_online: false, running_jobs: 1, series_active: 0,
  },
};

const collections = new Set([
  'assets', 'styles', 'shots', 'jobs', 'events', 'deliveries', 'delivery-orders', 'film-artifacts',
  'candidate-assets', 'voice-profiles', 'production-series', 'documents', 'episode-plans',
  'material-preparation', 'generation-segments', 'film-runs', 'workflow-capabilities',
  'install-plans', 'attempts', 'provider-profiles', 'agent-routes', 'workflow-profiles',
  'comfyops/inventory', 'film-agents', 'director/packages', 'runs', 'director-styles',
  'inventory', 'model-calls', 'install-plans', 'attempts', 'storyboard', 'scenes',
]);

function bodyFor(pathname) {
  const path = pathname.replace(/^\/api\/v1/, '');
  if (path === '/projects') return [project];
  if (path === '/projects/p1') return project;
  if (path === '/projects/p1/journey') return journey;
  if (path === '/projects/p1/director/overview') return directorOverview;
  if (path === '/projects/p1/assets') return assets;
  if (path === '/projects/p1/styles') return [styleVersion];
  if (path === '/projects/p1/shots') return shots;
  if (path === '/projects/p1/jobs') return jobs;
  if (path === '/projects/p1/events') return { items: [], total: 0, limit: 30, offset: 0, next_offset: null };
  if (path === '/projects/p1/director/strategy-options') return {
    model_variants: [{ variant_id: 'h3-ref2va-int8-convrot', label: 'h3-ref2va-int8-convrot', acceptance_status: 'SUPPORTED', verified: true, default_recommended: true }],
    accelerators: [{ accelerator_id: 'pdd8', steps: 8, verified: true }, { accelerator_id: 'native20', steps: 20, verified: true }],
    second_pass: { supported: true, recipes: [] }, upscale: [],
  };
  if (path === '/setup/variant-availability') return { project_id: 'p1', variants: [] };
  if (path === '/projects/p1/director/materials/requirements') {
    return { plan_revision_id: null, expected_snapshot: null, requirements: [] };
  }
  if (path === '/projects/p1/material-preparation') {
    return {
      project_id: 'p1', stage: 'collect', plan_revision_id: null, plan_status: null,
      document_id: null, visual_direction: null, confirmation: null, requirements: [], runs: [], blockers: [],
    };
  }
  if (path === '/projects/p1/generation-segments') return { series_id: null, run_id: null, segments: [] };
  if (path === '/production-runs/run1') return {
    id: 'run1', project_id: 'p1', package_id: 'pkg1', package_revision: 1,
    manifest_sha256: '', scope_digest: '', status: 'RUNNING', state_version: 1,
    control_reason: null, blocker_count: 0,
    scope: { episode_ids: ['e1'], all_episodes: false, shot_count: 2 },
    counters: {},
    progress: { required_episode_count: 1, delivered_episode_count: 0, producing_episode_count: 1,
      blocked_episode_count: 0, batch_count: 2, passed_batch_count: 1, delivered_seconds: 0, passed_shot_count: 1 },
    created_at: '', started_at: '', finished_at: null, last_progress_at: '', heartbeat_at: '', episodes: [],
  };
  if (path === '/production-runs/run1/events') return [];
  if (path === '/asset-center/capabilities') return {
    workspaces: ['director', 'assets'],
    image_capabilities: Object.fromEntries(['T2I','I2I','INPAINT','MULTIREF','WARDROBE','RGBA'].map((mode) => [
      'QWEN_IMAGE_' + mode, { state: 'PENDING_LOCAL_VALIDATION', can_generate: false, can_validate: mode === 'T2I', reason: '本机验证尚未完成。' },
    ])),
    voice_capabilities: { TTS_MANDARIN: { state: 'PENDING_LOCAL_VALIDATION', reason: '本机验证尚未完成。' } },
    t2i_runtime: {},
  };
  if (path === '/asset-center/assets') return { items: [{
    id: 'lib1', title: '雨夜街景', kind: 'image', category: 'scene', tags: ['雨夜'], collection: null,
    favorite: false, status: 'ACTIVE', state_version: 1, version_count: 1,
    current_version: { id: 'v1', ordinal: 1, parent_version_id: null, filename: 'street.svg', kind: 'image',
      content_type: 'image/svg+xml', content_sha256: 'e'.repeat(64), byte_size: 2048, width: 768, height: 1024,
      duration_s: null, source_mode: 'T2I', prompt: 'rainy street', workflow_revision_hash: null,
      references_manifest: [], rights: { id: 'r1', status: 'DECLARED', license_kind: 'LOCAL', allowed_project_ids: [], permitted_uses: ['research'], commercial_allowed: false, expires_at: null, revoked_at: null },
      provenance: {}, qc: { technical: 'pass' }, status: 'READY_FOR_REVIEW', is_mock: false,
      media_url: '/api/v1/asset-center/versions/v1/media' },
    versions: [{ id: 'v1', ordinal: 1, parent_version_id: null, filename: 'street.svg', kind: 'image',
      content_type: 'image/svg+xml', content_sha256: 'e'.repeat(64), byte_size: 2048, width: 768, height: 1024,
      duration_s: null, source_mode: 'T2I', prompt: 'rainy street', workflow_revision_hash: null,
      references_manifest: [], rights: { id: 'r1', status: 'DECLARED', license_kind: 'LOCAL', allowed_project_ids: [], permitted_uses: ['research'], commercial_allowed: false, expires_at: null, revoked_at: null },
      provenance: {}, qc: { technical: 'pass' }, status: 'READY_FOR_REVIEW', is_mock: false,
      media_url: '/api/v1/asset-center/versions/v1/media' }],
  }], limit: 100, offset: 0 };
  if (path === '/asset-center/voices') return { items: [] };
  if (path === '/asset-center/assignments') return { items: [] };
  if (path === '/system/status') return system;
  if (path === '/setup/environment') return {
    advisory_reviews: {},
    platform: { os: 'windows', arch: 'x64', release: '10.0' },
    tools: {}, gpu: {}, ram: { status: 'OK', ram_mb: 32768, at_floor: true },
    disk: { status: 'OK', free_gb: 256, at_floor: true }, port: { status: 'OK', port: 8000 },
    data_dir: { path: 'D:\\ShineData', writable: true }, deployment_dir: { path: 'D:\\Shine', writable: true },
    blockers: [], bootstrap: 'READY',
  };
  if (path === '/setup/llm-readiness') return { status: 'NOT_CONFIGURED', roles: [] };
  if (path === '/setup/voice-readiness') return { status: 'NOT_CONFIGURED', voice_master_capable: false, roles: [] };
  if (path === '/setup/image-readiness') return { configured: false, layer: 'NOT_READY', renderer: { state: 'UNKNOWN' }, upstream: { required_bytes: 0 } };
  if (path === '/setup/storage') return { root: 'D:\\ShineRuntime', configured: true, system_drive: false, requires_selection: false, free_bytes: 128000000000, free_gb: 119, paths: {}, drives: [] };
  if (path === '/setup/connectivity') return { status: 'OK', checked_at: '', blocked: [], degraded: [], upstreams: [], guidance: [] };
  if (path === '/setup/existing-installations') return { installations: [] };
  if (path === '/setup/catalog') return {
    catalog_id: 'h3-ref2va-windows-x64', catalog_version: '2026.11.14', generated: '', platform: {},
    total_bytes: 0, components: [],
    download_breakdown: { complete_catalog_bytes: 0, default_production_bytes: 0, optional_review_bytes: 0, optional_review_components: [], model_bytes: 0, runtime_bytes: 0 },
  };
  if (path === '/setup/readiness') return { catalog: { id: '', version: '' }, layers: [], production_gates: {}, human_steps: [] };
  if (path === '/setup/runtime') return { status: 'NOT_ACTIVATED', render_provider: 'mock', error: null, candidate: null, services_verified: false, service_validation: null };
  if (path === '/setup/variant-recommendations') return { recommendations: [] };
  if (path === '/settings/output-directory') return { path: 'D:\\ShineOutput', root: 'D:\\ShineOutput', writable: true, free_bytes: 128000000000, free_gb: 119, requires_selection: false };
  if (path === '/llm/overview') return { roles: [], profiles: [], routes: [] };
  if (path === '/agent-routes/preview') return { roles: [] };
  if (path === '/h3-rulebook') return { packs: [], sources: [], rules: [], local_workflow_capabilities: [] };
  const last = path.split('/').filter(Boolean).pop() || '';
  if (collections.has(last) || path.includes('install-plans')) return [];
  return {};
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1:8000');
  res.setHeader('content-type', 'application/json; charset=utf-8');
  if (url.pathname === '/health' || url.pathname === '/api/v1/health') {
    res.end(JSON.stringify({ status: 'ok' }));
    return;
  }
  if (req.method !== 'GET') {
    res.end(JSON.stringify({ ok: true }));
    return;
  }
  if (/\/media$/.test(url.pathname)) {
    res.setHeader('content-type', 'image/svg+xml');
    res.end(`<svg xmlns="http://www.w3.org/2000/svg" width="768" height="1024"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e3a5f"/><stop offset="0.55" stop-color="#b46b3f"/><stop offset="1" stop-color="#f2c98a"/></linearGradient></defs><rect width="768" height="1024" fill="url(#g)"/><circle cx="560" cy="300" r="120" fill="#ffd79b" opacity="0.85"/><text x="60" y="900" fill="#fff" font-family="sans-serif" font-size="46">Shine Mock Media</text></svg>`);
    return;
  }
  res.end(JSON.stringify(bodyFor(url.pathname)));
});
server.listen(8000, '127.0.0.1', () => console.log('mock api on 8000'));
