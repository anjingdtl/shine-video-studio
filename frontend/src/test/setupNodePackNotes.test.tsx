/** 节点包诊断必须在前端可见：缺哪些包、本次运行会怎样、下一步去哪补装。

node_pack_notes 是后端唯一的「节点包缺失」出口。前端不消费它，门禁降级就等于静默通过——
用户以为配好了，实际那几个节点在本次运行里被禁用。

专业模式下线后，SetupPanel(高级设置里的环境单步诊断)是 nodePackWarning 的唯一消费方。
这两层断言各管一件事：
1) 文案函数本身：缺哪几个包、会禁用、下一步去哪补装；
2) 生成受管配置之后屏上仍然看得到这段事实，不被「配置已生成」顶掉。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SetupPanel } from '../components/SetupPanel';
import { missingNodePacks, nodePackWarning } from '../components/nodePackNotes';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;
const refresh = vi.fn().mockResolvedValue(undefined);
function route(method: string, suffix: string, body: unknown, status = 200) {
  stub.add({ method, pattern: new RegExp(suffix + '(?:\\?.*)?$'), respond: () => jsonResponse(body, status) });
}
function show() { return render(<SetupPanel projectId="proj-1" onRefresh={refresh} />); }

const MISSING_PACKS = ['ComfyUI-KJNodes', 'comfyui-videohelpersuite'];
/** 后端在门禁降级时写入的诊断：缺哪几个节点包。 */
function withMissingNodePacks() {
  route('POST', 'setup/configuration', {
    recipe: 'C:/tmp/managed_recipe.json',
    node_pack_notes: [{
      code: 'NODE_PACK_DIRECTORY_MISSING',
      missing: MISSING_PACKS,
      message: '这些节点包在安装的 custom_nodes 里找不到对应目录；请先在「安装与下载」补装节点。',
    }],
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  stub = new StudioStub(baseRoutes());
  route('GET', 'setup/runtime', { status: 'NOT_ACTIVATED', candidate: null });
});
afterEach(() => { stub.restore(); vi.restoreAllMocks(); localStorage.clear(); });

async function generateConfiguration() {
  const user = userEvent.setup();
  show();
  await user.click(screen.getByText('单步操作（高级）'));
  await user.click(await screen.findByRole('button', { name: '生成受管配置' }));
}

describe('节点包诊断文案', () => {
  it('列出缺失的节点包并给出下一步', () => {
    const warning = nodePackWarning([{
      code: 'NODE_PACK_DIRECTORY_MISSING',
      missing: ['ComfyUI-KJNodes', 'comfyui-videohelpersuite'],
      message: '这些节点包在安装的 custom_nodes 里找不到对应目录。',
    }]);
    expect(warning).toContain('ComfyUI-KJNodes');
    expect(warning).toContain('comfyui-videohelpersuite');
    expect(warning).toContain('2');
    expect(warning).toContain('安装与下载');
    expect(warning).toContain('禁用');
  });

  it('没有缺失时不产生噪音', () => {
    expect(nodePackWarning([])).toBe('');
    expect(nodePackWarning(undefined)).toBe('');
    expect(nodePackWarning([{ code: 'OTHER' }])).toBe('');
    expect(missingNodePacks(undefined)).toEqual([]);
  });
});

describe('环境诊断里的节点包降级提示', () => {
  it('生成受管配置之后仍然看得见缺哪些节点包、会禁用、下一步去哪补装', async () => {
    withMissingNodePacks();
    await generateConfiguration();
    await waitFor(() => expect(screen.getByText(/受管配置已生成/)).toBeInTheDocument());
    const line = screen.getByText(/受管配置已生成/);
    expect(line.textContent).toContain('ComfyUI-KJNodes');
    expect(line.textContent).toContain('comfyui-videohelpersuite');
    expect(line.textContent).toContain('安装与下载');
    expect(line.textContent).toContain('禁用');
  });

  it('没有节点包缺失时不出现警示文案', async () => {
    route('POST', 'setup/configuration', { recipe: 'C:/tmp/managed_recipe.json' });
    await generateConfiguration();
    await waitFor(() => expect(screen.getByText(/受管配置已生成/)).toBeInTheDocument());
    expect(screen.getByText(/受管配置已生成/).textContent).not.toContain('禁用');
  });

  it('重新生成时按本次实际结果更新警示，不留上一次的残留', async () => {
    withMissingNodePacks();
    await generateConfiguration();
    await waitFor(() => expect(screen.getByText(/ComfyUI-KJNodes/)).toBeInTheDocument());
    route('POST', 'setup/configuration', { recipe: 'C:/tmp/managed_recipe.json' });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: '生成受管配置' }));
    await waitFor(() => expect(screen.queryByText(/ComfyUI-KJNodes/)).toBeNull());
  });
});