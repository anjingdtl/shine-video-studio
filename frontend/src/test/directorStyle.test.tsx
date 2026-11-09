import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DirectorStylePreview, DirectorStyleSummary, Style } from '../api';
import StylePanel from '../components/StylePanel';
import { DIRECTOR_STYLE_CATALOG, StudioStub, jsonResponse } from './studioStub';

const style: Style = {
  photography_language: '缓慢横移，观察人物。',
  shot_blocking: '前景分层，保持轴线。',
  color_palette: '暖琥珀与暗绿。',
  lighting_tone: '实景暖光与克制环境光。',
  lens_texture: '柔和景深，保留自然纹理。',
  editing_rhythm: '保留情绪停顿但不改时长。',
  sound_performance: '遵循项目对白，保留环境声。',
};

const source: DirectorStyleSummary = {
  profile_id: 'wong-kar-wai', profile_version: '1.0.0', profile_sha256: 'b'.repeat(64),
  strength: 'balanced', resolution_sha256: 'd'.repeat(64),
};

function Harness({ onSave, onFreeze }: { onSave: ReturnType<typeof vi.fn>; onFreeze: ReturnType<typeof vi.fn> }) {
  const [value, setValue] = useState<Style>({
    photography_language: '', shot_blocking: '', color_palette: '', lighting_tone: '',
    lens_texture: '', editing_rhythm: '', sound_performance: '',
  });
  return <StylePanel
    projectId="proj-1"
    style={value}
    setStyle={setValue}
    serverDraft={null}
    styles={[]}
    busy={false}
    onSave={onSave}
    onFreeze={onFreeze}
    onDraftApplied={vi.fn()}
    onNavigate={vi.fn()}
    onMessage={vi.fn()}
  />;
}

describe('director style picker', () => {
  let stub: StudioStub;
  afterEach(() => stub?.restore());

  it('loads the server catalog, previews seven dimensions, adopts to draft, and keeps freeze explicit', async () => {
    const user = userEvent.setup();
    let state = { draft_sha256: 'c'.repeat(64), draft_source: null as DirectorStyleSummary | null, frozen_source: null };
    const preview: DirectorStylePreview = {
      profile: DIRECTOR_STYLE_CATALOG[0],
      style_bible: style,
      diff: Object.entries(style).map(([field, suggested]) => ({
        field: field as keyof Style, current: '', suggested, changed: true,
      })),
      resolution: {
        selected_technique_ids: ['foreground-layering', 'motivated-practical-light'],
        constraints: { camera: [], lighting: [], color: [], texture: [], editing: [], sound: [] },
        warnings: [], resolution_sha256: 'd'.repeat(64),
      },
    };
    stub = new StudioStub([
      { method: 'GET', pattern: /\/api\/v1\/director-styles$/, respond: () => jsonResponse(DIRECTOR_STYLE_CATALOG) },
      { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/director-style$/, respond: () => jsonResponse(state) },
      { method: 'GET', pattern: /\/api\/v1\/film-agents$/, respond: () => jsonResponse([]) },
      { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/rulepack$/, respond: () => jsonResponse({ rulepack_id: null }) },
      { method: 'GET', pattern: /\/api\/v1\/h3-rulebook$/, respond: () => jsonResponse({ packs: [{ id: 'shine-h3-rules/1.1.0' }] }) },
      { method: 'GET', pattern: /\/api\/v1\/projects\/proj-1\/film-artifacts$/, respond: () => jsonResponse([]) },
      { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director-style\/preview$/, respond: () => jsonResponse(preview) },
      { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/director-style\/apply-draft$/, respond: () => {
        state = { draft_sha256: 'e'.repeat(64), draft_source: source, frozen_source: null };
        return jsonResponse({ style_draft: style, draft_sha256: state.draft_sha256, draft_source: source, frozen: false });
      } },
      { method: 'POST', pattern: /\/api\/v1\/projects\/proj-1\/rulepack\/freeze$/, respond: () => jsonResponse({ rulepack_id: 'shine-h3-rules/1.1.0' }) },
    ]);
    const onSave = vi.fn();
    const onFreeze = vi.fn();

    render(<Harness onSave={onSave} onFreeze={onFreeze} />);
    const selector = await screen.findByRole('combobox', { name: '选择导演风格模板' });
    expect(selector).toHaveTextContent('宫崎骏动画电影参考风格');
    expect(selector).toHaveTextContent('押井守动画电影参考风格');
    await user.selectOptions(selector, 'wong-kar-wai');
    expect(screen.getByText('城市情绪、前景遮挡与克制留白。')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '预览差异' }));
    expect(await screen.findByText('王家卫参考风格 · 已选 2 项适用技法')).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(8);

    await user.click(screen.getByRole('button', { name: '采用到草稿' }));
    expect(await screen.findByLabelText('摄影语言（自定义）')).toHaveValue(style.photography_language);
    expect(stub.callsOf('POST', /director-style\/apply-draft$/)[0].body).toMatchObject({
      profile_id: 'wong-kar-wai', profile_sha256: 'b'.repeat(64), strength: 'balanced',
      expected_draft_sha256: 'c'.repeat(64),
    });
    expect(screen.getByText(/采用只更新草稿/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '保存草稿' }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(true));
    await user.click(screen.getByRole('button', { name: /批准并冻结/ }));
    await waitFor(() => expect(onFreeze).toHaveBeenCalledWith(true));
  });
});
