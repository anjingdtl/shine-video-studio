/** 素材准备 → 导演包唯一链路回归（2026-11-16 用户报障四项）。
 *
 *  1. 需求列表有一键批量勾选处理方式；
 *  2. 进度条挂载与素材页同一份生成台账（public_run），不是第二套判定；
 *  3. 素材页的「下一步」由服务端 journey.next_stage 驱动，不在前端硬编码就绪。
 *  缺陷2（上传后清单塌缩）的反例在后端 test_director_material_commit.py。 */
import { render, screen, waitFor } from '@testing-library/react';
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