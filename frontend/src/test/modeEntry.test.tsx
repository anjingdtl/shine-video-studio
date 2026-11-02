/** 演示 ↔ 真实制作的唯一状态链（P0 契约）：
 *  1. 演示徽章 + 启用入口常驻；点击进入单一路径向导；
 *  2. 向导优先识别并复用已有 ComfyUI，不默认重新下载；
 *  3. 环境不完整时给出中文阻断原因，而不是英文异常码；
 *  4. 向导走完验证与激活后，提示真实制作已准备好；
 *  5. READY 状态下单镜头提交 provider=comfyui；演示状态仍为 mock；
 *  6. 无 LLM 配置时不假装 AI 分镜可用。 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import {
  PRODUCTION_MODE_READY, READINESS, RUNTIME, SYSTEM_STATUS, StudioStub,
  baseProject, baseRoutes, jsonResponse, productionMode,
} from './studioStub';

let stub: StudioStub;

const SHOT = {
  id: 'shot-1', project_id: 'proj-1', name: '第一镜', revision: 1,
  spec: { story_beat: '雨夜交接' }, bindings: [],
};
const FROZEN = {
  id: 'style-1', project_id: 'proj-1', version: 1, sha256: 'a'.repeat(64),
  snapshot: {}, approved_by: 'local-user', approval_note: '', frozen_at: 't',
  impact: { affected_shot_ids: [], invalidated_job_ids: [] },
};
const WORKFLOW = {
  id: 'wf-1', project_id: 'proj-1', name: 'Ref2VA 主方案', mode: 'Ref2VA', graph_sha256: 'g',
  parameter_map: {}, required_nodes: [], checkpoint_id: 'h3', validated_against: null, created_at: 't',
};

/** 冻结风格 + 一个镜头 + 一个方案：单镜头提交的前置条件。 */
function realReadyProject() {
  stub.add({ method: 'GET', pattern: /projects\/proj-1$/, respond: (_u, _i) => jsonResponse(baseProject({ frozen_style_version_id: 'style-1' })) });
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/styles$/, respond: () => jsonResponse([FROZEN]) });
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/shots$/, respond: () => jsonResponse([SHOT]) });
  stub.add({ method: 'GET', pattern: /workflow-profiles\?project_id=proj-1$/, respond: () => jsonResponse([WORKFLOW]) });
  stub.add({ method: 'POST', pattern: /projects\/proj-1\/jobs$/, respond: () => jsonResponse({ id: 'job-1' }, 201) });
}

/** 顶栏模式入口组件（.mode-entry 是组件样式合同的一部分）。 */
async function topbarModeEntry(): Promise<HTMLElement> {
  const entry = await waitFor(() => {
    const node = document.querySelector('.mode-entry');
    expect(node).not.toBeNull();
    return node as HTMLElement;
  });
  return entry;
}

async function openSingleShotTools(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /AI导演制作/ }));
  await user.click(await screen.findByText('单镜头制作与任务明细（高级）'));
  await waitFor(() => {
    expect(document.querySelector('.advanced-fold')).not.toBeNull();
  });
}

function singleShotCard(): HTMLElement {
  return document.querySelector('.queue-provider-card') as HTMLElement;
}

function advancedFold(): HTMLElement {
  return document.querySelector('.advanced-fold') as HTMLElement;
}

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('顶部模式入口（唯一权威状态）', () => {
  it('演示徽章常驻且提供启用入口；点击打开向导', async () => {
    const user = userEvent.setup();
    render(<App />);
    const entry = await topbarModeEntry();
    expect(entry.textContent).toContain('演示体验');
    expect(entry.textContent).toContain('启用真实制作');
    await user.click(entry);
    // 单一路径向导打开，并显示分阶段检查。
    expect(await screen.findByRole('dialog', { name: '启用真实制作' })).toBeInTheDocument();
    expect((await screen.findByTestId('wizard-stages')).textContent).toContain('检查本机硬件与基础程序');
  });

  it('环境不完整时给出中文阻断原因，不暴露英文异常码', async () => {
    stub.add({
      method: 'GET', pattern: /\/api\/v1\/setup\/environment$/,
      respond: () => jsonResponse({ blockers: [{ code: 'GPU_VRAM_BELOW_FLOOR' }] }),
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await topbarModeEntry());
    const result = await screen.findByTestId('wizard-result');
    await waitFor(() => {
      expect(result.textContent).toContain('显卡可用显存不足');
    });
    expect(result.textContent).not.toMatch(/GPU_VRAM_BELOW_FLOOR/);
  });

  it('检测到已有 ComfyUI 时先复用，验证与激活走完整链路', async () => {
    const calls: string[] = [];
    const record = (mark: string) => () => { calls.push(mark); return jsonResponse({}); };
    stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({ installations: ['D:/ComfyUI'] }) });
    stub.add({
      method: 'POST', pattern: /setup\/existing-installations\/inspect$/,
      respond: () => jsonResponse({ root: 'D:/ComfyUI', snapshot: 'snap', candidate_bytes: 4096,
        matches: [{ component_id: 'h3', size: 4096 }], missing: [], skipped: [] }),
    });
    stub.add({
      method: 'POST', pattern: /setup\/existing-installations\/import-models$/,
      respond: () => { calls.push('import'); return jsonResponse({ reused_bytes: 4096, rejected: [] }); },
    });
    stub.add({ method: 'GET', pattern: /setup\/readiness$/, respond: () => jsonResponse({
      ...READINESS, layers: [{ layer: 'L2_files_verified', status: 'READY' }] }) });
    stub.add({
      method: 'GET', pattern: /setup\/runtime$/,
      respond: () => jsonResponse({ ...RUNTIME, status: 'NOT_ACTIVATED',
        candidate: { project_id: 'proj-1', recipe_sha256: 'r'.repeat(64) } }),
    });
    stub.add({
      method: 'POST', pattern: /setup\/next-instance-plan$/,
      respond: () => jsonResponse({ complete: true }),
    });
    stub.add({ method: 'POST', pattern: /setup\/profiles$/, respond: record('profiles') });
    stub.add({ method: 'POST', pattern: /setup\/configuration$/, respond: record('configuration') });
    stub.add({
      method: 'POST', pattern: /setup\/services\/validate$/,
      respond: () => { calls.push('validate'); return jsonResponse({ status: 'READY' }); },
    });
    stub.add({
      method: 'POST', pattern: /setup\/runtime\/activate$/,
      respond: (_url, init) => {
        calls.push('activate');
        stub.add({ method: 'GET', pattern: /system\/status$/, respond: () => jsonResponse({
          ...SYSTEM_STATUS, production_mode: productionMode({ mode: 'READY' }),
        }) });
        const body = JSON.parse(String(init?.body));
        expect(body.recipe_sha256).toBe('r'.repeat(64));
        return jsonResponse({ status: 'ACTIVE' });
      },
    });

    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<App />);
    await user.click(await topbarModeEntry());
    // 复用邀请出现：已有模型优先，不默认重新下载。
    const reuse = await screen.findByTestId('wizard-reuse');
    expect(reuse.textContent).toContain('可复用的已有模型');
    await user.click(within(reuse).getByRole('button', { name: '校验并复用' }));
    const result = await screen.findByTestId('wizard-result');
    await waitFor(() => {
      expect(result.textContent).toContain('真实制作已准备好');
    }, { timeout: 4000 });
    // 顺序契约：先复用已有模型，再登记方案，最后验证并激活。
    expect(calls).toEqual(['import', 'profiles', 'configuration', 'validate', 'activate']);
    // 成功后的返回入口直接指回制作。
    expect(result.textContent).toContain('返回作品，开始制作');
  });

  it('向导无项目时明确要求先创建作品', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([]) });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText('创建我的第一部AI短剧')).toBeInTheDocument();
    // 首启卡片的模式入口同样存在（顶部）。
    await user.click(await topbarModeEntry());
    const result = await screen.findByTestId('wizard-result');
    expect(result.textContent).toContain('先创建作品');
  });
});

describe('任务提交与模式一致', () => {
  it('READY 状态下单镜头真实提交 provider=comfyui', async () => {
    stub.add({
      method: 'GET', pattern: /system\/status$/,
      respond: () => jsonResponse({ ...SYSTEM_STATUS, render_mode: 'comfyui', production_mode: PRODUCTION_MODE_READY }),
    });
    realReadyProject();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const user = userEvent.setup();
    render(<App />);
    await openSingleShotTools(user);
    await user.selectOptions(await within(singleShotCard()).findByLabelText(/生成方案/), 'wf-1');
    await user.selectOptions(await within(singleShotCard()).findByLabelText(/自动检查范围/), 'full');
    const queue = await within(advancedFold()).findByRole('button', { name: /加入队列/ });
    await waitFor(() => { expect(queue).toBeEnabled(); });
    await user.click(queue);
    await waitFor(() => {
      const body = stub.callsOf('POST', /projects\/proj-1\/jobs$/)[0].body as { provider: string; workflow_profile_id: string };
      expect(body.provider).toBe('comfyui');
      expect(body.workflow_profile_id).toBe('wf-1');
    });
  });

  it('演示状态下任务只能是 mock，真实选项被锁', async () => {
    realReadyProject();
    const user = userEvent.setup();
    render(<App />);
    await openSingleShotTools(user);
    const provider = await within(singleShotCard()).findByLabelText(/生成方式/);
    expect((provider as HTMLSelectElement).disabled).toBe(true);
    await user.click(within(advancedFold()).getByRole('button', { name: /加入队列/ }));
    await waitFor(() => {
      const body = stub.callsOf('POST', /projects\/proj-1\/jobs$/)[0].body as { provider: string };
      expect(body.provider).toBe('mock');
    });
  });

  it('无 LLM 配置时明确提示 AI 分镜未就绪，保留手工入口', async () => {
    realReadyProject();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: /AI导演制作/ }));
    expect(await screen.findByText(/AI 分镜能力未就绪/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /去配置模型服务/ })).toBeInTheDocument();
    // 手工分镜入口仍在（高级折叠里的镜头表单）。
    await user.click(await screen.findByText('单镜头制作与任务明细（高级）'));
    await waitFor(() => {
      expect(within(advancedFold()).getByText(/时长（4–15 秒）/)).toBeInTheDocument();
    });
  });

  it('BLOCKED 状态提供检查并修复入口而不是退回演示', async () => {
    stub.add({
      method: 'GET', pattern: /system\/status$/,
      respond: () => jsonResponse({
        ...SYSTEM_STATUS, render_mode: 'unavailable',
        production_mode: productionMode({
          mode: 'BLOCKED', label: '真实制作暂不可用', action: 'check_fix',
          detail: '本机制作环境的固定文件发生了变化，需要重新检查配置。', code: 'ACTIVE_RUNTIME_CONFIGURATION_CHANGED',
        }),
      }),
    });
    const user = userEvent.setup();
    render(<App />);
    const entry = await topbarModeEntry();
    expect(entry.textContent).toContain('真实制作暂不可用');
    expect(entry.textContent).toContain('检查并修复');
    await user.click(entry);
    expect(await screen.findByRole('dialog', { name: '检查真实制作环境' })).toBeInTheDocument();
  });
});
