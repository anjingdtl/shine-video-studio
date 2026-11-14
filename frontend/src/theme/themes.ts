/**
 * Shine Glass UI — single six-theme registry.
 *
 * The interface theme only changes how the workbench looks. It never touches
 * the director-style engine, project data, generated media or production
 * parameters.
 */
export type ThemeId = 'light' | 'dark' | 'xianxia' | 'cyberpunk' | 'cinema' | 'comic';

export type ThemeAppearance = 'light' | 'dark';

export type ThemePreview = {
  background: string;
  surface: string;
  accent: string;
  text: string;
  onAccent: string;
};

export type ThemeDefinition = {
  id: ThemeId;
  name: string;
  description: string;
  appearance: ThemeAppearance;
  preview: ThemePreview;
};

export const THEMES: readonly ThemeDefinition[] = [
  {
    id: 'light',
    name: '星云银白',
    description: '银白玻璃与科技冷蓝，明亮房间里的默认工作台。',
    appearance: 'light',
    preview: { background: '#f1f5fb', surface: '#ffffff', accent: '#315cdf', text: '#1b273c', onAccent: '#ffffff' },
  },
  {
    id: 'dark',
    name: '星际墨夜',
    description: '深蓝黑玻璃与冷月蓝高亮，暗光环境长时间盯片。',
    appearance: 'dark',
    preview: { background: '#0d1424', surface: '#131d33', accent: '#8fafff', text: '#eef4ff', onAccent: '#0b1530' },
  },
  {
    id: 'xianxia',
    name: '云阙灵境',
    description: '灵玉青、云雾与月华金，安静克制的东方质感。',
    appearance: 'dark',
    preview: { background: '#0d262a', surface: '#123236', accent: '#7bdcc3', text: '#effdf9', onAccent: '#092925' },
  },
  {
    id: 'cyberpunk',
    name: '霓虹之城',
    description: '深蓝紫夜色、霓虹粉与电光青，未来都市气息。',
    appearance: 'dark',
    preview: { background: '#090b22', surface: '#101331', accent: '#55e8ff', text: '#f6f3ff', onAccent: '#081522' },
  },
  {
    id: 'cinema',
    name: '胶片时光',
    description: '复古琥珀、暖棕与柔和玫瑰，文艺电影质感。',
    appearance: 'dark',
    preview: { background: '#282320', surface: '#332c28', accent: '#d7a577', text: '#fff0e3', onAccent: '#2d211a' },
  },
  {
    id: 'comic',
    name: '次元物语',
    description: '明亮色块、漫画描边与高对比，轻快有精神。',
    appearance: 'light',
    preview: { background: '#fff4df', surface: '#ffffff', accent: '#b62e2b', text: '#243044', onAccent: '#ffffff' },
  },
];

export const THEME_IDS: readonly ThemeId[] = THEMES.map((theme) => theme.id);

/** Unknown, empty or corrupted values always fall back to the light theme. */
export function parseThemeId(value: unknown): ThemeId {
  return typeof value === 'string' && (THEME_IDS as readonly string[]).includes(value)
    ? value as ThemeId
    : 'light';
}

export function getThemeDefinition(id: ThemeId): ThemeDefinition {
  return THEMES.find((theme) => theme.id === id) ?? THEMES[0];
}
