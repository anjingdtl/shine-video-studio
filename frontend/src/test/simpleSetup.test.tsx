import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupPanel } from '../components/SetupPanel';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;
let refresh: ReturnType<typeof vi.fn>;
beforeEach(() => {
  stub = new StudioStub(baseRoutes());
  refresh = vi.fn().mockResolvedValue(undefined);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  stub.add({ method: 'GET', pattern: /setup\/catalog$/, respond: () => jsonResponse({
    catalog_id: 'c', catalog_version: '1', generated: '', platform: {}, total_bytes: 123,
    components: [{ component_id: 'comfyui', revision: 'pinned', bytes: 123 }],
  }) });
  stub.add({ method: 'GET', pattern: /setup\/connectivity$/, respond: () => jsonResponse({
    status: 'OK', blocked: [], upstreams: [], degraded: [], guidance: [], checked_at: '',
  }) });
});
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

async function detect(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '检测本机' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '下载缺项并自动配置' })).toBeEnabled());
}

function automaticRoutes(failValidation = false) {
  stub.add({ method: 'POST', pattern: /setup\/install-plan$/, respond: () => jsonResponse({ plan_id: 'download', sha256: 'p', status: 'AWAITING_APPROVAL' }) });
  stub.add({ method: 'POST', pattern: /install-plans\/.*\/approve$/, respond: () => jsonResponse({ sha256: 'p' }) });
  stub.add({ method: 'POST', pattern: /install-plans\/download\/execute$/, respond: () => jsonResponse({ status: 'FILES_VERIFIED' }) });
  let next = 0;
  stub.add({ method: 'POST', pattern: /setup\/next-instance-plan$/, respond: () => jsonResponse(next++ === 0
    ? { complete: false, plan_id: 'instance', sha256: 'p', status: 'AWAITING_APPROVAL', action: 'CREATE' }
    : { complete: true }) });
  stub.add({ method: 'POST', pattern: /instance-plans\/instance\/execute$/, respond: () => jsonResponse({ status: 'COMPLETED' }) });
  stub.add({ method: 'POST', pattern: /setup\/profiles$/, respond: () => jsonResponse({ profiles: [] }) });
  stub.add({ method: 'POST', pattern: /setup\/configuration$/, respond: () => jsonResponse({}) });
  stub.add({ method: 'GET', pattern: /setup\/runtime$/, respond: () => jsonResponse({
    status: 'NOT_ACTIVATED', render_provider: 'mock', candidate: { project_id: 'proj-1', recipe_sha256: 'a'.repeat(64) },
  }) });
  stub.add({ method: 'POST', pattern: /setup\/services\/validate$/, respond: () => failValidation
    ? jsonResponse({ detail: 'WORKFLOW_CHANGED' }, 409) : jsonResponse({ status: 'READY' }) });
  stub.add({ method: 'POST', pattern: /setup\/runtime\/activate$/, respond: () => jsonResponse({ status: 'ACTIVE' }) });
}

function catalogComponent(component_id: string, kind: string, role: string | null, files: number, bytes: number) {
  return {
    component_id, kind, role, files, bytes, revision: '1'.repeat(40), license_id: 'test-license',
    usage: 'test fixture', license_url: 'https://example.invalid/license', destination_family: null,
    status: 'READY', blocked_reason: '',
  };
}

describe('本机环境简化流程', () => {
  it('发现已有安装时先检查复用，不能直接要求重新下载', async () => {
    stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({
      installations: ['F:\\AI\\ComfyUI'],
    }) });
    stub.add({ method: 'POST', pattern: /existing-installations\/inspect$/, respond: () => jsonResponse({
      root: 'F:\\AI\\ComfyUI', snapshot: 'a'.repeat(64), candidate_bytes: 13, scanned_files: 2,
      matches: [{ component_id: 'h3', relative_path: 'model.safetensors', size: 13 }], missing: [], skipped: [],
    }) });
    stub.add({ method: 'POST', pattern: /existing-installations\/import-models$/, respond: () => jsonResponse({
      reused_bytes: 13, imported: [{ component_id: 'h3', relative_path: 'model.safetensors', size: 13, status: 'CACHED' }], rejected: [],
    }) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await user.click(screen.getByRole('button', { name: '检测本机' }));
    await screen.findByText('发现 1 个已有安装。');
    expect(screen.getByRole('button', { name: '下载缺项并自动配置' })).toBeDisabled();
    expect(stub.calls.filter(c => c.method !== 'GET')).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '检查已有目录' }));
    await screen.findByText('h3 / model.safetensors：待哈希校验');
    expect(screen.getByRole('button', { name: '下载缺项并自动配置' })).toBeDisabled();
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await user.click(screen.getByRole('button', { name: '校验并复用匹配模型' }));
    expect(stub.callsOf('POST', /import-models$/)).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '校验并复用匹配模型' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '下载缺项并自动配置' })).toBeEnabled());
    expect(stub.callsOf('POST', /install-plan$/)).toHaveLength(0);
    expect(stub.callsOf('GET', /connectivity$/)).toHaveLength(0);
    await user.type(screen.getByRole('textbox', { name: '已有 ComfyUI 目录' }), 'changed');
    expect(screen.getByRole('button', { name: '下载缺项并自动配置' })).toBeDisabled();
  });

  it('已有 ComfyUI 被明确确认已安装，模型只读哈希核验且不复制或创建重复计划', async () => {
    stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({
      installations: ['F:\\AI\\ComfyUI'],
    }) });
    stub.add({ method: 'POST', pattern: /existing-installations\/inspect$/, respond: () => jsonResponse({
      root: 'F:\\AI\\ComfyUI', snapshot: 'a'.repeat(64), candidate_bytes: 13, scanned_files: 2,
      matches: [{ component_id: 'h3', relative_path: 'model.safetensors', size: 13 }], missing: [], skipped: [],
      software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
        runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED', python_executable: 'F:\\AI\\python.exe' },
        node_revisions_verified: false },
    }) });
    stub.add({ method: 'POST', pattern: /existing-installations\/verify$/, respond: () => jsonResponse({
      status: 'INSTALLED_VERIFIED', software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
        runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false },
      verified: [{ component_id: 'h3', relative_path: 'model.safetensors', size: 13, status: 'INSTALLED_HASH_MATCH' }],
      rejected: [], verified_bytes: 13, copied_bytes: 0, runtime_verified: false,
    }) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await user.click(screen.getByRole('button', { name: '检测本机' }));
    await screen.findByText('发现 1 个已有安装。');
    await user.click(screen.getByRole('button', { name: '检查已有目录' }));
    await screen.findByText(/ComfyUI 已安装：主程序版本与固定清单一致/);
    await user.click(screen.getByRole('button', { name: '确认已安装并核验' }));
    await waitFor(() => expect(document.body).toHaveTextContent(/原目录未修改、未复制、未下载/));
    expect(screen.getByRole('button', { name: '已有安装等待兼容性验证' })).toBeDisabled();
    expect(stub.callsOf('POST', /existing-installations\/verify$/)).toHaveLength(1);
    expect(stub.callsOf('POST', /existing-installations\/import-models$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
  });

  it('新机自检区分不完整的建议审片模型，不把局部文件显示为已就绪', async () => {
    const components = [
      catalogComponent('comfyui', 'source', null, 0, 0),
      catalogComponent('minimax-h3-diffusion', 'model', null, 1, 100),
      catalogComponent('qwen3-vl-8b-fp8', 'review-model', 'VLM', 3, 300),
      catalogComponent('qwen3-asr-0.6b', 'review-model', 'ASR', 4, 400),
    ];
    stub.add({ method: 'GET', pattern: /setup\/catalog$/, respond: () => jsonResponse({
      catalog_id: 'c', catalog_version: '1', generated: '', platform: {}, total_bytes: 800, components,
    }) });
    stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({
      installations: ['F:\\AI\\ComfyUI'],
    }) });
    const verified = [
      { component_id: 'minimax-h3-diffusion', relative_path: 'h3.safetensors', size: 100, status: 'INSTALLED_HASH_MATCH' },
      { component_id: 'qwen3-vl-8b-fp8', relative_path: '.gitattributes', size: 10, status: 'INSTALLED_HASH_MATCH' },
      { component_id: 'qwen3-asr-0.6b', relative_path: '.gitattributes', size: 10, status: 'INSTALLED_HASH_MATCH' },
      { component_id: 'qwen3-asr-0.6b', relative_path: 'merges.txt', size: 10, status: 'INSTALLED_HASH_MATCH' },
      { component_id: 'qwen3-asr-0.6b', relative_path: 'vocab.json', size: 10, status: 'INSTALLED_HASH_MATCH' },
    ];
    stub.add({ method: 'POST', pattern: /existing-installations\/inspect$/, respond: () => jsonResponse({
      root: 'F:\\AI\\ComfyUI', snapshot: 'a'.repeat(64), candidate_bytes: 140, scanned_files: 10,
      matches: verified.map(row => ({ ...row, status: 'PENDING_HASH' })),
      missing: [
        { component_id: 'qwen3-vl-8b-fp8', relative_path: 'model-00001.safetensors', size: 100 },
        { component_id: 'qwen3-vl-8b-fp8', relative_path: 'model-00002.safetensors', size: 100 },
        { component_id: 'qwen3-asr-0.6b', relative_path: 'model.safetensors', size: 100 },
      ], skipped: [], software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
        runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false },
    }) });
    stub.add({ method: 'POST', pattern: /existing-installations\/verify$/, respond: () => jsonResponse({
      status: 'INSTALLED_VERIFIED', software: { installed: true, version_status: 'INSTALLED_VERSION_MATCH',
        runtime: { status: 'PYTHON_PRESENT_COMPATIBILITY_UNVERIFIED' }, node_revisions_verified: false },
      verified, rejected: [], verified_bytes: 140, copied_bytes: 0, runtime_verified: false,
    }) });

    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await user.click(screen.getByRole('button', { name: '检测本机' }));
    await screen.findByText('发现 1 个已有安装。');
    await user.click(screen.getByRole('button', { name: '检查已有目录' }));
    await screen.findByText(/候选可复用 5 项.*未找到固定版本文件 3 项/);
    expect(screen.getByText(/minimax-h3-diffusion \/ h3.safetensors：待哈希校验/)).toBeInTheDocument();
    expect(screen.queryByText(/哈希不匹配或文件已变化/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '确认已安装并核验' }));

    expect(await screen.findByText(/建议审片模型未就绪（已核验 1\/3 项，不阻断 H3 出片）/)).toBeInTheDocument();
    expect(screen.getByText(/建议审片模型未就绪（已核验 3\/4 项，不阻断 H3 出片）/)).toBeInTheDocument();
    expect(screen.getByText(/默认安装只准备 H3 所需组件，不会自动下载 ASR\/VLM 审片模型/)).toBeInTheDocument();
    expect(screen.queryByText('已安装并核验（原目录，未复制）')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '已有安装等待兼容性验证' })).toBeDisabled();
  });

  it('自动检索失败时不开放下载，允许重新检测', async () => {
    stub.add({ method: 'GET', pattern: /setup\/existing-installations$/, respond: () => jsonResponse({ detail: '扫描失败' }, 503) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await user.click(screen.getByRole('button', { name: '检测本机' }));
    await screen.findByText('扫描失败');
    expect(screen.queryByRole('button', { name: '下载缺项并自动配置' })).toBeNull();
    expect(stub.calls.filter(c => c.method !== 'GET')).toHaveLength(0);
  });

  it('检测只读，取消下载不发起安装；无需密码', async () => {
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    expect(document.querySelector('input[type=password]:not([placeholder="API Key（不会回显）"]):not([placeholder="更新密钥"])')).toBeNull();
    expect(screen.queryByLabelText(/操作员.*密码|操作员.*凭据/)).toBeNull();
    expect(stub.calls.filter(c => c.method !== 'GET')).toHaveLength(0);
    vi.mocked(window.confirm).mockReturnValue(false);
    await user.click(screen.getByRole('button', { name: '下载缺项并自动配置' }));
    expect(stub.calls.filter(c => c.method !== 'GET')).toHaveLength(0);
  });

  it('一次确认依次完成下载、实例、工作流、服务验证和激活', async () => {
    automaticRoutes();
    stub.add({ method: 'GET', pattern: /setup\/catalog$/, respond: () => jsonResponse({
      catalog_id: 'c', catalog_version: '1', generated: '', platform: {}, total_bytes: 800,
      components: [
        catalogComponent('comfyui', 'source', null, 1, 100),
        catalogComponent('minimax-h3-diffusion', 'model', null, 1, 100),
        catalogComponent('qwen3-vl-8b-fp8', 'review-model', 'VLM', 3, 300),
        catalogComponent('qwen3-asr-0.6b', 'review-model', 'ASR', 4, 400),
      ],
    }) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    await user.click(screen.getByRole('button', { name: '下载缺项并自动配置' }));
    await screen.findByText(/环境已配置并通过服务检测/);
    const posts = stub.calls.filter(c => c.method === 'POST').map(c => c.url);
    const index = (suffix: string) => posts.findIndex(url => url.endsWith(suffix));
    expect(index('/download/execute')).toBeLessThan(index('/instance/execute'));
    expect(index('/instance/execute')).toBeLessThan(index('/setup/configuration'));
    expect(index('/setup/configuration')).toBeLessThan(index('/services/validate'));
    expect(index('/services/validate')).toBeLessThan(index('/runtime/activate'));
    expect(stub.callsOf('POST', /approval-grants$/)).toHaveLength(2);
    const installPlan = stub.callsOf('POST', /setup\/install-plan$/)[0];
    expect((installPlan.body as { components: string[] }).components).toEqual(['comfyui', 'minimax-h3-diffusion']);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('ASR/VLM 仅用于建议审片，不会自动下载'));
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    for (const call of stub.calls) expect(new Headers(call.init?.headers).get('X-Shine-Local-Action')).toBe('1');
  });

  it('服务验证失败后停止，不激活、不重试', async () => {
    automaticRoutes(true);
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    await user.click(screen.getByRole('button', { name: '下载缺项并自动配置' }));
    await screen.findByText('WORKFLOW_CHANGED');
    expect(stub.callsOf('POST', /services\/validate$/)).toHaveLength(1);
    expect(stub.callsOf('POST', /runtime\/activate$/)).toHaveLength(0);
  });

  it('已有不确定安装时停止，不创建重复计划', async () => {
    stub.add({ method: 'GET', pattern: /comfyops\/install-plans/, respond: () => jsonResponse([
      { id: 'old', sha256: 'p', status: 'NEEDS_REVIEW', payload: { deployment: true } },
    ]) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    await user.click(screen.getByRole('button', { name: '下载缺项并自动配置' }));
    await screen.findByText(/已有未完成的安装记录/);
    expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
  });

  it('下载期间切换页面，已发出的安装保留，后续实例步骤不再启动', async () => {
    automaticRoutes();
    let finish: (value: ReturnType<typeof jsonResponse>) => void = () => undefined;
    stub.add({ method: 'POST', pattern: /install-plans\/download\/execute$/, respond: () =>
      new Promise(resolve => { finish = resolve; }) });
    const user = userEvent.setup();
    const view = render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    await user.click(screen.getByRole('button', { name: '下载缺项并自动配置' }));
    await waitFor(() => expect(stub.callsOf('POST', /download\/execute$/)).toHaveLength(1));
    view.unmount();
    finish(jsonResponse({ status: 'FILES_VERIFIED' }));
    await waitFor(() => expect(stub.callsOf('GET', /setup\/runtime$/).length).toBeGreaterThan(2));
    expect(stub.callsOf('POST', /next-instance-plan$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /runtime\/activate$/)).toHaveLength(0);
  });
});
