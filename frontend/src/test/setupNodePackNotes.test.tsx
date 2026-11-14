/** 门禁降级后必须让人看得见：缺哪些节点包、下一步去哪补装。

node_pack_notes 是后端唯一的「节点包缺失」出口。前端不消费它，降级就等于静默通过——
用户以为配好了，实际那几个节点在本次运行里被禁用。
 */
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RealProductionWizard } from '../components/RealProductionWizard';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;
const callbacks = { onClose: vi.fn(), onFinished: vi.fn(), onOpenExpertSetup: vi.fn(), onRefresh: vi.fn() };

function route(method: string, suffix: string, body: unknown, status = 200) {
  stub.add({ method, pattern: new RegExp(suffix + '(?:\\?.*)?$'), respond: () => jsonResponse(body, status) });
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  stub = new StudioStub(baseRoutes());
  route('GET', 'system/status', {});
  route('GET', 'setup/readiness', { layers: [] });
  route('POST', 'setup/profiles', { profiles: [] });
  route('GET', 'setup/runtime', { status: 'NOT_ACTIVATED', candidate: { project_id: 'proj-1', recipe_sha256: 'a'.repeat(64) } });
  route('POST', 'setup/services/validate', { status: 'READY' });
});

afterEach(() => { stub.restore(); vi.restoreAllMocks(); localStorage.clear(); });

describe('节点包诊断必须在前端可见', () => {
  it('运行配置生成后，界面说明缺哪些节点包与下一步', async () => {
    route('POST', 'setup/configuration', {
      recipe: 'C:/tmp/managed_recipe.json',
      node_pack_notes: [{
        code: 'NODE_PACK_DIRECTORY_MISSING',
        missing: ['ComfyUI-KJNodes', 'comfyui-videohelpersuite'],
        message: '这些节点包在安装的 custom_nodes 里找不到对应目录；请先在「安装与下载」补装节点。',
      }],
    });
    render(<RealProductionWizard mode="enable" projectId="proj-1" {...callbacks} />);
    await waitFor(() => expect(screen.getByText(/ComfyUI-KJNodes/)).toBeInTheDocument(), { timeout: 8000 });
    expect(screen.getByText(/安装与下载/)).toBeInTheDocument();
  });
});
