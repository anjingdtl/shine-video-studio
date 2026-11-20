import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StudioStub, jsonResponse } from './studioStub';

import App from '../App';
import { HardwareMonitor } from '../components/HardwareMonitor';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub([]);
});

const SAMPLE_TELEMETRY = {
  checked_at: '2026-11-20T16:00:00+00:00',
  cpu: { utilization_pct: 18.5, temperature_c: null, power_w: null },
  memory: { utilization_pct: 59.0, used_mb: 19251, total_mb: 32600 },
  gpus: [{
    index: 0,
    name: 'NVIDIA GeForce RTX 5080',
    utilization_pct: 42.0,
    memory_used_mb: 4096,
    memory_total_mb: 16303,
    memory_pct: 25.1,
    temperature_c: 51.4,
    power_w: 112.6,
    power_limit_w: 360.0,
  }],
  gpu_error: null,
};

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  cleanup();
});

describe('左侧栏本机硬件监控', () => {
  it('展示 CPU、内存与 GPU 占用率/功耗/温度及实时趋势图，无传感器指标如实显示 —', async () => {
    stub.add({
      method: 'GET',
      pattern: /setup\/hardware-telemetry$/,
      respond: () => jsonResponse(SAMPLE_TELEMETRY),
    });

    render(<HardwareMonitor />);

    expect(await screen.findByText('18.5%')).toBeInTheDocument();
    expect(screen.getByText('3s 实时 · 00:00:00', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'CPU 实时占用趋势' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'GPU 实时占用趋势' })).toBeInTheDocument();
    expect(screen.getByText('温度 —')).toBeInTheDocument();
    expect(screen.getByText('功耗 —')).toBeInTheDocument();
    expect(screen.getByText(/18\.8\/31\.8 GB \(59%\)/)).toBeInTheDocument();
    expect(screen.getByText(/GPU · RTX 5080/)).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();
    expect(screen.getByText('温度 51°C')).toBeInTheDocument();
    expect(screen.getByText('功耗 113/360W')).toBeInTheDocument();
    expect(screen.getByText('显存 4.0/15.9 GB')).toBeInTheDocument();
  });

  it('每 3 秒自动刷新并实时推进动态变化数值', async () => {
    vi.useFakeTimers();
    try {
      let step = 0;
      stub.add({
        method: 'GET',
        pattern: /setup\/hardware-telemetry$/,
        respond: () => {
          step += 1;
          return jsonResponse({
            ...SAMPLE_TELEMETRY,
            cpu: {
              utilization_pct: step === 1 ? 18.5 : 64.2,
              temperature_c: step === 1 ? 49.0 : 62.4,
              power_w: step === 1 ? 38.2 : 89.6,
            },
            gpus: [{
              ...SAMPLE_TELEMETRY.gpus[0],
              utilization_pct: step === 1 ? 42.0 : 88.0,
              temperature_c: step === 1 ? 51.4 : 67.0,
              power_w: step === 1 ? 112.6 : 245.0,
            }],
          });
        },
      });

      render(<HardwareMonitor />);
      await vi.waitFor(() => expect(screen.getByText('18.5%')).toBeInTheDocument());
      expect(screen.getByText('温度 49°C')).toBeInTheDocument();
      expect(screen.getByText('功耗 38W')).toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(3000);
      await vi.waitFor(() => expect(screen.getByText('64.2%')).toBeInTheDocument());
      expect(screen.getByText('温度 62°C')).toBeInTheDocument();
      expect(screen.getByText('功耗 90W')).toBeInTheDocument();
      expect(screen.getByText('88%')).toBeInTheDocument();
      expect(screen.getByText('温度 67°C')).toBeInTheDocument();
      expect(screen.getByText('功耗 245/360W')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('无 NVIDIA GPU 时给出明确提示且支持手动刷新和收起', async () => {
    stub.add({
      method: 'GET',
      pattern: /setup\/hardware-telemetry$/,
      respond: () => jsonResponse({
        ...SAMPLE_TELEMETRY,
        gpus: [],
        gpu_error: 'NVIDIA_SMI_UNAVAILABLE',
      }),
    });

    render(<HardwareMonitor />);

    expect(await screen.findByText(/未检测到 NVIDIA GPU/)).toBeInTheDocument();
    const beforeRefresh = stub.callsOf('GET', /hardware-telemetry$/).length;
    fireEvent.click(screen.getByRole('button', { name: '刷新硬件监控' }));
    await waitFor(() => expect(stub.callsOf('GET', /hardware-telemetry$/)).toHaveLength(beforeRefresh + 1));

    const toggle = screen.getByRole('button', { name: /硬件资源/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/未检测到 NVIDIA GPU/)).toBeNull();
  });

  it('在导演工作台左侧栏中严格位于工作栈监控窗口上方', async () => {
    window.history.replaceState(null, '', '#d/projects');
    stub.add({ method: 'GET', pattern: /setup\/hardware-telemetry$/, respond: () => jsonResponse(SAMPLE_TELEMETRY) });
    stub.add({
      method: 'GET',
      pattern: /setup\/work-stacks$/,
      respond: () => jsonResponse({
        checked_at: '2026-11-20T16:00:00Z',
        stacks: [], workers: [], jobs: [],
        counts: { stacks: 0, working_stacks: 0, workers_online: 0, running_jobs: 0 },
        process_error: null,
      }),
    });

    render(<App />);

    const hw = await screen.findByLabelText('硬件监控');
    const stacks = await screen.findByLabelText('工作栈监控');
    expect(hw.compareDocumentPosition(stacks) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
