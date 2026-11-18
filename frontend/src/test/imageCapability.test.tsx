import { render, screen } from '@testing-library/react';
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
  renderer: { state: 'NOT_STARTED', instance_id: 'i1', port: 8188, pid: null },
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
    { method: 'POST', pattern: /setup\/image-runtime\/start$/, respond: () => jsonResponse({ status: 'RUNNING', base_url: 'http://127.0.0.1:8188' }) },
    { method: 'POST', pattern: /setup\/image-runtime\/stop$/, respond: () => jsonResponse({ status: 'STOPPED' }) },
    { method: 'POST', pattern: /setup\/image-runtime$/, respond: () => jsonResponse({ readiness: READY }) },
    { method: 'POST', pattern: /setup\/install-plan$/, respond: () => jsonResponse({ plan_id: 'plan-1' }) },
    { method: 'POST', pattern: /setup\/prepare\/execute$/, respond: () => jsonResponse({ status: 'QUEUED', task_id: 't1', steps: [] }) },
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

it('没有绑定受管渲染器时说明原因', async () => {
  stub = new StudioStub(routes(UNBOUND));
  render(<ImageCapabilityCard projectId="p1" busy={false} />);
  expect(await screen.findByText(/还没有绑定本机的受管渲染器/)).toBeTruthy();
});

it('R5 B7/INV-16：只读诊断卡没有第二执行入口，也不发任何执行请求', async () => {
  stub = new StudioStub(routes(UNBOUND));
  render(<ImageCapabilityCard projectId="p1" busy={false} />);
  await screen.findByText(/还没有绑定本机的受管渲染器/);
  // 下载/绑定/启停/验证的唯一入口是制作环境的持久整备任务与工作台后台管理。
  expect(screen.queryByRole('button', { name: /登记出图权重安装计划/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /绑定现有实例用于出图/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /启动本机出图渲染器/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /停止出图渲染器并释放显存/ })).toBeNull();
  expect(screen.queryByRole('button', { name: /验证三类真实小样/ })).toBeNull();
  expect(screen.getByText(/统一在「制作环境」一次确认/)).toBeTruthy();
  expect(stub.callsOf('POST', /setup\/install-plan$/)).toHaveLength(0);
  expect(stub.callsOf('POST', /setup\/image-runtime/)).toHaveLength(0);
  expect(stub.callsOf('POST', /setup\/prepare\/execute$/)).toHaveLength(0);
});