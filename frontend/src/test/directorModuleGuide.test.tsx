/** D6 回归：导演七阶段没有共用的必做动作块与模块引导块。
 *
 * 根因：required-action / must-button / module-guide 三个类名此前只在专业模式
 * （StrategyResourceCard）出现，module-guide 全仓 0 命中；导演七阶段页面上
 * 用户看不到「这一步要做什么」与「为什么必须做」。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { ModuleGuide, RequiredAction } from '../components/RequiredAction';
import { StudioStub, baseRoutes, gotoDirectorPage } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

async function switchToDirector(user: ReturnType<typeof userEvent.setup>) {
  if (document.querySelector('.app-shell.ui-director')) return;
  const toggle = await waitFor(() => {
    const node = document.querySelector('.ui-toggle');
    expect(node).not.toBeNull();
    return node as HTMLElement;
  });
  await user.click(toggle);
  await waitFor(() => expect(document.querySelector('.app-shell.ui-director')).not.toBeNull(), { timeout: 5000 });
}

/** 当前可见的阶段页（隐藏页也在 DOM 里，不能拿它们的引导块当断言对象）。 */
function visibleGuide(): Element | null {
  const page = document.querySelector('.director-page:not([hidden])');
  return page?.querySelector('.module-guide') ?? null;
}

describe('必做动作块与模块引导块', () => {
  it('导演七阶段每一页都带模块引导块，说清要做什么与为什么必须做', async () => {
    const user = userEvent.setup();
    render(<App />);
    await switchToDirector(user);

    for (const name of [/创意与剧本/, /剧本统筹/, /镜头拆解/, /拍摄素材准备/, /导演包/, /视频生产/, /后期成片/]) {
      await gotoDirectorPage(user, name);
      const guide = visibleGuide();
      expect(guide, `阶段 ${name} 没有模块引导块`).not.toBeNull();
      expect(guide?.textContent).toContain('这一步要做什么');
      expect(guide?.textContent).toContain('为什么必须做');
    }
  });

  it('必做动作块给出可点的下一步', async () => {
    const go = vi.fn();
    render(
      <div>
        <ModuleGuide what="整理并确认总体大纲。" why="大纲是后面几步的共同依据。" />
        <RequiredAction what="给编剧角色配置文本模型。" why="没有模型就没有大纲。"
          action={{ label: '去配置文本模型', go }} />
      </div>,
    );
    const guide = document.querySelector('.module-guide');
    expect(guide?.textContent).toContain('为什么必须做');
    const required = document.querySelector('.required-action');
    expect(required?.textContent).toContain('这一步要做什么');
    expect(required?.textContent).toContain('为什么必须做');
    await userEvent.setup().click(screen.getByRole('button', { name: '去配置文本模型' }));
    expect(go).toHaveBeenCalled();
  });

  it('三类块共用同一套类名', () => {
    render(
      <div>
        <ModuleGuide what="整理并确认总体大纲。" why="大纲是后面几步的共同依据。" />
        <RequiredAction what="给编剧角色配置文本模型。" why="没有模型就没有大纲。"
          action={{ label: '去配置文本模型', go: () => undefined }} />
      </div>,
    );
    expect(document.querySelector('.module-guide')).not.toBeNull();
    expect(document.querySelector('.required-action')).not.toBeNull();
    expect(document.querySelector('.required-action .must-button')).not.toBeNull();
  });
});
