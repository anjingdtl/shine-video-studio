/** 节点包诊断必须在前端可见：缺哪些包、本次运行会怎样、下一步去哪补装。

node_pack_notes 是后端唯一的「节点包缺失」出口。前端不消费它，门禁降级就等于静默通过——
用户以为配好了，实际那几个节点在本次运行里被禁用。向导与基础配置面板共用同一段文案。
 */
import { describe, expect, it } from 'vitest';

import { missingNodePacks, nodePackWarning } from '../components/nodePackNotes';

describe('节点包诊断文案', () => {
  it('列出缺失的节点包并给出下一步', () => {
    const warning = nodePackWarning([{
      code: 'NODE_PACK_DIRECTORY_MISSING',
      missing: ['ComfyUI-KJNodes', 'comfyui-videohelpersuite'],
      message: '这些节点包在安装的 custom_nodes 里找不到对应目录。',
    }]);
    expect(warning).toContain('ComfyUI-KJNodes');
    expect(warning).toContain('comfyui-videohelpersuite');
    expect(warning).toContain('2');
    expect(warning).toContain('安装与下载');
    expect(warning).toContain('禁用');
  });

  it('没有缺失时不产生噪音', () => {
    expect(nodePackWarning([])).toBe('');
    expect(nodePackWarning(undefined)).toBe('');
    expect(nodePackWarning([{ code: 'OTHER' }])).toBe('');
    expect(missingNodePacks(undefined)).toEqual([]);
  });
});
