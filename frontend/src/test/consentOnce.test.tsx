/** P4 项目级一次同意契约（设计方案 §6.2 / §9）：
 *  - 一次同意卡同意后写入项目级授权（granted=true），并回调继续。
 *  - 暂不发送不写入授权。
 *  - 旅程内冗余 consent 勾选已移除（StylePanel AI / FilmCrew 工种）。 */
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { ConsentOnceCard } from '../components/ConsentOnceCard';
import StylePanel from '../components/StylePanel';
import { StudioStub, baseRoutes, jsonResponse, FROZEN_STYLE } from './studioStub';
import { emptyStyle } from '../constants';

let stub: StudioStub;
beforeEach(() => { stub = new StudioStub(baseRoutes()); });
afterEach(() => { stub.restore(); vi.restoreAllMocks(); });

const SCOPE = {
  scope_hash: 's'.repeat(64), document_sha256: 'd'.repeat(64),
  transmission: '仅发送剧本文本到已配置的云端文字模型。',
  roles: [{ role: 'scriptwriter', configured: true, provider: 'minimax', model: 'M3.1', base_url: 'https://api.example.com', allowed: false }],
};

describe('项目级一次同意卡', () => {
  it('同意并继续写入项目级授权（granted=true）并回调', async () => {
    stub.add({ method: 'GET', pattern: /text-consent\/doc-1$/, respond: () => jsonResponse(SCOPE) });
    stub.add({ method: 'POST', pattern: /text-consent$/, respond: () => jsonResponse({ ...SCOPE, roles: SCOPE.roles.map(r => ({ ...r, allowed: true })) }) });
    const onAgreed = vi.fn();
    const user = userEvent.setup();
    render(<ConsentOnceCard projectId="proj-1" documentId="doc-1" onAgreed={onAgreed} />);
    const agree = await screen.findByRole('button', { name: '同意并继续' });
    await waitFor(() => expect(agree).not.toBeDisabled());
    await user.click(agree);
    await waitFor(() => expect(onAgreed).toHaveBeenCalled());
    const posts = stub.callsOf('POST', /text-consent$/);
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toMatchObject({ granted: true, document_id: 'doc-1' });
  });

  it('暂不发送不写入授权', async () => {
    stub.add({ method: 'GET', pattern: /text-consent\/doc-1$/, respond: () => jsonResponse(SCOPE) });
    const onDecline = vi.fn();
    const user = userEvent.setup();
    render(<ConsentOnceCard projectId="proj-1" documentId="doc-1" onDecline={onDecline} />);
    await user.click(await screen.findByRole('button', { name: '暂不发送' }));
    expect(onDecline).toHaveBeenCalled();
    expect(stub.callsOf('POST', /text-consent$/)).toHaveLength(0);
  });
});

describe('旅程内冗余 consent 勾选已移除', () => {
  it('StylePanel 不再渲染逐流程 AI 同意勾选', async () => {
    stub.add({ method: 'GET', pattern: /\/projects\/proj-1\/styles$/, respond: () => jsonResponse([FROZEN_STYLE]) });
    render(<StylePanel
      projectId="proj-1" style={emptyStyle} setStyle={() => undefined} serverDraft={null}
      currentStyle={FROZEN_STYLE} styles={[FROZEN_STYLE]} busy={false}
      onSave={() => undefined} onFreeze={() => undefined} onApplyStyleToAllShots={() => undefined}
      onRefreshFreeze={() => undefined} onDraftApplied={() => undefined} onNavigate={() => undefined}
      onMessage={() => undefined} />);
    await waitFor(() => expect(screen.queryByText('AI 帮我推荐')).toBeInTheDocument());
    expect(screen.queryByRole('checkbox', { name: /同意把项目设置/ })).toBeNull();
  });
});