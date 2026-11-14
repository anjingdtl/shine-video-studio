import { afterEach, describe, expect, it, vi } from 'vitest';
import { timeoutSignal } from '../api';

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe('timeoutSignal 旧 WebView 兼容', () => {
  it('现代环境使用原生 AbortSignal.timeout', () => {
    vi.useFakeTimers();
    const signal = timeoutSignal(1000);
    expect(signal.aborted).toBe(false);
    vi.advanceTimersByTime(1001);
    expect(signal.aborted).toBe(true);
  });

  it('AbortSignal.timeout 缺失时回退到 AbortController，不抛错且仍会超时', () => {
    vi.useFakeTimers();
    const original = AbortSignal.timeout;
    // 旧版 Edge WebView2 / Safari 16 之前没有这个静态方法。
    // @ts-expect-error 故意删除以模拟旧环境
    delete AbortSignal.timeout;
    try {
      expect(typeof AbortSignal.timeout).toBe('undefined');
      const signal = timeoutSignal(5000);
      expect(signal.aborted).toBe(false);
      vi.advanceTimersByTime(5001);
      expect(signal.aborted).toBe(true);
    } finally {
      AbortSignal.timeout = original;
    }
  });
});