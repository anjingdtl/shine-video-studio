import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { ImageCapabilityCard } from '../components/ImageCapabilityCard';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

const READY = {
  configured: true,
  layer: 'GRAPH_VERIFIED',
  families: {
    character: { layer: 'RENDER_VERIFIED', evidence_attempt_id: 'a1', can_generate: true },
    scene: { layer: 'GRAPH_VERIFIED', evidence_attempt_id: null, can_generate: false },
    prop: { layer: 'GRAPH_VERIFIED', evidence_attempt_id: null, can_generate: false },
  },
  fingerprint: 'f'.repeat(64),
  config_revision: 2,
  config_digest: 'c'.repeat(64),
  graph_sha256: 'g'.repeat(64),
  output_node_id: '461',
  output_class_type: 'SaveImage',
  model_files: [{ role: 'diffusion', filename: 'qwen.safetensors', expected_sha256: 'd'.repeat(64), actual_sha256: 'd'.repeat(64), cached: true, verified_at: '2026-11-05T09:00:00' }],
  graph_report: { complete: true, missing_nodes: [], missing_inputs: [] },
  prompt_template: { template_version: 'material-image-v1', families: ['character', 'prop', 'scene'] },
  blockers: [],
  license: { name: 'Qwen RESEARCH LICENSE AGREEMENT', scope: 'NON_COMMERCIAL_RESEARCH_OR_EVALUATION_ONLY', note: '第 1.i 条将非商业用途限定为研究或评估。' },
  upstream: {
    template_repository: 'Comfy-Org/workflow_templates',
    template_commit: '1234567890abcdef',
    template_sha256: 't'.repeat(64),
    model_repository: 'Comfy-Org/Qwen-Image-2.1',
    model_revision: 'aacf31a6bbbb',
    required_bytes: 17283091112,
  },
};

const UNBOUND = {
  ...READY,
  configured: false,
  layer: 'NOT_CONFIGURED',
  families: {
    character: { layer: 'NOT_CONFIGURED', evidence_attempt_id: null, can_generate: false },
    scene: { layer: 'NOT_CONFIGURED', evidence_attempt_id: null, can_generate: false },
    prop: { layer: 'NOT_CONFIGURED', evidence_attempt_id: null, can_generate: false },
  },
  blockers: [{ code: 'IMAGE_RUNTIME_NOT_BOUND', layer: 'NOT_CONFIGURED' }],
};

function routes(readiness: unknown) {
  return [
    { method: 'GET', pattern: /setup\/image-readiness$/, respond: () => jsonResponse(readiness) },
    { method: 'POST', pattern: /setup\/image-runtime\/validate$/, respond: () => jsonResponse({ run_id: 'run-9' }) },
    { method: 'POST', pattern: /preparation\/rights$/, respond: () => jsonResponse({ id: 'rights-1' }) },
    { method: 'POST', pattern: /preparation\/rights\/rights-1\/approve$/, respond: () => jsonResponse({ id: 'rights-1' }) },
    { method: 'POST', pattern: /preparation\/approval-grants$/, respond: () => jsonResponse({ grant_id: 'g1', snapshot: 's1' }) },
  ];
}

beforeEach(() => { localStorage.clear(); });
afterEach(() => { stub?.restore(); localStorage.clear(); });

it('按四层显示三类素材的就绪度，不把未验证说成可用', async () => {
  stub = new StudioStub(routes(READY));
  render(<ImageCapabilityCard projectId="p1" busy={false} />);
  expect(await screen.findByText('角色')).toBeTruthy();
  expect(screen.getByText('已真实出图验证')).toBeTruthy();
  // 场景与道具只到「节点与图已校验」，所以不能被算成可生成。
  expect(screen.getAllByText('节点与图已校验').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText(/角色 可生成/)).toBeTruthy();
  // 权重占用与许可限制必须写在页面上。
  expect(screen.getByText(/16.10 GiB/)).toBeTruthy();
  expect(screen.getByText(/研究或评估/)).toBeTruthy();
});

it('没有绑定受管渲染器时说明原因，并且不排队验证', async () => {
  stub = new StudioStub(routes(UNBOUND));
  const user = userEvent.setup();
  render(<ImageCapabilityCard projectId="p1" busy={false} />);
  expect(await screen.findByText(/还没有绑定本机的受管渲染器/)).toBeTruthy();
  const button = screen.getByRole('button', { name: /验证三类真实小样/ }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  await user.click(button);
  expect(stub.callsOf('POST', /setup\/image-runtime\/validate$/)).toHaveLength(0);
});

it('验证三类小样会先登记研究用途授权，再排队真实渲染', async () => {
  stub = new StudioStub(routes(READY));
  const user = userEvent.setup();
  render(<ImageCapabilityCard projectId="p1" busy={false} />);
  await screen.findByText('角色');
  await user.click(screen.getByRole('button', { name: /验证三类真实小样/ }));
  expect(await screen.findByText(/三类真实小样已排队/)).toBeTruthy();
  expect(stub.callsOf('POST', /preparation\/rights$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /setup\/image-runtime\/validate$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /setup\/image-runtime\/validate$/)[0].body).toMatchObject({
    project_id: 'p1',
    families: ['character', 'scene', 'prop'],
    rights_id: 'rights-1',
  });
});

it('可以登记出图权重安装计划，并展示下载量、占用与目标盘', async () => {
  stub = new StudioStub(routes(READY));
  stub.add({ method: 'GET', pattern: /setup\/storage$/, respond: () => jsonResponse({
    root: 'D:\\ComfyUI', free_bytes: 200 * 1024 ** 3, requires_selection: false,
  }) });
  stub.add({ method: 'POST', pattern: /setup\/install-plan$/, respond: () => jsonResponse({
    plan_id: 'plan-1', download_bytes: 17283091112, disk_required: 34566182224,
  }) });
  const user = userEvent.setup();
  render(<ImageCapabilityCard projectId="p1" busy={false} />);
  await screen.findByText('角色');
  await user.click(screen.getByRole('button', { name: '登记出图权重安装计划' }));
  expect(await screen.findByText(/预计下载 16.10 GiB/)).toBeTruthy();
  expect(screen.getByText(/需要 32.19 GiB/)).toBeTruthy();
  expect(screen.getByText(/目标盘 D:\\ComfyUI/)).toBeTruthy();
  expect(stub.callsOf('POST', /setup\/install-plan$/)[0].body).toMatchObject({
    catalog: 'qwen-image-2.1-windows-x64',
    components: ['qwen-image-2.1-unet', 'qwen-image-2.1-text-encoder', 'qwen-image-2.1-vae'],
    project_id: 'p1',
  });
});
