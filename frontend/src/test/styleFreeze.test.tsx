/** 归一化后的第 2 阶段风格冻结（三原则：一套事实、一个裁决、一个执行者）：
 *  - 剧本统筹页只保留一个「第 2 阶段 · 分镜书」入口，「逐项微调七个风格维度（高级）」
 *    直接折叠在其内部，不再保留第二套「导演风格/影片类型/采用到草稿/批准并冻结」。
 *  - 屏幕上的七维微调与影片类型通过同一个 confirmStoryboardBook 执行者一次性冻结。
 *  - 失败或刷新时保留用户选择；已确认后改七维或影片类型也通过同一个重新确认入口冻结。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { STYLE_DIMENSION_OPTIONS } from '../styleCatalog';
import { DIRECTOR_STYLE_CATALOG, FROZEN_STYLE, StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

const BOOK = {
  plan_version: 1,
  plan_sha256: 'e'.repeat(64),
  status: 'DRAFT',
  preview: {
    mode: 'sections',
    target_seconds: 60,
    tolerance_seconds: 15,
    max_shot_seconds: 15,
    episode_count: 2,
    total_planned_seconds: 120,
    episodes: [
      {
        episode_id: 'ep-01', order: 1, title: '第 1 集', planned_seconds: 60,
        deviation_seconds: 0, boundary_note: null, section_count: 1, sections: [],
      },
      {
        episode_id: 'ep-02', order: 2, title: '第 2 集', planned_seconds: 60,
        deviation_seconds: 0, boundary_note: null, section_count: 1, sections: [],
      },
    ],
  },
};

const TEMPLATE_BIBLE = {
  photography_language: '固定中景，机位平视。',
  shot_blocking: '正面构图，保持轴线。',
  color_palette: '冷青与暖灯对比。',
  lighting_tone: '实景低调光影。',
  lens_texture: '35mm 胶片颗粒。',
  editing_rhythm: '舒缓情绪剪辑。',
  sound_performance: '真实环境音与克制对白。',
};

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
  stub.add({
    method: 'GET',
    pattern: /\/api\/v1\/director-styles\/[^/]+$/,
    respond: () => jsonResponse({
      ...DIRECTOR_STYLE_CATALOG[0],
      profile: {
        id: 'wong-kar-wai',
        name_zh: '王家卫参考风格',
        version: '1.0.0',
        summary_zh: '城市情绪',
        style_bible: TEMPLATE_BIBLE,
      },
    }),
  });
});

afterEach(() => {
  stub.restore();
  vi.useRealTimers();
});

describe('第 2 阶段风格归一化合并（三原则）', () => {
  it('剧本统筹页不再出现第二套导演风格模板/采用到草稿/批准并冻结按钮', async () => {
    window.location.hash = '#d/assets';
    render(<App />);
    expect(await screen.findByText('逐项微调七个风格维度（高级）')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '选择导演风格模板' })).toBeNull();
    expect(screen.queryByRole('button', { name: '采用到草稿' })).toBeNull();
    expect(screen.queryByRole('button', { name: /批准并冻结/ })).toBeNull();
  });

  it('在高级折叠里微调七个维度并选择动画后，由同一个确认分镜书入口直接冻结（无需采用到草稿）', async () => {
    const user = userEvent.setup();
    const confirms: Array<Record<string, unknown>> = [];
    stub.add({
      method: 'POST',
      pattern: /\/api\/v1\/projects\/proj-1\/director\/storyboard-book$/,
      respond: () => jsonResponse(BOOK, 201),
    });
    stub.add({
      method: 'POST',
      pattern: /\/api\/v1\/projects\/proj-1\/director\/storyboard-book\/confirm$/,
      respond: (_url, init) => {
        confirms.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return jsonResponse({ plan_version: 1, style: { style_version_id: FROZEN_STYLE.id } });
      },
    });

    window.location.hash = '#d/assets';
    render(<App />);
    await user.click(await screen.findByText('逐项微调七个风格维度（高级）'));

    // 导演风格模板加载的七维内容直接出现在微调框中
    await waitFor(() => {
      expect(screen.getByLabelText('摄影语言（自定义）')).toHaveValue(TEMPLATE_BIBLE.photography_language);
    });

    // 用户直接修改其中一维为目录选项，另一维保持自定义并改写文本
    const blockingSelect = screen.getByLabelText('镜头调度') as HTMLSelectElement;
    const firstBlockingOption = STYLE_DIMENSION_OPTIONS.shot_blocking[0];
    await user.selectOptions(blockingSelect, firstBlockingOption.id);

    const photoTextarea = screen.getByLabelText('摄影语言（自定义）');
    await user.clear(photoTextarea);
    await user.type(photoTextarea, '固定远景，缓慢推近。（用户微调）');

    await user.selectOptions(screen.getByLabelText('影片类型'), 'animation');

    await user.click(screen.getAllByRole('button', { name: /生成分镜书/ })[0]);
    await screen.findByText(/计划总时长/);
    await user.click(screen.getAllByRole('button', { name: /确认分镜书（分集 \+ 风格）/ })[0]);

    await waitFor(() => expect(confirms).toHaveLength(1));
    expect(confirms[0]).toMatchObject({
      plan_version: 1,
      expected_sha256: BOOK.plan_sha256,
      style: {
        profile_id: 'wong-kar-wai',
        film_type: 'animation',
        dimensions: {
          photography_language: '固定远景，缓慢推近。（用户微调）',
          shot_blocking: firstBlockingOption.text,
          color_palette: TEMPLATE_BIBLE.color_palette,
          lighting_tone: TEMPLATE_BIBLE.lighting_tone,
          lens_texture: TEMPLATE_BIBLE.lens_texture,
          editing_rhythm: TEMPLATE_BIBLE.editing_rhythm,
          sound_performance: TEMPLATE_BIBLE.sound_performance,
        },
      },
    });
    // 没有再走第二条 style/draft + style/freeze 链路
    expect(stub.callsOf('PUT', /style\/draft$/)).toHaveLength(0);
    expect(stub.callsOf('POST', /style\/freeze$/)).toHaveLength(0);
  });

  it('确认分镜书失败（网络错误）时，高级区已修改的七维选择保留不丢失', async () => {
    const user = userEvent.setup();
    stub.add({
      method: 'POST',
      pattern: /\/api\/v1\/projects\/proj-1\/director\/storyboard-book$/,
      respond: () => jsonResponse(BOOK, 201),
    });
    stub.add({
      method: 'POST',
      pattern: /\/api\/v1\/projects\/proj-1\/director\/storyboard-book\/confirm$/,
      respond: () => { throw new Error('网络中断'); },
    });

    window.location.hash = '#d/assets';
    render(<App />);
    await user.click(await screen.findByText('逐项微调七个风格维度（高级）'));
    await waitFor(() => {
      expect(screen.getByLabelText('摄影语言（自定义）')).toHaveValue(TEMPLATE_BIBLE.photography_language);
    });
    const photoSelect = screen.getByLabelText('摄影语言') as HTMLSelectElement;
    const option = STYLE_DIMENSION_OPTIONS.photography_language[0];
    await user.selectOptions(photoSelect, option.id);

    await user.click(screen.getAllByRole('button', { name: /生成分镜书/ })[0]);
    await screen.findByText(/计划总时长/);
    await user.click(screen.getAllByRole('button', { name: /确认分镜书（分集 \+ 风格）/ })[0]);

    await waitFor(() => expect(screen.getByText(/网络中断/)).toBeInTheDocument());
    expect(screen.getByLabelText('摄影语言')).toHaveValue(option.id);
  });

  it('已确认后在高级区修改七个维度之一，触发同一个重新确认入口冻结新版本', async () => {
    const user = userEvent.setup();
    const confirms: Array<Record<string, unknown>> = [];
    const project = baseProject({
      frozen_style_version_id: FROZEN_STYLE.id,
      style_draft: { ...TEMPLATE_BIBLE, film_type: 'live_action', dialogue_ratio_percent: 45, narration_level: 'none', max_shot_seconds: 15 },
    });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([project]) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1$/, respond: () => jsonResponse(project) });
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/styles$/, respond: () => jsonResponse([{ ...FROZEN_STYLE, snapshot: project.style_draft }]) });
    stub.add({
      method: 'GET',
      pattern: /\/api\/v1\/projects\/proj-1\/episode-plans$/,
      respond: () => jsonResponse([
        { version: 1, status: 'CONFIRMED', sha256: BOOK.plan_sha256, target_seconds: 60, payload: { target_seconds: 60, episodes: [{ episode_id: 'ep-01' }] } },
      ]),
    });
    stub.add({
      method: 'GET',
      pattern: /\/api\/v1\/projects\/proj-1\/director-style$/,
      respond: () => jsonResponse({
        style_draft: project.style_draft,
        draft_source: { profile_id: 'wong-kar-wai', strength: 'balanced' },
        frozen_source: { profile_id: 'wong-kar-wai', strength: 'balanced' },
      }),
    });
    stub.add({
      method: 'POST',
      pattern: /\/api\/v1\/projects\/proj-1\/director\/storyboard-book\/confirm$/,
      respond: (_url, init) => {
        confirms.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
        return jsonResponse({ plan_version: 1, style: { style_version_id: 'style-2' } });
      },
    });

    window.location.hash = '#d/assets';
    render(<App />);
    await screen.findByText(/第 1 版已确认/);
    await user.click(await screen.findByText('逐项微调七个风格维度（高级）'));
    const colorSelect = screen.getByLabelText('色彩风格') as HTMLSelectElement;
    const warmColor = STYLE_DIMENSION_OPTIONS.color_palette[1];
    await user.selectOptions(colorSelect, warmColor.id);

    await screen.findByText(/风格选择已修改/);
    await user.click(screen.getAllByRole('button', { name: /重新确认分镜书（冻结新风格）/ })[0]);
    await waitFor(() => expect(confirms).toHaveLength(1));
    expect(confirms[0]).toMatchObject({
      plan_version: 1,
      expected_sha256: BOOK.plan_sha256,
      style: {
        profile_id: 'wong-kar-wai',
        dimensions: {
          color_palette: warmColor.text,
        },
      },
    });
  });
});
