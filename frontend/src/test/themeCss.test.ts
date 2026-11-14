import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { THEMES, THEME_IDS, type ThemeId } from '../theme/themes';

const readCss = (relative: string) => readFileSync(new URL(relative, import.meta.url), 'utf8');
const themesCss = readCss('../theme/themes.css');
const glassCss = readCss('../theme/glass.css');
const stylesCss = readCss('../styles.css');
const assetsCss = readCss('../asset-center/assets.css');
const indexHtml = readCss('../../index.html');

const THEME_BLOCKS: Record<Exclude<ThemeId, 'light'>, string> = {
  dark: blockFor('dark'),
  xianxia: blockFor('xianxia'),
  cyberpunk: blockFor('cyberpunk'),
  cinema: blockFor('cinema'),
  comic: blockFor('comic'),
};

function blockFor(id: string): string {
  const marker = `html[data-theme='${id}']`;
  const start = themesCss.indexOf(marker);
  if (start < 0) return '';
  const open = themesCss.indexOf('{', start);
  const end = themesCss.indexOf('\n}', open);
  return themesCss.slice(open + 1, end);
}

const REQUIRED_TOKENS = [
  '--ui-bg',
  '--ui-bg-elevated',
  '--ui-text',
  '--ui-text-muted',
  '--ui-border',
  '--ui-accent',
  '--ui-on-accent',
  '--ui-focus',
  '--ui-contrast',
  '--status-ok-bg',
  '--status-ok-text',
  '--status-warning-bg',
  '--status-warning-text',
  '--status-danger-bg',
  '--status-danger-text',
  '--status-info-bg',
  '--status-info-text',
  '--glass-panel',
  '--glass-sidebar',
  '--glass-topbar',
  '--glass-dialog',
  '--glass-border',
  '--glass-fallback',
  '--control-bg',
  '--control-text',
  '--control-disabled-text',
  '--table-header',
  '--media-stage',
  '--sidebar-bg',
  '--sidebar-ink',
  '--sidebar-accent',
];

function declarations(block: string): Map<string, string> {
  const map = new Map<string, string>();
  for (const match of block.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) {
    map.set(match[1], match[2].trim());
  }
  return map;
}

describe('six-theme CSS contract', () => {
  it('defines every required semantic token for all six themes', () => {
    const lightBlock = themesCss.slice(0, themesCss.indexOf("html[data-theme='dark']"));
    const light = declarations(lightBlock);
    for (const token of REQUIRED_TOKENS) expect(light.has(token), `light missing ${token}`).toBe(true);

    for (const [id, block] of Object.entries(THEME_BLOCKS)) {
      const values = declarations(block);
      expect(values.size, `${id} block is empty`).toBeGreaterThan(20);
      for (const token of REQUIRED_TOKENS) expect(values.has(token), `${id} missing ${token}`).toBe(true);
    }
  });

  it('keeps registry preview colours consistent with the CSS base tokens', () => {
    const lightBlock = themesCss.slice(0, themesCss.indexOf("html[data-theme='dark']"));
    const blocks: Record<string, string> = { light: lightBlock, ...THEME_BLOCKS };
    for (const theme of THEMES) {
      const values = declarations(blocks[theme.id]);
      expect(values.get('--ui-bg')?.toLowerCase()).toBe(theme.preview.background);
      expect(values.get('--ui-accent')?.toLowerCase()).toBe(theme.preview.accent);
      expect(values.get('--ui-text')?.toLowerCase()).toBe(theme.preview.text);
    }
  });

  it('does not rely on !important overrides', () => {
    expect(themesCss).not.toContain('!important');
    expect(glassCss).not.toContain('!important');
  });

  it('resolves every CSS variable used by the workbench', () => {
    const all = stylesCss + themesCss + glassCss + assetsCss;
    const defined = new Set<string>();
    for (const match of all.matchAll(/(--[a-z0-9-]+)\s*:/gi)) defined.add(match[1]);
    const missing: string[] = [];
    for (const match of all.matchAll(/var\((--[a-z0-9-]+)\s*(,)?/gi)) {
      if (!defined.has(match[1]) && !match[2]) missing.push(match[1]);
    }
    expect([...new Set(missing)]).toEqual([]);
  });

  it('ships reduced-motion, reduced-transparency and no-blur fallbacks', () => {
    expect(glassCss).toContain('prefers-reduced-motion');
    expect(glassCss).toContain('prefers-reduced-transparency');
    expect(glassCss).toContain('@supports not');
    expect(glassCss).toContain('-webkit-backdrop-filter');
  });

  it('defines the six theme cards and keeps media colour-true', () => {
    expect(glassCss).toContain('.theme-card');
    expect(glassCss).toContain('.theme-grid');
    expect(glassCss).toContain('filter: none');
    expect(glassCss).toContain('mix-blend-mode: normal');
  });

  it('keeps the first-paint bootstrap in sync with the registry', () => {
    for (const id of THEME_IDS) expect(indexHtml).toContain(`'${id}'`);
    expect(indexHtml).toContain("localStorage.getItem('shine-theme')");
    expect(indexHtml).toContain('dataset.theme');
  });
});
