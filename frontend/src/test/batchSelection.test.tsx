/** P4 批量操作契约（设计方案 §4④ / §9）：
 *  - 待确认上传素材可全选，批量确认按钮实时显示已选计数。
 *  - 清空选择后批量确认禁用。 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import userEvent from '@testing-library/user-event';
import App from '../App';
import { StudioStub, baseRoutes, jsonResponse, gotoDirectorPage } from './studioStub';

let stub: StudioStub;
beforeEach(() => { stub = new StudioStub(baseRoutes()); });
afterEach(() => { stub.restore(); });

function pendingAsset(id: string, name: string) {
  return { id, project_id: 'proj-1', filename: name, kind: 'image', sha256: 'a'.repeat(64),
    byte_size: 32, approved: false, locked: false, tags: [] };
}

describe('待确认素材批量选择', () => {
  it('全选后批量确认按钮显示已选计数；清空后禁用', async () => {
    stub.add({ method: 'GET', pattern: /projects\/proj-1\/assets$/, respond: () => jsonResponse([
      pendingAsset('asset-1', '甲.png'), pendingAsset('asset-2', '乙.png'),
    ]) });
    const user = userEvent.setup();
    render(<App />);
    await gotoDirectorPage(user, /第 4 阶段：拍摄素材准备/);

    const selectAll = await screen.findByRole('checkbox', { name: '全选待确认素材' });
    await user.click(selectAll);
    const confirm = await screen.findByRole('button', { name: /确认使用所选 2 项素材/ });
    expect(confirm).not.toBeDisabled();

    await user.click(screen.getByRole('button', { name: '清空' }));
    await waitFor(() => {
      const idle = screen.getByRole('button', { name: /确认使用所选待确认素材/ });
      expect(idle).toBeDisabled();
    });
  });
});