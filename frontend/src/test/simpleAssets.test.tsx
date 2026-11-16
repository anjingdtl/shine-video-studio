/** 导演④素材准备页的导航契约。
 *
 *  2026-11-16 用户裁定：素材准备页只保留按镜头需求驱动的 ShotMaterialsPanel，
 *  移除重叠的「上传与确认项目素材」(AssetPanel) 与「项目素材」卡。通用素材管理
 *  归素材中心（平行模块），不再串进生产环节。这里固定深链恢复与往返素材中心的
 *  地址保持；素材准备本身的契约见 materialToPackage.test.tsx。 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it } from 'vitest';
import App from '../App';
import { StudioStub, baseRoutes } from './studioStub';

let stub: StudioStub;
beforeEach(() => { stub = new StudioStub(baseRoutes()); });
afterEach(() => { stub.restore(); });

const MATERIALS_HEADING = '镜头要什么，就准备什么';

it('刷新后按 director 深链接恢复素材页，而不是回到作品概览', async () => {
  window.history.replaceState(null, '', '#d/produce?stage=assets');
  render(<App />);
  expect((await screen.findAllByRole('heading', { name: MATERIALS_HEADING })).length).toBeGreaterThan(0);
  expect(screen.queryByText('MY WORK')).toBeNull();
});

it('从导演页面进入素材中心再返回时恢复原页面与地址', async () => {
  window.history.replaceState(null, '', '#d/produce?stage=assets');
  const user = userEvent.setup();
  render(<App />);
  expect((await screen.findAllByRole('heading', { name: MATERIALS_HEADING })).length).toBeGreaterThan(0);
  await user.click(screen.getByRole('button', { name: '素材中心' }));
  expect(await screen.findByRole('heading', { name: '创作首页' })).toBeInTheDocument();
  expect(window.location.hash).toBe('#a/home');
  await user.click(screen.getByRole('button', { name: /导演工作台/ }));
  expect((await screen.findAllByRole('heading', { name: MATERIALS_HEADING })).length).toBeGreaterThan(0);
  // 素材准备已是独立一页：旧的 produce?stage=assets 地址会规范化为 #d/materials。
  expect(window.location.hash).toBe('#d/materials');
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