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
      reused_bytes: 13, rejected: [],
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
