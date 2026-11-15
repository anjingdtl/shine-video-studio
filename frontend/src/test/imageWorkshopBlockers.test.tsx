import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AssetsWorkspace } from '../asset-center/AssetsWorkspace';
import type { CapabilityReport } from '../asset-center/api';

/** R4-01：出图入口不得把不同的前置缺失折叠成同一句「等待本机能力验证」。
 *  屏上必须出现服务端真实报告的具名码 + 中文原因 + 下一步；
 *  未登记的码只展示码本身，不编造解释。 */

const capabilitiesMock = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));

vi.mock('../asset-center/api', () => ({
  createImageJobs: vi.fn(async () => ({ items: [], notice: '' })),
  createVoiceJob: vi.fn(async () => ({ item: {}, capability: {}, notice: '' })),
  saveProjectAssetToLibrary: vi.fn(async () => ({ id: 'library-1', title: 'locked' })),
  listLibraryAssets: vi.fn(async () => ({ items: [], limit: 100, offset: 0 })),
  listLibraryVoices: vi.fn(async () => ({ items: [] })),
  getLibraryAsset: vi.fn(async () => { throw new Error('asset not found'); }),
  grantVersionPromotion: vi.fn(async () => ({ grant_id: 'g', snapshot: 's', expires_at: '' })),
  promoteLibraryVersion: vi.fn(async () => ({})),
  assignLibraryVersion: vi.fn(async () => ({})),
  getCapabilities: vi.fn(async () => capabilitiesMock.current),
  listAssignments: vi.fn(async () => ({ items: [] })),
  getLibraryTask: vi.fn(async () => { throw new Error('task not found'); }),
}));

function report(blockers: Array<Record<string, unknown>>, extra: Record<string, unknown> = {}): CapabilityReport {
  const pending = { state: 'PENDING_LOCAL_VALIDATION', can_generate: false, can_validate: false, reason: 'Qwen-Image-2.1 T2I 需要本机模型、工作流和真实小样通过。' };
  return {
    workspaces: ['director', 'assets'],
    image_capabilities: {
      QWEN_IMAGE_T2I: pending,
      QWEN_IMAGE_I2I: { ...pending, reason: '需要本机 ComfyUI、模型文件及官方编辑节点合同通过验证。' },
      QWEN_IMAGE_INPAINT: pending, QWEN_IMAGE_MULTIREF: pending, QWEN_IMAGE_WARDROBE: pending,
      QWEN_IMAGE_RGBA: pending, QWEN_IMAGE_2K: pending,
    },
    voice_capabilities: { TTS_MANDARIN: { state: 'PENDING_LOCAL_VALIDATION', reason: '' } },
    t2i_runtime: { layer: 'NOT_CONFIGURED', blockers },
    ...extra,
  };
}

function renderWorkshop() {
  render(<AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()}
    projects={[]} projectId="" projectAssets={[]} onOpenImageSetup={vi.fn()} />);
}

afterEach(() => { cleanup(); localStorage.clear(); vi.clearAllMocks(); });

it('未绑定运行时时显示具名码、中文原因与下一步，而不只有一句等待', async () => {
  capabilitiesMock.current = report([{ code: 'IMAGE_RUNTIME_NOT_BOUND', layer: 'NOT_CONFIGURED' }]);
  renderWorkshop();
  expect(await screen.findByText('IMAGE_RUNTIME_NOT_BOUND')).toBeInTheDocument();
  expect(screen.getByText(/还没有绑定本机出图运行时/)).toBeInTheDocument();
  expect(screen.getByText(/下一步：打开本机出图配置，完成绑定/)).toBeInTheDocument();
});

it('权重缺失与未验证工作流分别具名，不折叠成同一句话', async () => {
  capabilitiesMock.current = report([
    { code: 'IMAGE_MODEL_FILE_MISSING', path_present: false },
    { code: 'IMAGE_GRAPH_NOT_VERIFIED', layer: 'GRAPH_VERIFIED' },
  ]);
  renderWorkshop();
  expect(await screen.findByText('IMAGE_MODEL_FILE_MISSING')).toBeInTheDocument();
  expect(screen.getByText('IMAGE_GRAPH_NOT_VERIFIED')).toBeInTheDocument();
  expect(screen.getByText(/固定清单里的模型权重文件在本机缺失/)).toBeInTheDocument();
  expect(screen.getByText(/出图工作流还没有在本机渲染器上验证/)).toBeInTheDocument();
});

it('未登记的阻塞码只展示码本身，不编造解释', async () => {
  capabilitiesMock.current = report([{ code: 'IMAGE_SOMETHING_NEW' }]);
  renderWorkshop();
  expect(await screen.findByText('IMAGE_SOMETHING_NEW')).toBeInTheDocument();
  expect(screen.getByText('服务端报告了这一阻塞项。')).toBeInTheDocument();
});

it('图生图缺参考图与本机能力缺失分开说明，编辑图节点差异具名列出', async () => {
  const base = report([{ code: 'IMAGE_RUNTIME_NOT_BOUND' }]);
  base.image_capabilities.QWEN_IMAGE_I2I = {
    ...base.image_capabilities.QWEN_IMAGE_I2I,
    graph_report: { complete: false, missing_nodes: ['QwenImageEditLoader'], missing_inputs: [] },
  };
  capabilitiesMock.current = base;
  renderWorkshop();
  // 切到图生图
  const modeButton = await screen.findByRole('button', { name: /图生图/ });
  modeButton.click();
  expect(await screen.findByText(/这一模式还需要至少一张底图或参考图/)).toBeInTheDocument();
  expect(screen.getByText(/QwenImageEditLoader/)).toBeInTheDocument();
  expect(screen.getByText('IMAGE_RUNTIME_NOT_BOUND')).toBeInTheDocument();
});

it('渲染器崩溃有自己的具名码，不折叠成「工作流未验证」', async () => {
  capabilitiesMock.current = report([
    { code: 'IMAGE_RENDERER_GONE', layer: 'GRAPH_VERIFIED', exit_code: 3221225477 },
  ]);
  renderWorkshop();
  expect(await screen.findByText('IMAGE_RENDERER_GONE')).toBeInTheDocument();
  expect(screen.getByText(/本机出图渲染器已经退出/)).toBeInTheDocument();
  expect(screen.getByText(/下一步：打开本机出图配置/)).toBeInTheDocument();
  expect(screen.queryByText('IMAGE_GRAPH_NOT_VERIFIED')).not.toBeInTheDocument();
});

it('渲染器在启动中退出时也是具名码与下一步', async () => {
  capabilitiesMock.current = report([
    { code: 'IMAGE_RENDERER_EXITED_DURING_STARTUP', layer: 'GRAPH_VERIFIED' },
  ]);
  renderWorkshop();
  expect(await screen.findByText('IMAGE_RENDERER_EXITED_DURING_STARTUP')).toBeInTheDocument();
  expect(screen.getByText(/渲染器在启动过程中自己退出了/)).toBeInTheDocument();
});

it('能力已就绪时不再显示阻塞清单', async () => {
  const base = report([]);
  base.image_capabilities.QWEN_IMAGE_T2I = {
    state: 'READY', can_generate: true, can_validate: true, reason: '本机已验证。',
  };
  capabilitiesMock.current = base;
  renderWorkshop();
  expect(await screen.findByText('本机已验证')).toBeInTheDocument();
  expect(screen.queryByText('IMAGE_RUNTIME_NOT_BOUND')).not.toBeInTheDocument();
});