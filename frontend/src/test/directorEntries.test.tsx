/** 双入口、剧本先分集、部署组合推荐与 journey 如实显示：界面契约测试。
 *
 *  这些是 stub 出来的服务端形状，只证明界面按服务端事实行事，不是真实生产证据。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { EMPTY_JOURNEY, StudioStub, baseProject, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  window.history.replaceState(null, '', '#');
  stub = new StudioStub(baseRoutes(baseProject({ frozen_style_version_id: 'style-1' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

const BRIEF = {
  id: 'brief-1', version: 1, sha256: 'a'.repeat(64), confirmed: false, stale: false,
  created_at: '2026-11-06T00:00:00Z', confirmed_at: null, script_task: null,
  payload: { version: 1, idea: '雨夜里，一个钟表匠修好了一只停在十二点的怀表。', locked_facts: ['主角叫老周'] },
};

describe('journey 读取状态', () => {
  it('读不到进度时如实提示并可重试，绝不显示“全部选定范围已交付”', async () => {
    let fail = true;
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
      respond: () => (fail ? jsonResponse({ detail: 'boom' }, 500) : jsonResponse(EMPTY_JOURNEY)) });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText(/暂时读不到这部作品的进度/)).toBeInTheDocument();
    expect(screen.queryByText(/全部选定范围已交付/)).toBeNull();
    fail = false;
    await user.click(await screen.findByRole('button', { name: '重新读取进度' }));
    expect(await screen.findByRole('button', { name: /继续：/ })).toBeInTheDocument();
    expect(screen.queryByText(/全部选定范围已交付/)).toBeNull();
  });

  it('进度还在读取时不冒充完成', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
      respond: () => new Promise(() => undefined) });
    render(<App />);
    expect(await screen.findByRole('button', { name: '正在读取进度…' })).toBeDisabled();
    expect(screen.queryByText(/全部选定范围已交付/)).toBeNull();
    expect(screen.queryByText(/六个阶段全部完成/)).toBeNull();
  });

  it('导演页按确认计划范围显示镜头数，历史任务不会混入本次 Run', async () => {
    const stages = EMPTY_JOURNEY.stages.map((stage) => {
      if (stage.id === 'storyboard') return {
        ...stage, done: true, summary: '64 个计划内镜头（项目共 70 个）',
        facts: { shot_count: 70, planned_shot_count: 64,
          planned_shot_ids: Array.from({ length: 64 }, (_, index) => `shot-${index + 1}`) }, blockers: [],
      };
      if (stage.id === 'production') return {
        ...stage, done: true, summary: '64 / 64 镜已通过',
        facts: { passed_shot_count: 64, scope_shot_count: 64 }, blockers: [],
      };
      if (stage.id === 'package' || stage.id === 'post') return { ...stage, done: true, blockers: [] };
      return stage;
    });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
      respond: () => jsonResponse({
        ...EMPTY_JOURNEY, stages, next_stage: null,
        run: { id: 'run-1', status: 'COMPLETED', required_episode_count: 4,
          delivered_episode_count: 4, blockers: [], control_reason: null, last_progress_at: null },
      }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/shots$/,
      respond: () => jsonResponse(Array.from({ length: 70 }, (_, index) => ({
        id: `shot-${index + 1}`, project_id: 'proj-1', name: `镜头 ${index + 1}`, revision: 1,
        spec: { characters: [], duration_target_s: 6 }, bindings: [],
      }))) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
      respond: () => jsonResponse(Array.from({ length: 72 }, (_, index) => ({
        id: `job-${index + 1}`, project_id: 'proj-1', shot_id: `shot-${index + 1}`,
        shot_name: `镜头 ${index + 1}`, shot_revision: 1,
        status: index < 65 ? 'PASSED' : index < 68 ? 'BLOCKED' : 'FAILED',
        state_version: 1, provider: 'comfyui', simulated: false, video_created: index < 65,
        paused: false, stale: false, failure_code: null, provider_job_id: null,
        semantic_retries: 0, max_semantic_retries: 2, style_version_id: 'style-1',
        generation_mode: null, workflow_profile_id: null, compiled_prompt_sha256: null,
        artifact_id: null, last_qc_result: null, last_repair_plan: null,
        request: { reference_manifest: [] }, attempts: [], repairs: [],
        created_at: '2026-11-07T00:00:00Z', updated_at: '2026-11-07T00:00:00Z',
      }))) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 3 阶段：镜头拆解/);

    expect(await screen.findByText(/已应用 64 个计划镜头（项目共 70 镜）/)).toBeInTheDocument();
    // 排产与进度已归并进⑥视频生产页（旧四步层移除）。
    await gotoDirectorPage(user, /第 6 阶段：视频生产/);
    expect(screen.getByText('一键开拍：自动制作全部 64 个镜头')).toBeInTheDocument();
    expect(screen.queryByText('一键开拍：自动制作全部 70 个镜头')).toBeNull();
    expect(screen.getByText('⑥ 进度与结果 · 64/64 个计划镜头已通过')).toBeInTheDocument();
    expect(screen.getByText(/本次确认范围：64 镜，64\/64 个计划镜头已通过/)).toBeInTheDocument();
    expect(screen.queryByText(/65\/72 完成/)).toBeNull();
  });
});

describe('① 创意与剧本 双入口', () => {
  it('想法入口：保存简报 → 确认 → 生成剧本；远程模型先征得同意', async () => {
    let briefs: unknown[] = [];
    const posts: Array<{ url: string; body: unknown }> = [];
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/creative-briefs$/,
      respond: () => jsonResponse(briefs) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/creative-briefs$/,
      respond: (url, init) => {
        posts.push({ url, body: JSON.parse(String(init?.body)) });
        briefs = [BRIEF];
        return jsonResponse(BRIEF, 201);
      } });
    stub.add({ method: 'POST', pattern: /\/creative-briefs\/brief-1\/confirm$/,
      respond: () => {
        briefs = [{ ...BRIEF, confirmed: true }];
        return jsonResponse(briefs[0]);
      } });
    let generateCalls = 0;
    stub.add({ method: 'POST', pattern: /\/creative-briefs\/brief-1\/generate-script$/,
      respond: (url, init) => {
        generateCalls += 1;
        const body = JSON.parse(String(init?.body));
        posts.push({ url, body });
        if (!body.remote_text_consent) {
          return jsonResponse({ detail: { code: 'REMOTE_AGENT_CONSENT_REQUIRED' } }, 409);
        }
        briefs = [{ ...BRIEF, confirmed: true, script_task: {
          id: 'task-1', status: 'QUEUED', error_code: null, document_id: null, document_sha256: null,
          external_outcome: null, created_at: '2026-11-06T00:01:00Z' } }];
        return jsonResponse({ ...(briefs[0] as object), task_id: 'task-1' }, 202);
      } });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 1 阶段：创意与剧本/);
    expect(await screen.findByText(/入口 A · 我有一个想法/)).toBeInTheDocument();
    expect(screen.getByText(/入口 B · 我已有剧本/)).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText(/雨夜里/), '雨夜钟表铺的故事');
    await user.type(screen.getByPlaceholderText(/主角叫老周/), '主角叫老周');
    await user.click(screen.getByRole('button', { name: '保存为创意简报' }));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0].body).toMatchObject({ idea: '雨夜钟表铺的故事', locked_facts: ['主角叫老周'] });

    await user.click(await screen.findByRole('button', { name: '确认这一版' }));
    await user.click(await screen.findByRole('button', { name: '生成剧本' }));
    await waitFor(() => expect(generateCalls).toBe(2));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(posts.at(-1)?.body).toEqual({ remote_text_consent: true });
    expect(await screen.findByText(/剧本任务：排队中/)).toBeInTheDocument();
  });

  it('导入入口：上传剧本走同一个文档接口', async () => {
    let uploaded = 0;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/documents$/,
      respond: () => {
        uploaded += 1;
        return jsonResponse({ id: 'doc-1', project_id: 'proj-1', filename: '钟表铺.txt', content_type: 'text/plain',
          sha256: 'c'.repeat(64), source_format: 'txt', created_at: '2026-11-06T00:00:00Z' }, 201);
      } });
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 1 阶段：创意与剧本/);
    const input = document.querySelector('.script-entry-b input[type="file"]') as HTMLInputElement;
    expect(input).not.toBeNull();
    await user.upload(input, new File(['第1场：钟表铺'], '钟表铺.txt', { type: 'text/plain' }));
    await waitFor(() => expect(uploaded).toBe(1));
    const call = stub.callsOf('POST', /\/projects\/proj-1\/documents$/)[0];
    expect(call.body).toBe('[FormData]');
  });
});

describe('② 剧本统筹先分集', () => {
  it('大纲已确认、还没有镜头时也能按场次生成分集', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/,
      respond: () => jsonResponse({
        ...EMPTY_JOURNEY,
        stages: EMPTY_JOURNEY.stages.map((stage) => (stage.id === 'script'
          ? { ...stage, done: true, blockers: [], facts: { outline_confirmed: true } }
          : stage)),
        next_stage: 'materials',
      }) });
    let posted: Record<string, unknown> | null = null;
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/episode-plans$/,
      respond: (_url, init) => {
        posted = JSON.parse(String(init?.body));
        return jsonResponse({
          id: 'plan-1', project_id: 'proj-1', version: 1, status: 'DRAFT', target_seconds: 60,
          tolerance_seconds: 15, sha256: 'b'.repeat(64), source: 'script_sections',
          payload: { mode: 'sections', target_seconds: 60, tolerance_seconds: 15, episodes: [
            { episode_id: 'ep-01', order: 1, title: '第1集', shot_ids: [], section_ids: ['sec-1', 'sec-2'],
              planned_seconds: 50, boundary_note: 'final_episode' }] },
          created_at: '2026-11-06T00:00:00Z', confirmed_at: null,
        }, 201);
      } });
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 5 阶段：导演包/);
    const generate = await screen.findByRole('button', { name: /生成分集草案/ });
    await waitFor(() => expect(generate).toBeEnabled());
    await user.click(generate);
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted?.['mode']).toBe('sections');
  });
});

describe('基础配置：部署组合推荐', () => {
  it('列出可满足/不满足的组合与具名原因，生图不满足时不预先勾选', async () => {
    stub.add({ method: 'POST', pattern: /\/api\/v1\/setup\/deployment-recommendations$/,
      respond: () => jsonResponse({
        host: { gpu: 'NVIDIA RTX 4090', vram_mb: 24564, ram_mb: 65536, free_gb: 322.8 },
        notes: ['多张显卡的显存不会相加。'],
        recommended: [],
        blocked: [],
        options: [
          { purpose: 'h3_video', label: 'H3 正式视频生成', skippable: false, catalog_id: 'h3-ref2va-windows-x64',
            catalog_version: '1.2.0', status: 'SUPPORTED', total_bytes: 50 * 1024 ** 3,
            reuse_bytes: 20 * 1024 ** 3, download_bytes: 30 * 1024 ** 3, blockers: [], warnings: [],
            requirements: { min_vram_mb: 23552, min_ram_mb: 31744, min_disk_gb: 180 },
            evidence: { level: 'estimated', verified: false } },
          { purpose: 'image_prep', label: '生图候选准备', skippable: true, catalog_id: 'qwen-image-2.1-windows-x64',
            catalog_version: '1.0.0', status: 'BLOCKED', total_bytes: 40 * 1024 ** 3, reuse_bytes: 0,
            download_bytes: 40 * 1024 ** 3,
            blockers: [{ code: 'VRAM_INSUFFICIENT', required: 32768, actual: 24564 }], warnings: [],
            evidence: { level: 'estimated', verified: false } },
        ],
      }) });
    const user = userEvent.setup();
    window.history.replaceState(null, '', '#d/setup');
    render(<App />);
    await user.click(await screen.findByRole('button', { name: /检测并推荐/ }));
    expect(await screen.findByText('H3 正式视频生成')).toBeInTheDocument();
    expect(screen.getByText(/显存不足（需要 32768 MB，本机 24564 MB）/)).toBeInTheDocument();
    expect(screen.getAllByText('估算（尚未实测）').length).toBe(2);
    const imageBox = screen.getByRole('checkbox', { name: /生图候选准备/ });
    expect(imageBox).not.toBeChecked();
    expect(screen.getByRole('checkbox', { name: /H3 正式视频生成/ })).toBeDisabled();
    expect(screen.getAllByText(/可以不装生图/).length).toBeGreaterThan(0);
  });
});

describe('本机服务版本提示', () => {
  it('数据库落后于程序时如实提示重启升级', async () => {
    stub.add({ method: 'GET', pattern: /^\/health$/, respond: () => jsonResponse({
      status: 'ok', schema_revision: 'f170a0030001', schema_head: 'f180b0010001', schema_up_to_date: false }) });
    render(<App />);
    expect(await screen.findByText(/落后于当前程序/)).toBeInTheDocument();
  });

  it('版本一致或读不到时不显示任何结论', async () => {
    stub.add({ method: 'GET', pattern: /^\/health$/, respond: () => jsonResponse({
      status: 'ok', schema_revision: null, schema_head: 'f180b0010001', schema_up_to_date: null }) });
    render(<App />);
    await screen.findByRole('button', { name: /继续：|正在读取进度…/ });
    expect(screen.queryByText(/落后于当前程序/)).toBeNull();
  });
});
