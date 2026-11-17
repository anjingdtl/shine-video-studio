import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MachineProgressBanner } from '../components/MachineProgressBanner';
import DirectorSetupPanel from '../components/DirectorSetupPanel';
import { StudioStub, baseRoutes, jsonResponse, setupSummary } from './studioStub';

/**
 * 顶部唯一机器进度条（用户裁定 2026-11-17）：
 * 一条红→绿的渐进条体现基础配置页所有后台运作；卡片下载按钮经同一个整备
 * 台账驱动它；100% 只对应「整备完成 · 真实制作已就绪」。
 */

let stub: StudioStub;
beforeEach(() => { window.sessionStorage.clear(); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

const DOWNLOADING = {
  active: true, percent: 42, label: '下载并安装 relayvsr-x2-diffusion',
  detail: 'model.safetensors · 关闭页面也会继续', source: 'download',
};
const READY = {
  active: false, percent: 100, label: '真实制作已就绪',
  detail: '本机 H3 制作环境可用。', source: 'idle',
};

describe('机器进度条只读服务端唯一投影', () => {
  it('后台运作中显示百分比、步骤文字与运作标记，填充色随完成度渐变', async () => {
    stub = new StudioStub(baseRoutes());
    stub.add({ method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse(setupSummary({ machine_progress: DOWNLOADING })) });
    render(<MachineProgressBanner />);
    const bar = await screen.findByRole('progressbar', undefined, { timeout: 20000 });
    expect(bar.getAttribute('aria-valuenow')).toBe('42');
    expect(screen.getByTestId('setup-background')).toBeTruthy();
    expect(screen.getByText('下载并安装 relayvsr-x2-diffusion')).toBeTruthy();
    const fill = bar.querySelector('.meter-fill') as HTMLElement;
    // hsl(50,72%,42%)：42% 处仍是偏红的黄，未到绿。
    expect(fill.style.background).toBe('rgb(184, 159, 30)');
  });

  it('100% 且静止才说整备完成，不挂运作标记', async () => {
    stub = new StudioStub(baseRoutes());
    stub.add({ method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse(setupSummary({ machine_progress: READY })) });
    render(<MachineProgressBanner />);
    expect(await screen.findByText('整备完成 · 真实制作已就绪', undefined, { timeout: 20000 })).toBeTruthy();
    expect(screen.queryByTestId('setup-background')).toBeNull();
    const bar = screen.getByRole('progressbar');
    // hsl(120,72%,42%)：100% 处是绿。
    expect((bar.querySelector('.meter-fill') as HTMLElement).style.background).toBe('rgb(30, 184, 30)');
  });

  it('未就绪的静止机器不给 100%，如实显示当前裁决', async () => {
    stub = new StudioStub(baseRoutes());
    stub.add({ method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse(setupSummary({
      machine_progress: { active: false, percent: 20, label: '尚未检查', detail: '先检测本机。', source: 'idle' },
    })) });
    render(<MachineProgressBanner />);
    const bar = await screen.findByRole('progressbar', undefined, { timeout: 20000 });
    expect(bar.getAttribute('aria-valuenow')).toBe('20');
    expect(screen.queryByText('整备完成 · 真实制作已就绪')).toBeNull();
  });
});

describe('卡片下载驱动同一条顶部进度条', () => {
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

  it('点 RelayVSR 卡的下载安装缺文件后，顶部条从静止转为运作中', async () => {
    let submitted = false;
    const base = setupSummary();
    const relayvsr = {
      ...base.capabilities.vlm, key: 'relayvsr', label: 'RelayVSR 2X 精修',
      two_step: {
        files: { state: 'MISSING', label: '缺 2 个文件（约 4.9 GiB）' },
        run: { state: 'MISSING', label: '跑不通', reason: '文件未就绪，先补齐文件' },
      },
    };
    const summaryFor = () => setupSummary({
      capabilities: { ...base.capabilities, relayvsr },
      machine_progress: submitted ? DOWNLOADING : { active: false, percent: 20, label: '尚未检查', detail: '', source: 'idle' },
      preparation: submitted
        ? { status: 'RUNNING', task_id: 'setup-x', current: 'DOWNLOAD', done: 2, total: 4,
          steps: [{ id: 'SCAN', label: '深搜本机已有资源', status: 'DONE' },
            { id: 'RECONCILE', label: '核验并登记已有字节', status: 'DONE' },
            { id: 'DOWNLOAD', label: '下载并安装缺失文件', status: 'RUNNING' }],
          message: '正在下载并安装：relayvsr（关闭页面也会继续）。' }
        : { status: 'IDLE', task_id: null, current: null, steps: [] },
    });
    stub = new StudioStub(baseRoutes());
    stub.add({ method: 'GET', pattern: /setup\/summary/, respond: () => jsonResponse(summaryFor()) });
    stub.add({ method: 'POST', pattern: /setup\/prepare\/execute$/, respond: () => {
      submitted = true;
      return jsonResponse({ status: 'QUEUED', task_id: 'setup-x', steps: ['SCAN', 'RECONCILE', 'DOWNLOAD'], message: '等待后台开始' });
    } });
    const user = userEvent.setup();
    render(<DirectorSetupPanel {...props} />);
    const button = await screen.findByRole('button', { name: /下载安装缺文件/ }, { timeout: 20000 });
    expect(screen.queryByTestId('setup-background')).toBeNull();
    await user.click(button);
    await waitFor(() => expect(screen.getByTestId('setup-background')).toBeTruthy(), { timeout: 20000 });
    expect(stub.callsOf('POST', /setup\/prepare\/execute/)).toHaveLength(1);
    const bar = screen.getByRole('progressbar', { name: /下载并安装/ });
    expect(bar.getAttribute('aria-valuenow')).toBe('42');
  });
});
