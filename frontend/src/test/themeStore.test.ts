import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  __resetThemeStoreForTests,
  applyThemeToDocument,
  bootstrapTheme,
  getTheme,
  setTheme,
  subscribeTheme,
} from '../theme/themeStore';

beforeEach(() => {
  window.localStorage.clear();
  __resetThemeStoreForTests();
  document.documentElement.removeAttribute('data-theme');
  document.documentElement.removeAttribute('data-appearance');
  document.documentElement.style.colorScheme = '';
  document.querySelector('meta[name="theme-color"]')?.remove();
  window.history.replaceState(null, '', '/');
});

describe('single theme store', () => {
  it('restores legacy light/dark preferences and falls back safely', () => {
    window.localStorage.setItem('shine-theme', 'dark');
    expect(getTheme()).toBe('dark');

    __resetThemeStoreForTests();
    window.localStorage.setItem('shine-theme', 'legacy-unknown');
    expect(getTheme()).toBe('light');
  });

  it('applies a theme immediately to the document, storage and subscribers', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeTheme(listener);
    setTheme('xianxia');
    expect(getTheme()).toBe('xianxia');
    expect(document.documentElement.dataset.theme).toBe('xianxia');
    expect(document.documentElement.dataset.appearance).toBe('dark');
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(window.localStorage.getItem('shine-theme')).toBe('xianxia');
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#0d262a');
    expect(listener).toHaveBeenCalledTimes(1);

    setTheme('xianxia');
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    setTheme('comic');
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('never changes the route or business URL while switching', () => {
    window.history.replaceState(null, '', '#d/creative');
    setTheme('cinema');
    expect(window.location.hash).toBe('#d/creative');
  });

  it('keeps working when localStorage throws', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    try {
      expect(getTheme()).toBe('light');
      expect(() => setTheme('cyberpunk')).not.toThrow();
      expect(getTheme()).toBe('cyberpunk');
      expect(document.documentElement.dataset.theme).toBe('cyberpunk');
    } finally {
      getItem.mockRestore();
      setItem.mockRestore();
    }
  });

  it('restores the stored theme once at bootstrap', () => {
    window.localStorage.setItem('shine-theme', 'cinema');
    expect(bootstrapTheme()).toBe('cinema');
    expect(document.documentElement.dataset.theme).toBe('cinema');
    expect(window.localStorage.getItem('shine-theme')).toBe('cinema');
    expect(bootstrapTheme()).toBe('cinema');
  });

  it('follows storage events from other tabs', () => {
    bootstrapTheme();
    const listener = vi.fn();
    subscribeTheme(listener);
    window.dispatchEvent(new StorageEvent('storage', { key: 'shine-theme', newValue: 'comic' }));
    expect(getTheme()).toBe('comic');
    expect(document.documentElement.dataset.theme).toBe('comic');
    expect(listener).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new StorageEvent('storage', { key: 'shine-theme', newValue: 'not-a-theme' }));
    expect(getTheme()).toBe('light');
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('ignores storage events for unrelated keys', () => {
    bootstrapTheme();
    const listener = vi.fn();
    subscribeTheme(listener);
    window.dispatchEvent(new StorageEvent('storage', { key: 'shine-font', newValue: 'large' }));
    expect(listener).not.toHaveBeenCalled();
  });

  it('applies appearance and browser colour without React', () => {
    applyThemeToDocument('comic');
    expect(document.documentElement.dataset.theme).toBe('comic');
    expect(document.documentElement.dataset.appearance).toBe('light');
    expect(document.documentElement.style.colorScheme).toBe('light');
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#fff4df');
  });
});
