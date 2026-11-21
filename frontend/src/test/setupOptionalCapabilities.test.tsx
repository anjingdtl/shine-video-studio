import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SetupEnvironmentStatus } from '../components/SetupEnvironmentStatus';
import { StudioStub, baseRoutes, jsonResponse, setupSummary } from './studioStub';

let stub: StudioStub;
afterEach(() => {
  stub?.restore();
  vi.restoreAllMocks();
});

describe('V3 P3 制作环境可选能力与外部 ComfyUI 复用显示', () => {
  it('如实展示按需可选增强状态且清晰说明外部 ComfyUI 复用不复制第二套', async () => {
    const base = setupSummary();
    stub = new StudioStub(baseRoutes());
    stub.add({
      method: 'GET',
      pattern: /setup\/summary/,
      respond: () => jsonResponse({
        ...base,
        installation: {
          ...base.installation,
          selected: {
            root: 'F:\\ComfyUI-Mie-V7.0\\ComfyUI',
            port: 8188,
            managed: false,
            adopted: true,
            source: 'adopted',
            reason: '已经确认复用的本机 ComfyUI',
            exists: true,
          },
        },
        optional_capabilities: {
          pdd: { key: 'pdd', label: 'PDD 8步加速', optional: true, available: true, status: 'READY' },
          second_pass: { key: 'second_pass', label: '二次采样精修', optional: true, available: false, status: 'OPTIONAL_UNCONFIGURED' },
          relayvsr: { key: 'relayvsr', label: 'RelayVSR 2X 超分', optional: true, available: false, status: 'OPTIONAL_UNCONFIGURED' },
        },
      }),
    });

    render(<SetupEnvironmentStatus projectId="proj-1" />);
    const installLine = await screen.findByTestId('setup-installation', undefined, { timeout: 20000 });
    expect(installLine.textContent).toContain('复用本机已有 ComfyUI（受管启动与显存保护，不复制第二套程序或模型）');

    const optionalBox = await screen.findByTestId('setup-optional-capabilities', undefined, { timeout: 20000 });
    expect(optionalBox.textContent).toContain('PDD 8步加速：已就绪');
    expect(optionalBox.textContent).toContain('二次采样精修：可选未配置');
    expect(optionalBox.textContent).toContain('RelayVSR 2X 超分：可选未配置');
  });
});
