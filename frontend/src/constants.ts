import type { Style } from './api';

export const emptyStyle: Style = {
  photography_language: '', shot_blocking: '', color_palette: '', lighting_tone: '',
  lens_texture: '', editing_rhythm: '', sound_performance: '',
};

export const styleFields: Array<[keyof Style, string, string]> = [
  ['photography_language', '摄影语言', '景别、机位高度、镜头运动与禁用项'],
  ['shot_blocking', '镜头调度', '人物走位、正反打与空间关系'],
  ['color_palette', '色彩风格', '主辅色、冷暖关系与肤色保护'],
  ['lighting_tone', '影调与灯光', '反差、黑位、主光方向与阴影细节'],
  ['lens_texture', '镜头质感', '焦段、景深、颗粒与锐化边界'],
  ['editing_rhythm', '剪辑与节奏', '镜头时长、对白停顿与切换规则'],
  ['sound_performance', '声音表演', '语言、语速、音色及环境声规则'],
];

export const statusLabels: Record<string, string> = {
  PLANNED: '等待前置镜头', WAITING_ASSET: '等待素材', READY: '准备中', QUEUED: '队列中',
  SUBMITTING: '提交中', RUNNING: '运行中', QC_PENDING: '检查中', PASSED: '通过',
  REPAIR_PENDING: '等待修复', NEEDS_REVIEW: '需要人工复核', FAILED: '失败',
};

export const roleOptions = [
  ['character', '人物'], ['costume', '服装'], ['scene', '场景'], ['prop', '道具'],
  ['voice', '声音'], ['style_reference', '风格参考'], ['generic', '通用'],
] as const;

export const useAsOptions = [
  ['subject', 'Subject 人物主体'], ['picture', 'Picture 图像参考'],
  ['video', 'Video 视频参考'], ['audio', 'Audio 音频参考'],
] as const;
