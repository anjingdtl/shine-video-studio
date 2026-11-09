import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Clapperboard, Info, Layers, Play, ShieldCheck, Square, Loader2, Wand2 } from 'lucide-react';
import { api, json, type Asset, type Job, type ProductionSeries, type Shot, type SystemStatus, type WorkflowProfile } from '../api';
import { continuityLabels, failureLabels, finalizationLabels, label, statusLabels } from '../constants';

type AutoBinding = { asset_id: string; use_as: string; role: string | null; frame_role: string | null; ordinal: number };

type Props = {
  projectId: string;
  shots: Shot[];
  assets: Asset[];
  /** 全局任务数据（3 秒轮询）：进度卡的当前镜百分比来自最新 attempt.progress。 */
  jobs: Job[];
  workflowProfiles: WorkflowProfile[];
  system: SystemStatus | null;
  busy: boolean;
  demoSelected?: boolean;
  onSelectReal?: () => void;
  reviewWidth: string;
  reviewHeight: string;
  onStarted: () => void;
  onMessage: (message: string, kind?: 'success' | 'error') => void;
  /** 保存某镜的完整引用绑定（一键开拍用它自动补齐场景图/音色等参考）。 */
  onSaveBindings: (shotId: string, bindings: AutoBinding[]) => Promise<void>;
  /** 模式入口动作：enable_real 打开启用向导，check_fix 打开检查修复。 */
  onModeAction: (action: 'enable_real' | 'check_fix') => void;
  /** 缺生成方案/宽高映射时，引导回基础配置。 */
  onGoSetup?: () => void;
};

const statusText: Record<string, string> = {
  QUEUED: '排队等待制作',
  RUNNING: '制作中',
  PAUSED: '已暂停',
  PAUSE_REQUESTED: '正在暂停',
  COMPLETED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  NEEDS_REVIEW: '需要复核',
  PENDING: '待生产',
};

const itemText: Record<string, string> = {
  PENDING: '待生产',
  QUEUED: '已入队',
  RUNNING: '渲染中',
  PASSED: '已通过',
  FAILED: '失败',
  BLOCKED: '已阻断',
  CANCELLED: '已取消',
};

/** H3 的实测规格：24fps、宽高 32 的倍数、单镜 4–15 秒。预设只给已经在
 *  本项目验收过的画幅，自定义仍受同一组约束。 */
const FRAME_PRESETS = [
  { id: 'default', label: '按方案默认（不改分辨率）', width: 0, height: 0 },
  { id: 'portrait', label: '竖屏 9:16 · 768×1344（手机短剧）', width: 768, height: 1344 },
  { id: 'landscape', label: '横屏 16:9 · 1344×768（横屏成片）', width: 1344, height: 768 },
  { id: 'square', label: '方形 1:1 · 960×960', width: 960, height: 960 },
  { id: 'custom', label: '自定义（宽高需为 32 的倍数）', width: 0, height: 0 },
] as const;

const CLIP_CHOICES = ['', '4', '5', '6', '8', '10', '12', '15'];

/** 拍摄质量档：按方案名确定性匹配（pdd8=步数加速、upscale=画面放大），
 *  每档明说耗时代价，用户自己权衡。 */
type QualityTier = 'fast' | 'native' | 'upscale';
const QUALITY_TIERS: Array<{ id: QualityTier; label: string; note: string; match: (name: string) => boolean }> = [
  { id: 'fast', label: '快速 · 8步加速', note: '8 步加速生成，速度最快（默认）', match: (n) => /pdd8/i.test(n) && !/upscale/i.test(n) },
  { id: 'native', label: '高质量 · 原生20步', note: '原生 20 步生成，画面细节与稳定性更好，耗时约为快速档的 2~3 倍', match: (n) => !/pdd8/i.test(n) && !/upscale/i.test(n) },
  { id: 'upscale', label: '超清 · 加速+画面放大', note: '8 步加速生成后再高清放大，细节最高；生成+放大两段耗时，整片时间最长', match: (n) => /pdd8/i.test(n) && /upscale/i.test(n) },
];

function tierOfProfile(name: string): QualityTier | null {
  if (/portrait|竖屏/i.test(name)) return null; // 竖屏变体不参与一键质量匹配
  return QUALITY_TIERS.find((tier) => tier.match(name))?.id ?? null;
}

const FAMILY_ORDER: Record<string, number> = { subject: 0, picture: 1, video: 2, audio: 3 };

/** The reference labels this shot will compile to: the same family order and
 *  character order the server uses when it builds the manifest. */
function referenceLabels(shot: Shot) {
  const characters = [...new Set(shot.spec.characters ?? [])];
  const rank = new Map(characters.map((name, index) => [name, index]));
  const ordered = [...shot.bindings].sort((a, b) => {
    const family = (FAMILY_ORDER[a.use_as] ?? 9) - (FAMILY_ORDER[b.use_as] ?? 9);
    if (family !== 0) return family;
    if (a.use_as === 'subject' && b.use_as === 'subject') {
      const left = rank.get(a.entity_id ?? '') ?? 999;
      const right = rank.get(b.entity_id ?? '') ?? 999;
      if (left !== right) return left - right;
    }
    return a.ordinal - b.ordinal || a.asset_id.localeCompare(b.asset_id);
  });
  let image = 0;
  let subject = 0;
  const rows: Array<{ label: string; name: string; use_as: string; asset_id: string }> = [];
  for (const binding of ordered) {
    if (binding.use_as === 'subject' || binding.use_as === 'picture') image += 1;
    if (binding.use_as === 'subject') {
      subject += 1;
      rows.push({ label: `<Subject ${subject}>`, name: binding.entity_id || binding.filename, use_as: binding.use_as, asset_id: binding.asset_id });
    } else if (binding.use_as === 'picture') {
      rows.push({ label: `<Picture ${image}>`, name: binding.entity_id || binding.filename, use_as: binding.use_as, asset_id: binding.asset_id });
    } else if (binding.use_as === 'audio') {
      rows.push({ label: '音色参考', name: binding.entity_id || binding.filename, use_as: binding.use_as, asset_id: binding.asset_id });
    }
  }
  return rows;
}

function isMultipleOf32(value: number): boolean {
  return Number.isInteger(value) && value >= 256 && value <= 2048 && value % 32 === 0;
}

/** 从方案的 parameter_map 读出它声明的参考槽位（如 reference_picture_1 → picture×1）。
 *  一键开拍按这份声明自动补齐，不会多绑也不会静默缺绑。 */
function declaredSlots(profile: WorkflowProfile): { subject: number; picture: number; audio: number } {
  const slots = { subject: 0, picture: 0, audio: 0 };
  for (const key of Object.keys(profile.parameter_map ?? {})) {
    const match = key.match(/^reference_(subject|picture|audio)_(\d+)$/);
    if (match) slots[match[1] as 'subject' | 'picture' | 'audio'] = Math.max(slots[match[1] as 'subject' | 'picture' | 'audio'], Number(match[2]));
  }
  return slots;
}

/** 为一个镜头补齐方案声明的参考槽位：已绑定保留；缺的场景图/音色按已确认素材的
 *  固定顺序补足；人物主体缺位不自动编造（那是分镜/对照的职责），如实报缺。 */
function autoAdaptBindings(shot: Shot, profile: WorkflowProfile, assets: Asset[]): { bindings: AutoBinding[]; added: string[]; missing: string[] } {
  const slots = declaredSlots(profile);
  // 无对白的镜头不自动绑人声参考：音频参考只服务台词，而且真声样本会被 H3 当作
  // 语音线索、无视「不要人声」的合同（2026-11-04 实测出现外语呢喃）。提交层会
  // 剪掉未用的音频装载节点，方案声明 audio 也不再强制。
  if (!(shot.spec.dialogue ?? []).length) slots.audio = 0;
  const current = shot.bindings
    .map((b, index) => ({ asset_id: b.asset_id, use_as: b.use_as, role: b.role ?? null, frame_role: b.frame_role ?? null, ordinal: index }));
  const used = new Set(current.map((b) => b.asset_id));
  const count = (family: string) => current.filter((b) => b.use_as === family).length;
  const added: string[] = [];
  const missing: string[] = [];
  const candidates = (family: 'picture' | 'audio') => assets
    .filter((a) => a.locked && !used.has(a.id) && (family === 'picture'
      ? a.kind === 'image' && (a.role === 'scene' || a.role === 'prop' || !a.role || a.role === 'character')
      : a.kind === 'audio'))
    .sort((x, y) => (x.role === (family === 'picture' ? 'scene' : 'voice') ? -1 : 0) - (y.role === (family === 'picture' ? 'scene' : 'voice') ? -1 : 0) || x.filename.localeCompare(y.filename));
  for (const family of ['picture', 'audio'] as const) {
    const pool = candidates(family);
    let have = count(family);
    for (let i = 0; have < slots[family] && i < pool.length; i += 1) {
      const candidate = pool[i];
      current.push({ asset_id: candidate.id, use_as: family, role: candidate.role ?? null, frame_role: null, ordinal: current.length });
      used.add(candidate.id);
      have += 1;
      added.push(`${shot.name}：${candidate.filename}（${family === 'picture' ? '画面参考' : '音色参考'}）`);
    }
    for (; have < slots[family]; have += 1) missing.push(family === 'picture' ? '场景/画面参考图' : '音色参考音频');
  }
  const subjectShort = Math.max(0, slots.subject - count('subject'));
  for (let i = 0; i < subjectShort; i += 1) missing.push(`${shot.name} 的人物主体`);
  return { bindings: current, added, missing };
}

export default function ProductionSeriesPanel({
  projectId, shots, assets, jobs, workflowProfiles, system, busy, reviewWidth, reviewHeight,
  onStarted, onMessage, onSaveBindings, onModeAction, onGoSetup, demoSelected = false, onSelectReal,
}: Props) {
  const [series, setSeries] = useState<ProductionSeries[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [profileId, setProfileId] = useState('');
  const [scope, setScope] = useState<'full' | 'basic'>('full');
  const [editIntents, setEditIntents] = useState<Array<{ id: string; created_at: string }>>([]);
  const [editIntentId, setEditIntentId] = useState('');
  const [framePreset, setFramePreset] = useState<string>('default');
  const [customWidth, setCustomWidth] = useState('768');
  const [customHeight, setCustomHeight] = useState('1344');
  const [clipSeconds, setClipSeconds] = useState('');
  const [working, setWorking] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [quality, setQuality] = useState<QualityTier>('fast');
  /** 制作批次会越积越多：默认只展示最近 3 个，历史按需展开。 */
  const [seriesLimit, setSeriesLimit] = useState(3);

  /** 一键为本作品登记生成方案：与启用向导第 6 阶段同一组接口（固定工作流登记 +
   *  运行配置），在本页原地完成，不再让用户跳去基础配置跑整个向导。
   *  本机服务验证是机器级的，已激活的环境无需按作品重跑。 */
  async function registerProfiles() {
    if (registering) return;
    setRegistering(true);
    try {
      await api('/setup/profiles', json('POST', { project_id: projectId }));
      await api('/setup/configuration', json('POST', { project_id: projectId }));
      onMessage('本作品的生成方案已登记完成，正在刷新可选项…', 'success');
      await onStarted();
    } catch (error) {
      onMessage(`生成方案登记失败：${String(error)}。可点「检查生成方案」打开向导查看具体原因。`, 'error');
    } finally {
      setRegistering(false);
    }
  }

  const reload = useCallback(async () => {
    const rows = await api<ProductionSeries[]>(`/projects/${projectId}/production-series`);
    setSeries(rows);
  }, [projectId]);

  useEffect(() => { reload().catch(() => setSeries([])); }, [reload, projectId, shots.length]);

  useEffect(() => {
    api<Array<{ id: string; kind: string; stale: boolean; created_at: string }>>(`/projects/${projectId}/film-artifacts`)
      .then((rows) => setEditIntents(
        rows.filter((row) => row.kind === 'EditIntentPlan' && !row.stale)
          .map(({ id, created_at }) => ({ id, created_at })),
      ))
      .catch(() => setEditIntents([]));
  }, [projectId, shots.length]);

  const ref2vaProfiles = workflowProfiles.filter((profile) => profile.mode.toLowerCase() === 'ref2va');
  // Only one validated Ref2VA profile exists: preselect it instead of making
  // the user pick from one option; multiple options keep the manual choice.
  useEffect(() => {
    if (ref2vaProfiles.length === 1 && !profileId) setProfileId(ref2vaProfiles[0].id);
  }, [ref2vaProfiles, profileId]);
  const activeSeries = series.find((row) => ['QUEUED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED'].includes(row.status));

  /** 制作期间每 5 秒刷新序列本身，进度卡才有最新的分镜状态。 */
  useEffect(() => {
    if (!activeSeries) return;
    const timer = window.setInterval(() => { reload().catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [activeSeries?.id, reload]);

  /** 计时器每秒走一格；起止时间来自服务端（attempt.submit_intent_at / 系列最早 job 的
   *  created_at），刷新页面也不会清零。 */
  const [timerTick, setTimerTick] = useState(0);
  useEffect(() => {
    if (!activeSeries) return;
    const timer = window.setInterval(() => setTimerTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [activeSeries?.id]);

  /** 实时进度：总进度按「已完成镜 + 当前镜的 attempt 百分比」计算，阶段名跟随真实状态。 */
  const liveProgress = useMemo(() => {
    void timerTick; // 计时显示依赖当前时刻，秒级重渲染由 ticker 驱动
    if (!activeSeries) return null;
    const items = activeSeries.items;
    const total = activeSeries.shot_count || items.length;
    const completed = items.filter((item) => item.status === 'PASSED').length;
    const current = items.find((item) => !['PASSED', 'CANCELLED', 'FAILED', 'BLOCKED'].includes(item.status));
    const job = current?.job_id ? jobs.find((row) => row.id === current.job_id) : undefined;
    const attempt = job?.attempts.reduce<Job['attempts'][number] | null>((latest, row) =>
      !latest || row.attempt_no > latest.attempt_no ? row : latest, null);
    let phase: string;
    let shotFraction = 0;
    if (['PAUSED', 'PAUSE_REQUESTED'].includes(activeSeries.status)) {
      phase = '已暂停';
    } else if (job?.status === 'SUBMITTING' || attempt?.status === 'SUBMIT_INTENT') {
      phase = '正在启动本机渲染引擎';
    } else if (job?.status === 'RUNNING') {
      if (attempt?.progress != null && attempt.progress > 0) {
        phase = `正在生成 · ${attempt.progress}%`;
        shotFraction = attempt.progress / 100;
      } else {
        phase = '正在生成（本机渲染中，一段约需几分钟）';
      }
    } else if (job?.status === 'QC_PENDING') {
      phase = '自动质检中';
    } else if (job?.status === 'NEEDS_REVIEW') {
      phase = '等待人工复核';
    } else if (current?.status === 'RUNNING') {
      phase = '正在生成（本机渲染中，一段约需几分钟）';
    } else if (current?.status === 'QUEUED') {
      const anyRendering = jobs.some((row) => ['SUBMITTING', 'RUNNING'].includes(row.status));
      phase = anyRendering ? '排队等待中（前一镜完成后自动开始）' : '正在启动本机渲染引擎（首次启动约需一两分钟）';
    } else if (current?.status === 'BLOCKED') {
      phase = '有镜头需要处理，后续镜头不受影响';
    } else if (!current && completed < total) {
      phase = '正在整理成片';
    } else {
      phase = '即将完成';
    }
    const overall = total > 0 ? Math.min(100, ((completed + shotFraction) / total) * 100) : 0;
    // 计时：总耗时从本系列最早的任务创建时刻起算；本镜耗时从其最新 attempt 的
    // 提交意图时刻起算（都是服务端 UTC 时间，刷新不清零）。后端时间戳无时区后缀，
    // 必须显式按 UTC 解析，否则本地时区（UTC+8）会造成 8 小时偏差。
    const utcMs = (value: string | null | undefined) => {
      if (!value) return null;
      // The API serialises naive UTC datetimes with or without the 'T'
      // separator and never with a zone suffix; parsing them as local time
      // inflates the elapsed time by the local UTC offset (8 h here).
      const normalised = value.includes('T') ? value : value.replace(' ', 'T');
      const parsed = new Date(normalised.endsWith('Z') ? normalised : normalised + 'Z').getTime();
      return Number.isFinite(parsed) ? parsed : null;
    };
    const now = Date.now();
    const seriesJobIds = new Set(items.map((item) => item.job_id).filter(Boolean) as string[]);
    const seriesJobs = jobs.filter((row) => seriesJobIds.has(row.id));
    const startCandidates = seriesJobs
      .map((row) => utcMs(row.created_at)).filter((ms): ms is number => ms != null);
    const totalStartS = startCandidates.length ? Math.min(...startCandidates) : null;
    const shotStartS = utcMs(attempt?.submit_intent_at);
    const totalElapsedS = totalStartS != null ? Math.max(0, Math.floor((now - totalStartS) / 1000)) : null;
    const shotElapsedS = shotStartS != null ? Math.max(0, Math.floor((now - shotStartS) / 1000)) : null;
    return { total, completed, current, phase, overall, items, totalElapsedS, shotElapsedS };
  }, [activeSeries, jobs, timerTick]);
  // A shot that is already being produced must not be queued a second time.
  const busyShotIds = new Set(
    series
      .filter((row) => ['QUEUED', 'RUNNING', 'PAUSED', 'PAUSE_REQUESTED'].includes(row.status))
      .flatMap((row) => row.items.map((item) => item.shot_id)),
  );
  const selectedBusy = selected.filter((id) => busyShotIds.has(id));
  const activeProfile = ref2vaProfiles.find((profile) => profile.id === profileId);
  const profileMapsSize = !!activeProfile
    && 'width' in (activeProfile.parameter_map ?? {}) && 'height' in (activeProfile.parameter_map ?? {});

  const preset = FRAME_PRESETS.find((row) => row.id === framePreset) ?? FRAME_PRESETS[0];
  const requestedSize = framePreset === 'custom'
    ? { width: Number(customWidth), height: Number(customHeight) }
    : { width: preset.width, height: preset.height };
  const wantsSize = requestedSize.width > 0 || requestedSize.height > 0;
  const sizeValid = !wantsSize || (isMultipleOf32(requestedSize.width) && isMultipleOf32(requestedSize.height));
  const sizeBlocked = wantsSize && !profileMapsSize;

  /** 成片长度：选中的镜头（未选时全部）按计划时长或统一时长估算。 */
  const lengthPreview = useMemo(() => {
    const rows = shots.filter((shot) => selected.length === 0 || selected.includes(shot.id));
    const each = clipSeconds ? Number(clipSeconds) : 0;
    const total = rows.reduce((sum, shot) => sum + (each || Number(shot.spec.duration_target_s ?? 0)), 0);
    return { count: rows.length, total, unified: !!each };
  }, [shots, selected, clipSeconds]);

  const identityRows = useMemo(() => {
    const rows = shots.filter((shot) => selected.length === 0 || selected.includes(shot.id));
    return rows.slice(0, 4).map((shot) => {
      const refs = referenceLabels(shot);
      const subjects = refs.filter((row) => row.use_as === 'subject');
      const characters = [...new Set(shot.spec.characters ?? [])];
      const unbound = characters.filter((name) => !subjects.some((row) => row.name === name));
      return { shot, refs, characters, unbound };
    });
  }, [shots, selected]);
  const unboundCount = identityRows.reduce((sum, row) => sum + row.unbound.length, 0);

  function toggle(shotId: string) {
    setSelected((current) => current.includes(shotId)
      ? current.filter((id) => id !== shotId)
      : [...current, shotId]);
  }

  /** 选择全部可制作镜头（排除已在生产中的），并按项目镜头顺序提交。 */
  function selectAllProducible() {
    setSelected(shots.filter((shot) => !busyShotIds.has(shot.id)).map((shot) => shot.id));
  }

  async function start(options?: { shotIds?: string[]; profileOverride?: string }) {
    const effectiveProfile = options?.profileOverride ?? profileId;
    const effectiveSelected = options?.shotIds ?? selected;
    if (demoSelected || system?.production_mode?.mode !== 'READY' || working || busy) return;
    if (!effectiveProfile) { onMessage('当前没有可用的生成方案，请先在基础配置里完成登记。', 'error'); return; }
    if (effectiveSelected.length === 0) { onMessage('请至少选择一个镜头。', 'error'); return; }
    const busyInSelection = effectiveSelected.filter((id) => busyShotIds.has(id));
    if (busyInSelection.length > 0) {
      onMessage('选中的镜头已经在生产队列中，请勿重复提交。', 'error');
      return;
    }
    if (!sizeValid) {
      onMessage('自定义画幅需要 256–2048 之间、且为 32 的倍数的宽高（H3 规范）。', 'error');
      return;
    }
    if (sizeBlocked) {
      onMessage('当前生成方案没有接入宽高参数：请到基础配置 → 模型与生成方案，点「补齐宽高参数」后再选画幅。', 'error');
      return;
    }
    if (clipSeconds) {
      const tooTight = shots
        .filter((shot) => effectiveSelected.includes(shot.id))
        .filter((shot) => Math.max(0, ...(shot.spec.dialogue ?? []).map((line) => line.end_s)) + 0.2 > Number(clipSeconds))
        .map((shot) => shot.name);
      if (tooTight.length > 0) {
        onMessage(`这些镜头的对白装不进 ${clipSeconds} 秒：${tooTight.join('、')}。请改回「按分镜计划」或加长片段。`, 'error');
        return;
      }
      if (!window.confirm(`把选中的 ${effectiveSelected.length} 个镜头统一改为 ${clipSeconds} 秒？\n这会重新编译这些镜头，已生成的同镜成片会失效。`)) return;
    }
    setWorking(true);
    try {
      // 按项目镜头顺序提交，不按勾选顺序改变故事顺序。
      const shot_ids = shots
        .map((shot) => shot.id)
        .filter((id) => effectiveSelected.includes(id));
      const body: Record<string, unknown> = {
        shot_ids,
        provider: 'comfyui',
        workflow_profile_id: effectiveProfile,
        self_review_scope: scope,
      };
      if (scope === 'basic') {
        body.basic_review_resolution = { width: Number(reviewWidth), height: Number(reviewHeight) };
      }
      if (wantsSize || clipSeconds) {
        body.output = {
          ...(wantsSize ? { width: requestedSize.width, height: requestedSize.height } : {}),
          clip_seconds: clipSeconds ? Number(clipSeconds) : null,
        };
      }
      if (editIntentId) {
        body.edit_intent_artifact_id = editIntentId;
      }
      await api(`/projects/${projectId}/production-series`, json('POST', body));
      setSelected([]);
      onMessage(`已开始连续制作；本机会依次生成 ${shot_ids.length} 个镜头并自动整理成片。小模型审片和对白识别只提供建议。`, 'success');
      await reload();
      onStarted();
    } catch (error) {
      onMessage(String(error), 'error');
    } finally {
      setWorking(false);
    }
  }

  /** 一键开拍：自动选方案 → 按方案声明的槽位自动补齐每镜参考 → 自动勾选全部可制作镜头 → 提交。
   *  用户只点一次；每步自动做了什么都会说明，缺什么就明说，绝不静默跳过。 */
  async function oneClickStart() {
    if (working || busy) return;
    if (demoSelected) { onMessage('当前是演示状态：点上方「切回真实制作」后再一键开拍。', 'error'); return; }
    if (mode === null) { onMessage('正在确认本机制作环境（约需几秒），稍等两三秒再点一次。', 'error'); return; }
    if (mode !== 'READY') { onMessage(system?.production_mode?.detail ?? '制作环境未就绪：请先在顶部完成「启用真实制作」。', 'error'); return; }
    if (activeSeries) { onMessage('已有未结束的生产序列：等它完成，或先在下方处理该序列。', 'error'); return; }
    // 1) 自动选方案：先按所选拍摄质量档过滤，再优先宽高已接通的标准方案。
    const tierPool = ref2vaProfiles.filter((p) => tierOfProfile(p.name) === quality);
    if (tierPool.length === 0) {
      onMessage('本作品还没有该质量档的生成方案：点下方「为本作品一键登记生成方案」补齐（登记不会改动已有方案）。', 'error');
      return;
    }
    let target = profileId;
    if (!target || tierOfProfile(ref2vaProfiles.find((p) => p.id === target)?.name ?? '') !== quality) {
      const sized = tierPool.filter((p) => 'width' in (p.parameter_map ?? {}) && 'height' in (p.parameter_map ?? {}));
      const pool = sized.length ? sized : tierPool;
      target = pool[0]?.id ?? '';
      if (target) setProfileId(target);
    }
    const profile = ref2vaProfiles.find((p) => p.id === target);
    if (!profile) { onMessage('本机还没有可用的生成方案：先点下方「为本作品一键登记生成方案」，或去基础配置完成安装。', 'error'); return; }
    // 2) 自动勾选全部可制作镜头。
    const shotIds = shots.filter((shot) => !busyShotIds.has(shot.id)).map((shot) => shot.id);
    if (shotIds.length === 0) { onMessage('还没有可制作的镜头：先在上方完成并应用分镜。', 'error'); return; }
    setSelected(shotIds);
    // 3) 按方案槽位自动补齐参考绑定（场景图/音色等），人物主体缺位如实报缺。
    const adapted: string[] = [];
    const missingAll: string[] = [];
    setWorking(true);
    try {
      for (const shot of shots.filter((row) => shotIds.includes(row.id))) {
        const plan = autoAdaptBindings(shot, profile, assets);
        adapted.push(...plan.added);
        missingAll.push(...plan.missing);
        if (plan.added.length > 0) await onSaveBindings(shot.id, plan.bindings);
      }
    } catch (error) {
      setWorking(false);
      onMessage(`自动补齐参考时失败：${String(error)}。可在下方高级设置里手工调整后重试。`, 'error');
      return;
    }
    if (missingAll.length > 0) {
      setWorking(false);
      onMessage(`自动适配还缺：${[...new Set(missingAll)].join('、')}。到「镜头与素材」页的素材准备分区上传并确认对应素材后，再点一次一键开拍即可。`, 'error');
      return;
    }
    // 4) 提交生产序列（沿用 start 的全部校验）。
    await start({ shotIds, profileOverride: target });
    if (adapted.length > 0) {
      onMessage(`一键开拍已自动绑定 ${adapted.length} 项参考（${adapted.slice(0, 3).join('；')}${adapted.length > 3 ? '…' : ''}），方案「${profile.name}」。`, 'success');
    }
  }

  async function act(seriesId: string, action: 'cancel' | 'pause' | 'resume') {
    setWorking(true);
    try {
      await api(`/production-series/${seriesId}/${action}`, json('POST', {}));
      await reload();
      onMessage(`已${{ cancel: '取消', pause: '暂停', resume: '恢复' }[action]}该生产序列。`, 'success');
    } catch (error) {
      onMessage(String(error), 'error');
    } finally {
      setWorking(false);
    }
  }

  // 单一权威来源：后端 production_mode。READY 才允许开始新的连续制作；
  // 渲染器空闲停机不影响 READY（受管渲染器按需启动）。
  const mode = system?.production_mode?.mode ?? null;
  const canStart = !demoSelected && mode === 'READY' && !activeSeries && selected.length > 0
    && selectedBusy.length === 0 && !!profileId && sizeValid && !sizeBlocked;
  // 「硬阻滞」只保留会让生成根本无法发生（或必然生成错误结果）的事：
  // 无分镜、没勾选、无生成方案、画幅不合法/未接入、人物没参考图、环境未就绪。
  // 调度与瞬时状态（已在生产中、正在处理）降为下方「提示」，不再阻断样式恐吓。
  // 「已完成」优先于「还不能开始」：当前范围内每一镜都有真实产物时，排产区应显示
  // 已完成并允许重拍/补拍，而不是继续解释"为什么不能开始"。
  const producedShotIds = new Set(
    jobs
      .filter((job) => job.artifact_id && !job.simulated
        && ['PASSED', 'DELIVERED'].includes(job.status))
      .map((job) => job.shot_id),
  );
  const scopeAllProduced = shots.length > 0 && shots.every((shot) => producedShotIds.has(shot.id));
  const blockers = [
    ...(shots.length === 0 ? ['还没有应用分镜，制作列表为 0 镜；先完成并应用分镜。'] : []),
    ...(shots.length > 0 && selected.length === 0 && !scopeAllProduced
      ? ['还没有勾选镜头；一键开拍会自动勾选。'] : []),
    ...(!profileId && !scopeAllProduced ? ['还没有生成方案；点下方按钮一键登记。'] : []),
    ...(sizeBlocked ? ['当前方案缺少宽高映射；去基础配置补齐，或改回默认画幅。'] : []),
    ...(!sizeValid ? ['自定义画幅必须是 256–2048 之间、32 的倍数（H3 规范）。'] : []),
    ...(unboundCount > 0 && !scopeAllProduced
      ? [`有 ${unboundCount} 个角色未绑定参考图，提交会被拦下。`] : []),
    ...(demoSelected ? ['当前选择演示，请先切回真实制作。'] : []),
    ...(mode !== 'READY' ? [system?.production_mode?.detail ?? '尚未连接本机服务，无法确认制作环境。'] : []),
  ];
  // 提示（不阻断开拍判断之外的事实）：展示为非阻断的黄色说明，不进红色阻断卡。
  const hints = [
    ...(scopeAllProduced
      ? [`本范围 ${shots.length} 镜已全部出片；要重拍或补拍，勾选镜头后会按当前方案重新生成。`] : []),
    ...(activeSeries ? ['已有未结束的生产序列在跑，可先在下方查看进度；新序列要等它收尾后才能开拍。'] : []),
    ...(selectedBusy.length > 0 ? [`选中的 ${selectedBusy.length} 个镜头已在生产中，取消它们的勾选即可提交其余镜头。`] : []),
  ];

  // 制作已在进行（本面板的序列或任一任务在跑）时，不再解释“为什么不能开始”，
  // 状态由上方的进度卡负责；这份清单只服务“想开拍但开不了”的时刻。
  const productionLive = !!activeSeries
    || jobs.some((job) => ['SUBMITTING', 'RUNNING', 'QC_PENDING'].includes(job.status));

  return <div className="card section-card series-card" id="production-series">
    <div className="section-heading compact">
      <div>
        <div className="section-kicker">③ 排产制作</div>
        <h2>开始连续制作</h2>
        <p>本机连续生成并自动成片；AI 建议仅供参考。</p>
      </div>
      <div className="section-icon plum"><Layers size={17} /></div>
    </div>

    {mode !== 'READY' && mode !== 'RUNNING' && <div className="blocked-callout">
      <AlertTriangle size={15} />
      <span>{system?.production_mode?.detail ?? '暂无法确认制作模式。'}</span>
      {system?.production_mode?.action === 'enable_real'
        && <button type="button" className="text-button" onClick={() => onModeAction('enable_real')}>启用真实制作</button>}
      {system?.production_mode?.action === 'check_fix'
        && <button type="button" className="text-button" onClick={() => onModeAction('check_fix')}>检查并修复</button>}
    </div>}

    {demoSelected && <div className="blocked-callout">
      <span>当前选择演示；连续制作只支持真实 H3，演示态不能启动。</span>
      <button type="button" className="text-button" onClick={onSelectReal}>切回真实制作</button>
    </div>}

    <div className="one-click-card">
      <div className="one-click-main">
        <Wand2 size={18} className="one-click-icon" />
        <div className="one-click-text">
          <b>{shots.length === 0
            ? '完成分镜后，这里一键开拍'
            : `一键开拍：自动制作全部 ${shots.filter((shot) => !busyShotIds.has(shot.id)).length} 个镜头`}</b>
          <small>自动选方案、补齐参考、勾选镜头；高级设置一般不用改。</small>
        </div>
        <button type="button" className="must-button" disabled={busy || working || registering || demoSelected || !!activeSeries}
          onClick={() => void oneClickStart()}>
          {working ? <Loader2 size={15} /> : <Wand2 size={15} />}
          {working ? '正在自动适配…' : '一键开拍（自动适配）'}
        </button>
      </div>
      <div className="quality-picker" role="radiogroup" aria-label="拍摄质量">
        {QUALITY_TIERS.map((tier) => {
          const available = ref2vaProfiles.some((p) => tierOfProfile(p.name) === tier.id);
          const active = quality === tier.id;
          return (
            <button type="button" key={tier.id} role="radio" aria-checked={active}
              className={`quality-option ${active ? 'active' : ''} ${available ? '' : 'unavailable'}`}
              title={available ? tier.note : `${tier.note}——本作品还没有该档方案，点下方「一键登记」补齐`}
              onClick={() => setQuality(tier.id)}>
              {tier.label}{!available && <em>未登记</em>}
            </button>
          );
        })}
        <small className="quality-note">{QUALITY_TIERS.find((t) => t.id === quality)?.note}</small>
      </div>
    </div>

    {liveProgress && (
      <div className="series-progress" role="status" aria-label="制作进度">
        <div className="series-progress-head">
          <Loader2 size={14} className="spin" />
          <b>正在制作 · 第 {Math.min(liveProgress.completed + 1, liveProgress.total)}/{liveProgress.total} 镜</b>
          <span className="series-progress-phase">{liveProgress.phase}</span>
          {(liveProgress.shotElapsedS != null || liveProgress.totalElapsedS != null) && (
            <span className="series-progress-timer">
              本镜 {liveProgress.shotElapsedS != null ? `${liveProgress.shotElapsedS}` : '—'} 秒
              <i>·</i>
              总计 {liveProgress.totalElapsedS != null ? `${liveProgress.totalElapsedS}` : '—'} 秒
            </span>
          )}
        </div>
        <div className="series-progress-bar" aria-hidden="true">
          <i style={{ width: `${liveProgress.overall}%` }} />
        </div>
        <div className="series-progress-shots">
          {liveProgress.items.map((item, index) => {
            const name = shots.find((shot) => shot.id === item.shot_id)?.name ?? `镜头 ${index + 1}`;
            const isCurrent = liveProgress.current?.id === item.id;
            return <span key={item.id}
              className={`progress-shot ${item.status.toLowerCase()} ${isCurrent ? 'current' : ''}`}
              title={`${name} · ${itemText[item.status] ?? item.status}`}>
              {index + 1}
            </span>;
          })}
          <small>{liveProgress.completed}/{liveProgress.total} 镜完成 · 完成后自动整理成片并出现在「成片中心」</small>
        </div>
      </div>
    )}

    <details className="series-advanced">
      <summary>高级设置：方案 · 审片 · 规格</summary>
    <div className="series-builder">
      <div className="series-profile">
        <label htmlFor="series-profile">生成方案
          <select id="series-profile" value={profileId} onChange={(event) => setProfileId(event.target.value)}>
            <option value="">自动选择已验证的方案</option>
            {ref2vaProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>{profile.name}</option>
            ))}
          </select>
        </label>
        <label htmlFor="series-scope">审片建议（可选）
          <select id="series-scope" value={scope} onChange={(event) => setScope(event.target.value as 'full' | 'basic')}>
            <option value="full">完整建议：画面、对白与风格（只记录，不阻断）</option>
            <option value="basic">基础建议：人物与对白（只记录，不阻断）</option>
          </select>
        </label>
        {scope === 'basic' && <p className="series-hint">画面规格仍会做技术校验；人物与对白建议未覆盖时会留待查看，不影响制作。</p>}
        <label htmlFor="series-edit-intent">剪辑方案（可选，分镜阶段冻结）
          <select id="series-edit-intent" value={editIntentId} onChange={(event) => setEditIntentId(event.target.value)}>
            <option value="">默认剪辑（全片完整镜头、直接切换）</option>
            {editIntents.map((row) => (
              <option key={row.id} value={row.id}>剪辑方案 {row.id.slice(0, 8)}…</option>
            ))}
          </select>
        </label>
      </div>

      <div className="output-spec">
        <div className="output-spec-head">
          <b>成片规格</b>
          <span>H3 规范：24fps · 宽高为 32 的倍数 · 单镜 4–15 秒</span>
        </div>
        <div className="output-spec-grid">
          <label htmlFor="frame-preset">分辨率与纵横比
            <select id="frame-preset" value={framePreset} onChange={(event) => setFramePreset(event.target.value)}>
              {FRAME_PRESETS.map((row) => <option key={row.id} value={row.id}>{row.label}</option>)}
            </select>
          </label>
          {framePreset === 'custom' && <>
            <label htmlFor="frame-width">宽（像素）
              <input id="frame-width" type="number" min={256} max={2048} step={32} value={customWidth}
                onChange={(event) => setCustomWidth(event.target.value)} />
            </label>
            <label htmlFor="frame-height">高（像素）
              <input id="frame-height" type="number" min={256} max={2048} step={32} value={customHeight}
                onChange={(event) => setCustomHeight(event.target.value)} />
            </label>
          </>}
          <label htmlFor="clip-seconds">单镜长度
            <select id="clip-seconds" value={clipSeconds} onChange={(event) => setClipSeconds(event.target.value)}>
              {CLIP_CHOICES.map((value) => <option key={value || 'plan'} value={value}>
                {value ? `${value} 秒（统一所有选中镜头）` : '按分镜计划（推荐）'}
              </option>)}
            </select>
          </label>
        </div>
        <p className="output-spec-foot">
          {wantsSize ? `${requestedSize.width}×${requestedSize.height}` : '分辨率沿用生成方案'}
          {' · '}
          预计成片 {lengthPreview.total.toFixed(1)} 秒 / {lengthPreview.count} 镜
          {lengthPreview.unified ? '（已统一单镜长度）' : ''}
          {!profileId ? '' : sizeBlocked ? ' · 当前方案未接入宽高参数' : ' · 宽高可在此调整'}
        </p>
        {sizeBlocked && <div className="blocked-callout">
          <AlertTriangle size={15} />
          <span>当前生成方案没有接住界面的宽高设置。补齐后才会按你选的分辨率渲染。</span>
          {onGoSetup && <button type="button" className="text-button" onClick={onGoSetup}>去基础配置补齐</button>}
        </div>}
      </div>

      <details className="identity-check" open={unboundCount > 0}>
        <summary><ShieldCheck size={13} /> 角色参考对照（提交前核对，避免脸对不上）</summary>
        {identityRows.length === 0 && <p className="empty-inline">还没有可核对的镜头。</p>}
        {identityRows.map(({ shot, refs, characters, unbound }) => (
          <div className="identity-row" key={shot.id}>
            <b>{shot.name}</b>
            <span className="identity-cast">{characters.length ? characters.join('、') : '无人物'}</span>
            <span className="identity-labels">
              {refs.filter((row) => row.use_as === 'subject').map((row) => (
                <em key={row.asset_id} className="identity-chip">{row.name} → {row.label}</em>
              ))}
              {refs.filter((row) => row.use_as === 'subject').length === 0 && <em className="identity-chip muted">没有人物参考</em>}
            </span>
            {unbound.length > 0 && <span className="identity-missing">未绑定：{unbound.join('、')}</span>}
          </div>
        ))}
        {shots.length > 4 && <p className="series-hint">只列出前 4 个镜头；每个镜头提交前都会按同一规则核对。</p>}
      </details>

      <div className="series-shots">
        {shots.map((shot) => (
          <label key={shot.id} className={`series-shot ${busyShotIds.has(shot.id) ? 'is-busy' : ''}`}>
            <input
              type="checkbox"
              checked={selected.includes(shot.id)}
              disabled={busyShotIds.has(shot.id)}
              onChange={() => toggle(shot.id)}
            />
            <span className="series-shot-name">{shot.name}</span>
            <small>{label(continuityLabels, String(shot.spec.continuity?.mode ?? 'cut'))}</small>
            <small className="series-shot-length">{Number(shot.spec.duration_target_s ?? 0).toFixed(1)}s</small>
          </label>
        ))}
        {shots.length === 0 && <span className="empty-inline">先创建分镜，才能排入生产序列。</span>}
      </div>
      <div className="series-actions-row">
        <button className="secondary-button" disabled={shots.length === 0} onClick={selectAllProducible}>选择全部可制作镜头</button>
        {busyShotIds.size > 0 && <small className="series-excluded-note">已在生产中被排除 {busyShotIds.size} 镜，不重复提交。</small>}
      </div>
      <button className="primary-button" disabled={busy || working || !canStart} onClick={() => void start()}>
        {working ? <Loader2 size={14} /> : <Play size={14} />} 开始制作影片（{selected.length} 镜）
      </button>
    </div>
    </details>

    {!productionLive && hints.length > 0 && <div className="series-hint-card" role="note" aria-label="开拍提示（不阻断）">
      <Info size={15} />
      <ul>{hints.map((hint) => <li key={hint}>{hint}</li>)}</ul>
    </div>}

    {!productionLive && blockers.length > 0 && <div className="required-action" role="status" aria-label="开始制作前还需完成">
      <AlertTriangle size={17} className="required-icon" />
      <div className="required-text">
        <b>现在为什么不能开始制作</b>
        <ul>{blockers.slice(0, 4).map(reason => <li key={reason}>{reason}</li>)}</ul>
        {blockers.length > 4 && <small>还有 {blockers.length - 4} 项前置条件，处理完上面的会自动刷新。</small>}
        {shots.length === 0 && <small>生成分镜方案只是草稿；人工复核并应用后，这里才会出现可勾选的镜头。</small>}
      </div>
      {shots.length === 0 && <button type="button" className="must-button"
        onClick={() => document.getElementById('storyboard')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>
        去完成并应用分镜
      </button>}
      {!profileId && <>
        <button type="button" className="must-button" disabled={busy || working || registering} onClick={() => void registerProfiles()}>
          {registering ? '正在登记生成方案…' : '为本作品一键登记生成方案'}
        </button>
        <button type="button" className="text-button" onClick={() => onModeAction('check_fix')}>检查生成方案</button>
      </>}
    </div>}

    {series.length > 0 && <div className="series-list">
      {series.slice(0, seriesLimit).map((row) => (
        <article key={row.id} className={`series-block ${row.status.toLowerCase()}`}>
          <div className="series-head">
            <div>
              <b>{statusText[row.status] ?? row.status}</b>
              <span>{row.completed_count}/{row.shot_count} 镜完成</span>
              {row.output_spec?.width && row.output_spec?.height
                && <small>规格：{row.output_spec.width}×{row.output_spec.height}
                  {row.output_spec.clip_seconds ? ` · 单镜 ${row.output_spec.clip_seconds}s` : ''}</small>}
              {row.failure_code && <small className="failure">· {label(failureLabels, row.failure_code)}</small>}
              {row.finalization_state !== 'NONE' && <small>收尾：{label(finalizationLabels, row.finalization_state)}</small>}
              {row.delivery_result && <small className="delivered">· 自动交付 v{row.delivery_result.version}（{row.delivery_result.review_state === 'all_passed' ? '来源审查通过' : '待审样片'}）</small>}
            </div>
            <div className="series-actions">
              {row.status === 'QUEUED' && <button className="secondary-button" disabled={working} onClick={() => act(row.id, 'pause')}>暂停</button>}
              {['PAUSED', 'PAUSE_REQUESTED'].includes(row.status) && <button className="secondary-button" disabled={working} onClick={() => act(row.id, 'resume')}>恢复</button>}
              {['QUEUED', 'PAUSED', 'PAUSE_REQUESTED', 'NEEDS_REVIEW'].includes(row.status)
                && <button className="subtle-button" disabled={working} onClick={() => act(row.id, 'cancel')}><Square size={12} /> 取消</button>}
            </div>
          </div>
          <ol className="series-items">
            {row.items.map((item) => (
              <li key={item.id}>
                <Clapperboard size={12} />
                <span>{shots.find((shot) => shot.id === item.shot_id)?.name ?? item.shot_id.slice(0, 8)}</span>
                <em className={`series-item-status ${item.status.toLowerCase()}`}>{itemText[item.status] ?? item.status}</em>
                {item.job_status && <small>{label(statusLabels, item.job_status)}</small>}
                {item.job_artifact_id && <small>已出片</small>}
                {item.continuity_mode !== 'cut' && <small>{label(continuityLabels, item.continuity_mode)}</small>}
                {item.job_failure_code && <small className="failure">{label(failureLabels, item.job_failure_code)}</small>}
              </li>
            ))}
          </ol>
          <details className="tech-details">
            <summary>技术详情：批次编号与执行服务</summary>
            <div className="tech-details-body">
              <span>批次编号 <code>{row.id}</code></span>
              {row.worker_id && <span>执行服务 <code>{row.worker_id}</code></span>}
              {row.failure_code && <span>失败代码 <code>{row.failure_code}</code></span>}
              {row.finalization_state !== 'NONE' && <span>收尾状态 <code>{row.finalization_state}</code></span>}
            </div>
          </details>
        </article>
      ))}
      {series.length > seriesLimit && <button type="button" className="list-more"
        onClick={() => setSeriesLimit((limit) => limit + 5)}>
        查看更早的制作批次（还有 {series.length - seriesLimit} 个）
      </button>}
    </div>}
  </div>;
}
