/** 素材准备 → 导演包唯一链路回归（2026-11-16 用户报障四项）。
 *
 *  1. 需求列表有一键批量勾选处理方式；
 *  2. 进度条挂载与素材页同一份生成台账（public_run），不是第二套判定；
 *  3. 素材页的「下一步」由服务端 journey.next_stage 驱动，不在前端硬编码就绪。
 *  缺陷2（上传后清单塌缩）的反例在后端 test_director_material_commit.py。 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { ShotMaterialsPanel } from '../components/StreamlinePanels';
import { MaterialGenerationProgress } from '../components/MaterialGenerationProgress';
import { StageRailProvider } from '../components/StageRail';
import { StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  window.history.replaceState(null, '', '#');
  stub = new StudioStub([]);
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

const REQUIREMENTS = {
  plan_revision_id: 'plan-1',
  expected_snapshot: 'a'.repeat(64),
  requirements: [
    { requirement_id: 'r1', family: 'character', entity_id: '沈砚', name: '沈砚',
      description: ' pale young man', critical: true, optional: false, variants: [],
      used_shot_ids: ['s1'], used_shot_count: 1, used_episodes: ['ep-01'],
      matched_master: null, status: 'missing' },
    { requirement_id: 'r2', family: 'scene', entity_id: '码头', name: '码头',
      description: 'rainy dock', critical: false, optional: true, variants: [],
      used_shot_ids: ['s1'], used_shot_count: 1, used_episodes: ['ep-01'],
      matched_master: null, status: 'missing' },
  ],
};

function stubMaterials() {
  stub.add({ method: 'GET', pattern: /projects\/p1\/director\/materials\/requirements$/,
    respond: () => jsonResponse(REQUIREMENTS) });
  stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
    respond: () => jsonResponse({ project_id: 'p1', runs: [] }) });
}

describe('素材需求批量处理方式', () => {
  it('一键「全部 AI 制作」把可选项也设为制作', async () => {
    stubMaterials();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);
    await screen.findByText('沈砚');
    // 默认：必需项制作、可选项跳过。
    const rows = screen.getAllByLabelText(/处理方式：/) as HTMLSelectElement[];
    expect(rows.map((row) => row.value)).toEqual(['generate', 'skip']);

    await userEvent.setup().click(screen.getByRole('button', { name: '全部 AI 制作' }));
    expect((screen.getAllByLabelText(/处理方式：/) as HTMLSelectElement[]).map((row) => row.value))
      .toEqual(['generate', 'generate']);
  });

  it('批量应用到所选：缺主素材的「复用」按事实降级为制作', async () => {
    stubMaterials();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);
    await screen.findByText('沈砚');
    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: '选择 沈砚' }));
    expect(screen.getByText('已选 1 项')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('批量处理方式'), 'reuse_master');
    await user.click(screen.getByRole('button', { name: '应用到所选' }));
    // 沈砚没有主素材，复用不可用 → 降级为 AI 制作（与逐项下拉同一份 decisions）。
    expect((screen.getByLabelText('处理方式：沈砚') as HTMLSelectElement).value).toBe('generate');
  });
});

describe('素材生成进度（与素材页同一台账）', () => {
  it('在途批次显示进度条与正在处理的条目', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [{
        run_id: 'run-1', status: 'RUNNING', kind: 'production',
        counts: { total: 3, candidate_ready: 1, failed: 0, needs_review: 0, queued: 1, running: 1 },
        items: [
          { item_id: 'i1', entity_id: '沈砚', family: 'character', status: 'RUNNING', error_code: null },
          { item_id: 'i2', entity_id: '老陈', family: 'character', status: 'CANDIDATE_READY', error_code: null },
          { item_id: 'i3', entity_id: '码头', family: 'scene', status: 'QUEUED', error_code: null },
        ],
      }] }) });
    render(<MaterialGenerationProgress projectId="p1" />);
    const bar = await screen.findByRole('progressbar', { name: '素材生成进度' });
    expect(bar).toHaveAttribute('aria-valuenow', '33');
    expect(screen.getByText(/后台正在生成素材/)).toBeInTheDocument();
    expect(screen.getByText(/候选 1\/3/)).toBeInTheDocument();
    expect(screen.getByText(/正在处理：沈砚（渲染中）/)).toBeInTheDocument();
  });

  it('没有批次时不占位（不谎称有任务）', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [] }) });
    const { container } = render(<MaterialGenerationProgress projectId="p1" />);
    await waitFor(() => expect(container.querySelector('.material-generation-progress')).toBeNull());
  });

  it('唯一选定面：已采用主素材与待选候选同列网格，无批次时也显示', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [] }) });
    render(<MaterialGenerationProgress projectId="p1" onPromoted={() => {}}
      masters={[{ requirement_id: 'r1', entity_id: '沈砚', family: 'character', asset_id: 'asset-1' }]} />);
    expect(await screen.findByText(/选定并采用/)).toBeInTheDocument();
    expect(screen.getAllByText(/已采用/).length).toBeGreaterThan(0);
  });
});

describe('勾选后统一重新生成（导演④页）', () => {
  it('「重新生成所选」只作用于勾选集，复用唯一 regenerate-all 执行者', async () => {
    stubMaterials();
    stub.add({ method: 'POST', pattern: /projects\/p1\/material-preparation\/regenerate-all$/,
      respond: () => jsonResponse({ items: ['item-9'], run_id: 'run-9' }) });
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);
    await screen.findByText('沈砚');
    const user = userEvent.setup();
    await user.click(screen.getByRole('checkbox', { name: '选择 沈砚' }));
    await user.click(screen.getByRole('button', { name: /重新生成所选（1）/ }));
    await waitFor(() => expect(stub.callsOf('POST', /regenerate-all$/)).toHaveLength(1));
    expect(stub.callsOf('POST', /regenerate-all$/)[0].body).toMatchObject({ requirement_ids: ['r1'] });
    expect(stub.callsOf('POST', /director\/materials\/commit$/)).toHaveLength(0);
  });
});

describe('素材页下一步由 journey 驱动', () => {
  it('分镜与素材已核对后，素材页给出「下一步：导演包」', async () => {
    window.history.replaceState(null, '', '#d/materials');
    const ready = {
      project_id: 'proj-1',
      stages: [
        { id: 'script', label: '创意与剧本', done: true, summary: '剧本已确认', blockers: [], facts: {} },
        { id: 'plan', label: '剧本统筹', done: true, summary: '分集已确认', blockers: [], facts: {} },
        { id: 'storyboard', label: '镜头拆解', done: true, summary: '分镜已定稿', blockers: [], facts: { shot_count: 6 } },
        { id: 'assets', label: '拍摄素材准备', done: true, summary: '6 镜的素材需求已核对', blockers: [], facts: { material_missing_count: 0 } },
        { id: 'package', label: '导演包', done: false, summary: '尚未生成导演包', blockers: [], facts: {} },
        { id: 'production', label: '视频生产', done: false, summary: '等待导演包', blockers: [], facts: {} },
        { id: 'post', label: '后期成片', done: false, summary: '还没有整部作品运行', blockers: [], facts: {} },
      ],
      next_stage: 'package', package: null, episode_plan: null, run: null,
    };
    stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '雾港夜账' })));
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/, respond: () => jsonResponse(ready) });
    render(<App />);
    const next = await screen.findByRole('button', { name: '下一步：导演包' });
    expect(next).toBeInTheDocument();
  });
});

describe('素材准备页只保留按镜头需求的唯一面板（2026-11-16 用户裁定）', () => {
  it('移除大纲候选批次板块、重叠上传板块与「打开素材中心」引导', async () => {
    window.history.replaceState(null, '', '#d/materials');
    const ready = {
      project_id: 'proj-1',
      stages: [
        { id: 'script', label: '创意与剧本', done: true, summary: '', blockers: [], facts: {} },
        { id: 'plan', label: '剧本统筹', done: true, summary: '', blockers: [], facts: {} },
        { id: 'storyboard', label: '镜头拆解', done: true, summary: '', blockers: [], facts: { shot_count: 6 } },
        { id: 'assets', label: '拍摄素材准备', done: false, summary: '6 镜的素材待准备', blockers: [], facts: { material_missing_count: 2 } },
        { id: 'package', label: '导演包', done: false, summary: '', blockers: [], facts: {} },
        { id: 'production', label: '视频生产', done: false, summary: '', blockers: [], facts: {} },
        { id: 'post', label: '后期成片', done: false, summary: '', blockers: [], facts: {} },
      ],
      next_stage: 'assets', package: null, episode_plan: null, run: null,
    };
    stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '雾港夜账' })));
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/journey$/, respond: () => jsonResponse(ready) });
    stub.add({ method: 'GET', pattern: /projects\/proj-1\/director\/materials\/requirements$/,
      respond: () => jsonResponse({ plan_revision_id: 'plan-1', expected_snapshot: 'a'.repeat(64), requirements: [] }) });
    stub.add({ method: 'GET', pattern: /projects\/proj-1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'proj-1', runs: [] }) });
    render(<App />);
    await screen.findByRole('heading', { name: '镜头要什么，就准备什么' });
    expect(screen.queryByText(/按场次分析剧本与候选批次/)).toBeNull();
    expect(screen.queryByText('上传与确认项目素材')).toBeNull();
    expect(screen.queryByRole('button', { name: '打开素材中心' })).toBeNull();
    // 素材未准备确认时，旅程停在素材准备，不提示「下一步：导演包」。
    expect(screen.queryByRole('button', { name: '下一步：导演包' })).toBeNull();
  });
});

describe('素材准备页内候选选定与晋升（不引去素材中心）', () => {
  it('候选就绪时本页展示「选用并晋升」，复用同一晋升执行者并回调刷新', async () => {
    const promoted = vi.fn();
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [{
        run_id: 'run-1', status: 'SUCCEEDED', kind: 'production',
        counts: { total: 1, candidate_ready: 1, failed: 0, needs_review: 0, queued: 0, running: 0 },
        items: [{ item_id: 'i1', requirement_id: 'r1', entity_id: '沈砚', family: 'character',
          status: 'CANDIDATE_READY', error_code: null,
          candidates: [{ candidate_id: 'cand-1', status: 'READY_FOR_REVIEW', content_sha256: 'x',
            is_mock: false, formal_asset_id: null, attempt_no: 1 }] }],
      }] }) });
    stub.add({ method: 'POST', pattern: /preparation\/approval-grants$/,
      respond: () => jsonResponse({ grant_id: 'g1', snapshot: 's1' }) });
    stub.add({ method: 'POST', pattern: /candidate-assets\/cand-1\/promote$/,
      respond: () => jsonResponse({ formal_asset_id: 'asset-new' }) });
    render(<MaterialGenerationProgress projectId="p1" onPromoted={promoted} />);
    const button = await screen.findByRole('button', { name: '选用并晋升' });
    expect(screen.queryByText(/去素材中心/)).toBeNull();
    expect(screen.getByAltText('沈砚 候选 1')).toHaveAttribute('src', '/api/v1/candidate-assets/cand-1/thumbnail');
    await userEvent.setup().click(button);
    await waitFor(() => expect(promoted).toHaveBeenCalledTimes(1));
    expect(stub.callsOf('POST', /preparation\/approval-grants$/)[0].body)
      .toMatchObject({ action: 'promote', target_id: 'cand-1' });
    expect(stub.callsOf('POST', /candidate-assets\/cand-1\/promote$/)[0].body)
      .toMatchObject({ grant_id: 'g1', snapshot: 's1', offline: false });
  });

  it('已采用的主素材可「取消采用」，回调同一决策执行者', async () => {
    const unadopt = vi.fn();
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [] }) });
    render(<MaterialGenerationProgress projectId="p1" onPromoted={() => {}} onUnadopt={unadopt}
      masters={[{ requirement_id: 'r1', entity_id: '沈砚', family: 'character', asset_id: 'asset-1' }]} />);
    const button = await screen.findByRole('button', { name: '取消采用' });
    await userEvent.setup().click(button);
    await waitFor(() => expect(unadopt).toHaveBeenCalledWith('r1'));
  });
});

describe('取消采用不触发新批次（导演④页）', () => {
  it('已采用主素材点「取消采用」＝改回生成且 queue_generation:false', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/materials\/requirements$/,
      respond: () => jsonResponse({ ...REQUIREMENTS, requirements: [
        { ...REQUIREMENTS.requirements[0], strategy: 'reuse_master',
          matched_master: { asset_id: 'asset-1', sha256: 'x' }, status: 'matched' },
      ] }) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [] }) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/materials\/commit$/,
      respond: () => jsonResponse({ confirmation: {}, bound_shots: 1 }) });
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);
    const button = await screen.findByRole('button', { name: '取消采用' });
    await userEvent.setup().click(button);
    await waitFor(() => expect(stub.callsOf('POST', /director\/materials\/commit$/)).toHaveLength(1));
    const body = stub.callsOf('POST', /director\/materials\/commit$/)[0].body as {
      queue_generation: boolean; decisions: Array<{ strategy: string; asset_id: null }> };
    expect(body.queue_generation).toBe(false);
    expect(body.decisions[0]).toMatchObject({ strategy: 'generate', asset_id: null });
    expect(stub.callsOf('POST', /regenerate-all$/)).toHaveLength(0);
  });
});

describe('素材就绪后主操作进入下一步（journey 单一裁决）', () => {
  it('assetsDone 时主操作「确认并进入下一步」+ 次操作「重新生成素材」', async () => {
    stubMaterials();
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/materials\/commit$/,
      respond: () => jsonResponse({ confirmation: {}, generate_count: 0, batch: null }) });
    const advance = vi.fn();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" assetsDone onAdvance={advance} />
    </StageRailProvider>);
    await screen.findByText('沈砚');
    const rail = screen.getByLabelText('本阶段操作');
    expect(within(rail).getByRole('button', { name: '确认并进入下一步' })).toBeInTheDocument();
    expect(within(rail).getByRole('button', { name: '重新生成素材' })).toBeInTheDocument();
    await userEvent.setup().click(within(rail).getByRole('button', { name: '确认并进入下一步' }));
    await waitFor(() => expect(advance).toHaveBeenCalledTimes(1));
  });

  it('未完成时主操作仍是「确认决策并开始准备素材」', async () => {
    stubMaterials();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);
    await screen.findByText('沈砚');
    const rail = screen.getByLabelText('本阶段操作');
    // 轨按钮经 passive effect 注册：等按钮本身出现，而不是在行渲染后同步断言。
    expect(await within(rail).findByRole('button', { name: '确认决策并开始准备素材' })).toBeInTheDocument();
  });
});