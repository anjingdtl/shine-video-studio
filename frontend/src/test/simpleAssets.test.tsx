import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import App from '../App';
import { StudioStub, baseRoutes, gotoDirectorPage, jsonResponse } from './studioStub';

let stub: StudioStub;
beforeEach(() => {
  stub = new StudioStub(baseRoutes());
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

it('刷新后按 director 深链接恢复素材页，而不是回到作品概览', async () => {
  window.history.replaceState(null, '', '#d/assets');
  render(<App />);
  expect(await screen.findByRole('heading', { name: '项目素材' })).toBeInTheDocument();
  expect(screen.queryByText('MY WORK')).toBeNull();
});

it('一次确认先批准再锁定；取消不写入，重复操作不覆盖已固定素材', async () => {
  const asset = { id: 'asset-1', project_id: 'proj-1', filename: '主角.png', kind: 'image',
    sha256: 'a'.repeat(64), byte_size: 32, approved: false, locked: false, tags: [] };
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/assets$/, respond: () => jsonResponse([{ ...asset }]) });
  stub.add({ method: 'POST', pattern: /assets\/asset-1\/approve$/, respond: () => {
    asset.approved = true; return jsonResponse({ ...asset });
  } });
  stub.add({ method: 'POST', pattern: /assets\/asset-1\/lock$/, respond: () => {
    expect(asset.approved).toBe(true); asset.locked = true; return jsonResponse({ ...asset });
  } });
  const user = userEvent.setup();
  render(<App />);
  await gotoDirectorPage(user, /素材与风格/);
  const confirm = await screen.findByRole('button', { name: '确认使用' });
  expect(screen.queryByLabelText(/操作员凭据|核验依据/)).toBeNull();
  vi.mocked(window.confirm).mockReturnValue(false);
  await user.click(confirm);
  expect(stub.callsOf('POST', /assets\/asset-1\//)).toHaveLength(0);
  vi.mocked(window.confirm).mockReturnValue(true);
  await user.click(confirm);
  await screen.findByText('可用于分镜');
  expect(stub.callsOf('POST', /assets\/asset-1\//).map(c => c.url.split('/').at(-1))).toEqual(['approve', 'lock']);
  expect(stub.callsOf('POST', /approval-grants$/)).toHaveLength(2);
  expect(screen.queryByRole('button', { name: '确认使用' })).toBeNull();
});

it('图片素材显示缩略图，待确认素材用高对比按钮提示必做', async () => {
  const asset = { id: 'asset-1', project_id: 'proj-1', filename: '主角.png', kind: 'image',
    sha256: 'a'.repeat(64), byte_size: 32, approved: false, locked: false, tags: [] };
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/assets$/, respond: () => jsonResponse([{ ...asset }]) });
  const user = userEvent.setup();
  render(<App />);
  await gotoDirectorPage(user, /素材与风格/);

  const thumb = await screen.findByAltText('主角.png');
  expect(thumb).toHaveAttribute('src', '/api/v1/assets/asset-1/media');
  // The one action the user must take here is loud, not a grey text link.
  const must = screen.getByRole('button', { name: /确认使用全部待确认素材/ });
  expect(must.className).toContain('must-button');
  expect(screen.getByText(/必做：确认使用 1 项素材/)).toBeInTheDocument();
  expect(screen.getByText(/人物对照确认后才调用导演/)).toBeInTheDocument();
});

it('锁定失败会保留已批准状态；再次确认只补锁定，不重复批准', async () => {
  const asset = { id: 'asset-1', project_id: 'proj-1', filename: '主角.png', kind: 'image',
    sha256: 'a'.repeat(64), byte_size: 32, approved: false, locked: false, tags: [] };
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/assets$/, respond: () => jsonResponse([{ ...asset }]) });
  stub.add({ method: 'POST', pattern: /assets\/asset-1\/approve$/, respond: () => {
    asset.approved = true; return jsonResponse({ ...asset });
  } });
  let failed = false;
  stub.add({ method: 'POST', pattern: /assets\/asset-1\/lock$/, respond: () => {
    if (!failed) { failed = true; return jsonResponse({ detail: 'PLAN_HASH_CHANGED' }, 409); }
    asset.locked = true; return jsonResponse({ ...asset });
  } });
  const user = userEvent.setup();
  render(<App />);
  await gotoDirectorPage(user, /素材与风格/);
  await user.click(await screen.findByRole('button', { name: '确认使用' }));
  await screen.findByText('PLAN_HASH_CHANGED');
  await waitFor(() => expect(screen.getByRole('button', { name: '确认使用' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: '确认使用' }));
  await screen.findByText('可用于分镜');
  expect(stub.callsOf('POST', /asset-1\/approve$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /asset-1\/lock$/)).toHaveLength(2);
});
