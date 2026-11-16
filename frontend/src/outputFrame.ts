/** 导演包画幅与分辨率档位 → H3 宽高。
 *  与后端 shine/h3/output_frame.py 是同一份事实的镜像（两侧测试钉住同值）：
 *  它只产出 OutputSpec.width/height，走既有的导演包冻结 → 方案宽高映射门禁 →
 *  渲染器 31.inputs.width/height 链路，不另起一套渲染控制。基准是已登记 H3
 *  工作流的模型原生画布（横屏 1344×768、竖屏 768×1344）；档位按 32 像素网格
 *  取整并夹在 256–2048，最低档短边夹到 256，不承诺 H3 渲染不了的尺寸。 */

export type Orientation = 'landscape' | 'portrait';

export const BASELINE: Record<Orientation, [number, number]> = {
  landscape: [1344, 768],
  portrait: [768, 1344],
};

export const RESOLUTION_SCALES = [0.3, 0.5, 0.7, 1.0] as const;

const MIN_SIDE = 256;
const MAX_SIDE = 2048;
const GRID = 32;

function roundGrid(value: number): number {
  const side = Math.floor(value / GRID + 0.5) * GRID;
  return Math.max(MIN_SIDE, Math.min(MAX_SIDE, side));
}

export function frameSize(orientation: Orientation, scale: number): { width: number; height: number } {
  const [baseW, baseH] = BASELINE[orientation];
  return { width: roundGrid(baseW * scale), height: roundGrid(baseH * scale) };
}

export const ORIENTATION_LABELS: Record<Orientation, string> = {
  landscape: '横屏 16:9',
  portrait: '竖屏 9:16',
};

export function scaleLabel(scale: number): string {
  return scale === 1 ? '1.0 · 原生基准' : `${scale} · 缩样`;
}