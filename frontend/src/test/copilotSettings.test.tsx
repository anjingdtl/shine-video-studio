import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { CopilotSettingsPage } from '../components/CopilotSettingsPage';
import { ShineCopilot } from '../components/ShineCopilot';
import {
  DEFAULT_COPILOT_PROFILE,
  applyCopilotProfile,
  getCopilotProfile,
  type CopilotProfile,
} from '../copilotProfile';
import { StudioStub, baseProject, baseRoutes, jsonResponse } from './studioStub';

const PROFILE: CopilotProfile = {
  ...DEFAULT_COPILOT_PROFILE,
  assistant_nickname: '小映',
  user_salutation: '导演',
  startup_memory: '我主要制作 AI 影视短剧。',
  config_revision: 3,
  updated_at: '2026-11-17T10:00:00',
};

const STATUS = {
  state: 'OK',
  fts5: 'AVAILABLE',
  fts_rows: 1,
  vector: { provider: 'local-hash-ngram-v1', dim: 256, semantic_model: 'NOT_CONFIGURED' },
  vector_rows: 1,
  stale_vectors: 0,
  counts: { active: 1, global: 1, project: 1, auto: 0, manual: 1, deleted_tombstones: 0 },
  memory_enabled: true,
  auto_memory_enabled: false,
};

const MEMORY = {
  id: 'mem-1',
  scope: 'PROJECT',
  project_id: 'proj-1',
  project_title: '雾港',
  content: '项目镜头偏好：手持跟拍与自然光。',
  memory_type: 'PROJECT_DECISION',
  source: 'MANUAL',
  source_session_id: 's1',
  source_message_ids: ['m1'],
  content_revision: 1,
  status: 'ACTIVE',
  confirmed: true,
  created_at: '2026-11-17T10:00:00',
  updated_at: '2026-11-17T10:00:00',
  sources: [{ message_id: 'm1', role: 'user', excerpt: '我喜欢手持跟拍。', created_at: '2026-11-17T09:00:00' }],
};

function memoryRoutes() {
  return [
    { method: 'GET', pattern: /\/api\/v1\/support\/preferences$/, respond: () => jsonResponse(PROFILE) },
    {
      method: 'POST',
      pattern: /\/api\/v1\/support\/preferences$/,
      respond: (_url: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body || '{}'));
        return jsonResponse({ ...PROFILE, ...body, config_revision: PROFILE.config_revision + 1 });
      },
    },
    { method: 'GET', pattern: /\/api\/v1\/support\/memories\/status$/, respond: () => jsonResponse(STATUS) },
    {
      method: 'GET',
      pattern: /\/api\/v1\/support\/memories\?/,
      respond: () => jsonResponse({ memories: [MEMORY], total: 1, counts: STATUS.counts }),
    },
    {
      method: 'POST',
      pattern: /\/api\/v1\/support\/memories$/,
      respond: () => jsonResponse({ memory: MEMORY, deduplicated: false, suppressed: false }, 201),
    },
    {
      method: 'PATCH',
      pattern: /\/api\/v1\/support\/memories\/mem-1$/,
      respond: () => jsonResponse({ memory: { ...MEMORY, content: '更新后的项目偏好。' }, changed: true }),
    },
    {
      method: 'DELETE',
      pattern: /\/api\/v1\/support\/memories\/mem-1$/,
      respond: () => jsonResponse({ memory_id: 'mem-1', deleted: true }),
    },
    {
      method: 'POST',
      pattern: /\/api\/v1\/support\/memories\/clear$/,
      respond: () => jsonResponse({ cleared: 1, scope: 'PROJECT' }),
    },
    {
      method: 'POST',
      pattern: /\/api\/v1\/support\/memories\/rebuild$/,
      respond: () => jsonResponse({ rebuild: { memories: 1 }, status: STATUS }),
    },
    { method: 'GET', pattern: /\/api\/v1\/support\/context\?/, respond: () => jsonResponse({ snapshot: {}, suggested_questions: [], web_search_enabled: false }) },
    {
      method: 'GET',
      pattern: /\/api\/v1\/support\/capabilities\/actions$/,
      respond: () => jsonResponse({ capabilities: [], build: { state: 'FRESH', notice: '合同已匹配' } }),
    },
    {
      method: 'GET',
      pattern: /\/api\/v1\/asset-center\/capabilities$/,
      respond: () => jsonResponse({
        workspaces: [],
        image_capabilities: {},
        voice_capabilities: {},
        t2i_runtime: {},
      }),
    },
  ];
}

beforeEach(() => {
  applyCopilotProfile(DEFAULT_COPILOT_PROFILE);
  window.history.replaceState(null, '', '#');
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('AI 管家配置页', () => {
  it('渲染四个区域，昵称与称呼保存后立即同步共享配置', async () => {
    const stub = new StudioStub(memoryRoutes());
    const user = userEvent.setup();
    render(<CopilotSettingsPage projects={[{ id: 'proj-1', title: '雾港' }]} projectId="proj-1" />);

    expect(await screen.findByText('给管家一个名字，也告诉它怎么称呼你')).toBeInTheDocument();
    expect(screen.getByText('每次新会话，先读一遍你的长期说明')).toBeInTheDocument();
    expect(screen.getByText('本机记得你的偏好与项目历史')).toBeInTheDocument();
    expect(screen.getByText('记忆留在本机，去向由你决定')).toBeInTheDocument();

    const nickname = screen.getByLabelText('管家昵称');
    await user.clear(nickname);
    await user.type(nickname, '小映');
    const salutation = screen.getByLabelText('对用户的称呼');
    await user.clear(salutation);
    await user.type(salutation, '导演');
    await user.click(screen.getByRole('button', { name: '保存身份' }));

    await waitFor(() => {
      const post = stub.callsOf('POST', /\/support\/preferences$/)[0];
      expect(post.body).toMatchObject({ assistant_nickname: '小映', user_salutation: '导演' });
    });
    expect(getCopilotProfile().assistant_nickname).toBe('小映');
    expect(await screen.findByText(/身份设置已保存/)).toBeInTheDocument();
    stub.restore();
  });

  it('启动预读可保存、清空并显示字符数', async () => {
    const stub = new StudioStub(memoryRoutes());
    const user = userEvent.setup();
    render(<CopilotSettingsPage projects={[]} projectId="" />);
    const textarea = await screen.findByLabelText('启动预读记忆');
    await waitFor(() => expect(textarea).toHaveValue(PROFILE.startup_memory));
    const originalLength = PROFILE.startup_memory.length;
    expect(screen.getByText(new RegExp(`${originalLength}/4000`))).toBeInTheDocument();
    await user.type(textarea, '我偏好电影化叙事。');
    expect(screen.getByText(new RegExp(`${originalLength + '我偏好电影化叙事。'.length}/4000`))).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '保存预读' }));
    await waitFor(() => {
      expect(stub.callsOf('POST', /\/support\/preferences$/)[0].body).toMatchObject({
        startup_memory: `${PROFILE.startup_memory}我偏好电影化叙事。`,
      });
    });
    await user.click(screen.getByRole('button', { name: '清空' }));
    await waitFor(() => expect(stub.callsOf('POST', /\/support\/preferences$/).at(-1)?.body).toMatchObject({ startup_memory: '' }));
    stub.restore();
  });

  it('长期记忆可搜索、编辑、删除、清空项目并重建索引', async () => {
    const stub = new StudioStub(memoryRoutes());
    const user = userEvent.setup();
    render(<CopilotSettingsPage projects={[{ id: 'proj-1', title: '雾港' }]} projectId="proj-1" />);

    expect(await screen.findByText('项目镜头偏好：手持跟拍与自然光。')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('记忆项目筛选'), 'proj-1');
    await waitFor(() => expect(stub.callsOf('GET', /memories\?.*project_id=proj-1/).length).toBeGreaterThan(0));

    const search = screen.getByLabelText('搜索长期记忆');
    await user.type(search, '镜头');
    await user.click(screen.getByRole('button', { name: '搜索' }));
    await waitFor(() => expect(stub.callsOf('GET', /memories\?.*q=%E9%95%9C%E5%A4%B4/).length).toBeGreaterThan(0));

    await user.click(screen.getByRole('button', { name: '编辑' }));
    const edit = screen.getByLabelText('编辑记忆内容');
    await user.clear(edit);
    await user.type(edit, '更新后的项目偏好。');
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(stub.callsOf('PATCH', /\/memories\/mem-1$/)[0].body).toMatchObject({ content: '更新后的项目偏好。' }));

    await user.click(screen.getByRole('button', { name: '删除' }));
    expect(window.confirm).toHaveBeenCalled();
    await waitFor(() => expect(stub.callsOf('DELETE', /\/memories\/mem-1$/).length).toBe(1));

    await user.click(screen.getByRole('button', { name: '清除该项目记忆' }));
    await waitFor(() => expect(stub.callsOf('POST', /\/memories\/clear$/)[0].body).toMatchObject({
      scope: 'PROJECT', project_id: 'proj-1', confirm: true,
    }));

    await user.click(screen.getByRole('button', { name: /重建索引/ }));
    await waitFor(() => expect(stub.callsOf('POST', /\/memories\/rebuild$/).length).toBe(1));
    stub.restore();
  });

  it('关闭自动提炼与远端授权都会立即写入统一配置', async () => {
    const stub = new StudioStub(memoryRoutes());
    const user = userEvent.setup();
    render(<CopilotSettingsPage projects={[]} projectId="" />);
    await user.click(await screen.findByLabelText('启用自动提炼'));
    await waitFor(() => expect(stub.callsOf('POST', /\/support\/preferences$/).at(-1)?.body).toMatchObject({ auto_memory_enabled: true }));
    await user.click(screen.getByLabelText('允许远端模型提炼记忆'));
    await waitFor(() => expect(stub.callsOf('POST', /\/support\/preferences$/).at(-1)?.body).toMatchObject({ remote_memory_processing_consent: true }));
    await user.click(screen.getByLabelText('允许把召回记忆发送给远端模型'));
    await waitFor(() => expect(stub.callsOf('POST', /\/support\/preferences$/).at(-1)?.body).toMatchObject({ remote_context_consent: true }));
    stub.restore();
  });

  it('浅色与深色主题下都使用同一套配置界面', async () => {
    const stub = new StudioStub(memoryRoutes());
    document.documentElement.dataset.theme = 'dark';
    document.documentElement.dataset.appearance = 'dark';
    const { unmount } = render(<CopilotSettingsPage projects={[]} projectId="" />);
    expect(await screen.findByText('给管家一个名字，也告诉它怎么称呼你')).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe('dark');
    unmount();
    document.documentElement.dataset.theme = 'light';
    document.documentElement.dataset.appearance = 'light';
    render(<CopilotSettingsPage projects={[]} projectId="" />);
    expect(await screen.findByText('记忆留在本机，去向由你决定')).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe('light');
    stub.restore();
  });
});

describe('全局浮窗与导航', () => {
  it('昵称保存后全局 AI 浮窗标题与按钮同步', async () => {
    const stub = new StudioStub(memoryRoutes());
    applyCopilotProfile(PROFILE);
    const user = userEvent.setup();
    render(<ShineCopilot pageId="copilot" pageLabel="AI 管家配置" />);
    const fab = await screen.findByRole('button', { name: '打开 小映' });
    expect(fab).toHaveTextContent('小映');
    await user.click(fab);
    expect(await screen.findByText('SHINE 小映')).toBeInTheDocument();
    stub.restore();
  });

  it('左侧导航新增 AI 管家配置，hash 路由可刷新恢复', async () => {
    const project = baseProject();
    const stub = new StudioStub([...baseRoutes(project), ...memoryRoutes()]);
    const user = userEvent.setup();
    const { unmount } = render(<App />);
    await user.click(await screen.findByRole('button', { name: 'AI 管家配置' }));
    expect(window.location.hash).toBe('#d/copilot');
    expect(await screen.findByText(/让管家有名字、有记忆/)).toBeInTheDocument();
    unmount();

    window.history.replaceState(null, '', '#d/copilot');
    render(<App />);
    expect(await screen.findByText('给管家一个名字，也告诉它怎么称呼你')).toBeInTheDocument();
    stub.restore();
  });

  it('从素材中心也能通过现有导航进入 AI 管家配置', async () => {
    const stub = new StudioStub([...baseRoutes(baseProject()), ...memoryRoutes()]);
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole('button', { name: '素材中心' }));
    expect(window.location.hash).toBe('#a/home');
    await user.click(await screen.findByRole('button', { name: 'AI 管家配置' }));
    expect(window.location.hash).toBe('#d/copilot');
    expect(await screen.findByText('给管家一个名字，也告诉它怎么称呼你')).toBeInTheDocument();
    stub.restore();
  });
});
