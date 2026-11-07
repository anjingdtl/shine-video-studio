/** Counterexample (audit 2026-11-01 #3): a delivery order created after the
 *  page loaded never appeared — not on view switch, not on the global topbar
 *  refresh, only via the panel's own local refresh button.  The fix gives the
 *  delivery panel three independent triggers: entering the delivery view, the
 *  global refresh epoch, and any change in shot-job progress; the synthetic
 *  BLOCKED order below is UI-state evidence only, never a real production
 *  acceptance. */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseProject, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
});

const SYNTHETIC_BLOCKED_ORDER = {
  id: 'order-synth-1', series_id: 'series-synth', version: 1, status: 'BLOCKED',
  edl_source: null, artifact_id: null, attempt_count: 1, max_attempts: 3,
  failure_code: 'EDL_EMPTY', delivered_at: null,
};

function injectBlockedOrder() {
  stub.add({
    method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/delivery-orders$/,
    respond: () => jsonResponse([SYNTHETIC_BLOCKED_ORDER]),
  });
}

async function expectOrderVisible(timeout = 3000) {
  await waitFor(() => {
    const auto = document.querySelector('.auto-delivery');
    expect(auto).not.toBeNull();
    expect(within(auto as HTMLElement).getByText(/已阻断（需人工处理）/)).toBeInTheDocument();
  }, { timeout });
}

describe('新交付订单自动出现在页面', () => {
  it('ASR 不可用或未运行时明确提示交付音频尚未完整核对', async () => {
    const unavailableJob = {
        id: 'job-1', project_id: 'proj-1', shot_id: 'shot-1', shot_name: '无对白镜头', shot_revision: 1,
        status: 'PASSED', state_version: 1, provider: 'comfyui', simulated: false,
        video_created: true, paused: false, stale: false, failure_code: null, provider_job_id: null,
        semantic_retries: 0, max_semantic_retries: 2, style_version_id: 'style-1',
        generation_mode: 'Ref2VA', workflow_profile_id: 'profile-1', compiled_prompt_sha256: null,
        artifact_id: 'shot-artifact-1', last_qc_result: 'passed', last_repair_plan: null,
        request: { reference_manifest: [] }, attempts: [], repairs: [],
        advisory_reviews: [{ id: 'asr-1', kind: 'ASR', status: 'UNAVAILABLE',
          error_code: 'ASR_ROUTE_NOT_CONFIGURED', model_id: 'UNCONFIGURED', artifact_id: 'shot-artifact-1',
          artifact_sha256: 'a'.repeat(64), job_id: 'job-1', started_at: null, finished_at: null,
          outcome: 'unavailable', is_mock: null }],
        created_at: '2026-11-07T00:00:00Z', updated_at: '2026-11-07T00:00:00Z',
    };
    const unreviewedJob = { ...unavailableJob, id: 'job-2', shot_id: 'shot-2', shot_name: '没有 ASR 记录',
      artifact_id: 'shot-artifact-2', advisory_reviews: [] };
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
      respond: () => jsonResponse([unavailableJob, unreviewedJob]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/deliveries$/,
      respond: () => jsonResponse([{
        artifact_id: 'delivery-1', sha256: 'b'.repeat(64), byte_size: 1024,
        created_at: '2026-11-07T00:01:00Z',
        provenance: { review_state: 'pending_review', duration_s: 6,
          clips: [{ job_id: 'job-1', in_s: 0, out_s: 3 }, { job_id: 'job-2', in_s: 0, out_s: 3 }] },
      }]) });

    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /后期成片/);

    expect(await screen.findByRole('status', { name: '音频语言核对未完成' })).toHaveTextContent(
      '音频语言/禁声核对未完整：2 个交付镜头没有可用的真实 ASR 结论（1 个 ASR 不可用，其中 1 个未配置语音识别路由；1 个没有 ASR 记录）',
    );
    expect(screen.getByRole('status', { name: '音频语言核对未完成' })).toHaveTextContent(
      '技术音轨可解码不代表对白或旁白语言正确',
    );
  });

  it('初始为空；全局刷新按钮（刷新全部数据）后合成订单出现', async () => {
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /后期成片/);
    // Initially empty and no error pretending to be empty.
    await screen.findByText('暂无交付成片。', {}, { timeout: 3000 });
    injectBlockedOrder();
    await user.click(screen.getByRole('button', { name: '刷新全部数据' }));
    await expectOrderVisible();
  });

  it('进入成片中心即重新读取，合成订单出现', async () => {
    const user = userEvent.setup();
    render(<App />);
    // The delivery panel loads when its page is entered through the real
    // step navigation (not while it sits unopened).
    await gotoDirectorPage(user, /后期成片/);
    await screen.findByText('暂无交付成片。', {}, { timeout: 3000 });
    injectBlockedOrder();
    await gotoDirectorPage(user, /镜头拆解/);
    await gotoDirectorPage(user, /后期成片/);
    await expectOrderVisible();
  });

  it('镜头任务状态变化会触发交付列表重读（任务推进 → 交付发现）', async () => {
    render(<App />);
    await gotoDirectorPage(userEvent.setup(), /后期成片/);
    await screen.findByText('暂无交付成片。', {}, { timeout: 3000 });
    injectBlockedOrder();
    // Simulate backend job progress that the 3s poller picks up.
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/,
      respond: () => jsonResponse([{
        id: 'job-1', project_id: 'proj-1', shot_id: 'shot-1', shot_name: '镜头', shot_revision: 1,
        status: 'PASSED', state_version: 2, provider: 'mock', simulated: true, video_created: true,
        paused: false, stale: false, failure_code: null, provider_job_id: null,
        semantic_retries: 0, max_semantic_retries: 2, style_version_id: 'style-1',
        generation_mode: null, workflow_profile_id: null, compiled_prompt_sha256: null,
        artifact_id: null, last_qc_result: null, last_repair_plan: null, attempts: [], repairs: [],
        request: { reference_manifest: [] },
        created_at: 't', updated_at: 't',
      }]),
    });
    // The real 3s poller picks the job change up; the fingerprint trigger
    // then reloads the delivery data without any manual action.  The sleep
    // runs inside act() so the interval's state updates flush.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 7000));
    });
    await expectOrderVisible(3000);
  }, 15000);

  it('请求失败时显示错误，而不是把失败伪装成空列表（故障注入）', async () => {
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/delivery-orders$/,
      respond: () => jsonResponse({ detail: 'DATABASE_OFFLINE' }, 500),
    });
    render(<App />);
    await gotoDirectorPage(userEvent.setup(), /后期成片/);
    await waitFor(() => {
      expect(screen.getByText(/交付列表读取失败/)).toBeInTheDocument();
    }, { timeout: 3000 });
    // The empty-state line must NOT claim there are no deliveries.
    expect(screen.queryByText('暂无交付成片。')).not.toBeInTheDocument();
  });

  it('项目切换后旧请求不污染新项目的交付面板', async () => {
    const user = userEvent.setup();
    const projectB = baseProject({ id: 'proj-2', title: '第二个项目' });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([baseProject(), projectB]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2$/, respond: () => jsonResponse(projectB) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/(assets|styles|shots|jobs)$/, respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/events/, respond: () => jsonResponse({ items: [], total: 0, limit: 30, offset: 0 }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/(terminology|subtitles|documents|candidate-assets|voice-profiles|production-series|film-artifacts|deliveries|delivery-orders)$/, respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/workflow-profiles\?project_id=proj-2/, respond: () => jsonResponse([]) });
    render(<App />);
    await gotoDirectorPage(user, /后期成片/);
    await screen.findByText('暂无交付成片。', {}, { timeout: 3000 });
    // The old project's panel gets a poisoned response AFTER switching.
    injectBlockedOrder();
    const switcher = screen.getByLabelText('快速切换作品');
    await user.selectOptions(switcher, 'proj-2');
    await waitFor(() => {
      // DeliveryPanel remounts per project (key) and reads the new project's
      // (empty) list; the stale synthetic order must not leak into it.
      expect(screen.queryByText(/已阻断（需人工处理）/)).not.toBeInTheDocument();
    }, { timeout: 3000 });
  });
});

describe('可视化 EDL 编辑（D01）', () => {
  it('入点修改写回提交体，无需手写 JSON；装配按钮仍走原合同', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/film-artifacts$/, respond: () => jsonResponse([
      { id: 'edl-1', kind: 'EditDecisionList', stale: false, payload_json: { schema_version: '1.1', clips: [
        { job_id: 'job-1', attempt_id: 'att-1', artifact_id: 'art-1', in_s: 0, out_s: 5, transition: 'CUT', reason: '提案顺序' },
      ] } },
    ]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/jobs$/, respond: () => jsonResponse([
      { id: 'job-1', shot_name: '第一镜', status: 'PASSED', state_version: 1, request: {}, attempts: [] },
    ]) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/deliveries$/, respond: () => jsonResponse({ review_state: 'all_passed' }, 201) });
    const user = userEvent.setup();

    render(<App />);
    await gotoDirectorPage(user, /后期成片/);
    await screen.findByLabelText('装配镜头顺序（可编辑）');
    await user.selectOptions(screen.getByLabelText('剪辑方案'), 'edl-1');
    const inPoint = await screen.findByLabelText('入点（秒）');
    await user.clear(inPoint);
    await user.type(inPoint, '1.5');
    await user.click(screen.getByRole('button', { name: /校验并装配成片/ }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /deliveries$/)).toHaveLength(1);
    });
    const body = stub.callsOf('POST', /deliveries$/)[0].body as { clips: Array<{ in_s: number }> };
    expect(body.clips[0].in_s).toBe(1.5);
  });
});
