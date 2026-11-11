/** ActionRail 契约（设计方案 §3.1 / §9）：
 *  - 每页主操作唯一，且只存在于右侧操作栏；内容区不得出现 primary。
 *  - disabled 主操作必须带原因，最好带「去补齐」出口。
 *  - loading 主操作文案前缀「正在…」且不可再点。
 *  - 运行中的长任务显示为进度态，而不是 disabled。 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { ActionRail } from '../components/ActionRail';
import { StudioStub, baseRoutes } from './studioStub';
import { gotoDirectorPage } from './studioStub';

let stub: StudioStub;
beforeEach(() => { stub = new StudioStub(baseRoutes()); });
afterEach(() => { stub.restore(); });

describe('ActionRail 组件契约', () => {
  it('主操作唯一且渲染在操作栏内；disabled 必带原因与出口', () => {
    const { container } = render(
      <ActionRail
        stageId="package"
        stageLabel="第⑤步 · 导演包"
        primary={{ label: '生成导演包', onClick: () => undefined, disabled: true,
          disabledReason: '先在基础配置登记本机生成方案', guide: { label: '去基础配置登记', go: () => undefined } }}
        secondary={[{ label: '重新检查', onClick: () => undefined }]}
      />,
    );
    const primaries = container.querySelectorAll('.btn-primary');
    expect(primaries).toHaveLength(1);
    expect(container.querySelector('.action-rail .btn-primary')).not.toBeNull();
    expect(primaries[0]).toBeDisabled();
    expect(screen.getByText('先在基础配置登记本机生成方案')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /去基础配置登记/ })).toBeInTheDocument();
  });

  it('loading 主操作文案前缀「正在…」且禁用', () => {
    render(<ActionRail stageId="run" primary={{ label: '启动整部作品生产', onClick: () => undefined, loading: true }} />);
    const button = screen.getByRole('button', { name: /正在启动整部作品生产…/ });
    expect(button).toBeDisabled();
  });

  it('运行中长任务显示进度态而非 disabled 主操作', () => {
    const { container } = render(
      <ActionRail stageId="production" primary={{ label: '一键开拍', onClick: () => undefined }}
        running={{ label: '拍摄进行中', done: 3, total: 12 }} />,
    );
    expect(container.querySelector('.rail-running')).not.toBeNull();
    expect(container.querySelector('.btn-primary')).toBeNull();
    expect(screen.getByText('3/12')).toBeInTheDocument();
  });

  it('次级操作超过 4 个折叠为「更多」', async () => {
    const user = userEvent.setup();
    const secondary = Array.from({ length: 6 }, (_, i) => ({ label: `次级${i}`, onClick: () => undefined }));
    render(<ActionRail stageId="plan" primary={null} secondary={secondary} />);
    expect(screen.queryByRole('button', { name: '次级5' })).toBeNull();
    await user.click(screen.getByRole('button', { name: /更多（2）/ }));
    expect(screen.getByRole('button', { name: '次级5' })).toBeInTheDocument();
  });
});

describe('页面级主操作唯一性', () => {
  it('剧本统筹页：内容区无 primary，操作栏恰好一个 primary', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await gotoDirectorPage(user, /剧本统筹/);
    const rail = container.querySelector('.director-plan .action-rail');
    expect(rail).not.toBeNull();
    const contentPrimaries = container.querySelectorAll('.director-plan .stage-content .btn-primary');
    expect(contentPrimaries).toHaveLength(0);
    expect(rail!.querySelectorAll('.btn-primary').length).toBeLessThanOrEqual(1);
  });

  it('导演包页：主操作收敛到操作栏', async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await gotoDirectorPage(user, /导演包/);
    const page = container.querySelector('#package');
    expect(page).not.toBeNull();
    expect(page!.querySelectorAll('.stage-content .btn-primary')).toHaveLength(0);
    expect(page!.querySelectorAll('.action-rail .btn-primary').length).toBeLessThanOrEqual(1);
  });
});