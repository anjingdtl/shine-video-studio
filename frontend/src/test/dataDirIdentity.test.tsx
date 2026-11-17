/** 顶栏必须显示当前服务栈读的数据目录身份；presence/重启等监督流量
 *  跟随服务栈自报的 supervisor 端口，而不是硬编码 8790。
 *  回归：验收实例接管默认端口后，页面读空台账无从分辨，心跳还发往日常 supervisor。 */
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import { noteSupervisorPort, presencePing, supervisorPort } from '../api';
import { SYSTEM_STATUS, StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
  window.location.hash = '';
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.location.hash = '';
});

describe('数据目录身份与 supervisor 跟随', () => {
  it('顶栏显示后端自报的数据目录', async () => {
    stub.add({
      method: 'GET', pattern: /system\/status$/,
      respond: () => jsonResponse({ ...SYSTEM_STATUS, data_dir: 'F:\\acceptance\\launcher-4' }),
    });
    render(<App />);
    const chip = await screen.findByTitle('当前数据目录：F:\\acceptance\\launcher-4');
    expect(chip).toHaveTextContent('F:\\acceptance\\launcher-4');
  });

  it('presence 跟随服务栈自报的 supervisor 端口', async () => {
    noteSupervisorPort(8796);
    expect(supervisorPort()).toBe(8796);
    const fetchMock = vi.fn(async (_url: string) => ({ json: async () => ({ status: 'ok' }) }));
    vi.stubGlobal('fetch', fetchMock);
    await presencePing();
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8796/presence');
  });
});
