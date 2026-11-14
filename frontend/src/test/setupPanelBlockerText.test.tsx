/** D-R4-04 回归：设置板块的「本机阻断项」把裸错误码直接拼给用户。
 *
 * 根因：frontend/src/components/SetupPanel.tsx 的环境检查区块用
 * env.blockers.map((b) => b.code + ...) 直接拼裸码，于是屏上出现
 * 「本机阻断项：TOOL_MISSING_INSTALLABLE(npm)」。
 * 同一个文件里已经有 blockerText() 做了中文映射（TOOL_MISSING_INSTALLABLE
 * → 「npm 尚未安装」），只是这一处没有用它。
 *
 * 期望：阻断项用中文说明，不出现裸大写码；未收录的码也要有可读兜底，
 * 但不能把裸码原样丢给用户。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SetupPanel } from '../components/SetupPanel';
import { StudioStub, baseRoutes, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub(baseRoutes());
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  stub.add({ method: 'GET', pattern: /setup\/catalog$/, respond: () => jsonResponse({
    catalog_id: 'c', catalog_version: '1', generated: '', platform: {}, total_bytes: 0,
    components: [],
  }) });
  stub.add({ method: 'GET', pattern: /setup\/connectivity$/, respond: () => jsonResponse({
    status: 'OK', blocked: [], upstreams: [], degraded: [], guidance: [], checked_at: '',
  }) });
});

afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

async function detectWithBlockers(blockers: unknown[]) {
  stub.add({ method: 'GET', pattern: /setup\/environment$/, respond: () => jsonResponse({
    platform: {}, tools: { npm: { present: false, version: null, at_floor: false } },
    gpu: {}, ram: { status: 'OK' }, disk: { status: 'OK', free_gb: 100, at_floor: true },
    port: { status: 'OK', port: 8000 }, data_dir: { path: 'D:\\Shine', writable: true },
    blockers, bootstrap: '',
  }) });
  const user = userEvent.setup();
  render(<SetupPanel projectId="proj-1" onRefresh={async () => undefined} />);
  await user.click(screen.getByRole('button', { name: '检测本机' }));
  await waitFor(() => expect(screen.getByText(/本机阻断项/)).toBeInTheDocument());
}

describe('设置板块的本机阻断项', () => {
  it('已收录的阻断项用中文说明，不把裸码丢给用户', async () => {
    await detectWithBlockers([{ code: 'TOOL_MISSING_INSTALLABLE', tool: 'npm' }]);
    const note = screen.getByText(/本机阻断项/);
    expect(note.textContent).toContain('尚未安装');
    expect(note.textContent).not.toContain('TOOL_MISSING_INSTALLABLE');
  });

  it('未收录的阻断项也有可读兜底，裸码只作为补充信息', async () => {
    await detectWithBlockers([{ code: 'SOME_BRAND_NEW_BLOCKER' }]);
    const note = screen.getByText(/本机阻断项/);
    expect(note.textContent).not.toMatch(/本机阻断项：SOME_BRAND_NEW_BLOCKER\b/);
    expect(note.textContent).toContain('未通过');
  });
});
