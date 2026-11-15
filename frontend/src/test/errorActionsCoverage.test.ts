/** 常见装机/门禁拒绝码必须有可行动指引，而不是只甩原始码。 */
import { describe, expect, it } from 'vitest';

import { ERROR_ACTIONS, describeBlocker } from '../errorActions';

const SETUP_CODES = [
  'HOST_NOT_QUALIFIED',
  'PROFILE_WEIGHTS_NOT_AVAILABLE',
  'PROFILE_WEIGHTS_NOT_VERIFIED',
  'PROFILE_WEIGHTS_NOT_CONSUMABLE',
  'PROFILE_NOT_REGISTERED_FOR_PROJECT',
  'PROFILE_GRAPH_IDENTITY_CHANGED',
  'MANAGED_INSTANCE_NOT_CONFIGURED',
  'MODEL_VARIANT_NOT_ACCEPTED',
  'VARIANT_NOT_DOWNLOADABLE',
  'REAL_RUNTIME_NOT_ACTIVATED',
  'SERVICE_VALIDATION_REQUIRED',
  'ASSET_RIGHTS_NOT_APPROVED',
  'CHARACTER_NAME_ALREADY_IN_USE',
  'OUTPUT_DIRECTORY_BUSY',
  'SHOT_SUBMISSION_OUTCOME_UNKNOWN',
];

describe('错误码 → 动作映射覆盖', () => {
  it.each(SETUP_CODES)('%s 有原因与导航动作', (code) => {
    const entry = ERROR_ACTIONS[code];
    expect(entry, `${code} 缺少映射`).toBeDefined();
    expect(entry.reason.length).toBeGreaterThan(0);
    expect(entry.action?.target).toBeTruthy();
  });

  it('未知码保留服务端原文，不编造动作', () => {
    const result = describeBlocker('SOME_FUTURE_CODE', '服务端给出的具体原因');
    expect(result.reason).toBe('服务端给出的具体原因');
    expect(result.action).toBeUndefined();
  });

  it('HOST_NOT_QUALIFIED 说清下载与运行的区别', () => {
    const result = describeBlocker('HOST_NOT_QUALIFIED', '');
    expect(result.why).toContain('运行');
    expect(result.action?.target).toBe('setup-env');
  });
});