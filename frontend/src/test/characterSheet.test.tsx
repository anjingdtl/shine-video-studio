import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { AssetsWorkspace } from '../asset-center/AssetsWorkspace';

const apiMock = vi.hoisted(() => ({
  getCapabilities: vi.fn(),
  createImageJobs: vi.fn(async (_body: Record<string, unknown>) => ({
    items: [{
      id: 'cs-task-1', kind: 'image', mode: 'I2I', status: 'RUNNING', state_version: 1,
      error_code: null, external_outcome: 'NOT_SENT', external_prompt_id: null, result_version_id: null,
      capability: { state: 'READY', can_generate: true }, candidate_index: 0,
        character_sheet: {
          enabled: true,
          layout_version: 'character-sheet-16x9-dual-closeup-triview-v2',
          note: '深色长裤与黑靴',
          progress: { current_step: 'fullbody_front', current_step_label: '全身正面照', completed_steps: [{ step_key: 'closeup_front' }, { step_key: 'closeup_3_4' }], total_steps: 5 },
          outputs: {},
        },
      attempts: [], created_at: '', updated_at: '',
    }],
    capability: { state: 'READY', can_generate: true },
    notice: '任务已排入本机生成队列。',
  })),
  uploadLibraryMedia: vi.fn(async () => ({
    id: 'media-char-1', filename: 'hero.png', sha256: 'a'.repeat(64),
    width: 768, height: 1024, media_url: '/api/v1/asset-center/media/media-char-1/content', rights_id: 'r-1',
  })),
  listLibraryAssets: vi.fn(async () => ({ items: [] as unknown[], limit: 100, offset: 0 })),
  getLibraryAsset: vi.fn(),
  getLibraryTask: vi.fn(async () => ({
    id: 'cs-task-1', kind: 'image', mode: 'I2I', status: 'CANDIDATE_READY', state_version: 5,
    error_code: null, external_outcome: 'SUCCEEDED', external_prompt_id: 'p-4', result_version_id: 'ver-comp',
    capability: { state: 'READY', can_generate: true }, candidate_index: 0,
    character_sheet: {
      enabled: true,
      layout_version: 'character-sheet-16x9-dual-closeup-triview-v2',
      note: '深色长裤与黑靴',
      progress: { current_step: null, current_step_label: null, completed_steps: [{}, {}, {}, {}, {}], total_steps: 5 },
      outputs: {
        composite_sheet: { version_id: 'ver-comp', asset_id: 'a-1', ordinal: 4, status: 'READY_FOR_REVIEW', width: 3584, height: 2016, sha256: 'c'.repeat(64), media_url: '/api/v1/asset-center/versions/ver-comp/media' },
        closeup_front: { version_id: 'ver-cu-f', asset_id: 'a-1', ordinal: 1, status: 'READY_FOR_REVIEW', width: 736, height: 928, sha256: 'f'.repeat(64), media_url: '/api/v1/asset-center/versions/ver-cu-f/media' },
        closeup_3_4: { version_id: 'ver-close', asset_id: 'a-1', ordinal: 2, status: 'READY_FOR_REVIEW', width: 736, height: 928, sha256: 'd'.repeat(64), media_url: '/api/v1/asset-center/versions/ver-close/media' },
        fullbody_triview: { version_id: 'ver-tri', asset_id: 'a-1', ordinal: 3, status: 'READY_FOR_REVIEW', width: 1872, height: 1376, sha256: 'e'.repeat(64), media_url: '/api/v1/asset-center/versions/ver-tri/media' },
      },
    },
    attempts: [], created_at: '', updated_at: '',
  })),
}));

vi.mock('../asset-center/api', () => ({
  getCapabilities: apiMock.getCapabilities,
  createImageJobs: apiMock.createImageJobs,
  uploadLibraryMedia: apiMock.uploadLibraryMedia,
  listLibraryAssets: apiMock.listLibraryAssets,
  getLibraryAsset: apiMock.getLibraryAsset,
  getLibraryTask: apiMock.getLibraryTask,
  createLibraryAsset: vi.fn(),
  patchLibraryAsset: vi.fn(),
  archiveLibraryAsset: vi.fn(),
  getVisualFacts: vi.fn(async () => ({ project_id: 'p1', project_title: '测试', film_style: '', film_type: null, world: '', style_version: null })),
  createVoiceJob: vi.fn(),
  reconcileLibraryImageTask: vi.fn(),
  newLibraryImageAttempt: vi.fn(),
  qcLibraryVersion: vi.fn(),
  grantVersionPromotion: vi.fn(),
  promoteLibraryVersion: vi.fn(),
  listLibraryVoices: vi.fn(async () => ({ items: [] })),
  createVoiceFreezeGrant: vi.fn(),
  freezeLibraryVoice: vi.fn(),
  assignLibraryVersion: vi.fn(),
  listAssignments: vi.fn(async () => ({ items: [] })),
  saveProjectAssetToLibrary: vi.fn(),
}));

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
  apiMock.listLibraryAssets.mockResolvedValue({ items: [], limit: 100, offset: 0 });
});

function capabilitiesPayload(loraReady: boolean) {
  const ready = { state: 'READY', can_generate: true, can_validate: false, reason: 'ok' };
  return {
    workspaces: ['director', 'assets', 'settings'],
    image_capabilities: {
      QWEN_IMAGE_T2I: { ...ready, verified_families: ['character'] },
      QWEN_IMAGE_I2I: ready,
      QWEN_IMAGE_INPAINT: ready,
      QWEN_IMAGE_MULTIREF: ready,
      QWEN_IMAGE_WARDROBE: ready,
      QWEN_IMAGE_RGBA: ready,
      QWEN_IMAGE_CHARACTER_SHEET: {
        ...ready,
        state: loraReady ? 'READY' : 'PENDING_LOCAL_VALIDATION',
        can_generate: loraReady,
        lora_id: 'qwen-image-2.1-lora-multiple-angles',
        lora_status: loraReady ? 'READY' : 'NOT_INSTALLED',
        lora_ready: loraReady,
        setup_hint: loraReady ? '' : '请前往「设置 → 制作环境 → 角色五视图定妆照」勾选下载并完成纳管；角色定妆照不会静默降级为无 LoRA 生图。',
        layout_version: 'character-sheet-16x9-dual-closeup-triview-v2',
      },
    },
    voice_capabilities: { TTS_MANDARIN: { state: 'PENDING_LOCAL_VALIDATION', reason: 'pending' } },
    t2i_runtime: {},
  };
}

it('缺少多视角 LoRA 时展示制作环境引导且禁止提交定妆照任务', async () => {
  apiMock.getCapabilities.mockResolvedValue(capabilitiesPayload(false));
  const openSetup = vi.fn();
  const user = userEvent.setup();
  render(
    <AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()}
      projects={[]} projectId="" projectAssets={[]} onOpenImageSetup={openSetup} />,
  );

  await user.click(screen.getByRole('button', { name: '图生图' }));
  expect(await screen.findByTestId('character-sheet-card')).toBeInTheDocument();
  expect(screen.getByText(/缺少「Qwen 多视角镜头」LoRA/)).toBeInTheDocument();
  expect(screen.getByText(/设置 → 制作环境 → 角色五视图定妆照/)).toBeInTheDocument();

  await user.click(screen.getByRole('switch', { name: '启用角色定妆照生成' }));
  expect(screen.getByLabelText(/补充描述/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/修改指令/)).not.toBeInTheDocument();

  const submitBtn = screen.getByRole('button', { name: /等待多视角 LoRA/ });
  expect(submitBtn).toBeDisabled();

  await user.click(screen.getByRole('button', { name: /前往设置 → 制作环境/ }));
  expect(openSetup).toHaveBeenCalledTimes(1);
});

it('就绪时提交定妆照载荷不带风格配方并展示进度与三产物入口', async () => {
  apiMock.getCapabilities.mockResolvedValue(capabilitiesPayload(true));
  const user = userEvent.setup();
  const { container } = render(
    <AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()}
      projects={[]} projectId="" projectAssets={[]} />,
  );

  await user.click(await screen.findByRole('button', { name: '图生图' }));
  await user.click(screen.getByRole('switch', { name: '启用角色定妆照生成' }));

  await user.type(screen.getByLabelText(/补充描述/), '深色长裤与黑靴');
  const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(fileInput, new File(['png'], 'hero.png', { type: 'image/png' }));
  await waitFor(() => expect(apiMock.uploadLibraryMedia).toHaveBeenCalled());

  const submitBtn = screen.getByRole('button', { name: /生成 16:9 角色定妆照/ });
  expect(submitBtn).toBeEnabled();
  await user.click(submitBtn);

  await waitFor(() => expect(apiMock.createImageJobs).toHaveBeenCalledTimes(1));
  const submitted = apiMock.createImageJobs.mock.calls[0][0];
  expect(submitted.mode).toBe('I2I');
  expect(submitted.character_sheet).toBe(true);
  expect(submitted.character_sheet_note).toBe('深色长裤与黑靴');
  expect(submitted.style_preset).toBeUndefined();
  expect(submitted.style_loras).toBeUndefined();

  expect(await screen.findByText(/步骤 3\/5：全身正面照/)).toBeInTheDocument();

  // Simulate task polling update with composite + intermediate outputs
  localStorage.setItem('shine-assets-recent-tasks', JSON.stringify([{ id: 'cs-task-1', kind: 'image' }]));
  cleanup();
  render(
    <AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()}
      projects={[]} projectId="" projectAssets={[]} />,
  );
  expect(await screen.findByTestId('character-sheet-task-outputs')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /整体定妆照（3584×2016）/ })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /肩上正面照（736×928）/ })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /3\/4 侧面肩上照（736×928）/ })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /全身三视图（1872×1376）/ })).toBeInTheDocument();
});

it('素材库详情页展示定妆照各版本角色标签', async () => {
  apiMock.getCapabilities.mockResolvedValue(capabilitiesPayload(true));
  const versions = [
    {
      id: 'v-comp', ordinal: 3, parent_version_id: null, filename: 'comp.png', kind: 'image',
      content_type: 'image/png', content_sha256: 'a'.repeat(64), byte_size: 100, width: 2864, height: 1104,
      duration_s: null, source_mode: 'CHARACTER_SHEET', prompt: '', workflow_revision_hash: 'h',
      references_manifest: [], rights: null,
      provenance: { character_sheet: { artifact_label: '整体角色定妆照（左特写 + 右三视图）', layout_version: 'character-sheet-left-closeup-right-triview-v1' } },
      qc: { technical: 'pass' }, status: 'READY_FOR_REVIEW', is_mock: false,
      media_url: '/api/v1/asset-center/versions/v-comp/media',
    },
    {
      id: 'v-tri', ordinal: 2, parent_version_id: null, filename: 'tri.png', kind: 'image',
      content_type: 'image/png', content_sha256: 'b'.repeat(64), byte_size: 100, width: 2048, height: 1104,
      duration_s: null, source_mode: 'CHARACTER_SHEET', prompt: '', workflow_revision_hash: 'h',
      references_manifest: [], rights: null,
      provenance: { character_sheet: { artifact_label: '全身三视图（正面/侧面/背面）', layout_version: 'character-sheet-left-closeup-right-triview-v1' } },
      qc: { technical: 'pass' }, status: 'READY_FOR_REVIEW', is_mock: false,
      media_url: '/api/v1/asset-center/versions/v-tri/media',
    },
  ];
  const asset = {
    id: 'asset-cs-1', title: '许知微定妆照', kind: 'image', category: 'character', tags: [], collection: null,
    favorite: false, status: 'ACTIVE', state_version: 1, version_count: 2, current_version: null, versions,
  };
  apiMock.listLibraryAssets.mockResolvedValueOnce({ items: [asset], limit: 100, offset: 0 });
  apiMock.getLibraryAsset.mockResolvedValue(asset);
  const user = userEvent.setup();
  render(
    <AssetsWorkspace page="library" onNavigate={vi.fn()} onWorkspace={vi.fn()}
      projects={[]} projectId="" projectAssets={[]} />,
  );

  await user.click(await screen.findByRole('button', { name: /许知微定妆照/ }));
  expect(await screen.findByText('整体角色定妆照（左特写 + 右三视图）')).toBeInTheDocument();
  expect(screen.getByText('全身三视图（正面/侧面/背面）')).toBeInTheDocument();
});
