/** 空数据库与两层界面的入口契约（2026-11-02 重构后）：
 *  1. 空库首次进入 → 导演模式「创建我的第一部AI短剧」，不强迫先装机；
 *  2. 制作环境检查不需要项目：从首启卡片一键进入专业模式的设置板块；
 *  3. 建立第一个项目后，导演模式四步流程完整出现；
 *  4. 已有项目时默认进入导演模式，专业模式保留完整四板块。
 *  历史契约（空库可达设置板块）继续被第 1、2 条覆盖。 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '雾港夜账' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('空数据库与两层界面入口', () => {
  function emptyDatabase() {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([]) });
  }

  it('无任何项目：首启卡片出现，制作环境检查无需项目即可进入', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    // 首次使用体验：明确的第一步，而不是一堆面板。
    expect(await screen.findByText('创建我的第一部AI短剧')).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/给作品起个名字/)).toBeInTheDocument();
    // 制作环境检查不需要项目，一键直达专业模式的设置板块。
    await user.click(screen.getByRole('button', { name: '检查制作环境' }));
    expect(await screen.findByRole('heading', { name: '制作环境' })).toBeInTheDocument();
    expect(screen.getByText(/1 · 环境检查/)).toBeInTheDocument();
  });

  it('无项目时依赖项目的操作有明确说明', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: '检查制作环境' }));
    await screen.findByRole('heading', { name: '制作环境' });
    expect(screen.getByText(/计划生成、Profile 登记、配置生成与激活需要先创建一个项目/)).toBeInTheDocument();
  });

  it('建立第一个项目后导演流程完整出现', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText('创建我的第一部AI短剧')).toBeInTheDocument();
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects$/,
      respond: (_url, init) => {
        const body = JSON.parse(String(init?.body));
        return jsonResponse(baseProject({ id: 'proj-new', title: body.title }), 201);
      },
    });
    await user.type(screen.getByPlaceholderText(/给作品起个名字/), '第一个项目');
    await user.click(screen.getByRole('button', { name: '创建作品' }));
    // 建项后进入四步导演流程，下一步引导由真实状态驱动。
    await waitFor(() => {
      expect(screen.getAllByText('第一个项目').length).toBeGreaterThan(0);
    });
    expect(screen.getByRole('button', { name: /我的作品/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /素材与风格/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /AI导演制作/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /成片中心/ })).toBeInTheDocument();
  });

  it('已有项目时默认导演模式；专业模式保留完整四板块', async () => {
    const user = userEvent.setup();
    render(<App />);
    // 默认进入导演模式（普通用户层）。
    await waitFor(() => {
      expect(screen.getAllByText('雾港夜账').length).toBeGreaterThan(0);
    });
    expect(screen.queryByTitle('打开设置板块')).not.toBeInTheDocument();
    // 切到专业模式：完整四板块布局仍在。
    await user.click(screen.getByRole('button', { name: /专业模式/ }));
    expect(await screen.findByTitle('打开设置板块')).toBeInTheDocument();
    await waitFor(() => {
      expect(document.querySelector('.date-chip')?.textContent).toContain('1 个');
    });
  });
});
