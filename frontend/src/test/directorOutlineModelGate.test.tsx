/** D1 回归：创意与剧本的大纲主按钮曾经永久禁用，禁用理由固定写「先在上方授权剧本文字发送」。
 *
 * 根因链：编剧角色没配模型时，服务端返回的角色行只有 configured=false、没有 allowed 字段；
 * 前端把「角色没配模型」与「还没授权」压成一个布尔值，于是主按钮永远 disabled，
 * 理由还指向一个已经做完的动作；而角色没配模型时授权也不会写库，用户因此被永久卡死。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OutlineConfirmationPanel } from '../components/OutlineConfirmationPanel';
import { StageRailProvider } from '../components/StageRail';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

beforeEach(() => {
  stub = new StudioStub([]);
});

afterEach(() => {
  stub.restore();
  vi.restoreAllMocks();
});

function stubScope(roles: unknown[]) {
  stub.add({ method: 'GET', pattern: /text-consent\/doc-1$/, respond: () => jsonResponse({ roles }) });
  stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([]) });
  stub.add({ method: 'GET', pattern: /director\/outline/, respond: () => jsonResponse({}, 404) });
}

describe('创意与剧本：大纲主按钮的禁用理由必须是真实阻塞', () => {
  it('角色没配模型时，说清真实阻塞并给出去配置文本模型的入口', async () => {
    stubScope([{ role: 'scriptwriter', configured: false }]);
    const onGoSetupModels = vi.fn();
    render(
      <StageRailProvider stageId="script" stageLabel="第①步 · 创意与剧本">
        <OutlineConfirmationPanel projectId="p1" documentId="doc-1"
          documentHash={'b'.repeat(64)} onGoSetupModels={onGoSetupModels} />
      </StageRailProvider>);

    const button = await screen.findByRole('button', { name: '后台提取并归并总体大纲' });
    await waitFor(() => expect(button).toBeDisabled());
    // 禁用理由必须说真实阻塞，而不是把用户引回一个已经做完的动作。
    expect(screen.getByText('编剧角色还没有配置模型，无法提取大纲')).toBeInTheDocument();
    expect(screen.queryByText('先在上方授权剧本文字发送')).toBeNull();

    // 必做动作块：说清「要做什么」与「为什么必须做」，并给可点的下一步。
    const required = document.querySelector('.required-action');
    expect(required).not.toBeNull();
    expect(required?.textContent).toContain('为什么必须做');
    expect(required?.textContent).toContain('配置');

    const go = await screen.findAllByRole('button', { name: '去配置文本模型' });
    expect(go.length).toBeGreaterThan(0);
    await userEvent.setup().click(go[0]);
    expect(onGoSetupModels).toHaveBeenCalled();
  });

  it('角色已配置但还没授权时，禁用理由指向授权这一步', async () => {
    stubScope([{ role: 'scriptwriter', configured: true, allowed: false }]);
    render(
      <StageRailProvider stageId="script">
        <OutlineConfirmationPanel projectId="p2" documentId="doc-1"
          documentHash={'b'.repeat(64)} />
      </StageRailProvider>);

    const button = await screen.findByRole('button', { name: '后台提取并归并总体大纲' });
    await waitFor(() => expect(button).toBeDisabled());
    // 角色路由读到「已配置但还没授权」后，理由才切到授权这一步。
    expect(await screen.findByText('先在上方授权剧本文字发送')).toBeInTheDocument();
    expect(screen.queryByText(/还没有配置模型/)).toBeNull();
    expect(document.querySelector('.required-action')).toBeNull();
  });

  it('角色已配置并已授权时，主按钮可用', async () => {
    stubScope([{ role: 'scriptwriter', configured: true, allowed: true }]);
    render(
      <StageRailProvider stageId="script">
        <OutlineConfirmationPanel projectId="p3" documentId="doc-1"
          documentHash={'b'.repeat(64)} />
      </StageRailProvider>);

    const button = await screen.findByRole('button', { name: '后台提取并归并总体大纲' });
    await waitFor(() => expect(button).toBeEnabled());
    expect(document.querySelector('.required-action')).toBeNull();
  });

  it('一个文本模型都没配时，授权不再谎报成功', async () => {
    stub.add({ method: 'GET', pattern: /text-consent\/doc-1$/,
      respond: () => jsonResponse({ roles: [{ role: 'scriptwriter', configured: false }] }) });
    stub.add({ method: 'POST', pattern: /text-consent$/,
      respond: () => jsonResponse({ roles: [{ role: 'scriptwriter', configured: false }] }) });
    stub.add({ method: 'GET', pattern: /story-preparation\?document_id=doc-1$/, respond: () => jsonResponse([]) });
    stub.add({ method: 'GET', pattern: /director\/outline/, respond: () => jsonResponse({}, 404) });

    render(<OutlineConfirmationPanel projectId="p4" documentId="doc-1"
      documentHash={'b'.repeat(64)} />);

    await userEvent.setup().click(
      await screen.findByRole('button', { name: '允许向上述模型发送本剧本必要文字' }));
    expect(await screen.findByText(/授权暂不生效/)).toBeInTheDocument();
    expect(screen.queryByText('已保存文字发送授权。')).toBeNull();
  });
});
