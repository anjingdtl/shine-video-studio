/**
 * The single typed style catalog (docs/UI_SIMPLIFICATION_PLAN_20261102.md §6.3):
 * presets and per-dimension options share one authoritative source of
 * deterministic seven-string content. The UI only ever writes these exact
 * strings into the existing StyleBible structure; a preset ID is never H3
 * text. Options match historical text by exact string equality, so catalog
 * upgrades never rewrite historical custom content.
 */
import type { Style } from './api';
import { styleFields } from './constants';

export type StyleDimension = keyof Style;

export type StyleOption = { id: string; label: string; text: string };

export type StylePreset = { id: string; label: string; summary: string; values: Style };

export const STYLE_CATALOG_VERSION = '2026-11.1';

/* Deterministic content per dimension, shared between presets and options. */
const PHOTO_STABLE = '以中景和近景叙事，机位接近人物视线；镜头稳定，仅在叙事需要时缓慢推进，避免无动机环绕。';
const PHOTO_SLOW = '以中景、近景与空镜结合，机位多平视或轻微仰俯；缓慢推进与横移，禁止无动机环绕。';
const PHOTO_HANDHELD = '以中近景和特写为主，机位贴近视线，手持感轻微；仅在情绪高点缓慢推进，禁止无动机环绕。';
const PHOTO_ORBIT = '机位稳定，产品与人物并重，景别对比明确；环绕展示仅在本机工作流能力已验证时使用，否则以平移与固定机位替代。';

const BLOCKING_CLEAR_REVERSE = '保持人物空间关系和视线方向；对话正反打遵守同一动作轴线，走位服务于剧情。';
const BLOCKING_FOCUS = '单人聚焦与对角构图为主，对话正反打克制；保持轴线稳定，利用前景遮挡制造不安。';
const BLOCKING_PAIR = '人物同框与正反打并用；同场镜头保持人物方位一致，走位自然。';
const BLOCKING_ETIQUETTE = '人物站位符合身份礼序，正反打保守；保持轴线与人物朝向一致，走位含蓄。';

const COLOR_NATURAL = '自然中性色彩，肤色真实；同一场景保持主辅色和冷暖关系一致，避免过度饱和。';
const COLOR_WARM = '暖色调、柔和饱和度，肤色红润真实；场景主色统一，跨镜冷暖关系一致，避免过度饱和。';
const COLOR_COLD = '冷色低饱和，青蓝与灰绿主导，肤色偏冷但真实；跨镜保持同一冷基调，避免暖色突兀。';
const COLOR_BRIGHT = '明亮清透，主色鲜明、辅色克制；肤色明亮真实，跨镜色彩一致，避免杂色干扰。';
const LIGHT_SOFT = '使用有明确来源的自然柔光，保留阴影细节，跨镜主光方向一致，避免高光溢出。';
const LIGHT_BRIGHT = '明亮柔和的低反差光，日光感明确；保留阴影细节，无死黑。';
const LIGHT_LOWKEY = '低调布光，明暗比大但保留暗部细节；侧光和边缘光塑造立体感，主光方向跨镜一致。';
const LIGHT_SIDE = '使用有明确方向的侧光塑造人物立体感，反差较强但保留暗部细节，跨镜主光方向一致。';

const LENS_CLEAN = '干净写实，适度景深与清晰度；保留皮肤纹理，避免塑料肤质、过锐和突变颗粒。';
const LENS_SOFT = '柔和浅景深，轻微暖柔；皮肤质感真实，避免塑料肤质和过锐。';
const LENS_FILM = '柔和适度的胶片感，颗粒细腻；保留织物与皮肤细节，避免过锐。';
const LENS_SHOWCASE = '清晰展示质感，高清晰度、深景深；皮肤干净真实，避免塑料肤质。';
const EDIT_MEASURED = '均衡叙事，按动作和对白安排切点；保留必要停顿，默认硬切，不为追求节奏截断对白。';
const EDIT_CALM = '舒缓节奏，切点宽松，保留生活动作的完整过程；默认硬切。';
const EDIT_TIGHT = '紧凑叙事，对白切点稍密，保留情绪停顿；冲突段加快，默认硬切。';
const EDIT_SUSPENSE = '克制悬疑节奏，镜头保持完整，停顿蓄势；关键信息后留空拍，默认硬切。';

const SOUND_NATURAL = '使用项目指定语言和已确认音色；自然语速、清楚对白、适量环境声，音乐不遮盖人声。';
const SOUND_URBAN = '使用项目指定语言和已确认音色；对白清楚靠前，城市环境声适度，音乐只在情绪点进入。';
const SOUND_WARM = '使用项目指定语言和已确认音色；语速自然偏慢，生活环境声丰富，配乐轻。';
const SOUND_SUSPENSE = '使用项目指定语言和已确认音色；语速克制、对白简短，环境声克制且有层次，配乐低频铺底不盖人声。';
const SOUND_AD = '使用项目指定语言和已确认音色；语速明快、对白清楚，音乐明快不盖人声，环境声弱化。';

/** Per-dimension dropdown options: label for humans, text is what gets saved. */
export const STYLE_DIMENSION_OPTIONS: Record<StyleDimension, StyleOption[]> = {
  photography_language: [
    { id: 'photography.stable', label: '稳定叙事', text: PHOTO_STABLE },
    { id: 'photography.slow', label: '缓慢推进', text: PHOTO_SLOW },
    { id: 'photography.handheld', label: '克制手持', text: PHOTO_HANDHELD },
    { id: 'photography.orbit', label: '展示环绕（仅能力已验证时）', text: PHOTO_ORBIT },
  ],
  shot_blocking: [
    { id: 'blocking.clear_reverse', label: '清晰正反打', text: BLOCKING_CLEAR_REVERSE },
    { id: 'blocking.focus', label: '单人聚焦', text: BLOCKING_FOCUS },
    { id: 'blocking.pair', label: '双人同框', text: BLOCKING_PAIR },
    { id: 'blocking.etiquette', label: '礼序站位', text: BLOCKING_ETIQUETTE },
  ],
  color_palette: [
    { id: 'color.natural', label: '自然中性', text: COLOR_NATURAL },
    { id: 'color.warm', label: '温暖柔和', text: COLOR_WARM },
    { id: 'color.cold', label: '冷色低饱和', text: COLOR_COLD },
    { id: 'color.bright', label: '明亮清透', text: COLOR_BRIGHT },
  ],
  lighting_tone: [
    { id: 'light.soft', label: '自然柔光', text: LIGHT_SOFT },
    { id: 'light.bright', label: '明亮低反差', text: LIGHT_BRIGHT },
    { id: 'light.lowkey', label: '低调有层次', text: LIGHT_LOWKEY },
    { id: 'light.side', label: '戏剧侧光', text: LIGHT_SIDE },
  ],
  lens_texture: [
    { id: 'lens.clean', label: '干净写实', text: LENS_CLEAN },
    { id: 'lens.soft', label: '柔和浅景深', text: LENS_SOFT },
    { id: 'lens.film', label: '克制胶片感', text: LENS_FILM },
    { id: 'lens.showcase', label: '清晰展示', text: LENS_SHOWCASE },
  ],
  editing_rhythm: [
    { id: 'edit.measured', label: '均衡叙事', text: EDIT_MEASURED },
    { id: 'edit.calm', label: '舒缓', text: EDIT_CALM },
    { id: 'edit.tight', label: '紧凑', text: EDIT_TIGHT },
    { id: 'edit.suspense', label: '克制悬疑', text: EDIT_SUSPENSE },
  ],
  sound_performance: [
    { id: 'sound.natural', label: '自然对白', text: SOUND_NATURAL },
    { id: 'sound.urban', label: '都市对白', text: SOUND_URBAN },
    { id: 'sound.warm', label: '温暖轻松', text: SOUND_WARM },
    { id: 'sound.suspense', label: '克制悬疑', text: SOUND_SUSPENSE },
    { id: 'sound.ad', label: '广告讲述', text: SOUND_AD },
  ],
};

/** Six complete presets; each expands to seven non-empty deterministic strings. */
export const STYLE_PRESETS: StylePreset[] = [
  {
    id: 'preset.natural_realistic',
    label: '自然写实',
    summary: '贴近真实拍摄的叙事风格：机位稳定、光线自然、色彩中性，适合现实题材短剧。',
    values: {
      photography_language: PHOTO_STABLE,
      shot_blocking: BLOCKING_CLEAR_REVERSE,
      color_palette: COLOR_NATURAL,
      lighting_tone: LIGHT_SOFT,
      lens_texture: LENS_CLEAN,
      editing_rhythm: EDIT_MEASURED,
      sound_performance: SOUND_NATURAL,
    },
  },
  {
    id: 'preset.urban_drama',
    label: '都市短剧',
    summary: '节奏更快的都市情绪剧：对白靠前、切点稍密，夜景冷色与室内暖色对照。',
    values: {
      photography_language: PHOTO_HANDHELD,
      shot_blocking: BLOCKING_CLEAR_REVERSE,
      color_palette: COLOR_NATURAL,
      lighting_tone: LIGHT_BRIGHT,
      lens_texture: LENS_CLEAN,
      editing_rhythm: EDIT_TIGHT,
      sound_performance: SOUND_URBAN,
    },
  },
  {
    id: 'preset.warm_life',
    label: '温暖生活',
    summary: '明亮柔和的日常向风格：暖色、低反差、节奏舒缓，适合生活流和亲情题材。',
    values: {
      photography_language: PHOTO_STABLE,
      shot_blocking: BLOCKING_PAIR,
      color_palette: COLOR_WARM,
      lighting_tone: LIGHT_BRIGHT,
      lens_texture: LENS_SOFT,
      editing_rhythm: EDIT_CALM,
      sound_performance: SOUND_WARM,
    },
  },
  {
    id: 'preset.cold_suspense',
    label: '冷调悬疑',
    summary: '压抑克制的悬疑氛围：冷色低调、停顿蓄势，适合犯罪和悬疑短剧。',
    values: {
      photography_language: PHOTO_SLOW,
      shot_blocking: BLOCKING_FOCUS,
      color_palette: COLOR_COLD,
      lighting_tone: LIGHT_LOWKEY,
      lens_texture: LENS_CLEAN,
      editing_rhythm: EDIT_SUSPENSE,
      sound_performance: SOUND_SUSPENSE,
    },
  },
  {
    id: 'preset.period_drama',
    label: '古风剧情',
    summary: '含蓄典雅的古装叙事：礼序站位、古雅配色、含蓄配乐，适合古装短剧。',
    values: {
      photography_language: PHOTO_STABLE,
      shot_blocking: BLOCKING_ETIQUETTE,
      color_palette: COLOR_NATURAL,
      lighting_tone: LIGHT_SOFT,
      lens_texture: LENS_FILM,
      editing_rhythm: EDIT_MEASURED,
      sound_performance: SOUND_NATURAL,
    },
  },
  {
    id: 'preset.ad_showcase',
    label: '广告展示',
    summary: '清楚明亮的产品向风格：高调明亮、切点有力、对白明快，适合展示类内容。',
    values: {
      photography_language: PHOTO_ORBIT,
      shot_blocking: BLOCKING_FOCUS,
      color_palette: COLOR_BRIGHT,
      lighting_tone: LIGHT_BRIGHT,
      lens_texture: LENS_SHOWCASE,
      editing_rhythm: EDIT_TIGHT,
      sound_performance: SOUND_AD,
    },
  },
];

/** FNV-1a over the canonical catalog JSON: changes when any text/option changes. */
export function styleCatalogHash(): string {
  const canonical = JSON.stringify({
    version: STYLE_CATALOG_VERSION,
    options: STYLE_DIMENSION_OPTIONS,
    presets: STYLE_PRESETS,
  });
  let hash = 0x811c9dc5;
  for (let index = 0; index < canonical.length; index += 1) {
    hash ^= canonical.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

/** Exact-match the option whose deterministic text equals `text`. */
export function matchStyleOption(field: StyleDimension, text: string): StyleOption | null {
  if (!text) return null;
  return STYLE_DIMENSION_OPTIONS[field].find((option) => option.text === text) ?? null;
}

/** Exact-match a complete preset; null means the draft is a custom style. */
export function presetForStyle(style: Style): StylePreset | null {
  return STYLE_PRESETS.find((preset) =>
    styleFields.every(([field]) => preset.values[field] === style[field])) ?? null;
}
