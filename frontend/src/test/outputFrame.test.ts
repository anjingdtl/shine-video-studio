/** 画幅/分辨率档位镜像必须与后端 shine/h3/output_frame.py 钉住同一份事实。 */
import { describe, expect, it } from 'vitest';
import { BASELINE, RESOLUTION_SCALES, frameSize } from '../outputFrame';
import type { OutputSpec } from '../api';

describe('outputFrame', () => {
  it('基准是模型原生画布', () => {
    expect(frameSize('landscape', 1.0)).toEqual({ width: 1344, height: 768 });
    expect(frameSize('portrait', 1.0)).toEqual({ width: 768, height: 1344 });
    expect(BASELINE.landscape).toEqual([1344, 768]);
  });

  it('每个档位都落在 H3 的 32 像素网格与 256–2048 窗口内', () => {
    for (const orientation of ['landscape', 'portrait'] as const) {
      for (const scale of RESOLUTION_SCALES) {
        const { width, height } = frameSize(orientation, scale);
        expect(width % 32).toBe(0);
        expect(height % 32).toBe(0);
        expect(width).toBeGreaterThanOrEqual(256);
        expect(height).toBeGreaterThanOrEqual(256);
        expect(width).toBeLessThanOrEqual(2048);
        expect(height).toBeLessThanOrEqual(2048);
        const spec: OutputSpec = { width, height, fps: 24, clip_seconds: null };
        expect(spec.width).toBe(width);
      }
    }
  });

  it('最低档短边夹到 H3 下限，不承诺渲染不了的尺寸', () => {
    expect(frameSize('landscape', 0.3)).toEqual({ width: 416, height: 256 });
    expect(frameSize('portrait', 0.3)).toEqual({ width: 256, height: 416 });
    expect(frameSize('landscape', 0.5)).toEqual({ width: 672, height: 384 });
    expect(frameSize('landscape', 0.7)).toEqual({ width: 928, height: 544 });
  });
});