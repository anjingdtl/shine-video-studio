import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { StageRailProvider } from '../components/StageRail';
import { StoryboardBookPanel } from '../components/StreamlinePanels';
import { DEFAULT_MAX_SHOT_SECONDS } from '../constants';
import { StudioStub, jsonResponse } from './studioStub';

afterEach(() => vi.unstubAllGlobals());

const BOOK = {
  plan_version: 1,
  plan_sha256: 'e'.repeat(64),
  preview: {
    mode: 'sections', target_seconds: 60, tolerance_seconds: 15,
    episode_count: 1, total_planned_seconds: 60,
    episodes: [{
      episode_id: 'e1', order: 1, title: '第 1 集', planned_seconds: 60,
      deviation_seconds: 0, boundary_note: null, section_count: 1, sections: [],
    }],
  },
};

it('对白与旁白设定随分镜书一起提交，两项都进入冻结风格', async () => {
  const confirms: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'noir', name_zh: '黑色电影', summary_zh: '低照度、硬光', version: '1.0' },
    ]) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/director\/storyboard-book$/, respond: () => jsonResponse(BOOK) },
    { method: 'POST', pattern: /\/director\/storyboard-book\/confirm$/, respond: (_url, init) => {
      confirms.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ plan_version: 1, style: { style_version_id: 'style-1' } });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);

  await screen.findByRole('option', { name: /电影专业 Skills 自动/ });
  await user.click(screen.getByRole('button', { name: '多对白' }));
  await user.click(screen.getByRole('button', { name: '多旁白' }));
  await user.click(screen.getByRole('button', { name: /生成分镜书/ }));
  await screen.findByText(/计划总时长/);
  await user.click(screen.getByRole('button', { name: /确认分镜书/ }));

  await waitFor(() => expect(confirms).toHaveLength(1));
  expect(confirms[0].style).toMatchObject({
    profile_id: '',
    dialogue_ratio_percent: 70,
    narration_level: 'high',
  });
  stub.restore();
});

it('单镜秒数上限随分镜书确认进入冻结风格（同一控制链路）', async () => {
  const confirms: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'noir', name_zh: '黑色电影', summary_zh: '低照度、硬光', version: '1.0' },
    ]) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/director\/storyboard-book$/, respond: () => jsonResponse(BOOK) },
    { method: 'POST', pattern: /\/director\/storyboard-book\/confirm$/, respond: (_url, init) => {
      confirms.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ plan_version: 1, style: { style_version_id: 'style-1' } });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);
  await screen.findByRole('option', { name: /电影专业 Skills 自动/ });
  fireEvent.change(screen.getByLabelText('单镜秒数上限'), { target: { value: '10' } });
  await user.click(screen.getByRole('button', { name: /生成分镜书/ }));
  await screen.findByText(/计划总时长/);
  await user.click(screen.getByRole('button', { name: /确认分镜书/ }));
  await waitFor(() => expect(confirms).toHaveLength(1));
  expect(confirms[0].style).toMatchObject({ profile_id: '', max_shot_seconds: 10 });
  stub.restore();
});

it('切页回来读回已持久化的决定，而不是硬编码默认值（三原则）', async () => {
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'wong-kar-wai', name_zh: '王家卫参考风格', summary_zh: '城市情绪', version: '1.0.0' },
      { id: 'mamoru-oshii', name_zh: '押井守动画电影参考风格', summary_zh: '建筑空间', version: '1.0.0' },
    ]) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([
      { version: 1, status: 'CONFIRMED', target_seconds: 60,
        payload: { target_seconds: 60, episodes: [{ episode_id: 'e1' }] } },
    ]) },
    { method: 'GET', pattern: /\/projects\/proj-1\/director-style$/, respond: () => jsonResponse({
      style_draft: { film_type: 'animation', dialogue_ratio_percent: 20,
        narration_level: 'low', max_shot_seconds: 10 },
      draft_source: { profile_id: 'mamoru-oshii' },
      frozen_source: { profile_id: 'mamoru-oshii' },
    }) },
  ]);
  const { unmount } = render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);
  await screen.findByRole('option', { name: /押井守/ });
  await waitFor(() => {
    expect((screen.getByLabelText('导演风格') as HTMLSelectElement).value).toBe('mamoru-oshii');
    expect((screen.getByLabelText('影片类型') as HTMLSelectElement).value).toBe('animation');
    expect((screen.getByLabelText('集数') as HTMLInputElement).value).toBe('1');
    expect((screen.getByLabelText('每集目标秒数') as HTMLInputElement).value).toBe('60');
    expect((screen.getByLabelText('单镜秒数上限') as HTMLInputElement).value).toBe('10');
  });
  expect(screen.getByRole('button', { name: '少对白' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: '少旁白' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByText(/第 1 版已确认/)).toBeInTheDocument();
  // 模拟切走再回来：重新挂载仍读同一份服务端事实。
  unmount();
  render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);
  await waitFor(() => {
    expect((screen.getByLabelText('导演风格') as HTMLSelectElement).value).toBe('mamoru-oshii');
    expect((screen.getByLabelText('单镜秒数上限') as HTMLInputElement).value).toBe('10');
  });
  stub.restore();
});

it('确认后改影片类型：经原确认入口重新冻结，而不是选了不生效', async () => {
  const confirms: Array<Record<string, unknown>> = [];
  const generates: unknown[] = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'noir', name_zh: '黑色电影', summary_zh: '低照度、硬光', version: '1.0' },
    ]) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([
      { version: 1, status: 'CONFIRMED', sha256: 'e'.repeat(64), target_seconds: 60,
        payload: { target_seconds: 60, episodes: [{ episode_id: 'e1' }] } },
    ]) },
    { method: 'GET', pattern: /\/projects\/proj-1\/director-style$/, respond: () => jsonResponse({
      style_draft: { film_type: 'live_action', dialogue_ratio_percent: 45,
        narration_level: 'none', max_shot_seconds: 15 },
      draft_source: { profile_id: 'noir' },
      frozen_source: { profile_id: 'noir' },
    }) },
    { method: 'POST', pattern: /\/director\/storyboard-book$/, respond: (_url, init) => {
      generates.push(init); return jsonResponse(BOOK);
    } },
    { method: 'POST', pattern: /\/director\/storyboard-book\/confirm$/, respond: (_url, init) => {
      confirms.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse({ plan_version: 1, style: { style_version_id: 'style-2', film_type: 'animation' } });
    } },
  ]);
  const user = userEvent.setup();
  render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);
  await screen.findByText(/第 1 版已确认/);
  await waitFor(() => {
    expect((screen.getByLabelText('影片类型') as HTMLSelectElement).value).toBe('live_action');
  });
  // 未改选择时不出现重新确认入口。
  expect(screen.queryByRole('button', { name: /重新确认分镜书/ })).toBeNull();
  fireEvent.change(screen.getByLabelText('影片类型'), { target: { value: 'animation' } });
  await screen.findByText(/风格选择已修改/);
  await user.click(screen.getByRole('button', { name: /重新确认分镜书（冻结新风格）/ }));
  await waitFor(() => expect(confirms).toHaveLength(1));
  expect(confirms[0]).toMatchObject({
    plan_version: 1,
    expected_sha256: 'e'.repeat(64),
    style: { profile_id: 'noir', film_type: 'animation' },
  });
  expect(generates).toHaveLength(0);
  stub.restore();
});

it('页面操作栏的生成分镜书携带当前集数与秒数，而不是注册时的旧值', async () => {
  const bodies: Array<Record<string, unknown>> = [];
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'noir', name_zh: '黑色电影', summary_zh: '低照度、硬光', version: '1.0' },
    ]) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse([]) },
    { method: 'POST', pattern: /\/director\/storyboard-book$/, respond: (_url, init) => {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return jsonResponse(BOOK);
    } },
  ]);
  const user = userEvent.setup();
  render(
    <StageRailProvider stageId="plan" stageLabel="第②步 · 剧本统筹">
      <StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />
    </StageRailProvider>,
  );
  await screen.findByRole('option', { name: /黑色电影/ });
  fireEvent.change(screen.getByLabelText('集数'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('每集目标秒数'), { target: { value: '45' } });
  // On the director page the rail is the primary surface: its action must read
  // the current inputs, not the closure captured when the rail first registered.
  const rail = screen.getByLabelText('本阶段操作');
  await user.click(within(rail).getByRole('button', { name: '生成分镜书' }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  // A new book starts from the default of the person's own per-shot ceiling.
  // The number is the backend's schemas.DEFAULT_MAX_SHOT_SECONDS: one setting,
  // the same first value on both sides.
  expect(DEFAULT_MAX_SHOT_SECONDS).toBe(10);
  expect(bodies[0]).toMatchObject({
    episode_count: 3, target_seconds: 45, tolerance_seconds: 11, max_shot_seconds: DEFAULT_MAX_SHOT_SECONDS,
  });
  expect((screen.getByLabelText('单镜秒数上限') as HTMLInputElement).value).toBe('10');
  // The range of the setting itself is H3's own: a person may still choose 15.
  expect(screen.getByLabelText('单镜秒数上限')).toHaveAttribute('max', '15');
  expect(screen.getByLabelText('单镜秒数上限')).toHaveAttribute('min', '4');
  stub.restore();
});

it('大纲超出设定集数时，显示当前覆盖章节、后续预计集数，并在重挂载后从服务端读回预览', async () => {
  const scopedBook = {
    plan_version: 2,
    plan_sha256: 'f'.repeat(64),
    status: 'DRAFT',
    preview: {
      mode: 'sections',
      target_seconds: 60,
      tolerance_seconds: 15,
      max_shot_seconds: 10,
      episode_count: 2,
      total_planned_seconds: 120,
      episodes: [
        {
          episode_id: 'ep-01', order: 1, title: '第 1 集', planned_seconds: 60,
          deviation_seconds: 0, boundary_note: null, section_count: 2, sections: [],
        },
        {
          episode_id: 'ep-02', order: 2, title: '第 2 集', planned_seconds: 60,
          deviation_seconds: 0, boundary_note: null, section_count: 2, sections: [],
        },
      ],
      covered_scope: {
        covered_section_count: 4,
        total_section_count: 10,
        from_order: 1,
        to_order: 4,
        from_title: '雨夜钟表铺',
        to_title: '失踪的怀表',
        covered_seconds: 120,
        total_outline_seconds: 300,
        coverage_percent: 40,
        note: '本次 2 集覆盖大纲第 1 场「雨夜钟表铺」至第 4 场「失踪的怀表」（共 4/10 场，原文预计约 120 / 300 秒，占 40%）。',
      },
      out_of_scope: {
        count: 6,
        from_order: 5,
        to_order: 10,
        from_title: '阁楼暗格',
        to_title: '黎明重逢',
        remaining_seconds: 180,
        estimated_remaining_episodes: 3,
        estimated_total_episodes: 5,
        note: '当前 2 集覆盖至第 4 场「失踪的怀表」；后续另有 6 场不在本次 2 集范围内（第 5 场「阁楼暗格」至第 10 场「黎明重逢」，预计约 180 秒）；按每集 60 秒预计后续还需约 3 集（全剧共约 5 集），可先拍本次计划，后续再由你决定是否继续或调整集数。',
      },
    },
  };
  let savedBook: typeof scopedBook | null = null;
  const stub = new StudioStub([
    { method: 'GET', pattern: /\/director-styles$/, respond: () => jsonResponse([
      { id: 'noir', name_zh: '黑色电影', summary_zh: '低照度、硬光', version: '1.0' },
    ]) },
    { method: 'GET', pattern: /\/episode-plans$/, respond: () => jsonResponse(savedBook ? [{
      version: 2, status: 'DRAFT', target_seconds: 60,
      payload: { target_seconds: 60, episodes: [{ episode_id: 'ep-01' }, { episode_id: 'ep-02' }], source: { max_shot_seconds: 10 } },
    }] : []) },
    { method: 'GET', pattern: /\/director\/storyboard-book$/, respond: () => jsonResponse(savedBook) },
    { method: 'POST', pattern: /\/director\/storyboard-book$/, respond: () => {
      savedBook = scopedBook;
      return jsonResponse(scopedBook, 201);
    } },
  ]);
  const user = userEvent.setup();
  const { unmount } = render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);
  await screen.findByRole('option', { name: /黑色电影/ });
  fireEvent.change(screen.getByLabelText('单镜秒数上限'), { target: { value: '10' } });
  await user.click(screen.getByRole('button', { name: /生成分镜书/ }));

  expect(await screen.findByText(/单镜上限 10 秒/)).toBeInTheDocument();
  expect(screen.getByText(/本次 2 集覆盖大纲第 1 场「雨夜钟表铺」至第 4 场「失踪的怀表」/)).toBeInTheDocument();
  expect(screen.getByText(/按每集 60 秒预计后续还需约 3 集（全剧共约 5 集）/)).toBeInTheDocument();

  // 切页回来重新挂载：直接从服务端读回已生成分镜书与覆盖范围，不丢失预览。
  unmount();
  render(<StoryboardBookPanel projectId="proj-1" onMessage={vi.fn()} />);
  expect(await screen.findByText(/按每集 60 秒预计后续还需约 3 集（全剧共约 5 集）/)).toBeInTheDocument();
  stub.restore();
});
