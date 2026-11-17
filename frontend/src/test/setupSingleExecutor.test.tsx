import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SetupCenter } from '../components/SetupCenter';
import DirectorSetupPanel from '../components/DirectorSetupPanel';
import { StudioStub, baseRoutes, jsonResponse, setupSummary } from './studioStub';

/**
 * 一个执行者：基础配置里只剩「一键准备」这一个执行入口。
 *
 * 复现过的旁路：「核验已有文件」直调 `/setup/reconcile`（关页面就断的长请求），
 * 画面审片/语音核对由页面自己排安装计划并等下载结束。两者都绕过现有后台任务，
 * 与任务各自推进同一件事。这里固定：两处都提交 `/setup/prepare/execute`，进度
 * 读已有安装记录；页面不再自己排安装计划。
 */

let stub: StudioStub;
beforeEach(() => { window.sessionStorage.clear(); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

async function detect() {
  await screen.findByText(/上次检测/, undefined, { timeout: 20000 });
}

function reconcileSummary() {
  return setupSummary({
    action_required: {
      code: 'FILES_NOT_VERIFIED', label: '核验已有文件', reason: '文件已在本机，还没有逐字节核验。',
      action: 'RECONCILE', capability: 'h3',
    },
  });
}

describe('核验已有文件不再走页面直调', () => {
  it('点主行动只提交一键准备任务', async () => {
    stub = new StudioStub(baseRoutes());
    stub.add({ method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse(reconcileSummary()) });
    const user = userEvent.setup();
    render(<SetupCenter projectId="proj-1" onRefresh={vi.fn()} />);
    await detect();

    await user.click(await screen.findByRole('button', { name: '核验已有文件' }));
    await waitFor(() => expect(stub.callsOf('POST', /setup\/prepare\/execute/).length)
      .toBeGreaterThan(0), { timeout: 20000 });
    expect(stub.callsOf('POST', /setup\/reconcile/)).toHaveLength(0);
    const body = stub.callsOf('POST', /setup\/prepare\/execute/)[0].body as { capabilities: string[] };
    expect(body.capabilities).toContain('h3');
  });
});

describe('可选审片/语音也只有一个执行入口', () => {
  const props = {
    projectId: 'proj-1', projectTitle: '', setProjectTitle: () => {}, createProject: () => {},
    system: null, profiles: [], routes: [], workflowProfiles: [], busy: false,
    refreshEpoch: 0, onRefresh: async () => {},
    onProjectSettings: () => {}, onCreateProfile: () => {}, onTestProfile: () => {},
    onSetKey: () => {}, onDeleteProfile: () => {}, onRoute: () => {},
    onTestComfy: async () => ({ ok: true, message: '' }),
    onRegisterWorkflow: () => {}, onValidateWorkflow: () => {}, onDeleteWorkflow: () => {},
    onBindStandardParameters: () => {},
  };

  it('画面审片交给一键准备，页面不再自己排安装计划', async () => {
    stub = new StudioStub(baseRoutes());
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const user = userEvent.setup();
    render(<DirectorSetupPanel {...props} />);
    await user.click(screen.getByRole('button', { name: '③ 独立能力（可选）' }));
    await user.click(await screen.findByRole('button', { name: /下载并启用画面审片/ }));

    await waitFor(() => expect(stub.callsOf('POST', /setup\/prepare\/execute/).length)
      .toBeGreaterThan(0), { timeout: 20000 });
    const body = stub.callsOf('POST', /setup\/prepare\/execute/)[0].body as { capabilities: string[] };
    expect(body.capabilities).toEqual(['vlm']);
    expect(stub.callsOf('POST', /setup\/install-plan/)).toHaveLength(0);
    expect(await screen.findByText(/已交给后台整备/)).toBeTruthy();
  });
});
