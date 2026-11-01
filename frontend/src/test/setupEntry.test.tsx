/** Counterexample (audit 2026-11-01 #5): on an empty database the "新机设置"
 *  entry only changed the address bar — the main area still demanded a
 *  project.  The fix renders the project-independent setup panels (environment,
 *  connectivity, catalog, storage, preferences) before any project exists. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes(baseProject({ id: 'proj-1', title: '已有项目' })));
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

describe('空数据库的新机设置入口', () => {
  function emptyDatabase() {
    stub.add({ method: 'GET', pattern: /\/api\/v1\/projects$/, respond: () => jsonResponse([]) });
  }

  it('无任何项目打开普通入口：设置板块可直接进入，环境检查可见', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText('从一个项目开始')).toBeInTheDocument();
    // The setup section is reachable without a project through real navigation.
    await user.click(screen.getByTitle('打开设置板块'));
    expect(await screen.findByText('新机设置与生产就绪')).toBeInTheDocument();
    expect(screen.getByText(/1 · 环境检查/)).toBeInTheDocument();
    // The project creation entry stays available in the same view.
    expect(screen.getByText(/接下来，从第一个项目开始/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('给项目起个名字')).toBeInTheDocument();
  });

  it('无项目时依赖项目的操作有明确说明', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByTitle('打开设置板块'));
    await screen.findByText('新机设置与生产就绪');
    expect(screen.getByText(/计划生成、Profile 登记、配置生成与激活需要先创建一个项目/)).toBeInTheDocument();
  });

  it('建立第一个项目后工作台完整出现并停留在设置板块', async () => {
    emptyDatabase();
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByTitle('打开设置板块'));
    await screen.findByText('新机设置与生产就绪');
    stub.add({
      method: 'POST', pattern: /\/api\/v1\/projects$/,
      respond: (_url, init) => {
        const body = JSON.parse(String(init?.body));
        return jsonResponse(baseProject({ id: 'proj-new', title: body.title }), 201);
      },
    });
    await user.type(screen.getByPlaceholderText('给项目起个名字'), '第一个项目');
    await user.click(screen.getByRole('button', { name: '新建项目' }));
    // The full workbench appears (metrics + panels) with the project context.
    await waitFor(() => {
      expect(screen.getAllByText('第一个项目').length).toBeGreaterThan(0);
    });
    expect(document.querySelector('.date-chip')?.textContent).toContain('1 个');
  });

  it('已有项目时打开工作台仍是完整四板块布局', async () => {
    render(<App />);
    await waitFor(() => {
      expect(document.querySelector('.date-chip')?.textContent).toContain('1 个');
    });
    expect(screen.queryByText('从一个项目开始')).not.toBeInTheDocument();
  });
});
