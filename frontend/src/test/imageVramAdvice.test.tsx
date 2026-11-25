import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ImageVramAdvice } from '../api';
import { DeploymentRecommendationCard } from '../components/DeploymentRecommendationCard';
import { ImageCapabilityCard } from '../components/ImageCapabilityCard';
import { ImageVramAdvicePanel } from '../shared/ImageVramAdvicePanel';
import { StyleLoraAssemblyPanel } from '../shared/StyleLoraAssemblyPanel';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

const CLASS_TABLE: ImageVramAdvice['classes'] = [
  { id: '24gb', label: '24 GB 档', min_mb: 23040, evidence: 'estimated', advice: '余量充足；两套配方、1024×1024 和多图参考都可以用。' },
  { id: '16gb', label: '16 GB 档', min_mb: 15360, evidence: 'measured_on_similar_hardware', advice: '实测可用，余量约 0.8 GB；出图时关掉其他占显存的程序。' },
  { id: '12gb', label: '12 GB 档', min_mb: 11776, evidence: 'reported_by_users', advice: '用户反馈可用；靠换页运行，配方未验证。' },
  { id: 'under_12gb', label: '12 GB 以下', min_mb: 8000, evidence: 'estimated', advice: '未验证：渲染器可以启动，能否出图没有记录。' },
  { id: 'below_start', label: '8 GB 以下', min_mb: 0, evidence: null, advice: '无法启动出图渲染器。' },
];

function makeAdvice(overrides: Partial<ImageVramAdvice> = {}): ImageVramAdvice {
  return {
    blocks: false,
    gpu: { total_mb: 16303, free_mb: 15400 },
    status: 'fits',
    class: { id: '16gb', label: '16 GB 档', min_mb: 15360, evidence: 'measured_on_similar_hardware' },
    evidence: { level: 'measured_on_similar_hardware', detail: '在 RTX 5080 16 GB 上实测过。' },
    usage_mb: 14580,
    peak_total_mb: 15473,
    measured_on: 'RTX 5080 16 GB · ComfyUI 0.39.0 · 2026-11-25',
    start_free_mb: 8000,
    supported_total_mb: 11776,
    headroom_mb: 830,
    headline: '实测可用：出图时整卡占用约 15473 MB，本卡余量约 830 MB。',
    notes: ['输出尺寸（640 到 1024 像素）和是否挂配方都不改变显存占用。'],
    classes: CLASS_TABLE,
    ...overrides,
  };
}

describe('ImageVramAdvicePanel 与三处前端复用', () => {
  it.each([
    {
      name: '24 GB 档（推算）',
      advice: makeAdvice({
        gpu: { total_mb: 24564, free_mb: 23800 },
        class: { id: '24gb', label: '24 GB 档', min_mb: 23040, evidence: 'estimated' },
        evidence: { level: 'estimated', detail: '这一档没有实测记录，结论由 16 GB 档的实测占用推算。' },
        headroom_mb: 9091,
        headline: '显存宽裕：出图时整卡占用约 15473 MB，本卡余量约 9091 MB。',
      }),
      expectedClass: '24 GB 档',
      expectedEvidence: '推算（尚未实测）',
    },
    {
      name: '16 GB 档（相近硬件实测）',
      advice: makeAdvice(),
      expectedClass: '16 GB 档',
      expectedEvidence: '相近硬件实测',
    },
    {
      name: '12 GB 档（用户反馈）',
      advice: makeAdvice({
        gpu: { total_mb: 12282, free_mb: 11800 },
        class: { id: '12gb', label: '12 GB 档', min_mb: 11776, evidence: 'reported_by_users' },
        evidence: { level: 'reported_by_users', detail: '据 RTX 4070 Super 12 GB 用户反馈可以正常出图；仓库里没有这一档的实测记录。' },
        headroom_mb: null,
        headline: '据 RTX 4070 Super 12 GB 用户反馈，这一档可以正常出图。',
        notes: ['两套配方（LoRA）在这一档没有验证过：建议先不挂配方出一张，确认可用后再试配方。'],
      }),
      expectedClass: '12 GB 档',
      expectedEvidence: '用户反馈（未实测）',
    },
    {
      name: '12 GB 以下（未验证）',
      advice: makeAdvice({
        status: 'unverified',
        gpu: { total_mb: 8188, free_mb: 8000 },
        class: { id: 'under_12gb', label: '12 GB 以下', min_mb: 8000 },
        evidence: { level: 'estimated', detail: '这一档没有实测记录，结论由 16 GB 档的实测占用推算。' },
        headroom_mb: null,
        headline: '本卡显存 8188 MB，低于已验证过的最低一档（12 GB 档）。渲染器可以启动，但能否出图还没有验证过。',
      }),
      expectedClass: '12 GB 以下',
      expectedEvidence: '推算（尚未实测）',
    },
    {
      name: '8 GB 以下（无法启动）',
      advice: makeAdvice({
        status: 'cannot_start',
        gpu: { total_mb: 6144, free_mb: 6000 },
        class: { id: 'below_start', label: '8 GB 以下', min_mb: 0 },
        evidence: { level: 'estimated', detail: '这一档没有实测记录，结论由 16 GB 档的实测占用推算。' },
        headroom_mb: null,
        headline: '本卡显存 6144 MB，低于出图渲染器的启动门槛（空闲 8000 MB），无法出图。',
      }),
      expectedClass: '8 GB 以下',
      expectedEvidence: '推算（尚未实测）',
    },
    {
      name: '未识别显卡（unknown）',
      advice: makeAdvice({
        status: 'unknown',
        gpu: null,
        class: null,
        evidence: { level: null, detail: '' },
        headroom_mb: null,
        headline: '没有识别到 NVIDIA 显卡，读不到显存，无法给出适配建议。',
        notes: [],
      }),
      expectedClass: null,
      expectedEvidence: null,
    },
  ])('渲染 $name 并高亮当前档位', ({ advice, expectedClass, expectedEvidence }) => {
    render(<ImageVramAdvicePanel advice={advice} gpuName={advice.gpu ? 'NVIDIA RTX' : null} />);
    const panel = screen.getByTestId('image-vram-advice');
    expect(panel).toHaveAttribute('data-status', advice.status);
    expect(panel).toHaveTextContent(advice.headline);
    expect(panel).toHaveTextContent('仅供参考，不拦截出图');
    if (advice.evidence.detail) {
      expect(panel).toHaveTextContent(advice.evidence.detail);
    }
    if (expectedEvidence) {
      expect(within(panel).getAllByText(expectedEvidence).length).toBeGreaterThanOrEqual(1);
    }
    const table = within(panel).getByRole('table', { name: '各显存档位建议' });
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    expect(rows).toHaveLength(5);
    const currentRows = rows.filter((row) => row.getAttribute('data-current') === 'true');
    if (expectedClass) {
      expect(currentRows).toHaveLength(1);
      expect(currentRows[0]).toHaveTextContent(expectedClass);
      expect(currentRows[0]).toHaveTextContent('当前本机');
    } else {
      expect(currentRows).toHaveLength(0);
      expect(panel).toHaveTextContent('未识别到 NVIDIA 显卡');
    }
  });

  it('StyleLoraAssemblyPanel 在 qwen_image_21 展示显存建议且永不禁用配方与挂载按钮；H3 不展示', async () => {
    const fix = {
      id: 'qwen-image-2.1-lora-fix',
      display_name: 'Qwen 画质修复',
      filename: 'fix.safetensors',
      storage_path: 'D:/Loras/fix.safetensors',
      sha256: 'a'.repeat(64),
      file_size_bytes: 54_800_124,
      default_strength: 0.75,
      trigger_word: '',
      origin: 'discovered',
      comfy_lora_name: 'fix.safetensors',
      status: 'READY',
      created_at: '2026-11-25T00:00:00Z',
      updated_at: '2026-11-25T00:00:00Z',
    };
    const stub = new StudioStub([
      {
        method: 'GET',
        pattern: /\/api\/v1\/loras\?base_model=qwen_image_21$/,
        respond: () => jsonResponse({
          items: [fix],
          comfy_reachable: true,
          presets: [{
            id: 'cinematic_photorealism',
            display_name: '电影级写实摄影',
            loras: [{ component_id: fix.id, lora_id: fix.id, strength_model: 0.75, status: 'READY' }],
            optional_loras: [],
          }],
          vram_advice: makeAdvice({
            status: 'cannot_start',
            gpu: { total_mb: 6144, free_mb: 5000 },
            class: { id: 'below_start', label: '8 GB 以下', min_mb: 0 },
            headline: '本卡显存 6144 MB，低于出图渲染器的启动门槛（空闲 8000 MB），无法出图。',
          }),
        }),
      },
      {
        method: 'GET',
        pattern: /\/api\/v1\/loras$/,
        respond: () => jsonResponse({ items: [], comfy_reachable: false }),
      },
    ]);

    const { unmount } = render(
      <StyleLoraAssemblyPanel baseModel="qwen_image_21" value={[]} onChange={() => {}} loraSupported />,
    );
    expect(await screen.findByTestId('image-vram-advice')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '电影级写实摄影' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: '挂载' })).not.toBeDisabled();
    unmount();

    render(<StyleLoraAssemblyPanel value={[]} onChange={() => {}} loraSupported />);
    await screen.findByText(/暂无已登记的风格 LoRA/);
    expect(screen.queryByTestId('image-vram-advice')).toBeNull();
    stub.restore();
  });

  it('ImageCapabilityCard 渲染 vram_advice 且保持纯只读无执行按钮', async () => {
    const stub = new StudioStub([{
      method: 'GET',
      pattern: /setup\/image-readiness$/,
      respond: () => jsonResponse({
        configured: true,
        layer: 'GRAPH_VERIFIED',
        families: {
          character: { layer: 'RENDER_VERIFIED', evidence_attempt_id: 'a1', can_generate: true },
          scene: { layer: 'GRAPH_VERIFIED', evidence_attempt_id: null, can_generate: false },
          prop: { layer: 'GRAPH_VERIFIED', evidence_attempt_id: null, can_generate: false },
        },
        renderer: { state: 'STOPPED', instance_id: 'i1', port: 8248, pid: null },
        fingerprint: 'f'.repeat(64),
        config_revision: 1,
        config_digest: 'c'.repeat(64),
        graph_sha256: 'g'.repeat(64),
        output_node_id: '461',
        output_class_type: 'SaveImage',
        model_files: [],
        graph_report: { complete: true, missing_nodes: [], missing_inputs: [] },
        prompt_template: { template_version: 'material-image-v6-qwen21', families: ['character', 'prop', 'scene'] },
        blockers: [],
        vram_advice: makeAdvice({
          gpu: { total_mb: 12282, free_mb: 11800 },
          class: { id: '12gb', label: '12 GB 档', min_mb: 11776, evidence: 'reported_by_users' },
          evidence: { level: 'reported_by_users', detail: '据 RTX 4070 Super 12 GB 用户反馈可以正常出图；仓库里没有这一档的实测记录。' },
          headroom_mb: null,
          headline: '据 RTX 4070 Super 12 GB 用户反馈，这一档可以正常出图。',
        }),
        license: { name: 'Qwen', scope: 'NON_COMMERCIAL_RESEARCH_OR_EVALUATION_ONLY', note: '研究或评估' },
        upstream: {
          template_repository: 'Comfy-Org/workflow_templates',
          template_commit: '1234567890ab',
          template_sha256: 't'.repeat(64),
          model_repository: 'Comfy-Org/Qwen-Image-2.1',
          model_revision: 'aacf31a6bbbb',
          required_bytes: 17283091112,
        },
      }),
    }]);

    render(<ImageCapabilityCard projectId="p1" busy={false} />);
    const adviceEl = await screen.findByTestId('image-vram-advice');
    expect(adviceEl).toHaveTextContent('据 RTX 4070 Super 12 GB 用户反馈，这一档可以正常出图。');
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    stub.restore();
  });

  it('DeploymentRecommendationCard 在 image_prep 展示 vram_advice 与 reported_by_users 标签，h3_video 不展示', async () => {
    const stub = new StudioStub([{
      method: 'POST',
      pattern: /\/api\/v1\/setup\/deployment-recommendations$/,
      respond: () => jsonResponse({
        recommended: [],
        blocked: [],
        host: { gpu: 'NVIDIA GeForce RTX 4070 SUPER', vram_mb: 12282, ram_mb: 31539, free_gb: 400 },
        notes: [],
        options: [
          {
            purpose: 'h3_video',
            label: 'H3 正式视频生成',
            skippable: false,
            catalog_id: 'h3-ref2va-windows-x64',
            catalog_version: '1.0',
            status: 'BLOCKED',
            requirements: { min_vram_mb: 15360, min_ram_mb: 32768, min_disk_gb: 180 },
            total_bytes: 40_000_000_000,
            reuse_bytes: 0,
            download_bytes: 40_000_000_000,
            blockers: [{ code: 'VRAM_INSUFFICIENT', required: 15360, actual: 12282 }],
            warnings: [],
            evidence: { level: 'estimated', verified: false },
          },
          {
            purpose: 'image_prep',
            label: '生图候选准备',
            skippable: true,
            catalog_id: 'qwen-image-2.1-windows-x64',
            catalog_version: '1.0',
            status: 'SUPPORTED',
            requirements: { min_vram_mb: 11776, min_ram_mb: 32768, min_disk_gb: 40 },
            total_bytes: 17_283_091_112,
            reuse_bytes: 0,
            download_bytes: 17_283_091_112,
            blockers: [],
            warnings: [],
            evidence: { level: 'reported_by_users', verified: false },
            vram_advice: makeAdvice({
              gpu: { total_mb: 12282, free_mb: 11800 },
              class: { id: '12gb', label: '12 GB 档', min_mb: 11776, evidence: 'reported_by_users' },
              evidence: { level: 'reported_by_users', detail: '据 RTX 4070 Super 12 GB 用户反馈可以正常出图；仓库里没有这一档的实测记录。' },
              headroom_mb: null,
              headline: '据 RTX 4070 Super 12 GB 用户反馈，这一档可以正常出图。',
            }),
          },
        ],
      }),
    }]);

    const user = userEvent.setup();
    render(<DeploymentRecommendationCard />);
    await user.click(screen.getByRole('button', { name: '检测并推荐' }));

    expect(await screen.findAllByTestId('image-vram-advice')).toHaveLength(1);
    const items = screen.getAllByRole('listitem');
    expect(within(items[0]).queryByTestId('image-vram-advice')).toBeNull();
    expect(within(items[1]).getByTestId('image-vram-advice')).toHaveTextContent('NVIDIA GeForce RTX 4070 SUPER');
    expect(within(items[1]).getAllByText('用户反馈（未实测）').length).toBeGreaterThanOrEqual(2);
    expect(within(items[1]).getByRole('checkbox')).not.toBeDisabled();
    stub.restore();
  });
});
