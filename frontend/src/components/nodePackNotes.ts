/** 节点包诊断的共用文案。
 *
 * node_pack_notes 是后端唯一的「节点包缺失」出口：门禁在离线/夹具场景降级时，缺失
 * 必须在这里变成人看得懂的话——缺哪几个包、这次运行会怎样、下一步去哪补装。
 * 文案函数单独成文件，是为了让向导与基础配置面板说同一句话，也让这段话能被直接断言。
 */

export type NodePackNote = { code?: string; missing?: string[]; message?: string };

export function missingNodePacks(notes: NodePackNote[] | undefined | null): string[] {
  return (notes ?? []).flatMap((note) => note.missing ?? []);
}

export function nodePackWarning(notes: NodePackNote[] | undefined | null): string {
  const missing = missingNodePacks(notes);
  if (!missing.length) return '';
  return `注意：${missing.length} 个节点包没有装（${missing.join('、')}）；`
    + '本次运行会把它们禁用，请到「安装与下载」补装后再重新生成运行配置。';
}
