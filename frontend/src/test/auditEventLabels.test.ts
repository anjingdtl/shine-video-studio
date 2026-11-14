/** 审计事件码不许裸奔。

后端写下的是大写事件码（COMFY_OUTPUT_PERSISTED 这样）。以前前端只精编了 35 个，
其余 300 多个直接原样上屏——对谁都没有意义。现在每一层都有说法，屏上不该再出现裸码。

清单 backendAuditEventCodes.json 与后端源码一一对应（backend/tests/test_audit_event_codes.py
守护），所以这里是**逐码**断言，不是挑几个例子。
 */
import { createElement } from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import AuditPanel from '../components/AuditPanel';

import {
  auditActionLabels,
  auditEntityLabels,
  auditEventLabel,
  auditEventLabels,
  auditEventLabelsDeprecated,
  auditSubjectLabels,
} from '../constants';
import backendAuditEventCodes from '../auditEventCodes.json';

describe('审计事件码的中文说法', () => {
  it('后端写下的每一个事件码都不再裸奔', () => {
    const naked = backendAuditEventCodes.filter((code) => auditEventLabel(code) === code);
    expect(naked).toEqual([]);
  });

  it('每一句说法都含中文，不会只剩一串大写码', () => {
    const notChinese = backendAuditEventCodes.filter(
      (code) => !/[\u4e00-\u9fff]/.test(auditEventLabel(code)),
    );
    expect(notChinese).toEqual([]);
  });

  it('带上实体类型时用实体的名字补全', () => {
    expect(auditEventLabel('SOME_UNMAPPED_EVENT', 'project_terminology')).toContain('人名对照表');
    expect(auditEventLabel('SOME_UNMAPPED_EVENT', 'library_asset')).toContain('素材库素材');
  });

  it('实在解析不出来时也有兜底说法，仍不是裸码', () => {
    expect(auditEventLabel('ZZZ_UNKNOWN')).toBe('未知操作（ZZZ_UNKNOWN）');
  });

  it('精编表里的每个键都是后端真的写下的码', () => {
    const codes = new Set<string>(backendAuditEventCodes);
    const stale = Object.keys(auditEventLabels).filter((code) => !codes.has(code));
    expect(stale).toEqual([]);
  });

  it('后端不再写下的码单独标注为废弃，不混在活码里', () => {
    const codes = new Set<string>(backendAuditEventCodes);
    const wronglyAlive = Object.keys(auditEventLabelsDeprecated).filter((code) => codes.has(code));
    expect(wronglyAlive).toEqual([]);
  });

  it('后端用到的实体类型都有名字', () => {
    const entities = ['job', 'agent_task', 'agent_artifact', 'library_render_task', 'comfy_instance',
      'production_series', 'installation_attempt', 'preproduction_plan', 'director_episode_plan',
      'resource_lease', 'production_run', 'material_run', 'library_asset', 'agent_route',
      'material_item', 'project_document', 'library_rights', 'director_package', 'package',
      'asset_requirement', 'relay_upscale_item', 'setup_task', 'library_voice_profile',
      'voice_runtime', 'library_media', 'tool_repair', 'project_terminology', 'material_text_consent'];
    const missing = entities.filter((name) => !auditEntityLabels[name]);
    expect(missing).toEqual([]);
  });

  it('动作与主体表不空', () => {
    expect(Object.keys(auditActionLabels).length).toBeGreaterThan(50);
    expect(Object.keys(auditSubjectLabels).length).toBeGreaterThan(40);
  });
});


/** UI 级：审计面板上不能出现裸大写码。 */
describe('审计面板上的事件码', () => {
  const page = {
    items: [
      {
        id: 'a1', project_id: 'p1', actor: 'user',
        event_type: 'COMFY_OUTPUT_PERSISTED', entity_type: 'artifact',
        entity_id: 'abcdef123456', payload: {}, created_at: '2026-11-15T00:00:00',
      },
      {
        id: 'a2', project_id: 'p1', actor: 'user',
        event_type: 'PROJECT_CREATED', entity_type: 'project',
        entity_id: 'project-1', payload: {}, created_at: '2026-11-15T00:01:00',
      },
    ],
    total: 2, limit: 100, offset: 0,
  };

  it('未收录的事件码在屏上也是能读懂的话', () => {
    render(createElement(AuditPanel, { page, busy: false, onLoad: () => {}, onMore: () => {} }));
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('COMFY_OUTPUT_PERSISTED');
    expect(text).toMatch(/[\u4e00-\u9fff]/);
  });

  it('精编过的事件码仍然显示精编文案', () => {
    render(createElement(AuditPanel, { page, busy: false, onLoad: () => {}, onMore: () => {} }));
    expect(screen.getByText('创建项目')).toBeInTheDocument();
  });
});