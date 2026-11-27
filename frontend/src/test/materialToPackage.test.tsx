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

  it('候选可批量勾选晋升，逐个复用同一晋升执行者', async () => {
    const promoted = vi.fn();
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [{
        run_id: 'run-1', status: 'SUCCEEDED', kind: 'production',
        counts: { total: 2, candidate_ready: 2, failed: 0, needs_review: 0, queued: 0, running: 0 },
        items: [
          { item_id: 'i1', requirement_id: 'r1', entity_id: '沈砚', family: 'character', status: 'CANDIDATE_READY', error_code: null,
            candidates: [{ candidate_id: 'cand-1', status: 'READY_FOR_REVIEW', content_sha256: 'x', is_mock: false, formal_asset_id: null, attempt_no: 1 }] },
          { item_id: 'i2', requirement_id: 'r2', entity_id: '码头', family: 'scene', status: 'CANDIDATE_READY', error_code: null,
            candidates: [{ candidate_id: 'cand-2', status: 'READY_FOR_REVIEW', content_sha256: 'y', is_mock: false, formal_asset_id: null, attempt_no: 1 }] },
        ],
      }] }) });
    stub.add({ method: 'POST', pattern: /preparation\/approval-grants$/, respond: () => jsonResponse({ grant_id: 'g1', snapshot: 's1' }) });
    stub.add({ method: 'POST', pattern: /candidate-assets\/cand-1\/promote$/, respond: () => jsonResponse({ formal_asset_id: 'a1' }) });
    stub.add({ method: 'POST', pattern: /candidate-assets\/cand-2\/promote$/, respond: () => jsonResponse({ formal_asset_id: 'a2' }) });
    render(<MaterialGenerationProgress projectId="p1" onPromoted={promoted} />);
    const user = userEvent.setup();
    await user.click(await screen.findByLabelText('全选候选'));
    await user.click(screen.getByRole('button', { name: '批量晋升所选' }));
    await waitFor(() => expect(stub.callsOf('POST', /candidate-assets\/cand-1\/promote$/)).toHaveLength(1));
    expect(stub.callsOf('POST', /candidate-assets\/cand-2\/promote$/)).toHaveLength(1);
    await waitFor(() => expect(promoted).toHaveBeenCalled());
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
    const advanceButton = await within(rail).findByRole('button', { name: '确认并进入下一步' });
    expect(advanceButton).toBeInTheDocument();
    expect(within(rail).getByRole('button', { name: '重新生成素材' })).toBeInTheDocument();
    await userEvent.setup().click(advanceButton);
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

describe('上传主素材后其他项选 AI 制作不丢失已上传图片', () => {
  it('先上传第 1 项并采用，再点「全部 AI 制作」与「确认决策」时第 1 项保持 reuse_master 并在候选区保留', async () => {
    let uploaded = false;
    let aiQueued = false;
    const commits: Array<Record<string, unknown>> = [];
    stub.add({
      method: 'GET',
      pattern: /projects\/p1\/director\/materials\/requirements$/,
      respond: () => jsonResponse({
        ...REQUIREMENTS,
        requirements: REQUIREMENTS.requirements.map((row) => (
          row.requirement_id === 'r1' && uploaded
            ? { ...row, strategy: 'reuse_master', matched_master: { asset_id: 'uploaded-1', sha256: 'u'.repeat(64) }, status: 'matched' }
            : row
        )),
      }),
    });
    stub.add({
      method: 'GET',
      pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({
        project_id: 'p1',
        runs: aiQueued ? [{
          run_id: 'run-2', status: 'SUCCEEDED', kind: 'production',
          counts: { total: 1, candidate_ready: 1, failed: 0, needs_review: 0, queued: 0, running: 0 },
          items: [{
            item_id: 'i2', requirement_id: 'r2', entity_id: '码头', family: 'scene',
            status: 'CANDIDATE_READY', error_code: null,
            candidates: [{
              candidate_id: 'cand-2', status: 'READY_FOR_REVIEW', content_sha256: 'c'.repeat(64),
              is_mock: false, formal_asset_id: null, attempt_no: 1,
            }],
          }],
        }] : [],
      }),
    });
    stub.add({
      method: 'POST',
      pattern: /projects\/p1\/assets$/,
      respond: () => jsonResponse({ id: 'uploaded-1' }, 201),
    });
    stub.add({
      method: 'POST',
      pattern: /preparation\/approval-grants$/,
      respond: () => jsonResponse({ grant_id: 'g1', snapshot: 's1' }),
    });
    stub.add({
      method: 'POST',
      pattern: /assets\/uploaded-1\/approve$/,
      respond: () => jsonResponse({ id: 'uploaded-1', approved: true }),
    });
    stub.add({
      method: 'POST',
      pattern: /assets\/uploaded-1\/lock$/,
      respond: () => jsonResponse({ id: 'uploaded-1', locked: true }),
    });
    stub.add({
      method: 'POST',
      pattern: /projects\/p1\/director\/materials\/commit$/,
      respond: (_url, init) => {
        const body = JSON.parse(String(init?.body)) as {
          decisions: Array<{ requirement_id: string; strategy: string; asset_id: string | null }>;
          queue_generation: boolean;
        };
        commits.push(body);
        if (body.decisions.some((d) => d.requirement_id === 'r1' && d.strategy === 'reuse_master')) {
          uploaded = true;
        }
        if (body.queue_generation && body.decisions.some((d) => d.requirement_id === 'r2' && d.strategy === 'generate')) {
          aiQueued = true;
        }
        return jsonResponse({ confirmation: {}, generate_count: 1, batch: null });
      },
    });

    const user = userEvent.setup();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);

    await screen.findByText('沈砚');
    const row1 = screen.getByText('沈砚').closest('tr')!;
    const fileInput = row1.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['png-bytes'], 'shenyan.png', { type: 'image/png' });
    await user.upload(fileInput, file);

    // 上传后第 1 项决定必须自动同步为 reuse_master，并在候选区显示「已采用」
    await waitFor(() => {
      expect((screen.getByLabelText('处理方式：沈砚') as HTMLSelectElement).value).toBe('reuse_master');
    });
    expect(await screen.findByAltText('沈砚 已采用')).toBeInTheDocument();

    // 再点「全部 AI 制作」并确认提交：只对未采用的「码头」排队 AI 制作，不得把「沈砚」重置掉
    await user.click(screen.getByRole('button', { name: '全部 AI 制作' }));
    expect((screen.getByLabelText('处理方式：沈砚') as HTMLSelectElement).value).toBe('reuse_master');
    expect((screen.getByLabelText('处理方式：码头') as HTMLSelectElement).value).toBe('generate');

    const rail = screen.getByLabelText('本阶段操作');
    await user.click(within(rail).getByRole('button', { name: '确认决策并开始准备素材' }));

    await waitFor(() => expect(commits).toHaveLength(2));
    const secondCommit = commits[1] as {
      decisions: Array<{ requirement_id: string; strategy: string; asset_id: string | null }>;
    };
    expect(secondCommit.decisions).toEqual(expect.arrayContaining([
      expect.objectContaining({ requirement_id: 'r1', strategy: 'reuse_master', asset_id: 'uploaded-1' }),
      expect.objectContaining({ requirement_id: 'r2', strategy: 'generate', asset_id: null }),
    ]));

    // 候选区同时可见已上传的主素材与 AI 生成的候选图
    expect(await screen.findByAltText('沈砚 已采用')).toBeInTheDocument();
    expect(await screen.findByAltText('码头 候选 1')).toBeInTheDocument();
  });

  it('重复上传已批准并锁定的同一张图片时，不重复调用 approve/lock 报 409，直接回绑并刷新候选区', async () => {
    let adopted = false;
    stub.add({
      method: 'GET',
      pattern: /projects\/p1\/director\/materials\/requirements$/,
      respond: () => jsonResponse({
        ...REQUIREMENTS,
        requirements: REQUIREMENTS.requirements.map((row) => (
          row.requirement_id === 'r1' && adopted
            ? { ...row, strategy: 'reuse_master', matched_master: { asset_id: 'dup-asset-1', sha256: 'd'.repeat(64) }, status: 'matched' }
            : row
        )),
      }),
    });
    stub.add({
      method: 'GET',
      pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [] }),
    });
    stub.add({
      method: 'POST',
      pattern: /projects\/p1\/assets$/,
      respond: () => jsonResponse({
        id: 'dup-asset-1', role: 'character', entity_id: '沈砚',
        approved: true, locked: true, filename: 'shenyan.png',
      }, 201),
    });
    stub.add({
      method: 'POST',
      pattern: /projects\/p1\/director\/materials\/commit$/,
      respond: () => {
        adopted = true;
        return jsonResponse({ confirmation: {}, generate_count: 0, batch: null });
      },
    });

    const user = userEvent.setup();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);

    await screen.findByText('沈砚');
    const row1 = screen.getByText('沈砚').closest('tr')!;
    const fileInput = row1.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, new File(['same'], 'shenyan.png', { type: 'image/png' }));

    expect(await screen.findByAltText('沈砚 已采用')).toBeInTheDocument();
    expect(stub.callsOf('POST', /approve$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /lock$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /director\/materials\/commit$/)).toHaveLength(1);
    expect((screen.getByLabelText('处理方式：沈砚') as HTMLSelectElement).value).toBe('reuse_master');
  });

  it('在素材页上传或确认进入下一步时同步触发 onJourneyRefresh，保证导演包阶段读取同一最新事实', async () => {
    const commitBodies: Array<{ queue_generation: boolean }> = [];
    stubMaterials();
    stub.add({
      method: 'POST',
      pattern: /projects\/p1\/assets$/,
      respond: () => jsonResponse({
        id: 'asset-sync-1', role: 'character', entity_id: '沈砚',
        approved: true, locked: true, filename: 'shenyan-sync.png',
      }, 201),
    });
    stub.add({
      method: 'POST',
      pattern: /projects\/p1\/director\/materials\/commit$/,
      respond: (_url, init) => {
        commitBodies.push(JSON.parse(String(init?.body || '{}')) as { queue_generation: boolean });
        return jsonResponse({ confirmation: {}, generate_count: 0, batch: null });
      },
    });

    const user = userEvent.setup();
    const refresh = vi.fn();
    const advance = vi.fn();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" assetsDone onJourneyRefresh={refresh} onAdvance={advance} />
    </StageRailProvider>);

    await screen.findByText('沈砚');
    const row1 = screen.getByText('沈砚').closest('tr')!;
    const fileInput = row1.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, new File(['sync'], 'shenyan-sync.png', { type: 'image/png' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));

    const rail = screen.getByLabelText('本阶段操作');
    await user.click(within(rail).getByRole('button', { name: '确认并进入下一步' }));
    await waitFor(() => expect(advance).toHaveBeenCalledTimes(1));
    expect(refresh).toHaveBeenCalledTimes(2);
    expect(commitBodies[commitBodies.length - 1]?.queue_generation).toBe(false);
  });
});

describe('④ 素材来源从素材中心导入与生成中锁定', () => {
  it('来源下拉「从素材中心导入」弹窗选素材并走分配+采纳执行者', async () => {
    stubMaterials();
    stub.add({ method: 'GET', pattern: /projects\/p1\/asset-center\/search/,
      respond: () => jsonResponse({ items: [{ asset: { id: 'la1', title: '沈砚 · 长风衣', category: 'character', kind: 'image' },
        version: { id: 'lv1', ordinal: 1, content_sha256: 'e'.repeat(64), media_url: '/api/v1/asset-center/versions/lv1/media' } }] }) });
    stub.add({ method: 'POST', pattern: /asset-center\/assignments$/, respond: () => jsonResponse({ project_asset_id: 'pa-1' }) });
    stub.add({ method: 'POST', pattern: /projects\/p1\/director\/materials\/commit$/, respond: () => jsonResponse({ replayed: false, generate_count: 0 }) });
    const user = userEvent.setup();
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);
    await screen.findByText('沈砚');
    const rows = screen.getAllByLabelText(/处理方式：/) as HTMLSelectElement[];
    await user.selectOptions(rows[0], 'import_center');
    const dialog = await screen.findByRole('dialog', { name: /从素材中心导入 · 沈砚/ });
    expect(dialog).toBeInTheDocument();
    await user.click(await screen.findByRole('option', { name: /沈砚 · 长风衣/ }));
    await waitFor(() => expect(stub.callsOf('POST', /asset-center\/assignments$/)).toHaveLength(1));
    expect(stub.callsOf('POST', /asset-center\/assignments$/)[0].body).toMatchObject({
      project_id: 'p1', library_version_id: 'lv1', role: 'character', entity_id: '沈砚' });
    await waitFor(() => expect(stub.callsOf('POST', /projects\/p1\/director\/materials\/commit$/)).toHaveLength(1));
  });

  it('后台在途出图时主操作锁定为「素材生成中…」', async () => {
    stub.add({ method: 'GET', pattern: /projects\/p1\/director\/materials\/requirements$/, respond: () => jsonResponse(REQUIREMENTS) });
    stub.add({ method: 'GET', pattern: /projects\/p1\/material-preparation\/generation-progress$/,
      respond: () => jsonResponse({ project_id: 'p1', runs: [{ run_id: 'run-1',
        counts: { total: 2, candidate_ready: 0, failed: 0, needs_review: 0, queued: 1, running: 1 }, items: [] }] }) });
    render(<StageRailProvider stageId="assets" stageLabel="第④步 · 拍摄素材准备">
      <ShotMaterialsPanel projectId="p1" />
    </StageRailProvider>);
    const btn = await screen.findByRole('button', { name: /素材生成中/ });
    expect(btn).toBeDisabled();
  });
});
