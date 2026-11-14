/** D-R4-03：实体列曾只显示 8 位 id 片段。
 *
 * 审计记录要能回答「对什么做了什么」：显示名字才读得懂；拿不到名字时回退到 id，
 * 但不能把 id 片段当成名字展示。
 */
import { createElement } from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import AuditPanel from '../components/AuditPanel';
import type { AuditItem, AuditPage } from '../api';

function pageWith(entity: Partial<AuditItem>): AuditPage {
  return {
    items: [{
      id: 'a1', project_id: 'p1', actor: 'user',
      event_type: 'PROJECT_CREATED', entity_type: 'project',
      entity_id: 'entity-1',
      payload: {}, created_at: '2026-11-15T00:00:00',
      ...entity,
    }],
    total: 1, limit: 100, offset: 0,
  };
}

function renderPanel(page: AuditPage) {
  render(createElement(AuditPanel, { page, busy: false, onLoad: () => {}, onMore: () => {} }));
}

describe('审计面板的实体列', () => {
  it('有名字时显示名字，不显示 id 片段', () => {
    renderPanel(pageWith({ entity_id: 'abcdef1234567890', entity_label: '雾港' }));
    expect(document.body.textContent).toContain('雾港');
    expect(document.body.textContent).not.toContain('abcdef12');
  });

  it('拿不到名字时回退到 id 片段', () => {
    renderPanel(pageWith({ entity_id: 'abcdef1234567890', entity_label: null }));
    expect(document.body.textContent).toContain('abcdef12…');
  });
});