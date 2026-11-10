/** The project video library lists real on-disk videos only, keeps one row per
 *  artifact even when several receipts name it, and never offers deletion for
 *  the adopted version or a file a valid reel still uses. */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { VideoPreview } from '../components/GenerationSegmentsPanel';
import { StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
});

function entry(index: number, overrides: Record<string, unknown> = {}) {
  const adopted = index === 1;
  return {
    artifact_id: `art-${index}`,
    kind: index % 5 === 0 ? 'delivery' : 'shot',
    project_id: 'proj-1',
    project_title: '雾港夜账',
    episode_position: null,
    episode_label: '未分集',
    shot_numbers: [index],
    shot_range_label: String(index).padStart(3, '0'),
    shot_names: [`镜头 ${index}`],
    shot_name: `镜头 ${index}`,
    job_id: `job-${index}`,
    take: 1,
    version_label: index % 5 === 0 ? 'v1' : '第1次拍摄',
    review_state: index === 2 ? 'pending_review' : 'passed',
    adopted,
    file_state: 'ok',
    deletable: !adopted,
    delete_blockers: adopted ? [{ code: 'CURRENT_VERSION', message: '这是当前采用的版本' }] : [],
    shared_file: false,
    duration_s: 4.5,
    width: 1344,
    height: 768,
    fps: 24,
    generated_at: '2026-11-10T07:50:00',
    byte_size: 265045,
    file_name: `《雾港夜账_${String(index).padStart(3, '0')}镜_未分集》.mp4`,
    folder: 'history',
    is_mock: false,
    deleted: false,
    preview_url: `/api/v1/jobs/job-${index}/thumbnail`,
    play_url: `/api/v1/jobs/job-${index}/media`,
    ...overrides,
  };
}

function library(entries: ReturnType<typeof entry>[]) {
  return {
    project_id: 'proj-1', project_title: '雾港夜账', entries,
    summary: {
      total: entries.length,
      current: entries.filter((row) => row.adopted).length,
      history: entries.filter((row) => !row.adopted).length,
      pending_review: entries.filter((row) => row.review_state !== 'passed').length,
      missing: 0,
      deletable: entries.filter((row) => row.deletable).length,
    },
  };
}

async function openLibrary(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /成片库/ }));
  await screen.findByRole('heading', { name: /成片库/ });
}

describe('视频预览', () => {
  it('封面失败时直接给出可播放的内嵌视频，而不是一帧“预览不可用”', () => {
    render(<VideoPreview poster="/api/v1/jobs/job-x/thumbnail" video="/api/v1/jobs/job-x/media" label="单镜" />);
    fireEvent.error(document.querySelector('.library-card img') ?? document.querySelector('img')!);
    expect(screen.getByText('封面不可用，可直接播放')).toBeInTheDocument();
    const video = document.querySelector('.media-preview-fallback video') as HTMLVideoElement;
    expect(video).not.toBeNull();
    expect(video.getAttribute('src')).toBe('/api/v1/jobs/job-x/media');
  });
});

describe('成片库', () => {
  it('列出真实文件、状态与磁盘名，并把采用版本标为不可删除', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/video-library$/, respond: () => jsonResponse(library([
      entry(1), entry(2), entry(5),
    ])) });
    const user = userEvent.setup();
    render(<App />);
    await openLibrary(user);

    expect(await screen.findByText('当前采用')).toBeInTheDocument();
    expect(screen.getByText('待审')).toBeInTheDocument();
    expect([...document.querySelectorAll('.library-file')].some((node) =>
      node.textContent?.includes('《雾港夜账_001镜_未分集》.mp4'))).toBe(true);
    expect(screen.getByText('不可删除：这是当前采用的版本')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /下载/ })).not.toBeInTheDocument();
  });

  it('全选只作用于本页；跨页勾选数量可见，确认单列出将删除的文件', async () => {
    const entries = Array.from({ length: 13 }, (_value, index) => entry(index + 1));
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/video-library$/, respond: () => jsonResponse(library(entries)) });
    stub.add({ method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/video-library\/delete$/, respond: () => jsonResponse({
      deleted: entries.filter((row) => row.artifact_id !== 'art-1').slice(0, 12).map((row) => row.artifact_id),
      already_deleted: [], missing: [], blocked: [], failed: [], files_deleted: 11, files_kept_shared: 0,
    }) });
    const user = userEvent.setup();
    render(<App />);
    await openLibrary(user);

    await screen.findByText('第 1–12 条 / 共 13 条');
    await user.click(screen.getByRole('checkbox', { name: /全选本页/ }));
    expect(document.querySelector('.library-selection-note')).toHaveTextContent('已勾选 12 条（跨全部页）');
    await user.click(screen.getByRole('button', { name: '下一页' }));
    await screen.findByText('第 13–13 条 / 共 13 条');
    await user.click(screen.getByRole('checkbox', { name: /全选本页/ }));
    expect(document.querySelector('.library-selection-note')).toHaveTextContent('已勾选 13 条（跨全部页）');

    await user.click(screen.getByRole('button', { name: /批量删除（12）/ }));
    const dialog = await screen.findByRole('dialog', { name: '确认批量删除' });
    expect(dialog).toHaveTextContent('雾港夜账');
    expect(dialog).toHaveTextContent('将删除的文件');
    expect(dialog).toHaveTextContent('《雾港夜账_002镜_未分集》.mp4');
    await user.click(within(dialog).getByRole('button', { name: /确认删除 12 条/ }));

    await waitFor(() => expect(stub.callsOf('POST', /video-library\/delete$/)).toHaveLength(1));
    const body = stub.callsOf('POST', /video-library\/delete$/)[0].body as { artifact_ids: string[] };
    expect(body.artifact_ids).toHaveLength(12);
    expect(body.artifact_ids).not.toContain('art-1');
    expect((await screen.findAllByText(/已删除 12 条/)).length).toBeGreaterThan(0);
  });

  it('受保护条目不会进入删除请求，并明确显示原因', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/video-library$/, respond: () => jsonResponse(library([
      entry(1),
      entry(2, { deletable: false, delete_blockers: [{ code: 'USED_BY_DELIVERY', message: '仍被一份有效成片引用' }] }),
      entry(3),
    ])) });
    const user = userEvent.setup();
    render(<App />);
    await openLibrary(user);

    await screen.findByText('不可删除：仍被一份有效成片引用');
    await user.click(screen.getByRole('checkbox', { name: /全选本页/ }));
    expect(document.querySelector('.library-selection-note')).toHaveTextContent('其中 1 条可删除，2 条受保护');
    await user.click(screen.getByRole('button', { name: /批量删除（1）/ }));
    const dialog = await screen.findByRole('dialog', { name: '确认批量删除' });
    expect(dialog).toHaveTextContent('选中 3 条，其中 1 条将删除磁盘文件，2 条受保护不会处理');
    expect(dialog).toHaveTextContent('无法删除：');
  });

  it('空库时说明还没有落盘视频，而不是显示空白页', async () => {
    const user = userEvent.setup();
    render(<App />);
    await openLibrary(user);
    expect(await screen.findByText(/这部作品还没有落盘视频/)).toBeInTheDocument();
  });

  it('读取失败时显示错误，不伪装成空库', async () => {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/video-library$/, respond: () => jsonResponse({ detail: 'DATABASE_OFFLINE' }, 500) });
    const user = userEvent.setup();
    render(<App />);
    await openLibrary(user);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('成片库读取失败'), { timeout: 3000 });
    expect(screen.queryByText(/这部作品还没有落盘视频/)).not.toBeInTheDocument();
  });

  it('切换作品后旧库响应不会污染新项目', async () => {
    const projectB = baseProject({ id: 'proj-2', title: '第二个项目' });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([baseProject(), projectB]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2$/, respond: () => jsonResponse(projectB) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/(assets|styles|shots|jobs|film-artifacts|deliveries|delivery-orders|production-series|terminology|subtitles|documents|candidate-assets|voice-profiles|episode-plans|director-packages|production-runs|creative-briefs)$/, respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/events/, respond: () => jsonResponse({ items: [], total: 0, limit: 30, offset: 0 }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/journey$/, respond: () => jsonResponse({
      project_id: 'proj-2', stages: [],
    }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/director-style$/, respond: () => jsonResponse({
      draft_sha256: null, draft_source: null, frozen_source: null,
    }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/rulepack$/, respond: () => jsonResponse({ rulepack_id: null }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-2\/video-library$/, respond: () => jsonResponse({
      project_id: 'proj-2', project_title: '第二个项目', entries: [],
      summary: { total: 0, current: 0, history: 0, pending_review: 0, missing: 0, deletable: 0 },
    }) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/video-library$/, respond: () => jsonResponse(library([entry(1)])) });
    const user = userEvent.setup();
    render(<App />);
    await openLibrary(user);
    await screen.findByText(/磁盘文件：/);
    await user.selectOptions(screen.getByLabelText('快速切换作品'), 'proj-2');
    expect(await screen.findByText(/这部作品还没有落盘视频/)).toBeInTheDocument();
    expect(screen.queryByText(/《雾港夜账_001镜_未分集》\.mp4/)).not.toBeInTheDocument();
  });
});
