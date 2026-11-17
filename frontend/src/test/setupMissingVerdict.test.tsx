import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SetupEnvironmentStatus } from '../components/SetupEnvironmentStatus';
import { StudioStub, baseRoutes, jsonResponse, setupSummary } from './studioStub';

/**
 * 能力卡「① 文件」与「缺少 N 个文件」同一裁决（用户报障 2026-11-17 真机截图）：
 * 已核验的字节不得再挂缺件入口；真缺件时数量/体积与缺件表同源；整备停步的
 * 失败块读错误码映射表的原因，不显示裸异常类名。
 */

let stub: StudioStub;
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

const GIB = 1024 ** 3;

function verifiedCap(base: ReturnType<typeof setupSummary>, key: 'h3' | 'vlm', label: string) {
  return {
    ...base.capabilities[key],
    key,
    label,
    state: 'VERIFIED',
    facts: { ...base.capabilities[key].facts, FILES_FOUND: true, FILES_VERIFIED: true },
    summary: '资源已核验，还没有绑定到当前 ComfyUI 实例。',
    two_step: {
      files: { state: 'READY', label: '文件已就绪并核验' },
      run: { state: 'BLOCKED', label: '跑不通', reason: '资源尚未绑定到本机渲染器或运行合同未核对' },
    },
    missing_files: [],
    missing_bytes: 0,
    unplaced_files: [{ component_id: 'minimax-h3-diffusion', relative_path: 'w.safetensors', size: 19 * GIB }],
  };
}

describe('能力卡缺件入口与文件裁决同源', () => {
  it('文件已核验时不再显示「缺少 N 个文件」', async () => {
    const base = setupSummary();
    stub = new StudioStub(baseRoutes());
    stub.add({
      method: 'GET', pattern: /setup\/summary/,
      respond: () => jsonResponse(setupSummary({
        capabilities: {
          ...base.capabilities,
          h3: verifiedCap(base, 'h3', 'H3 视频生成'),
          vlm: verifiedCap(base, 'vlm', '画面审片'),
        },
      })),
    });
    render(<SetupEnvironmentStatus projectId="proj-1" />);
    expect(await screen.findAllByText('文件已就绪并核验', undefined, { timeout: 20000 })).toHaveLength(2);
    expect(screen.queryByText(/缺少 \d+ 个文件/)).toBeNull();
  });

  it('真缺件时数量与体积读同一份缺件表，展开可见保存位置', async () => {
    const base = setupSummary();
    const missing = [
      { component_id: 'c1', relative_path: 'a.safetensors', filename: 'a.safetensors', size: 2 * GIB, url: 'https://example.invalid/a', save_to: 'D:\\m\\a' },
      { component_id: 'c2', relative_path: 'b.safetensors', filename: 'b.safetensors', size: 2 * GIB, url: 'https://example.invalid/b', save_to: 'D:\\m\\b' },
    ];
    stub = new StudioStub(baseRoutes());
    stub.add({
      method: 'GET', pattern: /setup\/summary/,
      respond: () => jsonResponse(setupSummary({
        capabilities: {
          ...base.capabilities,
          h3: {
            ...base.capabilities.h3,
            two_step: {
              files: { state: 'MISSING', label: '缺 2 个文件（约 4.0 GiB）', missing_count: 2, missing_bytes: 4 * GIB },
              run: { state: 'BLOCKED', label: '跑不通', reason: '文件未就绪，先补齐文件' },
            },
            missing_files: missing,
            missing_bytes: 4 * GIB,
          },
        },
      })),
    });
    render(<SetupEnvironmentStatus projectId="proj-1" />);
    const toggle = await screen.findByText('缺少 2 个文件（4.0 GiB）', undefined, { timeout: 20000 });
    await userEvent.click(toggle);
    expect(screen.getByText('a.safetensors')).toBeTruthy();
    expect(screen.getByText('b.safetensors')).toBeTruthy();
    expect(screen.getAllByText('复制保存位置')).toHaveLength(2);
  });

  it('整备停步显示映射后的原因与恢复说明，不显示 ValueError', async () => {
    const code = 'INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED';
    stub = new StudioStub(baseRoutes());
    stub.add({
      method: 'GET', pattern: /setup\/summary/,
      respond: () => jsonResponse(setupSummary({
        preparation: {
          status: 'FAILED', task_id: 'setup-t', current: 'DOWNLOAD',
          steps: [
            { id: 'RECONCILE', label: '核验并登记已有字节', status: 'DONE', detail: 'h3:RECONCILED' },
            { id: 'DOWNLOAD', label: '下载并安装缺失文件', status: 'FAILED', code, detail: code },
          ],
          done: 1, total: 2, message: code, error: code,
        },
      })),
    });
    render(<SetupEnvironmentStatus projectId="proj-1" />);
    expect(await screen.findByText(/安装计划的批准回执与当前计划对不上/, undefined, { timeout: 20000 })).toBeTruthy();
    expect(screen.getByText(/再点一次「一键准备」会重新批准同一份计划/)).toBeTruthy();
    expect(screen.queryByText(/ValueError/)).toBeNull();
  });
});
