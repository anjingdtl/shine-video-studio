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
  window.history.replaceState(null, '', '#d/produce?stage=assets');
  render(<App />);
  expect(await screen.findByRole('heading', { name: '项目素材' })).toBeInTheDocument();
  expect(screen.queryByText('MY WORK')).toBeNull();
});

it('从导演页面进入素材中心再返回时恢复原页面与地址', async () => {
  window.history.replaceState(null, '', '#d/produce?stage=assets');
  const user = userEvent.setup();
  render(<App />);
  expect(await screen.findByRole('heading', { name: '项目素材' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '素材中心' }));
  expect(await screen.findByRole('heading', { name: '创作首页' })).toBeInTheDocument();
  expect(window.location.hash).toBe('#a/home');
  await user.click(screen.getByRole('button', { name: /导演工作台/ }));
  expect(await screen.findByRole('heading', { name: '项目素材' })).toBeInTheDocument();
  expect(window.location.hash).toBe('#d/produce?stage=assets');
});

it('往返素材中心时保留指定导演包的深链标识', async () => {
  window.history.replaceState(null, '', '#d/package?package=pkg-exact-1234');
  const user = userEvent.setup();
  render(<App />);
  await user.click(await screen.findByRole('button', { name: '素材中心' }));
  expect(await screen.findByRole('heading', { name: '创作首页' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: /导演工作台/ }));
  expect(window.location.hash).toBe('#d/package?package=pkg-exact-1234');
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
  await gotoDirectorPage(user, /第 4 阶段：拍摄素材准备/);
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
  await gotoDirectorPage(user, /第 4 阶段：拍摄素材准备/);

  const thumb = await screen.findByAltText('主角.png');
  expect(thumb).toHaveAttribute('src', '/api/v1/assets/asset-1/thumbnail');
  // The one action the user must take here is loud, not a grey text link.
  const must = screen.getByRole('button', { name: /确认使用所选待确认素材/ });
  expect(must.className).toContain('must-button');
  expect(screen.getByText(/有 1 项上传素材待你选择/)).toBeInTheDocument();
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
  await gotoDirectorPage(user, /第 4 阶段：拍摄素材准备/);
  await user.click(await screen.findByRole('button', { name: '确认使用' }));
  await screen.findByText('PLAN_HASH_CHANGED');
  await waitFor(() => expect(screen.getByRole('button', { name: '确认使用' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: '确认使用' }));
  await screen.findByText('可用于分镜');
  expect(stub.callsOf('POST', /asset-1\/approve$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /asset-1\/lock$/)).toHaveLength(2);
});

it('项目锁定素材直接进入全局候选，不自动晋升', async () => {
  const asset = { id: 'asset-locked', project_id: 'proj-1', filename: '主角.png', kind: 'image', role: 'character',
    sha256: 'a'.repeat(64), byte_size: 32, approved: true, locked: true, entity_id: '林知远', tags: ['定妆'] };
  stub.add({ method: 'GET', pattern: /projects\/proj-1\/assets$/, respond: () => jsonResponse([{ ...asset }]) });
  stub.add({ method: 'POST', pattern: /projects\/proj-1\/assets\/asset-locked\/save-to-library$/, respond: () => jsonResponse({
    id: 'library-1', title: '林知远', versions: [{ id: 'version-1', status: 'READY_FOR_REVIEW' }],
  }, 201) });
  const user = userEvent.setup();
  render(<App />);
  await gotoDirectorPage(user, /第 4 阶段：拍摄素材准备/);

  await user.click(await screen.findByRole('button', { name: '收藏到素材中心' }));
  await user.click(screen.getByRole('button', { name: '保存为待审核候选' }));
  expect(await screen.findByText(/已保存为素材中心候选/)).toBeInTheDocument();
  expect(stub.callsOf('POST', /save-to-library$/)).toHaveLength(1);
  expect(stub.callsOf('POST', /save-to-library$/)[0].body).toMatchObject({
    title: '林知远', category: 'character',
  });
  await user.click(screen.getByRole('button', { name: '打开全局素材库' }));
  expect(window.location.hash).toBe('#a/library');
});
