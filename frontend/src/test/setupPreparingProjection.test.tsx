import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SetupEnvironmentStatus } from '../components/SetupEnvironmentStatus';
import { StudioStub, baseRoutes, jsonResponse, setupSummary } from './studioStub';

/**
 * 整备进行中不得把能力卡投影成「跑不通」死胡同（2026-11-19 用户报障）：
 * 同一份整备台账在进度面板显示进行中时，卡片②必须是「后台整备中」，
 * 不挂只会重排同一任务的按钮；整备结束仍未就绪才回到具名阻塞。
 */

let stub: StudioStub;
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

function imageCap(run: { state: string; label: string; reason?: string }) {
  const base = setupSummary();
  return {
    ...base.capabilities.image,
    state: 'FOUND',
    facts: { ...base.capabilities.image.facts, FILES_FOUND: true },
    summary: '已找到资源，尚未核验并登记。',
    two_step: {
      files: { state: 'UNVERIFIED', label: '文件已找到，待核验' },
      run,
    },
    missing_files: [],
    missing_bytes: 0,
  };
}

describe('整备进行中的能力卡投影', () => {
  it('整备活跃时②显示后台整备中，不显示跑不通与重复按钮', async () => {
    const base = setupSummary();
    stub = new StudioStub(baseRoutes());
    stub.add({
      method: 'GET', pattern: /setup\/summary/,
      respond: () => jsonResponse(setupSummary({
        capabilities: {
          ...base.capabilities,
          image: imageCap({ state: 'PREPARING', label: '后台整备中，完成后自动可用' }),
        },
        preparation: {
          status: 'RUNNING', task_id: 'setup-t', current: 'SCAN',
          steps: [{ id: 'SCAN', label: '深搜本机已有资源', status: 'RUNNING' }],
          done: 0, total: 1, message: '正在深搜', capabilities: ['image'],
        },
      })),
    });
    render(<SetupEnvironmentStatus projectId="proj-1" />);
    expect(await screen.findByText(/后台整备中，完成后自动可用/, undefined, { timeout: 20000 })).toBeTruthy();
    expect(screen.queryByText(/跑不通/)).toBeNull();
    expect(screen.queryByRole('button', { name: /一键绑定渲染器并核对运行合同/ })).toBeNull();
  });

  it('整备结束仍未就绪时回到具名阻塞与恢复按钮', async () => {
    const base = setupSummary();
    stub = new StudioStub(baseRoutes());
    stub.add({
      method: 'GET', pattern: /setup\/summary/,
      respond: () => jsonResponse(setupSummary({
        capabilities: {
          ...base.capabilities,
          image: imageCap({ state: 'BLOCKED', label: '跑不通', reason: '资源尚未绑定到本机渲染器或运行合同未核对' }),
        },
        preparation: { status: 'DONE', task_id: 'setup-t', current: null, steps: [], done: 1, total: 1, message: null },
      })),
    });
    render(<SetupEnvironmentStatus projectId="proj-1" />);
    expect(await screen.findByText(/跑不通/, undefined, { timeout: 20000 })).toBeTruthy();
    expect(screen.getByRole('button', { name: /一键绑定渲染器并核对运行合同/ })).toBeTruthy();
  });
});
