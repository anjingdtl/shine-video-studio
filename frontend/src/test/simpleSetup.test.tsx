import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupPanel } from '../components/SetupPanel';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

/**
 * R5 B7/INV-16 收敛后的专家诊断页合同：
 * 新的整备（下载/实例/登记/核验/激活）唯一入口是「制作环境」卡的持久整备任务；
 * 本页保留只读诊断、已有安装的原目录核验，以及既有计划的恢复（批准/整理）。
 * 浏览器不再创建并串行执行新的安装/实例链。
 */

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
  await screen.findByText('基础环境');
}

function catalogComponent(component_id: string, kind: string, role: string | null, files: number, bytes: number) {
  return {
    component_id, kind, role, files, bytes, revision: '1'.repeat(40), license_id: 'test-license',
    usage: 'test fixture', license_url: 'https://example.invalid/license', destination_family: null,
    status: 'READY', blocked_reason: '',
  };
}

describe('专家诊断页：只读诊断与唯一执行者', () => {
  it('发现已有安装时先检查复用，本页不创建任何安装计划', async () => {
    stub.add({ method: 'GET', pattern: /setup\/existing-installations(?:\?.*)?$/, respond: () => jsonResponse({
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
    // 检测只读：允许只读盘点（inspect），不得发起安装、导入或接管。
    expect(stub.callsOf('POST', /install-plan$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /import-models$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /existing-installations\/adopt$/)).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '检查已有目录' }));
    await screen.findByText('h3 / model.safetensors：待哈希校验');
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await user.click(screen.getByRole('button', { name: '校验并复用匹配模型' }));
    expect(stub.callsOf('POST', /import-models$/)).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '校验并复用匹配模型' }));
    await screen.findAllByText(/已校验并缓存 13 B/);
    expect(stub.callsOf('POST', /install-plan$/)).toHaveLength(0);
    // 浏览器串行链已删除：本页没有「下载缺项并自动配置」这类第二执行入口。
    expect(screen.queryByRole('button', { name: /下载缺项并自动配置/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /一键准备（自动登记并激活）/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /生成安装计划/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /生成实例链计划/ })).toBeNull();
  });

  it('已有 ComfyUI 被明确确认已安装，模型只读哈希核验且不复制或创建重复计划', async () => {
    stub.add({ method: 'GET', pattern: /setup\/existing-installations(?:\?.*)?$/, respond: () => jsonResponse({
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
    expect(stub.callsOf('POST', /existing-installations\/verify$/)).toHaveLength(1);
    expect(stub.callsOf('POST', /existing-installations\/import-models$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
  });

  it('扫描事实不冒充生产资格：局部审片文件如实报未就绪，视频卡不给核验绿灯', async () => {
    const components = [
      catalogComponent('comfyui', 'source', null, 0, 0),
      catalogComponent('minimax-h3-diffusion', 'model', null, 1, 100),
      catalogComponent('qwen3-vl-8b-fp8', 'review-model', 'VLM', 3, 300),
      catalogComponent('qwen3-asr-0.6b', 'review-model', 'ASR', 4, 400),
    ];
    stub.add({ method: 'GET', pattern: /setup\/catalog$/, respond: () => jsonResponse({
      catalog_id: 'c', catalog_version: '1', generated: '', platform: {}, total_bytes: 800, components,
    }) });
    stub.add({ method: 'GET', pattern: /setup\/existing-installations(?:\?.*)?$/, respond: () => jsonResponse({
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
    await user.click(screen.getByRole('button', { name: '确认已安装并核验' }));

    expect(await screen.findByText(/建议审片模型未就绪（1 个模型包 · 已核验 1\/3 个文件，不阻断 H3 出片）/)).toBeInTheDocument();
    expect(screen.getByText(/建议审片模型未就绪（1 个模型包 · 已核验 3\/4 个文件，不阻断 H3 出片）/)).toBeInTheDocument();
    // R5 B7：视频卡只报扫描事实，就绪结论指向唯一裁决（制作环境卡）。
    expect(screen.getByText(/扫描事实：1\/1 个固定版本文件哈希匹配（就绪结论见上方制作环境卡）/)).toBeInTheDocument();
    expect(screen.queryByText(/固定版本文件已安装并哈希核验/)).not.toBeInTheDocument();
  });

  it('自动检索失败时如实报错，允许重新检测，不发起任何写操作', async () => {
    stub.add({ method: 'GET', pattern: /setup\/existing-installations(?:\?.*)?$/, respond: () => jsonResponse({ detail: '扫描失败' }, 503) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await user.click(screen.getByRole('button', { name: '检测本机' }));
    await screen.findByText('扫描失败');
    expect(stub.calls.filter(c => c.method !== 'GET')).toHaveLength(0);
  });

  it('检测只读；无需密码', async () => {
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    expect(screen.getByLabelText('API Key')).toHaveAttribute('type', 'password');
    expect(screen.queryByLabelText(/操作员.*密码|操作员.*凭据/)).toBeNull();
    expect(stub.calls.filter(c => c.method !== 'GET')).toHaveLength(0);
  });

  it('可选审片补装提交同一持久整备入口，不直建安装计划', async () => {
    stub.add({ method: 'GET', pattern: /setup\/catalog$/, respond: () => jsonResponse({
      catalog_id: 'c', catalog_version: '1', generated: '', platform: {}, total_bytes: 800,
      components: [
        catalogComponent('comfyui', 'source', null, 1, 100),
        catalogComponent('minimax-h3-diffusion', 'model', null, 1, 100),
        catalogComponent('qwen3-vl-8b-fp8', 'review-model', 'VLM', 3, 300),
        catalogComponent('qwen3-asr-0.6b', 'review-model', 'ASR', 4, 400),
      ],
    }) });
    stub.add({ method: 'POST', pattern: /setup\/prepare\/execute$/, respond: () => jsonResponse({
      status: 'QUEUED', task_id: 'setup-task', steps: ['RECONCILE', 'DOWNLOAD'], message: '等待后台开始',
    }) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    await user.click(screen.getByRole('button', { name: /补装建议审片模型/ }));
    await screen.findByText(/已交给后台整备/);
    expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
    const submitted = stub.callsOf('POST', /setup\/prepare\/execute$/);
    expect(submitted).toHaveLength(1);
    expect(submitted[0].body).toMatchObject({ project_id: 'proj-1', capabilities: ['vlm', 'asr'], verify_runtime: false });
  });

  it('单步服务核验失败即停，不自动激活、不重试', async () => {
    stub.add({ method: 'GET', pattern: /setup\/runtime$/, respond: () => jsonResponse({
      status: 'ACTIVE', render_provider: 'comfyui',
      candidate: { project_id: 'proj-1', recipe_sha256: 'a'.repeat(64) },
    }) });
    stub.add({ method: 'POST', pattern: /setup\/services\/validate$/, respond: () => jsonResponse({ detail: 'WORKFLOW_CHANGED' }, 409) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    await user.click(screen.getByRole('button', { name: '验证服务与工作流' }));
    await screen.findByText('WORKFLOW_CHANGED');
    expect(stub.callsOf('POST', /services\/validate$/)).toHaveLength(1);
    expect(stub.callsOf('POST', /runtime\/activate$/)).toHaveLength(0);
  });

  it('已有不确定安装时只展示恢复入口，不创建重复计划', async () => {
    stub.add({ method: 'GET', pattern: /comfyops\/install-plans/, respond: () => jsonResponse([
      { id: 'old', sha256: 'p', status: 'NEEDS_REVIEW', payload: { deployment: true } },
    ]) });
    stub.add({ method: 'GET', pattern: /install-plans\/old\/attempts/, respond: () => jsonResponse([
      { id: 'attempt-old', status: 'FAILED_RECONCILE_REQUIRED' },
    ]) });
    stub.add({ method: 'POST', pattern: /preparation\/approval-grants$/, respond: () => jsonResponse({ grant_id: 'g1', snapshot: 's1' }) });
    stub.add({ method: 'POST', pattern: /install-attempts\/attempt-old\/reconcile$/, respond: () => jsonResponse({ status: 'RECONCILED' }) });
    const user = userEvent.setup();
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await detect(user);
    await screen.findByText('整理中断的安装');
    expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: '整理中断的安装' }));
    await screen.findByText(/没有重新下载/);
    expect(stub.callsOf('POST', /install-attempts\/attempt-old\/reconcile$/)).toHaveLength(1);
    expect(stub.callsOf('POST', /install-plans\/.*\/execute$/)).toHaveLength(0);
  });

  it('安装进度是只读投影：重附着既有尝试，不发起任何执行请求', async () => {
    stub.add({ method: 'GET', pattern: /comfyops\/install-plans/, respond: () => jsonResponse([
      { id: 'download', sha256: 'p', status: 'INSTALLING', payload: { deployment: true } },
    ]) });
    stub.add({ method: 'GET', pattern: /install-plans\/download\/attempts/, respond: () => jsonResponse([
      { attempt_id: 'attempt-download', plan_id: 'download', stage: 'DOWNLOADING', status: 'INSTALLING',
        confirmed_bytes: 1, total_bytes: 4, speed_bps: 0, retry_count: 0, cancel_requested: false,
        pause_requested: false, terminal: false, resumable: true },
    ]) });
    stub.add({ method: 'GET', pattern: /installation-attempts\/attempt-download$/, respond: () => jsonResponse({
      attempt_id: 'attempt-download', plan_id: 'download', stage: 'DOWNLOADING', status: 'INSTALLING',
      confirmed_bytes: 1, total_bytes: 4, speed_bps: 0, retry_count: 0, cancel_requested: false,
      pause_requested: false, terminal: false, resumable: true,
    }) });
    render(<SetupPanel projectId="proj-1" onRefresh={refresh} />);
    await screen.findByText(/安装进度/);
    // 重附着后只读轮询真实阶段（2s 节拍）；不发起任何执行请求。
    await waitFor(() => expect(stub.callsOf('GET', /installation-attempts\/attempt-download$/).length).toBeGreaterThanOrEqual(1), { timeout: 6000 });
    expect(stub.calls.filter(c => c.method === 'POST')).toHaveLength(0);
  });
});