import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { MaterialsStagePage } from '../manual4/pages/MaterialsStagePage';
import type { M4MaterialsState, M4Overview } from '../manual4/api/client';
import { StudioStub, jsonResponse } from './studioStub';

let stub: StudioStub;

const REQUIREMENT = {
  id: 'req-1', project_id: 'p1', storyboard_revision_id: 'sb-1', family: 'character',
  entity_name: '林知远', variant_name: '', canonical_key: '林知远', description: '长风衣。',
  shot_ids: ['shot-1'], status: 'pending', bound_asset_version_id: null, user_declined: false,
  bound_asset: null, candidates: [], reusable_assets: [],
};

const STATE: M4MaterialsState = {
  confirmed_revision: null, requirements: [REQUIREMENT], assets: [], shots: [],
  readiness: {} as M4MaterialsState['readiness'],
};

const LIBRARY_ITEM = {
  library_asset_id: 'la-1', library_version_id: 'lv-1', content_sha256: 'e'.repeat(64),
  media_kind: 'image', category: 'character', rights_id: 'r-1', rights_status: 'approved',
  filename: 'lin.png', title: '林知远 · 长风衣', project_authorized: true,
  media_url: '/api/v1/asset-center/versions/lv-1/media',
};

function routes() {
  return [
    { method: 'GET', pattern: /manual4\/projects\/p1\/materials\/requirements$/, respond: () => jsonResponse(STATE) },
    { method: 'GET', pattern: /manual4\/projects\/p1\/materials\/library/, respond: () => jsonResponse({ items: [LIBRARY_ITEM], total: 1 }) },
    { method: 'POST', pattern: /manual4\/projects\/p1\/materials\/library\/adopt$/, respond: () => jsonResponse({ asset: { id: 'lv-1' }, bound_shots: ['shot-1'], state: STATE }) },
  ];
}

beforeEach(() => { localStorage.clear(); stub = new StudioStub(routes()); });
afterEach(() => { stub.restore(); localStorage.clear(); });

it('四阶来源下拉「从素材中心导入」弹窗选素材并走既有采纳执行者', async () => {
  const user = userEvent.setup();
  render(<MaterialsStagePage
    overview={{ project: { id: 'p1' } } as unknown as M4Overview}
    onRefresh={async () => {}} onNextStage={() => {}} />);
  const source = await screen.findByLabelText('素材来源');
  await user.selectOptions(source, 'import_center');
  const dialog = await screen.findByRole('dialog', { name: /从素材中心导入 · 林知远/ });
  expect(dialog).toBeInTheDocument();
  await user.click(await screen.findByRole('option', { name: /林知远 · 长风衣/ }));
  await waitFor(() => expect(stub.callsOf('POST', /materials\/library\/adopt$/)).toHaveLength(1));
  expect(stub.callsOf('POST', /materials\/library\/adopt$/)[0].body).toMatchObject({
    version_id: 'lv-1', requirement_id: 'req-1', family: 'character',
  });
});