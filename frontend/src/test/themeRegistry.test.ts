import { describe, expect, it } from 'vitest';
import { getThemeDefinition, parseThemeId, THEME_IDS, THEMES } from '../theme/themes';

describe('six-theme registry', () => {
  it('contains exactly the six stable ids', () => {
    expect(THEME_IDS).toEqual(['light', 'dark', 'xianxia', 'cyberpunk', 'cinema', 'comic']);
    expect(new Set(THEME_IDS).size).toBe(6);
  });

  it('keeps metadata and previews complete and unique', () => {
    for (const theme of THEMES) {
      expect(theme.name.trim()).not.toBe('');
      expect(theme.description.trim()).not.toBe('');
      expect(theme.preview.background).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.preview.surface).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.preview.accent).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.preview.text).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.preview.onAccent).toMatch(/^#[0-9a-f]{6}$/i);
    }
    expect(new Set(THEMES.map((theme) => theme.name)).size).toBe(6);
    expect(new Set(THEMES.map((theme) => theme.preview.background)).size).toBe(6);
  });

  it('marks light and comic as light appearances and the rest as dark', () => {
    expect(THEMES.filter((theme) => theme.appearance === 'light').map((theme) => theme.id))
      .toEqual(['light', 'comic']);
    expect(THEMES.filter((theme) => theme.appearance === 'dark').map((theme) => theme.id))
      .toEqual(['dark', 'xianxia', 'cyberpunk', 'cinema']);
  });

  it('parses known ids and falls back to light for anything else', () => {
    for (const id of THEME_IDS) expect(parseThemeId(id)).toBe(id);
    expect(parseThemeId('sepia')).toBe('light');
    expect(parseThemeId('')).toBe('light');
    expect(parseThemeId(null)).toBe('light');
    expect(parseThemeId(undefined)).toBe('light');
    expect(parseThemeId(42)).toBe('light');
  });

  it('returns the light definition for unknown lookups', () => {
    expect(getThemeDefinition('light').id).toBe('light');
    expect(getThemeDefinition('dark').name).toBe('星际墨夜');
  });
});
