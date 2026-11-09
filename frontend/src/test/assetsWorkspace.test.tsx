import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { AssetsWorkspace } from '../asset-center/AssetsWorkspace';

const apiMock = vi.hoisted(() => ({
  createImageJobs: vi.fn(async (_body: Record<string, unknown>) => ({ items: [], notice: '本机验证样例已建立。' })),
  createVoiceJob: vi.fn(async (_body: Record<string, unknown>) => ({
    item: { id: 'voice-task-1', kind: 'voice', mode: 'DESIGN', status: 'QUEUED', state_version: 0,
      error_code: null, external_outcome: 'NOT_SENT', external_prompt_id: null, result_version_id: null,
      capability: null, candidate_index: null, attempts: [], created_at: '', updated_at: '' },
    capability: {}, notice: '声音任务已恢复。',
  })),
  saveProjectAssetToLibrary: vi.fn(async () => ({ id: 'library-1', title: 'locked' })),
  listLibraryAssets: vi.fn(async () => ({ items: [] as unknown[], limit: 100, offset: 0 })),
  listLibraryVoices: vi.fn(async () => ({ items: [] as unknown[] })),
  getLibraryAsset: vi.fn(async (_id: string): Promise<unknown> => { throw new Error('asset not found'); }),
  grantVersionPromotion: vi.fn(async () => ({ grant_id: 'grant-1', snapshot: 'version-snapshot' })),
  promoteLibraryVersion: vi.fn(async () => ({})),
}));

vi.mock('../asset-center/api', () => ({
  createImageJobs: apiMock.createImageJobs,
  createVoiceJob: apiMock.createVoiceJob,
  saveProjectAssetToLibrary: apiMock.saveProjectAssetToLibrary,
  listLibraryAssets: apiMock.listLibraryAssets,
  listLibraryVoices: apiMock.listLibraryVoices,
  getLibraryAsset: apiMock.getLibraryAsset,
  grantVersionPromotion: apiMock.grantVersionPromotion,
  promoteLibraryVersion: apiMock.promoteLibraryVersion,
  getCapabilities: vi.fn(async () => ({
    workspaces: ['director', 'assets'],
    image_capabilities: Object.fromEntries(['T2I', 'I2I', 'INPAINT', 'MULTIREF', 'WARDROBE', 'RGBA'].map((mode) => [
      `QWEN_IMAGE_${mode}`, { state: 'PENDING_LOCAL_VALIDATION', can_generate: false, can_validate: mode === 'T2I', reason: '本机验证尚未完成。' },
    ])),
    voice_capabilities: { TTS_MANDARIN: { state: 'PENDING_LOCAL_VALIDATION', reason: '本机验证尚未完成。' } },
    t2i_runtime: {},
  })),
  listAssignments: vi.fn(async () => ({ items: [] })),
  getLibraryTask: vi.fn(async () => { throw new Error('task not found'); }),
}));

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/');
});

it('本机候选可以直接进行人工晋升', async () => {
  const version = {
    id: 'version-1', ordinal: 1, parent_version_id: null, filename: 'portrait.png', kind: 'image',
    content_type: 'image/png', content_sha256: 'a'.repeat(64), byte_size: 10, width: 32, height: 32,
    duration_s: null, source_mode: 'T2I', prompt: 'portrait', workflow_revision_hash: 'hash',
    rights_snapshot_hash: 'rights-snapshot', references_manifest: [],
    rights: { id: 'rights-1', status: 'DECLARED', license_kind: 'QWEN_RESEARCH_ONLY', allowed_project_ids: [],
      permitted_uses: ['research'], commercial_allowed: false, expires_at: null, revoked_at: null },
    provenance: {}, qc: { technical: 'pass' }, status: 'READY_FOR_REVIEW', is_mock: false,
    media_url: '/api/v1/asset-center/versions/version-1/media',
  };
  const asset = { id: 'asset-1', title: 'Portrait', kind: 'image', category: 'character', tags: [], collection: null,
    favorite: false, status: 'ACTIVE', state_version: 1, version_count: 1, current_version: version, versions: [version] };
  apiMock.listLibraryAssets.mockResolvedValueOnce({ items: [asset], limit: 100, offset: 0 });
  apiMock.getLibraryAsset.mockResolvedValue(asset);
  const user = userEvent.setup();
  render(
    <AssetsWorkspace page="library" onNavigate={vi.fn()} onWorkspace={vi.fn()}
      projects={[{ id: 'project-a', title: '作品 A' } as never]} projectId="" projectAssets={[]} />,
  );

  await user.click(await screen.findByRole('button', { name: /Portrait/ }));
  await user.click(await screen.findByRole('button', { name: /检查并人工晋升/ }));
  await waitFor(() => expect(apiMock.promoteLibraryVersion).toHaveBeenCalledWith('version-1',
    expect.objectContaining({ grant_id: 'grant-1', snapshot: 'version-snapshot' })));
});

it('可以直接收藏已锁定的项目素材', async () => {
  apiMock.saveProjectAssetToLibrary.mockClear();
  const user = userEvent.setup();
  const projectAsset = {
    id: 'asset-1', project_id: 'project-1', filename: 'portrait.png', sha256: 'a'.repeat(64),
    kind: 'image', role: 'character', tags: [], approved: true, locked: true,
  } as never;
  render(
    <AssetsWorkspace
      page="assignments"
      onNavigate={vi.fn()}
      onWorkspace={vi.fn()}
      projects={[]}
      projectId="project-1"
      projectAssets={[projectAsset]}
    />,
  );

  const saveButton = await screen.findByRole('button', { name: '收藏到全局库' });
  await user.click(saveButton);
  await waitFor(() => expect(apiMock.saveProjectAssetToLibrary).toHaveBeenCalledWith('project-1', 'asset-1', { title: 'portrait', category: 'character', tags: [] }));
});

it('素材中心可以在没有影视项目时独立打开并进入图片工坊', async () => {
  const onNavigate = vi.fn();
  const user = userEvent.setup();
  render(
    <AssetsWorkspace
      page="home"
      onNavigate={onNavigate}
      onWorkspace={vi.fn()}
      projects={[]}
      projectId=""
      projectAssets={[]}
    />,
  );

  expect(await screen.findByText('不需要先创建影视项目')).toBeInTheDocument();
  expect(await screen.findByText('素材库是空的。可以先在图片工坊导入一张图片。')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '图片工坊' }));
  expect(onNavigate).toHaveBeenCalledWith('images');
  expect(window.location.hash).toBe('#a/images');
});

it('切回文生图时不会隐藏或静默提交保留的参考图', async () => {
  localStorage.setItem('shine-assets-image-draft-v1', JSON.stringify({
    references: [{ id: 'source', filename: 'source.png', sha256: 'a'.repeat(64), width: 32, height: 32,
      media_url: '/source', rights_id: 'rights-1', role: 'canvas', order: 0 }],
    maskMedia: null,
  }));
  const user = userEvent.setup();
  render(<AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()}
    projects={[]} projectId="" projectAssets={[]} />);

  expect(await screen.findByText('文生图不会使用参考图')).toBeInTheDocument();
  await user.type(screen.getByPlaceholderText(/例如：雨后的老街/), '一盏红色纸灯');
  const submit = screen.getByRole('button', { name: '运行一次本机验证样例' });
  expect(submit).toBeDisabled();
  expect(apiMock.createImageJobs).not.toHaveBeenCalled();
});

it('刷新后恢复局部修订底图、遮罩、模式和提示词', async () => {
  localStorage.setItem('shine-assets-image-draft-v1', JSON.stringify({
    mode: 'INPAINT', title: '窗边灯光', prompt: '只修改窗边灯光', category: 'scene',
    width: 32, height: 32, candidateCount: 1, transparent: false, targetAssetId: '',
    references: [{ id: 'source', filename: 'source.png', sha256: 'a'.repeat(64), width: 32, height: 32,
      media_url: '/source', rights_id: 'rights-1', role: 'canvas', order: 0 }],
    maskMedia: { id: 'mask-1', filename: 'shine-mask.png', sha256: 'b'.repeat(64), width: 32, height: 32,
      media_url: '/mask', rights_id: 'rights-2', role: 'mask', order: 0 },
  }));
  render(<AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()} projects={[]} projectId="" projectAssets={[]} />);

  expect(await screen.findByLabelText('修改指令')).toHaveValue('只修改窗边灯光');
  expect(screen.getByText('真实遮罩已保存：32 × 32 像素')).toBeInTheDocument();
  await waitFor(() => expect(JSON.parse(localStorage.getItem('shine-assets-image-draft-v1') ?? '{}').maskMedia.id).toBe('mask-1'));
});

it('断网后的图片恢复沿用同一幂等键，避免重复创建候选任务', async () => {
  apiMock.createImageJobs.mockClear();
  apiMock.createImageJobs
    .mockRejectedValueOnce(new Error('网络暂时不可用'))
    .mockResolvedValueOnce({ items: [], notice: '已恢复同一任务。' });
  const user = userEvent.setup();
  render(<AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()} projects={[]} projectId="" projectAssets={[]} />);

  await user.type(await screen.findByLabelText('画面描述'), '纸灯笼');
  await user.click(screen.getByRole('button', { name: '运行一次本机验证样例' }));
  expect(await screen.findByText('网络暂时不可用')).toBeInTheDocument();
  const firstRequest = apiMock.createImageJobs.mock.calls[0][0];
  const idempotencyKey = firstRequest.idempotency_key;
  expect(idempotencyKey).toBeTruthy();

  await user.click(screen.getByRole('button', { name: '核对并恢复上次请求' }));
  await waitFor(() => expect(apiMock.createImageJobs).toHaveBeenCalledTimes(2));
  expect(apiMock.createImageJobs.mock.calls[1][0]).toMatchObject({
    idempotency_key: idempotencyKey,
    prompt: '纸灯笼',
    validation_only: true,
  });
  expect(localStorage.getItem('shine-assets-pending-image-intent-v1')).toBeNull();
  expect(await screen.findByText('已按同一幂等请求恢复任务；不会重复创建生成任务。')).toBeInTheDocument();
});

it('生成请求未完成时禁用重复点击', async () => {
  apiMock.createImageJobs.mockClear();
  let finishRequest!: (value: any) => void;
  apiMock.createImageJobs.mockImplementationOnce(() => new Promise((resolve) => { finishRequest = resolve; }));
  const user = userEvent.setup();
  render(<AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()} projects={[]} projectId="" projectAssets={[]} />);

  await user.type(await screen.findByLabelText('画面描述'), '蓝色雨伞');
  await user.click(screen.getByRole('button', { name: '运行一次本机验证样例' }));
  const busyButton = await screen.findByRole('button', { name: /正在保存请求/ });
  expect(busyButton).toBeDisabled();
  await user.click(busyButton);
  expect(apiMock.createImageJobs).toHaveBeenCalledTimes(1);
  finishRequest({ items: [], notice: '请求已保存。' });
  expect(await screen.findByText('请求已保存。')).toBeInTheDocument();
});

it('刷新后的声音任务恢复使用原幂等键', async () => {
  apiMock.createVoiceJob.mockClear();
  const body = {
    mode: 'DESIGN', title: '新音色 预览', character: '新音色', description: '',
    text: '你好，欢迎来到我的故事。', reference_text: '', reference_media_id: null,
    voice_profile_id: null, speed: 1, pause: 0, emotion: 'neutral',
  };
  localStorage.setItem('shine-assets-pending-voice-intent-v1', JSON.stringify({
    idempotency_key: 'voice-request-pending-0001', fingerprint: JSON.stringify(body), body,
  }));
  const user = userEvent.setup();
  render(<AssetsWorkspace page="voices" onNavigate={vi.fn()} onWorkspace={vi.fn()} projects={[]} projectId="" projectAssets={[]} />);

  await user.click(await screen.findByRole('button', { name: '核对并恢复上次请求' }));
  await waitFor(() => expect(apiMock.createVoiceJob).toHaveBeenCalledTimes(1));
  expect(apiMock.createVoiceJob.mock.calls[0][0]).toMatchObject({ idempotency_key: 'voice-request-pending-0001', text: body.text });
  expect(localStorage.getItem('shine-assets-pending-voice-intent-v1')).toBeNull();
  expect(JSON.parse(localStorage.getItem('shine-assets-recent-tasks') ?? '[]')[0]).toMatchObject({ id: 'voice-task-1', kind: 'voice' });
});

it('选择人物穿戴时自动把首张底图映射为人物槽位', async () => {
  localStorage.setItem('shine-assets-image-draft-v1', JSON.stringify({
    references: [
      { id: 'person', filename: 'person.png', sha256: 'a'.repeat(64), width: 32, height: 32, media_url: '/person', rights_id: 'r1', role: 'canvas', order: 0 },
      { id: 'shirt', filename: 'shirt.png', sha256: 'b'.repeat(64), width: 32, height: 32, media_url: '/shirt', rights_id: 'r2', role: 'garment', order: 1 },
    ],
    maskMedia: null,
  }));
  const user = userEvent.setup();
  render(
    <AssetsWorkspace
      page="images"
      onNavigate={vi.fn()}
      onWorkspace={vi.fn()}
      projects={[]}
      projectId=""
      projectAssets={[]}
    />,
  );

  await user.click(screen.getByRole('button', { name: /人物穿戴/ }));
  const firstRole = await screen.findByLabelText('第 1 张图片用途');
  await waitFor(() => expect(firstRole).toHaveValue('subject'));
  expect(firstRole).toHaveDisplayValue('人物主体');
});

it('本机验证按钮提交单个 validation_only 样例', async () => {
  apiMock.createImageJobs.mockClear();
  const user = userEvent.setup();
  render(
    <AssetsWorkspace page="images" onNavigate={vi.fn()} onWorkspace={vi.fn()} projects={[]} projectId="" projectAssets={[]} />,
  );

  await user.type(await screen.findByLabelText('画面描述'), 'a paper lantern');
  await user.click(screen.getByRole('button', { name: '运行一次本机验证样例' }));
  await waitFor(() => expect(apiMock.createImageJobs).toHaveBeenCalledTimes(1));
  expect(apiMock.createImageJobs.mock.calls[0][0]).toMatchObject({ validation_only: true, candidate_count: 1 });
  expect(await screen.findByText('本机验证样例已建立。')).toBeInTheDocument();
});
